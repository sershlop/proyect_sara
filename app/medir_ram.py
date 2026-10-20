# 📁 medir_ram.py — ¿Cuánta RAM gasta CADA pieza de SARA por separado?                v2 · 16 oct 2026
#
# Cada pieza se mide en un proceso Python NUEVO (así una no contamina a la otra) y al final se mide todo junto.
# No toca SARA ni su base de datos: solo importa embeddings.py, voz_piper.py y faster-whisper y los carga.
#
# Uso (con SARA CERRADA, para que no se mezcle con su consumo):
#     cd C:\SARA\app
#     ..\runtime\python\python.exe medir_ram.py
#     ..\runtime\python\python.exe medir_ram.py --variantes      (solo la Capa 2: compara opciones de memoria de ONNX Runtime)
#     ..\runtime\python\python.exe medir_ram.py --emb int8      (usa el modelo int8, si ya lo bajaste con SARA_EMB_MODELO=int8)
#
# Columnas: MB_ws   = RAM física que ocupa el proceso («conjunto de trabajo»; lo que ve el Administrador de tareas y `ram`).
#                     ES LA CIFRA QUE IMPORTA para saber cuánta RAM le quitas al equipo.
#           MB_priv = memoria COMPROMETIDA por el proceso: incluye reservas que quizá nunca se usen, así que sale mayor.
#                     Solo sirve para comparar variantes entre sí.
# Whisper se carga con los mismos parámetros que stt.py (base · cpu · int8 · 4 hilos · local_files_only).

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
RUNTIME = APP.parent / "runtime" if (APP.parent / "runtime").is_dir() else APP
PASOS_FRASES = ["abre la calculadora", "qué es la luna", "sube el volumen", "pon música en youtube", "cierra spotify"]


def _mb():
    import psutil
    p = psutil.Process()
    mi = p.memory_info()
    priv = getattr(mi, "private", None)
    if priv is None:
        try:
            priv = p.memory_full_info().uss
        except Exception:
            priv = 0
    return round(mi.rss / 1048576), round(priv / 1048576)


def _marca(paso: str, t0: float = None):
    ws, priv = _mb()
    print("@@" + json.dumps({"paso": paso, "ws": ws, "priv": priv,
                             "s": round(time.perf_counter() - t0, 1) if t0 else None}), flush=True)


# ─── PIEZAS (se ejecutan dentro del proceso hijo) ─────────────────────────

def _emb():
    sys.path.insert(0, str(APP))
    t0 = time.perf_counter()
    import embeddings
    _marca("import embeddings", t0)
    t0 = time.perf_counter()
    v = embeddings.vector_desde_texto(PASOS_FRASES[0])
    if v is None:
        print("@@" + json.dumps({"error": "embeddings: " + (embeddings.motivo() or "sin motor")}), flush=True)
        return
    _marca(f"Capa 2 cargada (motor {embeddings.backend()})", t0)
    for f in PASOS_FRASES:
        embeddings.vector_desde_texto(f)
    _marca("Capa 2 tras 5 frases")
    t0 = time.perf_counter()
    embeddings.liberar()
    _marca("Capa 2 tras liberar()", t0)


def _piper():
    sys.path.insert(0, str(APP))
    import voz_piper as p
    if not p.disponible():
        print("@@" + json.dumps({"error": "piper: " + p.motivo()}), flush=True)
        return
    if not p.instalada(p.VOZ_BASE):
        print("@@" + json.dumps({"error": "la voz claude no está descargada"}), flush=True)
        return
    t0 = time.perf_counter()
    destino = Path(tempfile.gettempdir()) / "medir_ram_piper.wav"
    ok = p.sintetizar_wav("Hola, soy Sara. Esta es una prueba de memoria.", p.VOZ_BASE, destino)
    if not ok:
        print("@@" + json.dumps({"error": "piper: " + p.motivo()}), flush=True)
        return
    _marca("Piper claude cargada + 1 frase", t0)
    p.sintetizar_wav("Otra frase más larga para ver si la memoria crece al seguir hablando con normalidad.", p.VOZ_BASE, destino)
    _marca("Piper tras 2 frases")
    try:
        destino.unlink()
    except OSError:
        pass


def _whisper():
    os.environ.setdefault("HF_HOME", str(RUNTIME / "hf_cache"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    t0 = time.perf_counter()
    try:
        from faster_whisper import WhisperModel
        _marca("import faster_whisper", t0)
        t0 = time.perf_counter()
        m = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=4, local_files_only=True)
    except Exception as e:
        print("@@" + json.dumps({"error": f"whisper: {type(e).__name__}: {e}"}), flush=True)
        return
    _marca("Whisper base int8 cargado", t0)
    import numpy as np
    t0 = time.perf_counter()
    audio = (np.random.default_rng(1).standard_normal(16000 * 5) * 0.01).astype(np.float32)
    list(m.transcribe(audio, language="es", beam_size=2, temperature=0, vad_filter=False)[0])
    _marca("Whisper tras transcribir 5 s", t0)


def _todo():
    _marca("arranque (python + psutil)")
    for f in (_emb, _piper, _whisper):
        f()


HIJOS = {"base": lambda: _marca("python + psutil, sin nada más"), "embeddings": _emb, "piper": _piper,
         "whisper": _whisper, "todo": _todo}
TITULOS = {"base": "Base", "embeddings": "Capa 2 (embeddings)", "piper": "Voz Piper", "whisper": "Whisper (voz de entrada)",
           "todo": "TODO JUNTO (en este orden)"}


# ─── PROCESO PADRE ────────────────────────────────────────────────────────

def _correr(nombre: str, extra_env: dict):
    env = dict(os.environ)
    env.update(extra_env)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--hijo", nombre],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=600)
    filas, errores = [], []
    for linea in r.stdout.splitlines():
        if linea.startswith("@@"):
            d = json.loads(linea[2:])
            (errores if "error" in d else filas).append(d)
    if r.returncode != 0 and not filas and not errores:
        errores.append({"error": (r.stderr.strip().splitlines() or ["terminó con error"])[-1][:200]})
    return filas, errores


def _variantes(extra_base: dict):
    """Solo la Capa 2, varias veces, con distintas opciones de ONNX Runtime. Muestra la RAM tras cargar y tras liberar."""
    modelos = APP.parent / "runtime" / "embeddings_onnx" if (APP.parent / "runtime").is_dir() else APP / "embeddings_onnx"
    carpeta = Path(os.environ.get("SARA_EMB_DIR", str(modelos)))
    variantes = [("actual (por defecto)", {}),
                 ("sin arena de memoria", {"SARA_EMB_ARENA": "0"}),
                 ("optimización básica", {"SARA_EMB_OPT": "basic"}),
                 ("sin arena + básica", {"SARA_EMB_ARENA": "0", "SARA_EMB_OPT": "basic"}),
                 ("sin arena + sin optimizar", {"SARA_EMB_ARENA": "0", "SARA_EMB_OPT": "none"})]
    if (carpeta / "model_quint8_avx2.onnx").exists():
        variantes.append(("modelo int8", {"SARA_EMB_MODELO": "int8"}))
        variantes.append(("int8 + sin arena", {"SARA_EMB_MODELO": "int8", "SARA_EMB_ARENA": "0"}))
    else:
        print("(modelo int8 no descargado: para incluirlo ejecuta antes  $env:SARA_EMB_MODELO='int8'; ..\\runtime\\python\\python.exe embeddings.py --instalar )\n")
    print(f"{'Variante':<28}{'ws cargada':>11}{'priv':>7}{'carga':>8}{'ws tras liberar':>17}")
    for nombre, env in variantes:
        e = dict(extra_base); e.update(env)
        filas, errores = _correr("embeddings", e)
        cargada = next((f for f in filas if f["paso"].startswith("Capa 2 cargada")), None)
        liberada = next((f for f in filas if f["paso"].startswith("Capa 2 tras liberar")), None)
        if not cargada:
            print(f"{nombre:<28}  ⚠ {errores[0]['error'] if errores else 'sin datos'}")
            continue
        print(f"{nombre:<28}{cargada['ws']:>9} MB{cargada['priv']:>5}{cargada['s']:>7} s{(str(liberada['ws']) + ' MB') if liberada else '—':>16}")
    return 0


def main(argv):
    if "--hijo" in argv:
        HIJOS[argv[argv.index("--hijo") + 1]]()
        return 0
    extra = {}
    if "--emb" in argv:
        extra["SARA_EMB_MODELO"] = argv[argv.index("--emb") + 1]
    if "--variantes" in argv:
        return _variantes(extra)
    print(f"Midiendo con {sys.executable}\nCarpeta de la app: {APP}\n(cada bloque tarda unos segundos; Whisper puede tardar más)\n")
    base_priv = None
    resumen = []
    for nombre in ("base", "embeddings", "piper", "whisper", "todo"):
        print(f"── {TITULOS[nombre]}")
        filas, errores = _correr(nombre, extra)
        for d in filas:
            if nombre == "base":
                base_priv = d["priv"]
            t = f"{d['s']:>5} s" if d.get("s") is not None else "      "
            print(f"   {d['paso']:<46} MB_ws={d['ws']:>5}   MB_priv={d['priv']:>5}   {t}")
        for e in errores:
            print(f"   ⚠ {e['error']}")
        if filas and nombre != "base":
            resumen.append((TITULOS[nombre], filas[-1]["ws"], filas[-1]["priv"]))
        print()
    print("RESUMEN (valor final de cada bloque; el de «todo junto» no es la suma: las DLL compartidas se cuentan una vez)")
    for t, ws, priv in resumen:
        print(f"   {t:<30} MB_ws={ws:>5}   MB_priv={priv:>5}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
