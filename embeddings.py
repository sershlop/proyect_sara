# 📁 embeddings.py
import numpy as np
import os
import logging
import logger
from config import MODELO_EMBEDDINGS

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
os.environ["TOKENIZERS_PARALLELISM"]     = "false"
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)

_modelo      = None
_disponible  = False


def cargar_modelo():
    global _modelo, _disponible
    try:
        from sentence_transformers import SentenceTransformer
        logger.info("embeddings", "Cargando modelo semántico...")
        _modelo     = SentenceTransformer(MODELO_EMBEDDINGS)
        _disponible = True
        logger.info("embeddings", f"Modelo '{MODELO_EMBEDDINGS}' cargado correctamente.")
        precalentar_modelo()
        return True
    except ImportError:
        logger.warning("embeddings", "sentence-transformers no instalado.")
        _disponible = False
        return False
    except Exception as e:
        logger.log_excepcion("embeddings", "cargar_modelo", e)
        _disponible = False
        return False


def esta_disponible():
    return _disponible


def precalentar_modelo():
    if not _disponible or _modelo is None:
        return False
    try:
        _modelo.encode("Inicializando modelo semántico.", convert_to_numpy=True)
        logger.debug("embeddings", "Modelo semántico precalentado y listo para consultas.")
        return True
    except Exception as e:
        logger.log_excepcion("embeddings", "precalentar_modelo", e)
        return False


def generar_vector(texto):
    if not _disponible or not _modelo or not texto:
        return None
    try:
        vector = _modelo.encode(texto, convert_to_numpy=True)
        return vector.tolist()
    except Exception as e:
        logger.log_excepcion("embeddings", "generar_vector", e)
        return None


def vector_desde_texto(texto):
    return generar_vector(texto)


def similitud_coseno(vector_a, vector_b):
    try:
        a       = np.array(vector_a, dtype=np.float32)
        b       = np.array(vector_b, dtype=np.float32)
        norma_a = np.linalg.norm(a)
        norma_b = np.linalg.norm(b)
        if norma_a == 0 or norma_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norma_a * norma_b))
    except Exception as e:
        logger.log_excepcion("embeddings", "similitud_coseno", e)
        return 0.0


def similitud_semantica(texto_a, texto_b):
    if not _disponible:
        return 0.0
    try:
        va = generar_vector(texto_a)
        vb = generar_vector(texto_b)
        if va is None or vb is None:
            return 0.0
        return similitud_coseno(va, vb)
    except Exception as e:
        logger.log_excepcion("embeddings", "similitud_semantica", e)
        return 0.0


def buscar_mas_similar(texto_consulta, lista_vectores):
    """
    Busca el item más similar semánticamente a texto_consulta dentro de
    lista_vectores. Acepta tuplas de longitud variable (>=2): usa SIEMPRE
    el primer elemento como identificador y el ÚLTIMO como vector, sin
    asumir tuplas de exactamente 2 elementos. Esto la hace compatible
    tanto con database.obtener_vectores_comandos() -> (dict, vector)
    como con database.obtener_vectores_conocimientos() -> (pregunta, respuesta, vector).
    """
    if not _disponible or not lista_vectores:
        return None, 0.0
    try:
        vector_consulta = generar_vector(texto_consulta)
        if vector_consulta is None:
            return None, 0.0
        mejor_id    = None
        mejor_score = 0.0
        for item in lista_vectores:
            if not item or len(item) < 2:
                continue
            item_id = item[0]
            vector  = item[-1]
            if vector is None:
                continue
            score = similitud_coseno(vector_consulta, vector)
            if score > mejor_score:
                mejor_score = score
                mejor_id    = item_id
        return mejor_id, mejor_score
    except Exception as e:
        logger.log_excepcion("embeddings", "buscar_mas_similar", e)
        return None, 0.0