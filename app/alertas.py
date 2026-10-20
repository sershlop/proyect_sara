# 🔔 alertas.py — Alertas proactivas al celular (Telegram) con interruptor maestro persistente (v1)
# Hilo propio (funciona con la ventana de SARA cerrada mientras SARA corra). Batería baja, cargador conectado/
# desconectado, internet perdido (se avisa al RECUPERARLO: sin red no se puede mandar nada), disco casi lleno y
# calentamiento. Antirrebote: enfriamiento por alerta + histéresis. Apagado (ALERTAS_ACTIVAS=False): no envía NADA.
# Natural: «silencia todo / silencia las alertas» · «activa las alertas» · /alertas on|off|estado
ALERTAS_VERSION = "v1"
import json, logging, os, re, socket, threading, time, unicodedata
from collections import deque
from typing import Optional, Tuple
log = logging.getLogger("SARA-ALERTAS")
try:
    import psutil
except Exception:
    psutil = None
try:
    import config
except Exception:
    config = None

def _c(n, d): return getattr(config, n, d) if config is not None else d
def _plano(s): return re.sub(r"[^a-z0-9 ]", " ", "".join(unicodedata.normalize("NFD", ch)[0] for ch in (s or "")).lower()).strip()

# ─── INTERRUPTOR MAESTRO (sara_ajustes.json, mismo archivo que /auto) ─────
def _ruta() -> str:
    base = getattr(config, "BASE_DIR", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(str(base), "sara_ajustes.json")

def _cargar() -> None:
    try:
        with open(_ruta(), encoding="utf-8") as f:
            v = json.load(f).get("ALERTAS_ACTIVAS")
        if isinstance(v, bool) and config is not None:
            config.ALERTAS_ACTIVAS = v
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("sara_ajustes.json ilegible: %s", e)

def _guardar(v: bool) -> bool:
    try:
        try:
            with open(_ruta(), encoding="utf-8") as f:
                d = json.load(f)
            d = d if isinstance(d, dict) else {}
        except Exception:
            d = {}
        d["ALERTAS_ACTIVAS"] = bool(v)
        tmp = _ruta() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _ruta())
        return True
    except Exception as e:
        log.warning("no pude guardar ALERTAS_ACTIVAS: %s", e)
        return False

def activas() -> bool:
    return bool(_c("ALERTAS_ACTIVAS", True))

def fijar(v: bool) -> Tuple[bool, str]:
    if config is not None:
        config.ALERTAS_ACTIVAS = bool(v)
    ok = _guardar(v)
    cola = "" if ok else " (no pude guardarlo: se perderá al reiniciar)"
    return True, ("🔔 Alertas ACTIVADAS: te aviso por Telegram de batería, cargador, internet, disco y calentamiento." if v
                  else "🔕 Todo lo proactivo hacia Telegram está en silencio (alertas y recordatorios). Seguiré respondiendo a tus órdenes.") + cola

# ─── INTÉRPRETE ───────────────────────────────────────────────────────────
_CORT = r"^(?:(?:oye|hey|hola|ok)\s+)?(?:sara\s+)?(?:(?:por favor|porfa|ya|ahora)\s+)*(?:(?:puedes|podrias|quiero que|necesito que)\s+)?"
_OBJ = r"(?:alertas?|avisos?|notificaciones|proactividad|control remoto)"
_RE_OFF = re.compile(_CORT + r"(?:silencia|silenciar|callate|calla|desactiva|desactivar|apaga|apagar|pausa|pausar|quita|deten)\s+(?:todas?\s+)?(?:las\s+|los\s+)?" + _OBJ + r"(?:\s+(?:por favor|porfa|ya|ahora))?$"
                     r"|" + _CORT + r"(?:silencia|silenciar|calla|callate|pausa|pausar)\s+todo$"
                     r"|" + _CORT + r"(?:no\s+me\s+(?:avises|mandes\s+" + _OBJ + r")|que\s+no\s+(?:digas|mandes)\s+nada)$")
_RE_ON = re.compile(_CORT + r"(?:activa|activar|enciende|prende|reactiva|reanuda|pon)\s+(?:de\s+nuevo\s+)?(?:todas?\s+)?(?:las\s+|los\s+)?" + _OBJ + r"(?:\s+(?:por favor|porfa|ya|ahora))?$")
_RE_EST = re.compile(_CORT + r"(?:estan\s+activ[oa]s\s+las\s+" + _OBJ + r"|estado\s+de\s+las\s+" + _OBJ + r")$")

def interpretar(texto: str) -> Optional[str]:
    n = _plano(texto)
    if not n or len(n.split()) > 12:
        return None
    if _RE_OFF.match(n): return "off"
    if _RE_ON.match(n): return "on"
    if _RE_EST.match(n): return "estado"
    return None

def es_comando(texto: str) -> bool:
    return interpretar(texto) is not None

def estado_texto() -> str:
    return (f"Alertas: {'ACTIVAS 🔔' if activas() else 'en silencio 🔕'} · hilo: {'sí' if _hilo else 'no'} · "
            f"batería ≤{_c('ALERTAS_BATERIA_PCT', 20)} % · disco libre <{_c('ALERTAS_DISCO_LIBRE_PCT', 10)} % · "
            f"calor: CPU ≥{_c('ALERTAS_CALOR_CPU', 90)} % sostenida\nUso: /alertas on · /alertas off · «silencia las alertas»")

def ejecutar(texto: str) -> Tuple[bool, str]:
    a = interpretar(texto)
    return fijar(a == "on") if a in ("on", "off") else (True, estado_texto())

def comando(arg: str) -> Tuple[bool, str]:
    a = (arg or "").strip().lower()
    if a in ("on", "activar", "si", "1"): return fijar(True)
    if a in ("off", "apagar", "silencio", "no", "0"): return fijar(False)
    return True, estado_texto()

# ─── MONITOR ──────────────────────────────────────────────────────────────
_ult: dict = {}
_cpu: deque = deque(maxlen=30)
_prev_enchufe: Optional[bool] = None
_net_caido: Optional[float] = None
_net_fallos = 0
_hilo: Optional[threading.Thread] = None

def _enviar(texto: str) -> bool:
    if not activas():
        return False
    try:
        import telegram_bot
        return bool(telegram_bot.enviar_alerta(texto))
    except Exception as e:
        log.warning("no pude enviar alerta: %s", e)
        return False

def _alerta(clave: str, texto: str, repetir: Optional[float] = None) -> None:
    """Antirrebote: misma clave no se repite antes de ALERTAS_REPETIR_S. Se vuelve a armar con _rearmar()."""
    ahora = time.time()
    if ahora - _ult.get(clave, 0) < (repetir if repetir is not None else float(_c("ALERTAS_REPETIR_S", 1800))):
        return
    if _enviar(texto):
        _ult[clave] = ahora

def _rearmar(*claves) -> None:
    for k in claves:
        _ult.pop(k, None)

def _internet() -> bool:
    try:
        socket.create_connection(("1.1.1.1", 53), 2).close()
        return True
    except OSError:
        return False

def _dur(s: float) -> str:
    return f"{int(s)} s" if s < 90 else f"{int(s // 60)} min"

def tick() -> None:
    global _prev_enchufe, _net_caido, _net_fallos
    if psutil is None or not activas():
        return
    ahora = time.time()
    # batería y cargador
    try:
        b = psutil.sensors_battery()
    except Exception:
        b = None
    if b is not None:
        pct, enchufe = float(b.percent), bool(b.power_plugged)
        if _prev_enchufe is not None and enchufe != _prev_enchufe:
            _alerta("cargador", f"🔌 Cargador conectado ({pct:.0f} %)." if enchufe else f"🔋 Cargador desconectado ({pct:.0f} %).", 60)
        _prev_enchufe = enchufe
        bajo, crit = float(_c("ALERTAS_BATERIA_PCT", 20)), float(_c("ALERTAS_BATERIA_CRITICA_PCT", 10))
        if not enchufe and pct <= crit:
            _alerta("bat_crit", f"🔴 Batería crítica: {pct:.0f} % y sin cargador.")
        elif not enchufe and pct <= bajo:
            _alerta("bat_baja", f"⚠️ Batería baja: {pct:.0f} % y sin cargador.")
        if enchufe or pct > bajo + 5:
            _rearmar("bat_baja", "bat_crit")
    # internet (se avisa al recuperarlo)
    if _internet():
        _net_fallos = 0
        if _net_caido is not None:
            d = ahora - _net_caido
            _net_caido = None
            if d >= float(_c("ALERTAS_WIFI_MIN_S", 60)):
                _alerta("wifi", f"📶 Estuve sin internet ~{_dur(d)}. Ya volvió la conexión.", 60)
    else:
        _net_fallos += 1
        if _net_fallos >= 2 and _net_caido is None:
            _net_caido = ahora - 2 * float(_c("ALERTAS_INTERVALO_S", 30))
    # disco
    try:
        raiz = (os.environ.get("SystemDrive", "C:") + "\\") if os.name == "nt" else "/"
        u = psutil.disk_usage(raiz)
        libre = u.free / u.total * 100
        if libre < float(_c("ALERTAS_DISCO_LIBRE_PCT", 10)):
            _alerta("disco", f"💽 Disco casi lleno: solo {libre:.0f} % libre.")
        elif libre > float(_c("ALERTAS_DISCO_LIBRE_PCT", 10)) + 3:
            _rearmar("disco")
    except Exception:
        pass
    # calentamiento: sensor si existe (Linux/macOS); en Windows psutil no lo tiene → CPU sostenida
    temp = None
    try:
        if hasattr(psutil, "sensors_temperatures"):
            t = [x.current for v in (psutil.sensors_temperatures() or {}).values() for x in v if x.current]
            temp = max(t) if t else None
    except Exception:
        temp = None
    _cpu.append(psutil.cpu_percent(interval=None))
    n = int(_c("ALERTAS_CALOR_MUESTRAS", 6))
    ult = list(_cpu)[-n:]
    if temp is not None and temp >= float(_c("ALERTAS_TEMP_C", 85)):
        _alerta("calor", f"🌡️ La laptop está a {temp:.0f} °C. Sácala de la mochila o ventílala.")
    elif temp is None and len(ult) >= n and sum(ult) / n >= float(_c("ALERTAS_CALOR_CPU", 90)):
        _alerta("calor", f"🌡️ CPU al {sum(ult) / n:.0f} % sostenida (sin sensor de temperatura): puede estar calentándose; revisa que no esté cerrada en la mochila.")
    elif temp is None and ult and sum(ult) / len(ult) < 60:
        _rearmar("calor")

def _bucle() -> None:
    time.sleep(10)
    if psutil:
        psutil.cpu_percent(interval=None)
    while True:
        try:
            tick()
        except Exception:
            log.exception("alertas.tick falló")
        time.sleep(max(10.0, float(_c("ALERTAS_INTERVALO_S", 30))))

def iniciar() -> None:
    """Lo llama core_bridge._arranque_pesado. Idempotente."""
    global _hilo
    _cargar()
    if _hilo is None and psutil is not None and _c("ALERTAS_HILO", True):
        _hilo = threading.Thread(target=_bucle, name="sara-alertas", daemon=True)
        _hilo.start()
