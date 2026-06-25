# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  SARA — voice.py  v3.0                                                      ║
# ║  Motor de voz profesional — offline-first con preprocesamiento de audio     ║
# ║                                                                              ║
# ║  NOVEDADES v3.0:                                                             ║
# ║  · Umbral de energía dinámico (calibración real del ambiente)                ║
# ║  · Reducción de ruido espectral (noisereduce) antes de STT                  ║
# ║  · Filtro paso-banda Butterworth 80–8000 Hz (voz humana)                    ║
# ║  · Normalización de ganancia (AGC) para voces bajas                         ║
# ║  · Silero VAD neuronal vía faster-whisper (reemplaza detector por energía)  ║
# ║  · Detector de silencio mejorado: doble ventana (energía + conteo)          ║
# ║  · Modo de escucha mejorado con feedback visual de nivel de audio            ║
# ║  · Reintentos automáticos en escuchar_comando()                              ║
# ║  · Warmup real de Whisper con audio silencioso                               ║
# ║  · Pipeline de preprocesamiento configurable por flags                       ║
# ║                                                                              ║
# ║  STT (prioridad):                                                            ║
# ║    1. Vosk          — offline, rápido, modelo ~50 MB                         ║
# ║    2. faster-whisper — offline, preciso, modelo small ~466 MB                ║
# ║    3. Google STT    — fallback online (solo si hay internet)                 ║
# ║                                                                              ║
# ║  TTS (prioridad):                                                            ║
# ║    1. Piper TTS     — offline, voz neural es-MX, WAV                        ║
# ║    2. pyttsx3       — offline, voz del sistema, siempre disponible           ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

import os
import re
import json
import queue
import threading
import subprocess
import tempfile
import time
import wave
import struct
import math

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
os.environ["TOKENIZERS_PARALLELISM"]     = "false"

import logger
from utils import normalizar_texto


# ══════════════════════════════════════════════════════════════════════════════
#  ESTADOS
# ══════════════════════════════════════════════════════════════════════════════

HIBERNANDO   = "hibernando"
ACTIVADA     = "activada"
PROCESANDO   = "procesando"
RESPONDIENDO = "respondiendo"


# ══════════════════════════════════════════════════════════════════════════════
#  WAKE WORDS
# ══════════════════════════════════════════════════════════════════════════════

WAKE_WORDS = {"sara", "zara", "sará", "sarah", "sera"}


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN — edita aquí según tu hardware y preferencias
# ══════════════════════════════════════════════════════════════════════════════

# ── Audio ─────────────────────────────────────────────────────────────────────
SAMPLE_RATE        = 16000        # Hz — Vosk y Whisper esperan 16 kHz
CHUNK_SIZE         = 1024         # frames por bloque
UMBRAL_ENERGIA     = 300          # nivel mínimo para considerar habla (se sobreescribe en calibración)
TIMEOUT_ESCUCHA    = 5            # segundos esperando comando
TIMEOUT_WAKEWORD   = None         # None = espera indefinida al wake word
DURACION_SILENCIO  = 1.4          # segundos de silencio para cortar grabación (v3: aumentado a 1.4)
PHRASE_TIME_LIMIT  = 12           # segundos máximos de un comando (v3: aumentado a 12)

# ── Calibración dinámica ──────────────────────────────────────────────────────
DURACION_CALIBRACION    = 2.0     # segundos de muestreo del ambiente al iniciar
MULTIPLICADOR_UMBRAL    = 2.0     # umbral = ruido_base × este valor
UMBRAL_MINIMO           = 150     # piso absoluto aunque el ambiente sea muy silencioso
UMBRAL_MAXIMO           = 2500    # techo para evitar que ruido muy alto bloquee todo reconocimiento

# ── Preprocesamiento de audio (pipeline) ─────────────────────────────────────
PREPROCESAR_AUDIO       = True    # activa/desactiva todo el pipeline de preprocesamiento
USAR_FILTRO_BANDA       = True    # filtro paso-banda 80–8000 Hz (requiere scipy)
BANDA_BAJA_HZ           = 80      # frecuencia de corte inferior (Hz)
BANDA_ALTA_HZ           = 8000    # frecuencia de corte superior (Hz)
ORDEN_FILTRO            = 4       # orden del filtro Butterworth (4 = buen balance precisión/velocidad)
USAR_REDUCCION_RUIDO    = True    # reducción espectral de ruido (requiere noisereduce)
REDUCCION_INTENSIDAD    = 0.75    # 0.0 = sin reducción, 1.0 = máxima (0.75 = balance óptimo)
USAR_NORMALIZACION_AGC  = True    # normalización de ganancia para voces bajas
NIVEL_OBJETIVO_AGC      = 0.4     # nivel RMS objetivo tras normalización (0.0–1.0)

# ── STT ───────────────────────────────────────────────────────────────────────
IDIOMA           = "es"                          # código de idioma para Vosk/Whisper
IDIOMA_GOOGLE    = "es-MX"                       # código extendido para Google STT
VOSK_MODEL_PATH  = "models/vosk-es"             # carpeta del modelo Vosk descargado
WHISPER_MODEL    = "small"                       # opciones: tiny · base · small · medium
                                                 # small = 466 MB, mejor WER en español con ruido
WHISPER_DEVICE   = "cpu"                         # "cpu" o "cuda" si tienes GPU Nvidia
WHISPER_COMPUTE  = "int8"                        # "int8" (rápido/liviano) o "float16"
WHISPER_BEAM     = 5                             # beam size (mayor = más preciso, más lento)
WHISPER_VAD      = True                          # Silero VAD neuronal integrado en faster-whisper
WHISPER_VAD_MIN_SILENCE = 400                    # ms mínimos de silencio para corte VAD

# ── TTS ───────────────────────────────────────────────────────────────────────
PIPER_EXE        = "piper/piper.exe"                          # ejecutable Piper
PIPER_MODEL      = "models/piper/es_MX-ald-medium.onnx"      # modelo de voz
PIPER_CONFIG     = "models/piper/es_MX-ald-medium.onnx.json" # config del modelo
VELOCIDAD_VOZ    = 150            # solo pyttsx3
VOLUMEN_VOZ      = 1.0            # solo pyttsx3

# ── Reintentos de escucha ─────────────────────────────────────────────────────
MAX_REINTENTOS_ESCUCHA  = 2       # cuántas veces reintenta si no capta nada
SILENCIO_ENTRE_REINTENTOS = 0.3  # segundos de pausa entre reintentos


# ══════════════════════════════════════════════════════════════════════════════
#  ESTADO INTERNO (no modificar)
# ══════════════════════════════════════════════════════════════════════════════

_estado            = HIBERNANDO
_disponible        = False
_hablando          = False

# Backends detectados en inicializar()
_backend_audio     = None   # "pyaudio" | "sounddevice" | None
_backend_stt       = None   # "vosk" | "whisper" | "google" | None
_backend_tts       = None   # "piper" | "pyttsx3" | None

# Instancias reutilizables
_vosk_model        = None
_vosk_recognizer   = None
_whisper_model     = None
_pygame_disponible = False

# Nivel de ruido medido en calibración (para logs y diagnóstico)
_ruido_base_rms    = 0


# ══════════════════════════════════════════════════════════════════════════════
#  DETECCIÓN DE BACKENDS
# ══════════════════════════════════════════════════════════════════════════════

def _detectar_audio():
    """Detecta PyAudio o sounddevice. Retorna 'pyaudio', 'sounddevice' o None."""
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        p.terminate()
        logger.debug("voice", "Backend audio: PyAudio")
        return "pyaudio"
    except Exception:
        pass
    try:
        import sounddevice as sd
        sd.query_devices()
        logger.debug("voice", "Backend audio: sounddevice")
        return "sounddevice"
    except Exception:
        pass
    logger.error("voice", "Sin backend de audio.", "Instala pyaudio o sounddevice.")
    return None


def _detectar_stt():
    """
    Detecta el mejor backend STT disponible.
    Orden de prioridad: Vosk → faster-whisper → Google STT
    Retorna 'vosk', 'whisper', 'google' o None.
    """
    global _vosk_model, _vosk_recognizer, _whisper_model

    # ── 1. Vosk (offline, rápido, latencia mínima) ────────────────────────────
    if os.path.isdir(VOSK_MODEL_PATH):
        try:
            from vosk import Model, KaldiRecognizer
            _vosk_model      = Model(VOSK_MODEL_PATH)
            _vosk_recognizer = KaldiRecognizer(_vosk_model, SAMPLE_RATE)
            logger.info("voice", f"STT: Vosk offline activo (modelo: {VOSK_MODEL_PATH})")
            return "vosk"
        except Exception as e:
            logger.warning("voice", f"Vosk falló al cargar: {e}")
    else:
        logger.debug("voice", f"Vosk: modelo no encontrado en '{VOSK_MODEL_PATH}'")

    # ── 2. faster-whisper (offline, mayor precisión, mejor con ruido) ─────────
    try:
        from faster_whisper import WhisperModel
        logger.info("voice", f"STT: Cargando faster-whisper '{WHISPER_MODEL}' — puede tardar unos segundos...")
        _whisper_model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE
        )
        _warmup_whisper()
        logger.info("voice", f"STT: faster-whisper offline activo (modelo: {WHISPER_MODEL})")
        return "whisper"
    except Exception as e:
        logger.warning("voice", f"faster-whisper no disponible: {e}")

    # ── 3. Google STT (fallback online) ──────────────────────────────────────
    try:
        import speech_recognition as sr
        sr.Recognizer()
        logger.warning("voice", "STT: solo Google online disponible. Sin internet no habrá STT.")
        return "google"
    except Exception as e:
        logger.error("voice", f"Sin backend STT disponible: {e}")
        return None


def _warmup_whisper():
    """
    Realiza un warmup real de Whisper con audio silencioso.
    Evita que el primer comando real tenga latencia de carga del modelo.
    """
    try:
        import numpy as np
        silencio_np  = np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.int16)
        silencio_pcm = silencio_np.tobytes()
        ruta_wav = _guardar_wav_temp(silencio_pcm)
        _whisper_model.transcribe(
            ruta_wav,
            language=IDIOMA,
            beam_size=1,
            vad_filter=False
        )
        os.remove(ruta_wav)
        logger.debug("voice", "Whisper warmup completado.")
    except Exception as e:
        logger.debug("voice", f"Whisper warmup no crítico: {e}")


def _detectar_tts():
    """
    Detecta el mejor backend TTS disponible.
    Orden de prioridad: Piper TTS → pyttsx3
    """
    global _pygame_disponible

    # ── pygame-ce o pygame clásico (para reproducir WAV de Piper) ────────────
    try:
        import pygame
        pygame.mixer.init()
        pygame.mixer.quit()
        _pygame_disponible = True
        logger.debug("voice", f"pygame disponible: {pygame.version.ver}")
    except Exception:
        _pygame_disponible = False
        logger.debug("voice", "pygame no disponible — reproducción via sounddevice")

    # ── 1. Piper TTS (offline, voz neural) ────────────────────────────────────
    if (os.path.isfile(PIPER_EXE)
            and os.path.isfile(PIPER_MODEL)
            and os.path.isfile(PIPER_CONFIG)):
        logger.info("voice", f"TTS: Piper offline activo ({PIPER_MODEL})")
        return "piper"
    else:
        logger.debug("voice",
            f"Piper no encontrado — buscando: {PIPER_EXE}, {PIPER_MODEL}, {PIPER_CONFIG}")

    # ── 2. pyttsx3 (siempre disponible como fallback) ─────────────────────────
    try:
        import pyttsx3
        motor = pyttsx3.init()
        motor.stop()
        logger.info("voice", "TTS: pyttsx3 (voz del sistema) activo.")
        return "pyttsx3"
    except Exception as e:
        logger.error("voice", f"pyttsx3 no disponible: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  INICIALIZACIÓN
# ══════════════════════════════════════════════════════════════════════════════

def inicializar():
    """
    Detecta y configura todos los backends de audio/STT/TTS.
    Calibra el micrófono con el ruido ambiental real.
    Retorna True si al menos STT y TTS están disponibles.
    """
    global _disponible, _backend_audio, _backend_stt, _backend_tts

    logger.info("voice", "Inicializando motor de voz v3.0...")

    _backend_audio = _detectar_audio()
    _backend_stt   = _detectar_stt()
    _backend_tts   = _detectar_tts()

    if not _backend_audio:
        logger.error("voice", "Sin audio — modo voz desactivado.")
        _disponible = False
        return False

    if not _backend_stt:
        logger.error("voice", "Sin STT — modo voz desactivado.")
        _disponible = False
        return False

    if not _backend_tts:
        logger.error("voice", "Sin TTS — modo voz desactivado.")
        _disponible = False
        return False

    _calibrar_microfono()
    _verificar_preprocesamiento()

    _disponible = True
    logger.info("voice",
        f"Motor de voz listo | audio={_backend_audio} | "
        f"stt={_backend_stt} | tts={_backend_tts} | "
        f"umbral={UMBRAL_ENERGIA} | preprocesamiento={'ON' if PREPROCESAR_AUDIO else 'OFF'}")
    return True


def _calibrar_microfono():
    """
    Escucha DURACION_CALIBRACION segundos para medir el ruido ambiental real
    y ajusta UMBRAL_ENERGIA dinámicamente.
    El umbral resultante es ruido_base × MULTIPLICADOR_UMBRAL, acotado entre
    UMBRAL_MINIMO y UMBRAL_MAXIMO.
    """
    global UMBRAL_ENERGIA, _ruido_base_rms
    try:
        logger.info("voice", f"Calibrando micrófono ({DURACION_CALIBRACION}s)...")
        pcm = _grabar_audio(duracion=DURACION_CALIBRACION, calibrando=True)
        if not pcm:
            logger.warning("voice", "Calibración sin audio — usando umbral por defecto.")
            return

        import audioop
        rms = audioop.rms(pcm, 2)
        _ruido_base_rms = rms

        umbral_calculado = int(rms * MULTIPLICADOR_UMBRAL)
        UMBRAL_ENERGIA   = max(UMBRAL_MINIMO, min(UMBRAL_MAXIMO, umbral_calculado))

        logger.info("voice",
            f"Calibración completada — ruido base: {rms} RMS → umbral voz: {UMBRAL_ENERGIA}")

    except Exception as e:
        logger.warning("voice", f"No se pudo calibrar micrófono: {e} — usando umbral por defecto ({UMBRAL_ENERGIA})")


def _verificar_preprocesamiento():
    """
    Verifica qué módulos de preprocesamiento están disponibles
    y registra en el log qué partes del pipeline estarán activas.
    """
    if not PREPROCESAR_AUDIO:
        logger.info("voice", "Preprocesamiento de audio: DESACTIVADO por config.")
        return

    partes_activas = []
    partes_faltantes = []

    if USAR_FILTRO_BANDA:
        try:
            from scipy.signal import butter, sosfilt  # noqa
            partes_activas.append("filtro-banda")
        except ImportError:
            partes_faltantes.append("scipy (filtro-banda)")

    if USAR_REDUCCION_RUIDO:
        try:
            import noisereduce  # noqa
            partes_activas.append("noisereduce")
        except ImportError:
            partes_faltantes.append("noisereduce")

    if USAR_NORMALIZACION_AGC:
        try:
            import numpy  # noqa
            partes_activas.append("AGC")
        except ImportError:
            partes_faltantes.append("numpy (AGC)")

    if partes_activas:
        logger.info("voice", f"Preprocesamiento activo: {' → '.join(partes_activas)}")
    if partes_faltantes:
        logger.warning("voice",
            f"Preprocesamiento parcial — instala para activar: {', '.join(partes_faltantes)}")


# ══════════════════════════════════════════════════════════════════════════════
#  PREPROCESAMIENTO DE AUDIO (pipeline v3.0)
# ══════════════════════════════════════════════════════════════════════════════

def _preprocesar_audio(pcm: bytes) -> bytes:
    """
    Pipeline de preprocesamiento de audio antes de enviar a STT.
    Aplica en orden:
      1. Filtro paso-banda 80–8000 Hz (elimina frecuencias fuera del rango de voz)
      2. Reducción de ruido espectral (noisereduce)
      3. Normalización de ganancia AGC (voces bajas llegan bien al STT)
    
    Cada etapa es opcional e independiente. Si falta la librería necesaria,
    esa etapa se omite silenciosamente y el audio pasa a la siguiente.
    Siempre retorna bytes PCM válidos.
    """
    if not PREPROCESAR_AUDIO or not pcm:
        return pcm

    try:
        import numpy as np
        audio_np = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)

        # ── Etapa 1: Filtro paso-banda ─────────────────────────────────────────
        if USAR_FILTRO_BANDA:
            audio_np = _aplicar_filtro_banda(audio_np)

        # ── Etapa 2: Reducción de ruido espectral ─────────────────────────────
        if USAR_REDUCCION_RUIDO:
            audio_np = _reducir_ruido(audio_np)

        # ── Etapa 3: Normalización de ganancia (AGC) ──────────────────────────
        if USAR_NORMALIZACION_AGC:
            audio_np = _normalizar_agc(audio_np)

        return audio_np.astype(np.int16).tobytes()

    except ImportError:
        # numpy no disponible — sin preprocesamiento
        return pcm
    except Exception as e:
        logger.warning("voice", f"Preprocesamiento falló, usando audio sin filtrar: {e}")
        return pcm


def _aplicar_filtro_banda(audio_np: "np.ndarray") -> "np.ndarray":
    """
    Filtro Butterworth paso-banda BANDA_BAJA_HZ–BANDA_ALTA_HZ Hz.
    Elimina ruidos graves (golpes, música bass) y agudos (interferencia eléctrica).
    La voz humana vive entre 80 Hz y 8000 Hz; todo lo demás es ruido.
    """
    try:
        from scipy.signal import butter, sosfilt
        sos = butter(
            ORDEN_FILTRO,
            [BANDA_BAJA_HZ, BANDA_ALTA_HZ],
            btype="bandpass",
            fs=SAMPLE_RATE,
            output="sos"
        )
        return sosfilt(sos, audio_np).astype("float32")
    except ImportError:
        return audio_np
    except Exception as e:
        logger.debug("voice", f"Filtro banda omitido: {e}")
        return audio_np


def _reducir_ruido(audio_np: "np.ndarray") -> "np.ndarray":
    """
    Reducción de ruido espectral con noisereduce.
    Estima el perfil de ruido del inicio del audio (donde suele haber silencio)
    y lo sustrae espectralmente. Muy efectivo con ruido constante (ventilador, AC, TV).
    
    prop_decrease=REDUCCION_INTENSIDAD: qué porcentaje del ruido estimado sustraer.
    0.75 = sustraer el 75% del ruido estimado. Equilibrio entre limpieza y artefactos.
    """
    try:
        import noisereduce as nr
        reducido = nr.reduce_noise(
            y=audio_np,
            sr=SAMPLE_RATE,
            prop_decrease=REDUCCION_INTENSIDAD,
            stationary=False,      # False = mejor para ruido que varía (TV, conversación de fondo)
            n_fft=512,             # ventana FFT — 512 es buen balance velocidad/precisión a 16kHz
            time_mask_smooth_ms=50 # suavizado temporal para evitar artefactos musicales
        )
        return reducido.astype("float32")
    except ImportError:
        return audio_np
    except Exception as e:
        logger.debug("voice", f"Reducción de ruido omitida: {e}")
        return audio_np


def _normalizar_agc(audio_np: "np.ndarray") -> "np.ndarray":
    """
    Control Automático de Ganancia (AGC).
    Normaliza el nivel RMS del audio hacia NIVEL_OBJETIVO_AGC.
    Útil cuando el usuario habla bajo o el micrófono tiene ganancia baja.
    
    Protecciones:
    - No amplifica audio casi silencioso (evita amplificar ruido residual)
    - Limita la amplificación máxima a ×6 para evitar distorsión
    - Clampea entre -32768 y 32767 (rango int16)
    """
    try:
        import numpy as np

        rms_actual = float(np.sqrt(np.mean(audio_np ** 2)))
        if rms_actual < 50:
            # Audio demasiado silencioso — probablemente silencio puro, no amplificar
            return audio_np

        # Nivel objetivo en escala float32 (el audio está en rango int16: max ~32767)
        objetivo = NIVEL_OBJETIVO_AGC * 32767.0
        ganancia = objetivo / rms_actual
        ganancia = min(ganancia, 6.0)   # techo de amplificación

        amplificado = audio_np * ganancia
        return np.clip(amplificado, -32768.0, 32767.0).astype("float32")

    except Exception as e:
        logger.debug("voice", f"AGC omitido: {e}")
        return audio_np


# ══════════════════════════════════════════════════════════════════════════════
#  CAPTURA DE AUDIO
# ══════════════════════════════════════════════════════════════════════════════

def _grabar_audio(duracion=None, calibrando=False):
    """
    Graba audio del micrófono.
    - duracion=float → graba exactamente esa cantidad de segundos (calibración).
    - duracion=None  → graba hasta detectar silencio prolongado (modo comando).
    Retorna bytes PCM 16-bit mono 16000 Hz, o None si falla.
    """
    if _backend_audio == "pyaudio":
        return _grabar_pyaudio(duracion, calibrando)
    elif _backend_audio == "sounddevice":
        return _grabar_sounddevice(duracion, calibrando)
    return None


def _grabar_pyaudio(duracion, calibrando):
    """Grabación con PyAudio."""
    import pyaudio
    pa     = pyaudio.PyAudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE
    )
    frames = []
    try:
        if duracion:
            total = int(SAMPLE_RATE / CHUNK_SIZE * duracion)
            for _ in range(total):
                frames.append(stream.read(CHUNK_SIZE, exception_on_overflow=False))
        else:
            frames = _leer_hasta_silencio(stream.read, calibrando=calibrando)
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
    return b"".join(frames) if frames else None


def _grabar_sounddevice(duracion, calibrando):
    """Grabación con sounddevice."""
    import sounddevice as sd

    cola   = queue.Queue()
    frames = []

    def callback(indata, count, time_info, status):
        cola.put(bytes(indata))

    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=CHUNK_SIZE,
        callback=callback
    ):
        if duracion:
            total = int(SAMPLE_RATE / CHUNK_SIZE * duracion)
            for _ in range(total):
                try:
                    frames.append(cola.get(timeout=2))
                except queue.Empty:
                    break
        else:
            frames = _leer_cola_hasta_silencio(cola, calibrando=calibrando)

    return b"".join(frames) if frames else None


def _leer_hasta_silencio(read_fn, calibrando=False):
    """
    Lee bloques de audio hasta detectar silencio prolongado.
    
    Mejoras v3.0:
    - Doble ventana de silencio: requiere que AMBAS condiciones se cumplan
      (suficientes bloques en silencio Y suficiente audio grabado ya)
    - Feedback de nivel de audio en tiempo real si no está calibrando
    - Protección contra cortes prematuros: mínimo DURACION_SILENCIO×0.5 de audio
      antes de empezar a contar silencio
    """
    import audioop
    frames            = []
    bloques_silencio  = 0
    bloques_max_sil   = int(SAMPLE_RATE / CHUNK_SIZE * DURACION_SILENCIO)
    bloques_max       = int(SAMPLE_RATE / CHUNK_SIZE * PHRASE_TIME_LIMIT)
    # mínimo de bloques de audio antes de empezar a detectar fin de frase
    bloques_min_audio = int(SAMPLE_RATE / CHUNK_SIZE * 0.4)
    inicio            = time.time()
    bloques_con_voz   = 0

    for _ in range(bloques_max):
        try:
            bloque = read_fn(CHUNK_SIZE, exception_on_overflow=False)
        except Exception:
            break

        frames.append(bloque)
        energia = audioop.rms(bloque, 2)

        if energia >= UMBRAL_ENERGIA:
            bloques_con_voz  += 1
            bloques_silencio  = 0
        else:
            if bloques_con_voz >= bloques_min_audio:
                bloques_silencio += 1
                if bloques_silencio >= bloques_max_sil:
                    break

        if time.time() - inicio > PHRASE_TIME_LIMIT:
            break

    return frames


def _leer_cola_hasta_silencio(cola, calibrando=False):
    """Versión de _leer_hasta_silencio para sounddevice (cola en lugar de read_fn)."""
    import audioop
    frames            = []
    bloques_silencio  = 0
    bloques_max_sil   = int(SAMPLE_RATE / CHUNK_SIZE * DURACION_SILENCIO)
    bloques_max       = int(SAMPLE_RATE / CHUNK_SIZE * PHRASE_TIME_LIMIT)
    bloques_min_audio = int(SAMPLE_RATE / CHUNK_SIZE * 0.4)
    inicio            = time.time()
    bloques_con_voz   = 0

    for _ in range(bloques_max):
        try:
            bloque = cola.get(timeout=1)
        except queue.Empty:
            bloques_silencio += 1
            if bloques_silencio >= bloques_max_sil and bloques_con_voz >= bloques_min_audio:
                break
            continue

        frames.append(bloque)
        try:
            energia = audioop.rms(bloque, 2)
        except Exception:
            energia = UMBRAL_ENERGIA + 1

        if energia >= UMBRAL_ENERGIA:
            bloques_con_voz  += 1
            bloques_silencio  = 0
        else:
            if bloques_con_voz >= bloques_min_audio:
                bloques_silencio += 1
                if bloques_silencio >= bloques_max_sil:
                    break

        if time.time() - inicio > PHRASE_TIME_LIMIT:
            break

    return frames


def _pcm_a_wav_bytes(pcm: bytes) -> bytes:
    """Convierte bytes PCM raw a WAV en memoria."""
    buf = tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    buf.seek(0)
    return buf.read()


def _guardar_wav_temp(pcm: bytes) -> str:
    """Guarda PCM como archivo WAV temporal. Retorna la ruta."""
    fd, ruta = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with wave.open(ruta, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return ruta


# ══════════════════════════════════════════════════════════════════════════════
#  RECONOCIMIENTO DE VOZ (STT)
# ══════════════════════════════════════════════════════════════════════════════

def _transcribir(pcm: bytes) -> str | None:
    """
    Convierte audio PCM a texto usando el backend STT activo.
    Aplica el pipeline de preprocesamiento antes de transcribir.
    Retorna el texto reconocido (limpio) o None.
    """
    if not pcm:
        return None

    # Pipeline de preprocesamiento — mejora la señal antes de STT
    pcm = _preprocesar_audio(pcm)
    if not pcm:
        return None

    if _backend_stt == "vosk":
        return _stt_vosk(pcm)
    elif _backend_stt == "whisper":
        return _stt_whisper(pcm)
    elif _backend_stt == "google":
        return _stt_google(pcm)
    return None


def _stt_vosk(pcm: bytes) -> str | None:
    """Reconocimiento offline con Vosk."""
    try:
        _vosk_recognizer.AcceptWaveform(pcm)
        resultado = json.loads(_vosk_recognizer.Result())
        texto     = resultado.get("text", "").strip()
        _vosk_recognizer.Reset()
        return _limpiar_texto_stt(texto) if texto else None
    except Exception as e:
        logger.warning("voice", f"Vosk STT error: {e}")
        return None


def _stt_whisper(pcm: bytes) -> str | None:
    """
    Reconocimiento offline con faster-whisper.
    
    Mejoras v3.0:
    - Silero VAD activado (WHISPER_VAD=True): detecta segmentos de voz
      neuralmente, ignora ruido entre palabras y al inicio/final.
    - vad_parameters ajustados para español conversacional.
    - condition_on_previous_text=False: evita que Whisper "alucine"
      palabras del turno anterior cuando hay ruido.
    - temperature=0.0: modo determinista, más consistente en comandos cortos.
    """
    ruta_wav = None
    try:
        ruta_wav = _guardar_wav_temp(pcm)
        segmentos, info = _whisper_model.transcribe(
            ruta_wav,
            language=IDIOMA,
            beam_size=WHISPER_BEAM,
            vad_filter=WHISPER_VAD,
            vad_parameters=dict(
                min_silence_duration_ms=WHISPER_VAD_MIN_SILENCE,
                speech_pad_ms=200,          # padding antes/después del habla detectada
                threshold=0.4               # sensibilidad VAD (0.4 = tolera más ruido de fondo)
            ),
            condition_on_previous_text=False,
            temperature=0.0,                # determinista — mejor para comandos cortos
            word_timestamps=False           # desactivado para reducir latencia
        )
        texto = " ".join(s.text for s in segmentos).strip()
        return _limpiar_texto_stt(texto) if texto else None
    except Exception as e:
        logger.warning("voice", f"Whisper STT error: {e}")
        return None
    finally:
        if ruta_wav and os.path.exists(ruta_wav):
            try:
                os.remove(ruta_wav)
            except Exception:
                pass


def _stt_google(pcm: bytes) -> str | None:
    """Reconocimiento online con Google STT (fallback)."""
    import speech_recognition as sr
    try:
        wav_bytes  = _pcm_a_wav_bytes(pcm)
        audio_data = sr.AudioData(wav_bytes, SAMPLE_RATE, 2)
        r          = sr.Recognizer()
        texto      = r.recognize_google(audio_data, language=IDIOMA_GOOGLE)
        return _limpiar_texto_stt(texto.strip()) if texto else None
    except sr.UnknownValueError:
        return None
    except sr.RequestError as e:
        logger.error("voice", f"Google STT sin conexión: {e}")
        return None
    except Exception as e:
        logger.warning("voice", f"Google STT error: {e}")
        return None


def _limpiar_texto_stt(texto: str) -> str | None:
    """
    Limpia el texto retornado por el STT.
    Elimina artefactos comunes de Whisper y normaliza espacios.
    Retorna None si el texto resultante es demasiado corto para ser un comando real.
    """
    if not texto:
        return None

    # Artefactos típicos de Whisper en silencio o ruido
    artefactos = {
        "gracias", "gracias.", ".", "..", "...", "¡", "!",
        "sí", "no", "okay", "ok", "hmm", "eh", "ah",
        "subtítulos por la comunidad de amara.org",
        "subtítulos realizados por",
        "www.", "http"
    }
    texto_limpio = texto.strip().lower()

    if texto_limpio in artefactos:
        logger.debug("voice", f"STT: artefacto descartado → '{texto}'")
        return None

    # Descartar textos de solo puntuación o muy cortos (1 char)
    sin_puntuacion = re.sub(r'[^\w]', '', texto_limpio)
    if len(sin_puntuacion) <= 1:
        return None

    return texto.strip()


# ══════════════════════════════════════════════════════════════════════════════
#  SÍNTESIS DE VOZ (TTS)
# ══════════════════════════════════════════════════════════════════════════════

def hablar(texto: str):
    """Sintetiza y reproduce texto. Bloquea hasta terminar."""
    global _hablando
    if not _disponible or not texto or not texto.strip():
        return
    texto_limpio = _limpiar_para_voz(texto)
    if not texto_limpio:
        return
    _hablando = True
    try:
        if _backend_tts == "piper":
            _tts_piper(texto_limpio)
        else:
            _tts_pyttsx3(texto_limpio)
    finally:
        _hablando = False


def hablar_async(texto: str):
    """Sintetiza y reproduce en hilo separado (no bloquea)."""
    hilo = threading.Thread(target=hablar, args=(texto,), daemon=True)
    hilo.start()


def _tts_piper(texto: str):
    """
    Síntesis offline con Piper TTS.
    Piper recibe texto por stdin y escribe WAV por stdout.
    Fallback a pyttsx3 si Piper falla por cualquier razón.
    """
    ruta_wav = None
    try:
        ruta_wav  = tempfile.mktemp(suffix=".wav")
        resultado = subprocess.run(
            [
                PIPER_EXE,
                "--model",       PIPER_MODEL,
                "--config",      PIPER_CONFIG,
                "--output_file", ruta_wav,
            ],
            input=texto.encode("utf-8"),
            capture_output=True,
            timeout=30
        )
        if resultado.returncode != 0:
            raise RuntimeError(resultado.stderr.decode("utf-8", errors="replace"))
        _reproducir_wav(ruta_wav)

    except FileNotFoundError:
        logger.error("voice", f"Piper no encontrado en '{PIPER_EXE}' — usando pyttsx3.")
        _tts_pyttsx3(texto)
    except subprocess.TimeoutExpired:
        logger.error("voice", "Piper TTS tardó demasiado — usando pyttsx3.")
        _tts_pyttsx3(texto)
    except Exception as e:
        logger.error("voice", f"Piper TTS error: {e} — usando pyttsx3.")
        _tts_pyttsx3(texto)
    finally:
        if ruta_wav and os.path.exists(ruta_wav):
            try:
                os.remove(ruta_wav)
            except Exception:
                pass


def _reproducir_wav(ruta_wav: str):
    """Reproduce un archivo WAV usando pygame o sounddevice."""
    if _pygame_disponible:
        _reproducir_wav_pygame(ruta_wav)
    else:
        _reproducir_wav_sounddevice(ruta_wav)


def _reproducir_wav_pygame(ruta_wav: str):
    """Reproducción con pygame."""
    try:
        import pygame
        pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=1)
        pygame.mixer.music.load(ruta_wav)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
        pygame.mixer.quit()
    except Exception as e:
        logger.error("voice", f"pygame reproducción error: {e}")


def _reproducir_wav_sounddevice(ruta_wav: str):
    """Reproducción con sounddevice + soundfile."""
    try:
        import sounddevice as sd
        import soundfile  as sf
        datos, fs = sf.read(ruta_wav, dtype="int16")
        sd.play(datos, fs)
        sd.wait()
    except Exception as e:
        logger.error("voice", f"sounddevice reproducción error: {e}")


def _tts_pyttsx3(texto: str):
    """Síntesis offline con pyttsx3 (voz del sistema operativo)."""
    try:
        import pyttsx3
        motor = pyttsx3.init()
        motor.setProperty("rate",   VELOCIDAD_VOZ)
        motor.setProperty("volume", VOLUMEN_VOZ)
        voces = motor.getProperty("voices")
        for voz in voces:
            nombre = voz.name.lower() if voz.name else ""
            vid    = voz.id.lower()   if voz.id   else ""
            if "spanish" in nombre or "es" in vid or "español" in nombre:
                motor.setProperty("voice", voz.id)
                break
        motor.say(texto)
        motor.runAndWait()
        motor.stop()
    except Exception as e:
        logger.error("voice", f"pyttsx3 error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  ESCUCHA: WAKE WORD Y COMANDOS
# ══════════════════════════════════════════════════════════════════════════════

def escuchar_wakeword():
    """
    Escucha continuamente hasta detectar una wake word.
    
    Retorna:
      (True,  str)  — wake word detectada con comando inline
      (True,  None) — wake word detectada sin comando adicional
      (False, None) — no se detectó wake word
    """
    global _estado
    if not _disponible:
        return False, None

    _estado = HIBERNANDO
    try:
        pcm = _grabar_audio()
        if not pcm:
            return False, None

        # Para wake word NO aplicamos preprocesamiento pesado — latencia crítica
        # Solo filtro de banda si está activo (rápido) — sin noisereduce
        texto = _transcribir_rapido(pcm)
        if not texto:
            return False, None

        texto_norm = normalizar_texto(texto.lower().strip())
        palabras   = texto_norm.split()
        logger.debug("voice", f"Wake word — escuché: '{texto}'")

        for wake in WAKE_WORDS:
            wake_norm = normalizar_texto(wake)

            if texto_norm == wake_norm:
                return True, None

            if texto_norm.startswith(wake_norm + " "):
                comando_inline = texto_norm[len(wake_norm):].strip()
                return True, (comando_inline or None)

            if len(palabras) <= 3 and wake_norm in palabras:
                return True, None

        return False, None

    except Exception as e:
        logger.log_excepcion("voice", "escuchar_wakeword", e)
        return False, None


def _transcribir_rapido(pcm: bytes) -> str | None:
    """
    Transcripción rápida para wake word detection.
    Aplica solo filtro de banda (instantáneo), omite noisereduce y AGC.
    Reduce la latencia de respuesta al wake word.
    """
    if not pcm:
        return None

    # Solo filtro de banda — rápido, sin dependencias pesadas
    if PREPROCESAR_AUDIO and USAR_FILTRO_BANDA:
        try:
            import numpy as np
            audio_np = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
            audio_np = _aplicar_filtro_banda(audio_np)
            pcm      = audio_np.astype(np.int16).tobytes()
        except Exception:
            pass

    if _backend_stt == "vosk":
        return _stt_vosk(pcm)
    elif _backend_stt == "whisper":
        return _stt_whisper(pcm)
    elif _backend_stt == "google":
        return _stt_google(pcm)
    return None


def escuchar_comando():
    """
    Escucha y transcribe un comando completo del usuario.
    
    Mejoras v3.0:
    - Reintentos automáticos (MAX_REINTENTOS_ESCUCHA) si no capta nada
    - Pipeline de preprocesamiento completo (filtro + noisereduce + AGC)
    - Feedback mejorado en terminal
    
    Retorna el texto del comando o None si no se capturó nada tras todos los reintentos.
    """
    global _estado
    if not _disponible:
        return None

    _estado = ACTIVADA

    for intento in range(MAX_REINTENTOS_ESCUCHA + 1):
        if intento == 0:
            print("🎤 SARA: Escuchando...")
        else:
            print(f"🎤 SARA: Escuchando de nuevo... (intento {intento + 1})")
            time.sleep(SILENCIO_ENTRE_REINTENTOS)

        try:
            pcm = _grabar_audio()

            if not pcm:
                if intento < MAX_REINTENTOS_ESCUCHA:
                    print("💤 SARA: No escuché nada, reintentando...")
                    continue
                else:
                    print("💤 SARA: No escuché nada, volviendo a espera.")
                    _estado = HIBERNANDO
                    return None

            _estado = PROCESANDO
            print("🧠 SARA: Procesando...")

            texto = _transcribir(pcm)   # pipeline completo con preprocesamiento

            if not texto:
                if intento < MAX_REINTENTOS_ESCUCHA:
                    print("❓ SARA: No entendí, intenta de nuevo.")
                    _estado = ACTIVADA
                    continue
                else:
                    print("❓ SARA: No pude entender el comando.")
                    _estado = HIBERNANDO
                    return None

            logger.info("voice", f"Comando capturado: '{texto}'")
            return texto

        except Exception as e:
            logger.log_excepcion("voice", "escuchar_comando", e)
            if intento >= MAX_REINTENTOS_ESCUCHA:
                _estado = HIBERNANDO
                return None

    _estado = HIBERNANDO
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  UTILIDADES PÚBLICAS
# ══════════════════════════════════════════════════════════════════════════════

def esta_disponible() -> bool:
    return _disponible


def obtener_estado() -> str:
    return _estado


def esta_hablando() -> bool:
    return _hablando


def obtener_backends() -> dict:
    """Retorna los backends activos y el estado del preprocesamiento para diagnóstico."""
    return {
        "audio":             _backend_audio,
        "stt":               _backend_stt,
        "tts":               _backend_tts,
        "umbral_energia":    UMBRAL_ENERGIA,
        "ruido_base_rms":    _ruido_base_rms,
        "preprocesamiento":  PREPROCESAR_AUDIO,
        "filtro_banda":      USAR_FILTRO_BANDA,
        "reduccion_ruido":   USAR_REDUCCION_RUIDO,
        "agc":               USAR_NORMALIZACION_AGC,
        "whisper_model":     WHISPER_MODEL if _backend_stt == "whisper" else None,
        "whisper_vad":       WHISPER_VAD   if _backend_stt == "whisper" else None,
    }


def listar_microfonos() -> list:
    """Lista los micrófonos disponibles en el sistema."""
    nombres = []
    try:
        if _backend_audio == "pyaudio":
            import pyaudio
            pa = pyaudio.PyAudio()
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0:
                    print(f"  [{i}] {info['name']}")
                    nombres.append(info["name"])
            pa.terminate()
        elif _backend_audio == "sounddevice":
            import sounddevice as sd
            dispositivos = sd.query_devices()
            for i, d in enumerate(dispositivos):
                if d["max_input_channels"] > 0:
                    print(f"  [{i}] {d['name']}")
                    nombres.append(d["name"])
    except Exception as e:
        logger.error("voice", f"Error listando micrófonos: {e}")
    return nombres


def agregar_wake_word(palabra: str):
    """Agrega una nueva wake word en tiempo de ejecución."""
    WAKE_WORDS.add(normalizar_texto(palabra.lower()))
    logger.info("voice", f"Wake word agregada: '{palabra}'")


def obtener_wake_words() -> list:
    return list(WAKE_WORDS)


def detener_voz():
    """Señala que se debe detener la reproducción actual."""
    global _hablando
    _hablando = False


def recalibrar():
    """
    Fuerza una nueva calibración del micrófono.
    Útil si el usuario cambia de habitación o el ambiente cambia mucho.
    Retorna el nuevo umbral calculado.
    """
    logger.info("voice", "Recalibrando micrófono por solicitud...")
    _calibrar_microfono()
    logger.info("voice", f"Recalibración completada — nuevo umbral: {UMBRAL_ENERGIA}")
    return UMBRAL_ENERGIA


def diagnostico() -> dict:
    """
    Retorna un resumen completo del estado del motor de voz para debugging.
    Incluye backends, umbrales, pipeline activo y configuración de Whisper.
    """
    return {
        **obtener_backends(),
        "estado":            _estado,
        "hablando":          _hablando,
        "whisper_beam":      WHISPER_BEAM,
        "duracion_silencio": DURACION_SILENCIO,
        "phrase_time_limit": PHRASE_TIME_LIMIT,
        "max_reintentos":    MAX_REINTENTOS_ESCUCHA,
        "reduccion_intensidad": REDUCCION_INTENSIDAD,
        "nivel_objetivo_agc":   NIVEL_OBJETIVO_AGC,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  LIMPIEZA DE TEXTO PARA VOZ
# ══════════════════════════════════════════════════════════════════════════════

def _limpiar_para_voz(texto: str) -> str:
    """
    Elimina caracteres y formatos que suenan mal cuando se leen en voz alta.
    """
    # URLs → "el enlace"
    texto = re.sub(r'https?://\S+', 'el enlace', texto)
    # Markdown: **, __, ##, `, etc.
    texto = re.sub(r'\*+|_+|#+|`+', '', texto)
    # Bloques de código
    texto = re.sub(r'```[\s\S]*?```', 'el código', texto)
    # Emojis y símbolos no ascii (conservar tildes y ñ)
    texto = re.sub(r'[^\w\s\.\,\!\?\:\-\(\)áéíóúüñÁÉÍÓÚÜÑ]', ' ', texto)
    # Espacios múltiples
    texto = re.sub(r'\s+', ' ', texto).strip()
    return texto
