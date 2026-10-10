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
# API: hablar(texto, voz=None, emit=None, exito=True, interrumpir=False, forzar=False, muletilla=True)
#      detener() · silenciar() · activar() · esta_activa() · probar() · precalentar()
#      get_voz() · set_voz(id) · listar_voces()

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

logger = logging.getLogger("SARA-VOZ")

VOZ_BASE = os.getenv("SARA_VOZ", "es-MX-DaliaNeural")      # voz por defecto si el usuario no eligió otra
VOZ_SAPI = "sapi"                                           # voz offline de Windows (System.Speech)
CONFIG_VOZ = Path(__file__).resolve().parent / "voz_config.json"

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


def _cargar_voz_guardada() -> str:
    try:
        datos = json.loads(CONFIG_VOZ.read_text(encoding="utf-8"))
        v = str(datos.get("voz", ""))
        if v in _IDS_VOCES:
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
    return [dict(v) for v in VOCES]


def set_voz(voz_id: str) -> bool:
    """Cambia la voz activa y la guarda. False si el id no está en la lista cerrada."""
    global _voz_actual, _precalentado
    if voz_id not in _IDS_VOCES:
        logger.warning(f"⚠️ set_voz: voz no permitida {voz_id!r}")
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

_activa = os.getenv("SARA_VOZ_ACTIVA", "1") != "0"
_ultimo_error = ""

# ─── LIMPIEZA DEL TEXTO ───────────────────────────────────────────────────

_RE_URL = re.compile(r"https?://\S+")
_RE_RUTA = re.compile(r"[A-Za-z]:\\(?:[^\s\\/:*?\"<>|]+[\\/]?)+")
_RE_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U0001F1E6-\U0001F1FF\u2190-\u21FF\u2300-\u23FF"
    "\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d]+"
)


def limpiar_para_voz(texto: str) -> str:
    t = str(texto or "")
    t = _RE_URL.sub(" un enlace ", t)
    t = _RE_RUTA.sub(lambda m: " " + (re.split(r"[\\/]", m.group(0).rstrip("\\/"))[-1] or "esa carpeta") + " ", t)
    t = _RE_EMOJI.sub(" ", t)
    t = t.replace("%", " por ciento")
    t = re.sub(r"[*`#>|]+", " ", t)
    t = t.replace("_", " ").replace("·", ",").replace("—", ",").replace("…", "...")

    lineas = [re.sub(r"\s+", " ", ln).strip(" -•\t") for ln in t.splitlines()]
    lineas = [ln for ln in lineas if ln]
    recortado = len(lineas) > MAX_LINEAS
    lineas = [ln.rstrip(".:;,") for ln in lineas[:MAX_LINEAS]]
    salida = ". ".join(lineas)

    if len(salida) > MAX_CARACTERES:
        corte = salida[:MAX_CARACTERES]
        limite = max(corte.rfind(". "), corte.rfind(", "), corte.rfind(" "))
        salida = corte[:limite if limite > 60 else MAX_CARACTERES].rstrip(" ,.;:")
        recortado = True
    if recortado and salida:
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


# ─── SÍNTESIS (con caché) ─────────────────────────────────────────────────

async def _generar_async(texto: str, ruta: str, voz: str) -> None:
    import edge_tts
    await asyncio.wait_for(edge_tts.Communicate(texto, voz).save(ruta), timeout=TIMEOUT_SINTESIS_S)


_escrituras = 0


def _podar_cache() -> None:
    try:
        archivos = sorted(CACHE_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        for p in archivos[:-MAX_ARCHIVOS_CACHE]:
            p.unlink(missing_ok=True)
    except Exception:
        pass


def _obtener_audio(texto: str, voz: str) -> Optional[str]:
    """Devuelve la ruta de un MP3 con `texto` (de caché o recién sintetizado), o None si falla."""
    global _ultimo_error, _escrituras
    if voz == VOZ_SAPI:          # voz offline: no hay MP3 que sintetizar (la dice _hablar_sapi)
        return None
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        final = CACHE_DIR / (hashlib.sha1(f"{voz}|{texto}".encode("utf-8")).hexdigest()[:24] + ".mp3")
        if final.exists() and final.stat().st_size > 0:
            logger.info(f"🔊 voz: '{texto[:40]}' → caché (0 ms)")
            return str(final)
        t0 = time.perf_counter()
        tmp = final.with_suffix(f".{threading.get_ident()}.tmp")
        asyncio.run(_generar_async(texto, str(tmp), voz))
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise RuntimeError("Edge-TTS devolvió un archivo vacío")
        os.replace(tmp, final)
        logger.info(f"🔊 voz: '{texto[:40]}' → síntesis {int((time.perf_counter() - t0) * 1000)} ms")
        _escrituras += 1
        if _escrituras % 25 == 0:
            _podar_cache()
        return str(final)
    except ImportError:
        _ultimo_error = "edge-tts no está instalado (pip install edge-tts)"
    except Exception as e:
        _ultimo_error = f"Edge-TTS: {type(e).__name__}: {e}"
    logger.error(f"❌ {_ultimo_error}")
    return None


_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="sara-tts-syn")
_precalentado = False


def precalentar() -> None:
    """Genera en segundo plano las muletillas (llamar una vez al arrancar SARA)."""
    global _precalentado
    if _precalentado or not _activa or _voz_actual == VOZ_SAPI:
        return
    _precalentado = True
    voz_pre = _voz_actual

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
        _mci(f'open "{ruta}" type mpegvideo alias {_ALIAS_MCI}')
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


def _hablar_sapi(texto: str) -> bool:
    global _ultimo_error
    if os.name != "nt":
        return False
    ps = ("Add-Type -AssemblyName System.Speech; "
          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          "try { $s.SelectVoiceByHints('NotSet','NotSet',0,[cultureinfo]'es-MX') } catch { "
          "try { $s.SelectVoiceByHints('NotSet','NotSet',0,[cultureinfo]'es-ES') } catch {} }; "
          "$s.Speak($env:SARA_TTS_TEXT)")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       env=dict(os.environ, SARA_TTS_TEXT=texto),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=CREATE_NO_WINDOW, timeout=60)
        return True
    except Exception as e:
        _ultimo_error = f"SAPI: {type(e).__name__}: {e}"
        logger.error(f"❌ {_ultimo_error}")
        return False


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


def _decir(segmentos: List[str], futuros: list, emit: Optional[Callable]) -> None:
    _emit_seguro(emit, "voz_estado", hablando=True)
    try:
        for texto, fut in zip(segmentos, futuros):
            try:
                ruta = fut.result(timeout=TIMEOUT_SINTESIS_S + 5)
            except Exception:
                ruta = None
            if not (ruta and _reproducir_mp3(ruta)):
                if _voz_actual != VOZ_SAPI:
                    logger.warning(f"⚠️ Voz neuronal no disponible ({_ultimo_error}); uso SAPI offline")
                _hablar_sapi(texto)
    finally:
        if _cola.empty():
            _emit_seguro(emit, "voz_estado", hablando=False)


def _trabajador() -> None:
    while True:
        segmentos, futuros, emit = _cola.get()
        try:
            if _activa:
                _decir(segmentos, futuros, emit)
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
        pref = _prefijo(limpio, exito) if muletilla else ""      # charla: sin "Claro, aquí tienes."
        segmentos = ([pref] if pref else []) + _trocear(limpio)
        if interrumpir:
            detener()
        _asegurar_hilo()
        futuros = [_pool.submit(_obtener_audio, s, voz) for s in segmentos]
        while _cola.qsize() >= MAX_COLA:
            try:
                _cola.get_nowait()
            except queue.Empty:
                break
        _cola.put((segmentos, futuros, emit))
    except Exception as e:
        logger.error(f"❌ hablar() falló: {e}", exc_info=True)

def sintetizar(texto: str, voz: Optional[str] = None) -> Optional[str]:
    """Texto → ruta de un MP3 (caché o recién sintetizado), SIN reproducirlo. Bloquea mientras sintetiza.
    Lo usa telegram_bot.py para mandar la respuesta como nota de voz. None si falla o no hay nada que decir."""
    try:
        limpio = limpiar_para_voz(texto)
        v = voz or _voz_actual
        if v == VOZ_SAPI:               # una nota de voz (Telegram) necesita MP3: usa la voz neuronal base
            v = VOZ_BASE if VOZ_BASE != VOZ_SAPI else "es-MX-DaliaNeural"
        return _obtener_audio(limpio, v) if limpio else None
    except Exception as e:
        logger.error(f"❌ sintetizar() falló: {e}")
        return None


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


def probar() -> str:
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
            f"Frases en caché: {en_cache}\n"
            f"Último error: {_ultimo_error or 'ninguno'}")
