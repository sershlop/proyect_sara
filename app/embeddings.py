# 📁 embeddings.py — Vectores semánticos (Capa 2 del motor de clasificación)        v2 · 15 oct 2026
#
# v2.1 (16 oct 2026): la Capa 2 se SUELTA de la RAM (medido: ~770 MB → ~70 MB) SOLO si lleva 10 min sin usarse Y la RAM del
#       equipo está apretada (≥80 %). Con RAM holgada nunca se suelta: la rapidez de respuesta manda. Si se suelta, se recarga
#       sola (~4-6 s) en la próxima consulta que la necesite. Nuevo: python embeddings.py --comparar-int8 [frases.txt]
#
# v2: la Capa 2 corre sobre ONNX Runtime + tokenizers (+ numpy). YA NO necesita torch, transformers, scipy,
#     scikit-learn ni sympy (~760 MB menos en disco). El modelo es el MISMO (paraphrase-multilingual-MiniLM-L12-v2,
#     384 dimensiones, mean pooling, máx. 128 tokens), así que los vectores ya guardados en sara.db siguen valiendo.
#
# Orden de motores (degradación elegante, Principio 3):
#   1. ONNX     — si existen  <runtime>\embeddings_onnx\model.onnx  y  tokenizer.json  (los baja asegurar()).
#   2. sentence-transformers — solo si está instalado (instalaciones antiguas): se usa como antes.
#   3. Ninguno  — la Capa 2 se salta y el motor sigue a la Capa 3. Un import fallido NUNCA tumba el arranque.
#
# Variables de entorno (todas opcionales):
#   SARA_EMB_BACKEND = auto (defecto) | onnx | st        forzar un motor (útil para comparar)
#   SARA_EMB_MODELO  = fp32 (defecto) | int8             int8 pesa ~118 MB en vez de 470 MB, pero los vectores
#                                                        cambian un poco: valida el umbral antes de usarlo
#   SARA_EMB_DIR     = carpeta del modelo ONNX           (defecto: <runtime>\embeddings_onnx o <app>\embeddings_onnx)
#   SARA_EMB_HILOS   = hilos de CPU                      (defecto: núcleos/2, entre 1 y 4)
#   SARA_EMB_LIBERAR_MIN = minutos sin uso para soltar el modelo de la RAM (defecto 10; 0 = nunca lo suelta)
#   SARA_EMB_LIBERAR_RAM_PCT = % de RAM del equipo a partir del cual se permite soltarlo (defecto 80; 0 = soltar siempre
#                          que esté ocioso, sin mirar la RAM; sin psutil nunca se suelta)
#   SARA_EMB_ARENA   = 1 (defecto) | 0                   0 desactiva la reserva de memoria «arena» de ONNX Runtime
#   SARA_EMB_OPT     = all (defecto) | extended | basic | none   nivel de optimización del grafo (afecta a la RAM)
#
# Nada se descarga durante una clasificación: la descarga la hacen asegurar() / el instalador /
#   python embeddings.py --instalar
#
# API (sin cambios): esta_disponible() · vector_desde_texto(texto) · buscar_mas_similar(vector, candidatos, umbral)
# API nueva:         backend() · motivo() · asegurar() · carpeta_modelo() · liberar()
# Línea de comandos: python embeddings.py [--instalar] [--probar] [--comparar]

import gc
import logging
import math
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import List, Optional

try:
    from config import MODELO_EMBEDDINGS as _MODELO_CONFIGURADO
except Exception:
    _MODELO_CONFIGURADO = "paraphrase-multilingual-MiniLM-L12-v2"

logger = logging.getLogger("SARA-EMB")

_REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_BASE_URL = "https://huggingface.co/" + _REPO + "/resolve/main/{ruta}?download=true"
_MAX_TOKENS = 128                       # sentence_bert_config.json de este modelo
_VARIANTES = {                          # variante → (archivo en el repositorio, tamaño mínimo aceptable)
    "fp32": ("onnx/model.onnx", 400_000_000),
    "int8": ("onnx/model_quint8_avx2.onnx", 80_000_000),
}
_TOKENIZADOR = ("tokenizer.json", 1_000_000)
TIMEOUT_RED_S = 30

_modelo = None                          # motor activo: ("onnx", sesion, tokenizador) o ("st", SentenceTransformer)
_intento_fallido = False
_ultimo_uso = 0.0                       # time.monotonic() de la última consulta (para soltar el modelo por inactividad)
_vigilando = False
_ultimo_error = ""
_lock_carga = threading.Lock()
_lock_descarga = threading.Lock()


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    try:
        import logger as _logger
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[EMBEDDINGS ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


# ─── RUTAS ────────────────────────────────────────────────────────────────

def carpeta_modelo() -> Path:
    env = os.getenv("SARA_EMB_DIR")
    if env:
        return Path(env)
    app = Path(__file__).resolve().parent
    runtime = app.parent / "runtime"                 # paquete: C:\SARA\app\embeddings.py → C:\SARA\runtime
    return (runtime if runtime.is_dir() else app) / "embeddings_onnx"


def _variante() -> str:
    v = os.getenv("SARA_EMB_MODELO", "fp32").strip().lower()
    return v if v in _VARIANTES else "fp32"


def _rutas():
    d = carpeta_modelo()
    return d / Path(_VARIANTES[_variante()][0]).name, d / _TOKENIZADOR[0]


def _onnx_listo() -> bool:
    modelo, tok = _rutas()
    try:
        return modelo.stat().st_size > _VARIANTES[_variante()][1] and tok.stat().st_size > _TOKENIZADOR[1]
    except OSError:
        return False


# ─── CARGA DE LOS MOTORES ─────────────────────────────────────────────────

def _hilos() -> int:
    try:
        return max(1, int(os.getenv("SARA_EMB_HILOS", "")))
    except ValueError:
        return max(1, min(4, (os.cpu_count() or 2) // 2))


def _cargar_onnx():
    import onnxruntime as ort
    from tokenizers import Tokenizer
    ruta_modelo, ruta_tok = _rutas()
    tok = Tokenizer.from_file(str(ruta_tok))
    tok.enable_truncation(max_length=_MAX_TOKENS)
    pad = tok.token_to_id("<pad>")
    tok.enable_padding(pad_id=pad if pad is not None else 0, pad_token="<pad>")
    so = ort.SessionOptions()
    so.intra_op_num_threads = _hilos()
    so.inter_op_num_threads = 1
    so.enable_mem_pattern = False                    # frases sueltas de longitud distinta: no reservar de más
    niveles = {"all": ort.GraphOptimizationLevel.ORT_ENABLE_ALL, "extended": ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED,
               "basic": ort.GraphOptimizationLevel.ORT_ENABLE_BASIC, "none": ort.GraphOptimizationLevel.ORT_DISABLE_ALL}
    so.graph_optimization_level = niveles.get(os.getenv("SARA_EMB_OPT", "all").strip().lower(), niveles["all"])
    if os.getenv("SARA_EMB_ARENA", "1").strip() == "0":
        so.enable_cpu_mem_arena = False
    sesion = ort.InferenceSession(str(ruta_modelo), sess_options=so, providers=["CPUExecutionProvider"])
    return ("onnx", sesion, tok)


def _cargar_st():
    from sentence_transformers import SentenceTransformer
    return ("st", SentenceTransformer(_MODELO_CONFIGURADO))


def _cargar_modelo():
    global _modelo, _intento_fallido, _ultimo_error, _ultimo_uso
    if _modelo is not None:
        _ultimo_uso = time.monotonic()
        return _modelo
    if _intento_fallido:
        return None
    with _lock_carga:
        if _modelo is not None:
            _ultimo_uso = time.monotonic()
            return _modelo
        if _intento_fallido:
            return None
        pedido = os.getenv("SARA_EMB_BACKEND", "auto").strip().lower()
        errores = []
        t0 = time.perf_counter()
        orden = []
        if pedido in ("auto", "onnx") and _onnx_listo():
            orden.append(_cargar_onnx)
        elif pedido in ("auto", "onnx"):
            errores.append(f"falta el modelo ONNX en {carpeta_modelo()} (python embeddings.py --instalar)")
        if pedido in ("auto", "st"):
            orden.append(_cargar_st)
        for cargador in orden:
            try:
                _modelo = cargador()
                logger.info(f"🧠 Capa 2: motor {_modelo[0]} listo en {int((time.perf_counter() - t0) * 1000)} ms")
                _ultimo_uso = time.monotonic()
                _arrancar_vigilante()
                return _modelo
            except Exception as e:
                errores.append(f"{cargador.__name__[8:]}: {type(e).__name__}: {e}")
                _log_error("embeddings", cargador.__name__, e)
        _intento_fallido = True
        _ultimo_error = " | ".join(errores) or "ningún motor de embeddings disponible"
        return None


def esta_disponible() -> bool:
    return _cargar_modelo() is not None


def backend() -> Optional[str]:
    """'onnx' | 'st' | None (None = Capa 2 apagada). Fuerza la carga si aún no se intentó."""
    m = _cargar_modelo()
    return m[0] if m else None


def motivo() -> str:
    return _ultimo_error


def liberar() -> None:
    """Suelta el modelo de la RAM; se vuelve a cargar solo en la próxima consulta."""
    global _modelo, _intento_fallido
    with _lock_carga:
        _modelo = None
        _intento_fallido = False
    gc.collect()


def _liberar_min() -> float:
    try:
        return max(0.0, float(os.getenv("SARA_EMB_LIBERAR_MIN", "10")))
    except ValueError:
        return 10.0


def _hay_presion_de_ram() -> bool:
    """True si la RAM del equipo está lo bastante apretada como para soltar el modelo. Sin forma de medirla: False."""
    try:
        limite = float(os.getenv("SARA_EMB_LIBERAR_RAM_PCT", "80"))
    except ValueError:
        limite = 80.0
    if limite <= 0:
        return True
    try:
        import psutil
        return psutil.virtual_memory().percent >= limite
    except Exception:
        return False


def _arrancar_vigilante() -> None:
    """Lanza (una sola vez) el hilo que suelta el modelo tras un rato sin uso. Llamar con _lock_carga tomado."""
    global _vigilando
    if _vigilando or _liberar_min() <= 0:
        return
    _vigilando = True
    threading.Thread(target=_vigilar, name="sara-emb-idle", daemon=True).start()


def _vigilar() -> None:
    global _vigilando
    while True:
        time.sleep(max(0.2, min(30.0, (_liberar_min() * 60 or 30.0) / 4)))
        with _lock_carga:
            limite = _liberar_min() * 60
            if limite <= 0 or _modelo is None:
                _vigilando = False
                return
            ocioso = time.monotonic() - _ultimo_uso >= limite
        if not ocioso or not _hay_presion_de_ram():      # RAM holgada → se queda cargado (respuesta inmediata)
            continue
        with _lock_carga:
            if _modelo is None or time.monotonic() - _ultimo_uso < limite:
                continue
            _vigilando = False
        liberar()
        logger.info("🧠 Capa 2 soltada de la RAM (ociosa y con la RAM del equipo apretada); se recarga sola en la próxima consulta")
        return


# ─── VECTORES ─────────────────────────────────────────────────────────────

def _vector_onnx(sesion, tok, texto: str) -> List[float]:
    import numpy as np
    enc = tok.encode(texto)
    ids = np.asarray([enc.ids], dtype=np.int64)
    mascara = np.asarray([enc.attention_mask], dtype=np.int64)
    entradas = {}
    for e in sesion.get_inputs():
        if e.name == "input_ids":
            entradas[e.name] = ids
        elif e.name == "attention_mask":
            entradas[e.name] = mascara
        elif e.name == "token_type_ids":
            entradas[e.name] = np.zeros_like(ids)
        else:
            raise RuntimeError(f"el modelo ONNX pide una entrada desconocida: {e.name}")
    nombres = [s.name for s in sesion.get_outputs()]
    salidas = sesion.run(None, entradas)
    if "sentence_embedding" in nombres:                      # el modelo ya trae el pooling incorporado
        v = salidas[nombres.index("sentence_embedding")][0]
    else:                                                    # tokens × 384 → mean pooling con la máscara (igual que sentence-transformers)
        h = salidas[0]
        m = mascara[..., None].astype(np.float32)
        v = ((h * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None))[0]
    return v.astype(np.float32).tolist()


def vector_desde_texto(texto: str):
    """Retorna list[float] o None si el modelo no está disponible o falla."""
    modelo = _cargar_modelo()
    if modelo is None or not texto:
        return None
    try:
        if modelo[0] == "onnx":
            return _vector_onnx(modelo[1], modelo[2], texto)
        vector = modelo[1].encode(texto)
        return vector.tolist() if hasattr(vector, "tolist") else list(vector)
    except Exception as e:
        _log_error("embeddings", "vector_desde_texto", e)
        return None


def _coseno(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    producto = sum(x * y for x, y in zip(a, b))
    norma_a = math.sqrt(sum(x * x for x in a))
    norma_b = math.sqrt(sum(y * y for y in b))
    if norma_a == 0 or norma_b == 0:
        return 0.0
    return producto / (norma_a * norma_b)


def _mejor_con_numpy(vector: list, candidatos: list):
    """(id, score) del candidato más parecido, calculado de una vez con numpy. Lanza excepción si los datos no
    encajan (vectores de distinto largo, etc.): buscar_mas_similar entonces usa el bucle de siempre."""
    import numpy as np
    q = np.asarray(vector, dtype=np.float32)
    m = np.asarray([c[1] for c in candidatos], dtype=np.float32)
    if m.ndim != 2 or m.shape[1] != q.shape[0]:
        raise ValueError("vectores de distinto largo")
    nq = float(np.linalg.norm(q))
    nm = np.linalg.norm(m, axis=1)
    if nq == 0:
        return None, 0.0
    scores = np.where(nm > 0, (m @ q) / np.where(nm > 0, nm * nq, 1.0), 0.0)
    i = int(np.argmax(scores))
    return candidatos[i][0], float(scores[i])


def buscar_mas_similar(vector: list, candidatos: list, umbral: float = 0.80):
    """
    candidatos: [(identificador, vector), ...] — ya viene en este formato
    desde database.obtener_intenciones_vectores().
    Retorna {"identificador": str, "score": float} del mejor match que
    supere el umbral, o None si ninguno lo supera.
    """
    if not vector or not candidatos:
        return None
    try:
        mejor_id, mejor_score = _mejor_con_numpy(vector, candidatos)
    except Exception:
        mejor_id, mejor_score = None, 0.0
        for identificador, vec in candidatos:
            try:
                score = _coseno(vector, vec)
            except Exception as e:
                _log_error("embeddings", "buscar_mas_similar (comparacion)", e)
                continue
            if score > mejor_score:
                mejor_score, mejor_id = score, identificador
    if mejor_id is not None and mejor_score >= umbral:
        return {"identificador": mejor_id, "score": mejor_score}
    return None


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
        if hecho and r.status != 206:                # el servidor no reanuda: empezar de cero
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


def asegurar() -> bool:
    """Descarga el modelo ONNX y el tokenizador si faltan (bloquea) y comprueba que dan un vector de 384 valores.
    True si la Capa 2 con ONNX queda lista. El archivo .part se conserva si se corta la descarga (reanuda)."""
    global _ultimo_error, _modelo, _intento_fallido
    if _onnx_listo():
        return True
    with _lock_descarga:
        if _onnx_listo():
            return True
        ruta_modelo, ruta_tok = _rutas()
        archivo_repo, minimo = _VARIANTES[_variante()]
        try:
            t0 = time.perf_counter()
            logger.info(f"⬇️ Descargando el modelo semántico ONNX ({ruta_modelo.name}) — solo esta vez…")
            if not (ruta_tok.exists() and ruta_tok.stat().st_size > _TOKENIZADOR[1]):
                _descargar(_BASE_URL.format(ruta=_TOKENIZADOR[0]), ruta_tok)
            if not (ruta_modelo.exists() and ruta_modelo.stat().st_size > minimo):
                _descargar(_BASE_URL.format(ruta=archivo_repo), ruta_modelo)
            if not _onnx_listo():
                raise RuntimeError("la descarga quedó incompleta")
            with _lock_carga:                        # volver a intentar con el motor nuevo
                _modelo, _intento_fallido = None, False
            v = None
            if os.getenv("SARA_EMB_BACKEND", "auto").strip().lower() != "st":
                m = _cargar_onnx()
                v = _vector_onnx(m[1], m[2], "prueba de la capa semántica")
            if v is None or len(v) != 384 or not any(v):
                raise RuntimeError("el modelo descargado no devuelve un vector válido de 384 valores")
            logger.info(f"✅ Modelo semántico ONNX listo ({int(time.perf_counter() - t0)} s)")
            return True
        except Exception as e:
            _ultimo_error = f"no pude dejar listo el modelo ONNX: {type(e).__name__}: {e}"
            logger.error(f"❌ {_ultimo_error}")
            return False


# ─── LÍNEA DE COMANDOS (la usa el instalador) ─────────────────────────────

_FRASES = ["abre chrome", "cierra spotify", "qué hora es", "busca mis documentos de la semana pasada",
           "ejecuta el comando para ver los procesos", "crea una carpeta llamada fotos en el escritorio",
           "sube el volumen", "pon música relajante en youtube", "cuánta memoria ram estoy usando",
           "mándale un mensaje a mamá", "recuérdame llamar al doctor mañana a las cinco", "borra el archivo de prueba"]


_FRASES_PARES = [
    "abre chrome", "abre el navegador", "lanza google chrome", "abre la calculadora", "ejecuta la calculadora",
    "abre spotify", "pon spotify", "sube el volumen", "más volumen", "baja el volumen", "silencia el sonido",
    "quita el sonido", "qué hora es", "dime la hora", "qué día es hoy", "cuál es la fecha de hoy",
    "crea una carpeta llamada fotos", "haz una carpeta nueva en el escritorio", "borra el archivo de prueba",
    "elimina ese archivo", "busca mis documentos", "recuérdame llamar al doctor mañana",
    "ponme un recordatorio para mañana a las cinco", "anota una tarea comprar leche", "qué es la luna",
    "háblame de la luna", "quién fue einstein", "cuánta ram estoy usando", "cuánta memoria libre tengo",
    "pon música relajante en youtube"]


def _comparar_int8(argv: List[str]) -> int:
    """Mide, con las MISMAS frases, cuánto cambian los vectores y la velocidad entre el modelo fp32 y el int8.
    Con tus frases reales (un archivo .txt, una por línea) la prueba es mucho más fiable que con las de ejemplo."""
    import numpy as np
    ruta = next((a for a in argv if a.lower().endswith(".txt")), None)
    frases = _FRASES_PARES
    if ruta:
        frases = [l.strip() for l in Path(ruta).read_text(encoding="utf-8").splitlines() if l.strip()]
    if len(frases) < 5:
        print("[XX] hacen falta al menos 5 frases")
        return 1
    previo = {k: os.environ.get(k) for k in ("SARA_EMB_MODELO", "SARA_EMB_BACKEND")}
    datos = {}
    try:
        os.environ["SARA_EMB_BACKEND"] = "onnx"
        for var in ("fp32", "int8"):
            os.environ["SARA_EMB_MODELO"] = var
            liberar()
            if not _onnx_listo():
                print(f"[XX] falta el modelo {var} en {carpeta_modelo()}  "
                      f"(SARA_EMB_MODELO={var} y python embeddings.py --instalar)")
                return 1
            t0 = time.perf_counter()
            if vector_desde_texto(frases[0]) is None:
                print(f"[XX] no pude cargar el modelo {var}: {motivo()}")
                return 1
            carga = time.perf_counter() - t0
            vecs = []
            t1 = time.perf_counter()
            for f in frases:
                vecs.append(vector_desde_texto(f))
            ms = (time.perf_counter() - t1) * 1000 / len(frases)
            datos[var] = (np.asarray(vecs, dtype=np.float32), carga, ms)
    finally:
        for k, v in previo.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        liberar()
    a, b = datos["fp32"][0], datos["int8"][0]
    an, bn = a / np.linalg.norm(a, axis=1, keepdims=True), b / np.linalg.norm(b, axis=1, keepdims=True)
    propio = (an * bn).sum(axis=1)                                  # cada frase en fp32 contra ella misma en int8
    ca, cb = an @ an.T, bn @ bn.T                                   # similitudes entre todas las frases, en cada modelo
    iu = np.triu_indices(len(frases), k=1)
    dif = np.abs(ca[iu] - cb[iu])
    cambios = {u: int(((ca[iu] >= u) != (cb[iu] >= u)).sum()) for u in (0.75, 0.80)}
    np.fill_diagonal(ca, -1)
    np.fill_diagonal(cb, -1)
    vecino_igual = int((ca.argmax(axis=1) == cb.argmax(axis=1)).sum())
    print(f"{len(frases)} frases · fp32: {datos['fp32'][2]:.1f} ms/frase (carga {datos['fp32'][1]:.1f} s)"
          f" · int8: {datos['int8'][2]:.1f} ms/frase (carga {datos['int8'][1]:.1f} s)")
    print(f"misma frase fp32↔int8: coseno mínimo {propio.min():.4f} · medio {propio.mean():.4f}")
    print(f"similitud entre frases: cambio medio {dif.mean():.4f} · cambio máximo {dif.max():.4f}")
    print(f"decisiones que cambian al usar int8: umbral 0.80 → {cambios[0.80]} pares · umbral 0.75 → {cambios[0.75]} pares")
    print(f"frase más parecida (vecino más cercano) igual en {vecino_igual} de {len(frases)}")
    apto = propio.min() >= 0.98 and cambios[0.80] == 0 and cambios[0.75] == 0 and vecino_igual == len(frases)
    print("[OK] con estas frases int8 decide igual que fp32" if apto else
          "[!!] int8 cambia alguna decisión o se aleja bastante: mira los números antes de usarlo")
    if not ruta:
        print("(son frases de ejemplo: para una prueba fiable pásale las tuyas: python embeddings.py --comparar-int8 mis_frases.txt)")
    return 0 if apto else 1


def _main(argv: List[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "--instalar" in argv:
        ok = asegurar()
        print("[OK] Capa 2 (ONNX) lista" if ok else f"[XX] {motivo()}")
        return 0 if ok else 1
    if "--probar" in argv:
        t0 = time.perf_counter()
        v = vector_desde_texto("abre la calculadora")
        if v is None:
            print(f"[XX] sin motor de embeddings: {motivo()}")
            return 1
        t1 = time.perf_counter()
        for f in _FRASES:
            vector_desde_texto(f)
        ms = (time.perf_counter() - t1) * 1000 / len(_FRASES)
        print(f"[OK] motor={backend()} · {len(v)} valores · carga+1.ª frase {int((t1 - t0) * 1000)} ms · {ms:.0f} ms/frase")
        return 0
    if "--comparar" in argv:                         # ONNX contra sentence-transformers (hay que tener los dos instalados)
        try:
            os.environ["SARA_EMB_BACKEND"] = "onnx"
            liberar()
            vo = [vector_desde_texto(f) for f in _FRASES]
            if backend() != "onnx" or None in vo:
                print(f"[XX] ONNX no disponible: {motivo()}")
                return 1
            os.environ["SARA_EMB_BACKEND"] = "st"
            liberar()
            vs = [vector_desde_texto(f) for f in _FRASES]
            if backend() != "st" or None in vs:
                print(f"[XX] sentence-transformers no disponible: {motivo()}")
                return 1
        finally:
            os.environ.pop("SARA_EMB_BACKEND", None)
            liberar()
        cos = [_coseno(a, b) for a, b in zip(vo, vs)]
        dif = max(abs(x - y) for a, b in zip(vo, vs) for x, y in zip(a, b))
        print(f"coseno ONNX↔sentence-transformers: mínimo {min(cos):.6f} · media {sum(cos) / len(cos):.6f} · mayor diferencia {dif:.2e}")
        bien = min(cos) > (0.9999 if _variante() == "fp32" else 0.97)
        print("[OK] equivalentes: los vectores guardados en sara.db siguen valiendo" if bien else
              "[XX] DIFIEREN demasiado: no cambies de motor sin revisar el umbral")
        return 0 if bien else 1
    if "--comparar-int8" in argv:                    # fp32 contra int8, los dos sobre ONNX (no hace falta sentence-transformers)
        return _comparar_int8(argv)
    print("Uso: python embeddings.py [--instalar] [--probar] [--comparar] [--comparar-int8 [frases.txt]]")
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
