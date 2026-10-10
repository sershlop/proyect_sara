# 📁 navegadores.py — Navegadores abiertos y navegador "estratégico" de SARA (v1)
#
# Qué hace:
#   1) DETECTA qué navegadores tiene el usuario abiertos en este momento (con ventana visible, no procesos
#      en segundo plano: Chrome/Edge dejan procesos vivos aunque cierres todas las ventanas).
#   2) DISTINGUE variantes que comparten ejecutable: Opera y Opera GX son ambos "opera.exe"; se separan por
#      la ruta del proceso.
#   3) APRENDE cuál usa más el usuario: un muestreador ligero (cada 30 s mientras SARA corre) suma puntos al
#      navegador en primer plano y, menos, a los que están abiertos. Los puntos pierden peso con los días
#      (se adapta si cambias de hábito). También cuenta cuando pides un navegador por su nombre.
#   4) ELIGE el navegador para "abre youtube" cuando NO se pidió uno concreto:
#         · uno solo abierto           → ese
#         · varios abiertos            → el más usado de ellos
#         · ninguno abierto            → el que más usas; sin datos, el predeterminado de Windows
#      y ABRE la URL en él (una pestaña nueva en la instancia que ya está abierta).
#   Integración: web_navigator.seleccionar_navegador() llama a elegir(instalados=...) cuando la frase no
#   pide un navegador concreto; los ids de navegador coinciden con los de web_navigator (opera_gx, etc.).
#
# Principios (los de SARA):
#   * Módulo aislado y opcional: nada aquí lanza excepciones hacia afuera; si algo falla devuelve None/False
#     y quien lo llame sigue con su comportamiento anterior.
#   * La lógica de decisión (_decidir) es una función PURA, separada de las llamadas a Windows (ctypes, psutil,
#     registro), para poder probarla sin Windows.
#   * Seguridad: solo se abren URLs http(s); una "URL" que empiece con "-" se rechazaría como bandera del navegador.
#
# Persistencia: navegadores_uso.json (junto a este archivo; se crea solo).
#
# API: elegir(preferido=None) · abrir(url=None, preferido=None) · resolver_alias(texto) · estado()
#      registrar_uso_explicito(clave) · iniciar_muestreo() · detener_muestreo() · diagnostico()

import atexit
import datetime
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("SARA-NAV")

try:
    import config
except Exception:
    config = None


def _cfg(nombre: str, defecto):
    return getattr(config, nombre, defecto) if config is not None else defecto


# ─── PARÁMETROS (opcionales en config.py) ─────────────────────────────────

AUTODETECTAR = bool(_cfg("NAV_AUTODETECTAR", True))
MUESTREO_S = float(_cfg("NAV_MUESTREO_S", 30.0))
DECAY_DIARIO = float(_cfg("NAV_DECAY_DIARIO", 0.97))          # ≈ vida media de 23 días
PESO_PRIMER_PLANO = float(_cfg("NAV_PESO_PRIMER_PLANO", 1.0))
PESO_ABIERTO = float(_cfg("NAV_PESO_ABIERTO", 0.25))
PESO_PEDIDO_EXPLICITO = float(_cfg("NAV_PESO_PEDIDO_EXPLICITO", 5.0))
GUARDAR_CADA_MUESTRAS = 10
POR_DEFECTO_CONFIG = str(_cfg("NAVEGADOR_PREDETERMINADO", "chrome")).lower()

ARCHIVO_USO = Path(__file__).with_name("navegadores_uso.json")

# ─── CATÁLOGO ─────────────────────────────────────────────────────────────
# Orden = desempate final estable. "rutas" son lugares habituales de instalación (se expanden %VARIABLES%).

_CATALOGO: Dict[str, dict] = {
    "chrome": {
        "nombre": "Google Chrome", "alias": ("google chrome", "chrome", "cromo"),
        "rutas": (r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
                  r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                  r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    },
    "edge": {
        "nombre": "Microsoft Edge", "alias": ("microsoft edge", "edge"),
        "rutas": (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
                  r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    },
    "firefox": {
        "nombre": "Mozilla Firefox", "alias": ("mozilla firefox", "firefox", "mozilla"),
        "rutas": (r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
                  r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe"),
    },
    "opera_gx": {
        "nombre": "Opera GX", "alias": ("opera gx", "opera_gx", "opera g x", "gx"),
        "rutas": (r"%LOCALAPPDATA%\Programs\Opera GX\opera.exe",
                  r"%ProgramFiles%\Opera GX\opera.exe",
                  r"%ProgramFiles(x86)%\Opera GX\opera.exe"),
    },
    "opera": {
        "nombre": "Opera", "alias": ("opera",),
        "rutas": (r"%LOCALAPPDATA%\Programs\Opera\opera.exe",
                  r"%ProgramFiles%\Opera\opera.exe",
                  r"%ProgramFiles(x86)%\Opera\opera.exe"),
    },
    "brave": {
        "nombre": "Brave", "alias": ("brave",),
        "rutas": (r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
                  r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
    },
    "vivaldi": {
        "nombre": "Vivaldi", "alias": ("vivaldi",),
        "rutas": (r"%LOCALAPPDATA%\Vivaldi\Application\vivaldi.exe",
                  r"%ProgramFiles%\Vivaldi\Application\vivaldi.exe"),
    },
}

_EXE_A_CLAVE = {"chrome.exe": "chrome", "msedge.exe": "edge", "firefox.exe": "firefox",
                "brave.exe": "brave", "vivaldi.exe": "vivaldi"}
# Ojo: "msedgewebview2.exe" (el WebView de la propia ventana de SARA) NO está aquí a propósito.


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFD", str(t).lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t)).strip()


def nombre_de(clave: str) -> str:
    return _CATALOGO.get(clave, {}).get("nombre", clave)


def identificar(exe: str) -> Optional[str]:
    """Ruta de un ejecutable → clave del catálogo (separa Opera de Opera GX por la carpeta)."""
    if not exe:
        return None
    ruta = str(exe).lower()
    base = os.path.basename(ruta.replace("\\", "/"))
    if base in ("opera.exe", "launcher.exe") and "opera" in ruta:
        return "opera_gx" if ("opera gx" in ruta or "opera_gx" in ruta or "opera_gx" in ruta) else "opera"
    return _EXE_A_CLAVE.get(base)


_ALIAS_ORDENADOS: List[Tuple[str, str]] = sorted(
    ((_norm(a), k) for k, v in _CATALOGO.items() for a in v["alias"]), key=lambda x: -len(x[0]))


def resolver_alias(texto: str) -> Optional[str]:
    """Texto libre ("en opera gx", "con firefox") → clave de navegador, o None. El alias más largo gana,
    así "opera gx" no se confunde con "opera"."""
    n = f" {_norm(texto)} "
    for alias, clave in _ALIAS_ORDENADOS:
        if f" {alias} " in n:
            return clave
    return None


# ─── CAPA DE WINDOWS (no se puede probar fuera de Windows; se aísla aquí) ──

def _ventanas_visibles() -> List[Tuple[int, str]]:
    """[(pid, título)] de las ventanas de nivel superior visibles, con título y que no sean de herramientas."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    GWL_EXSTYLE, WS_EX_TOOLWINDOW = -20, 0x00000080
    salida: List[Tuple[int, str]] = []

    def cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                return True
            largo = user32.GetWindowTextLengthW(hwnd)
            if largo <= 0:
                return True
            buf = ctypes.create_unicode_buffer(largo + 1)
            user32.GetWindowTextW(hwnd, buf, largo + 1)
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            salida.append((int(pid.value), buf.value))
        except Exception:
            pass
        return True

    callback = proc(cb)                       # referencia viva mientras dura EnumWindows
    user32.EnumWindows(callback, 0)
    return salida


def _pid_primer_plano() -> Optional[int]:
    """PID del proceso dueño de la ventana que el usuario tiene en primer plano."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) or None


def _exe_de_pid(pid: int) -> Optional[str]:
    try:
        import psutil
        return psutil.Process(pid).exe()
    except Exception:
        return None


def _clave_desde_progid(progid: str) -> Optional[str]:
    p = (progid or "").lower()
    if "opera" in p:
        return "opera_gx" if "gx" in p else "opera"
    for fragmento, clave in (("brave", "brave"), ("vivaldi", "vivaldi"), ("firefox", "firefox"),
                             ("msedge", "edge"), ("edge", "edge"), ("chrome", "chrome")):
        if fragmento in p:
            return clave
    return None


def _predeterminado() -> Optional[str]:
    """Navegador predeterminado de Windows (por la asociación https del usuario)."""
    if os.name != "nt":
        return None
    try:
        import winreg
        for esquema in ("https", "http"):
            try:
                ruta = rf"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\{esquema}\UserChoice"
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ruta) as k:
                    clave = _clave_desde_progid(winreg.QueryValueEx(k, "ProgId")[0])
                    if clave:
                        return clave
            except OSError:
                continue
    except Exception as e:
        logger.debug(f"predeterminado: {e}")
    return None


def _rutas_registro() -> List[str]:
    """Ejecutables de los navegadores registrados en Windows (StartMenuInternet)."""
    rutas: List[str] = []
    if os.name != "nt":
        return rutas
    try:
        import winreg
        for raiz in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(raiz, r"SOFTWARE\Clients\StartMenuInternet") as base:
                    i = 0
                    while True:
                        try:
                            nombre = winreg.EnumKey(base, i)
                        except OSError:
                            break
                        i += 1
                        try:
                            with winreg.OpenKey(base, rf"{nombre}\shell\open\command") as cmd:
                                valor = winreg.QueryValueEx(cmd, "")[0]
                            m = re.match(r'\s*"?([^"]+?\.exe)', str(valor), re.IGNORECASE)
                            if m:
                                rutas.append(os.path.expandvars(m.group(1)))
                        except OSError:
                            continue
            except OSError:
                continue
    except Exception as e:
        logger.debug(f"registro navegadores: {e}")
    return rutas


# ─── DATOS APRENDIDOS (JSON atómico) ──────────────────────────────────────

_lock = threading.RLock()
_datos: Optional[dict] = None
_muestras_sin_guardar = 0


def _hoy() -> str:
    return datetime.date.today().isoformat()


def _nuevos_datos() -> dict:
    return {"version": 1, "puntos": {}, "exe": {}, "muestras": 0, "desde": _hoy(), "ultimo_decay": _hoy()}


def _aplicar_decay(datos: dict) -> None:
    try:
        dias = (datetime.date.today() - datetime.date.fromisoformat(datos.get("ultimo_decay", _hoy()))).days
    except Exception:
        dias = 0
    if dias > 0:
        f = DECAY_DIARIO ** dias
        datos["puntos"] = {k: round(v * f, 4) for k, v in datos.get("puntos", {}).items() if v * f >= 0.01}
        datos["ultimo_decay"] = _hoy()


def _datos_cargados() -> dict:
    global _datos
    with _lock:
        if _datos is None:
            try:
                d = json.loads(ARCHIVO_USO.read_text(encoding="utf-8"))
                if not isinstance(d, dict) or not isinstance(d.get("puntos"), dict):
                    raise ValueError("formato inválido")
                d.setdefault("exe", {})
                d.setdefault("muestras", 0)
                d.setdefault("desde", _hoy())
                d.setdefault("ultimo_decay", _hoy())
            except FileNotFoundError:
                d = _nuevos_datos()
            except Exception as e:
                logger.warning(f"⚠️  navegadores_uso.json ilegible ({e}); empiezo de cero")
                d = _nuevos_datos()
            _aplicar_decay(d)
            _datos = d
        return _datos


def _guardar() -> None:
    global _muestras_sin_guardar
    with _lock:
        if _datos is None:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=str(ARCHIVO_USO.parent), prefix=".nav_", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(_datos, f, ensure_ascii=False, indent=1)
            os.replace(tmp, ARCHIVO_USO)
            _muestras_sin_guardar = 0
        except Exception as e:
            logger.warning(f"⚠️  no pude guardar navegadores_uso.json: {e}")


atexit.register(_guardar)


# ─── OBSERVACIÓN ──────────────────────────────────────────────────────────

def _abiertos() -> Dict[str, dict]:
    """{clave: {"ventanas": n, "exe": ruta}} de los navegadores con ventana visible ahora."""
    res: Dict[str, dict] = {}
    por_pid: Dict[int, Tuple[Optional[str], Optional[str]]] = {}          # pid → (clave, exe)
    for pid, _titulo in _ventanas_visibles():
        if pid not in por_pid:
            ruta = _exe_de_pid(pid)
            por_pid[pid] = (identificar(ruta) if ruta else None, ruta)
        clave, exe = por_pid[pid]
        if clave:
            info = res.setdefault(clave, {"ventanas": 0, "exe": exe})
            info["ventanas"] += 1
    return res


def _primer_plano() -> Optional[str]:
    pid = _pid_primer_plano()
    if not pid:
        return None
    exe = _exe_de_pid(pid)
    return identificar(exe) if exe else None


def _instalados() -> Dict[str, str]:
    """{clave: ruta del ejecutable} de los navegadores instalados (visto en ejecución → rutas típicas → registro)."""
    res: Dict[str, str] = {}
    vistos = _datos_cargados().get("exe", {})
    for clave, info in _CATALOGO.items():
        ruta = vistos.get(clave)
        if ruta and os.path.exists(ruta):
            res[clave] = ruta
            continue
        for plantilla in info["rutas"]:
            p = os.path.expandvars(plantilla)
            if os.path.exists(p):
                res[clave] = p
                break
    for ruta in _rutas_registro():
        clave = identificar(ruta)
        if clave and clave not in res and os.path.exists(ruta):
            res[clave] = ruta
    return res


def _muestrear() -> None:
    """Un latido del aprendizaje: suma puntos al navegador en primer plano y, menos, a los abiertos."""
    global _muestras_sin_guardar
    abiertos = _abiertos()
    primero = _primer_plano()
    with _lock:
        d = _datos_cargados()
        _aplicar_decay(d)
        puntos = d["puntos"]
        for clave, info in abiertos.items():
            puntos[clave] = round(puntos.get(clave, 0.0) + PESO_ABIERTO, 4)
            if info.get("exe"):
                d["exe"][clave] = info["exe"]
        if primero in _CATALOGO:
            puntos[primero] = round(puntos.get(primero, 0.0) + PESO_PRIMER_PLANO, 4)
        d["muestras"] = int(d.get("muestras", 0)) + 1
        _muestras_sin_guardar += 1
        guardar = _muestras_sin_guardar >= GUARDAR_CADA_MUESTRAS
    if guardar:
        _guardar()


def registrar_uso_explicito(clave: str) -> None:
    """Pedir un navegador por su nombre es la señal de preferencia más fuerte."""
    try:
        if clave not in _CATALOGO:
            return
        with _lock:
            d = _datos_cargados()
            d["puntos"][clave] = round(d["puntos"].get(clave, 0.0) + PESO_PEDIDO_EXPLICITO, 4)
        _guardar()
    except Exception as e:
        logger.debug(f"registrar_uso_explicito: {e}")


# ─── DECISIÓN (función pura: se prueba sin Windows) ───────────────────────

def _decidir(preferido: Optional[str], abiertos: List[str], primer_plano: Optional[str],
             puntos: Dict[str, float], instalados: Dict[str, str], predeterminado: Optional[str],
             por_defecto: str = "chrome") -> Tuple[Optional[str], str]:
    """Devuelve (clave | None, motivo legible)."""
    orden = {k: i for i, k in enumerate(_CATALOGO)}

    def prioridad(k: str):
        return (-puntos.get(k, 0.0), 0 if k == primer_plano else 1,
                0 if k == predeterminado else 1, orden.get(k, 99))

    if preferido:
        if preferido in abiertos or preferido in instalados:
            return preferido, "es el que pediste"
        return None, f"{nombre_de(preferido)} no parece estar instalado"

    if len(abiertos) == 1:
        return abiertos[0], "es el único navegador abierto"

    if len(abiertos) > 1:
        k = min(abiertos, key=prioridad)
        if puntos.get(k, 0.0) > 0:
            return k, f"es el que más usas de los {len(abiertos)} abiertos"
        if k == primer_plano:
            return k, "es el que estás usando ahora (aún no hay estadísticas de uso)"
        if k == predeterminado:
            return k, "es tu navegador predeterminado (aún no hay estadísticas de uso)"
        return k, "criterio por defecto (aún no hay estadísticas de uso)"

    if instalados:
        con_datos = [k for k in instalados if puntos.get(k, 0.0) > 0]
        if con_datos:
            return min(con_datos, key=prioridad), "no hay ninguno abierto; es el que más usas"
        if predeterminado in instalados:
            return predeterminado, "no hay ninguno abierto; es tu navegador predeterminado"
        if por_defecto in instalados:
            return por_defecto, "no hay ninguno abierto; es el navegador por defecto de SARA"
        k = min(instalados, key=prioridad)
        return k, "no hay ninguno abierto; es el primero que encontré instalado"
    return None, "no encontré ningún navegador instalado"


def estado(instalados: Optional[Dict[str, str]] = None) -> dict:
    """Foto completa (la usa elegir() y el diagnóstico). Nunca lanza.
    `instalados` ({clave: ruta}) permite a quien llama aportar su propia detección (web_navigator mira también
    el Registro y el Menú Inicio); si no se pasa, se usa la detección propia de este módulo."""
    try:
        abiertos = _abiertos()
        d = _datos_cargados()
        return {"abiertos": abiertos, "primer_plano": _primer_plano(), "puntos": dict(d["puntos"]),
                "instalados": dict(instalados) if instalados else _instalados(),
                "predeterminado": _predeterminado(),
                "muestras": d.get("muestras", 0), "desde": d.get("desde", "")}
    except Exception as e:
        logger.exception("❌ navegadores.estado falló")
        return {"abiertos": {}, "primer_plano": None, "puntos": {}, "instalados": {}, "predeterminado": None,
                "muestras": 0, "desde": "", "error": str(e)}


def elegir(preferido: Optional[str] = None, instalados: Optional[Dict[str, str]] = None) -> Optional[dict]:
    """Elige el navegador: {"clave","nombre","exe","motivo","abierto"} o None (= usa tu lógica anterior)."""
    try:
        e = estado(instalados)
        clave, motivo = _decidir(preferido, list(e["abiertos"]), e["primer_plano"], e["puntos"],
                                 e["instalados"], e["predeterminado"], POR_DEFECTO_CONFIG)
        if not clave:
            logger.info(f"🌐 navegadores: sin elección ({motivo})")
            return None
        exe = (e["abiertos"].get(clave) or {}).get("exe") or e["instalados"].get(clave)
        if not exe:
            return None
        logger.info(f"🌐 navegador elegido: {nombre_de(clave)} — {motivo}")
        return {"clave": clave, "nombre": nombre_de(clave), "exe": exe, "motivo": motivo,
                "abierto": clave in e["abiertos"]}
    except Exception:
        logger.exception("❌ navegadores.elegir falló")
        return None


def abrir(url: Optional[str] = None, preferido: Optional[str] = None) -> dict:
    """Abre `url` (o solo el navegador) en el navegador elegido. Si ya está abierto, Windows/el navegador la
    abre como pestaña nueva de la instancia existente. Devuelve {"exito","clave","nombre","motivo","texto"}."""
    fallo = {"exito": False, "clave": None, "nombre": "", "motivo": "", "texto": ""}
    try:
        if os.name != "nt":
            return {**fallo, "texto": "Abrir navegadores solo está disponible en Windows."}
        if url is not None and not re.match(r"^https?://\S+$", str(url).strip(), re.IGNORECASE):
            return {**fallo, "texto": "Solo abro enlaces http o https."}
        sel = elegir(preferido)
        if not sel:
            return {**fallo, "texto": "No encontré un navegador para abrir eso."}
        args = [sel["exe"]] + ([str(url).strip()] if url else [])
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
        if preferido:
            registrar_uso_explicito(sel["clave"])
        return {"exito": True, "clave": sel["clave"], "nombre": sel["nombre"], "motivo": sel["motivo"],
                "texto": f"Abriendo en {sel['nombre']} ({sel['motivo']})."}
    except Exception as e:
        logger.exception("❌ navegadores.abrir falló")
        return {**fallo, "texto": f"No pude abrir el navegador: {type(e).__name__}"}


# ─── MUESTREADOR EN SEGUNDO PLANO ─────────────────────────────────────────

_parar = threading.Event()
_hilo: Optional[threading.Thread] = None


def _bucle() -> None:
    while not _parar.wait(MUESTREO_S):
        try:
            _muestrear()
        except Exception:
            logger.exception("❌ muestreo de navegadores")


def iniciar_muestreo() -> bool:
    """Arranca el aprendizaje de uso (un hilo daemon). Idempotente. Solo en Windows."""
    global _hilo
    if os.name != "nt" or not AUTODETECTAR:
        return False
    with _lock:
        if _hilo is not None and _hilo.is_alive():
            return True
        _parar.clear()
        _hilo = threading.Thread(target=_bucle, name="sara-nav-muestreo", daemon=True)
        _hilo.start()
    logger.info(f"✓ Aprendizaje de navegadores activo (cada {MUESTREO_S:.0f} s)")
    return True


def detener_muestreo() -> None:
    _parar.set()
    _guardar()


# ─── DIAGNÓSTICO (/navegadores) ───────────────────────────────────────────

def diagnostico() -> str:
    if os.name != "nt":
        return "Navegadores: la detección solo funciona en Windows."
    e = estado()
    ab = e["abiertos"]
    abiertos_txt = ", ".join(f"{nombre_de(k)} ({v['ventanas']} ventana{'s' if v['ventanas'] != 1 else ''})"
                             for k, v in ab.items()) or "ninguno"
    rutas_txt = "\n".join(f"   · {nombre_de(k)}: {v['exe']}" for k, v in ab.items())
    puntos = sorted(e["puntos"].items(), key=lambda x: -x[1])
    puntos_txt = " · ".join(f"{nombre_de(k)} {v:.0f}" for k, v in puntos[:6]) or "sin datos todavía"
    inst_txt = ", ".join(nombre_de(k) for k in e["instalados"]) or "ninguno detectado"
    clave, motivo = _decidir(None, list(ab), e["primer_plano"], e["puntos"], e["instalados"],
                             e["predeterminado"], POR_DEFECTO_CONFIG)
    eleccion = f"{nombre_de(clave)} — {motivo}" if clave else motivo
    hilo = "activo" if (_hilo is not None and _hilo.is_alive()) else "APAGADO"
    return ("Navegadores:\n"
            f"Abiertos ahora: {abiertos_txt}\n"
            + (f"{rutas_txt}\n" if rutas_txt else "")
            + f"En primer plano: {nombre_de(e['primer_plano']) if e['primer_plano'] else 'ninguno (o no es un navegador)'}\n"
            f"Predeterminado de Windows: {nombre_de(e['predeterminado']) if e['predeterminado'] else 'no detectado'}\n"
            f"Instalados: {inst_txt}\n"
            f"Uso acumulado (puntos): {puntos_txt}\n"
            f"Aprendizaje: {hilo} · {e['muestras']} muestras desde {e['desde'] or '-'}\n"
            f"Si dijeras \"abre youtube\" ahora: {eleccion}")
