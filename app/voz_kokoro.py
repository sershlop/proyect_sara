# 📁 voz_kokoro.py — Motor de voz NEURONAL LOCAL de SARA (Kokoro-82M vía kokoro-onnx)       v1 · 14 oct 2026
#
# Sin internet, sin claves, sin cuentas. Calidad claramente superior a las voces «medium» de Piper.
# Misma API que voz_piper.py (voz.py los trata igual): disponible · motivo · conocida · instalada · genero · cultura
#   · listar · asegurar · sintetizar_wav · precargar · carpeta_voces
#
# Archivos (se bajan UNA vez a <runtime>\kokoro\ o <app>\kokoro\; se fuerza con SARA_KOKORO_DIR):
#   kokoro-v1.0.onnx (~310 MB, mejor calidad)  ·  kokoro-v1.0.int8.onnx (~88 MB, más rápido y ligero)  ·  voices-v1.0.bin (~28 MB)
#   Elegir modelo: SARA_KOKORO_MODELO=fp32 (por defecto) | int8 (equipos lentos) | fp16
#   Velocidad de habla: SARA_KOKORO_VELOCIDAD=1.0 (0.8 más lento, 1.2 más rápido)
#   Hilos de CPU: SARA_KOKORO_HILOS (por defecto: núcleos físicos aprox., máx. 6)
#
# Licencias: el modelo es Apache-2.0. kokoro-onnx (MIT) trae phonemizer-fork (GPL-3.0): el instalador lo baja de PyPI
#   en el equipo del usuario; NO lo metas dentro del zip.
#
# Línea de comandos:  python voz_kokoro.py [--instalar] [--listar] [--probar kokoro:ef_dora]

import logging
import os
import sys
import threading
import time
import urllib.request
import wave
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("SARA-KOKORO")

PREFIJO = "kokoro:"
VOZ_BASE = "kokoro:ef_dora"
_BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
_MODELOS = {"fp32": "kokoro-v1.0.onnx", "fp16": "kokoro-v1.0.fp16.onnx", "int8": "kokoro-v1.0.int8.onnx"}
_ARCH_VOCES = "voices-v1.0.bin"
_MIN_MODELO = 50_000_000
_MIN_VOCES = 5_000_000
TIMEOUT_RED_S = 30

# Voces en español del paquete v1.0 (ef = femenina, em = masculina). «em_santa» es de fantasía: no se lista.
CATALOGO: List[dict] = [
    {"id": "kokoro:ef_dora", "voz": "ef_dora", "nombre": "Dora", "genero": "F", "cultura": "es-ES",
     "region": "Español neutro", "estilo": "Femenina clara y cálida · sin internet", "peso": "~310 MB la primera vez"},
    {"id": "kokoro:em_alex", "voz": "em_alex", "nombre": "Alex", "genero": "M", "cultura": "es-ES",
     "region": "Español neutro", "estilo": "Masculina serena, estilo asistente · sin internet", "peso": "~310 MB la primera vez"},
]

_ultimo_error = ""
_modulo = None                       # el paquete kokoro_onnx (None = sin probar; False = no instalado)
_motor = None                        # instancia Kokoro (una sola en RAM)
_lock_carga = threading.Lock()
_lock_sintesis = threading.Lock()    # el modelo no se comparte entre hilos: se sintetiza de uno en uno
_lock_descarga = threading.Lock()


def _kokoro():
    global _modulo, _ultimo_error
    if _modulo is None:
        try:
            import kokoro_onnx
            _ = kokoro_onnx.Kokoro
            _modulo = kokoro_onnx
        except Exception as e:
            _modulo = False
            _ultimo_error = f"kokoro-onnx no está instalado o no carga ({type(e).__name__}: {e})"
            logger.info(f"ℹ️ {_ultimo_error}")
    return _modulo or None


def disponible() -> bool:
    return _kokoro() is not None


def motivo() -> str:
    return _ultimo_error


def carpeta_voces() -> Path:
    env = os.getenv("SARA_KOKORO_DIR")
    if env:
        return Path(env)
    app = Path(__file__).resolve().parent
    runtime = app.parent / "runtime"
    return (runtime if runtime.is_dir() else app) / "kokoro"


def _variante() -> str:
    v = os.getenv("SARA_KOKORO_MODELO", "fp32").strip().lower()
    return v if v in _MODELOS else "fp32"


def _rutas():
    d = carpeta_voces()
    return d / _MODELOS[_variante()], d / _ARCH_VOCES


def _info(voz_id: str) -> Optional[dict]:
    return next((v for v in CATALOGO if v["id"] == voz_id), None)


def conocida(voz_id: str) -> bool:
    return isinstance(voz_id, str) and voz_id.startswith(PREFIJO) and _info(voz_id) is not None


def genero(voz_id: str) -> str:
    v = _info(voz_id)
    return v["genero"] if v else "S"


def cultura(voz_id: str) -> str:
    v = _info(voz_id)
    return v["cultura"] if v else ""


def instalada(voz_id: str = "") -> bool:
    """True si el modelo y las voces están descargados (valen para todas las voces Kokoro)."""
    modelo, voces = _rutas()
    try:
        return modelo.stat().st_size > _MIN_MODELO and voces.stat().st_size > _MIN_VOCES
    except OSError:
        return False


def listar() -> List[dict]:
    ok = instalada()
    return [dict(v, instalada=ok) for v in CATALOGO]


# ─── DESCARGA (con reanudación) ───────────────────────────────────────────

def _descargar(url: str, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".part")
    hecho = tmp.stat().st_size if tmp.exists() else 0
    cab = {"User-Agent": "SARA/2.0"}
    if hecho:
        cab["Range"] = f"bytes={hecho}-"
    req = urllib.request.Request(url, headers=cab)
    with urllib.request.urlopen(req, timeout=TIMEOUT_RED_S) as r:
        if hecho and r.status != 206:               # el servidor no reanuda: empezar de cero
            hecho = 0
        with open(tmp, "ab" if hecho else "wb") as f:
            ultimo = time.monotonic()
            while True:
                bloque = r.read(1 << 20)
                if not bloque:
                    break
                f.write(bloque)
                hecho += len(bloque)
                if time.monotonic() - ultimo > 15:
                    logger.info(f"⬇️ {destino.name}: {hecho // (1 << 20)} MB…")
                    ultimo = time.monotonic()
    os.replace(tmp, destino)


def asegurar(voz_id: str = VOZ_BASE) -> bool:
    """Descarga modelo y voces si faltan (bloquea). True si quedan listos."""
    global _ultimo_error
    if instalada():
        return True
    with _lock_descarga:
        if instalada():
            return True
        modelo, voces = _rutas()
        try:
            t0 = time.perf_counter()
            logger.info(f"⬇️ Descargando Kokoro ({modelo.name}) — solo esta vez…")
            if not (voces.exists() and voces.stat().st_size > _MIN_VOCES):
                _descargar(_BASE_URL + _ARCH_VOCES, voces)
            if not (modelo.exists() and modelo.stat().st_size > _MIN_MODELO):
                _descargar(_BASE_URL + modelo.name, modelo)
            if not instalada():
                raise RuntimeError("la descarga quedó incompleta")
            logger.info(f"✅ Kokoro listo ({int(time.perf_counter() - t0)} s)")
            return True
        except Exception as e:
            _ultimo_error = f"no pude descargar Kokoro: {type(e).__name__}: {e}"
            logger.error(f"❌ {_ultimo_error}")          # el .part se conserva para reanudar
            return False


# ─── SÍNTESIS ─────────────────────────────────────────────────────────────

def _hilos() -> int:
    try:
        return max(1, int(os.getenv("SARA_KOKORO_HILOS", "")))
    except ValueError:
        return max(2, min(6, (os.cpu_count() or 4) // 2 + 1))


def _cargar():
    global _motor
    with _lock_carga:
        if _motor is None:
            modelo, voces = _rutas()
            t0 = time.perf_counter()
            k = _kokoro()
            try:                                        # sesión ONNX con hilos fijos: más rápido y sin saturar la CPU
                import onnxruntime as ort
                so = ort.SessionOptions()
                so.intra_op_num_threads = _hilos()
                so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                sesion = ort.InferenceSession(str(modelo), sess_options=so, providers=["CPUExecutionProvider"])
                _motor = k.Kokoro.from_session(sesion, str(voces))
            except Exception as e:                      # versión de kokoro-onnx sin from_session: carga estándar
                logger.debug(f"kokoro: from_session no disponible ({e}); uso la carga estándar")
                _motor = k.Kokoro(str(modelo), str(voces))
            logger.info(f"🔊 Kokoro: modelo cargado en {int((time.perf_counter() - t0) * 1000)} ms")
        return _motor


def precargar(voz_id: str = VOZ_BASE) -> None:
    """Carga el modelo y hace una síntesis mínima en segundo plano: la primera frase real no paga el arranque."""
    if not disponible() or not instalada():
        return

    def _run():
        try:
            m = _cargar()
            with _lock_sintesis:
                m.create("Hola.", voice="ef_dora", speed=1.0, lang="es")
        except Exception as e:
            logger.debug(f"kokoro: precarga falló: {e}")
    threading.Thread(target=_run, name="sara-kokoro-warm", daemon=True).start()


def sintetizar_wav(texto: str, voz_id: str, destino) -> bool:
    """Texto → WAV mono 16 bit en `destino`. False si falla (motivo() dice por qué). No descarga nada."""
    global _ultimo_error
    v = _info(voz_id)
    if not v:
        _ultimo_error = f"voz Kokoro desconocida: {voz_id}"
        return False
    if not disponible():
        return False
    if not instalada():
        _ultimo_error = "Kokoro no está descargado todavía"
        return False
    destino = Path(destino)
    tmp = destino.with_suffix(f".{threading.get_ident()}.tmp")
    try:
        import numpy as np
        m = _cargar()
        try:
            vel = float(os.getenv("SARA_KOKORO_VELOCIDAD", "1.0"))
        except ValueError:
            vel = 1.0
        with _lock_sintesis:
            muestras, sr = m.create(texto, voice=v["voz"], speed=vel, lang="es")
        pcm = (np.clip(np.asarray(muestras, dtype=np.float32), -1.0, 1.0) * 32767).astype(np.int16)
        if pcm.size < 100:
            raise RuntimeError("Kokoro devolvió audio vacío")
        with wave.open(str(tmp), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(sr))
            wf.writeframes(pcm.tobytes())
        os.replace(tmp, destino)
        return True
    except Exception as e:
        _ultimo_error = f"Kokoro: {type(e).__name__}: {str(e)[:160]}"
        logger.error(f"❌ {_ultimo_error}")
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


# ─── LÍNEA DE COMANDOS ────────────────────────────────────────────────────

def _main(argv: List[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "--listar" in argv:
        for v in listar():
            print(f"[{'OK' if v['instalada'] else '--'}] {v['id']:<18} {v['nombre']:<8} {v['region']}")
        return 0
    if not disponible():
        print(f"[XX] {motivo()}\n     Instálalo con:  pip install kokoro-onnx")
        return 2
    if "--instalar" in argv:
        ok = asegurar()
        print("[OK] Kokoro listo" if ok else f"[XX] {motivo()}")
        return 0 if ok else 1
    if "--probar" in argv:
        i = argv.index("--probar")
        vid = argv[i + 1] if i + 1 < len(argv) else VOZ_BASE
        t0 = time.perf_counter()
        ok = sintetizar_wav("Hola, soy Sara. Si me escuchas, mi voz funciona correctamente.", vid, "prueba_kokoro.wav")
        print(f"[OK] prueba_kokoro.wav ({int((time.perf_counter() - t0) * 1000)} ms)" if ok else f"[XX] {motivo()}")
        return 0 if ok else 1
    print("Uso: python voz_kokoro.py [--instalar] [--listar] [--probar kokoro:ef_dora]")
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
