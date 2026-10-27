# 📁 navegador_activo.py — Qué navegador usar cuando NO dices cuál (v1)
#
# Qué resuelve:
#   "abre youtube" · "busca gatos en google" · "abre github.com"   (sin decir "en chrome")
#   → SARA mira qué navegador tienes ABIERTO y lo usa (se abre una pestaña nueva ahí).
#   → Si tienes DOS O MÁS abiertos, usa el que MÁS usas.
#   → Si no hay ninguno abierto, usa el que más usas; sin historial, el predeterminado de Windows;
#     y como último recurso el orden fijo de siempre (web_navigator._ORDEN_PREFERENCIA).
#
# Cómo sabe cuál usas más (sin leer tu historial de navegación ni las pestañas):
#   * Rastreo ligero: cada ~3 s mira qué ventana tiene el foco; si es un navegador y estás activo
#     (teclado/ratón en el último minuto) suma esos segundos a ESE navegador. Solo guarda segundos por
#     navegador y día en sara.db (tabla navegador_uso). El uso reciente pesa más (vida media 7 días).
#   * Cada vez que dices "en <navegador>" explícitamente, eso cuenta como un voto.
#   * El navegador predeterminado de Windows da un empujón inicial (sirve mientras no hay datos).
#
# "Abierto" = tiene una VENTANA visible (no basta un proceso: Chrome/Edge dejan procesos en segundo plano).
#
# Aislado y opcional (corolario v8): si este archivo falta o falla, web_navigator sigue con el orden fijo.
# Solo Windows para detectar ventanas; en otros sistemas elegir() devuelve None y todo sigue como antes.
#
# Config opcional (config.py):  NAV_INTELIGENTE_ACTIVO · NAV_RASTREAR_USO · NAV_RASTREO_INTERVALO_S ·
#                               NAV_VIDA_MEDIA_DIAS · NAV_SEG_POR_ELECCION · NAV_BONUS_PREDETERMINADO_S

NAVEGADOR_ACTIVO_VERSION = "v1-abierto-y-mas-usado"

import atexit
import ctypes
import logging
import ntpath
import sys
import threading
import time
from datetime import date, timedelta
from typing import Dict, Optional

log = logging.getLogger("SARA-NAV-ACTIVO")

try:
    import config as _cfg
except Exception:
    _cfg = None
try:
    from database import conectar as _conectar
except Exception:
    _conectar = None
try:
    import winreg
except ImportError:
    winreg = None

_ES_WIN = sys.platform.startswith("win")

NOMBRES = {"chrome": "Chrome", "edge": "Edge", "firefox": "Firefox",
           "opera_gx": "Opera GX", "opera": "Opera", "brave": "Brave"}
_EXE_A_ID = {"chrome.exe": "chrome", "msedge.exe": "edge", "firefox.exe": "firefox", "brave.exe": "brave"}


def _c(nombre: str, defecto):
    return getattr(_cfg, nombre, defecto) if _cfg is not None else defecto


def _activo() -> bool:
    return bool(_c("NAV_INTELIGENTE_ACTIVO", True))


def _err(contexto: str, exc: Exception) -> None:
    """Principio 2: ningún error se silencia."""
    try:
        import logger as _sl
        _sl.log_excepcion("navegador_activo", contexto, exc)
    except Exception:
        log.warning("%s: %s: %s", contexto, type(exc).__name__, exc)


def _id_por_exe(ruta: Optional[str]) -> Optional[str]:
    """Ruta de un .exe → id del catálogo de web_navigator (Opera y Opera GX comparten opera.exe)."""
    if not ruta:
        return None
    base, r = ntpath.basename(ruta).lower(), ruta.lower()
    if base in _EXE_A_ID:
        return _EXE_A_ID[base]
    if base in ("opera.exe", "launcher.exe") and "opera" in r:
        return "opera_gx" if ("opera gx" in r or "operagx" in r) else "opera"
    return None


# ═══════════════════════════════════════════════════════════════════════════
# WINDOWS: ventanas, primer plano e inactividad (ctypes, sin dependencias)
# ═══════════════════════════════════════════════════════════════════════════

_api = None


def _winapi():
    """Instancias PRIVADAS de user32/kernel32: no tocamos los prototipos que usen otros módulos."""
    global _api
    if _api is None:
        from ctypes import wintypes
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                   ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.GetTickCount.restype = wintypes.DWORD
        u32.GetForegroundWindow.restype = wintypes.HWND
        u32.IsWindowVisible.argtypes = [wintypes.HWND]
        u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
        _api = (u32, k32, wintypes, enum_proc, LASTINPUTINFO)
    return _api


def _exe_de_pid(pid: int) -> Optional[str]:
    _, k32, wt, _, _ = _winapi()
    h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        buf, n = ctypes.create_unicode_buffer(1024), wt.DWORD(1024)
        return buf.value if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else None
    finally:
        k32.CloseHandle(h)


def ventanas_por_navegador() -> Dict[str, int]:
    """{nav_id: nº de ventanas visibles con título}. Vacío fuera de Windows o si falla."""
    if not _ES_WIN:
        return {}
    try:
        u32, _, wt, enum_proc, _ = _winapi()
        out: Dict[str, int] = {}
        cache: Dict[int, Optional[str]] = {}

        def cb(hwnd, _lp):
            try:
                if u32.IsWindowVisible(hwnd) and u32.GetWindowTextLengthW(hwnd) > 0:
                    pid = wt.DWORD()
                    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value not in cache:
                        cache[pid.value] = _id_por_exe(_exe_de_pid(pid.value))
                    nid = cache[pid.value]
                    if nid:
                        out[nid] = out.get(nid, 0) + 1
            except Exception:
                pass
            return True
        proc = enum_proc(cb)                            # referencia viva mientras dura EnumWindows
        u32.EnumWindows(proc, 0)
        return out
    except Exception as e:
        _err("ventanas_por_navegador", e)
        return {}


def navegador_en_primer_plano() -> Optional[str]:
    if not _ES_WIN:
        return None
    try:
        u32, _, wt, _, _ = _winapi()
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return _id_por_exe(_exe_de_pid(pid.value))
    except Exception as e:
        _err("navegador_en_primer_plano", e)
        return None


def segundos_inactivo() -> float:
    """Segundos desde el último teclado/ratón (0.0 si no se puede saber)."""
    if not _ES_WIN:
        return 0.0
    try:
        u32, k32, _, _, LII = _winapi()
        li = LII()
        li.cbSize = ctypes.sizeof(li)
        if not u32.GetLastInputInfo(ctypes.byref(li)):
            return 0.0
        return ((k32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return 0.0


def predeterminado_windows() -> Optional[str]:
    """nav_id del navegador predeterminado de Windows (por el ProgId de https), o None."""
    if winreg is None:
        return None
    for proto in ("https", "http"):
        try:
            clave = rf"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\{proto}\UserChoice"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, clave) as k:
                prog = str(winreg.QueryValueEx(k, "ProgId")[0]).lower()
        except OSError:
            continue
        if "brave" in prog:
            return "brave"
        if "firefox" in prog:
            return "firefox"
        if "opera" in prog:
            return "opera_gx" if "gx" in prog else "opera"
        if "edge" in prog:
            return "edge"
        if "chrome" in prog:
            return "chrome"
    return None


# ═══════════════════════════════════════════════════════════════════════════
# MEMORIA (sara.db): segundos de foco por navegador y día + votos explícitos
# ═══════════════════════════════════════════════════════════════════════════

_tabla_ok = False
_lock = threading.Lock()
_acum: Dict[str, float] = {}            # segundos aún sin volcar a la BD
_ultimo_foco: Dict[str, float] = {}     # nav_id → epoch del último foco visto


def _crear_tabla() -> bool:
    global _tabla_ok
    if _tabla_ok:
        return True
    if _conectar is None:
        return False
    try:
        conn = _conectar()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS navegador_uso (nav_id TEXT NOT NULL, dia TEXT NOT NULL, "
                         "segundos REAL DEFAULT 0, explicitos INTEGER DEFAULT 0, ultimo_ts REAL DEFAULT 0, "
                         "PRIMARY KEY (nav_id, dia))")
            conn.commit()
        finally:
            conn.close()
        _tabla_ok = True
    except Exception as e:
        _err("_crear_tabla", e)
    return _tabla_ok


def _sumar(nav_id: str, seg: float = 0.0, expl: int = 0, ts: float = 0.0) -> bool:
    if not _crear_tabla():
        return False
    try:
        conn = _conectar()
        try:
            conn.execute(
                "INSERT INTO navegador_uso (nav_id, dia, segundos, explicitos, ultimo_ts) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(nav_id, dia) DO UPDATE SET segundos = segundos + excluded.segundos, "
                "explicitos = explicitos + excluded.explicitos, ultimo_ts = MAX(ultimo_ts, excluded.ultimo_ts)",
                (nav_id, date.today().isoformat(), float(seg), int(expl), float(ts)))
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as e:
        _err("_sumar", e)
        return False


def _volcar() -> None:
    """Pasa los segundos acumulados en memoria a la BD (si falla, se conservan para el próximo intento)."""
    with _lock:
        pendiente = dict(_acum)
        _acum.clear()
        focos = dict(_ultimo_foco)
    for nid, seg in pendiente.items():
        if seg > 0 and not _sumar(nid, seg=seg, ts=focos.get(nid, 0.0)):
            with _lock:
                _acum[nid] = _acum.get(nid, 0.0) + seg


def registrar_eleccion(nav_id: Optional[str]) -> None:
    """Un voto: el usuario pidió este navegador de forma explícita ('abre youtube en edge')."""
    if nav_id and _activo():
        _sumar(nav_id, expl=1, ts=time.time())


def olvidar() -> int:
    """Borra el historial de uso. Devuelve cuántas filas había."""
    with _lock:
        _acum.clear()
        _ultimo_foco.clear()
    if not _crear_tabla():
        return 0
    try:
        conn = _conectar()
        try:
            n = conn.execute("SELECT COUNT(*) FROM navegador_uso").fetchone()[0]
            conn.execute("DELETE FROM navegador_uso")
            conn.commit()
        finally:
            conn.close()
        return int(n)
    except Exception as e:
        _err("olvidar", e)
        return 0


def _nuevo() -> dict:
    return {"foco_s": 0.0, "explicitos": 0, "ultimo_ts": 0.0, "puntos": 0.0}


def puntuaciones() -> Dict[str, dict]:
    """{nav_id: {foco_s, explicitos, ultimo_ts, puntos}}. Los puntos pesan más lo reciente."""
    vida = max(0.5, float(_c("NAV_VIDA_MEDIA_DIAS", 7)))
    peso_voto = float(_c("NAV_SEG_POR_ELECCION", 120))
    hoy, res = date.today(), {}
    if _crear_tabla():
        try:
            desde = (hoy - timedelta(days=90)).isoformat()
            conn = _conectar()
            try:
                filas = conn.execute("SELECT nav_id, dia, segundos, explicitos, ultimo_ts FROM navegador_uso "
                                     "WHERE dia >= ?", (desde,)).fetchall()
            finally:
                conn.close()
            for nid, dia, seg, expl, ts in filas:
                edad = max(0, (hoy - date.fromisoformat(dia)).days)
                r = res.setdefault(nid, _nuevo())
                r["foco_s"] += seg or 0
                r["explicitos"] += expl or 0
                r["ultimo_ts"] = max(r["ultimo_ts"], ts or 0)
                r["puntos"] += ((seg or 0) + (expl or 0) * peso_voto) * (0.5 ** (edad / vida))
        except Exception as e:
            _err("puntuaciones", e)
    with _lock:
        for nid, seg in _acum.items():
            r = res.setdefault(nid, _nuevo())
            r["foco_s"] += seg
            r["puntos"] += seg
        for nid, ts in _ultimo_foco.items():
            r = res.setdefault(nid, _nuevo())
            r["ultimo_ts"] = max(r["ultimo_ts"], ts)
    return res


# ═══════════════════════════════════════════════════════════════════════════
# RASTREO (hilo demonio, muy ligero)
# ═══════════════════════════════════════════════════════════════════════════

_hilo: Optional[threading.Thread] = None


def _bucle() -> None:
    intervalo = max(1.0, float(_c("NAV_RASTREO_INTERVALO_S", 3)))
    previo = ultimo_volcado = time.monotonic()
    while True:
        time.sleep(intervalo)
        ahora = time.monotonic()
        dt, previo = min(ahora - previo, intervalo * 3), ahora     # tras suspender el PC no se cuenta el hueco
        try:
            if segundos_inactivo() < 60:
                nid = navegador_en_primer_plano()
                if nid:
                    with _lock:
                        _acum[nid] = _acum.get(nid, 0.0) + dt
                        _ultimo_foco[nid] = time.time()
            if ahora - ultimo_volcado >= 60:
                _volcar()
                ultimo_volcado = ahora
        except Exception as e:
            _err("_bucle", e)


def iniciar_rastreo() -> bool:
    """Arranca el rastreo (una sola vez). Llamar al iniciar SARA. False si no aplica."""
    global _hilo
    if not (_ES_WIN and _activo() and _c("NAV_RASTREAR_USO", True)):
        return False
    if _hilo is not None and _hilo.is_alive():
        return True
    _crear_tabla()
    _hilo = threading.Thread(target=_bucle, name="sara-nav-rastreo", daemon=True)
    _hilo.start()
    atexit.register(_volcar)
    log.info("🧭 rastreo de uso de navegadores iniciado")
    return True


# ═══════════════════════════════════════════════════════════════════════════
# DECISIÓN
# ═══════════════════════════════════════════════════════════════════════════

def _fmt(seg: float) -> str:
    m = int(seg // 60)
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


def elegir(instalados: Dict[str, str]) -> Optional[dict]:
    """instalados = {nav_id: ruta_exe} (web_navigator.detectar_navegadores_instalados()).
    Devuelve {"nav_id","ruta","motivo"} o None (= que decida el orden fijo de siempre)."""
    if not _activo() or not instalados:
        return None
    try:
        abiertos = {k: v for k, v in ventanas_por_navegador().items() if k in instalados}
        punt = puntuaciones()
        pred = predeterminado_windows()
        bonus = float(_c("NAV_BONUS_PREDETERMINADO_S", 600))

        def clave(nid):                       # más puntos → más reciente → es el predeterminado
            p = punt.get(nid, _nuevo())
            return (p["puntos"] + (bonus if nid == pred else 0.0), p["ultimo_ts"], nid == pred)

        def uso(nid):
            return f"{NOMBRES.get(nid, nid)} {_fmt(punt.get(nid, _nuevo())['foco_s'])}"

        if len(abiertos) == 1:
            nid = next(iter(abiertos))
            motivo = "es el único navegador abierto"
        elif len(abiertos) > 1:
            nid = max(abiertos, key=clave)
            motivo = ("abiertos " + ", ".join(uso(n) for n in sorted(abiertos, key=clave, reverse=True))
                      + " → gana el de más uso (lo reciente pesa más)")
        else:
            nid = max(instalados, key=clave)
            if clave(nid)[0] <= 0:
                log.info("🧭 navegador: ninguno abierto y sin historial → orden fijo")
                return None
            motivo = ("ninguno abierto; " + ("el predeterminado de Windows" if nid == pred and
                                              punt.get(nid, _nuevo())["puntos"] <= 0 else "el que más usas"))
        log.info(f"🧭 navegador: {NOMBRES.get(nid, nid)} ({motivo})")
        return {"nav_id": nid, "ruta": instalados[nid], "motivo": motivo}
    except Exception as e:
        _err("elegir", e)
        return None


def diagnostico(instalados: Optional[Dict[str, str]] = None) -> str:
    punt = puntuaciones()
    abiertos = ventanas_por_navegador()
    pred = predeterminado_windows()
    lineas = [f"{NOMBRES.get(n, n)}: {_fmt(p['foco_s'])} de uso · {p['explicitos']} voto(s) · {p['puntos']:.0f} pts"
              for n, p in sorted(punt.items(), key=lambda x: -x[1]["puntos"])]
    sel = elegir(instalados) if instalados else None
    return ("Navegador inteligente:\n"
            f"Versión de navegador_activo.py: {NAVEGADOR_ACTIVO_VERSION}\n"
            f"Estado: {'ACTIVO' if _activo() else 'APAGADO (NAV_INTELIGENTE_ACTIVO)'} · rastreo: "
            f"{'corriendo' if _hilo is not None and _hilo.is_alive() else 'parado'}"
            f"{'' if _ES_WIN else ' · (solo Windows detecta ventanas)'}\n"
            f"Abiertos ahora: {', '.join(f'{NOMBRES.get(n, n)} ({c})' for n, c in abiertos.items()) or 'ninguno'}\n"
            f"Predeterminado de Windows: {NOMBRES.get(pred, pred) if pred else 'no detectado'}\n"
            f"Uso aprendido: {'; '.join(lineas) or '— (aún sin datos)'}\n"
            f"Si dijeras «abre youtube» ahora: "
            f"{(NOMBRES.get(sel['nav_id']) + ' — ' + sel['motivo']) if sel else 'orden fijo de siempre'}\n"
            "Uso: /navegador · /navegador olvidar")
