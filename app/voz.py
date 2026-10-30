# 📁 voz.py — Text-to-Speech de SARA (v3, optimizado)
#
# Motor principal : Edge-TTS (voz neuronal, requiere internet + `pip install edge-tts`)
# Respaldo offline: Windows SAPI (System.Speech)
# Reproducción    : MCI de Windows vía ctypes (sin PowerShell, bloqueante)
#
# Velocidad (v3):
#   * CACHÉ en disco: una frase ya dicha suena al instante la próxima vez.
#   * La muletilla ("Claro, enseguida.") está pre-generada: suena en ~0.2 s mientras el resto
#     de la frase se sintetiza EN PARALELO.
#   * Las frases largas se parten en 2 trozos que se sintetizan a la vez.
#   * Se registra en el log cuánto tardó cada síntesis ("🔊 voz: ...").
#
# IMPORTANTE (arregla el bloqueo de la GUI): este módulo YA NO emite `estado_procesando`.
# Ese evento bloquea la caja de texto y llegaba DESPUÉS de la respuesta final → GUI atascada.
# Solo emite `voz_estado(hablando=True|False)`, que app.js debe usar para la aureola.
#
# Voz elegible (v3.1): la voz de SARA se puede cambiar en caliente (menú de apariencia → «Voz de SARA»).
#   * Lista cerrada en VOCES (Edge-TTS + «sapi» = voz offline de Windows, sin internet).
#   * La elección se guarda en voz_config.json (dato de usuario, junto a este archivo) y se reaplica al arrancar.
#   * Sin elección guardada se usa SARA_VOZ del .env (o es-MX-DaliaNeural). `voz.VOZ_POR_DEFECTO` sigue
#     existiendo y devuelve siempre la voz ACTIVA, para no romper módulos que la lean.
#
# v3.2 (13 oct 2026): Microsoft cambió la validación del servicio Edge-TTS (edge-tts 7.2.8 recibe 403 en TODA
#   síntesis nueva). Para no perder segundos en cada frase ni cambiar de voz sin avisar:
#   * Cortacircuitos: tras EDGE_FALLOS_PARA_PAUSAR fallos seguidos se deja de llamar a Edge durante EDGE_PAUSA_S
#     (el caché sigue sirviendo las frases ya dichas con la voz elegida). Se reintenta solo al terminar la pausa.
#   * Con Edge pausado la respuesta va en UNA sola frase con la voz de Windows (SAPI) del MISMO GÉNERO que la elegida
#     (Jorge/Álvaro/Alonso → masculina; Dalia/Elvira/Elena → femenina), sin muletilla.
#   * Los logs dicen qué voz se usó y por qué se cayó a SAPI.
#
# v3.3 (13 oct 2026): Microsoft cambió OTRA VEZ el servicio (desde el 10 oct 2026: host nuevo y token Sec-MS-GEC v2) y
#   la librería edge-tts 7.2.8 aún no lo soporta (issue #490 de rany2/edge-tts). Mientras tanto SARA sigue hablando:
#   * Las voces de Windows instaladas (es-*) se listan de verdad y se pueden elegir: id «sapi:<nombre>».
#   * Si eliges una voz neuronal y Edge no responde, suena la voz de Windows del mismo género Y de la misma región
#     (Jorge → la masculina de México; Álvaro → la masculina de España), no una cualquiera.
#   * sintetizar() (notas de voz de Telegram) ya no devuelve None sin Edge: genera la voz de Windows en WAV y la
#     pasa a OGG/Opus con PyAV (si PyAV falla, devuelve el WAV). Usa su propia carpeta de caché: no toca las muletillas.
#   * completo=True: sin el recorte de 300 caracteres / 3 líneas ni «El resto lo puedes ver en pantalla» (pendiente W2).
#   * estado_edge() y reintentar_edge(): el menú muestra si las neuronales están caídas y permite reintentar al instante
#     (p. ej. tras actualizar edge-tts) sin esperar la pausa de 5 min.
#
# v3.4 (13 oct 2026): motor neuronal OFFLINE Piper (módulo voz_piper.py). Voces «piper:<modelo>» en el menú.
#   * Orden de voz: neuronal elegida (Edge o Piper) → Piper del mismo género/región si Edge cae → voz de Windows.
#   * Piper devuelve WAV: la reproducción usa waveaudio (_tipo_mci) y las notas de Telegram pasan a OGG/Opus.
#   * reintentar_edge() prueba SOLO Edge (sin que el respaldo Piper dé un falso «volvió»).
#   * Si falta piper-tts o la voz no está descargada, todo sigue como en v3.3.
#
# v3.5 (14 oct 2026): motores neuronales LOCALES renovados (sin internet, sin claves):
#   * Se retiran las voces Piper «medium» (Ald, Davefx, Sharvard). Entran Kokoro (voz_kokoro.py: Dora y Alex) y la voz
#     Piper de calidad alta es_MX-claude-high. Ids «kokoro:<voz>» y «piper:<modelo>».
#   * Respaldo si Edge cae: Kokoro del mismo género → Piper → voz de Windows.
#   * Velocidad: el modelo local se puede precargar al arrancar/elegir la voz (sin pagar la carga en la 1.ª frase), las frases
#     se parten en trozos cortos (suena el 1.º mientras se sintetiza el 2.º) y las notas de Telegram reutilizan caché.
#
# v3.6 (15 oct 2026): SIN INTERNET queda solo Piper «claude» + la voz de Windows; CON INTERNET siguen las de Edge-TTS.
#   * Voz por defecto: «piper:es_MX-claude-high» (funciona sin red). Las Edge (Dalia, Jorge…) se eligen en el menú.
#   * Kokoro queda DESACTIVADO por defecto (una sola voz local): SARA_USAR_KOKORO=1 lo reactiva.
#     SARA_USAR_EDGE=0 oculta las de Edge (por defecto están activas).
#   * Un voz_config.json con una voz de Kokoro ya no se obedece: se usa la voz por defecto.
#   * No cambia nada más: caché, muletillas, notas de Telegram (WAV→OGG/Opus) y respaldo SAPI siguen igual.
#
# v3.7 (18 oct 2026): intento de recuperar las voces de Edge-TTS (Dalia, Jorge…) SIN cambiar de servicio ni descargar nada:
#   * _parchar_edge(): host nuevo api.msedgeservices.com (arreglo del PR #491 de edge-tts, sin publicar). Se aplica solo,
#     una vez, y se desactiva con SARA_EDGE_PARCHE=0. Pide `pip install -U edge-tts` (>= 7.3.1).
#   * Si el servicio sigue rechazando, todo queda como en v3.6 (cortacircuitos y respaldo local).
#   * NO VERIFICADO contra el servicio real (el taller no llega a los servidores de Microsoft): probar con probar_edge.py.
#
# API: hablar(texto, voz=None, emit=None, exito=True, interrumpir=False, forzar=False, muletilla=True)
#      detener() · silenciar() · activar() · esta_activa() · probar() · precalentar()
#      get_voz() · set_voz(id) · listar_voces() · estado_edge() · reintentar_edge()
#      sintetizar(texto, voz=None, completo=False)

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
import queue
import random
import re
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

import config as _config

logger = logging.getLogger("SARA-VOZ")

USAR_EDGE = os.getenv("SARA_USAR_EDGE", "1") != "0"          # v3.6: activo (voces CON internet); "0" las oculta
USAR_KOKORO = os.getenv("SARA_USAR_KOKORO", "0") == "1"      # v3.6: apagado (sin internet: solo Claude y Windows)

voz_kokoro = None
if USAR_KOKORO:                          # motor neuronal OFFLINE opcional (Kokoro-82M)
    try:
        import voz_kokoro
    except Exception as _e_kokoro:
        voz_kokoro = None
        logger.info(f"ℹ️ Kokoro no disponible ({type(_e_kokoro).__name__}: {_e_kokoro})")
try:                                     # motor neuronal OFFLINE (Piper); si falta, SARA sigue con Edge-TTS / Windows
    import voz_piper
except Exception as _e_piper:
    voz_piper = None
    logger.info(f"ℹ️ Piper no disponible ({type(_e_piper).__name__}: {_e_piper})")

VOZ_PIPER_BASE = "piper:es_MX-claude-high"                  # la voz única de SARA (v3.6)
_VOZ_ENV = os.getenv("SARA_VOZ", "").strip()                # el .env solo vale si apunta a un motor activo
if _VOZ_ENV.startswith(("piper:", "sapi")) or (USAR_KOKORO and _VOZ_ENV.startswith("kokoro:")) or (USAR_EDGE and _VOZ_ENV):
    VOZ_BASE = _VOZ_ENV
else:
    VOZ_BASE = VOZ_PIPER_BASE                               # voz por defecto si el usuario no eligió otra
VOZ_SAPI = "sapi"                                           # voz offline de Windows (System.Speech)
CONFIG_VOZ = Path(__file__).resolve().parent / "voz_config.json"
PREFIJO_WIN = "sapi:"                                       # id de una voz de Windows concreta: «sapi:<nombre completo>»
_RE_ID_WIN = re.compile(r"^sapi:[\w .()\-]{1,90}$")

# Lista cerrada de voces elegibles (id, nombre, género, región, estilo). Añadir una voz = añadir una línea.
VOCES = [
    {"id": "es-MX-DaliaNeural", "nombre": "Dalia", "genero": "F", "region": "México", "estilo": "Natural y expresiva"},
    {"id": "es-MX-JorgeNeural", "nombre": "Jorge", "genero": "M", "region": "México", "estilo": "Cálida y profesional"},
    {"id": "es-ES-AlvaroNeural", "nombre": "Álvaro", "genero": "M", "region": "España", "estilo": "Sobria, estilo mayordomo"},
    {"id": "es-ES-ElviraNeural", "nombre": "Elvira", "genero": "F", "region": "España", "estilo": "Clásica y elegante"},
    {"id": "es-US-AlonsoNeural", "nombre": "Alonso", "genero": "M", "region": "EE. UU. (neutro)", "estilo": "Pausada y corporativa"},
    {"id": "es-AR-ElenaNeural", "nombre": "Elena", "genero": "F", "region": "Argentina", "estilo": "Cálida y dinámica"},
    {"id": VOZ_SAPI, "nombre": "Windows (sin internet)", "genero": "S", "region": "Offline", "estilo": "Voz del sistema, siempre disponible"},
]
_IDS_VOCES = {v["id"] for v in VOCES}
_GENERO_DE = {v["id"]: v["genero"] for v in VOCES}      # 'F' | 'M' | 'S'


def _es_windows(voz_id: str) -> bool:
    """True si la voz es de Windows (la genérica «sapi» o una concreta «sapi:<nombre>»)."""
    return voz_id == VOZ_SAPI or str(voz_id).startswith(PREFIJO_WIN)


def _es_piper(voz_id: str) -> bool:
    """True si la voz es de un motor neuronal LOCAL: Piper («piper:<modelo>») o Kokoro («kokoro:<voz>»).
    (El nombre se conserva por compatibilidad: todo el código que decía «Piper» ahora cubre a los dos.)"""
    return str(voz_id).startswith(("piper:", "kokoro:"))


def _mod_local(voz_id: str):
    """Módulo que sintetiza esa voz local (voz_kokoro o voz_piper); None si no está cargado."""
    return voz_kokoro if str(voz_id).startswith("kokoro:") else voz_piper


def _listar_locales() -> List[dict]:
    """Voces locales con `instalada`; Kokoro primero (es la de mejor calidad: manda en los respaldos)."""
    salida: List[dict] = []
    for m in (voz_kokoro, voz_piper):
        try:
            if m is not None and m.disponible():
                salida += m.listar()
        except Exception as e:
            logger.debug(f"_listar_locales: {e}")
    return salida


def _tipo_mci(ruta: str) -> str:
    """Tipo MCI según el archivo: los WAV de Piper/Windows van con waveaudio; los MP3 de Edge, con mpegvideo."""
    return "waveaudio" if str(ruta).lower().endswith(".wav") else "mpegvideo"


def _genero(voz_id: str) -> str:
    """'F' | 'M' | 'S' de cualquier voz (Edge, Piper o Windows)."""
    if _es_piper(voz_id) and _mod_local(voz_id) is not None:
        return _mod_local(voz_id).genero(voz_id)
    return _GENERO_DE.get(voz_id, "S")


def _cultura(voz_id: str) -> str:
    """'es-MX', 'es-ES'… de una voz neuronal ('' si no se sabe)."""
    if _es_piper(voz_id):
        return _mod_local(voz_id).cultura(voz_id) if _mod_local(voz_id) is not None else ""
    return str(voz_id)[:5]


def _voz_valida(voz_id: str, estricta: bool = False) -> bool:
    """Lista cerrada: las de VOCES, las de Piper conocidas, la genérica, o «sapi:<nombre>» con forma segura.
    estricta=True además comprueba que esa voz de Windows esté instalada (puede tardar ~1 s la primera vez)."""
    if voz_id in _IDS_VOCES:
        return voz_id == VOZ_SAPI or USAR_EDGE              # v3.6: las de Edge solo si SARA_USAR_EDGE=1
    if _es_piper(voz_id):
        return _mod_local(voz_id) is not None and _mod_local(voz_id).conocida(voz_id)
    if not (isinstance(voz_id, str) and _RE_ID_WIN.match(voz_id)):
        return False
    return (not estricta) or voz_id[len(PREFIJO_WIN):] in {v["name"] for v in _cargar_sapi()}

# Cortacircuitos de Edge-TTS (ver nota v3.2 arriba)
EDGE_FALLOS_PARA_PAUSAR = 2
EDGE_PAUSA_S = 300
_edge_fallos = 0
_edge_pausado_hasta = 0.0
_edge_ultimo_ok = 0.0            # monotonic de la última síntesis neuronal correcta (0 = ninguna en esta sesión)
_edge_ultimo_error = ""


def edge_pausado() -> bool:
    """True mientras Edge-TTS está en pausa por fallos seguidos (las frases en caché siguen sonando)."""
    return time.monotonic() < _edge_pausado_hasta


def estado_edge() -> dict:
    """{'estado': 'ok'|'caido'|'desconocido', 'error': str}. «caido» desde el primer fallo hasta el próximo acierto."""
    if edge_pausado() or _edge_fallos > 0:
        return {"estado": "caido", "error": _edge_ultimo_error}
    return {"estado": "ok" if _edge_ultimo_ok else "desconocido", "error": ""}


# ─── VOCES DE WINDOWS (SAPI): se listan las instaladas, solo español ──────

_sapi_cache: Optional[List[dict]] = None
_sapi_t = 0.0
_sapi_lock = threading.Lock()
_REGIONES = {"es-MX": "México", "es-ES": "España", "es-US": "EE. UU.", "es-AR": "Argentina", "es-CO": "Colombia",
             "es-CL": "Chile", "es-PE": "Perú", "es-VE": "Venezuela", "es-CU": "Cuba"}


def _nombre_corto(nombre: str) -> str:
    """'Microsoft Sabina Desktop - Spanish (Mexico)' → 'Sabina'."""
    n = re.sub(r"\s+-\s+.*$", "", nombre)
    n = re.sub(r"\s+Desktop\b.*$", "", n)
    return re.sub(r"^Microsoft\s+", "", n).strip() or nombre


def _cargar_sapi(forzar: bool = False) -> List[dict]:
    """Voces de Windows instaladas en español: [{'name','cultura','genero'('M'|'F'|'S')}]. Se consulta una vez por
    sesión (PowerShell, ~1 s); si falla, se reintenta como mucho cada 2 min. Fuera de Windows devuelve []."""
    global _sapi_cache, _sapi_t
    if os.name != "nt":
        return []
    with _sapi_lock:
        if _sapi_cache is not None and not forzar and (_sapi_cache or time.monotonic() - _sapi_t < 120):
            return list(_sapi_cache)
        ps = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; Add-Type -AssemblyName System.Speech; "
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$s.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $i = $_.VoiceInfo; "
              "'{0}|{1}|{2}' -f $i.Name, $i.Culture.Name, $i.Gender }")
        voces: List[dict] = []
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30,
                               creationflags=CREATE_NO_WINDOW)
            for linea in r.stdout.decode("utf-8", errors="replace").splitlines():
                partes = linea.strip().split("|")
                if len(partes) != 3 or not partes[1].lower().startswith("es"):
                    continue
                genero = {"male": "M", "female": "F"}.get(partes[2].strip().lower(), "S")
                voces.append({"name": partes[0].strip(), "cultura": partes[1].strip(), "genero": genero})
            logger.info(f"🖥️ Voces de Windows en español: {[v['name'] for v in voces] or 'ninguna'}")
        except Exception as e:
            logger.warning(f"⚠️ No pude listar las voces de Windows: {type(e).__name__}: {e}")
        _sapi_cache, _sapi_t = voces, time.monotonic()
        return list(voces)


def _sapi_para(voz_id: str) -> Optional[str]:
    """Nombre de la voz de Windows que debe sonar para `voz_id` (None = que Windows elija por género)."""
    instaladas = _cargar_sapi()
    if str(voz_id).startswith(PREFIJO_WIN):
        n = voz_id[len(PREFIJO_WIN):]
        return n if any(v["name"] == n for v in instaladas) else None
    genero = _genero(voz_id)
    if genero not in ("M", "F"):
        return None
    cultura = _cultura(voz_id)
    mismas = [v for v in instaladas if v["genero"] == genero]
    for pref in (cultura, "es-MX", "es-ES"):
        for v in mismas:
            if v["cultura"].lower() == pref.lower():
                return v["name"]
    return mismas[0]["name"] if mismas else None


def _genero_sapi(voz_id: str) -> str:
    """Género para elegir la voz de Windows: 'Male' | 'Female' | 'NotSet'."""
    return {"M": "Male", "F": "Female"}.get(_genero(voz_id), "NotSet")


def _cargar_voz_guardada() -> str:
    try:
        datos = json.loads(CONFIG_VOZ.read_text(encoding="utf-8"))
        v = str(datos.get("voz", ""))
        if _voz_valida(v):
            return v
        logger.warning(f"⚠️ voz_config.json trae una voz desconocida ({v!r}); uso la de por defecto")
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"⚠️ No pude leer {CONFIG_VOZ.name}: {e}")
    return VOZ_BASE


_voz_actual = _cargar_voz_guardada()


def get_voz() -> str:
    return _voz_actual


def listar_voces() -> List[dict]:
    """Voces para el menú: neuronales offline (motor 'piper', con `instalada`), neuronales online (motor 'edge',
    con `disponible` según estado_edge) y las de Windows realmente instaladas (motor 'windows'). Sin voces de Windows en español → la genérica «sapi»."""
    edge_ok = estado_edge()["estado"] != "caido"
    salida = [dict(v, motor="edge", disponible=edge_ok) for v in VOCES if USAR_EDGE and v["id"] != VOZ_SAPI]
    for nombre_motor, mod in (("piper", voz_piper), ("kokoro", voz_kokoro)):   # neuronales SIN internet
        if mod is not None:
            motor_ok = mod.disponible()
            salida = [dict(v, motor=nombre_motor, disponible=motor_ok) for v in mod.listar()] + salida
    wins = [{"id": PREFIJO_WIN + v["name"], "nombre": _nombre_corto(v["name"]), "genero": v["genero"],
             "region": _REGIONES.get(v["cultura"], v["cultura"]), "estilo": "Instalada en este equipo",
             "motor": "windows", "disponible": True} for v in _cargar_sapi()]
    if not wins:
        wins = [dict(v, motor="windows", disponible=True) for v in VOCES if v["id"] == VOZ_SAPI]
    return salida + wins


def nombre_que_suena(voz_id: Optional[str] = None) -> str:
    """Nombre corto de la voz de Windows que sonaría ahora si la elegida es neuronal y Edge está caído ('' si no aplica)."""
    v = voz_id or _voz_actual
    if _es_windows(v) or _es_piper(v) or estado_edge()["estado"] != "caido":
        return ""
    alt = _piper_respaldo(v)
    if alt:
        info = next((x for x in _listar_locales() if x["id"] == alt), None)
        return f"{info['nombre']} (neuronal sin internet)" if info else "Piper"
    n = _sapi_para(v)
    return _nombre_corto(n) if n else ""


def set_voz(voz_id: str) -> bool:
    """Cambia la voz activa y la guarda. False si el id no está en la lista cerrada."""
    global _voz_actual, _precalentado
    if not _voz_valida(voz_id, estricta=True):
        logger.warning(f"⚠️ set_voz: voz no permitida o no instalada {voz_id!r}")
        return False
    if _es_piper(voz_id):                           # voz offline: hay que tener el motor y el modelo descargado
        mod = _mod_local(voz_id)
        if not mod.disponible():
            logger.warning(f"⚠️ set_voz: {voz_id} necesita su motor local ({mod.motivo()})")
            return False
        if not mod.asegurar(voz_id):                # la primera vez baja el modelo (el menú muestra «Descargando…»)
            logger.warning(f"⚠️ set_voz: no pude dejar lista {voz_id} ({mod.motivo()})")
            return False
    _voz_actual = voz_id
    try:
        CONFIG_VOZ.write_text(json.dumps({"voz": voz_id}, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.error(f"❌ No pude guardar {CONFIG_VOZ.name}: {e}")      # sigue activa en esta sesión
    _precalentado = False                                                # las muletillas de la voz nueva
    precalentar()
    return True


def __getattr__(nombre):                 # voz.VOZ_POR_DEFECTO → siempre la voz activa
    if nombre == "VOZ_POR_DEFECTO":
        return _voz_actual
    raise AttributeError(f"module {__name__!r} has no attribute {nombre!r}")


MAX_CARACTERES = 300
MAX_LINEAS = 3
MAX_COLA = 3
TIMEOUT_SINTESIS_S = 20
MAX_ARCHIVOS_CACHE = 300
CREATE_NO_WINDOW = 0x08000000
CACHE_DIR = Path(tempfile.gettempdir()) / "sara_voz_cache"
CACHE_TG = Path(tempfile.gettempdir()) / "sara_voz_cache_notas"      # notas de voz (Telegram): caché aparte, no pisa las muletillas
MAX_ARCHIVOS_CACHE_TG = 60

_activa = os.getenv("SARA_VOZ_ACTIVA", "1") != "0"
_ultimo_error = ""

# ─── LIMPIEZA DEL TEXTO ───────────────────────────────────────────────────

_RE_URL = re.compile(r"https?://\S+")
_RE_RUTA = re.compile(r"[A-Za-z]:\\(?:[^\s\\/:*?\"<>|]+[\\/]?)+")
_RE_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U0001F1E6-\U0001F1FF\u2190-\u21FF\u2300-\u23FF"
    "\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d]+"
)


MAX_CARACTERES_COMPLETO = 1200
MAX_LINEAS_COMPLETO = 40


def limpiar_para_voz(texto: str, completo: bool = False) -> str:
    """completo=True (notas de voz de Telegram): el texto ya está entero en el chat, así que casi no se recorta
    y no se añade «El resto lo puedes ver en pantalla»."""
    max_lineas = MAX_LINEAS_COMPLETO if completo else MAX_LINEAS
    max_car = MAX_CARACTERES_COMPLETO if completo else MAX_CARACTERES
    t = str(texto or "")
    t = _RE_URL.sub(" un enlace ", t)
    t = _RE_RUTA.sub(lambda m: " " + (re.split(r"[\\/]", m.group(0).rstrip("\\/"))[-1] or "esa carpeta") + " ", t)
    t = _RE_EMOJI.sub(" ", t)
    t = t.replace("%", " por ciento")
    t = re.sub(r"[*`#>|]+", " ", t)
    t = t.replace("_", " ").replace("·", ",").replace("—", ",").replace("…", "...")

    lineas = [re.sub(r"\s+", " ", ln).strip(" -•\t") for ln in t.splitlines()]
    lineas = [ln for ln in lineas if ln]
    recortado = len(lineas) > max_lineas
    lineas = [ln.rstrip(".:;,") for ln in lineas[:max_lineas]]
    salida = ". ".join(lineas)

    if len(salida) > max_car:
        corte = salida[:max_car]
        limite = max(corte.rfind(". "), corte.rfind(", "), corte.rfind(" "))
        salida = corte[:limite if limite > 60 else max_car].rstrip(" ,.;:")
        recortado = True
    if recortado and salida and not completo:
        salida += ". El resto lo puedes ver en pantalla."
    return re.sub(r"\s+", " ", salida).strip()


# ─── PERSONALIDAD (muletillas pre-generadas) ──────────────────────────────

MULETILLAS_ACCION = ["Claro, enseguida.", "Perfecto.", "Listo, me encargo."]
MULETILLAS_INFO = ["Claro, aquí tienes.", "emm. déjame ver.", "Esto es lo que encontré."]

_PALABRAS_ACCION = {"abriendo", "ejecutando", "creado", "creada", "copiado", "movido", "cerrando", "comprimiendo"}
_PALABRAS_INFO = {"cpu", "ram", "ip", "batería", "bateria", "disco", "memoria", "uso"}
_INICIOS_CORTESES = ("claro", "listo", "perfecto", "mm", "lo siento", "no ")


def _prefijo(texto: str, exito: bool) -> str:
    """Muletilla a anteponer (o ''). Solo frases cortas y solo si la acción salió bien."""
    if not exito or len(texto) > 120 or texto.lower().startswith(_INICIOS_CORTESES):
        return ""
    palabras = set(re.findall(r"\w+", texto.lower()))
    if palabras & _PALABRAS_ACCION:
        return random.choice(MULETILLAS_ACCION)
    if palabras & _PALABRAS_INFO:
        return random.choice(MULETILLAS_INFO)
    return ""


def _trocear(texto: str) -> List[str]:
    """Frases largas → 2 trozos que se sintetizan a la vez (el 1.º empieza a sonar antes)."""
    if len(texto) < 90:
        return [texto]
    partes = re.split(r"(?<=[.!?])\s+", texto)
    if len(partes) < 2:        # una sola frase larga: corta en la 1.ª coma (40-120 car.) para empezar a sonar antes
        m = re.match(r"^(.{40,120}?[,;:])\s+(.+)$", texto, re.S)
        return [m.group(1), m.group(2)] if m else [texto]
    return [partes[0], " ".join(partes[1:])]


def _trocear_local(texto: str) -> List[str]:
    """Voces locales: trozos cortos (1.º = la 1.ª frase, máx. ~160 car.) para que empiece a sonar cuanto antes mientras
    el motor sintetiza el resto. Máx. 4 trozos (el modelo sintetiza de uno en uno; la reproducción solapa)."""
    if len(texto) < 70:
        return [texto]
    frases = [f for f in re.split(r"(?<=[.!?…])\s+", texto) if f]
    if len(frases) == 1 and len(texto) > 160:                      # una sola frase larga: corta en la 1.ª coma
        m = re.match(r"^(.{40,120}?[,;:])\s+(.+)$", texto, re.S)
        frases = [m.group(1), m.group(2)] if m else frases
    trozos: List[str] = []
    for f in frases:
        if trozos and len(trozos) > 1 and len(trozos[-1]) + len(f) < 220:
            trozos[-1] += " " + f
        elif trozos and len(trozos) == 1 and len(trozos[0]) < 60 and len(trozos[0]) + len(f) < 160:
            trozos[0] += " " + f                                   # evitar un 1.º trozo de 2 palabras
        else:
            trozos.append(f)
    while len(trozos) > 4:                                         # nunca más de 4 trozos: se unen los últimos
        trozos[-2:] = [trozos[-2] + " " + trozos[-1]]
    return trozos


# ─── SÍNTESIS (con caché) ─────────────────────────────────────────────────

_EDGE_HOST_NUEVO = "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
_edge_parchado = False


def _parchar_edge() -> str:
    """v3.7: apunta edge-tts al servidor nuevo de Microsoft (desde el 10 oct 2026 el viejo responde 403).
    Mismo arreglo que el PR #491 de rany2/edge-tts (aún sin publicar): solo cambia la URL del WebSocket y manda el mismo
    token como parámetro `Ocp-Apim-Subscription-Key`. Requiere edge-tts >= 7.3 (token Sec-MS-GEC v2 y fecha sin locale).
    No hace nada si la librería ya trae el host nuevo (cuando salga la versión oficial) o con SARA_EDGE_PARCHE=0.
    Devuelve una línea de diagnóstico. Nunca lanza excepciones."""
    global _edge_parchado
    if _edge_parchado:
        return "ya aplicado"
    _edge_parchado = True
    if os.getenv("SARA_EDGE_PARCHE", "1") == "0":
        return "desactivado (SARA_EDGE_PARCHE=0)"
    try:
        import edge_tts.communicate as _com
        import edge_tts.constants as _const
        if "msedgeservices" in _const.WSS_URL:
            return "la librería ya usa el host nuevo"
        nueva = f"{_EDGE_HOST_NUEVO}?Ocp-Apim-Subscription-Key={_const.TRUSTED_CLIENT_TOKEN}"
        _const.WSS_URL = nueva
        _com.WSS_URL = nueva                # communicate.py importó el nombre: hay que cambiarlo ahí también
        logger.info("🔧 Edge-TTS: usando el servidor nuevo de Microsoft (api.msedgeservices.com)")
        return "host nuevo aplicado"
    except Exception as e:
        logger.warning(f"⚠️ No pude aplicar el parche de Edge-TTS ({type(e).__name__}: {e})")
        return f"no se pudo aplicar ({type(e).__name__})"


async def _generar_async(texto: str, ruta: str, voz: str) -> None:
    import edge_tts
    _parchar_edge()
    await asyncio.wait_for(edge_tts.Communicate(texto, voz).save(ruta), timeout=TIMEOUT_SINTESIS_S)


_escrituras = 0


def _podar_cache(carpeta: Optional[Path] = None, maximo: Optional[int] = None) -> None:
    carpeta = carpeta or CACHE_DIR
    maximo = maximo or MAX_ARCHIVOS_CACHE
    try:
        archivos = sorted((p for p in carpeta.iterdir() if p.suffix in (".mp3", ".ogg", ".wav")),
                          key=lambda p: p.stat().st_mtime)
        for p in archivos[:-maximo]:
            p.unlink(missing_ok=True)
    except Exception:
        pass


def _piper_respaldo(voz_edge: str) -> Optional[str]:
    """Voz Piper YA DESCARGADA que sustituye a una neuronal de Edge caída: mismo género y, si se puede, misma región
    (Jorge → Ald de México; Álvaro → Davefx de España). None si Piper no está o no hay ninguna descargada."""
    try:
        listas = [v for v in _listar_locales() if v["instalada"]]
        if not listas:
            return None
        genero, cultura = _genero(voz_edge), _cultura(voz_edge).lower()
        for filtro in (lambda v: v["genero"] == genero and v["cultura"].lower() == cultura,
                       lambda v: v["genero"] == genero,
                       lambda v: v["cultura"].lower() == cultura,
                       lambda v: True):
            for v in listas:
                if filtro(v):
                    return v["id"]
    except Exception as e:
        logger.debug(f"_piper_respaldo: {e}")
    return None


def _obtener_audio_piper(texto: str, voz: str, carpeta: Optional[Path] = None) -> Optional[str]:
    """Ruta de un WAV con `texto` dicho por una voz Piper (de caché o recién sintetizado), o None si falla."""
    global _ultimo_error, _escrituras
    mod = _mod_local(voz)
    if mod is None or not mod.disponible():
        _ultimo_error = "Motor de voz local no disponible (pip install kokoro-onnx piper-tts)"
        return None
    etiq = voz.split(":", 1)[1]
    carpeta = carpeta or CACHE_DIR
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        final = carpeta / (hashlib.sha1(f"{voz}|{texto}".encode("utf-8")).hexdigest()[:24] + ".wav")
        if final.exists() and final.stat().st_size > 44:
            logger.info(f"🔊 voz[{etiq}]: '{texto[:40]}' → caché (0 ms)")
            return str(final)
        t0 = time.perf_counter()
        if not mod.sintetizar_wav(texto, voz, final):
            _ultimo_error = mod.motivo() or "El motor local no pudo sintetizar"
            return None
        logger.info(f"🔊 voz[{etiq}]: '{texto[:40]}' → síntesis local {int((time.perf_counter() - t0) * 1000)} ms")
        _escrituras += 1
        if _escrituras % 25 == 0:
            _podar_cache(carpeta, MAX_ARCHIVOS_CACHE_TG if carpeta == CACHE_TG else None)
        return str(final)
    except Exception as e:
        _ultimo_error = f"Piper: {type(e).__name__}: {str(e)[:160]}"
        logger.error(f"❌ {_ultimo_error}")
        return None


def _obtener_audio(texto: str, voz: str, carpeta: Optional[Path] = None) -> Optional[str]:
    """Ruta de un audio (.mp3 de Edge o .wav de Piper) con `texto`, de caché o recién sintetizado; None si falla.
    Orden: voz Piper elegida · voz de Edge elegida → (si Edge falla) Piper del mismo género/región · None → SAPI."""
    if _es_windows(voz):         # voz offline de Windows: no hay archivo que sintetizar (la dice _hablar_sapi)
        return None
    if _es_piper(voz):
        return _obtener_audio_piper(texto, voz, carpeta)
    ruta = _obtener_audio_edge(texto, voz, carpeta)
    if ruta:
        return ruta
    alt = _piper_respaldo(voz)
    if alt:
        ruta = _obtener_audio_piper(texto, alt, carpeta)
        if ruta:
            logger.info(f"🔁 Edge-TTS no responde: sonó la voz neuronal offline {alt.split(":", 1)[1]} en lugar de {voz.replace('Neural', '')}")
            return ruta
    return None


def _obtener_audio_edge(texto: str, voz: str, carpeta: Optional[Path] = None) -> Optional[str]:
    """Devuelve la ruta de un MP3 con `texto` (de caché o recién sintetizado por Edge-TTS), o None si falla."""
    global _ultimo_error, _escrituras, _edge_fallos, _edge_pausado_hasta, _edge_ultimo_ok, _edge_ultimo_error
    if _es_windows(voz):         # voz offline: no hay MP3 que sintetizar (la dice _hablar_sapi)
        return None
    carpeta = carpeta or CACHE_DIR
    etiqueta = voz.replace("Neural", "")
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        final = carpeta / (hashlib.sha1(f"{voz}|{texto}".encode("utf-8")).hexdigest()[:24] + ".mp3")
        if final.exists() and final.stat().st_size > 0:
            logger.info(f"🔊 voz[{etiqueta}]: '{texto[:40]}' → caché (0 ms)")
            return str(final)
        if edge_pausado():       # cortacircuitos: no se pierde tiempo llamando a un servicio que está fallando
            _ultimo_error = "Edge-TTS en pausa por fallos seguidos (403 del servicio de Microsoft)"
            return None
        t0 = time.perf_counter()
        tmp = final.with_suffix(f".{threading.get_ident()}.tmp")
        asyncio.run(_generar_async(texto, str(tmp), voz))
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise RuntimeError("Edge-TTS devolvió un archivo vacío")
        os.replace(tmp, final)
        if _edge_fallos:
            logger.info("✅ Edge-TTS volvió a responder")
        _edge_fallos = 0
        _edge_ultimo_ok = time.monotonic()
        _edge_ultimo_error = ""
        logger.info(f"🔊 voz[{etiqueta}]: '{texto[:40]}' → síntesis {int((time.perf_counter() - t0) * 1000)} ms")
        _escrituras += 1
        if _escrituras % 25 == 0:
            _podar_cache(carpeta, MAX_ARCHIVOS_CACHE_TG if carpeta == CACHE_TG else None)
        return str(final)
    except ImportError:
        _ultimo_error = "edge-tts no está instalado (pip install edge-tts)"
        _edge_ultimo_error = _ultimo_error
    except Exception as e:
        _ultimo_error = f"Edge-TTS: {type(e).__name__}: {str(e)[:160]}"
        _edge_ultimo_error = _ultimo_error
        _edge_fallos += 1
        if _edge_fallos >= EDGE_FALLOS_PARA_PAUSAR and not edge_pausado():
            _edge_pausado_hasta = time.monotonic() + EDGE_PAUSA_S
            logger.warning(f"⚠️ Edge-TTS falló {_edge_fallos} veces seguidas: pausa de {EDGE_PAUSA_S // 60} min "
                           f"(solo suena lo que ya está en caché; el resto, con la voz de Windows). "
                           f"Se reintenta solo al terminar la pausa.")
    logger.error(f"❌ {_ultimo_error}")
    return None


_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="sara-tts-syn")
_precalentado = False


def precalentar() -> None:
    """Genera muletillas y precarga la voz local cuando se solicita expresamente."""
    global _precalentado
    if _precalentado or not _activa or _es_windows(_voz_actual):
        return
    _precalentado = True
    voz_pre = _voz_actual
    try:                                    # carga el modelo local YA (la 1.ª frase y la 1.ª nota de Telegram no pagan el arranque)
        voces_locales = {voz_pre}
        respaldo = _piper_respaldo(voz_pre) if _es_edge(voz_pre) else None
        if respaldo and getattr(_config, "PIPER_PRECALENTAR_RESPALDO", False):
            voces_locales.add(respaldo)
        for v in voces_locales:
            if v and _es_piper(v) and _mod_local(v) is not None:
                _mod_local(v).precargar(v)
    except Exception as e:
        logger.debug(f"precalentar: {e}")

    def _run():
        for frase in MULETILLAS_ACCION + MULETILLAS_INFO:
            _obtener_audio(frase, voz_pre)
    threading.Thread(target=_run, name="sara-tts-warm", daemon=True).start()


# ─── REPRODUCCIÓN ─────────────────────────────────────────────────────────

_ALIAS_MCI = "sara_voz"


def _mci(comando: str) -> str:
    import ctypes
    winmm = ctypes.windll.winmm
    buf = ctypes.create_unicode_buffer(255)
    err = winmm.mciSendStringW(comando, buf, 254, 0)
    if err:
        msg = ctypes.create_unicode_buffer(255)
        winmm.mciGetErrorStringW(err, msg, 254)
        raise RuntimeError(f"MCI '{comando}': {msg.value}")
    return buf.value


def _reproducir_mp3(ruta: str) -> bool:
    """Reproduce un MP3 (bloquea hasta que termina o hasta detener())."""
    global _ultimo_error
    if os.name != "nt":
        _ultimo_error = "La reproducción nativa solo existe en Windows"
        return False
    try:
        try:
            _mci(f"close {_ALIAS_MCI}")
        except Exception:
            pass
        _mci(f'open "{ruta}" type {_tipo_mci(ruta)} alias {_ALIAS_MCI}')
        try:
            _mci(f"play {_ALIAS_MCI} wait")
        finally:
            try:
                _mci(f"close {_ALIAS_MCI}")
            except Exception:
                pass
        return True
    except Exception as e:
        _ultimo_error = f"Reproducción: {e}"
        logger.error(f"❌ {_ultimo_error}")
        return False


def _hablar_sapi(texto: str, genero: str = "NotSet", nombre: Optional[str] = None,
                 wav: Optional[str] = None) -> bool:
    """Voz offline de Windows. `nombre` = voz concreta instalada; si no, `genero` ('Male' | 'Female' | 'NotSet') acerca
    la elegida del menú. Con `wav` NO suena: guarda el audio en ese archivo (para las notas de voz)."""
    global _ultimo_error
    if os.name != "nt":
        return False
    ps = ("Add-Type -AssemblyName System.Speech; "
          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $ok = $false; "
          "$n = $env:SARA_TTS_VOZ; if ($n) { try { $s.SelectVoice($n); $ok = $true } catch {} }; "
          "if (-not $ok) { $g = $env:SARA_TTS_GENERO; if (-not $g) { $g = 'NotSet' }; "
          "foreach ($c in 'es-MX','es-ES') { try { $s.SelectVoiceByHints($g,'NotSet',0,[cultureinfo]$c); $ok = $true; break } catch {} }; "
          "if (-not $ok) { try { $s.SelectVoiceByHints('NotSet','NotSet',0,[cultureinfo]'es-MX') } catch {} } }; "
          "$w = $env:SARA_TTS_WAV; if ($w) { $s.SetOutputToWaveFile($w) }; "
          "$s.Speak($env:SARA_TTS_TEXT); $s.Dispose()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       env=dict(os.environ, SARA_TTS_TEXT=texto, SARA_TTS_GENERO=genero,
                                SARA_TTS_VOZ=nombre or "", SARA_TTS_WAV=wav or ""),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=CREATE_NO_WINDOW, timeout=60)
        if wav:
            ok = os.path.exists(wav) and os.path.getsize(wav) > 44          # 44 bytes = solo la cabecera WAV
            if not ok:
                _ultimo_error = "SAPI no generó el WAV"
                logger.error(f"❌ {_ultimo_error}")
            return ok
        return True
    except Exception as e:
        _ultimo_error = f"SAPI: {type(e).__name__}: {e}"
        logger.error(f"❌ {_ultimo_error}")
        return False


def _wav_a_opus(wav: str, ogg: str) -> bool:
    """WAV → OGG/Opus mono 48 kHz con PyAV (la nota de voz de Telegram). False si algo falla (el WAV se conserva)."""
    try:
        import av
    except Exception as e:
        logger.warning(f"⚠️ Sin PyAV no puedo pasar la voz de Windows a OGG/Opus ({e}); mando el WAV")
        return False

    def _lista(x):
        return x if isinstance(x, list) else ([] if x is None else [x])

    try:
        with av.open(wav) as ent, av.open(ogg, "w", format="ogg") as sal:
            s_ent = ent.streams.audio[0]
            s_sal = sal.add_stream("libopus", rate=48000)
            capa = "mono"
            try:
                s_sal.layout = "mono"
            except Exception:
                capa = "stereo"          # si esta versión de PyAV no deja fijar la capa, se codifica en estéreo
            rs = av.AudioResampler(format="s16", layout=capa, rate=48000)

            def _volcar(frames):
                for fr in _lista(frames):
                    for pkt in s_sal.encode(fr):
                        sal.mux(pkt)

            for fr in ent.decode(s_ent):
                _volcar(rs.resample(fr))
            _volcar(rs.resample(None))
            for pkt in s_sal.encode(None):
                sal.mux(pkt)
        return os.path.exists(ogg) and os.path.getsize(ogg) > 0
    except Exception as e:
        logger.warning(f"⚠️ No pude convertir a OGG/Opus ({type(e).__name__}: {e}); mando el WAV")
        try:
            os.remove(ogg)
        except OSError:
            pass
        return False


def _nota_windows(texto: str, voz: str) -> Optional[str]:
    """Texto → archivo de audio con la voz de Windows (OGG/Opus; WAV si no se pudo convertir). Con caché propia."""
    nombre = _sapi_para(voz)
    genero = _genero_sapi(voz)
    CACHE_TG.mkdir(parents=True, exist_ok=True)
    base = CACHE_TG / hashlib.sha1(f"win|{nombre or genero}|{texto}".encode("utf-8")).hexdigest()[:24]
    wav, ogg = base.with_suffix(".wav"), base.with_suffix(".ogg")
    if ogg.exists() and ogg.stat().st_size > 0:
        return str(ogg)
    if not (wav.exists() and wav.stat().st_size > 44):
        t0 = time.perf_counter()
        if not _hablar_sapi(texto, genero, nombre, str(wav)):
            return None
        logger.info(f"🔊 voz[Windows {nombre or genero}]: '{texto[:40]}' → WAV {int((time.perf_counter() - t0) * 1000)} ms")
    resultado = str(ogg) if _wav_a_opus(str(wav), str(ogg)) else str(wav)
    if resultado.endswith(".ogg"):
        wav.unlink(missing_ok=True)
    _podar_cache(CACHE_TG, MAX_ARCHIVOS_CACHE_TG)
    return resultado


# ─── COLA + HILO REPRODUCTOR ──────────────────────────────────────────────

_cola: "queue.Queue" = queue.Queue()
_hilo: Optional[threading.Thread] = None
_lock = threading.Lock()


def _emit_seguro(emit: Optional[Callable], tipo: str, **datos) -> None:
    if emit is None:
        return
    try:
        emit(tipo, **datos)
    except Exception as e:
        logger.debug(f"emit({tipo}) falló: {e}")


def _decir(segmentos: List[str], futuros: list, emit: Optional[Callable], voz: Optional[str] = None) -> None:
    voz = voz or _voz_actual
    _emit_seguro(emit, "voz_estado", hablando=True)
    try:
        for texto, fut in zip(segmentos, futuros):
            try:
                ruta = fut.result(timeout=TIMEOUT_SINTESIS_S + 5)
            except Exception:
                ruta = None
            if not (ruta and _reproducir_mp3(ruta)):
                nombre = _sapi_para(voz)
                if not _es_windows(voz):
                    logger.warning(f"⚠️ Voz neuronal {voz} no disponible ({_ultimo_error}); "
                                   f"uso la voz de Windows ({nombre or _genero_sapi(voz)})")
                _hablar_sapi(texto, _genero_sapi(voz), nombre)
    finally:
        if _cola.empty():
            _emit_seguro(emit, "voz_estado", hablando=False)


def _trabajador() -> None:
    while True:
        segmentos, futuros, emit, voz = _cola.get()
        try:
            if _activa:
                _decir(segmentos, futuros, emit, voz)
        except Exception:
            logger.exception("❌ Excepción en el hilo de voz")


def _asegurar_hilo() -> None:
    global _hilo
    with _lock:
        if _hilo is None or not _hilo.is_alive():
            _hilo = threading.Thread(target=_trabajador, name="sara-tts-worker", daemon=True)
            _hilo.start()


# ─── API PÚBLICA ──────────────────────────────────────────────────────────

def hablar(texto: str, voz: Optional[str] = None, emit: Optional[Callable] = None,
           exito: bool = True, interrumpir: bool = False, forzar: bool = False,
           muletilla: bool = True) -> None:
    """Encola `texto`. Retorna al instante; la síntesis arranca YA, en paralelo."""
    try:
        if not (_activa or forzar):
            return
        limpio = limpiar_para_voz(texto)
        if not limpio:
            return
        voz = voz or _voz_actual
        edge_caido = _es_edge(voz) and edge_pausado()
        local = _es_piper(voz) or (edge_caido and _piper_respaldo(voz) is not None)   # suena con un motor local
        if _es_windows(voz) or (edge_caido and not local):
            segmentos = [limpio]            # voz de Windows: una sola llamada (cada una arranca PowerShell) y sin muletilla
        else:
            pref = _prefijo(limpio, exito) if muletilla else ""      # charla: sin "Claro, aquí tienes."
            segmentos = ([pref] if pref else []) + (_trocear_local(limpio) if local else _trocear(limpio))
        if interrumpir:
            detener()
        _asegurar_hilo()
        futuros = [_pool.submit(_obtener_audio, s, voz) for s in segmentos]
        while _cola.qsize() >= MAX_COLA:
            try:
                _cola.get_nowait()
            except queue.Empty:
                break
        _cola.put((segmentos, futuros, emit, voz))
    except Exception as e:
        logger.error(f"❌ hablar() falló: {e}", exc_info=True)

def _es_edge(voz_id: str) -> bool:
    """True si la voz es de Edge-TTS (no es de Windows ni de un motor local)."""
    return not (_es_windows(voz_id) or _es_piper(voz_id))


def _como_nota(ruta: str) -> str:
    """Un WAV de Piper → OGG/Opus (lo que Telegram acepta como nota de voz). Si no se puede convertir, el mismo WAV."""
    if not str(ruta).lower().endswith(".wav"):
        return ruta
    ogg = str(Path(ruta).with_suffix(".ogg"))
    if os.path.exists(ogg) and os.path.getsize(ogg) > 0:
        return ogg
    return ogg if _wav_a_opus(ruta, ogg) else ruta


def sintetizar(texto: str, voz: Optional[str] = None, completo: bool = False) -> Optional[str]:
    """Texto → ruta de un archivo de audio (.mp3 neuronal, .ogg o .wav de Windows), SIN reproducirlo. Bloquea mientras
    sintetiza. Lo usa telegram_bot.py para las notas de voz. completo=True: sin el recorte de la voz del PC.
    Orden: voz neuronal (si la elegida lo es y Edge responde) → voz de Windows. None solo si ambas fallan."""
    try:
        limpio = limpiar_para_voz(texto, completo=completo)
        if not limpio:
            return None
        v = voz or _voz_actual
        if not _es_windows(v):
            ruta = _obtener_audio(limpio, v, CACHE_TG if completo else None)
            if ruta:
                return _como_nota(ruta)
            logger.info(f"🔊 sintetizar: {v} no disponible → voz de Windows")
        return _nota_windows(limpio, v)
    except Exception as e:
        logger.error(f"❌ sintetizar() falló: {e}")
        return None


def reintentar_edge() -> bool:
    """Quita la pausa del cortacircuitos y prueba Edge-TTS YA con una frase nueva (nunca en caché). True si respondió."""
    global _edge_fallos, _edge_pausado_hasta
    _edge_fallos = 0
    _edge_pausado_hasta = 0.0
    voz = _voz_actual if _es_edge(_voz_actual) else VOZ_BASE
    if not _es_edge(voz):
        voz = "es-MX-DaliaNeural"
    ruta = _obtener_audio_edge(f"Prueba {int(time.time())}", voz, CACHE_TG)      # solo Edge: sin respaldo Piper
    if ruta:
        try:
            os.remove(ruta)
        except OSError:
            pass
    return bool(ruta)


def detener() -> None:
    while True:
        try:
            _cola.get_nowait()
        except queue.Empty:
            break
    if os.name == "nt":
        try:
            _mci(f"stop {_ALIAS_MCI}")
        except Exception:
            pass


def silenciar() -> None:
    global _activa
    _activa = False
    detener()


def activar() -> None:
    global _activa
    _activa = True


def esta_activa() -> bool:
    return _activa


def _estado_locales() -> str:
    partes = []
    motores = ((("Kokoro", voz_kokoro, "kokoro-onnx"),) if USAR_KOKORO else ()) + (("Piper", voz_piper, "piper-tts"),)
    for nombre, mod, pip in motores:
        if mod is None or not mod.disponible():
            partes.append(f"{nombre}: NO instalado → pip install {pip}")
        elif not any(v["instalada"] for v in mod.listar()):
            partes.append(f"{nombre}: motor listo, SIN descargar (elige una voz en el menú o: python voz_{nombre.lower()}.py --instalar)")
        else:
            partes.append(f"{nombre}: listo")
    return " · ".join(partes)


def probar() -> str:
    edge, red = "desactivado (SARA_USAR_EDGE=0)", "no se usan"
    if USAR_EDGE:
        try:
            import edge_tts  # noqa: F401
            edge = "instalado"
        except ImportError:
            edge = "NO instalado → pip install edge-tts"
        try:
            socket.create_connection(("speech.platform.bing.com", 443), timeout=3).close()
            red = "OK"
        except OSError:
            red = "SIN conexión (usaré la voz offline de Windows)"
    hablar("Hola, soy Sara. Si me escuchas, mi voz funciona correctamente.", forzar=True)
    try:
        en_cache = len(list(CACHE_DIR.glob("*.mp3")))
    except Exception:
        en_cache = 0
    return (f"Diagnóstico de voz:\n"
            f"Voz activa: {'sí' if _activa else 'no (usa /voz on)'}\n"
            f"Sistema: {'Windows' if os.name == 'nt' else os.name}\n"
            f"edge-tts: {edge}\n"
            f"Servidores de voz: {red}\n"
            f"Voz: {_voz_actual}\n"
            f"Voces locales (sin internet): {_estado_locales()}\n"
            f"Edge-TTS: {'EN PAUSA (falla el servicio; suena ' + (nombre_que_suena() or 'la voz de Windows ' + _genero_sapi(_voz_actual)) + ')' if edge_pausado() else estado_edge()['estado']}\n"
            f"Voces de Windows en español: {', '.join(_nombre_corto(v['name']) for v in _cargar_sapi()) or 'ninguna detectada'}\n"
            f"Frases en caché: {en_cache}\n"
            f"Último error: {_ultimo_error or 'ninguno'}")
