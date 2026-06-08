# ╔══════════════════════════════════════════════════════════════════╗
# ║  SARA — voice.py  v2.0                                          ║
# ║  Motor de voz offline-first                                      ║
# ║                                                                  ║
# ║  STT (prioridad):                                                ║
# ║    1. Vosk          — offline, rápido, modelo ~50 MB             ║
# ║    2. faster-whisper — offline, preciso, modelo tiny ~75 MB      ║
# ║    3. Google STT    — fallback online (solo si hay internet)      ║
# ║                                                                  ║
# ║  TTS (prioridad):                                                ║
# ║    1. Piper TTS     — offline, voz neural es-MX, WAV             ║
# ║    2. pyttsx3       — offline, voz del sistema, siempre          ║
# ╚══════════════════════════════════════════════════════════════════╝

import os
import re
import queue
import threading
import subprocess
import tempfile
import time
import wave

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
os.environ["TOKENIZERS_PARALLELISM"]     = "false"

import logger
from utils import normalizar_texto

# ── Estados ───────────────────────────────────────────────────────────────────
HIBERNANDO   = "hibernando"
ACTIVADA     = "activada"
PROCESANDO   = "procesando"
RESPONDIENDO = "respondiendo"

# ── Wake words ────────────────────────────────────────────────────────────────
WAKE_WORDS = {"sara", "zara", "sará", "sarah", "sera"}

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN — edita aquí según tu hardware y preferencias
# ══════════════════════════════════════════════════════════════════════════════

# ── Audio ─────────────────────────────────────────────────────────────────────
SAMPLE_RATE      = 16000       # Hz  (Vosk y Whisper esperan 16 kHz)
CHUNK_SIZE       = 1024        # frames por bloque
UMBRAL_ENERGIA   = 300         # nivel mínimo para considerar habla
TIMEOUT_ESCUCHA  = 5           # segundos esperando comando
TIMEOUT_WAKEWORD = None        # None = espera indefinida al wake word
DURACION_SILENCIO = 1.2        # segundos de silencio para cortar grabación
PHRASE_TIME_LIMIT = 10         # segundos máximos de un comando

# ── STT ───────────────────────────────────────────────────────────────────────
IDIOMA           = "es"                 # código de idioma para Vosk/Whisper
IDIOMA_GOOGLE    = "es-MX"             # código extendido para Google STT
VOSK_MODEL_PATH  = "models/vosk-es"   # carpeta del modelo Vosk descargado
WHISPER_MODEL    = "base"              # opciones: tiny · base · small · medium
WHISPER_DEVICE   = "cpu"              # "cpu" o "cuda" si tienes GPU Nvidia
WHISPER_COMPUTE  = "int8"             # "int8" (rápido/liviano) o "float16"

# ── TTS ───────────────────────────────────────────────────────────────────────
PIPER_EXE        = "piper/piper.exe"                         # ejecutable Piper
PIPER_MODEL      = "models/piper/es_MX-ald-medium.onnx"     # modelo de voz
PIPER_CONFIG     = "models/piper/es_MX-ald-medium.onnx.json" # config del modelo
VELOCIDAD_VOZ    = 150         # solo pyttsx3
VOLUMEN_VOZ      = 1.0         # solo pyttsx3

# ══════════════════════════════════════════════════════════════════════════════
#  Estado interno (no modificar)
# ══════════════════════════════════════════════════════════════════════════════
_estado           = HIBERNANDO
_disponible       = False
_hablando         = False

# Backends disponibles (se detectan en inicializar())
_backend_audio    = None   # "pyaudio" | "sounddevice" | None
_backend_stt      = None   # "vosk" | "whisper" | "google" | None
_backend_tts      = None   # "piper" | "pyttsx3" | None

# Instancias reutilizables
_vosk_model       = None
_vosk_recognizer  = None
_whisper_model    = None
_pygame_disponible = False


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
    Retorna 'vosk', 'whisper', 'google' o None.
    """
    global _vosk_model, _vosk_recognizer, _whisper_model

    # ── 1. Vosk (offline, rápido) ─────────────────────────────────────────────
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

    # ── 2. faster-whisper (offline, preciso) ─────────────────────────────────
    try:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE
        )
        # Calentamiento breve para que el primer uso no tenga latencia
        _whisper_model.transcribe.__doc__  # touch
        logger.info("voice", f"STT: faster-whisper offline activo (modelo: {WHISPER_MODEL})")
        return "whisper"
    except Exception as e:
        logger.warning("voice", f"faster-whisper no disponible: {e}")

    # ── 3. Google STT (fallback online) ──────────────────────────────────────
    try:
        import speech_recognition as sr
        r = sr.Recognizer()
        logger.warning("voice", "STT: solo Google online disponible. Sin internet no habrá voz.")
        return "google"
    except Exception as e:
        logger.error("voice", f"Sin backend STT disponible: {e}")
        return None

def _detectar_tts():
    global _pygame_disponible

    # ── pygame-ce (compatible Python 3.14) o pygame clásico ──────────────────
    try:
        import pygame
        pygame.mixer.init()
        pygame.mixer.quit()
        _pygame_disponible = True
        logger.debug("voice", f"pygame disponible: {pygame.version.ver}")
    except Exception:
        _pygame_disponible = False
        logger.debug("voice", "pygame no disponible — reproducción via sounddevice")
    
    # resto igual...

    # ── 1. Piper TTS (offline, neural) ────────────────────────────────────────
    if (os.path.isfile(PIPER_EXE)
            and os.path.isfile(PIPER_MODEL)
            and os.path.isfile(PIPER_CONFIG)):
        logger.info("voice", f"TTS: Piper offline activo ({PIPER_MODEL})")
        return "piper"
    else:
        logger.debug("voice",
            f"Piper no encontrado — buscando: {PIPER_EXE}, {PIPER_MODEL}, {PIPER_CONFIG}")

    # ── 2. pyttsx3 (siempre disponible) ──────────────────────────────────────
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
    Retorna True si al menos STT y TTS están disponibles.
    """
    global _disponible, _backend_audio, _backend_stt, _backend_tts

    logger.info("voice", "Inicializando motor de voz...")

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

    # Calibrar nivel de ruido ambiental
    _calibrar_microfono()

    _disponible = True
    logger.info("voice",
        f"Motor de voz listo | audio={_backend_audio} | "
        f"stt={_backend_stt} | tts={_backend_tts}")
    return True


def _calibrar_microfono():
    """Escucha 1 segundo para ajustar el umbral de energía al ruido ambiental."""
    try:
        audio = _grabar_audio(duracion=1.0, calibrando=True)
        logger.debug("voice", "Micrófono calibrado.")
    except Exception as e:
        logger.warning("voice", f"No se pudo calibrar micrófono: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  CAPTURA DE AUDIO
# ══════════════════════════════════════════════════════════════════════════════

def _grabar_audio(duracion=None, calibrando=False):
    """
    Graba audio del micrófono.
    - Si duracion es un float, graba exactamente esa cantidad de segundos.
    - Si duracion es None, graba hasta detectar silencio (modo comando).
    Retorna bytes de audio PCM 16-bit mono 16000 Hz, o None si falla.
    """
    if _backend_audio == "pyaudio":
        return _grabar_pyaudio(duracion, calibrando)
    elif _backend_audio == "sounddevice":
        return _grabar_sounddevice(duracion, calibrando)
    return None


def _grabar_pyaudio(duracion, calibrando):
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
            frames = _leer_hasta_silencio(stream.read)
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
    return b"".join(frames) if frames else None


def _grabar_sounddevice(duracion, calibrando):
    import sounddevice as sd
    import numpy as np

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
            frames = _leer_cola_hasta_silencio(cola)

    return b"".join(frames) if frames else None


def _leer_hasta_silencio(read_fn):
    """
    Lee bloques de audio hasta detectar silencio prolongado.
    Corta cuando la energía cae por DURACION_SILENCIO segundos.
    """
    import audioop
    frames          = []
    bloques_silencio = 0
    bloques_max_sil = int(SAMPLE_RATE / CHUNK_SIZE * DURACION_SILENCIO)
    bloques_max     = int(SAMPLE_RATE / CHUNK_SIZE * PHRASE_TIME_LIMIT)
    inicio          = time.time()

    for _ in range(bloques_max):
        try:
            bloque = read_fn(CHUNK_SIZE, exception_on_overflow=False)
        except Exception:
            break
        frames.append(bloque)
        energia = audioop.rms(bloque, 2)
        if energia < UMBRAL_ENERGIA:
            bloques_silencio += 1
            if bloques_silencio >= bloques_max_sil and len(frames) > bloques_max_sil:
                break
        else:
            bloques_silencio = 0

        if time.time() - inicio > PHRASE_TIME_LIMIT:
            break

    return frames


def _leer_cola_hasta_silencio(cola):
    """Versión de _leer_hasta_silencio para sounddevice (cola en lugar de read_fn)."""
    import audioop
    frames          = []
    bloques_silencio = 0
    bloques_max_sil = int(SAMPLE_RATE / CHUNK_SIZE * DURACION_SILENCIO)
    bloques_max     = int(SAMPLE_RATE / CHUNK_SIZE * PHRASE_TIME_LIMIT)
    inicio          = time.time()

    for _ in range(bloques_max):
        try:
            bloque = cola.get(timeout=1)
        except queue.Empty:
            bloques_silencio += 1
            if bloques_silencio >= bloques_max_sil:
                break
            continue

        frames.append(bloque)
        try:
            import audioop
            energia = audioop.rms(bloque, 2)
        except Exception:
            energia = UMBRAL_ENERGIA + 1  # asumir habla si no podemos medir

        if energia < UMBRAL_ENERGIA:
            bloques_silencio += 1
            if bloques_silencio >= bloques_max_sil and len(frames) > bloques_max_sil:
                break
        else:
            bloques_silencio = 0

        if time.time() - inicio > PHRASE_TIME_LIMIT:
            break

    return frames


def _pcm_a_wav_bytes(pcm: bytes) -> bytes:
    """Convierte bytes PCM raw a WAV en memoria (para faster-whisper)."""
    buf = tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)   # 16-bit
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
    Retorna el texto reconocido o None.
    """
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
    import json
    try:
        _vosk_recognizer.AcceptWaveform(pcm)
        resultado = json.loads(_vosk_recognizer.Result())
        texto     = resultado.get("text", "").strip()
        _vosk_recognizer.Reset()   # limpia para la siguiente frase
        return texto if texto else None
    except Exception as e:
        logger.warning("voice", f"Vosk STT error: {e}")
        return None


def _stt_whisper(pcm: bytes) -> str | None:
    """Reconocimiento offline con faster-whisper."""
    import io
    ruta_wav = None
    try:
        ruta_wav = _guardar_wav_temp(pcm)
        segmentos, info = _whisper_model.transcribe(
            ruta_wav,
            language=IDIOMA,
            beam_size=5,
            vad_filter=True,          # filtra segmentos sin voz
            vad_parameters=dict(min_silence_duration_ms=500)
        )
        texto = " ".join(s.text for s in segmentos).strip()
        return texto if texto else None
    except Exception as e:
        logger.warning("voice", f"Whisper STT error: {e}")
        return None
    finally:
        if ruta_wav and os.path.exists(ruta_wav):
            os.remove(ruta_wav)


def _stt_google(pcm: bytes) -> str | None:
    """Reconocimiento online con Google STT (fallback)."""
    import speech_recognition as sr
    import io
    try:
        wav_bytes  = _pcm_a_wav_bytes(pcm)
        audio_data = sr.AudioData(wav_bytes, SAMPLE_RATE, 2)
        r          = sr.Recognizer()
        texto      = r.recognize_google(audio_data, language=IDIOMA_GOOGLE)
        return texto.strip() if texto else None
    except sr.UnknownValueError:
        return None
    except sr.RequestError as e:
        logger.error("voice", f"Google STT sin conexión: {e}")
        return None
    except Exception as e:
        logger.warning("voice", f"Google STT error: {e}")
        return None


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
    """
    ruta_wav = None
    try:
        ruta_wav = tempfile.mktemp(suffix=".wav")
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
        # Intentar seleccionar voz en español
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
    Retorna (True, comando_inline) si hay comando en la misma frase,
    (True, None) si solo se dijo el wake word,
    (False, None) si no se detectó.
    """
    global _estado
    if not _disponible:
        return False, None

    _estado = HIBERNANDO
    try:
        pcm = _grabar_audio()
        if not pcm:
            return False, None

        texto = _transcribir(pcm)
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

            if len(palabras) <= 2 and wake_norm in palabras:
                return True, None

        return False, None

    except Exception as e:
        logger.log_excepcion("voice", "escuchar_wakeword", e)
        return False, None


def escuchar_comando():
    """
    Escucha y transcribe un comando completo del usuario.
    Retorna el texto del comando o None si no se capturó nada.
    """
    global _estado
    if not _disponible:
        return None

    _estado = ACTIVADA
    print("🎤 SARA: Escuchando...")

    try:
        pcm = _grabar_audio()

        if not pcm:
            print("💤 SARA: No escuché nada, volviendo a espera...")
            return None

        _estado = PROCESANDO
        print("🧠 SARA: Procesando...")

        texto = _transcribir(pcm)

        if not texto:
            print("❓ SARA: No entendí, intenta de nuevo.")
            return None

        logger.info("voice", f"Comando capturado: '{texto}'")
        return texto

    except Exception as e:
        logger.log_excepcion("voice", "escuchar_comando", e)
        return None
    finally:
        _estado = HIBERNANDO


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
    """Retorna los backends activos para diagnóstico."""
    return {
        "audio": _backend_audio,
        "stt":   _backend_stt,
        "tts":   _backend_tts,
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
    # Código entre backticks o bloques
    texto = re.sub(r'```[\s\S]*?```', 'el código', texto)
    # Emojis y símbolos no ascii (conservar tildes y ñ)
    texto = re.sub(r'[^\w\s\.\,\!\?\:\-\(\)áéíóúüñÁÉÍÓÚÜÑ]', ' ', texto)
    # Espacios múltiples
    texto = re.sub(r'\s+', ' ', texto).strip()
    return texto


