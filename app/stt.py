# 📁 stt.py — Voz de entrada (Speech-To-Text) de SARA  (v1)
#
# Qué hace: escucha el micrófono MIENTRAS el modo voz está activado (botón 🎙️ de la GUI), detecta cuándo
# hablas con un noise gate por RMS, transcribe cada frase con faster-whisper (modelo base, CPU, int8) y
# entrega el texto a la GUI, que lo envía por el MISMO camino que un mensaje escrito (mensaje_usuario).
#
# Diseño (mismos principios que voz.py / lector_voz.py):
#   * Módulo aislado y opcional: si falta faster-whisper, sounddevice o el micrófono, SARA sigue igual.
#   * Captura en Python (sounddevice), no en el navegador: sin permisos de micrófono de pywebview.
#   * NOISE GATE: el RMS de cada bloque (32 ms) se compara con el umbral del slider. Lo que no lo supera
#     NO se acumula ni se manda a Whisper (cero CPU en silencio). Slider a la derecha = exige más voz.
#   * Escala del slider 0-100 (logarítmica sobre el RMS). El medidor de nivel usa la MISMA escala, así que
#     se ve si tu voz pasa o no por encima de la marca del slider.
#   * Anti-eco: mientras SARA habla (voz.py) o el lector lee (lector_voz.py) el micrófono se ignora,
#     más una cola de 0.7 s después de que termina de hablar.
#   * El modelo se carga al primer uso (no al arrancar SARA). Primera vez: descarga (~150 MB, requiere
#     internet una sola vez). Después funciona 100 % offline desde la caché de Hugging Face.
#   * Desacoplado: este módulo solo produce TEXTO. A qué motor de lenguaje se enruta es cosa de quien
#     consuma `stt_texto` (hoy, el mismo clasificador de SARA).
#
# Eventos que emite (emit(tipo, **datos)):
#   stt_estado(estado = apagado|cargando|escuchando|grabando|transcribiendo|pausado|error,
#              activo, umbral, mensaje)
#   stt_nivel(nivel, umbral)                     # ~10 Hz, escala 0-100
#   stt_texto(texto, audio_s, tiempo_ms)
#
# API: activar(emit, umbral) · desactivar() · set_umbral(valor, guardar) · estado_actual()
#      transcribir_archivo(ruta, max_s) · precargar_modelo()      ← v3: notas de voz de WhatsApp (SARA-Net)
#      set_voz_hablando(bool) · esta_activo() · diagnostico()

import collections
import json
import logging
import math
import os
import queue
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("SARA-STT")

try:
    import numpy as np
except Exception:                                    # sin numpy no hay STT, pero SARA sigue viva
    np = None

try:
    import config
except Exception:
    config = None

try:
    import lector_voz                                # solo para saber si el lector está leyendo (anti-eco)
except Exception:
    lector_voz = None


def _cfg(nombre: str, defecto):
    return getattr(config, nombre, defecto) if config is not None else defecto


# ─── PARÁMETROS (se pueden sobrescribir en config.py con los nombres STT_*) ───────────────────────

MODELO = _cfg("STT_MODELO", "base")
IDIOMA = _cfg("STT_IDIOMA", "es")
BEAM = int(_cfg("STT_BEAM", 2))
SILENCIO_CIERRE_S = float(_cfg("STT_SILENCIO_CIERRE_S", 0.8))   # silencio que cierra una frase
MIN_VOZ_S = float(_cfg("STT_MIN_VOZ_S", 0.35))                   # menos voz que esto = ruido, se descarta
MAX_SEGMENTO_S = float(_cfg("STT_MAX_SEGMENTO_S", 20.0))         # corte forzado de una frase eterna
POS_DEFECTO = float(_cfg("STT_UMBRAL_POS_DEFECTO", 35))          # posición inicial del slider (0-100)
TRAS_VOZ_S = float(_cfg("STT_COLA_TRAS_VOZ_S", 0.7))             # silencio extra tras hablar SARA

# Palabra de activación: solo se atienden las frases que empiezan con "sara" (u "oye sara", "hey sara"...).
REQUERIR_ACTIVACION = bool(_cfg("STT_REQUERIR_ACTIVACION", True))
PALABRAS_ACTIVACION = tuple(_cfg("STT_PALABRAS_ACTIVACION", ("sara", "sarah", "zara", "sarra")))
VENTANA_COMANDO_S = float(_cfg("STT_VENTANA_COMANDO_S", 8.0))    # tras decir solo "Sara", segundos para dar la orden
STT_VERSION = "v3-activacion+archivos"                                    # aparece en /stt y en el log para confirmar qué versión cargó

MUESTREO_OBJETIVO = 16000
BLOQUE_S = 0.032
PREROLL_S = 0.30            # audio previo al disparo (para no comerse la primera sílaba)
HISTERESIS = 0.6            # una vez grabando, se sigue mientras RMS >= umbral * 0.6
BLOQUES_PARA_DISPARAR = 2   # 2 bloques seguidos sobre el umbral (un clic suelto no dispara)
R_MIN = 0.003               # RMS en posición 0 del slider (muy sensible)
R_MAX = 0.15                # RMS en posición 100 (hay que hablar fuerte)
PERIODO_NIVEL_S = 0.1

# Vocabulario de SARA: ayuda a Whisper con comandos cortos. Corto a propósito (un prompt largo alucina).
PROMPT = "Comandos para SARA: abre, cierra, busca, carpeta, archivo, recordatorio, tarea, nota."

RUTA_CFG = Path(__file__).resolve().parent / "stt_config.json"

# Frases que Whisper inventa ante ruido/silencio (aparecen con los modelos pequeños).
_ALUCINACIONES = (
    "subtitulos realizados", "subtitulos por la comunidad", "subtitulos de", "amara org",
    "gracias por ver", "suscribete", "no olvides suscribirte", "hasta la proxima",
    "gracias por su atencion", "musica", "aplausos", "risas",
)


# ─── ESTADO ───────────────────────────────────────────────────────────────

_lock = threading.RLock()
_emit: Optional[Callable] = None
_activo = False
_estado = "apagado"
_mensaje = ""
_stop: Optional[threading.Event] = None
_hilo: Optional[threading.Thread] = None
_modelo = None
_modelo_lock = threading.Lock()
_transcripcion_lock = threading.Lock()   # el micrófono y las notas de voz comparten el modelo: una a la vez
_ultimo_error = ""
_voz_hablando = False
_voz_expira = 0.0
_voz_fin = 0.0
_transcribiendo = 0
_cola_seg: "queue.Queue" = queue.Queue(maxsize=2)
_hilo_transcriptor: Optional[threading.Thread] = None
_espera_hasta = 0.0          # ventana abierta tras oír solo "Sara"


def _leer_umbral_guardado() -> float:
    try:
        return max(0.0, min(100.0, float(json.loads(RUTA_CFG.read_text(encoding="utf-8")).get("umbral"))))
    except Exception:
        return max(0.0, min(100.0, POS_DEFECTO))


_pos = _leer_umbral_guardado()


# ─── ESCALA DEL UMBRAL ────────────────────────────────────────────────────

def _rms_umbral(pos: float) -> float:
    return R_MIN * (R_MAX / R_MIN) ** (max(0.0, min(100.0, pos)) / 100.0)


def _pos_desde_rms(rms: float) -> float:
    if rms <= R_MIN:
        return 0.0
    return min(100.0, 100.0 * math.log(rms / R_MIN) / math.log(R_MAX / R_MIN))


# ─── EVENTOS ──────────────────────────────────────────────────────────────

def estado_actual() -> dict:
    """Foto del estado (la GUI la recibe al conectarse: así se resincroniza tras reconexiones)."""
    with _lock:
        return {"estado": _estado if _activo else "apagado", "activo": _activo,
                "umbral": round(_pos, 1), "mensaje": _mensaje if _activo else ""}


def _emitir(tipo: str, **datos) -> None:
    if _emit is None:
        return
    try:
        _emit(tipo, **datos)
    except Exception as e:
        logger.debug(f"emit({tipo}) falló: {e}")


def _poner_estado(estado: str, mensaje: str = "") -> None:
    """Emite stt_estado solo si algo cambió."""
    global _estado, _mensaje
    with _lock:
        if estado == _estado and mensaje == _mensaje:
            return
        _estado, _mensaje = estado, mensaje
    _emitir("stt_estado", **estado_actual())


def _fallar(stop: threading.Event, mensaje: str) -> None:
    """Error irrecuperable de esta sesión: se apaga el modo voz y se avisa a la GUI (una sola vez)."""
    global _activo, _estado, _mensaje, _ultimo_error
    logger.error(f"❌ STT: {mensaje}")
    with _lock:
        _ultimo_error = mensaje
        if _stop is not stop:
            return
        _activo = False
        stop.set()
    _emitir("stt_estado", estado="error", activo=False, umbral=round(_pos, 1), mensaje=mensaje)
    with _lock:
        _estado, _mensaje = "apagado", ""


# ─── ANTI-ECO ─────────────────────────────────────────────────────────────

def set_voz_hablando(hablando: bool) -> None:
    """Lo llama core_bridge al ver voz_estado de voz.py."""
    global _voz_hablando, _voz_expira, _voz_fin
    now = time.monotonic()
    with _lock:
        if hablando:
            _voz_hablando = True
            _voz_expira = now + 60.0          # red de seguridad: si nunca llega el 'False', se libera sola
        else:
            _voz_hablando = False
            _voz_fin = now + TRAS_VOZ_S


def _suprimido() -> bool:
    global _voz_hablando
    now = time.monotonic()
    with _lock:
        if _voz_hablando and now > _voz_expira:
            _voz_hablando = False
        if _voz_hablando or now < _voz_fin:
            return True
    try:
        return bool(lector_voz is not None and lector_voz.esta_leyendo())
    except Exception:
        return False


# ─── SEGMENTADOR (noise gate + detección de frases) ──────────────────────

class _Segmentador:
    """Recibe bloques de audio y devuelve (rms, frase|None). Sin hilos ni E/S: es fácil de probar."""

    def __init__(self, muestreo: int):
        self.sr = muestreo
        self.preroll = collections.deque(maxlen=max(1, int(PREROLL_S / BLOQUE_S)))
        self.reset()

    def reset(self) -> None:
        self.grabando = False
        self.buf = []
        self.altos = 0
        self.silencio = 0.0
        self.duracion = 0.0
        self.voz_s = 0.0
        self.preroll.clear()

    def alimentar(self, bloque, umbral: float):
        rms = float(np.sqrt(np.mean(np.square(bloque)))) if len(bloque) else 0.0
        dt = len(bloque) / float(self.sr)
        if not self.grabando:
            self.preroll.append(bloque)
            if rms >= umbral:
                self.altos += 1
                if self.altos >= BLOQUES_PARA_DISPARAR:
                    self.grabando = True
                    self.buf = list(self.preroll)
                    self.duracion = sum(len(b) for b in self.buf) / float(self.sr)
                    self.voz_s = self.altos * dt
                    self.silencio = 0.0
            else:
                self.altos = 0
            return rms, None

        self.buf.append(bloque)
        self.duracion += dt
        if rms >= umbral * HISTERESIS:
            self.silencio = 0.0
            self.voz_s += dt
        else:
            self.silencio += dt
        if self.silencio >= SILENCIO_CIERRE_S or self.duracion >= MAX_SEGMENTO_S:
            sobrante = int((self.silencio - 0.25) / dt) if dt > 0 else 0   # recorta el silencio final
            if sobrante > 0:
                self.buf = self.buf[:-sobrante]
            frase = np.concatenate(self.buf) if (self.buf and self.voz_s >= MIN_VOZ_S) else None
            self.reset()
            return rms, frase
        return rms, None


# ─── MODELO Y TRANSCRIPCIÓN ───────────────────────────────────────────────

def _cargar_modelo():
    """Carga faster-whisper una sola vez. Primero desde la caché local (offline); si no está, lo descarga."""
    global _modelo, _ultimo_error
    with _modelo_lock:
        if _modelo is not None:
            return _modelo
        try:
            from faster_whisper import WhisperModel
        except Exception as e:
            _ultimo_error = f"faster-whisper no disponible: {type(e).__name__}: {e}"
            logger.error(f"❌ STT: {_ultimo_error}")
            return None
        hilos = max(1, min(4, os.cpu_count() or 2))
        for solo_local in (True, False):
            try:
                if not solo_local:
                    _poner_estado("cargando", "Descargando el modelo de voz (solo la primera vez)…")
                t0 = time.perf_counter()
                _modelo = WhisperModel(MODELO, device="cpu", compute_type="int8",
                                       cpu_threads=hilos, local_files_only=solo_local)
                logger.info(f"✓ STT: modelo '{MODELO}' cargado en {int((time.perf_counter() - t0) * 1000)} ms "
                            f"({'caché local' if solo_local else 'descargado'})")
                return _modelo
            except Exception as e:
                if solo_local:
                    logger.info(f"STT: modelo '{MODELO}' no está en caché local ({type(e).__name__}); intento descargarlo")
                    continue
                _ultimo_error = f"No pude cargar/descargar el modelo '{MODELO}': {type(e).__name__}: {e}"
                logger.error(f"❌ STT: {_ultimo_error}")
        return None


def _normalizar(t: str) -> str:
    t = unicodedata.normalize("NFD", str(t).lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9ñ ]+", " ", t)).strip()


def _filtrar(texto: str) -> str:
    """Descarta alucinaciones típicas de Whisper y textos vacíos."""
    t = (texto or "").strip().strip(".…").strip()
    n = _normalizar(t)
    if len(n) < 2:
        return ""
    # Frases largas: por contención. Palabras sueltas ("musica", "risas"): solo si es TODO lo dicho,
    # para no borrar "pon musica" o "abre spotify y pon musica".
    if any(n == a or (len(a) > 12 and a in n) for a in _ALUCINACIONES):
        return ""
    # Repitió el prompt. Mínimo 20 caracteres: antes "sara" o "sara abre" (que están dentro del prompt)
    # se descartaban como si fueran eco del prompt.
    if len(n) >= 20 and n in _normalizar(PROMPT):
        return ""
    return t


_PREFIJOS_LLAMADA = ("oye", "hey", "ey", "hola", "ok", "okay", "okey")
_RE_ACTIVACION = re.compile(
    r"^\W*(?:(?:" + "|".join(_PREFIJOS_LLAMADA) + r")\W+)?"
    r"(?:" + "|".join(re.escape(p) for p in PALABRAS_ACTIVACION) + r")\b[\s,.:;!¡¿?\-–—]*(.*)$",
    re.IGNORECASE | re.DOTALL)


def _extraer_comando(texto: str) -> str:
    """Devuelve el comando SIN la palabra de activación, o "" si la frase no iba dirigida a SARA."""
    global _espera_hasta
    if not REQUERIR_ACTIVACION:
        return texto
    ahora = time.monotonic()
    m = _RE_ACTIVACION.match(texto)
    if m:
        comando = m.group(1).strip()
        if comando:
            _espera_hasta = 0.0
            return comando
        _espera_hasta = ahora + VENTANA_COMANDO_S       # dijo solo "Sara": la siguiente frase es la orden
        logger.info(f"🎙️ STT: palabra de activación; esperando la orden ({VENTANA_COMANDO_S:.0f} s)")
        return ""
    if ahora < _espera_hasta:                           # ventana abierta: se acepta sin repetir "sara"
        _espera_hasta = 0.0
        return texto
    return ""


def _a_16k(audio, muestreo: int):
    a = audio.astype(np.float32)
    if muestreo != MUESTREO_OBJETIVO and len(a) > 1:
        n = int(len(a) * MUESTREO_OBJETIVO / muestreo)
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
    return a


def _transcribir(modelo, audio, muestreo: int) -> str:
    a = _a_16k(audio, muestreo)
    with _transcripcion_lock:          # `segmentos` es un generador perezoso: el candado cubre todo el recorrido
        segmentos, _info = modelo.transcribe(
            a, language=IDIOMA, beam_size=BEAM, temperature=0.0, vad_filter=False,
            condition_on_previous_text=False, no_speech_threshold=0.6, log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4, initial_prompt=PROMPT, without_timestamps=True)
        partes = []
        for s in segmentos:
            if getattr(s, "no_speech_prob", 0.0) > 0.6 and getattr(s, "avg_logprob", 0.0) < -1.0:
                continue
            partes.append(s.text.strip())
    return _filtrar(" ".join(p for p in partes if p))


# ─── TRANSCRIBIR ARCHIVOS (notas de voz de WhatsApp: ogg/opus) ────────────
# La decodificación se hace aquí con PyAV (que faster-whisper ya instala) y NO con el decodificador interno de
# faster-whisper: éste pasa opciones que algunas versiones de PyAV ya no aceptan. Se obtiene un arreglo float32
# a 16 kHz y se transcribe con exactamente los mismos parámetros que el micrófono.

def _lista_de(r) -> list:
    return [] if r is None else (list(r) if isinstance(r, (list, tuple)) else [r])


def _decodificar_audio(ruta: str, max_s: float):
    """Archivo de audio → (float32 mono 16 kHz, segundos). Lanza si el archivo no se puede decodificar."""
    import av
    trozos, total, tope = [], 0, int(max_s * MUESTREO_OBJETIVO)
    with av.open(str(ruta)) as cont:
        flujo = next((x for x in cont.streams if x.type == "audio"), None)
        if flujo is None:
            raise ValueError("el archivo no tiene pista de audio")
        res = av.AudioResampler(format="s16", layout="mono", rate=MUESTREO_OBJETIVO)
        for frame in cont.decode(flujo):
            for f in _lista_de(res.resample(frame)):
                x = f.to_ndarray().reshape(-1)
                trozos.append(x)
                total += len(x)
            if total >= tope:
                break
        for f in _lista_de(res.resample(None)):
            trozos.append(f.to_ndarray().reshape(-1))
    if not trozos:
        raise ValueError("audio vacío")
    a = (np.concatenate(trozos)[:tope].astype(np.float32)) / 32768.0
    return a, len(a) / float(MUESTREO_OBJETIVO)


def precargar_modelo() -> bool:
    """Carga el modelo (si aún no lo está). Para llamarlo en un hilo al activar las notas de voz."""
    return _cargar_modelo() is not None


def transcribir_archivo(ruta: str, max_s: float = 60.0) -> Optional[str]:
    """Transcribe un archivo de audio. "" = no se entendió nada útil; None = no se pudo (sin modelo, sin numpy,
    archivo ilegible). Nunca lanza. No toca el estado del modo micrófono (ventana de «Sara», etc.)."""
    global _ultimo_error
    if np is None:
        _ultimo_error = "numpy no disponible"
        return None
    try:
        audio, dur = _decodificar_audio(ruta, max_s)
    except Exception as e:
        _ultimo_error = f"no pude decodificar el audio: {type(e).__name__}: {e}"
        logger.warning(f"⚠️  STT archivo: {_ultimo_error}")
        return None
    modelo = _cargar_modelo()
    if modelo is None:
        return None
    try:
        t0 = time.perf_counter()
        texto = _transcribir(modelo, audio, MUESTREO_OBJETIVO)
        logger.info(f"🎙️ STT archivo: audio {dur:.1f} s → {int((time.perf_counter() - t0) * 1000)} ms "
                    f"({'con texto' if texto else 'sin texto útil'})")
        return texto
    except Exception as e:
        _ultimo_error = f"error al transcribir: {type(e).__name__}: {e}"
        logger.exception("❌ STT archivo: excepción al transcribir")
        return None


def _trabajador_transcripcion() -> None:
    """Hilo propio: transcribir nunca frena la captura del micrófono."""
    global _transcribiendo
    while True:
        stop, audio, muestreo = _cola_seg.get()
        if stop.is_set():
            continue
        with _lock:
            _transcribiendo += 1
        try:
            modelo = _cargar_modelo()
            if modelo is None:
                continue
            t0 = time.perf_counter()
            texto = _transcribir(modelo, audio, muestreo)
            ms = int((time.perf_counter() - t0) * 1000)
            dur = len(audio) / float(muestreo)
            comando = _extraer_comando(texto) if texto else ""
            if not texto:
                logger.info(f"🎙️ STT: frase descartada (sin texto útil; audio {dur:.1f} s → {ms} ms)")
            elif not comando:
                logger.info(f"🎙️ STT: ignorado (sin 'sara' al inicio): '{texto}'")
            elif not stop.is_set():
                logger.info(f"🎙️ STT: comando '{comando}' (oído: '{texto}'; audio {dur:.1f} s → {ms} ms)")
                _emitir("stt_texto", texto=comando, crudo=texto, audio_s=round(dur, 1), tiempo_ms=ms)
        except Exception:
            logger.exception("❌ STT: excepción al transcribir")
        finally:
            with _lock:
                _transcribiendo -= 1


def _asegurar_transcriptor() -> None:
    global _hilo_transcriptor
    with _lock:
        if _hilo_transcriptor is None or not _hilo_transcriptor.is_alive():
            _hilo_transcriptor = threading.Thread(target=_trabajador_transcripcion,
                                                  name="sara-stt-transcriptor", daemon=True)
            _hilo_transcriptor.start()


# ─── HILO DE CAPTURA ──────────────────────────────────────────────────────

def _estado_por_bandera(seg: "_Segmentador") -> str:
    if _suprimido():
        return "pausado"
    if seg.grabando:
        return "grabando"
    return "transcribiendo" if _transcribiendo else "escuchando"


_MENSAJE_PAUSA = "SARA está hablando; te escucho en un momento"


def _capturar(stop: threading.Event, previo: Optional[threading.Thread]) -> None:
    if previo is not None and previo.is_alive():
        previo.join(timeout=3)                       # que la sesión anterior suelte el micrófono
    if stop.is_set():
        return
    if np is None:
        _fallar(stop, "Falta numpy (pip install numpy).")
        return
    try:
        import sounddevice as sd
    except Exception as e:
        _fallar(stop, f"Falta sounddevice o PortAudio ({type(e).__name__}: {e}). pip install sounddevice")
        return

    _poner_estado("cargando", "Cargando el modelo de voz…")
    if _cargar_modelo() is None:
        _fallar(stop, _ultimo_error or "No pude cargar el modelo de voz.")
        return
    if stop.is_set():
        return

    audio_q: "queue.Queue" = queue.Queue(maxsize=300)

    def _callback(indata, frames, tiempo, status):
        try:
            audio_q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    muestreo = MUESTREO_OBJETIVO
    try:
        try:
            flujo = sd.InputStream(samplerate=muestreo, channels=1, dtype="float32",
                                   blocksize=int(muestreo * BLOQUE_S), callback=_callback)
        except Exception:
            muestreo = int(sd.query_devices(kind="input")["default_samplerate"])
            flujo = sd.InputStream(samplerate=muestreo, channels=1, dtype="float32",
                                   blocksize=int(muestreo * BLOQUE_S), callback=_callback)
    except Exception as e:
        _fallar(stop, f"No pude abrir el micrófono ({type(e).__name__}: {e}). "
                      f"Revisa que esté conectado y permitido en Windows (Privacidad → Micrófono).")
        return

    _asegurar_transcriptor()
    seg = _Segmentador(muestreo)
    ultimo_nivel = 0.0
    try:
        with flujo:
            logger.info(f"🎙️ STT: micrófono abierto a {muestreo} Hz")
            _poner_estado("escuchando")
            while not stop.is_set():
                try:
                    bloque = audio_q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if stop.is_set():                   # apagado mientras esperaba: no emitir nada más
                    break
                if _suprimido():                    # anti-eco: SARA habla o el lector lee
                    seg.reset()
                    rms = float(np.sqrt(np.mean(np.square(bloque)))) if len(bloque) else 0.0
                    _poner_estado("pausado", _MENSAJE_PAUSA)
                else:
                    with _lock:
                        umbral = _rms_umbral(_pos)
                    rms, frase = seg.alimentar(bloque, umbral)
                    if frase is not None:
                        try:
                            _cola_seg.put_nowait((stop, frase, muestreo))
                        except queue.Full:
                            logger.warning("⚠️ STT: transcriptor ocupado; frase descartada")
                    _poner_estado(_estado_por_bandera(seg))
                ahora = time.monotonic()
                if ahora - ultimo_nivel >= PERIODO_NIVEL_S:
                    ultimo_nivel = ahora
                    _emitir("stt_nivel", nivel=round(_pos_desde_rms(rms), 1), umbral=round(_pos, 1))
    except Exception as e:
        logger.exception("❌ STT: excepción en la captura")
        _fallar(stop, f"Error en la captura de audio: {type(e).__name__}: {e}")
    finally:
        logger.info("🎙️ STT: micrófono cerrado")


# ─── API PÚBLICA ──────────────────────────────────────────────────────────

def activar(emit: Optional[Callable] = None, umbral=None) -> bool:
    """Enciende el modo voz. Retorna al instante (el modelo se carga en el hilo de captura)."""
    global _emit, _activo, _stop, _hilo
    with _lock:
        if emit is not None:
            _emit = emit
        if umbral is not None:
            set_umbral(umbral, guardar=True)
        if _activo:
            snapshot = True
        else:
            snapshot = False
            _activo = True
            stop = threading.Event()
            previo = _hilo
            _stop = stop
            h = threading.Thread(target=_capturar, name="sara-stt", daemon=True, args=(stop, previo))
            _hilo = h
    if snapshot:
        _emitir("stt_estado", **estado_actual())
        return True
    logger.info(f"🎙️ STT {STT_VERSION}: activación {'por palabra ' + str(PALABRAS_ACTIVACION) if REQUERIR_ACTIVACION else 'DESACTIVADA (atiende todo)'}")
    _poner_estado("cargando", "Iniciando…")
    h.start()
    return True


def desactivar() -> bool:
    """Apaga el modo voz (el modelo queda en memoria para reactivar rápido)."""
    global _activo, _stop, _estado, _mensaje
    with _lock:
        if not _activo:
            return False
        _activo = False
        if _stop is not None:
            _stop.set()
        _stop = None
    while True:
        try:
            _cola_seg.get_nowait()
        except queue.Empty:
            break
    _emitir("stt_estado", estado="apagado", activo=False, umbral=round(_pos, 1), mensaje="")
    with _lock:
        _estado, _mensaje = "apagado", ""
    return True


def set_umbral(valor, guardar: bool = False) -> float:
    """Posición del slider (0-100). Aplica al instante; `guardar=True` la persiste en stt_config.json."""
    global _pos
    try:
        v = max(0.0, min(100.0, float(valor)))
    except (TypeError, ValueError):
        return _pos
    with _lock:
        _pos = v
    if guardar:
        try:
            RUTA_CFG.write_text(json.dumps({"umbral": round(v, 1)}), encoding="utf-8")
        except Exception as e:
            logger.warning(f"⚠️ STT: no pude guardar el umbral ({type(e).__name__}: {e})")
    return v


def esta_activo() -> bool:
    return _activo


def diagnostico() -> str:
    """Texto para el comando /stt."""
    from importlib import metadata

    def _ver(paquete: str) -> str:
        try:
            return metadata.version(paquete)
        except Exception:
            return "NO instalado"

    try:
        import sounddevice as sd
        entradas = [d["name"] for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]
        try:
            predet = sd.query_devices(kind="input")["name"]
        except Exception:
            predet = "ninguno"
        mic = f"{len(entradas)} dispositivo(s) de entrada; predeterminado: {predet}"
    except Exception as e:
        mic = f"no disponible ({type(e).__name__}: {e})"

    with _lock:
        pos = _pos
        est = _estado if _activo else "apagado"
    return (f"Diagnóstico de voz de entrada:\n"
            f"Estado: {est} (modo voz {'activo' if _activo else 'inactivo'})\n"
            f"faster-whisper: {_ver('faster-whisper')} · ctranslate2: {_ver('ctranslate2')} · "
            f"sounddevice: {_ver('sounddevice')} · numpy: {_ver('numpy')}\n"
            f"Versión de stt.py: {STT_VERSION} · palabra de activación: "
            f"{', '.join(PALABRAS_ACTIVACION) if REQUERIR_ACTIVACION else 'DESACTIVADA (atiende todo)'}\n"
            f"Modelo: {MODELO} ({'cargado en memoria' if _modelo is not None else 'se carga al activar el modo voz'})\n"
            f"Micrófono: {mic}\n"
            f"Umbral del slider: {pos:.0f}/100 (RMS {_rms_umbral(pos):.4f})\n"
            f"Último error: {_ultimo_error or 'ninguno'}")
