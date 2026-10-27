# 🫀 energia_viva.py — Mantiene a SARA (laptop) viva con la pantalla apagada o bloqueada (v1)
#
# PROBLEMA: tu equipo usa «Modo de espera moderno» (S0, `powercfg /a` → «Inactivo de baja energía S0 · Red conectada»).
# Al apagarse la pantalla, Windows entra en ese modo y el «Desktop Activity Moderator» puede congelar los procesos de
# escritorio (como server.py). Congelado, el servidor del celular (puerto 8770) deja de contestar y la foto no se toma.
#
# QUÉ HACE (solo la librería estándar, con ctypes; sin dependencias nuevas):
#   1. Pide a Windows, con una «solicitud de energía» (PowerCreateRequest/PowerSetRequest), que NO suspenda este
#      proceso: SystemRequired + ExecutionRequired. NO pide mantener la pantalla encendida (DisplayRequired).
#   2. Mantiene además SetThreadExecutionState(CONTINUOUS|SYSTEM_REQUIRED) en un hilo propio (es por hilo).
#   3. Un latido cada LATIDO_S segundos mide si el proceso estuvo congelado. Si hubo un hueco, lo deja en el log:
#      «🧊 SARA estuvo congelada N s». Eso es la EVIDENCIA para saber si la solicitud de energía basta.
#
# Es aislado y opcional (como volumen.py / apagado.py): si falta o falla, SARA sigue igual. No toca el plan de energía,
# no necesita administrador y se apaga con ENERGIA_VIVA_ACTIVA = False en config.py.
# [probado: compila] — NO se ha ejecutado en Windows real. Léelo como hipótesis hasta ver el log.
ENERGIA_VIVA_VERSION = "v1"
import ctypes
import logging
import os
import threading
import time
from typing import Optional

log = logging.getLogger("SARA-ENERGIA-VIVA")
try:
    import config as _cfg
except Exception:
    _cfg = None


def _c(n, d):
    return getattr(_cfg, n, d) if _cfg is not None else d


# Constantes de Windows
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_REQ_SYSTEM = 1          # PowerRequestSystemRequired
_REQ_EXECUTION = 3       # PowerRequestExecutionRequired
_REASON_SIMPLE = 0x1     # POWER_REQUEST_CONTEXT_SIMPLE_STRING

_estado = {"activa": False, "handle": None, "request": [], "ultimo_latido": 0.0, "congelamientos": 0,
           "congelada_total_s": 0.0}
_lock = threading.Lock()


class _DETALLE(ctypes.Structure):
    _fields_ = [("LocalizedReasonModule", ctypes.c_void_p), ("LocalizedReasonId", ctypes.c_uint32),
                ("ReasonStringCount", ctypes.c_uint32), ("ReasonStrings", ctypes.c_void_p)]


class _RAZON_U(ctypes.Union):
    _fields_ = [("Detailed", _DETALLE), ("SimpleReasonString", ctypes.c_wchar_p)]


class _REASON_CONTEXT(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_uint32), ("Flags", ctypes.c_uint32), ("Reason", _RAZON_U)]


def _solicitud_energia() -> bool:
    """PowerCreateRequest + PowerSetRequest(System, Execution). Devuelve True si al menos una quedó puesta."""
    k32 = ctypes.windll.kernel32
    k32.PowerCreateRequest.restype = ctypes.c_void_p
    k32.PowerCreateRequest.argtypes = [ctypes.POINTER(_REASON_CONTEXT)]
    k32.PowerSetRequest.restype = ctypes.c_int
    k32.PowerSetRequest.argtypes = [ctypes.c_void_p, ctypes.c_int]
    ctx = _REASON_CONTEXT()
    ctx.Version = 0
    ctx.Flags = _REASON_SIMPLE
    ctx.Reason.SimpleReasonString = "SARA: enlace con el celular y control remoto"
    h = k32.PowerCreateRequest(ctypes.byref(ctx))
    if not h or h == ctypes.c_void_p(-1).value:
        log.warning("🫀 PowerCreateRequest falló (error %s)", ctypes.get_last_error() or ctypes.windll.kernel32.GetLastError())
        return False
    puestas = []
    for nombre, tipo in (("SystemRequired", _REQ_SYSTEM), ("ExecutionRequired", _REQ_EXECUTION)):
        ok = bool(k32.PowerSetRequest(h, tipo))
        log.info("🫀 PowerSetRequest(%s): %s", nombre, "OK" if ok else f"FALLÓ (error {k32.GetLastError()})")
        if ok:
            puestas.append(nombre)
    _estado["handle"] = h            # se conserva vivo mientras viva el proceso
    _estado["request"] = puestas
    return bool(puestas)


def _hilo_estado_ejecucion() -> None:
    """SetThreadExecutionState vale solo para el hilo que lo llama: este hilo no termina nunca."""
    try:
        r = ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint32(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED))
        log.info("🫀 SetThreadExecutionState(CONTINUOUS|SYSTEM_REQUIRED): %s", "OK" if r else "FALLÓ")
        while True:
            time.sleep(3600)
    except Exception:
        log.exception("hilo de estado de ejecución falló")


def _latido() -> None:
    """Mide huecos de tiempo: si el proceso estuvo congelado, el reloj saltó entre dos latidos."""
    intervalo = max(2.0, float(_c("ENERGIA_VIVA_LATIDO_S", 5)))
    umbral = max(intervalo * 3, float(_c("ENERGIA_VIVA_UMBRAL_CONGELADA_S", 20)))
    _estado["ultimo_latido"] = time.monotonic()
    ultimo_reloj = time.time()
    while True:
        time.sleep(intervalo)
        ahora_m, ahora_r = time.monotonic(), time.time()
        # monotonic no avanza durante el sueño profundo en algunos equipos; time.time() sí: se usan los dos.
        hueco = max(ahora_m - _estado["ultimo_latido"], ahora_r - ultimo_reloj) - intervalo
        _estado["ultimo_latido"], ultimo_reloj = ahora_m, ahora_r
        if hueco > umbral:
            with _lock:
                _estado["congelamientos"] += 1
                _estado["congelada_total_s"] += hueco
            log.warning("🧊 SARA estuvo congelada ~%d s (hueco entre latidos; pantalla apagada o equipo dormido). "
                        "Solicitud de energía activa: %s", int(hueco), ", ".join(_estado["request"]) or "NINGUNA")


def activar() -> bool:
    """Idempotente y nunca lanza. Lo llama server.py al arrancar."""
    try:
        with _lock:
            if _estado["activa"]:
                return True
            if not _c("ENERGIA_VIVA_ACTIVA", True):
                log.info("🫀 energía viva desactivada por config (ENERGIA_VIVA_ACTIVA=False)")
                return False
            _estado["activa"] = True
        if os.name == "nt":
            try:
                _solicitud_energia()
            except Exception:
                log.exception("🫀 la solicitud de energía falló; sigo con SetThreadExecutionState")
            threading.Thread(target=_hilo_estado_ejecucion, name="sara-energia-ejec", daemon=True).start()
        else:
            log.info("🫀 no es Windows: solo se activa el latido")
        threading.Thread(target=_latido, name="sara-energia-latido", daemon=True).start()
        log.info("🫀 energía viva %s activa (latido cada %s s)", ENERGIA_VIVA_VERSION, _c("ENERGIA_VIVA_LATIDO_S", 5))
        return True
    except Exception:
        log.exception("🫀 no pude activar la energía viva")
        return False


def estado() -> dict:
    with _lock:
        return {"version": ENERGIA_VIVA_VERSION, "activa": _estado["activa"], "solicitudes": list(_estado["request"]),
                "congelamientos": _estado["congelamientos"], "congelada_total_s": int(_estado["congelada_total_s"])}


if __name__ == "__main__":
    # Prueba suelta: python energia_viva.py  → deja la consola abierta; apaga la pantalla y revisa los avisos 🧊.
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s — %(levelname)s: %(message)s")
    activar()
    print("Activa. Apaga la pantalla unos minutos y vuelve: si hubo congelación verás «🧊 SARA estuvo congelada». Ctrl+C para salir.")
    try:
        while True:
            time.sleep(30)
            print(estado())
    except KeyboardInterrupt:
        pass
