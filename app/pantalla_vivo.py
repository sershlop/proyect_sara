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

_lock = threading.Lock()
_sesiones: Dict[str, dict] = {}
_MAX_ESPERA_SIN_ABRIR = 90
PERFILES = {2: 1, 5: 1, 8: 1, 12: 1}


def _c(nombre: str, defecto):
    return getattr(config, nombre, defecto) if config is not None else defecto


def _normalizar(texto: str) -> set:
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return set(re.findall(r"[a-z0-9]+", t))


def interpretar(texto: str) -> Optional[str]:
    palabras = _normalizar(texto)
    if palabras & {"celular", "telefono", "movil", "cel"}:
        return None
    if "pantalla" not in palabras or not (
        {"vivo", "viva", "directo", "directa", "stream", "streaming", "transmision"} & palabras
        or {"tiempo", "real"} <= palabras
    ):
        return None
    return _interpretar_accion(palabras)


def _interpretar_accion(palabras: set) -> Optional[str]:
    if palabras & {"deten", "detener", "para", "parar", "desactiva", "desactivar", "cierra", "cerrar",
                   "termina", "terminar", "finaliza", "finalizar", "apaga", "apagar"}:
        return "detener"
    if palabras & {"activa", "activar", "inicia", "iniciar", "abre", "abrir", "muestra", "mostrar", "muestrame",
                   "ensena", "ver", "quiero", "pon", "poner", "comparte", "compartir", "transmite"}:
        return "iniciar"
    return None


def resolver_intencion(texto: str, origen: str) -> Optional[Tuple[str, str]]:
    palabras = _normalizar(texto)
    if "pantalla" not in palabras or not (
        palabras & {"vivo", "viva", "directo", "directa", "stream", "streaming", "transmision"}
        or {"tiempo", "real"} <= palabras
    ):
        return None
    accion = _interpretar_accion(palabras)
    if accion is None:
        return None
    texto_plano = " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFD", str(texto or "").lower())
                                      .encode("ascii", "ignore").decode("ascii")))
    grupo_laptop = r"(?:laptop|computadora|ordenador|compu|pc|portatil)"
    grupo_celular = r"(?:celular|telefono|movil|cel)"
    dispositivo_fuente = re.search(
        rf"\b(?:de|del)\s+(?:mi|la|el|tu)?\s*({grupo_laptop}|{grupo_celular})\b", texto_plano
    )
    dispositivo_destino = re.search(
        rf"\b(?:en|a|para|hacia)\s+(?:mi|la|el|tu)?\s*({grupo_laptop}|{grupo_celular})\b", texto_plano
    )
    if dispositivo_fuente:
        es_laptop = bool(re.fullmatch(grupo_laptop, dispositivo_fuente.group(1)))
        direccion = "laptop-a-celular" if es_laptop else "celular-a-laptop"
    elif dispositivo_destino:
        es_laptop = bool(re.fullmatch(grupo_laptop, dispositivo_destino.group(1)))
        direccion = "celular-a-laptop" if es_laptop else "laptop-a-celular"
    elif palabras & {"celular", "telefono", "movil", "cel"} and not palabras & {
        "laptop", "computadora", "ordenador", "compu", "pc", "portatil"
    }:
        direccion = "celular-a-laptop"
    elif palabras & {"laptop", "computadora", "ordenador", "compu", "pc", "portatil"} and not palabras & {
        "celular", "telefono", "movil", "cel"
    }:
        direccion = "laptop-a-celular"
    else:
        direccion = "laptop-a-celular" if origen == "celular" else "celular-a-laptop"
    return direccion, accion


def iniciar(dispositivo: str, solicitado_desde: str = "laptop") -> Tuple[Optional[str], str]:
    if not _c("CEL_PANTALLA_VIVO_ACTIVA", True):
        return None, "La pantalla en vivo está desactivada en la configuración de SARA."
    with _lock:
        for dev, sesion in list(_sesiones.items()):
            if sesion["evento"].is_set():
                _sesiones.pop(dev, None)
            elif dev == dispositivo:
                if sesion["abierta"]:
                    return None, "La pantalla en vivo ya está abierta en este celular."
                return sesion["id"], ""
            else:
                return None, "Ya hay una sesión de pantalla en vivo activa en otro dispositivo."
        sid = secrets.token_hex(5)
        _sesiones[dispositivo] = {
            "id": sid,
            "evento": threading.Event(),
            "creada": time.monotonic(),
            "abierta": False,
            "solicitado_desde": solicitado_desde if solicitado_desde in ("laptop", "celular") else "laptop",
            "fps": 2,
            "control": False,
        }
        return sid, ""


def metadatos(dispositivo: str, sid: str) -> Optional[dict]:
    with _lock:
        actual = _sesiones.get(dispositivo)
        if actual is None or actual["id"] != sid or actual["evento"].is_set():
            return None
        if not actual["abierta"] and time.monotonic() - actual["creada"] > _MAX_ESPERA_SIN_ABRIR:
            actual["evento"].set()
            _sesiones.pop(dispositivo, None)
            return None
        return {
            "solicitado_desde": actual["solicitado_desde"],
            "fps": actual["fps"],
            "escala": PERFILES[actual["fps"]],
            "control": actual["control"],
        }


def cambiar_perfil(dispositivo: str, sid: str, fps: int) -> bool:
    if type(fps) is not int or fps not in PERFILES:
        return False
    with _lock:
        actual = _sesiones.get(dispositivo)
        if actual is None or actual["id"] != sid or actual["evento"].is_set():
            return False
        actual["fps"] = fps
        return True


def cambiar_control(dispositivo: str, sid: str, activo: bool) -> bool:
    if type(activo) is not bool:
        return False
    with _lock:
        actual = _sesiones.get(dispositivo)
        if actual is None or actual["id"] != sid or actual["evento"].is_set():
            return False
        actual["control"] = activo
        return True


def control_activo(dispositivo: str, sid: str) -> bool:
    datos = metadatos(dispositivo, sid)
    return bool(datos and datos["control"])


def sesion(dispositivo: str, sid: str) -> Optional[threading.Event]:
    with _lock:
        actual = _sesiones.get(dispositivo)
        if not actual or actual["id"] != sid or actual["evento"].is_set():
            return None
        if not actual["abierta"] and time.monotonic() - actual["creada"] > _MAX_ESPERA_SIN_ABRIR:
            actual["evento"].set()
            _sesiones.pop(dispositivo, None)
            return None
        if actual["abierta"]:
            return None
        actual["abierta"] = True
        return actual["evento"]


def detener(dispositivo: str) -> bool:
    with _lock:
        actual = _sesiones.pop(dispositivo, None)
        if actual is None:
            return False
        actual["evento"].set()
        return True


def activa(dispositivo: str) -> bool:
    with _lock:
        actual = _sesiones.get(dispositivo)
        if actual is None or actual["evento"].is_set():
            return False
        if not actual["abierta"] and time.monotonic() - actual["creada"] > _MAX_ESPERA_SIN_ABRIR:
            actual["evento"].set()
            _sesiones.pop(dispositivo, None)
            return False
        return True


def cerrar(dispositivo: str, sid: str) -> None:
    with _lock:
        actual = _sesiones.get(dispositivo)
        if actual and actual["id"] == sid:
            actual["evento"].set()
            _sesiones.pop(dispositivo, None)


def detener_todas() -> None:
    with _lock:
        sesiones = list(_sesiones.values())
        _sesiones.clear()
    for actual in sesiones:
        actual["evento"].set()
