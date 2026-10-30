# 📁 voz_piper.py — Motor de voz NEURONAL OFFLINE de SARA (Piper)            v2 · 14 oct 2026
#
# v2: se RETIRARON las voces «medium» (Ald, Davefx, Sharvard). Queda la voz en calidad ALTA es_MX-claude-high (~63 MB).
#     `python voz_piper.py --limpiar` borra del disco las voces retiradas.
#
# Por qué existe: desde el 10 oct 2026 Microsoft rechaza (403) a la librería edge-tts (issue #490 de rany2/edge-tts,
#   sin arreglo oficial todavía). Piper es una voz neuronal (VITS + onnxruntime) que corre EN EL PC: sin internet,
#   sin cuentas, sin servicio de terceros que pueda cambiar. Encaja con el principio «local primero».
#
# Diseño (Principio 1: módulo aislado; Principio 3: degradación elegante):
#   * Si `piper-tts` no está instalado, este módulo se importa igual: disponible() → False y voz.py sigue con
#     Edge-TTS / voz de Windows. Nunca lanza excepciones hacia fuera: devuelve False/None y guarda el motivo.
#   * Las voces (.onnx + .onnx.json, ~60 MB las «medium») viven en  <runtime>\piper_voces\  (paquete) o
#     <app>\piper_voces\  (desarrollo). Se puede forzar con la variable de entorno SARA_PIPER_DIR.
#   * Cualquier voz española que copies a esa carpeta (es_XX-nombre-calidad.onnx + .onnx.json) aparece sola en el menú.
#   * Solo la librería estándar + piper (que trae onnxruntime y numpy). Sin huggingface_hub.
#   * sintetizar_wav() NUNCA descarga nada (no debe haber esperas dentro de una frase): la descarga la hacen
#     asegurar() (al elegir la voz en el menú) o  `python voz_piper.py --instalar`  (instalador).
#
# Licencias: piper-tts es GPL-3.0 (el instalador lo baja de PyPI en el equipo del usuario; NO lo metas dentro del zip).
#   Cada voz tiene su propia licencia: revísala en su tarjeta de https://huggingface.co/rhasspy/piper-voices
#
# API: disponible() · motivo() · conocida(id) · instalada(id) · genero(id) · cultura(id) · listar()
#      asegurar(id) · sintetizar_wav(texto, id, destino) · precargar(id) · carpeta_voces()
# Línea de comandos:  python voz_piper.py [--instalar] [--listar] [id ...]

import logging
import os
import sys
import threading
import time
import urllib.request
import wave
import gc
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("SARA-PIPER")

try:
    import config as _cfg
except Exception:
    _cfg = None


def _c(nombre: str, defecto):
    return getattr(_cfg, nombre, defecto) if _cfg is not None else defecto


PREFIJO = "piper:"
VOZ_BASE = "piper:es_MX-claude-high"                # la que instala el instalador por defecto
_RETIRADAS = {"es_MX-ald-medium", "es_ES-davefx-medium", "es_ES-sharvard-medium"}
_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{ruta}/{modelo}{ext}?download=true"
_MIN_ONNX_BYTES = 5_000_000                         # un .onnx de voz «low» ya pesa más; menos = descarga truncada
TIMEOUT_RED_S = 30

# Catálogo cerrado (verificado: existen en rhasspy/piper-voices). Añadir una voz = añadir una línea.
#   ruta = carpeta dentro del repositorio · speaker = índice si el modelo tiene varias voces (None = única)
CATALOGO: List[dict] = [
    {"id": "piper:es_MX-claude-high", "modelo": "es_MX-claude-high", "ruta": "es/es_MX/claude/high", "speaker": None,
     "nombre": "Claude", "genero": "S", "cultura": "es-MX", "region": "México", "estilo": "Calidad alta · sin internet"},
]

_ultimo_error = ""
_modulo = None                                       # el paquete `piper` (None = no probado; False = no instalado)
_cargadas: Dict[str, object] = {}                    # modelo → PiperVoice (solo se conserva el último usado: RAM)
_lock_carga = threading.Lock()
_lock_uso = threading.RLock()
_locks_descarga: Dict[str, threading.Lock] = {}
_timer_liberacion: Optional[threading.Timer] = None
_generacion_liberacion = 0


# ─── DISPONIBILIDAD ───────────────────────────────────────────────────────

def _piper():
    """El paquete `piper` o None (el motivo queda en motivo())."""
    global _modulo, _ultimo_error
    if _modulo is None:
        try:
            import piper
            _ = piper.PiperVoice, piper.SynthesisConfig
            _modulo = piper
        except Exception as e:
            _modulo = False
            _ultimo_error = f"piper-tts no está instalado o no carga ({type(e).__name__}: {e})"
            logger.info(f"ℹ️ {_ultimo_error}")
    return _modulo or None


def disponible() -> bool:
    """True si el motor (piper-tts) se puede usar. No dice si hay alguna voz descargada: ver instalada()."""
    return _piper() is not None


def motivo() -> str:
    return _ultimo_error


def carpeta_voces() -> Path:
    env = os.getenv("SARA_PIPER_DIR")
    if env:
        return Path(env)
    app = Path(__file__).resolve().parent
    runtime = app.parent / "runtime"                 # paquete: C:\SARA\app\voz_piper.py → C:\SARA\runtime
    return (runtime if runtime.is_dir() else app) / "piper_voces"


# ─── CATÁLOGO + VOCES LOCALES ─────────────────────────────────────────────

def _locales() -> List[dict]:
    """Voces españolas que el usuario copió a mano a la carpeta (es_XX-nombre-calidad.onnx + .onnx.json)."""
    salida = []
    try:
        conocidos = {c["modelo"] for c in CATALOGO}
        for onnx in sorted(carpeta_voces().glob("es_*.onnx")):
            modelo = onnx.stem
            if modelo in conocidos or modelo in _RETIRADAS or not Path(str(onnx) + ".json").exists():
                continue
            cultura = modelo[:5].replace("_", "-")
            nombre = modelo.split("-")[1].replace("_", " ").title() if "-" in modelo else modelo
            salida.append({"id": PREFIJO + modelo, "modelo": modelo, "ruta": "", "speaker": None, "nombre": nombre,
                           "genero": "S", "cultura": cultura, "region": cultura, "estilo": "Añadida por ti · sin internet"})
    except Exception as e:
        logger.debug(f"piper: no pude escanear voces locales: {e}")
    return salida


def _info(voz_id: str) -> Optional[dict]:
    for v in CATALOGO + _locales():
        if v["id"] == voz_id:
            return v
    return None


def conocida(voz_id: str) -> bool:
    return isinstance(voz_id, str) and voz_id.startswith(PREFIJO) and _info(voz_id) is not None


def genero(voz_id: str) -> str:
    v = _info(voz_id)
    return v["genero"] if v else "S"


def cultura(voz_id: str) -> str:
    v = _info(voz_id)
    return v["cultura"] if v else ""


def _rutas(v: dict):
    base = carpeta_voces() / v["modelo"]
    return Path(str(base) + ".onnx"), Path(str(base) + ".onnx.json")


def instalada(voz_id: str) -> bool:
    v = _info(voz_id)
    if not v:
        return False
    onnx, cfg = _rutas(v)
    try:
        return onnx.stat().st_size > _MIN_ONNX_BYTES and cfg.stat().st_size > 0
    except OSError:
        return False


def listar() -> List[dict]:
    """Catálogo + voces locales, con `instalada`. Es lo que ve el menú."""
    return [dict(v, instalada=instalada(v["id"])) for v in CATALOGO + _locales()]


# ─── DESCARGA ─────────────────────────────────────────────────────────────

def _descargar(url: str, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "SARA/2.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_RED_S) as r, open(tmp, "wb") as f:
        while True:
            bloque = r.read(1 << 20)
            if not bloque:
                break
            f.write(bloque)
    os.replace(tmp, destino)


def asegurar(voz_id: str) -> bool:
    """Descarga la voz si falta (bloquea). True si queda lista para usar. Seguro llamarla desde varios hilos."""
    global _ultimo_error
    v = _info(voz_id)
    if not v:
        _ultimo_error = f"voz Piper desconocida: {voz_id}"
        return False
    if instalada(voz_id):
        return True
    if not v["ruta"]:
        _ultimo_error = f"a la voz local {v['modelo']} le falta su .onnx o su .onnx.json en {carpeta_voces()}"
        return False
    with _locks_descarga.setdefault(v["modelo"], threading.Lock()):
        if instalada(voz_id):                        # otro hilo la acaba de bajar
            return True
        onnx, cfg = _rutas(v)
        try:
            t0 = time.perf_counter()
            logger.info(f"⬇️ Descargando la voz Piper {v['modelo']} (una sola vez)…")
            _descargar(_URL.format(ruta=v["ruta"], modelo=v["modelo"], ext=".onnx.json"), cfg)
            _descargar(_URL.format(ruta=v["ruta"], modelo=v["modelo"], ext=".onnx"), onnx)
            if not instalada(voz_id):
                raise RuntimeError("el archivo descargado está incompleto")
            logger.info(f"✅ Voz Piper {v['modelo']} lista ({int(time.perf_counter() - t0)} s)")
            return True
        except Exception as e:
            _ultimo_error = f"no pude descargar la voz {v['modelo']}: {type(e).__name__}: {e}"
            logger.error(f"❌ {_ultimo_error}")
            for p in (onnx, cfg):                    # no dejar a medias un archivo que parezca bueno
                try:
                    if p.exists() and p.stat().st_size <= (_MIN_ONNX_BYTES if p == onnx else 0):
                        p.unlink()
                except OSError:
                    pass
            return False


# ─── SÍNTESIS ─────────────────────────────────────────────────────────────

def _cargar(v: dict):
    with _lock_carga:
        voz = _cargadas.get(v["modelo"])
        if voz is None:
            onnx, cfg = _rutas(v)
            t0 = time.perf_counter()
            voz = _piper().PiperVoice.load(str(onnx), config_path=str(cfg))
            _cargadas.clear()                        # una sola voz en RAM (cada modelo ocupa ~100-200 MB cargado)
            _cargadas[v["modelo"]] = voz
            logger.info(f"🔊 Piper: modelo {v['modelo']} cargado en {int((time.perf_counter() - t0) * 1000)} ms")
        return voz


def _liberar_si_ociosa(generacion: int) -> None:
    global _timer_liberacion
    with _lock_uso:
        with _lock_carga:
            if generacion != _generacion_liberacion:
                return
            _timer_liberacion = None
            if not _cargadas:
                return
            _cargadas.clear()
        gc.collect()
        logger.info("🔊 Piper liberado de la RAM tras inactividad")


def _programar_liberacion() -> None:
    global _timer_liberacion, _generacion_liberacion
    try:
        minutos = max(0.0, float(_c("PIPER_LIBERAR_MIN", 10)))
    except (TypeError, ValueError):
        minutos = 10.0
    with _lock_carga:
        _generacion_liberacion += 1
        if _timer_liberacion is not None:
            _timer_liberacion.cancel()
            _timer_liberacion = None
        if minutos <= 0 or not _cargadas:
            return
        _timer_liberacion = threading.Timer(minutos * 60, _liberar_si_ociosa,
                                             args=(_generacion_liberacion,))
        _timer_liberacion.name = "sara-piper-idle"
        _timer_liberacion.daemon = True
        _timer_liberacion.start()


def liberar() -> bool:
    global _timer_liberacion, _generacion_liberacion
    with _lock_uso:
        with _lock_carga:
            _generacion_liberacion += 1
            if _timer_liberacion is not None:
                _timer_liberacion.cancel()
                _timer_liberacion = None
            habia_modelo = bool(_cargadas)
            _cargadas.clear()
        if habia_modelo:
            gc.collect()
            logger.info("🔊 Piper liberado de la RAM")
        return habia_modelo


def precargar(voz_id: str) -> None:
    """Carga el modelo en segundo plano para que la primera frase no pague el arranque."""
    v = _info(voz_id)
    if not v or not disponible() or not instalada(voz_id):
        return

    def _run():
        try:
            with _lock_uso:
                _cargar(v)
                _programar_liberacion()
        except Exception as e:
            logger.debug(f"piper: precarga falló: {e}")
    threading.Thread(target=_run, name="sara-piper-warm", daemon=True).start()


def sintetizar_wav(texto: str, voz_id: str, destino) -> bool:
    """Texto → WAV (mono, 16 bit) en `destino`. False si algo falla (el motivo queda en motivo()). No descarga nada."""
    global _ultimo_error
    v = _info(voz_id)
    if not v:
        _ultimo_error = f"voz Piper desconocida: {voz_id}"
        return False
    if not disponible():
        return False
    if not instalada(voz_id):
        _ultimo_error = f"la voz {v['modelo']} no está descargada"
        return False
    destino = Path(destino)
    tmp = destino.with_suffix(f".{threading.get_ident()}.tmp")
    try:
        with _lock_uso:
            voz = _cargar(v)
            _programar_liberacion()
            cfg = None
            if v["speaker"] is not None or os.getenv("SARA_PIPER_VELOCIDAD"):
                kw = {}
                if v["speaker"] is not None:
                    kw["speaker_id"] = v["speaker"]
                if os.getenv("SARA_PIPER_VELOCIDAD"):    # length_scale: <1 más rápido, >1 más lento (por defecto, el de la voz)
                    kw["length_scale"] = float(os.getenv("SARA_PIPER_VELOCIDAD"))
                cfg = _piper().SynthesisConfig(**kw)
            with wave.open(str(tmp), "wb") as wf:
                voz.synthesize_wav(texto, wf, syn_config=cfg)
            _programar_liberacion()
        if not tmp.exists() or tmp.stat().st_size <= 44:
            raise RuntimeError("Piper devolvió un WAV vacío")
        os.replace(tmp, destino)
        return True
    except Exception as e:
        _ultimo_error = f"Piper: {type(e).__name__}: {str(e)[:160]}"
        logger.error(f"❌ {_ultimo_error}")
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


# ─── LÍNEA DE COMANDOS (la usa el instalador) ─────────────────────────────

def _main(argv: List[str]) -> int:
    try:                                              # consola de Windows (cp1252): nunca fallar por una tilde o un emoji
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "--limpiar" in argv:
        for m in sorted(_RETIRADAS):
            for ext in (".onnx", ".onnx.json"):
                p = carpeta_voces() / (m + ext)
                try:
                    if p.exists():
                        p.unlink()
                        print(f"[OK] borrado {p.name}")
                except OSError as e:
                    print(f"[XX] no pude borrar {p.name}: {e}")
        return 0
    if "--listar" in argv:
        for v in listar():
            print(f"[{'OK' if v['instalada'] else '--'}] {v['id']:<36} {v['nombre']:<12} {v['region']}")
        return 0
    if not disponible():
        print(f"[XX] {motivo()}\n     Instalalo con:  pip install piper-tts")
        return 2
    ids = [a if a.startswith(PREFIJO) else PREFIJO + a for a in argv if not a.startswith("--")]
    if "--instalar" in argv and not ids:
        ids = [VOZ_BASE]
    if not ids:
        print("Uso: python voz_piper.py [--instalar] [--listar] [--limpiar] [es_MX-claude-high ...]")
        return 1
    fallos = 0
    for i in ids:
        ok = asegurar(i)
        print(("[OK] " if ok else "[XX] ") + i + ("" if ok else f"  ({motivo()})"))
        fallos += 0 if ok else 1
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
