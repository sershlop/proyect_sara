# 📁 optimizador.py — Pilar 3 ampliado: SARA analiza, decide y (con /auto on) actúa
#
# Qué hace:
#   * Cada ~20 s (lo llama core_bridge.obtener_eventos_proactivos) mide RAM y CPU.
#   * Si hay saturación sostenida, DIAGNOSTICA: quién consume, si la causa es SARA misma
#     (ventana, voz, modelos) o la IA local (Ollama), y qué procesos son basura en segundo plano.
#   * Con /auto off  -> explica la causa y propone un plan (tarjeta Activar/Ignorar en la GUI).
#   * Con /auto on   -> ejecuta SOLO acciones verdes sola; las amarillas únicamente si la RAM sigue
#                       crítica después de las verdes (máx. 3 apps, cada 10 min).
#
# Niveles:
#   VERDE    reversible o inocuo: descargar la IA local de RAM, vaciar memoria inactiva (working set),
#            borrar temporales viejos, bajar prioridad a quien devora CPU, pasar a IA externa (temporal).
#   AMARILLO cerrar procesos de usuario SIN ventana visible (basura en segundo plano). Nunca editores
#            con trabajo sin guardar (app_actions.cerrar_procesos ya no los fuerza).
#   ROJO     JAMÁS: procesos del sistema, SARA misma, antivirus, apps con ventana, lo que tú protejas.
#
# Degradación elegante: sin psutil devuelve [] y nada se rompe; cualquier error se loguea y se ignora.

import ctypes
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

try:
    import psutil
except Exception:
    psutil = None

try:
    import config
except Exception:
    config = None

logger = logging.getLogger("SARA-OPT")
_IS_WIN = os.name == "nt"
_MB = 1024 ** 2
_GB = 1024 ** 3


def _c(nombre: str, defecto):
    return getattr(config, nombre, defecto) if config is not None else defecto


# ─── ESTADO ───────────────────────────────────────────────────────────────

_lock = threading.Lock()
_hist_cpu: deque = deque(maxlen=10)
_ultimo_aviso: Dict[str, Tuple[str, float]] = {}
_pendientes: Dict[str, dict] = {}
_t_limpieza = 0.0
_t_cierre = 0.0
_ia_cambiada = False          # True si FUE el modo auto quien pasó la IA a externa (así sabe devolverla)
_ORDEN = {"aviso": 1, "critico": 2}

_g = {"externa_disponible": lambda: False, "fijar_externa": None, "externa_activa": lambda: False}


def registrar_ganchos(externa_disponible: Callable, fijar_externa: Callable, externa_activa: Callable) -> None:
    """core_bridge entrega aquí cómo consultar/cambiar el modo de IA (evita imports circulares)."""
    _g["externa_disponible"] = externa_disponible
    _g["fijar_externa"] = fijar_externa
    _g["externa_activa"] = externa_activa


# ─── LISTAS DE SEGURIDAD ──────────────────────────────────────────────────

_SIEMPRE_PROTEGIDOS = {
    "svchost.exe", "dwm.exe", "explorer.exe", "csrss.exe", "lsass.exe", "services.exe", "winlogon.exe",
    "wininit.exe", "smss.exe", "system", "registry", "memcompression", "taskhostw.exe", "conhost.exe",
    "sihost.exe", "ctfmon.exe", "fontdrvhost.exe", "audiodg.exe", "spoolsv.exe", "runtimebroker.exe",
    "searchhost.exe", "searchindexer.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "applicationframehost.exe", "textinputhost.exe", "msmpeng.exe", "securityhealthservice.exe",
    "nissrv.exe", "taskmgr.exe", "python.exe", "pythonw.exe", "py.exe", "msedgewebview2.exe",
}
# Nombres que suenan a servicio/seguridad/sincronización: nunca se cierran solos aunque no tengan ventana.
_RE_SENSIBLE = re.compile(
    r"(driver|service|helper|updater|security|defender|antivir|vpn|audio|bluetooth|nvidia|intel|realtek|"
    r"amd|logi|razer|backup|sync|onedrive|dropbox|cloud|ollama)", re.I)

_ARCHIVO_ESTADO = Path(_c("BASE_DIR", None) or Path(__file__).resolve().parent) / "optimizador_estado.json"


def _leer_protegidos_usuario() -> List[str]:
    try:
        datos = json.loads(_ARCHIVO_ESTADO.read_text(encoding="utf-8"))
        return [str(x).lower() for x in datos.get("protegidos", [])]
    except Exception:
        return []


def _guardar_protegidos_usuario(lista: List[str]) -> bool:
    try:
        tmp = str(_ARCHIVO_ESTADO) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"protegidos": sorted(set(lista))}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _ARCHIVO_ESTADO)
        return True
    except Exception as e:
        logger.warning(f"No pude guardar protegidos: {e}")
        return False


def _protegidos_total() -> set:
    p = set(_SIEMPRE_PROTEGIDOS) | set(_leer_protegidos_usuario())
    try:
        import app_actions
        p |= {str(x).lower() for x in app_actions._PROTEGIDOS}
    except Exception:
        pass
    return p


# ─── LECTURA DEL SISTEMA ──────────────────────────────────────────────────

def _gb(b: float) -> str:
    return f"{b / _GB:.1f} GB" if b >= _GB else f"{b / _MB:.0f} MB"


def _bonito(n: str) -> str:
    n = n[:-4] if n.endswith(".exe") else n
    return n[:1].upper() + n[1:]


def _pids_ventana() -> Tuple[set, int]:
    """PIDs con ventana visible y con título, y PID de la ventana en primer plano."""
    if not _IS_WIN:
        return set(), 0
    pids: set = set()
    fg = ctypes.c_ulong(0)
    try:
        u32 = ctypes.windll.user32
        u32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        u32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        u32.GetForegroundWindow.restype = ctypes.c_void_p
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(hwnd, _):
            try:
                if u32.IsWindowVisible(hwnd) and u32.GetWindowTextLengthW(hwnd) > 0:
                    pid = ctypes.c_ulong(0)
                    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    pids.add(pid.value)
            except Exception:
                pass
            return True

        u32.EnumWindows(enum_proc(cb), 0)
        h = u32.GetForegroundWindow()
        if h:
            u32.GetWindowThreadProcessId(h, ctypes.byref(fg))
    except Exception as e:
        logger.debug(f"_pids_ventana: {e}")
    return pids, fg.value


def _propios() -> set:
    """SARA, sus ancestros (lanzador, terminal) y todos sus hijos (WebView2, node de WhatsApp...)."""
    p = {os.getpid()}
    try:
        import app_actions
        p |= set(app_actions._propios())
    except Exception:
        pass
    try:
        p |= {c.pid for c in psutil.Process(os.getpid()).children(recursive=True)}
    except Exception:
        pass
    return p


def _etiqueta(nombre: str, propio: bool) -> str:
    if nombre.startswith("ollama"):
        return "IA local (Ollama)"
    if propio:
        if "webview2" in nombre:
            return "SARA (ventana)"
        if nombre.startswith("node"):
            return "SARA (WhatsApp)"
        if nombre.startswith("python"):
            return "SARA (núcleo, voz y modelos)"
        return f"SARA ({nombre})"
    return nombre


def _escanear(espera: float = 1.0) -> List[dict]:
    """Dos pasadas separadas por `espera` s para que cpu_percent por proceso tenga base de medición."""
    objs = {}
    for p in psutil.process_iter():
        objs[p.pid] = p
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    time.sleep(espera)
    ventanas, fg = _pids_ventana()
    propios = _propios()
    ncpu = psutil.cpu_count() or 1
    ahora = time.time()
    salida = []
    for pid, p in objs.items():
        try:
            with p.oneshot():
                nombre = (p.name() or "").lower()
                rss = p.memory_info().rss
                cpu = p.cpu_percent(None) / ncpu
                ppid = p.ppid()
                edad = ahora - p.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:
            continue
        es_propio = pid in propios
        salida.append({"pid": pid, "nombre": nombre, "rss": rss, "cpu": cpu, "edad": edad,
                       "ventana": pid in ventanas, "fg": pid == fg, "propio": es_propio,
                       "ia": nombre.startswith("ollama"), "orfano": ppid not in objs,
                       "etiqueta": _etiqueta(nombre, es_propio)})
    return salida


def _agrupar(procs: List[dict]) -> List[dict]:
    g: Dict[str, dict] = {}
    for x in procs:
        d = g.setdefault(x["etiqueta"], {"etiqueta": x["etiqueta"], "nombre": x["nombre"], "rss": 0, "cpu": 0.0,
                                        "pids": [], "ventana": False, "propio": False, "ia": False,
                                        "primer_plano": False, "edad": 1e18, "orfano": False})
        d["rss"] += x["rss"]
        d["cpu"] += x["cpu"]
        d["pids"].append(x["pid"])
        d["ventana"] |= x["ventana"]
        d["propio"] |= x["propio"]
        d["ia"] |= x["ia"]
        d["primer_plano"] |= x["fg"]
        d["orfano"] |= x["orfano"]
        d["edad"] = min(d["edad"], x["edad"])
    return sorted(g.values(), key=lambda d: d["rss"], reverse=True)


def _mi_usuario() -> str:
    try:
        return psutil.Process().username()
    except Exception:
        return ""


def _candidatos_segundo_plano(grupos: List[dict]) -> List[dict]:
    """Procesos de TU usuario, sin ninguna ventana visible, pesados, con >10 min vivos y nada sensible."""
    minimo = float(_c("OPT_MIN_SEGUNDO_PLANO_MB", 150)) * _MB
    prot = _protegidos_total()
    mi = _mi_usuario()
    if not mi:
        return []
    out = []
    for g in grupos:
        if g["propio"] or g["ia"] or g["ventana"] or g["primer_plano"]:
            continue
        n = g["nombre"]
        if n in prot or _RE_SENSIBLE.search(n) or g["rss"] < minimo or g["edad"] < 600:
            continue
        try:
            if psutil.Process(g["pids"][0]).username() != mi:
                continue
        except Exception:
            continue
        out.append(g)
    out.sort(key=lambda g: (g["orfano"], g["rss"]), reverse=True)
    return out


def diagnosticar(espera: float = 1.0) -> dict:
    psutil.cpu_percent(None)
    procs = _escanear(espera)
    cpu_total = psutil.cpu_percent(None)
    vm = psutil.virtual_memory()
    grupos = _agrupar(procs)
    sara = [g for g in grupos if g["propio"] or g["ia"]]
    ajenos = [g for g in grupos if not (g["propio"] or g["ia"])]
    sara_rss = sum(g["rss"] for g in sara)
    sara_cpu = sum(g["cpu"] for g in sara)
    ia_rss = sum(g["rss"] for g in grupos if g["ia"])
    top_rss = ajenos[0]["rss"] if ajenos else 0
    top_cpu = max((g["cpu"] for g in ajenos), default=0.0)
    prot = _protegidos_total()
    umbral_cpu = float(_c("OPT_CPU_PROCESO_ALTO", 25))
    return {
        "ram_pct": vm.percent, "total": vm.total, "cpu_pct": cpu_total, "grupos": grupos, "ajenos": ajenos,
        "sara_rss": sara_rss, "sara_cpu": sara_cpu, "ia_rss": ia_rss,
        "causa_ram": sara_rss >= top_rss and sara_rss / vm.total >= 0.10,
        "causa_cpu": sara_cpu >= top_cpu and sara_cpu >= 15,
        "candidatos": _candidatos_segundo_plano(grupos),
        "objetivos_vaciar": [p for g in ajenos[:6] if g["rss"] >= 100 * _MB and g["nombre"] not in prot
                             and not g["primer_plano"] for p in g["pids"]],
        "cpu_altos": [g for g in ajenos if g["cpu"] >= umbral_cpu and not g["primer_plano"]
                      and g["nombre"] not in prot],
    }


# ─── ACCIONES ─────────────────────────────────────────────────────────────

def liberar_ia_local() -> Tuple[bool, str]:
    """Descarga de la RAM los modelos cargados en Ollama (keep_alive=0). Se recargan solos al usarse."""
    try:
        import requests
    except Exception:
        return False, "Falta la librería requests."
    base = str(_c("OLLAMA_URL", "http://localhost:11434/api/generate")).split("/api/")[0]
    try:
        modelos = [m.get("name") for m in requests.get(base + "/api/ps", timeout=2).json().get("models", [])]
    except Exception:
        return False, "Ollama no responde (o no está en marcha)."
    if not modelos:
        return True, "La IA local ya estaba descargada de la memoria."
    for m in modelos:
        try:
            requests.post(base + "/api/generate", json={"model": m, "keep_alive": 0}, timeout=5)
        except Exception as e:
            logger.warning(f"No pude descargar {m}: {e}")
    return True, f"Descargué la IA local de la memoria ({', '.join(modelos)}); se recarga sola cuando la necesite."


def vaciar_memoria(pids: List[int]) -> Tuple[int, int]:
    """EmptyWorkingSet: devuelve a Windows la memoria inactiva. No cierra ni daña nada. -> (procesos, bytes)"""
    if not (_IS_WIN and psutil and pids):
        return 0, 0
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ps = ctypes.WinDLL("psapi", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    ps.EmptyWorkingSet.argtypes = [ctypes.c_void_p]
    n, liberado = 0, 0
    for pid in pids:
        try:
            antes = psutil.Process(pid).memory_info().rss
            h = k32.OpenProcess(0x0400 | 0x0100, 0, pid)     # QUERY_INFORMATION | SET_QUOTA
            if not h:
                continue
            try:
                if ps.EmptyWorkingSet(h):
                    n += 1
                    liberado += max(0, antes - psutil.Process(pid).memory_info().rss)
            finally:
                k32.CloseHandle(h)
        except Exception:
            continue
    return n, liberado


def limpiar_temporales(dias: Optional[int] = None) -> Tuple[int, int]:
    """Borra archivos de la carpeta TEMP con más de `dias` días sin tocarse. -> (bytes, archivos)"""
    dias = dias or int(_c("OPT_TEMP_DIAS", 3))
    raiz = Path(tempfile.gettempdir())
    limite = time.time() - dias * 86400
    fin = time.time() + 20
    libres, n = 0, 0
    for dirpath, _dirs, files in os.walk(raiz, topdown=False, followlinks=False):
        if time.time() > fin:
            break
        for f in files:
            ruta = os.path.join(dirpath, f)
            try:
                if os.path.islink(ruta):
                    continue
                st = os.stat(ruta)
                if max(st.st_mtime, st.st_atime) < limite:
                    os.remove(ruta)
                    libres += st.st_size
                    n += 1
            except Exception:
                continue            # en uso / sin permiso: se deja
        try:
            if Path(dirpath) != raiz and os.stat(dirpath).st_mtime < limite:
                os.rmdir(dirpath)   # solo si quedó vacía
        except Exception:
            pass
    return libres, n


# ─── PLAN Y EJECUCIÓN ─────────────────────────────────────────────────────

def _paso(clave: str, nivel: str, desc: str, **extra) -> dict:
    return dict({"clave": clave, "nivel": nivel, "desc": desc}, **extra)


def _plan(d: dict, ram_nivel: bool, cpu_alto: bool) -> List[dict]:
    pasos: List[dict] = []
    ia_grande = d["ia_rss"] >= 300 * _MB
    ext_ok = bool(_g["externa_disponible"]()) and not _g["externa_activa"]()
    if ram_nivel:
        if ia_grande:
            pasos.append(_paso("liberar_ia", "verde", f"descargar la IA local de la memoria ({_gb(d['ia_rss'])})"))
        if d["objetivos_vaciar"]:
            pasos.append(_paso("vaciar", "verde", "recuperar la memoria inactiva de las apps que más usan"))
        pasos.append(_paso("temporales", "verde", f"borrar temporales de más de {int(_c('OPT_TEMP_DIAS', 3))} días"))
        if d["candidatos"]:
            top = d["candidatos"][:int(_c("OPT_MAX_CIERRES", 3))]
            pasos.append(_paso("cerrar", "amarillo",
                               "cerrar procesos en segundo plano sin ventana: " +
                               ", ".join(f"{_bonito(g['etiqueta'])} ({_gb(g['rss'])})" for g in top),
                               claves=[g["nombre"] for g in top]))
    if cpu_alto and d["cpu_altos"]:
        pasos.append(_paso("prioridad", "verde", "bajar la prioridad de " +
                           ", ".join(_bonito(g["etiqueta"]) for g in d["cpu_altos"][:3]) + " para que no te frenen"))
    if ext_ok and ((ram_nivel and d["causa_ram"] and ia_grande) or (cpu_alto and d["causa_cpu"])):
        pasos.append(_paso("ia_externa", "verde", "pasar mi cerebro a IA externa temporalmente (/ia local para volver)"))
    vistos, unicos = set(), []
    for p in pasos:
        if p["clave"] not in vistos:
            vistos.add(p["clave"])
            unicos.append(p)
    return unicos


def _h_liberar_ia(p, d):
    ok, t = liberar_ia_local()
    return "descargué la IA local de la RAM" if ok and "ya estaba" not in t else ""


def _h_vaciar(p, d):
    n, lib = vaciar_memoria(d["objetivos_vaciar"])
    return f"recuperé ~{_gb(lib)} de memoria inactiva ({n} procesos)" if n else ""


def _h_temporales(p, d):
    b, n = limpiar_temporales()
    return f"borré {n} temporales viejos ({_gb(b)})" if n else ""


def _h_prioridad(p, d):
    nivel = getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", None)
    if nivel is None:
        return ""
    nombres = []
    for g in d["cpu_altos"][:3]:
        ok = False
        for pid in g["pids"]:
            try:
                psutil.Process(pid).nice(nivel)
                ok = True
            except Exception:
                pass
        if ok:
            nombres.append(_bonito(g["etiqueta"]))
    return ("bajé la prioridad de " + ", ".join(nombres)) if nombres else ""


def _h_ia_externa(p, d):
    global _ia_cambiada
    fijar = _g["fijar_externa"]
    if fijar is None or not _g["externa_disponible"]() or _g["externa_activa"]():
        return ""
    fijar(True)
    _ia_cambiada = True
    liberar_ia_local()
    return "pasé mi cerebro a IA externa (volveré a la local cuando baje la carga)"


def _h_cerrar(p, d):
    try:
        import app_actions
    except Exception:
        return ""
    claves = set(p.get("claves") or [])
    elegidos = [g for g in d["candidatos"] if g["nombre"] in claves][:int(_c("OPT_MAX_CIERRES", 3))]
    pids = [x for g in elegidos for x in g["pids"]]
    if not pids:
        return ""
    r = app_actions.cerrar_procesos(pids, timeout_segundos=5)
    nombres = ", ".join(_bonito(g["etiqueta"]) for g in elegidos)
    return f"cerré {nombres}" if r.get("cerrados") else f"intenté cerrar {nombres}: {r.get('texto', '')}"


_HACER = {"liberar_ia": _h_liberar_ia, "vaciar": _h_vaciar, "temporales": _h_temporales,
          "prioridad": _h_prioridad, "ia_externa": _h_ia_externa, "cerrar": _h_cerrar}


def _ejecutar(pasos: List[dict], niveles: Tuple[str, ...], d: dict) -> List[str]:
    res = []
    for p in pasos:
        if p["nivel"] not in niveles:
            continue
        try:
            t = _HACER[p["clave"]](p, d)
            if t:
                res.append(t)
        except Exception as e:
            logger.warning(f"Paso {p['clave']} falló: {type(e).__name__}: {e}")
    return res


# ─── TEXTOS ───────────────────────────────────────────────────────────────

def _explicar(d: dict, ram_nivel: Optional[str], cpu_alto: bool) -> str:
    partes = []
    if ram_nivel:
        icono = "🔴" if ram_nivel == "critico" else "⚠️"
        top = ", ".join(f"{_bonito(g['etiqueta'])} ({_gb(g['rss'])})" for g in d["grupos"][:3])
        partes.append(f"{icono} RAM al {d['ram_pct']:.0f} %. Lo que más usa: {top}.")
    if cpu_alto:
        top = ", ".join(f"{_bonito(g['etiqueta'])} ({g['cpu']:.0f} %)"
                        for g in sorted(d["grupos"], key=lambda g: g["cpu"], reverse=True)[:3])
        partes.append(f"⚠️ CPU sostenida al {d['cpu_pct']:.0f} %. Lo que más usa: {top}.")
    if (ram_nivel and d["causa_ram"]) or (cpu_alto and d["causa_cpu"]):
        if d["ia_rss"] >= 300 * _MB:
            pct = d["ia_rss"] / d["total"] * 100
            partes.append(f"Ojo: la causa soy yo. La IA local que uso como cerebro (Ollama) ocupa {_gb(d['ia_rss'])} "
                          f"({pct:.0f} % de tu RAM) y está ralentizando tu equipo. Puedo descargarla de la memoria, "
                          f"o pásame a la IA externa con /ia externa (para volver: /ia local).")
        else:
            partes.append(f"Ojo: la causa soy yo (ventana, voz y modelos suman {_gb(d['sara_rss'])}). "
                          f"Puedes apagar el modo voz (🎙️) o cerrar el lector para aligerarme.")
    return " ".join(partes)


def _lineas_plan(pasos: List[dict]) -> str:
    icono = {"verde": "🟢", "amarillo": "🟡"}
    return " ".join(f"{icono[p['nivel']]} {p['desc'][:1].upper() + p['desc'][1:]}." for p in pasos)


def _toca_avisar(clave: str, nivel: str, ahora: float, repeticion: float) -> bool:
    prev = _ultimo_aviso.get(clave)
    if prev is None or _ORDEN[nivel] > _ORDEN[prev[0]] or ahora - prev[1] >= repeticion:
        _ultimo_aviso[clave] = (nivel, ahora)
        return True
    return False


# ─── CICLO PROACTIVO ──────────────────────────────────────────────────────

def paso_proactivo() -> List[Tuple[str, dict]]:
    """Lo llama core_bridge.obtener_eventos_proactivos (~cada 20 s). Devuelve [(tipo_evento, datos)]. Nunca lanza."""
    if psutil is None:
        return []
    try:
        return _ciclo()
    except Exception as e:
        logger.warning(f"paso_proactivo: {type(e).__name__}: {e}", exc_info=True)
        return []


def _ciclo() -> List[Tuple[str, dict]]:
    global _t_limpieza, _t_cierre, _ia_cambiada
    ahora = time.time()
    eventos: List[Tuple[str, dict]] = []
    auto = bool(_c("AUTO_OPTIMIZACION", False))
    ram = psutil.virtual_memory().percent
    cpu = psutil.cpu_percent(interval=None)
    _hist_cpu.append(cpu)

    if auto and ahora - _t_limpieza >= float(_c("OPT_LIMPIEZA_INTERVALO_S", 1800)):      # basura mínima, constante
        _t_limpieza = ahora
        b, n = limpiar_temporales()
        if b >= 50 * _MB:
            eventos.append(("notificacion", {"texto": f"🧹 Limpieza automática: borré {n} temporales viejos ({_gb(b)}).",
                                             "nivel": "exito"}))

    if auto and _ia_cambiada and ram < float(_c("OPT_RAM_RESTAURAR", 65)) and cpu < 50:
        fijar = _g["fijar_externa"]
        if fijar is not None:
            fijar(False)
            _ia_cambiada = False
            eventos.append(("notificacion", {"texto": "✅ La carga bajó: volví a usar la IA local como cerebro.",
                                             "nivel": "exito"}))

    crit, aviso = float(_c("OPT_RAM_CRITICO", 90)), float(_c("OPT_RAM_AVISO", 80))
    nivel_ram = "critico" if ram >= crit else "aviso" if ram >= aviso else None
    n = int(_c("OPT_MUESTRAS_CPU", 3))
    muestras = list(_hist_cpu)[-n:]
    cpu_alto = len(muestras) >= n and sum(muestras) / n >= float(_c("OPT_CPU_SOSTENIDO", 85))

    if not nivel_ram:
        _ultimo_aviso.pop("ram", None)
    if not cpu_alto:
        _ultimo_aviso.pop("cpu", None)
    repeticion = 300.0 if auto else float(_c("OPT_REPETICION_S", 900))
    tratar_ram = bool(nivel_ram) and _toca_avisar("ram", nivel_ram, ahora, repeticion)
    tratar_cpu = cpu_alto and _toca_avisar("cpu", "aviso", ahora, repeticion)
    if not (tratar_ram or tratar_cpu):
        return eventos

    d = diagnosticar(1.0)
    pasos = _plan(d, bool(nivel_ram), cpu_alto)
    expl = _explicar(d, nivel_ram, cpu_alto)

    if auto:
        res = _ejecutar(pasos, ("verde",), d)
        time.sleep(3)
        ram2 = psutil.virtual_memory().percent
        if nivel_ram and ram2 >= crit and ahora - _t_cierre >= 600 and any(p["nivel"] == "amarillo" for p in pasos):
            _t_cierre = ahora
            res += _ejecutar(pasos, ("amarillo",), d)
            time.sleep(2)
            ram2 = psutil.virtual_memory().percent
        accion = "; ".join(res) if res else "no encontré nada seguro que hacer"
        cola = f" RAM {ram:.0f} % → {ram2:.0f} %." if nivel_ram else ""
        eventos.append(("notificacion", {
            "texto": f"{expl} 🤖 Modo automático: {accion}.{cola} (¿algo no debía tocarlo? /auto proteger NOMBRE)",
            "nivel": "aviso",
            "voz": f"Detecté {'la memoria' if nivel_ram else 'el procesador'} saturado y lo optimicé."}))
        return eventos

    sufijo = " (Para que lo haga solo: /auto on)"
    if pasos:
        pid = "opt:" + uuid.uuid4().hex[:8]
        with _lock:
            for k in [k for k, v in _pendientes.items() if ahora - v["t"] > 1800]:
                _pendientes.pop(k, None)
            _pendientes[pid] = {"pasos": pasos, "t": ahora}
        eventos.append(("sugerencia_proactiva", {
            "id_sugerencia": pid, "titulo": "Optimizar el equipo",
            "descripcion": f"{expl} Puedo hacer esto: {_lineas_plan(pasos)}{sufijo}",
            "voz": "Tu equipo está saturado. Te dejé una sugerencia en pantalla para optimizarlo."}))
    else:
        eventos.append(("notificacion", {"texto": expl + sufijo, "nivel": "aviso", "voz": "Tu equipo está saturado."}))
    return eventos


def responder_sugerencia(id_sugerencia: str, respuesta: str) -> dict:
    """Respuesta a la tarjeta 'Optimizar el equipo' (activar / ignorar)."""
    with _lock:
        p = _pendientes.pop(id_sugerencia, None)
    if p is None:
        return {"exito": False, "texto": "Esa sugerencia ya caducó; si sigue saturado te aviso de nuevo."}
    if respuesta != "activar":
        return {"exito": True, "texto": "Entendido, no toco nada. (Para que lo haga solo: /auto on)"}
    antes = psutil.virtual_memory().percent
    d = diagnosticar(0.5)
    res = _ejecutar(p["pasos"], ("verde", "amarillo"), d)
    time.sleep(2)
    despues = psutil.virtual_memory().percent
    if not res:
        return {"exito": False, "texto": "No encontré nada que pudiera hacer con seguridad ahora mismo."}
    return {"exito": True, "texto": f"Listo: {'; '.join(res)}. RAM {antes:.0f} % → {despues:.0f} %."}


# ─── COMANDOS «/» ─────────────────────────────────────────────────────────

def comando_auto(arg: str, guardar: Callable[[bool], bool]) -> Tuple[bool, str]:
    partes = (arg or "").strip().split(maxsplit=1)
    a = partes[0].lower() if partes else "estado"
    resto = partes[1].strip() if len(partes) > 1 else ""
    if a in ("on", "activar"):
        config.AUTO_OPTIMIZACION = True
        ok = guardar(True)
        return True, ("🤖 Modo automático ACTIVADO" + ("" if ok else " (no pude guardarlo: se perderá al reiniciar)") +
                      ".\nSolo: 🟢 limpio temporales viejos cada 30 min, y si la RAM/CPU se saturan descargo la IA local, "
                      "recupero memoria inactiva y bajo la prioridad de lo que devora CPU. Si la RAM sigue crítica, "
                      "cierro hasta 3 procesos pesados SIN ventana (nunca el sistema, ni a mí, ni apps con ventana).\n"
                      "Funciona mientras la ventana de SARA esté abierta. Protege algo con /auto proteger NOMBRE; "
                      "apágalo con /auto off.")
    if a in ("off", "apagar", "desactivar"):
        config.AUTO_OPTIMIZACION = False
        guardar(False)
        return True, "Modo automático APAGADO. Seguiré avisándote y proponiéndote soluciones, pero sin tocar nada solo."
    if a in ("proteger", "protege"):
        if not resto:
            return False, "Uso: /auto proteger NOMBRE (ej. /auto proteger spotify)"
        nombre = resto.lower() if resto.lower().endswith(".exe") else resto.lower() + ".exe"
        lista = _leer_protegidos_usuario()
        if nombre not in lista:
            lista.append(nombre)
        return (True, f"Listo: nunca tocaré {nombre}.") if _guardar_protegidos_usuario(lista) \
            else (False, "No pude guardar la lista de protegidos.")
    if a in ("soltar", "desproteger"):
        nombre = resto.lower() if resto.lower().endswith(".exe") else resto.lower() + ".exe"
        lista = [x for x in _leer_protegidos_usuario() if x != nombre]
        _guardar_protegidos_usuario(lista)
        return True, f"{nombre} ya no está en tu lista de protegidos."
    prot = ", ".join(_leer_protegidos_usuario()) or "ninguno"
    return True, (f"Modo automático: {'ACTIVO 🤖' if _c('AUTO_OPTIMIZACION', False) else 'apagado'}\n"
                  f"Umbrales: RAM aviso {_c('OPT_RAM_AVISO', 80)} % · crítico {_c('OPT_RAM_CRITICO', 90)} % · "
                  f"CPU sostenida {_c('OPT_CPU_SOSTENIDO', 85)} %\nProtegidos por ti: {prot}\n"
                  "Uso: /auto on · /auto off · /auto proteger NOMBRE · /auto soltar NOMBRE")


def comando_optimizar(arg: str) -> Tuple[bool, str]:
    if psutil is None:
        return False, "Necesito psutil para analizar el equipo."
    a = (arg or "").strip().lower()
    if a == "ia":
        ok, t = liberar_ia_local()
        return ok, t
    d = diagnosticar(1.0)
    if a == "ahora":
        antes = d["ram_pct"]
        pasos = _plan(d, True, d["cpu_pct"] >= 60)
        res = _ejecutar(pasos, ("verde",), d)
        time.sleep(2)
        return True, (("Hecho: " + "; ".join(res) + ".") if res else "No había nada seguro que hacer.") + \
            f" RAM {antes:.0f} % → {psutil.virtual_memory().percent:.0f} %."
    if a == "cerrar":
        pasos = [p for p in _plan(d, True, False) if p["clave"] == "cerrar"]
        if not pasos:
            return True, "No encontré procesos pesados en segundo plano que se puedan cerrar con seguridad."
        return True, "Hecho: " + "; ".join(_ejecutar(pasos, ("amarillo",), d)) + "."
    ram_nivel = "critico" if d["ram_pct"] >= float(_c("OPT_RAM_CRITICO", 90)) else \
        "aviso" if d["ram_pct"] >= float(_c("OPT_RAM_AVISO", 80)) else None
    cpu_alto = d["cpu_pct"] >= float(_c("OPT_CPU_SOSTENIDO", 85))
    top = ", ".join(f"{_bonito(g['etiqueta'])} ({_gb(g['rss'])})" for g in d["grupos"][:5])
    lineas = [f"RAM {d['ram_pct']:.0f} % · CPU {d['cpu_pct']:.0f} % · SARA+IA local usan {_gb(d['sara_rss'])}",
              f"Top memoria: {top}"]
    expl = _explicar(d, ram_nivel or ("aviso" if d["causa_ram"] else None), cpu_alto)
    if expl and (ram_nivel or cpu_alto or d["causa_ram"]):
        lineas.append(expl)
    pasos = _plan(d, True, d["cpu_pct"] >= 60)
    lineas.append("Lo que puedo hacer:\n" + "\n".join(
        f"  {'🟢' if p['nivel'] == 'verde' else '🟡'} {p['desc']}" for p in pasos) if pasos else "No veo nada que optimizar.")
    lineas.append("Uso: /optimizar ahora (verdes) · /optimizar cerrar (🟡) · /optimizar ia · /auto on (que lo haga solo)")
    return True, "\n".join(lineas)
