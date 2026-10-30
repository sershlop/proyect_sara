import re
import secrets
import threading
import time
import unicodedata
from typing import Dict, Optional, Tuple

try:
    import config
except Exception:
    config = None

_lock = threading.RLock()
_sesiones: Dict[str, dict] = {}
_MAX_FOTOGRAMA = 1_000_000
PERFILES = {2, 5, 8, 12}


def _c(nombre: str, defecto):
    return getattr(config, nombre, defecto) if config is not None else defecto


def _palabras(texto: str) -> set:
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return set(re.findall(r"[a-z0-9]+", t))


def interpretar(texto: str) -> Optional[str]:
    palabras = _palabras(texto)
    if "pantalla" not in palabras or not (palabras & {"celular", "telefono", "movil", "cel"}):
        return None
    if not (
        palabras & {"vivo", "viva", "directo", "directa", "stream", "streaming", "transmision"}
        or {"tiempo", "real"} <= palabras
    ):
        return None
    if palabras & {
        "deten", "detener", "para", "parar", "desactiva", "desactivar", "cierra", "cerrar",
        "termina", "terminar", "finaliza", "finalizar", "apaga", "apagar",
    }:
        return "detener"
    if palabras & {
        "activa", "activar", "inicia", "iniciar", "abre", "abrir", "muestra", "mostrar",
        "muestrame", "ensena", "ver", "quiero", "pon", "poner", "comparte", "compartir", "transmite",
    }:
        return "iniciar"
    return None


def iniciar(dispositivo: str, solicitado_desde: str = "laptop") -> Tuple[Optional[str], str]:
    if not _c("CEL_PANTALLA_CELULAR_ACTIVA", True):
        return None, "La pantalla en vivo del celular está desactivada en la configuración."
    with _lock:
        ahora = time.monotonic()
        for dev, sesion in list(_sesiones.items()):
            recibio = sesion["recibido"]
            limite = 120 if recibio == 0 else 30
            marca = sesion["creada"] if recibio == 0 else recibio
            if ahora - marca > limite:
                _sesiones.pop(dev, None)
        actual = _sesiones.get(dispositivo)
        if actual and not actual["detenida"]:
            return None, "Ya hay una transmisión de ese celular activa."
        if any(not s["detenida"] for s in _sesiones.values()):
            return None, "Ya hay una transmisión de pantalla del celular activa."
        sid = secrets.token_hex(5)
        _sesiones[dispositivo] = {
            "id": sid,
            "ultimo_indice": -1,
            "fotograma": None,
            "recibido": 0.0,
            "creada": time.monotonic(),
            "detenida": False,
            "solicitado_desde": solicitado_desde if solicitado_desde in ("laptop", "celular") else "laptop",
            "fps": 2,
            "control": False,
        }
        return sid, ""


def cambiar_perfil(dispositivo: str, sid: str, fps: int) -> bool:
    if type(fps) is not int or fps not in PERFILES:
        return False
    with _lock:
        actual = _sesiones.get(dispositivo)
        if actual is None or actual["id"] != sid or actual["detenida"]:
            return False
        actual["fps"] = fps
        return True


def cambiar_control(dispositivo: str, sid: str, activo: bool) -> bool:
    if type(activo) is not bool:
        return False
    with _lock:
        actual = _sesiones.get(dispositivo)
        if actual is None or actual["id"] != sid or actual["detenida"]:
            return False
        actual["control"] = activo
        return True


def control_activo(dispositivo: str, sid: str) -> bool:
    with _lock:
        actual = _sesiones.get(dispositivo)
        return bool(actual and actual["id"] == sid and not actual["detenida"] and actual["control"])


def aceptar_fotograma(dispositivo: str, sid: str, indice: int, datos: bytes) -> bool:
    if not isinstance(datos, bytes) or not datos or len(datos) > _MAX_FOTOGRAMA:
        return False
    with _lock:
        actual = _sesiones.get(dispositivo)
        if (
            actual is None or actual["id"] != sid or actual["detenida"]
            or indice <= actual["ultimo_indice"]
        ):
            return False
        actual["ultimo_indice"] = indice
        actual["fotograma"] = datos
        actual["recibido"] = time.monotonic()
        return True


def fotograma(dispositivo: str, sid: str) -> Optional[Tuple[int, bytes]]:
    with _lock:
        actual = _sesiones.get(dispositivo)
        if (
            actual is None or actual["id"] != sid or actual["detenida"]
            or actual["fotograma"] is None
        ):
            return None
        return actual["ultimo_indice"], actual["fotograma"]


def activa(dispositivo: str, sid: str) -> bool:
    with _lock:
        actual = _sesiones.get(dispositivo)
        if not actual or actual["id"] != sid or actual["detenida"]:
            return False
        recibido = actual["recibido"]
        limite = 120 if recibido == 0 else 30
        marca = actual["creada"] if recibido == 0 else recibido
        if time.monotonic() - marca > limite:
            _sesiones.pop(dispositivo, None)
            return False
        return True


def activa_dispositivo(dispositivo: str) -> bool:
    with _lock:
        actual = _sesiones.get(dispositivo)
        if actual is None or actual["detenida"]:
            return False
        recibio = actual["recibido"]
        limite = 120 if recibio == 0 else 30
        marca = actual["creada"] if recibio == 0 else recibio
        if time.monotonic() - marca > limite:
            _sesiones.pop(dispositivo, None)
            return False
        return True


def detener(dispositivo: str, sid: Optional[str] = None) -> Optional[str]:
    with _lock:
        actual = _sesiones.get(dispositivo)
        if actual is None or (sid is not None and actual["id"] != sid):
            return None
        _sesiones.pop(dispositivo, None)
        actual["detenida"] = True
        actual["fotograma"] = None
        return actual["id"]


def detener_todas() -> None:
    with _lock:
        for actual in _sesiones.values():
            actual["detenida"] = True
            actual["fotograma"] = None
        _sesiones.clear()
