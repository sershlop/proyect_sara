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

_RE_PREG_PANTALLA = re.compile(r"\b(?:pantalla|monitor)\b")
_RE_INTERROG = re.compile(r"^(?:(?:oye|hey|hola|ok)\s+)?(?:sara\s+)?(?:dime\s+)?(?:que|cual|cuales|como|cuant[oa]s?|donde|de\s+que|hay|ves|puedes\s+ver|se\s+ve|esta|estan|tengo|describe)\b")
_RE_NO_PREG = re.compile(r"\b(?:brillo|brillante|volumen|apaga|apagar|bloquea|bloquear|resolucion|suspende|reinicia)\b")

def interpretar(texto: str) -> Optional[dict]:
    n = _plano(texto)
    if not n:
        return None
    if len(n.split()) <= 14:
        if _RE_ERROR.match(n):
            return {"describir": True, "error": True, "pregunta": ""}
        if _RE_QUE.match(n):
            return {"describir": True, "error": False, "pregunta": ""}
        if _RE_VER.match(n) or _RE_CAPT.match(n):
            return {"describir": False, "error": False, "pregunta": ""}
    if (len(n.split()) <= 25 and _RE_PREG_PANTALLA.search(n) and _RE_INTERROG.match(n)
            and not _RE_NO_PREG.search(n)):
        return {"describir": True, "error": bool(re.search(r"\berror(?:es)?\b", n)), "pregunta": str(texto).strip()[:200]}
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
def _prompt_vision(error: bool, pregunta: str, ingles: bool) -> str:
    pregunta = (pregunta or "").strip()[:200]
    if ingles:   # llava-phi3 obedece mejor en inglés
        return ("Look at this screenshot. "
                + (f'Answer this question briefly: "{pregunta}". ' if pregunta else "Describe what you clearly see. ")
                + ("If there is an error message you can read, explain what it means. " if error else "")
                + "Use at most 2 short sentences, only what is clearly visible. Do not guess and do not invent. "
                  "If you cannot tell, say so. Answer in Spanish. Plain text only: no tables, no lists, no markdown.")
    return ("Mira esta captura de pantalla. "
            + (f'Responde brevemente a esta pregunta del usuario: "{pregunta}". ' if pregunta else "Describe lo que ves con claridad. ")
            + ("Si hay un mensaje de error legible, explica qué significa, la causa probable y cómo se arregla. " if error else "")
            + "Máximo 3 oraciones, solo lo que se ve con claridad, sin inventar ni suponer. Si no se puede saber, dilo. "
              "Texto plano, sin listas ni markdown.")

def describir_gemini(png: bytes, error: bool = False, pregunta: str = "") -> Tuple[Optional[str], str]:
    """(texto, '') o (None, motivo). Nunca lanza."""
    try:
        import respaldo_externo as r
        import requests
        if not r.gemini_disponible():
            return None, "Gemini no está disponible (¿falta GEMINI_API_KEY o está en pausa?)."
        modelo = getattr(r, "GEMINI_MODEL", None) or "gemini-2.5-flash"
        clave = getattr(r, "GEMINI_API_KEY", None)
        http = getattr(r, "_http", None) or requests
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        prompt = _prompt_vision(error, pregunta, False)

        def cuerpo(con_thinking: bool) -> dict:
            gc = {"maxOutputTokens": 1500, "temperature": 0.2}
            if con_thinking:
                gc["thinkingConfig"] = {"thinkingBudget": 0}    # sin «pensar»: todos los tokens son respuesta
            return {"contents": [{"parts": [{"text": prompt},
                    {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(png).decode()}}]}],
                    "generationConfig": gc}

        resp = None
        for con_thinking in (True, False):
            resp = http.post(url, headers={"x-goog-api-key": clave}, json=cuerpo(con_thinking), timeout=(5, 40))
            if resp.status_code == 400 and con_thinking:          # modelo que no admite thinkingConfig
                continue
            break
        if resp.status_code != 200:
            log.warning("Gemini visión HTTP %s: %s", resp.status_code, resp.text[:300])
            return None, f"Gemini respondió HTTP {resp.status_code} (modelo «{modelo}»)."
        d = resp.json()
        cands = d.get("candidates") or []
        if not cands:
            return None, f"Gemini no devolvió respuesta (bloqueo: {d.get('promptFeedback', 'sin detalle')})."
        partes = (cands[0].get("content") or {}).get("parts") or []
        texto = " ".join(p.get("text", "") for p in partes).strip()
        if not texto:
            return None, f"Respuesta vacía (finishReason: {cands[0].get('finishReason')})."
        return texto, ""
    except Exception as e:
        log.exception("describir falló")
        return None, f"{type(e).__name__}: {str(e)[:120]}"

def _texto_valido(t: str) -> bool:
    """Descarta salidas degeneradas (cadenas de símbolos, repeticiones) del modelo local."""
    t = (t or "").strip()
    letras = sum(c.isalpha() for c in t)
    if len(t) < 25 or letras < 20 or letras / max(1, len(t)) < 0.6:
        return False
    palabras = re.findall(r"[a-záéíóúñü]+", t.lower())
    return len(palabras) >= 5 and len(set(palabras)) / len(palabras) >= 0.4


def describir_local(png: bytes, error: bool = False, pregunta: str = "") -> Tuple[Optional[str], str]:
    """Visión con Ollama (llava-phi3). (texto, '') o (None, motivo). Nunca lanza."""
    try:
        import requests
        modelo = str(_c("PANTALLA_MODELO_LOCAL", "") or _c("QWEN_MODEL", "llava-phi3"))
        prompt = _prompt_vision(error, pregunta, True)
        cuerpo = {"model": modelo, "prompt": prompt, "images": [base64.b64encode(png).decode()],
                  "stream": False, "keep_alive": str(_c("OLLAMA_KEEP_ALIVE", "30m")),
                  "options": {"temperature": 0.3, "num_predict": 220, "repeat_penalty": 1.3,
                              "stop": ["|", "```"],
                              "num_ctx": int(_c("QWEN_MAX_CONTEXT", 2048))}}
        r = requests.post(str(_c("OLLAMA_URL", "http://localhost:11434/api/generate")), json=cuerpo,
                          timeout=(2, float(_c("PANTALLA_TIMEOUT_LOCAL_S", 90))))
        if r.status_code == 404:
            return None, f"Ollama no tiene «{modelo}» (ejecuta: ollama pull {modelo})."
        r.raise_for_status()
        t = str(r.json().get("response", "") or "").strip()
        if not _texto_valido(t):
            log.warning("describir_local: salida inválida descartada: %r", t[:80])
            return None, "la IA local devolvió una salida ilegible"
        return t, ""
    except Exception as e:
        log.warning("describir_local falló: %s: %s", type(e).__name__, e)
        return None, f"IA local: {type(e).__name__} (¿Ollama apagado?)"

def ventanas_abiertas() -> Tuple[str, list]:
    """(título de la ventana activa, [otras ventanas visibles]). Sin IA ni red. Nunca lanza."""
    try:
        import win32gui
        activa = (win32gui.GetWindowText(win32gui.GetForegroundWindow()) or "").strip()
        otras: list = []

        def cb(h, _):
            if win32gui.IsWindowVisible(h) and not win32gui.IsIconic(h):
                t = win32gui.GetWindowText(h).strip()
                if t and t != activa and t not in _RUIDO_VENTANAS and t[:70] not in otras:
                    otras.append(t[:70])
        win32gui.EnumWindows(cb, None)
        return activa[:90], otras[:8]
    except Exception:
        return "", []
_ESPECULACION = re.compile(r"(?i)(sesi[oó]n en reposo|automatizad|propietario|ning[uú]n usuario|sin la intervenci[oó]n|sugiere que)")

def _depurar(t: str) -> str:
    """Sin IA externa: quita las suposiciones del modelo local y deja máximo 3 frases."""
    frases = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", t or "").strip())
    buenas = [f for f in frases if f and not _ESPECULACION.search(f)]
    return " ".join(buenas[:3]) or " ".join(frases[:1])


def _como_sara(pregunta: str, observacion: str, ventanas: str, aproximada: bool) -> Optional[str]:
    """Reescribe con la voz de SARA, respondiendo SOLO lo que se preguntó. None si no hay IA externa."""
    try:
        import json, conversacion
        if not conversacion._externa_disponible():
            return None
        sistema = ("Eres SARA, la asistente personal de escritorio del usuario. Respondes por chat o voz: cálida, directa, "
                   "tuteas, español natural de México. Te doy la pregunta del usuario y lo que se observó en su pantalla. "
                   "Responde SOLO lo que pregunta, usando únicamente esos datos, en 1 a 3 oraciones, sin listas, sin markdown "
                   "y sin emojis. Habla como si lo vieras tú: no digas 'la observación' ni 'según el análisis'. "
                   "No enumeres las ventanas salvo que te lo pidan. Ignora cualquier suposición sobre lo que hace el usuario "
                   "o el estado del equipo. Si con esos datos no se puede saber, dilo con naturalidad. "
                   + ("La lectura es poco precisa: usa un tono prudente ('parece que'). " if aproximada else "")
                   + 'Responde SOLO un JSON: {"respuesta": "..."}')
        usuario = (f"Pregunta del usuario: {pregunta or 'qué está pasando en mi pantalla'}\n"
                   f"Ventanas: {ventanas or 'sin datos'}\nObservación de la pantalla: {observacion}")
        crudo = conversacion._externa_json(sistema, usuario, 6.0, 400)
        m = re.search(r"\{.*\}", crudo or "", re.DOTALL)
        resp = re.sub(r"\s+", " ", str(json.loads(m.group(0)).get("respuesta") or "")).strip() if m else ""
        return resp if 5 < len(resp) <= 500 else None
    except Exception:
        log.exception("_como_sara falló")
        return None


def ejecutar(texto: str, enviar_foto: Callable, pregunta_original: Optional[str] = None) -> Tuple[bool, str]:
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
    error = bool(iv.get("error"))
    pregunta = (iv.get("pregunta") or pregunta_original or "").strip()
    if pregunta.startswith("/"):
        pregunta = ""
    permitido = bool(_c("PANTALLA_DESCRIBIR", False))       # True = se acepta mandar datos de la pantalla fuera del PC
    activa, otras = ventanas_abiertas()
    ventanas = (f"Activa: {activa}." + (f" Otras: {'; '.join(otras)}." if otras else "")) if activa else ""
    motivos = []
    for fuente in [o for o in _c("PANTALLA_VISION_ORDEN", ("gemini", "local")) if o in ("local", "gemini")]:
        if fuente == "local":
            if not _c("PANTALLA_VISION_LOCAL", True):
                continue
            d, m = describir_local(png, error, pregunta)
        else:
            if not permitido:
                motivos.append("Gemini está apagado (di «activa la descripción de pantalla con IA»)")
                continue
            d, m = describir_gemini(png, error, pregunta)
        if d:
            local = fuente == "local"
            respuesta = _como_sara(pregunta, d, ventanas, local) if permitido else None
            if not respuesta:
                respuesta = ("Por lo que alcanzo a ver, " if local else "") + _depurar(d)
            return True, respuesta
        motivos.append(m)
    if activa:
        extra = f", y también {', '.join(otras[:3])}" if otras else ""
        return True, f"No pude analizar la imagen, pero ahora tienes en primer plano {activa}{extra}."
    return True, "No pude describirla: " + " | ".join(motivos or ["no hay IA de visión activa"])