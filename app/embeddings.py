# 📁 embeddings.py — Vectores semánticos (Capa 2 del motor de clasificación)
#
# Principio de diseño: si sentence-transformers no está instalado, o el
# modelo falla al cargar, la Capa 2 se salta (no falla) y el motor sigue
# a la Capa 3 (Qwen). Un import fallido aquí NUNCA debe tumbar el
# arranque de SARA — es exactamente el pilar de "resiliencia" del
# documento conceptual.

import math

try:
    from config import MODELO_EMBEDDINGS as _MODELO_CONFIGURADO
except Exception:
    _MODELO_CONFIGURADO = "paraphrase-multilingual-MiniLM-L12-v2"

_modelo = None
_intento_fallido = False


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    try:
        import logger as _logger
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[EMBEDDINGS ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


def _cargar_modelo():
    global _modelo, _intento_fallido
    if _modelo is not None:
        return _modelo
    if _intento_fallido:
        return None
    try:
        from sentence_transformers import SentenceTransformer
        _modelo = SentenceTransformer(_MODELO_CONFIGURADO)
        return _modelo
    except Exception as e:
        _intento_fallido = True
        _log_error("embeddings", "_cargar_modelo", e)
        return None


def esta_disponible() -> bool:
    return _cargar_modelo() is not None


def vector_desde_texto(texto: str):
    """Retorna list[float] o None si el modelo no está disponible o falla."""
    modelo = _cargar_modelo()
    if modelo is None or not texto:
        return None
    try:
        vector = modelo.encode(texto)
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


def buscar_mas_similar(vector: list, candidatos: list, umbral: float = 0.80):
    """
    candidatos: [(identificador, vector), ...] — ya viene en este formato
    desde database.obtener_intenciones_vectores().
    Retorna {"identificador": str, "score": float} del mejor match que
    supere el umbral, o None si ninguno lo supera.
    """
    if not vector or not candidatos:
        return None
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
