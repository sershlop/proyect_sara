# 🖥️ pantalla.py — «quiero ver la pantalla», «¿qué está pasando en la pantalla?» por Telegram (v1)
# Captura con pywin32 (ya instalado) y PNG propio con zlib: sin Pillow ni mss. Detecta sesión bloqueada o imagen
# negra y avisa en vez de mandar una imagen vacía. La descripción con IA de visión (Gemini) está APAGADA por defecto
# (PANTALLA_DESCRIBIR): manda tu pantalla a un servicio externo.
PANTALLA_VERSION = "v1"
import base64, logging, os, re, struct, unicodedata, zlib
from typing import Callable, Optional, Tuple
log = logging.getLogger("SARA-PANTALLA")
try:
    import config as _cfg
except Exception:
    _cfg = None

def _c(n, d): return getattr(_cfg, n, d) if _cfg is not None else d
def _plano(s): return re.sub(r"[^a-z0-9 ]", " ", "".join(unicodedata.normalize("NFD", ch)[0] for ch in (s or "")).lower()).strip()

_CORT = r"^(?:(?:oye|hey|hola|ok)\s+)?(?:sara\s+)?(?:(?:por favor|porfa|ya)\s+)*(?:(?:puedes|podrias|quiero|necesito|me puedes)\s+)?(?:(?:por favor|porfa)\s+)*"
_RE_VER = re.compile(_CORT + r"(?:ver|veo|muestrame|mostrar|mandame|enviame|pasame|dame|saca(?:me)?|toma(?:me)?)\s+(?:de\s+)?(?:la\s+|una\s+|mi\s+|tu\s+)?(?:pantalla|captura(?:\s+de\s+(?:la\s+)?pantalla)?|screenshot)(?:\s+(?:de\s+)?(?:la\s+)?(?:laptop|compu|computadora|pc))?(?:\s+(?:por favor|porfa|ya|ahora))?$")
_RE_QUE = re.compile(_CORT + r"(?:dime\s+)?(?:que|qu[eé])\s+(?:esta\s+pasando|pasa|hay|esta\s+haciendo|esta\s+mostrando|se\s+ve)\s+en\s+(?:la\s+)?(?:pantalla|laptop|compu|computadora|pc)(?:\s+(?:por favor|porfa|ahora|ahorita))?$"
                    r"|" + _CORT + r"(?:dime|describe(?:me)?|explicame)\s+(?:que\s+)?(?:esta\s+pasando|pasa|hay|se\s+ve)?\s*(?:en\s+)?(?:la\s+)?pantalla$")
_RE_ERROR = re.compile(_CORT + r"(?:dime\s+)?(?:que\s+(?:significa|dice|quiere\s+decir|es)|explicame|ayudame\s+(?:con|a\s+entender)|por\s+que\s+(?:sale|me\s+sale|me\s+da|aparece))\s+(?:este|ese|el|esto)\s+(?:error|mensaje|aviso|problema)(?:\s+(?:de\s+(?:la\s+)?pantalla|que\s+(?:sale|tengo|aparece)(?:\s+en\s+(?:la\s+)?pantalla)?))?\??$"
                       r"|" + _CORT + r"que\s+error\s+(?:es|sale|tengo|me\s+sale)(?:\s+en\s+(?:la\s+)?pantalla)?$")
_RE_CAPT = re.compile(r"^(?:/pantalla|screenshot|captura|captura de pantalla|pantalla)$")

def interpretar(texto: str) -> Optional[dict]:
    n = _plano(texto)
    if not n or len(n.split()) > 14:
        return None
    if _RE_ERROR.match(n):
        return {"describir": True, "error": True}
    if _RE_QUE.match(n):
        return {"describir": True, "error": False}
    if _RE_VER.match(n) or _RE_CAPT.match(n):
        return {"describir": False, "error": False}
    return None

def es_comando(texto: str) -> bool:
    return interpretar(texto) is not None

# ─── PNG sin dependencias ─────────────────────────────────────────────────
def _chunk(tipo: bytes, datos: bytes) -> bytes:
    c = struct.pack(">I", len(datos)) + tipo + datos
    return c + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)

def png_desde_bgra(w: int, h: int, bgra: bytes, paso: int = 1) -> bytes:
    nw = len(range(0, w, paso))
    filas = []
    for y in range(0, h, paso):
        fila = bgra[y * w * 4:(y + 1) * w * 4]
        px = bytearray(3 * nw)
        px[0::3], px[1::3], px[2::3] = fila[2::4 * paso], fila[1::4 * paso], fila[0::4 * paso]
        filas.append(b"\x00" + bytes(px))
    nh = len(filas)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", nw, nh, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(b"".join(filas), 6)) + _chunk(b"IEND", b""))

def _es_negra(bgra: bytes) -> bool:
    m = bgra[0::4 * 997] + bgra[1::4 * 997] + bgra[2::4 * 997]
    return len(m) > 0 and max(m) < 6

# ─── CAPTURA (Windows) ────────────────────────────────────────────────────
def _sesion_bloqueada() -> bool:
    import ctypes
    u = ctypes.windll.user32
    u.OpenInputDesktop.restype = ctypes.c_void_p
    h = u.OpenInputDesktop(0, False, 0x0100)        # DESKTOP_SWITCHDESKTOP: falla si hay pantalla de bloqueo
    if not h:
        return True
    u.CloseDesktop(ctypes.c_void_p(h))
    return False

def _bgra() -> Tuple[int, int, bytes]:
    import ctypes, win32gui, win32ui, win32con
    u = ctypes.windll.user32
    try:
        u.SetProcessDPIAware()
    except Exception:
        pass
    w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    hw = win32gui.GetDesktopWindow()
    hdc = win32gui.GetWindowDC(hw)
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)
        mem.BitBlt((0, 0), (w, h), src, (0, 0), win32con.SRCCOPY)
        return w, h, bmp.GetBitmapBits(True)
    finally:
        mem.DeleteDC(); src.DeleteDC(); win32gui.ReleaseDC(hw, hdc); win32gui.DeleteObject(bmp.GetHandle())

def capturar() -> Tuple[Optional[bytes], str]:
    """(png, '') o (None, motivo). Nunca lanza."""
    if os.name != "nt":
        return None, "La captura de pantalla solo funciona en Windows."
    try:
        if _sesion_bloqueada():
            return None, "🔒 La sesión de Windows está bloqueada (o la laptop duerme): la captura saldría negra. Desbloquéala en el PC."
        w, h, data = _bgra()
        if _es_negra(data):
            return None, "⬛ La pantalla salió completamente negra (apagada o en suspensión); no te mando una imagen vacía."
        return png_desde_bgra(w, h, data, max(1, int(_c("PANTALLA_ESCALA", 1)))), ""
    except ImportError:
        return None, "Falta pywin32 (pip install pywin32)."
    except Exception as e:
        log.exception("captura falló")
        return None, f"No pude capturar la pantalla ({type(e).__name__})."

# ─── DESCRIPCIÓN (opcional, externa) ──────────────────────────────────────
def describir(png: bytes, error: bool = False) -> Optional[str]:
    try:
        import respaldo_externo as r
        if not r.gemini_disponible():
            return None
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{r.GEMINI_MODEL}:generateContent"
        cuerpo = {"contents": [{"parts": [
            {"text": ("En esta captura de pantalla hay un error o mensaje de aviso. Dime en español, en máximo 4 oraciones: "
                      "qué significa y cuál es la causa más probable y cómo se arregla. Si no ves ningún error, dilo. Sin markdown.")
                     if error else
                     "Describe en español, en máximo 3 oraciones, qué está pasando en esta pantalla de computadora "
                     "(apps abiertas, descargas, errores visibles). Sin markdown."},
            {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(png).decode()}}]}],
            "generationConfig": {"maxOutputTokens": 400, "temperature": 0.2}}
        resp = r._http.post(url, headers={"x-goog-api-key": r.GEMINI_API_KEY}, json=cuerpo, timeout=(5, 25))
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        log.exception("describir falló")
        return None

def ejecutar(texto: str, enviar_foto: Callable) -> Tuple[bool, str]:
    """enviar_foto(png_bytes, caption) -> bool. Nunca lanza."""
    iv = interpretar(texto)
    if iv is None:
        return False, "No entendí lo que quieres ver."
    png, motivo = capturar()
    if png is None:
        return False, motivo
    log.warning("🖥️ captura enviada por Telegram (%d KB)", len(png) // 1024)
    if not enviar_foto(png, "🖥️ Pantalla ahora"):
        return False, "Capturé la pantalla pero no pude subirla a Telegram."
    if not iv["describir"]:
        return True, "Aquí está la pantalla."
    if not _c("PANTALLA_DESCRIBIR", False):
        return True, ("Aquí está la pantalla. La descripción con IA está apagada porque enviaría tu pantalla a un servicio "
                      "externo. Para que la lea, di «activa la descripción de pantalla con IA» (o /descripcion on).")
    d = describir(png, bool(iv.get("error")))
    return True, ("Aquí está la pantalla. " + d) if d else "Aquí está la pantalla (la IA de visión no respondió)."
