# 🎵 multimedia.py — Control de reproducción en lenguaje natural (v1)
# Pausa, reanuda, siguiente, anterior, detener, adelantar/retroceder y «qué suena» sobre lo que se esté
# reproduciendo en la laptop (Spotify, YouTube en el navegador, VLC, etc.), sin comandos rígidos.
#
# Contrato (igual que volumen.py): es_comando(texto) -> bool · interpretar(texto) -> dict | None
#                                  ejecutar(texto) -> (bool, str)  (nunca lanza) · diagnostico() -> str
#
# Backends (se elige solo, con degradación elegante):
#   1) SMTC de Windows vía «winsdk» (pip install winsdk): pausa y play EXPLÍCITOS (no alterna), elige la app
#      («pausa spotify»), lee el título y permite adelantar/retroceder.
#   2) Teclas multimedia de Windows (ctypes, sin dependencias): play/pausa ALTERNA, siguiente, anterior, detener.
#      Van a la app que Windows considere «la actual». No puede leer qué suena ni elegir app.
#
# Parámetros opcionales de config.py (todos con valor por defecto aquí):
#   MEDIA_PERMITIR_REMOTO = True   (False = no obedece por WhatsApp/Telegram/celular; lo aplica core_bridge)
#   MEDIA_SALTO_S = 10             (segundos al adelantar/retroceder si no dices cuántos)
import asyncio, logging, re, sys, threading, time, unicodedata
from typing import Optional, Tuple

log = logging.getLogger("SARA-MEDIA")
MULTIMEDIA_VERSION = "v1-smtc-teclas"

try:
    import config as _cfg
except Exception:
    _cfg = None


def _c(n, d):
    return getattr(_cfg, n, d) if _cfg is not None else d


# ───────────────────────── normalización ─────────────────────────

def _sin_acentos(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")


_STT = [  # errores típicos del dictado → forma buena
    (r"\bpausa(?:r|la|lo)?\b", "pausa"), (r"\bpaus[ea]\b", "pausa"),
    (r"\bplei\b|\bpley\b|\bplay\b", "play"), (r"\bnext\b|\bsiguiente\b", "siguiente"),
    (r"\bprevious\b|\bprebius\b", "anterior"), (r"\bespotifai\b|\bespotify\b|\bspotifi\b", "spotify"),
    (r"\byutub\b|\byoutube\b|\bllutub\b|\byutube\b", "youtube"),
    (r"\bvideo\b|\bvideos\b|\bvidio\b", "video"),
]


def _norm(t: str) -> str:
    s = _sin_acentos((t or "").lower())
    s = re.sub(r"[¿?¡!.,;:\"“”«»()]", " ", s)
    for pat, rep in _STT:
        s = re.sub(pat, rep, s)
    return re.sub(r"\s+", " ", s).strip()


# ───────────────────────── vocabulario por señales ─────────────────────────

_CORTESIA = {"sara", "oye", "hey", "ey", "por", "favor", "porfa", "porfavor", "porfis", "podrias", "puedes", "me",
             "puedes", "quiero", "que", "ya", "pues", "ok", "okay", "mejor", "ahora", "tantito", "un", "poco",
             "poquito", "gracias", "vamos", "a", "ahi", "ahorita", "de", "nuevo", "la", "el", "lo", "los", "las",
             "mi", "tu", "en", "del", "al", "esa", "ese", "eso", "esta", "este", "esto", "una", "unos", "unas", "y",
             "si", "te", "se", "le", "para", "pon", "ponme", "ponle", "dale", "dame", "hazme", "hazle", "o", "u",
             "laptop", "compu", "computadora", "pc", "portatil", "ordenador"}

# lo que se está reproduciendo
_OBJETO = {"musica", "cancion", "canciones", "rola", "rolas", "tema", "pista", "video", "videos", "reproduccion",
           "playlist", "lista", "podcast", "episodio", "capitulo", "audio", "sonido", "cancioncita", "melodia",
           "reproductor", "multimedia", "peli", "pelicula", "serie", "clip", "mix"}
# aplicaciones concretas (elegir sesión)
_APPS = {
    "spotify": ("spotify",),
    "youtube": ("chrome", "msedge", "firefox", "brave", "opera", "vivaldi"),
    "netflix": ("chrome", "msedge", "firefox", "brave", "opera", "vivaldi"),
    "navegador": ("chrome", "msedge", "firefox", "brave", "opera", "vivaldi"),
    "chrome": ("chrome",), "edge": ("msedge",), "firefox": ("firefox",), "brave": ("brave",), "opera": ("opera",),
    "vlc": ("vlc",), "itunes": ("itunes", "applemusic"), "deezer": ("deezer",), "tidal": ("tidal",),
}
# cosas que NO son reproducción multimedia (diapositivas, páginas…)
_AJENO_OBJ = {"diapositiva", "diapositivas", "slide", "slides", "pagina", "paginas", "archivo", "archivos", "correo",
              "correos", "mensaje", "mensajes", "tarea", "tareas", "ventana", "pestana", "pestanas", "foto", "fotos",
              "imagen", "imagenes", "resultado", "resultados", "linea", "capa", "paso", "pregunta", "turno", "cita",
              "alarma", "reunion", "clase", "tab", "documento"}
_AJENO_DISPOSITIVO = {"celular", "telefono", "movil", "cel", "tele", "television", "tv", "radio", "carro", "coche",
                      "auto", "bocina", "alexa", "stereo", "estereo", "consola", "playstation", "xbox", "tablet"}

_V_SIGUIENTE = {"siguiente", "otra", "proxima", "proximo", "salta", "saltar", "saltala", "saltalo", "pasa",
                "pasale", "pasala", "pasalo", "cambia", "cambiale", "skip", "adelante"}
_V_ANTERIOR = {"anterior", "atras", "regresa", "regresale", "regresala", "regresalo", "devuelve", "devuelvela",
               "devuelvelo", "previa", "previo", "retrocede", "vuelve", "volver"}
_V_PAUSA = {"pausa", "deten", "detener", "detenla", "detenlo", "frena", "frenala", "freno", "stop", "alto",
            "paralo", "paro", "detente"}
_V_PARA = {"para", "parar", "parala", "paralo"}          # ambiguas: «para» también es preposición → exigen objeto
_V_RESUMIR = {"play", "reanuda", "reanudar", "reanudala", "reanudalo", "continua", "continuar", "continuala",
              "continualo", "resume", "seguir", "sigue"}
_V_INICIAR = {"reproduce", "reproducir", "reproducela", "reproducelo", "pon", "ponla", "ponlo", "ponme", "inicia",
              "empieza", "activa", "arranca"}
_V_PLAY = _V_RESUMIR | _V_INICIAR
_V_ADELANTAR = {"adelanta", "adelantar", "avanza", "avanzar", "adelantale", "avanzale", "salta"}
_V_RETROCEDER = {"retrocede", "retroceder", "atrasa", "atrasar", "retrocedele", "rebobina", "rebobinar", "atrasale"}
_V_REINICIAR = {"reinicia", "reiniciar", "reiniciala", "reinicialo", "repite", "repetir", "repitela", "repitelo"}

_COMPUESTA = re.compile(r"\b(?:y luego|y despues|despues|luego|y abre|y busca|y manda|y apaga|y bloquea|y sube|y baja|"
                        r"y mutea|y silencia|y cierra|abre|abreme|busca|buscame|mandame|apaga|bloquea|cierra)\b")
_NO_ORDEN = re.compile(r"^(?:como|que es|que significa|por que|porque|cual es|cuando|donde|explica|explicame|"
                       r"que tal|quien|cuantos|cuantas|sabes|dime como|ensename|me ensenas|tutorial)\b")
_CONSULTA = re.compile(r"\b(?:que|cual)\b.*\b(?:suena|sonando|escucho|escuchando|reproduce|reproduciendo|"
                       r"toca|tocando|cancion es|musica es|video es|viendo)\b|\bque (?:es|era) (?:esto|eso|lo que suena)\b|"
                       r"\bcomo se llama (?:esta|esa|la) (?:cancion|rola|musica|pista)\b|\bque estoy (?:escuchando|viendo)\b")
_SEGUNDOS = re.compile(r"\b(\d{1,4})\s*(?:segundos|seg|s|minutos|minuto|min)\b")
_PALABRAS_NUM = {"cinco": 5, "diez": 10, "quince": 15, "veinte": 20, "treinta": 30, "cuarenta": 40,
                 "sesenta": 60, "un": 1, "uno": 1, "dos": 2, "tres": 3}


def _segundos_pedidos(n: str) -> Optional[int]:
    m = _SEGUNDOS.search(n)
    if m:
        v = int(m.group(1))
        return v * 60 if re.search(r"min", m.group(0)) else v
    for pal, v in _PALABRAS_NUM.items():
        mm = re.search(r"\b" + pal + r"\s*(segundos|seg|minutos|minuto|min)\b", n)
        if mm:
            return v * 60 if mm.group(1).startswith("min") else v
    if re.search(r"\bun minuto\b", n):
        return 60
    return None


# ───────────────────────── interpretación ─────────────────────────

def interpretar(texto: str) -> Optional[dict]:
    """{'accion': siguiente|anterior|pausa|play|alternar|detener|adelantar|retroceder|reiniciar|consultar,
        'app': 'spotify'|None, 'todo': bool, 'seg': int|None} o None si no es una orden multimedia."""
    try:
        n = _norm(texto)
        if not n or len(n) > 160:
            return None
        pal = n.split()
        conj = set(pal)

        if conj & _AJENO_DISPOSITIVO:
            return None
        if _COMPUESTA.search(n):
            return None

        objetos = conj & _OBJETO
        apps = [a for a in pal if a in _APPS]
        app = apps[0] if apps else None
        hay_objeto = bool(objetos) or bool(app)

        # consulta: «qué está sonando» (antes del filtro de preguntas: es una pregunta que SÍ atendemos)
        if _CONSULTA.search(n) and (hay_objeto or re.search(r"\b(suena|sonando|escucho|escuchando)\b", n)):
            return {"accion": "consultar", "app": app, "todo": False, "seg": None}
        if _NO_ORDEN.search(n):
            return None

        # lo que no es multimedia (diapositivas, páginas…) se queda fuera salvo que nombre algo de audio/video
        if (conj & _AJENO_OBJ) and not hay_objeto:
            return None

        # palabras con contenido (lo que queda tras quitar cortesías, verbos y objetos): si sobra algo, probablemente
        # es «reproduce <algo>» / «pon <artista>»: eso lo resuelve web_navigator, no este módulo.
        verbos = (_V_SIGUIENTE | _V_ANTERIOR | _V_PAUSA | _V_PARA | _V_PLAY | _V_ADELANTAR | _V_RETROCEDER
                  | _V_REINICIAR)
        sobra = [w for w in pal if w not in _CORTESIA and w not in verbos and w not in _OBJETO
                 and w not in _APPS and not w.isdigit() and w not in {"segundos", "segundo", "seg", "minuto",
                                                                      "minutos", "min", "todo", "todos", "toda",
                                                                      "todas", "actual", "mas", "menos", "bien",
                                                                      "otro", "otra", "desde", "principio",
                                                                      "inicio", "empezar", "comienzo", "cinco",
                                                                      "diez", "quince", "veinte", "treinta",
                                                                      "cuarenta", "sesenta", "dos", "tres", "uno"}]
        if sobra:
            return None

        todo = bool(conj & {"todo", "todos", "toda", "todas"})
        seg = _segundos_pedidos(n)
        corto = len([w for w in pal if w not in _CORTESIA]) <= 2   # «siguiente», «pausa», «dale play»

        def r(a):
            return {"accion": a, "app": app, "todo": todo, "seg": seg}

        # orden de prioridad (lo más específico primero)
        if conj & _V_REINICIAR and (hay_objeto or re.search(r"\bdesde (?:el )?(?:principio|inicio)\b", n)):
            return r("reiniciar")
        if re.search(r"\bdesde (?:el )?(?:principio|inicio|comienzo)\b", n) and hay_objeto:
            return r("reiniciar")
        if conj & _V_ADELANTAR and (seg or hay_objeto) and "siguiente" not in conj:
            if "salta" in conj and not seg:
                pass          # «salta la canción» = siguiente
            else:
                return r("adelantar")
        if conj & _V_RETROCEDER and (seg or hay_objeto):
            return r("retroceder")
        if ("atras" in conj or "atrasa" in conj) and seg:
            return r("retroceder")
        if "adelante" in conj and seg:
            return r("adelantar")

        # siguiente / anterior
        if re.search(r"\bque sigue\b", n) and (hay_objeto or corto or "la" in conj):
            return r("siguiente")
        if conj & _V_SIGUIENTE and (hay_objeto or corto) and not (conj & _V_ANTERIOR):
            return r("siguiente")
        if conj & _V_ANTERIOR and (hay_objeto or corto):
            return r("anterior")

        # pausa y play a la vez («pausa/reproduce») = alternar
        pausa = bool(conj & _V_PAUSA) or (bool(conj & _V_PARA) and hay_objeto)
        definido = bool(conj & {"la", "el", "lo", "esa", "ese", "esta", "este", "eso", "esto", "mi", "tu"})
        hay_cifra = any(w.isdigit() for w in pal) and not seg
        en_app = bool(app) and bool(re.search(r"\ben (?:el |la )?" + re.escape(app) + r"\b", n))
        reanudar = bool(conj & _V_RESUMIR) and (hay_objeto or bool(conj & {"play", "reanuda", "reanudar", "reanudala",
                                                 "reanudalo", "resume", "continua", "continuar"})) and not hay_cifra
        iniciar = bool(conj & _V_INICIAR) and not hay_cifra and (
            (hay_objeto and definido and not en_app)
            or (corto and bool(conj & {"reproduce", "reproducir", "reproducela", "reproducelo", "ponla", "ponlo"})))
        play = reanudar or iniciar
        if pausa and play:
            return r("alternar")
        if pausa and (hay_objeto or corto or "pausa" in conj):
            return r("pausa")
        if play and (hay_objeto or "play" in conj or corto):
            return r("play")
        return None
    except Exception:
        log.exception("multimedia.interpretar falló")
        return None


def es_comando(texto: str) -> bool:
    return interpretar(texto) is not None


# ───────────────────────── backends ─────────────────────────

_lock = threading.Lock()


class _BackendTeclas:
    """Teclas multimedia de Windows (sin dependencias). Play/pausa ALTERNA; no puede leer ni elegir app."""
    nombre = "teclas multimedia (sin lectura; play/pausa alterna)"
    VK = {"siguiente": 0xB0, "anterior": 0xB1, "detener": 0xB2, "alternar": 0xB3}

    def _tecla(self, vk: int) -> None:
        import ctypes
        u = ctypes.windll.user32
        u.keybd_event(vk, 0, 1, 0)       # KEYEVENTF_EXTENDEDKEY
        u.keybd_event(vk, 0, 3, 0)       # + KEYEVENTF_KEYUP
        time.sleep(0.05)

    def ejecutar(self, orden: dict) -> Tuple[bool, str]:
        a = orden["accion"]
        if a in ("consultar", "adelantar", "retroceder", "reiniciar"):
            return False, ("Para eso necesito «winsdk» (pip install winsdk): con las teclas multimedia solo puedo "
                           "pausar, seguir, ir a la siguiente o a la anterior.")
        if orden.get("app"):
            extra = f" (no puedo elegir {orden['app']}: lo mando a la app que Windows tenga activa)"
        else:
            extra = ""
        with _lock:
            if a == "siguiente":
                self._tecla(self.VK["siguiente"]); return True, "⏭ Siguiente." + extra
            if a == "anterior":
                self._tecla(self.VK["anterior"]); return True, "⏮ Anterior." + extra
            if a == "detener":
                self._tecla(self.VK["detener"]); return True, "⏹ Detenido." + extra
            if a in ("pausa", "play", "alternar"):
                self._tecla(self.VK["alternar"])
                txt = {"pausa": "⏸ Pausa/Reproducir (tecla alterna).", "play": "▶ Reproducir/Pausa (tecla alterna).",
                       "alternar": "⏯ Listo."}[a]
                return True, txt + extra
        return False, "No supe qué hacer con eso."


class _BackendSMTC:
    """Sesiones multimedia de Windows (SMTC) con «winsdk»: control explícito por app y lectura del título."""
    nombre = "SMTC de Windows (winsdk)"

    def __init__(self):
        from winsdk.windows.media.control import (  # noqa
            GlobalSystemMediaTransportControlsSessionManager as M,
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as S)
        self._M, self._S = M, S

    @staticmethod
    def _id(s) -> str:
        try:
            return (s.source_app_user_model_id or "").lower()
        except Exception:
            return ""

    async def _sesiones(self):
        m = await self._M.request_async()
        return m, list(m.get_sessions())

    @staticmethod
    def _estado(s) -> str:
        try:
            return str(s.get_playback_info().playback_status).split(".")[-1].upper()
        except Exception:
            return "?"

    async def _titulo(self, s) -> str:
        try:
            p = await s.try_get_media_properties_async()
            t = (p.title or "").strip()
            a = (p.artist or "").strip()
            return f"«{t}»" + (f" — {a}" if a else "") if t else ""
        except Exception:
            return ""

    def _elegir(self, m, ses, orden):
        """Devuelve (lista de sesiones objetivo, mensaje de error | None)."""
        if orden.get("todo"):
            return ses, None
        app = orden.get("app")
        if app:
            claves = _APPS.get(app, (app,))
            c = [s for s in ses if any(k in self._id(s) for k in claves)]
            if not c:
                return [], f"No encuentro {app} reproduciendo nada ahora mismo."
            # si hay varias (varias ventanas del navegador), la que suena primero
            c.sort(key=lambda s: 0 if "PLAYING" in self._estado(s) else 1)
            return c[:1], None
        cur = m.get_current_session()
        if cur is not None:
            return [cur], None
        if ses:
            return ses[:1], None
        return [], "No hay nada reproduciéndose en la laptop."

    async def _run(self, orden: dict) -> Tuple[bool, str]:
        m, ses = await self._sesiones()
        objetivo, err = self._elegir(m, ses, orden)
        if err:
            return False, err
        a = orden["accion"]
        salto = int(orden.get("seg") or _c("MEDIA_SALTO_S", 10))
        partes = []
        for s in objetivo:
            if a == "consultar":
                t = await self._titulo(s)
                est = self._estado(s)
                que = {"PLAYING": "Suena", "PAUSED": "En pausa", "STOPPED": "Detenido"}.get(est, "Sesión")
                partes.append(f"{que}: {t}" if t else f"{que} (sin título) en {self._id(s) or 'una app'}.")
                continue
            if a == "pausa":
                ok = await s.try_pause_async()
            elif a == "play":
                ok = await s.try_play_async()
            elif a == "alternar":
                ok = await s.try_toggle_play_pause_async()
            elif a == "detener":
                ok = await s.try_stop_async()
            elif a == "siguiente":
                ok = await s.try_skip_next_async()
            elif a == "anterior":
                ok = await s.try_skip_previous_async()
            elif a in ("adelantar", "retroceder", "reiniciar"):
                tl = s.get_timeline_properties()
                pos = tl.position.total_seconds() if hasattr(tl.position, "total_seconds") else tl.position / 1e7
                nueva = 0.0 if a == "reiniciar" else max(0.0, pos + (salto if a == "adelantar" else -salto))
                ok = await s.try_change_playback_position_async(int(nueva * 10_000_000))
            else:
                ok = False
            if not ok:
                partes.append(f"{self._id(s) or 'La app'} no aceptó la orden.")
                continue
            if a in ("siguiente", "anterior", "reiniciar"):
                await asyncio.sleep(0.6)
                t = await self._titulo(s)
                partes.append({"siguiente": "⏭ Siguiente", "anterior": "⏮ Anterior",
                               "reiniciar": "⏮ Desde el principio"}[a] + (f": {t}" if t else "."))
            else:
                partes.append({"pausa": "⏸ En pausa.", "play": "▶ Reproduciendo.", "alternar": "⏯ Listo.",
                               "detener": "⏹ Detenido.",
                               "adelantar": f"⏩ +{salto} s.", "retroceder": f"⏪ −{salto} s."}[a])
        ok_total = any(not p.endswith("no aceptó la orden.") for p in partes)
        if orden.get("todo") and len(partes) > 1 and a in ("pausa", "play", "detener", "alternar"):
            return ok_total, f"{partes[0].split('.')[0]}. ({len(partes)} reproductores)"
        return ok_total, " ".join(partes) if partes else "No pasó nada."

    def ejecutar(self, orden: dict) -> Tuple[bool, str]:
        with _lock:
            return asyncio.run(self._run(orden))


_backend = None


def usar_backend(b) -> None:
    """Para pruebas: fija un backend con método ejecutar(orden)->(bool,str)."""
    global _backend
    _backend = b


def _elegir_backend():
    global _backend
    if _backend is not None:
        return _backend
    if sys.platform == "win32":
        try:
            _backend = _BackendSMTC()
            log.info("🎵 Multimedia: backend SMTC (winsdk)")
            return _backend
        except Exception as e:
            log.info("🎵 Multimedia: sin winsdk (%s); uso teclas multimedia", e)
        _backend = _BackendTeclas()
        return _backend
    return None


# ───────────────────────── ejecución ─────────────────────────

def ejecutar(texto: str) -> Tuple[bool, str]:
    """Interpreta y ejecuta. Nunca lanza."""
    try:
        orden = interpretar(texto)
        if orden is None:
            return False, "No entendí qué hacer con la reproducción."
        b = _elegir_backend()
        if b is None:
            return False, "El control multimedia solo funciona en Windows."
        try:
            return b.ejecutar(orden)
        except Exception as e:
            log.warning("🎵 %s falló (%s); pruebo con teclas", getattr(b, "nombre", "backend"), e)
            if not isinstance(b, _BackendTeclas) and sys.platform == "win32":
                global _backend
                _backend = _BackendTeclas()
                return _backend.ejecutar(orden)
            return False, "No pude controlar la reproducción."
    except Exception:
        log.exception("multimedia.ejecutar falló")
        return False, "No pude controlar la reproducción."


def diagnostico() -> str:
    b = _elegir_backend()
    if b is None:
        return "🎵 Multimedia: no disponible (no es Windows)."
    líneas = [f"🎵 Multimedia {MULTIMEDIA_VERSION} · backend: {getattr(b, 'nombre', '?')}"]
    if isinstance(b, _BackendSMTC):
        try:
            async def _l():
                m, ses = await b._sesiones()
                out = []
                for s in ses:
                    out.append(f"   · {b._id(s)} → {b._estado(s)} {await b._titulo(s)}")
                return out
            ss = asyncio.run(_l())
            líneas += ss or ["   (ninguna sesión multimedia abierta ahora)"]
        except Exception as e:
            líneas.append(f"   no pude listar sesiones: {e}")
    else:
        líneas.append("   (con «pip install winsdk» pauso/sigo sin alternar, elijo app y leo el título)")
    return "\n".join(líneas)


if __name__ == "__main__":
    print(diagnostico())
    for f in ("pausa la música", "siguiente canción", "qué está sonando"):
        print(f, "→", interpretar(f))
