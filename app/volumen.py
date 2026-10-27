# 📁 volumen.py — Control del volumen del sistema en lenguaje natural (v1)
#
# Qué hace: entiende frases como «súbele tantito», «ya no se oye nada», «pon el volumen al 40»,
# «mutea eso», «quítale el mute», «bájale a la música» y las convierte en una acción sobre el volumen
# MAESTRO de Windows. No hay lista rígida de comandos: se reconoce la intención por señales
# (qué se quiere hacer + de qué se habla + cuánto), tolerando cortesías, muletillas y errores de STT.
#
# Contrato (igual que los demás módulos aislados de SARA; nada aquí lanza hacia fuera):
#   es_comando(texto)  -> bool            ¿esta frase es de volumen? (barata, sin efectos)
#   interpretar(texto) -> dict | None     {"accion": subir|bajar|fijar|mutear|desmutear|consultar, "pct": int|None}
#   ejecutar(texto)    -> (bool, str)     interpreta y actúa; el str es la respuesta corta que SARA dice/escribe
#   diagnostico()      -> str             qué backend se usa y su estado
#
# Backends (se elige solo, con degradación elegante):
#   1) pycaw (pip install pycaw): lee y fija el nivel exacto, sabe si hay mute. Es el recomendado.
#   2) Teclas multimedia de Windows (ctypes, sin dependencias): sube/baja de 2 en 2 %, nivel exacto
#      «a ciegas» (baja todo y sube), pero NO puede leer el nivel ni el estado de mute.
#
# Parámetros opcionales (config.py; todos tienen valor por defecto aquí):
#   VOL_PASO = 10 · VOL_PASO_POCO = 5 · VOL_PASO_MUCHO = 25 · VOL_PERMITIR_REMOTO = True (lo lee core_bridge)

import logging
import math
import os
import re
import threading
import unicodedata
from difflib import SequenceMatcher
from typing import Callable, Dict, Optional, Tuple

logger = logging.getLogger("SARA-VOL")

try:
    import config
except Exception:                                   # el módulo sigue funcionando con sus valores por defecto
    config = None


def _cfg(nombre: str, defecto):
    try:
        return getattr(config, nombre, defecto)
    except Exception:
        return defecto


# ─── NORMALIZACIÓN ────────────────────────────────────────────────────────

_NUMEROS_PALABRA = (                                # los más largos primero («setenta y cinco» antes que «cinco»)
    ("setenta y cinco", "75"), ("veinticinco", "25"), ("sesenta y cinco", "65"), ("cuarenta y cinco", "45"),
    ("cincuenta", "50"), ("sesenta", "60"), ("setenta", "70"), ("ochenta", "80"), ("noventa", "90"),
    ("cuarenta", "40"), ("treinta", "30"), ("veinte", "20"), ("quince", "15"), ("diez", "10"),
    ("cinco", "5"), ("cero", "0"), ("cien", "100"),
)


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = t.replace("%", " por ciento ")
    t = re.sub(r"[^a-z0-9ñ ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for palabra, cifra in _NUMEROS_PALABRA:
        t = re.sub(rf"(?<![a-z0-9]){palabra}(?![a-z0-9])", cifra, t)
    return t


def _corregir_stt(t: str) -> str:
    """Whisper oye «bolumen», «volumem»...: cualquier palabra muy parecida a «volumen» se lee como «volumen»."""
    salida = []
    for tok in t.split():
        if len(tok) >= 6 and tok != "volumen" and SequenceMatcher(None, tok, "volumen").ratio() >= 0.8:
            tok = "volumen"
        salida.append(tok)
    return " ".join(salida)


_CORTESIAS = ("oye sara", "hey sara", "hola sara", "ok sara", "sara", "por favor", "porfa", "porfavor",
              "podrias", "puedes", "me puedes", "me podrias", "quiero que", "necesito que", "ayudame a",
              "eh", "este", "pues", "a ver", "mira", "oye")


def _sin_cortesias(t: str) -> str:
    cambio = True
    while cambio and t:
        cambio = False
        for p in sorted(_CORTESIAS, key=len, reverse=True):
            if t == p:
                return ""
            if t.startswith(p + " "):
                t, cambio = t[len(p) + 1:], True
                break
    return t


# ─── SEÑALES DEL LENGUAJE ─────────────────────────────────────────────────

# De qué se habla. «musica» solo vale para subir/bajar (no para apagar: «apaga la música» ≠ silenciar el equipo).
_N_VOL = r"(?:volumen|sonido|audio|bocinas?|bocinitas?|altavoces|altavoz|parlantes?)"
_N_VOL_MUSICA = r"(?:volumen|sonido|audio|bocinas?|bocinitas?|altavoces|altavoz|parlantes?|musica|cancion)"
_ART = r"(?:(?:el|la|los|las|mi|mis|todo el|todo|ese|esa|este|esta)\s+)?"

# Dispositivos o cosas que NO son el equipo: «sube el volumen de la tele» no debe tocar el PC.
_AJENO = re.compile(r"\b(tele|television|televisor|televisión|radio|carro|coche|auto|celular|cel|telefono|"
                    r"estereo|bluetooth|gui?itarra|bajo electrico)\b")

# Frases que son preguntas o búsquedas sobre el tema, no una orden.
_NO_ORDEN = re.compile(r"^(?:busca|buscar|buscame|googlea|investiga|encuentra|explicame|explica|como|por que|"
                       r"que significa|que es|cual es la diferencia|dime como|ensename|enseñame|tutorial)\b")
_INICIO_AJENO = re.compile(r"^(?:crea|crear|agrega|anade|anota|apunta|recuerdame|genera|escribe|manda|envia|abre|cierra|"
                           r"ejecuta|inicia|lanza|guarda|mueve|copia|borra|elimina|renombra|comprime|muestra|dime|lee)\b")
_CORTE_COMPUESTO = re.compile(r"\s+(?:y|luego|despues|tambien|ademas|entonces)\s+")
_COMPUESTO = re.compile(r"\b(?:y|luego|despues|tambien|ademas|entonces)\s+(?:abre|cierra|busca|pon|ponme|reproduce|"
                        r"toca|ejecuta|crea|dime|muestra|manda|envia|inicia|lanza|genera|guarda|recuerdame|enciende|"
                        r"apaga|mueve|copia|borra|elimina)\b")

# MUTE / DESMUTE
_RE_DESMUTE = re.compile(
    r"\b(?:desmutea|desmutear|desmutealo|desmutealas|unmute|desilencia|desilenciar)\b"
    r"|\b(?:quita|quitale|quitar|quitarle|desactiva|apaga|saca|sacale|cancela|elimina)\s+" + _ART + r"(?:mute|silencio)\b"
    r"|\b(?:activa|reactiva|activale|enciende|prende|habilita|restaura|restablece|regresa|devuelve|dale|"
    r"pon|ponle|ponme|ponla|ponlo)\s+(?:de nuevo |otra vez )?" + _ART + r"(?:sonido|audio)\b"
    r"|\b(?:con|ponle)\s+sonido\b|\bque\s+(?:vuelva|regrese)\s+(?:el\s+)?(?:sonido|audio)\b"
    r"|\bya\s+(?:no\s+)?(?:esta|estes)\s+(?:mute|silenciad[oa])\b")
_RE_MUTE = re.compile(
    r"\b(?:silencia|silenciar|silenciame|silenciala|silencialo|mutea|mutear|mutealo|mutealas|muteame|mute|miut|muteo)\b"
    r"|\b(?:quita|quitale|quitar|apaga|apagale|apagar|desactiva|corta|cancela)\s+" + _ART + _N_VOL + r"\b"
    r"|\bsin\s+(?:sonido|audio)\b"
    r"|\b(?:pon|ponlo|ponla|ponme|dejalo|dejala|deja)\s+(?:en\s+)?(?:silencio|mute)\b")

# CONSULTA (estrechas a propósito: «qué es el volumen de un cubo» NO es una consulta)
_FIN_CONSULTA = (r"(?:\s+(?:exacto|exacta|actual|ahorita|ahora|puesto|que\s+tengo|que\s+tiene|del\s+equipo|"
                 r"de\s+la\s+(?:compu|computadora|pc|laptop)|de\s+mi\s+(?:compu|computadora|pc|laptop)))*\s*$")
_RE_CONSULTA = re.compile(
    r"\bcuanto\s+(?:volumen|sonido)\b"
    r"|\b(?:a|en)\s+cuanto\s+(?:esta|lo\s+tengo|tengo|esta\s+puesto|estamos)\b.*\b(?:volumen|sonido)\b"
    r"|\b(?:que|cual)\s+(?:es\s+)?(?:el\s+)?(?:nivel\s+(?:de|del)\s+)?(?:volumen|sonido)\s+(?:tengo|tiene|actual|"
    r"ahorita|ahora|esta\s+puesto|exacto|estoy|estamos)\b"
    r"|\ben\s+que\s+(?:volumen|nivel)\b.*\b(?:estoy|estamos|esta|tengo)\b"
    r"|\bque\s+nivel\s+de\s+(?:volumen|sonido)\b"
    r"|\bque\s+tan\s+(?:alto|fuerte)\s+esta\s+(?:el\s+)?(?:volumen|sonido)\b"
    r"|\b(?:dime|dame|dime\s+cual\s+es|dime\s+cuanto\s+es|cual\s+es|cuanto\s+es|muestrame|checa|revisa|"
    r"checame|revisame)\s+(?:el\s+)?(?:nivel\s+(?:de|del)\s+)?(?:volumen|sonido)" + _FIN_CONSULTA +
    r"|^(?:el\s+)?(?:nivel\s+(?:de|del)\s+)?volumen\s+(?:actual|exacto)\b"
    r"|\b(?:esta|estoy|sigue|seguimos)\s+(?:muteado|silenciado|en\s+silencio|en\s+mute)\b"
    r"|\b(?:dime|checa|revisa)\s+si\s+(?:esta|estoy|sigue)\s+(?:muteado|silenciado|en\s+mute|en\s+silencio)\b"
    r"|\bcomo\s+esta\s+(?:el\s+)?(?:volumen|sonido)\b")

# SUBIR / BAJAR (por verbo)
_V_SUBIR = r"(?:sube|subele|subelo|subela|subeme|subir|subirle|aumenta|aumentale|aumentar|incrementa|alza|alzale)"
_V_BAJAR = r"(?:baja|bajale|bajalo|bajala|bajame|bajar|bajarle|reduce|reducele|reducir|disminuye|disminuir|atenua)"
_CLITICO_SUBIR = {"subele", "subelo", "subela", "subeme", "subirle", "aumentale", "alzale"}
_CLITICO_BAJAR = {"bajale", "bajalo", "bajala", "bajame", "bajarle", "reducele"}

# SUBIR / BAJAR (por descripción: «más fuerte», «no se oye», «está muy alto»)
_RE_SUBIR_DESC = re.compile(
    r"\bmas\s+(?:volumen|fuerte|alto|duro)\b"
    r"|\bno\s+(?:se\s+)?(?:oye|escucha|alcanza\s+a\s+oir|alcanzo\s+a\s+oir)\b|\bno\s+(?:te\s+|lo\s+|la\s+)?(?:oigo|escucho)\b"
    r"|\bse\s+(?:oye|escucha)\s+(?:muy\s+|demasiado\s+|tan\s+|bien\s+|medio\s+)?(?:bajo|bajito|quedito|despacio|poco)\b"
    r"|\b(?:muy|demasiado|tan)\s+(?:bajo|bajito|quedito)\b")
_RE_BAJAR_DESC = re.compile(
    r"\bmenos\s+(?:volumen|fuerte)\b|\bmas\s+(?:bajo|bajito|quedito|suave)\b"
    r"|\bse\s+(?:oye|escucha)\s+(?:muy\s+|demasiado\s+|tan\s+|bien\s+|medio\s+)?(?:fuerte|alto|duro|recio)\b"
    r"|\b(?:muy|demasiado|tan)\s+(?:fuerte|alto|duro|recio)\b")
# Las descripciones sin verbo solo valen si se habla de sonido o son una frase corta dirigida a SARA.
_DESC_REQUIERE_TEMA = re.compile(r"\b" + _N_VOL_MUSICA + r"\b|\bse\s+(?:oye|escucha)\b|\bno\s+(?:se\s+)?(?:oye|escucha)\b"
                                 r"|\bno\s+(?:te\s+|lo\s+|la\s+)?(?:oigo|escucho)\b")

# FIJAR un nivel
_RE_ABS_PREP = re.compile(r"\b(?:a|al|en|hasta|a\s+un|en\s+un)\s+(\d{1,3})\b")
_RE_ABS_NOMBRE = re.compile(r"\b(?:volumen|sonido|audio)\s+(?:a\s+|al\s+|en\s+|a\s+un\s+)?(\d{1,3})\b")
_RE_MAXIMO = re.compile(r"\b(?:al\s+maximo|a\s+lo\s+maximo|al\s+tope|a\s+todo\s+volumen|a\s+todo\s+lo\s+que\s+da|"
                        r"al\s+maximo\s+volumen|maximo\s+volumen|volumen\s+maximo|a\s+tope)\b")
_RE_MINIMO = re.compile(r"\b(?:al\s+minimo|a\s+lo\s+minimo|volumen\s+minimo|minimo\s+volumen)\b")
_RE_MITAD = re.compile(r"\b(?:a\s+la\s+mitad|la\s+mitad|a\s+media\s+potencia|a\s+medias|volumen\s+medio|volumen\s+normal)\b")

# CANTIDAD relativa
_RE_POCO = re.compile(r"\b(?:poco|poquito|poquitito|tantito|tantitito|tantillo|ligeramente|tantico|una\s+rayita|un\s+tris)\b")
_RE_MUCHO = re.compile(r"\b(?:mucho|muchisimo|bastante|harto|bien\s+(?:fuerte|alto)|un\s+buen\s+tanto)\b")


# ─── INTERPRETACIÓN ───────────────────────────────────────────────────────

def _pct(n) -> int:
    return max(0, min(100, int(n)))


def interpretar(texto: str) -> Optional[Dict]:
    """Frase → {"accion", "pct"} o None si no es del volumen. Determinista, sin red, sin efectos."""
    try:
        n = _corregir_stt(_norm(texto))
        if not n or len(n.split()) > 16:                # un texto largo no es una orden de volumen
            return None
        base = _sin_cortesias(n)
        if not base:
            return None
        es_consulta = bool(_RE_CONSULTA.search(base))
        if _COMPUESTO.search(base) or (_INICIO_AJENO.match(base) and not es_consulta):   # «abre youtube y sube el volumen» / «crea una tarea…»
            return None                                 # (si es compuesta la separa el puente conversacional)
        partes = _CORTE_COMPUESTO.split(base)
        if len(partes) > 1 and any(_INICIO_AJENO.match(_sin_cortesias(p)) and not _RE_CONSULTA.search(p) for p in partes):
            return None
        if _AJENO.search(base):
            return None
        tokens = base.split()
        corta = len(tokens) <= 5

        # 1) consulta («a cuánto está el volumen») — antes que el filtro de preguntas
        if es_consulta:
            return {"accion": "consultar", "pct": None}

        if _NO_ORDEN.match(base):                       # «cómo subo el volumen», «busca qué es el volumen»
            return None

        habla_de_sonido = bool(re.search(r"\b" + _N_VOL_MUSICA + r"\b", base))
        verbo_subir = re.search(r"\b" + _V_SUBIR + r"\b", base)
        verbo_bajar = re.search(r"\b" + _V_BAJAR + r"\b", base)
        clitico = (tokens[0] in _CLITICO_SUBIR or tokens[0] in _CLITICO_BAJAR) and corta

        # 2) fijar un nivel exacto
        pct = None
        if _RE_MAXIMO.search(base) and (habla_de_sonido or verbo_subir or corta):
            pct = 100
        elif _RE_MINIMO.search(base) and (habla_de_sonido or verbo_bajar or corta):
            pct = 0
        elif _RE_MITAD.search(base) and habla_de_sonido:
            pct = 50
        else:
            m = _RE_ABS_NOMBRE.search(base)
            if m and not ((verbo_subir or verbo_bajar) and not re.search(r"\b(?:a|al|en|hasta)\s+\d", base)):
                pct = _pct(m.group(1))                  # «volumen 40» / «volumen al 40»; «sube el volumen 20» es relativo
            if pct is None and (habla_de_sonido or clitico):
                m = _RE_ABS_PREP.search(base)
                if m and (verbo_subir or verbo_bajar or re.search(r"\b(?:pon|ponle|ponlo|deja|dejalo|ajusta|fija|"
                                                                    r"establece|cambia|configura|regula)\b", base)):
                    pct = _pct(m.group(1))
        if pct is not None:
            return {"accion": "fijar", "pct": pct}

        # 3) mute / desmute (antes que subir/bajar: «quita el mute» contiene «quita»)
        if _RE_DESMUTE.search(base):
            return {"accion": "desmutear", "pct": None}
        if _RE_MUTE.search(base):
            return {"accion": "mutear", "pct": None}

        # 4) subir / bajar por verbo: exige hablar de sonido, o un verbo con clítico en frase corta («súbele»)
        if verbo_subir and not verbo_bajar and (habla_de_sonido or clitico):
            return {"accion": "subir", "pct": _cantidad(base)}
        if verbo_bajar and not verbo_subir and (habla_de_sonido or clitico):
            return {"accion": "bajar", "pct": _cantidad(base)}

        # 5) subir / bajar por descripción («más fuerte», «no se oye», «está muy alto la música»)
        tema_ok = bool(_DESC_REQUIERE_TEMA.search(base)) or corta
        if tema_ok and _RE_SUBIR_DESC.search(base) and not _RE_BAJAR_DESC.search(base):
            return {"accion": "subir", "pct": _cantidad(base)}
        if tema_ok and _RE_BAJAR_DESC.search(base) and not _RE_SUBIR_DESC.search(base):
            return {"accion": "bajar", "pct": _cantidad(base)}
        return None
    except Exception:
        logger.exception("⚠️ volumen.interpretar falló")
        return None


def _cantidad(base: str) -> int:
    """Cuánto subir o bajar: la cifra dicha («sube 20»), «un poquito», «mucho», o el paso por defecto."""
    m = re.search(r"\b(\d{1,3})\b", base)
    if m:
        return max(1, _pct(m.group(1)))
    if _RE_POCO.search(base):
        return max(1, int(_cfg("VOL_PASO_POCO", 5)))
    if _RE_MUCHO.search(base):
        return max(1, int(_cfg("VOL_PASO_MUCHO", 25)))
    return max(1, int(_cfg("VOL_PASO", 10)))


def es_comando(texto: str) -> bool:
    return interpretar(texto) is not None


# ─── BACKENDS ─────────────────────────────────────────────────────────────

_com_local = threading.local()


def _com_listo() -> None:
    """COM debe iniciarse en CADA hilo (SARA procesa cada orden en un hilo del pool). No se desinicia a propósito:
    cerrar COM con punteros vivos provoca violaciones de acceso; el hilo del pool se reutiliza."""
    if getattr(_com_local, "ok", False):
        return
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    _com_local.ok = True


class _BackendPycaw:
    nombre = "pycaw"
    exacto = True                                       # lee y fija el nivel, y sabe si hay mute

    def __init__(self):
        from ctypes import POINTER, cast                # noqa: F401  (se valida que todo importa)
        from comtypes import CLSCTX_ALL                 # noqa: F401
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # noqa: F401

    def _endpoint(self):
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        _com_listo()
        altavoces = AudioUtilities.GetSpeakers()
        if altavoces is None:
            raise RuntimeError("Windows no reporta un dispositivo de salida de audio activo")
        ev = getattr(altavoces, "EndpointVolume", None)   # pycaw reciente: el dispositivo ya trae el control
        if ev is not None:
            return ev
        iface = altavoces.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)   # pycaw clásico
        return cast(iface, POINTER(IAudioEndpointVolume))

    def get_level(self) -> Optional[int]:
        return int(round(self._endpoint().GetMasterVolumeLevelScalar() * 100))

    def set_level(self, pct: int) -> None:
        self._endpoint().SetMasterVolumeLevelScalar(_pct(pct) / 100.0, None)

    def get_mute(self) -> Optional[bool]:
        return bool(self._endpoint().GetMute())

    def set_mute(self, valor: bool) -> None:
        self._endpoint().SetMute(1 if valor else 0, None)


class _BackendTeclas:
    """Teclas multimedia de Windows. Cada pulsación de volumen mueve 2 % (50 pulsaciones = 0 → 100)."""
    nombre = "teclas"
    exacto = False
    _VK_MUTE, _VK_BAJAR, _VK_SUBIR = 0xAD, 0xAE, 0xAF

    def __init__(self):
        import ctypes
        self._user32 = ctypes.windll.user32            # AttributeError fuera de Windows → se descarta el backend
        self._mute_asumido = False

    def _tecla(self, vk: int, veces: int = 1) -> None:
        import time
        for _ in range(max(0, veces)):
            self._user32.keybd_event(vk, 0, 1, 0)       # KEYEVENTF_EXTENDEDKEY
            self._user32.keybd_event(vk, 0, 1 | 2, 0)   # + KEYEVENTF_KEYUP
            time.sleep(0.01)

    def get_level(self) -> Optional[int]:
        return None                                     # sin pycaw no se puede leer

    def get_mute(self) -> Optional[bool]:
        return None

    def subir(self, pct: int) -> None:
        self._tecla(self._VK_SUBIR, max(1, math.ceil(pct / 2)))
        self._mute_asumido = False                      # subir el volumen quita el mute en Windows

    def bajar(self, pct: int) -> None:
        self._tecla(self._VK_BAJAR, max(1, math.ceil(pct / 2)))

    def set_level(self, pct: int) -> None:
        self._tecla(self._VK_BAJAR, 50)                 # a 0 …
        self._tecla(self._VK_SUBIR, math.ceil(_pct(pct) / 2))   # … y hasta el nivel pedido
        self._mute_asumido = False

    def set_mute(self, valor: bool) -> None:
        if valor:
            if not self._mute_asumido:                  # la tecla ALTERNA: solo se pulsa si creemos que hay sonido
                self._tecla(self._VK_MUTE)
            self._mute_asumido = True
        else:
            self._tecla(self._VK_SUBIR)                 # subir un paso quita el mute en Windows …
            self._tecla(self._VK_BAJAR)                 # … y se vuelve al nivel de antes
            self._mute_asumido = False


_backend = None                # se elige la primera vez (o lo fija usar_backend en pruebas)
_backend_probado = False
_pycaw_roto = False
_lock = threading.Lock()
_ultimo_error = ""


def usar_backend(b) -> None:
    """Para pruebas: fija un backend simulado."""
    global _backend, _backend_probado
    _backend, _backend_probado = b, True


def _obtener_backend():
    global _backend, _backend_probado, _ultimo_error
    if _backend_probado and (_backend is not None):
        return _backend
    if os.name != "nt":
        _ultimo_error = "el control de volumen solo funciona en Windows"
        return None
    if not _pycaw_roto:
        try:
            _backend = _BackendPycaw()
            _backend_probado = True
            logger.info("🔊 Volumen: backend pycaw (nivel exacto)")
            return _backend
        except Exception as e:
            _ultimo_error = f"pycaw no disponible ({type(e).__name__}: {e})"
            logger.info(f"🔊 Volumen: sin pycaw ({type(e).__name__}); uso las teclas multimedia. "
                        f"Para nivel exacto: pip install pycaw")
    try:
        _backend = _BackendTeclas()
        _backend_probado = True
        logger.info("🔊 Volumen: backend teclas multimedia (sin lectura de nivel)")
        return _backend
    except Exception as e:
        _ultimo_error = f"sin backend de volumen ({type(e).__name__}: {e})"
        return None


def _degradar_a_teclas(motivo: Exception):
    """pycaw falló en pleno uso (COM, dispositivo cambiado…): se sigue con las teclas y se registra el error."""
    global _backend, _pycaw_roto, _backend_probado
    logger.warning(f"⚠️ pycaw falló ({type(motivo).__name__}: {motivo}); sigo con las teclas multimedia")
    _pycaw_roto = True
    _backend, _backend_probado = None, False
    return _obtener_backend()


# ─── EJECUCIÓN ────────────────────────────────────────────────────────────

def _txt_nivel(n: Optional[int]) -> str:
    return f"{n} %" if n is not None else ""


def _aplicar(b, plan: Dict) -> Tuple[bool, str]:
    acc, pct = plan["accion"], plan.get("pct")
    exacto = getattr(b, "exacto", False)

    if acc == "consultar":
        if not exacto:
            return False, "Para leer el volumen necesito pycaw. Instálalo con: pip install pycaw"
        n, m = b.get_level(), b.get_mute()
        return True, (f"Está silenciado; el volumen está en {n} %." if m
                      else f"El volumen está en {n} %.")

    if acc == "mutear":
        if exacto and b.get_mute():
            return True, "Ya estaba silenciado."
        b.set_mute(True)
        return True, "Silenciado."

    if acc == "desmutear":
        if exacto:
            if not b.get_mute():
                return True, f"El sonido ya estaba activo, volumen en {b.get_level()} %."
            b.set_mute(False)
            return True, f"Sonido activado, volumen en {b.get_level()} %."
        b.set_mute(False)
        return True, "Sonido activado."

    if acc == "fijar":
        b.set_level(pct)
        if exacto and pct > 0 and b.get_mute():
            b.set_mute(False)
        return True, ("Volumen al mínimo." if pct == 0 else
                      "Volumen al máximo." if pct == 100 else
                      f"Volumen al {pct} %." + ("" if exacto else " (aproximado)"))

    if acc in ("subir", "bajar"):
        paso = int(pct or _cfg("VOL_PASO", 10))
        if exacto:
            actual, mute = b.get_level(), b.get_mute()
            if acc == "subir":
                if mute:
                    b.set_mute(False)
                if actual >= 100 and not mute:
                    return True, "Ya está al máximo."
                nuevo = min(100, actual + paso)
            else:
                if actual <= 0:
                    return True, "Ya está al mínimo."
                nuevo = max(0, actual - paso)
            b.set_level(nuevo)
            return True, ("Volumen al máximo." if nuevo == 100 and acc == "subir" else
                          "Volumen al mínimo." if nuevo == 0 else f"Volumen al {nuevo} %.")
        (b.subir if acc == "subir" else b.bajar)(paso)
        return True, ("Subí el volumen." if acc == "subir" else "Bajé el volumen.")

    return False, "No entendí qué hacer con el volumen."


def ejecutar(texto: str) -> Tuple[bool, str]:
    """Interpreta y actúa sobre el volumen. Nunca lanza: siempre (ok, respuesta)."""
    try:
        plan = interpretar(texto)
        if plan is None:
            return False, "No entendí qué hacer con el volumen."
        with _lock:                                     # una secuencia de teclas a la vez
            b = _obtener_backend()
            if b is None:
                return False, f"No puedo controlar el volumen: {_ultimo_error or 'sin backend disponible'}."
            try:
                ok, msg = _aplicar(b, plan)
            except Exception as e:
                if getattr(b, "nombre", "") == "pycaw":
                    b2 = _degradar_a_teclas(e)
                    if b2 is None:
                        return False, "No pude cambiar el volumen (revisa el log)."
                    ok, msg = _aplicar(b2, plan)
                else:
                    raise
        logger.info(f"🔊 volumen {plan} → {msg}")
        return ok, msg
    except Exception as e:
        logger.exception("❌ volumen.ejecutar falló")
        return False, f"No pude cambiar el volumen ({type(e).__name__})."


def diagnostico() -> str:
    b = _backend if _backend_probado else None
    nombre = getattr(b, "nombre", None) or ("sin elegir aún" if not _backend_probado else "ninguno")
    return (f"Volumen · backend: {nombre} · paso {_cfg('VOL_PASO', 10)} % "
            f"(poco {_cfg('VOL_PASO_POCO', 5)}, mucho {_cfg('VOL_PASO_MUCHO', 25)})"
            + (f" · último error: {_ultimo_error}" if _ultimo_error else ""))


if __name__ == "__main__":                              # python volumen.py  → comprueba el backend y lee el volumen real
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ok, msg = ejecutar("a cuánto está el volumen")
    print("\n" + diagnostico())
    print(("✅ " if ok else "⚠️ ") + msg)
    if not ok and "pycaw" in msg:
        print("   → instala pycaw para leer y fijar el nivel exacto:  pip install pycaw")
