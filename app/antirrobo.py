# 🛡️ antirrobo.py — «¿dónde está mi laptop?» y «toma una foto» por Telegram (v1)
#   /donde  → Wi-Fi (SSID), IP local y pública, batería, nombre del equipo, hora
#   /foto   → una foto con la webcam (necesita opencv-python: pip install opencv-python)
#   /bloquear → ya existe: «bloquea la pantalla» (flujo de apagado.py con botón)
# Ambas piden el desbloqueo TOTP (core_bridge) salvo que la verificación esté apagada. Nada se guarda en disco.
ANTIRROBO_VERSION = "v1"
import logging, os, re, socket, subprocess, time, unicodedata
from typing import Callable, Optional, Tuple
log = logging.getLogger("SARA-ANTIRROBO")
try:
    import config as _cfg
except Exception:
    _cfg = None

def _c(n, d): return getattr(_cfg, n, d) if _cfg is not None else d
def _plano(s): return re.sub(r"[^a-z0-9 ]", " ", "".join(unicodedata.normalize("NFD", ch)[0] for ch in (s or "")).lower()).strip()

_CORT = r"^(?:(?:oye|hey|hola|ok)\s+)?(?:sara[\s,]+)?(?:(?:por favor|porfa|ya|ahora)\s+)*(?:(?:puedes|podrias|quiero|necesito|me puedes)\s+)?(?:(?:por favor|porfa)\s+)*"
_EQ = r"(?:laptop|compu|computadora|pc|portatil|equipo|lap)"
_RE_DONDE = re.compile(_CORT + r"(?:donde\s+(?:esta|andas|estas)(?:\s+(?:mi|la|tu))?(?:\s+" + _EQ + r")?|localiza(?:me)?\s+(?:mi|la|tu)\s+" + _EQ
                       + r"|a\s+que\s+(?:wifi|red)\s+esta(?:s)?\s+conectad[ao]s?(?:\s+(?:la|mi))?(?:\s+" + _EQ + r")?|que\s+ip\s+tienes|cual\s+es\s+tu\s+ip)(?:\s+(?:por favor|porfa|ahora))?$|^/donde$")
_RE_FOTO = re.compile(_CORT + r"(?:toma(?:me)?|saca(?:me)?|haz(?:me)?)\s+una\s+foto(?:\s+(?:con|de)\s+(?:la\s+)?(?:webcam|camara|laptop|compu|computadora))?(?:\s+(?:por favor|porfa|ya))?$"
                      r"|" + _CORT + r"(?:mandame|enviame|pasame|dame)\s+una\s+foto\s+(?:con|de)\s+(?:la\s+)?(?:webcam|camara)(?:\s+de\s+(?:la\s+)?" + _EQ + r")?$"
                      r"|" + _CORT + r"(?:quien|que)\s+(?:esta|hay)\s+(?:frente\s+a|enfrente\s+de|usando)\s+(?:la\s+)?" + _EQ + r"$|^/foto$")

def interpretar(texto: str) -> Optional[str]:
    crudo = (texto or "").strip().lower()
    if crudo == "/donde": return "donde"
    if crudo == "/foto": return "foto"
    n = _plano(texto)
    if not n or len(n.split()) > 14:
        return None
    if _RE_FOTO.match(n): return "foto"
    if _RE_DONDE.match(n): return "donde"
    return None

def es_comando(texto: str) -> bool:
    return interpretar(texto) is not None

# ─── /donde ───────────────────────────────────────────────────────────────
def _ssid() -> Optional[str]:
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, errors="replace",
                             timeout=6, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return parsear_ssid(out)
    except Exception:
        log.exception("ssid falló")
        return None

def parsear_ssid(salida: str) -> Optional[str]:
    """Toma la línea «SSID : nombre» (no «BSSID»). Igual en cualquier idioma de Windows."""
    m = re.search(r"^\s*SSID\s*:\s*(.+?)\s*$", salida or "", re.M)
    return m.group(1) if m else None

def donde_texto() -> str:
    try:
        import system_info as si
    except Exception:
        si = None
    ip_local = si.obtener_ip_local() if si else None
    ip_pub = si.obtener_ip_publica(4) if si else None
    ssid = _ssid()
    bat = si.obtener_bateria() if si else None
    lineas = ["📍 Estado de la laptop:",
              f"• Wi-Fi: {ssid or 'no conectada a Wi-Fi (o cable / sin datos)'}",
              f"• IP local: {ip_local or '—'}",
              f"• IP pública: {ip_pub or '—'}  (aproxima la ciudad/proveedor, no la dirección exacta)",
              f"• Equipo: {socket.gethostname()}"]
    if bat:
        lineas.append(f"• Batería: {bat['porcentaje']} % ({'cargando' if bat['cargando'] else 'sin cargador'})")
    lineas.append(f"• Hora del equipo: {time.strftime('%d/%m %H:%M:%S')}")
    return "\n".join(lineas)

# ─── /foto ────────────────────────────────────────────────────────────────
def capturar_foto() -> Tuple[Optional[bytes], str]:
    try:
        import cv2
    except ImportError:
        return None, "Falta opencv-python (pip install opencv-python; ponlo también en bootstrap/wheels)."
    cam = None
    try:
        idx = int(_c("ANTIRROBO_CAMARA", 0))
        cam = cv2.VideoCapture(idx, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(idx)
        if not cam.isOpened():
            return None, "No pude abrir la cámara (¿está en uso, desactivada en Privacidad de Windows o no tiene?)."
        frame = None
        for _ in range(12):                       # las primeras imágenes salen oscuras: la cámara ajusta la exposición
            ok, f = cam.read()
            if ok:
                frame = f
            time.sleep(0.08)
        if frame is None:
            return None, "La cámara no entregó imagen."
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return None, "No pude codificar la foto."
        aviso = "" if frame.mean() > 8 else " (salió muy oscura: ¿tapa de cámara, mochila cerrada?)"
        return buf.tobytes(), aviso
    except Exception as e:
        log.exception("foto falló")
        return None, f"No pude tomar la foto ({type(e).__name__})."
    finally:
        if cam is not None:
            cam.release()

def ejecutar(texto: str, enviar_foto: Callable) -> Tuple[bool, str]:
    """enviar_foto(bytes, caption, nombre) -> bool. Nunca lanza."""
    a = interpretar(texto)
    if a == "donde":
        log.warning("📍 /donde consultado")
        return True, donde_texto()
    if a == "foto":
        img, aviso = capturar_foto()
        if img is None:
            return False, aviso
        log.warning("📷 foto de webcam enviada")
        if not enviar_foto(img, "📷 Webcam ahora", "webcam.jpg"):
            return False, "Tomé la foto pero no pude subirla a Telegram."
        return True, "Aquí está la foto." + aviso
    return False, "No entendí qué necesitas."
