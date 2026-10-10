# 📁 lector_voz.py — Lector de texto a voz de SARA (canal INDEPENDIENTE de voz.py)
#
# Qué hace: lee en voz alta CUALQUIER texto completo (sin el recorte de voz.py: sin límite de
# caracteres ni de líneas, sin "el resto lo puedes ver en pantalla").
#
# Diseño:
#   * Módulo aislado (Principio 1): si falla o no existe, SARA sigue igual. voz.py NO se modifica.
#   * Reutiliza de voz.py solo lo ya probado: VOZ_POR_DEFECTO, _generar_async (Edge-TTS), _mci y _RE_EMOJI.
#   * Alias MCI propio ("sara_lector"): nunca choca con el de la voz principal ("sara_voz").
#   * Pool de síntesis propio: un texto largo no le quita hilos a la voz principal.
#   * El texto se parte en bloques (~220 caracteres) que se sintetizan por adelantado (PRECARGA)
#     mientras suena el anterior. Se reproduce y se detiene siempre desde el MISMO hilo (poll cada 50 ms),
#     así "Detener" corta en menos de 0.1 s y no hay carreras con MCI.
#   * Los MP3 son temporales y se borran al terminar (no ensucian la caché de voz.py).
#   * Prioridad de la bandeja principal: core_bridge llama a detener("prioridad") y el lector queda
#     "pausado" con su posición guardada; reanudar() continúa desde el bloque interrumpido.
#
# Eventos que emite (emit(tipo, **datos)):
#   lector_estado(estado = leyendo | terminado | detenido | pausado | error,
#                 actual, total, puede_reanudar, [mensaje])
#
# API: leer(texto, emit) · detener(motivo) · reanudar(emit) · esta_leyendo() · hay_pendiente()

import asyncio
import concurrent.futures
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Iterator, List, Optional

logger = logging.getLogger("SARA-LECTOR")

try:
    import voz
except Exception as _e_voz:                      # sin voz.py no hay lector, pero SARA sigue viva
    voz = None
    logger.warning(f"⚠️ lector_voz: voz.py no disponible ({type(_e_voz).__name__}: {_e_voz})")

TAM_BLOQUE = 220              # caracteres por bloque de síntesis
PRECARGA = 2                  # bloques que se sintetizan por adelantado
ESPERA_SINTESIS_S = 25        # máximo esperando UN bloque
MAX_BLOQUE_REPRODUCCION_S = 600
ALIAS_MCI = "sara_lector"
TMP_DIR = Path(tempfile.gettempdir()) / "sara_lector_tmp"
CREATE_NO_WINDOW = 0x08000000

_pool = concurrent.futures.ThreadPoolExecutor(max_workers=PRECARGA, thread_name_prefix="sara-lector-syn")
_lock = threading.RLock()
_ses = {"bloques": [], "idx": 0, "activa": False, "stop": None, "motivo": "", "hilo": None, "proc": None}
_emit: Optional[Callable] = None

_RE_URL = re.compile(r"https?://\S+")
_RE_CORTE = re.compile(r"(?<=[.!?…;:])\s+")


# ─── PREPARACIÓN DEL TEXTO (sin recortes) ─────────────────────────────────

def _partir(frase: str) -> Iterator[str]:
    """Una frase gigante se corta en la coma o espacio más cercano al límite."""
    while len(frase) > TAM_BLOQUE:
        corte = max(frase.rfind(", ", 0, TAM_BLOQUE), frase.rfind(" ", 0, TAM_BLOQUE))
        if corte < 60:
            corte = TAM_BLOQUE
        yield frase[:corte].strip()
        frase = frase[corte:].strip(" ,")
    if frase:
        yield frase


def _preparar(texto: str) -> List[str]:
    t = str(texto or "")
    t = _RE_URL.sub(" un enlace ", t)
    if voz is not None and hasattr(voz, "_RE_EMOJI"):
        t = voz._RE_EMOJI.sub(" ", t)
    t = t.replace("%", " por ciento")
    t = re.sub(r"[*`#>|]+", " ", t).replace("_", " ")

    frases: List[str] = []
    for linea in t.splitlines():
        linea = re.sub(r"\s+", " ", linea).strip(" -•\t")
        if not linea:
            continue
        if linea[-1] not in ".!?…;:,":
            linea += "."                          # pausa natural al final de cada línea
        frases.extend(f for f in _RE_CORTE.split(linea) if f.strip())

    bloques: List[str] = []
    actual = ""
    for f in frases:
        for p in _partir(f):
            if actual and len(actual) + 1 + len(p) > TAM_BLOQUE:
                bloques.append(actual)
                actual = p
            else:
                actual = f"{actual} {p}".strip()
    if actual:
        bloques.append(actual)
    return bloques


# ─── SÍNTESIS Y REPRODUCCIÓN ──────────────────────────────────────────────

def _borrar(ruta: Optional[str]) -> None:
    if ruta:
        try:
            Path(ruta).unlink(missing_ok=True)
        except Exception:
            pass


def _borrar_futuro(fut) -> None:
    try:
        _borrar(fut.result())
    except Exception:
        pass


def _sintetizar(texto: str, voz_name: str) -> Optional[str]:
    if voz is None:
        return None
    ruta = None
    try:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        ruta = TMP_DIR / f"{uuid.uuid4().hex}.mp3"
        t0 = time.perf_counter()
        asyncio.run(voz._generar_async(texto, str(ruta), voz_name))
        if ruta.exists() and ruta.stat().st_size > 0:
            logger.info(f"🔊 lector: {len(texto)} car. → síntesis {int((time.perf_counter() - t0) * 1000)} ms")
            return str(ruta)
    except Exception as e:
        logger.warning(f"⚠️ lector: síntesis falló ({type(e).__name__}: {e})")
    _borrar(str(ruta) if ruta else None)
    return None


def _esperar(fut, stop: threading.Event) -> Optional[str]:
    """Espera el MP3 de un bloque sin dejar de atender 'Detener'."""
    fin = time.monotonic() + ESPERA_SINTESIS_S
    while not stop.is_set() and time.monotonic() < fin:
        try:
            return fut.result(timeout=0.1)
        except concurrent.futures.TimeoutError:
            continue
        except Exception:
            return None
    fut.add_done_callback(_borrar_futuro)          # llegó tarde o hubo stop: no dejar el archivo huérfano
    return None


def _reproducir(ruta: str, stop: threading.Event) -> bool:
    """MCI: abrir, reproducir y vigilar 'stop' desde este mismo hilo (corte en ≤ 50 ms)."""
    if voz is None or os.name != "nt":
        return False
    try:
        try:
            voz._mci(f"close {ALIAS_MCI}")
        except Exception:
            pass
        voz._mci(f'open "{ruta}" type mpegvideo alias {ALIAS_MCI}')
        try:
            voz._mci(f"play {ALIAS_MCI}")
            limite = time.monotonic() + MAX_BLOQUE_REPRODUCCION_S
            while not stop.is_set() and time.monotonic() < limite:
                if voz._mci(f"status {ALIAS_MCI} mode") == "stopped":
                    break
                time.sleep(0.05)
            try:
                voz._mci(f"stop {ALIAS_MCI}")
            except Exception:
                pass
        finally:
            try:
                voz._mci(f"close {ALIAS_MCI}")
            except Exception:
                pass
        return True
    except Exception as e:
        logger.error(f"❌ lector: reproducción: {e}")
        return False


def _sapi(texto: str, stop: threading.Event) -> bool:
    """Respaldo offline (Windows SAPI). Proceso propio para poder matarlo al detener."""
    if os.name != "nt":
        return False
    ps = ("Add-Type -AssemblyName System.Speech; "
          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          "try { $s.SelectVoiceByHints('NotSet','NotSet',0,[cultureinfo]'es-MX') } catch { "
          "try { $s.SelectVoiceByHints('NotSet','NotSet',0,[cultureinfo]'es-ES') } catch {} }; "
          "$s.Speak($env:SARA_TTS_TEXT)")
    proc = None
    try:
        proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", ps],
                                env=dict(os.environ, SARA_TTS_TEXT=texto),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=CREATE_NO_WINDOW)
        with _lock:
            if _ses["stop"] is stop:
                _ses["proc"] = proc
        while proc.poll() is None:
            if stop.is_set():
                proc.kill()
                break
            time.sleep(0.1)
        return True
    except Exception as e:
        logger.error(f"❌ lector: SAPI: {type(e).__name__}: {e}")
        return False
    finally:
        with _lock:
            if _ses["proc"] is proc:
                _ses["proc"] = None


# ─── EVENTOS ──────────────────────────────────────────────────────────────

def _emitir(estado: str, **extra) -> None:
    if _emit is None:
        return
    try:
        total = len(_ses["bloques"])
        idx = _ses["idx"]
        _emit("lector_estado", estado=estado, actual=min(idx, total), total=total,
              puede_reanudar=bool(estado in ("detenido", "pausado") and idx < total), **extra)
    except Exception as e:
        logger.debug(f"emit(lector_estado) falló: {e}")


def _emitir_vigente(stop: threading.Event, estado: str, **extra) -> None:
    if _ses["stop"] is stop:
        _emitir(estado, **extra)


# ─── HILO LECTOR ──────────────────────────────────────────────────────────

def _trabajar(bloques: List[str], inicio: int, voz_name: str, stop: threading.Event, previo) -> None:
    if previo is not None and previo.is_alive():
        previo.join(timeout=3)                       # que el anterior suelte el alias MCI
    futuros: dict = {}

    def pedir(i: int) -> None:
        if i < len(bloques) and i not in futuros:
            futuros[i] = _pool.submit(_sintetizar, bloques[i], voz_name)

    error = ""
    i = inicio
    try:
        for k in range(inicio, inicio + PRECARGA):
            pedir(k)
        while i < len(bloques) and not stop.is_set():
            with _lock:
                if _ses["stop"] is stop:
                    _ses["idx"] = i
            pedir(i + PRECARGA)
            _emitir_vigente(stop, "leyendo")
            ruta = _esperar(futuros.pop(i), stop)
            if stop.is_set():
                _borrar(ruta)
                break
            ok = False
            if ruta:
                try:
                    ok = _reproducir(ruta, stop)
                finally:
                    _borrar(ruta)
            if stop.is_set():
                break
            if not ok:
                logger.warning("⚠️ lector: voz neuronal no disponible; uso SAPI offline")
                ok = _sapi(bloques[i], stop)
                if not ok and not stop.is_set():
                    error = "No pude generar ni reproducir audio (revisa internet / edge-tts / log)."
                    break
            i += 1
    except Exception as e:
        logger.exception("❌ excepción en el hilo del lector")
        error = f"{type(e).__name__}: {e}"
    finally:
        for f in futuros.values():
            f.cancel()
            f.add_done_callback(_borrar_futuro)
        with _lock:
            vigente = _ses["stop"] is stop
            if vigente and not stop.is_set():
                _ses["activa"] = False
                if not error:
                    _ses["idx"] = len(bloques)       # terminó: nada que reanudar
        if vigente and not stop.is_set():
            _emitir("error" if error else "terminado", **({"mensaje": error} if error else {}))


def _lanzar(inicio: int, voz_name: Optional[str]) -> None:
    with _lock:
        stop = threading.Event()
        previo = _ses["hilo"]
        bloques = list(_ses["bloques"])
        _ses.update(activa=True, stop=stop, motivo="", proc=None, idx=inicio)
        h = threading.Thread(target=_trabajar, name="sara-lector", daemon=True,
                             args=(bloques, inicio, voz_name or voz.VOZ_POR_DEFECTO, stop, previo))
        _ses["hilo"] = h
    h.start()


def _parar_sesion(motivo: str) -> bool:
    with _lock:
        stop = _ses["stop"]
        if not (_ses["activa"] and stop is not None and not stop.is_set()):
            return False
        _ses["motivo"] = motivo
        _ses["activa"] = False
        stop.set()
        proc = _ses["proc"]
    if proc is not None:
        try:
            proc.kill()
        except Exception:
            pass
    return True


# ─── API PÚBLICA ──────────────────────────────────────────────────────────

def leer(texto: str, emit: Optional[Callable] = None, voz_name: Optional[str] = None) -> dict:
    """Empieza a leer `texto` completo. Retorna al instante (todo ocurre en un hilo)."""
    global _emit
    if emit is not None:
        _emit = emit
    if voz is None:
        _emitir("error", mensaje="El módulo de voz (voz.py) no está disponible.")
        return {"ok": False, "mensaje": "voz no disponible"}
    bloques = _preparar(texto)
    if not bloques:
        _emitir("error", mensaje="No hay texto que leer.")
        return {"ok": False, "mensaje": "sin texto"}
    _parar_sesion("reemplazo")                       # un texto nuevo reemplaza al anterior (sin aviso)
    with _lock:
        _ses["bloques"] = bloques
        _ses["idx"] = 0
    _lanzar(0, voz_name)
    return {"ok": True, "bloques": len(bloques)}


def detener(motivo: str = "usuario") -> bool:
    """motivo='usuario' (botón rojo) o 'prioridad' (la bandeja principal reclamó el canal).
    Retorna True si había una lectura activa."""
    if not _parar_sesion(motivo):
        return False
    _emitir("pausado" if motivo == "prioridad" else "detenido")
    return True


def reanudar(emit: Optional[Callable] = None) -> bool:
    """Continúa desde el bloque que se interrumpió."""
    global _emit
    if emit is not None:
        _emit = emit
    with _lock:
        if _ses["activa"] or _ses["idx"] >= len(_ses["bloques"]):
            pendiente = False
        else:
            pendiente = True
            inicio = _ses["idx"]
    if not pendiente:
        _emitir("error", mensaje="No hay ninguna lectura pendiente por reanudar.")
        return False
    _lanzar(inicio, None)
    return True


def esta_leyendo() -> bool:
    return bool(_ses["activa"])


def hay_pendiente() -> bool:
    return (not _ses["activa"]) and _ses["idx"] < len(_ses["bloques"])


def _limpiar_temporales() -> None:
    """Al importar: borra MP3 huérfanos de ejecuciones anteriores (> 1 h)."""
    try:
        limite = time.time() - 3600
        for p in TMP_DIR.glob("*.mp3"):
            if p.stat().st_mtime < limite:
                p.unlink(missing_ok=True)
    except Exception:
        pass


_limpiar_temporales()
