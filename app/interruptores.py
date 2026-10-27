# 🎚️ interruptores.py — Switches persistentes de SARA: descripción de pantalla con IA y verificación (TOTP) (v1)
#   «activa / desactiva la descripción de pantalla con IA»  ·  /descripcion on|off|estado
#   «desactiva la verificación» / «ya no me pidas el código»  ·  «activa la verificación»  ·  /verificacion on|off|estado
# Se guardan en sara_ajustes.json (mismo archivo que /auto y /alertas) y se restauran al arrancar.
# Seguridad: la verificación se puede ENCENDER desde cualquier canal, pero APAGAR solo desde la ventana de SARA
# (si alguien tuviera tu Telegram, no podría quitar el candado desde ahí).
INTERRUPTORES_VERSION = "v1"
import json, logging, os, re, unicodedata
from typing import Optional, Tuple
log = logging.getLogger("SARA-INTERRUPTORES")
try:
    import config
except Exception:
    config = None

_PERSISTENTES = ("PANTALLA_DESCRIBIR", "VERIFICACION_ACTIVA")
_REMOTOS = ("whatsapp", "telegram")

def _c(n, d): return getattr(config, n, d) if config is not None else d
def _plano(s): return re.sub(r"[^a-z0-9 ]", " ", "".join(unicodedata.normalize("NFD", ch)[0] for ch in (s or "")).lower()).strip()

def _ruta() -> str:
    base = getattr(config, "BASE_DIR", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(str(base), "sara_ajustes.json")

def _leer() -> dict:
    try:
        with open(_ruta(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("sara_ajustes.json ilegible: %s", e)
        return {}

def cargar() -> None:
    d = _leer()
    for n in _PERSISTENTES:
        if isinstance(d.get(n), bool) and config is not None:
            setattr(config, n, d[n])

def _guardar(nombre: str, valor: bool) -> bool:
    try:
        d = _leer()
        d[nombre] = bool(valor)
        tmp = _ruta() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _ruta())
        return True
    except Exception as e:
        log.warning("no pude guardar %s: %s", nombre, e)
        return False

def descripcion_activa() -> bool:
    return bool(_c("PANTALLA_DESCRIBIR", False))

def verificacion_activa() -> bool:
    return bool(_c("VERIFICACION_ACTIVA", True))

def fijar_descripcion(v: bool) -> Tuple[bool, str]:
    if config is not None:
        config.PANTALLA_DESCRIBIR = bool(v)
    cola = "" if _guardar("PANTALLA_DESCRIBIR", v) else " (no pude guardarlo: se perderá al reiniciar)"
    if v:
        return True, ("🧠 Descripción con IA ACTIVADA: cuando pidas «dime qué pasa en la pantalla» o «qué significa este error», "
                      "mandaré la captura a Gemini (servicio externo) para que la lea." + cola)
    return True, "🧠 Descripción con IA APAGADA: solo te mando la captura, sin enviarla a ningún servicio externo." + cola

def fijar_verificacion(v: bool, origen: Optional[str] = None) -> Tuple[bool, str]:
    if not v and origen in _REMOTOS:
        return False, ("🔒 Por seguridad la verificación solo se puede APAGAR desde la ventana de SARA. "
                       "Encenderla sí se puede desde aquí.")
    if config is not None:
        config.VERIFICACION_ACTIVA = bool(v)
    cola = "" if _guardar("VERIFICACION_ACTIVA", v) else " (no pude guardarlo: se perderá al reiniciar)"
    try:                                        # al cambiar el estado se cierra la ventana de desbloqueo vigente
        import desbloqueo
        desbloqueo.cerrar()
        import celular
        celular.cerrar_ventanas()
    except Exception:
        pass
    if v:
        return True, "🔐 Verificación ACTIVADA: vuelvo a pedir tu código del autenticador para archivos, pantalla, cámara y apagado remoto." + cola
    return True, ("🔓 Verificación APAGADA: ya no pido código para archivos, pantalla, cámara ni apagado remoto "
                  "(el apagado remoto sigue pidiendo el botón de confirmación). Para volver a protegerlo: «activa la verificación»." + cola)
# ─── INTÉRPRETE ───────────────────────────────────────────────────────────
_CORT = r"^(?:(?:oye|hey|hola|ok)\s+)?(?:sara[\s,]+)?(?:(?:por favor|porfa|ya|ahora)\s+)*(?:(?:puedes|podrias|quiero que|necesito que)\s+)?"
_ON = r"(?:activa|activar|enciende|encender|prende|prender|habilita|habilitar|reactiva|reactivar|pon)"
_OFF = r"(?:desactiva|desactivar|apaga|apagar|quita|quitar|deshabilita|deshabilitar|deten)"
_COLA = r"(?:\s+(?:por favor|porfa|ya|ahora))?$"
_OBJ_DESC = r"(?:la\s+)?(?:descripcion|lectura|vision|interpretacion|analisis)(?:\s+de(?:\s+la)?\s+pantalla)?(?:\s+con(?:\s+la)?\s+(?:ia|inteligencia artificial))?"
_OBJ_VERIF = (r"(?:la\s+|el\s+)?(?:verificacion|codigo(?:\s+de\s+verificacion)?|totp|autenticacion|2fa|doble\s+factor|"
              r"protocolo(?:\s+de\s+(?:verificacion|seguridad))?)(?:\s+(?:de\s+)?(?:seguridad|dos\s+pasos))?")
_RE_D_ON = re.compile(_CORT + _ON + r"\s+" + _OBJ_DESC + _COLA)
_RE_D_OFF = re.compile(_CORT + _OFF + r"\s+" + _OBJ_DESC + _COLA)
_RE_V_ON = re.compile(_CORT + _ON + r"\s+" + _OBJ_VERIF + _COLA + r"|" + _CORT + r"(?:pideme|vuelve a pedir|pide)\s+(?:de nuevo\s+)?(?:el\s+)?codigo(?:\s+(?:otra vez|de nuevo))?" + _COLA)
_RE_V_OFF = re.compile(_CORT + _OFF + r"\s+" + _OBJ_VERIF + _COLA + r"|" + _CORT + r"(?:ya\s+)?no\s+(?:me\s+)?(?:pidas|pides|pida)\s+(?:mas\s+)?(?:el\s+)?codigo(?:\s+(?:de verificacion|mas))?" + _COLA)
_RE_V_EST = re.compile(_CORT + r"(?:esta\s+activ[ao]\s+(?:la\s+|el\s+)?|estado\s+de\s+(?:la\s+|el\s+))" + _OBJ_VERIF + r"\??$")
_RE_D_EST = re.compile(_CORT + r"(?:esta\s+activ[ao]\s+|estado\s+de\s+)" + _OBJ_DESC + r"\??$")

def interpretar(texto: str) -> Optional[Tuple[str, Optional[bool]]]:
    """(«descripcion»|«verificacion», True|False|None=estado) o None."""
    n = _plano(texto)
    if not n or len(n.split()) > 14:
        return None
    desc_ok = ("pantalla" in n or re.search(r"\b(?:ia|inteligencia)\b", n) is not None)
    if desc_ok and _RE_D_ON.match(n): return "descripcion", True
    if desc_ok and _RE_D_OFF.match(n): return "descripcion", False
    if desc_ok and _RE_D_EST.match(n): return "descripcion", None
    if _RE_V_ON.match(n): return "verificacion", True
    if _RE_V_OFF.match(n): return "verificacion", False
    if _RE_V_EST.match(n): return "verificacion", None
    return None

def es_comando(texto: str) -> bool:
    return interpretar(texto) is not None

def estado_texto(cual: str) -> str:
    if cual == "descripcion":
        return (f"Descripción de pantalla con IA: {'ACTIVADA 🧠 (manda la captura a Gemini)' if descripcion_activa() else 'apagada'}\n"
                "Uso: /descripcion on · /descripcion off · «activa la descripción de pantalla con IA»")
    return (f"Verificación (código del autenticador): {'ACTIVADA 🔐' if verificacion_activa() else 'APAGADA 🔓'}\n"
            "Uso: /verificacion on · /verificacion off (solo desde la ventana de SARA) · «activa la verificación»")

def ejecutar(texto: str, origen: Optional[str] = None) -> Tuple[bool, str]:
    r = interpretar(texto)
    if r is None:
        return False, "No entendí ese ajuste."
    cual, v = r
    if v is None:
        return True, estado_texto(cual)
    return fijar_descripcion(v) if cual == "descripcion" else fijar_verificacion(v, origen)

def _onoff(arg: str) -> Optional[bool]:
    a = (arg or "").strip().lower()
    return True if a in ("on", "activar", "si", "sí", "1") else False if a in ("off", "apagar", "no", "0") else None

def comando_descripcion(arg: str, origen: Optional[str] = None) -> Tuple[bool, str]:
    v = _onoff(arg)
    return fijar_descripcion(v) if v is not None else (True, estado_texto("descripcion"))

def comando_verificacion(arg: str, origen: Optional[str] = None) -> Tuple[bool, str]:
    v = _onoff(arg)
    return fijar_verificacion(v, origen) if v is not None else (True, estado_texto("verificacion"))

cargar()      # al importar: lo guardado manda sobre config.py
