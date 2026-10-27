# 📁 intent_engine.py — Núcleo de clasificación de SARA v2.0 (OPTIMIZADO, v3 GUI)
#
# Motor de 3 capas: patrones → vectorial → LLM
# Cambios v3 (integración GUI):
#   * Todo resultado incluye "texto_original" (antes solo lo añadía main/core_bridge DESPUÉS;
#     generador_codigo/informativo lo leían vacío).
#   * El fallback a Gemini/Groq ahora SÍ se dispara cuando Ollama cae: se capturan las excepciones
#     de `requests` (requests.exceptions.ConnectionError/Timeout no heredan de los builtin).
#   * Keywords con límite de palabra: "que es" ya no captura "que estas haciendo", "pon" ya no
#     captura "reponer".
#   * Caché de regex por texto del patrón (antes por id(Row): crecía sin límite).

import json
import re
import time
from typing import Optional
from functools import lru_cache

import database as db
import embeddings
from utils import normalizar_texto

try:
    import memoria_core
except ImportError:
    memoria_core = None

try:
    import respaldo_externo
except ImportError:
    respaldo_externo = None

_EXCEPCIONES_RED = (ConnectionError, TimeoutError)
try:
    import requests as _requests_excepciones
    _EXCEPCIONES_RED = _EXCEPCIONES_RED + (
        _requests_excepciones.exceptions.ConnectionError,
        _requests_excepciones.exceptions.Timeout,
    )
except ImportError:
    _requests_excepciones = None

try:
    from config import (
        QWEN_MODEL, QWEN_TEMPERATURA, QWEN_MAX_TOKENS, OLLAMA_URL, QWEN_TIMEOUT_SEGUNDOS,
        QWEN_FORMAT, QWEN_MAX_CONTEXT, FRASES_SIN_INTENCION_ACCIONABLE,
        UMBRAL_VECTORIAL_CLASIFICACION as UMBRAL_VECTORIAL,
        UMBRAL_CONFIANZA_QWEN,
        UMBRAL_APRENDIZAJE_CLASIFICACION as UMBRAL_APRENDIZAJE,
        CACHE_INTENCIONES_TTL_SEGUNDOS as CACHE_TTL_SEGUNDOS,
        CATEGORIA_DESCONOCIDA,
        DESCRIPCIONES_CATEGORIAS,
    )
except Exception:
    QWEN_MODEL, QWEN_TEMPERATURA, QWEN_MAX_TOKENS = "llava-phi3", 0.0, 150
    OLLAMA_URL = "http://localhost:11434/api/generate"
    QWEN_TIMEOUT_SEGUNDOS = 45
    QWEN_FORMAT = "json"
    QWEN_MAX_CONTEXT = 2048
    FRASES_SIN_INTENCION_ACCIONABLE = frozenset()
    UMBRAL_VECTORIAL = 0.80
    UMBRAL_CONFIANZA_QWEN = 0.65
    UMBRAL_APRENDIZAJE = 0.75
    CACHE_TTL_SEGUNDOS = 30
    CATEGORIA_DESCONOCIDA = "DESCONOCIDA"
    DESCRIPCIONES_CATEGORIAS = {}

try:
    import config as _cfg_ie
    _KEEP_ALIVE = str(getattr(_cfg_ie, "OLLAMA_KEEP_ALIVE", "30m"))   # Ollama descarga el modelo a los 5 min por defecto
except Exception:
    _KEEP_ALIVE = "30m"

CATEGORIAS_ARCHIVOS = frozenset({
    "ARCHIVO", "ABRIR_ARCHIVO", "CARPETA_ESPECIAL", "ABRIR_APLICACION",
    "CERRAR_APLICACION", "COPIAR_ARCHIVO", "BUSCAR_EN_NAVEGADOR",
    "MOVER_ARCHIVO", "RENOMBRAR_ARCHIVO", "COMPRIMIR_ARCHIVO",
    "ELIMINAR_ARCHIVO", "CREAR_CARPETA",
})

PALABRAS_RESERVADAS_CATEGORIA = {"CATEGORIA", "CATEGORIA_CORRECTA"}

# Caché de regex compilados, por texto del patrón
_CACHE_PATRONES_COMPILADOS = {}


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    try:
        import logger as _logger
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        pass  # Sin prints — solo logging interno


@lru_cache(maxsize=2048)
def _regex_keyword(patron: str):
    """Keyword como palabra(s) completa(s): 'abre' no coincide dentro de 'abrevadero'."""
    return re.compile(rf"(?<!\w){re.escape(patron)}(?!\w)")


_VECTORES_MEMO: dict = {}


def _vector_memo(texto_limpio: str):
    """Embedding memorizado: reconocer_rapido y clasificar procesan la MISMA frase seguidas (dos veces el modelo
    de embeddings). Determinista y solo de lectura. Un fallo (None) nunca se memoriza."""
    v = _VECTORES_MEMO.get(texto_limpio)
    if v is not None:
        return v
    v = embeddings.vector_desde_texto(texto_limpio)
    if v is not None:
        if len(_VECTORES_MEMO) >= 256:
            _VECTORES_MEMO.clear()
        _VECTORES_MEMO[texto_limpio] = v
    return v


def clasificar(texto_original: str, usar_cache: bool = True) -> dict:
    """
    Punto de entrada principal. Retorna {categoria, confianza, metodo, texto_norm, texto_original, ...}
    """
    texto_limpio = normalizar_texto(texto_original)
    if not texto_limpio:
        return {"categoria": CATEGORIA_DESCONOCIDA, "confianza": 0.0, "metodo": "ninguno",
                "texto_norm": texto_limpio, "texto_original": texto_original}

    # Fast-path: frases sin intención
    if texto_limpio in FRASES_SIN_INTENCION_ACCIONABLE:
        resultado = {"categoria": CATEGORIA_DESCONOCIDA, "confianza": 1.0, "metodo": "exacto",
                     "texto_norm": texto_limpio, "texto_original": texto_original}
        _finalizar(texto_original, texto_limpio, resultado)
        return resultado

    # Caché con TTL deslizante
    if usar_cache:
        cacheado = db.obtener_cache_intencion(texto_limpio)
        if cacheado:
            resultado = dict(cacheado)
            resultado["metodo"] = "cache"
            resultado["texto_norm"] = texto_limpio
            resultado["texto_original"] = texto_original
            id_historial = db.guardar_historial_clasificacion(
                texto_original, texto_limpio, resultado["categoria"], resultado["confianza"], "cache"
            )
            resultado["id_historial"] = id_historial
            db.guardar_cache_intencion(texto_limpio, resultado, ttl_segundos=CACHE_TTL_SEGUNDOS)
            return resultado

    # Cascada de capas
    resultado = _por_patrones(texto_limpio)
    if resultado is None:
        resultado = _por_aprendida_exacta(texto_limpio)
    if resultado is None:
        resultado = _por_vectorial(texto_limpio)
    if resultado is None:
        resultado = _por_qwen(texto_original, texto_limpio)
    if resultado is None:
        resultado = {"categoria": CATEGORIA_DESCONOCIDA, "confianza": 0.0, "metodo": "ninguno"}

    resultado["texto_norm"] = texto_limpio
    resultado["texto_original"] = texto_original
    _finalizar(texto_original, texto_limpio, resultado)
    return resultado


# Categorías que NO cuentan como "orden que SARA ya domina" para el puente conversacional: las preguntas y
# lo no reconocido las responde mejor la IA conversacional (una llamada en vez de dos).
CATEGORIAS_PARA_PUENTE = frozenset({"PREGUNTA_INFORMATIVA", "CONSULTA_GENERAL", "PREGUNTA_SISTEMA",
                                    CATEGORIA_DESCONOCIDA})
_PREFIJOS_CORTESIA_MOTOR = ("oye sara", "hey sara", "hola sara", "ok sara", "sara", "por favor", "porfa")


def _sin_cortesia(texto_limpio: str) -> str:
    t, cambio = texto_limpio, True
    while cambio:
        cambio = False
        for p in _PREFIJOS_CORTESIA_MOTOR:
            if t.startswith(p + " "):
                t, cambio = t[len(p) + 1:], True
    return t


def reconocer_rapido(texto_original: str) -> Optional[dict]:
    """¿Es una orden que SARA ya domina? Capas 0-2 SIN LLM y SIN efectos secundarios (no guarda historial,
    ni caché, ni aprendizaje). Lo usa core_bridge para decidir si un mensaje va directo al motor o pasa por
    el puente conversacional (IA externa).

    Devuelve el resultado solo si es una coincidencia SEGURA:
      · exacto / regex / caché / vectorial (y la categoría no es de preguntas ni DESCONOCIDA)
      · keyword solo si está al INICIO de la frase (así "cómo se abre una ostra" no cuenta) y, si es de
        una sola palabra, que no sea de ABRIR_APLICACION ("pon", "saca"... son ambiguas: decide la IA).
    En cualquier otro caso devuelve None.
    """
    try:
        texto_limpio = normalizar_texto(texto_original)
        if not texto_limpio or texto_limpio in FRASES_SIN_INTENCION_ACCIONABLE:
            return None

        cacheado = db.obtener_cache_intencion(texto_limpio)
        if cacheado:
            r = dict(cacheado)
            r["metodo"] = "cache"
        else:
            r = _por_patrones(texto_limpio)
            if r is None:
               r = _por_aprendida_exacta(texto_limpio)
        if not r or r.get("categoria") in CATEGORIAS_PARA_PUENTE:
            return None

        if r.get("metodo") == "keyword":
            patron = r.get("patron", "")
            inicio = _sin_cortesia(texto_limpio)
            if not patron or not (inicio == patron or inicio.startswith(patron + " ")):
                return None
            if len(patron.split()) < 2 and r.get("categoria") == "ABRIR_APLICACION":
                return None
        return r
    except Exception as e:
        _log_error("intent_engine", "reconocer_rapido", e)
        return None


def _por_patrones(texto_limpio: str) -> Optional[dict]:
    """
    Capa 1 — patrones (ya vienen ordenados por especificidad descendente).
    """
    try:
        patrones = db.obtener_patrones()
    except Exception as e:
        _log_error("intent_engine", "_por_patrones (obtener)", e)
        return None

    for p in patrones:
        try:
            tipo, patron, categoria = p["tipo"], p["patron"], p["categoria"]

            if tipo == "exacto" and texto_limpio == patron:
                return {"categoria": categoria, "confianza": 0.99, "metodo": "exacto"}

            if tipo == "keyword" and patron in texto_limpio and _regex_keyword(patron).search(texto_limpio):
                return {"categoria": categoria, "confianza": 0.95, "metodo": "keyword", "patron": patron}

            if tipo == "regex":
                cache_key = f"regex_{patron}"
                if cache_key not in _CACHE_PATRONES_COMPILADOS:
                    try:
                        _CACHE_PATRONES_COMPILADOS[cache_key] = re.compile(patron)
                    except Exception:
                        _CACHE_PATRONES_COMPILADOS[cache_key] = None
                regex_compilado = _CACHE_PATRONES_COMPILADOS[cache_key]
                if regex_compilado is not None and regex_compilado.search(texto_limpio):
                    return {"categoria": categoria, "confianza": 0.95, "metodo": "regex", "patron": patron}
        except Exception as e:
            _log_error("intent_engine", f"_por_patrones (id={p['id'] if 'id' in p.keys() else '?'})", e)
            continue
    return None

def _por_aprendida_exacta(texto_limpio: str) -> Optional[dict]:
    """Capa 1.5: la frase exacta ya se aprendió y se probó. Sin embeddings, sin LLM."""
    try:
        r = db.buscar_intencion_por_texto(texto_limpio)
        if r:
            conf = min(0.97, max(0.80, float(r.get("confianza") or 0.8)))
            return {"categoria": r["categoria"], "confianza": round(conf, 4), "metodo": "vectorial"}  # 'vectorial' cumple el CHECK de la BD
    except Exception as e:
        _log_error("intent_engine", "_por_aprendida_exacta", e)
    return None
def _por_vectorial(texto_limpio: str) -> Optional[dict]:
    """
    Capa 2 — búsqueda vectorial.
    """
    if not embeddings.esta_disponible():
        return None

    vector = _vector_memo(texto_limpio)
    if vector is None:
        return None

    try:
        categorias = db.obtener_categorias_registradas()
    except Exception as e:
        _log_error("intent_engine", "_por_vectorial (categorias)", e)
        return None

    mejor_categoria, mejor_match = None, None
    for categoria in categorias:
        try:
            candidatos = db.obtener_intenciones_vectores(categoria)
            if not candidatos:
                continue
            match = embeddings.buscar_mas_similar(vector, candidatos, umbral=UMBRAL_VECTORIAL)
            if match and (mejor_match is None or match["score"] > mejor_match["score"]):
                mejor_match, mejor_categoria = match, categoria
        except Exception as e:
            _log_error("intent_engine", f"_por_vectorial ({categoria})", e)
            continue

    if mejor_match is None:
        return None
    return {"categoria": mejor_categoria, "confianza": round(mejor_match["score"], 4), "metodo": "vectorial"}


def _externa_preferida() -> bool:
    """¿Está activo el modo «IA externa» (/ia externa)? Cualquier problema leyendo config = False (flujo de siempre)."""
    try:
        return bool(getattr(_cfg_ie, "IA_EXTERNA_PREFERIDA", False))
    except Exception:
        return False


def _por_respaldo_externo_blindaje(texto_original: str, categorias: list) -> Optional[dict]:
    """
    Respaldo cuando Ollama cae. Intenta Gemini → Groq.
    """
    if respaldo_externo is None:
        return None

    resultado = None
    if respaldo_externo.gemini_disponible():
        try:
            resultado = respaldo_externo.clasificar_via_gemini(
                texto_original, categorias, DESCRIPCIONES_CATEGORIAS, CATEGORIA_DESCONOCIDA
            )
        except Exception as e:
            _log_error("intent_engine", "_por_respaldo_externo_blindaje (gemini)", e)

    if not resultado and respaldo_externo.groq_disponible():
        try:
            resultado = respaldo_externo.clasificar_via_groq(
                texto_original, categorias, DESCRIPCIONES_CATEGORIAS, CATEGORIA_DESCONOCIDA
            )
        except Exception as e:
            _log_error("intent_engine", "_por_respaldo_externo_blindaje (groq)", e)

    if not resultado:
        return None

    categoria = resultado.get("categoria")
    try:
        confianza = float(resultado.get("confianza", 0.0))
    except (TypeError, ValueError):
        return None

    opciones = categorias + [CATEGORIA_DESCONOCIDA]
    if categoria not in opciones or confianza < UMBRAL_CONFIANZA_QWEN:
        return None
    return {"categoria": categoria, "confianza": round(confianza, 4), "metodo": "llm_blindaje"}


def _por_qwen(texto_original: str, texto_limpio: str) -> Optional[dict]:
    """
    Capa 3 — árbitro LLM local (Ollama). Con contexto de usuario (Pilar 1).
    """
    try:
        categorias = db.obtener_categorias_registradas()
    except Exception as e:
        _log_error("intent_engine", "_por_qwen (categorias)", e)
        categorias = []

    if not categorias:
        return None

    # /ia externa: el árbitro es la IA externa y Ollama queda de RESERVA (solo si la externa no está disponible, falló
    # o no tuvo una respuesta fiable). Con /ia local (lo normal) este bloque no hace nada: flujo de siempre.
    if _externa_preferida():
        try:
            externo = _por_respaldo_externo_blindaje(texto_original, categorias)
        except Exception as e:
            _log_error("intent_engine", "_por_qwen (árbitro externo)", e)
            externo = None
        if externo is not None:
            return externo

    opciones = categorias + [CATEGORIA_DESCONOCIDA]
    descripciones = "\n".join(
        f"- {cat}: {DESCRIPCIONES_CATEGORIAS.get(cat, 'sin descripcion adicional')}" for cat in opciones
    )

    contexto_usuario = ""
    if memoria_core is not None:
        try:
            contexto_usuario = memoria_core.get_core_memory_text()
        except Exception as e:
            _log_error("intent_engine", "_por_qwen (memoria_core)", e)

    contexto_prefix = f"Contexto del usuario:\n{contexto_usuario}\n\n" if contexto_usuario else ""

    prompt = (
        f"{contexto_prefix}"
        "Clasifica la frase del usuario en UNA sola categoria. Estas son las categorias validas:\n"
        f"{descripciones}\n\n"
        f"Frase: \"{texto_original}\"\n\n"
        "Si la frase encaja claramente en una categoria, usa confianza alta (0.8+). "
        "Si es ambigua, usa confianza baja (<0.5).\n\n"
        "Responde SOLO un JSON: {\"categoria\": \"<nombre>\", \"confianza\": <0-1>}"
    )

    try:
        import requests
        respuesta = requests.post(
            OLLAMA_URL,
            json={
                "model": QWEN_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": QWEN_FORMAT,
                "think": False,
                "keep_alive": _KEEP_ALIVE,
                "options": {
                    "temperature": QWEN_TEMPERATURA,
                    "num_predict": QWEN_MAX_TOKENS,
                    "num_ctx": QWEN_MAX_CONTEXT,
                },
            },
            timeout=QWEN_TIMEOUT_SEGUNDOS,
        )
        respuesta.raise_for_status()
        cuerpo = respuesta.json().get("response", "")
        datos = json.loads(cuerpo)
        categoria = datos.get("categoria")
        confianza = float(datos.get("confianza", 0.0))
    except _EXCEPCIONES_RED as e:
        _log_error("intent_engine", "_por_qwen (Ollama inalcanzable)", e)
        return _por_respaldo_externo_blindaje(texto_original, categorias)
    except Exception as e:
        _log_error("intent_engine", "_por_qwen", e)
        return None

    if categoria not in opciones or confianza < UMBRAL_CONFIANZA_QWEN:
        return None

    return {"categoria": categoria, "confianza": round(confianza, 4), "metodo": "llm"}


def _finalizar(texto_original: str, texto_limpio: str, resultado: dict) -> None:
    """
    Cierra la clasificación: historial, aprendizaje y caché.
    """
    id_historial = db.guardar_historial_clasificacion(
        texto_original, texto_limpio, resultado["categoria"], resultado["confianza"], resultado["metodo"]
    )
    resultado["id_historial"] = id_historial

    # Refuerzo de aprendizaje solo si confianza es alta
    if resultado.get("metodo") == "llm" and resultado["confianza"] >= UMBRAL_APRENDIZAJE:
        vector = _vector_memo(texto_limpio) if embeddings.esta_disponible() else None
        db.guardar_intencion_aprendida(
            resultado["categoria"], texto_limpio, resultado["categoria"],
            vector=vector, confianza=resultado["confianza"]
        )

    # Caché para siguiente vez
    db.guardar_cache_intencion(texto_limpio, resultado, ttl_segundos=CACHE_TTL_SEGUNDOS)


def confirmar_clasificacion(id_historial: int) -> bool:
    return db.marcar_resultado_historial(id_historial, True)


def validar_clasificacion(id_historial: int, texto_original: str, categoria: str) -> bool:
    if not id_historial or not texto_original or not categoria:
        return False
    if not db.marcar_resultado_historial(id_historial, True):
        return False
    return db.guardar_validacion(texto_original, categoria)


def corregir_clasificacion(id_historial: int, texto_original: str,
                            categoria_erronea: str, categoria_correcta: str,
                            confianza_erronea: float = 0.0) -> bool:
    db.marcar_resultado_historial(id_historial, False)

    if categoria_correcta.upper() in PALABRAS_RESERVADAS_CATEGORIA:
        _log_error("intent_engine", "corregir_clasificacion",
                   ValueError(f"'{categoria_correcta}' es placeholder de /ayuda"))
        return False

    categorias_validas = set(db.obtener_categorias_registradas())
    categorias_validas.add(CATEGORIA_DESCONOCIDA)
    if categoria_correcta not in categorias_validas:
        _log_error("intent_engine", "corregir_clasificacion",
                   ValueError(f"categoria_correcta '{categoria_correcta}' no existe"))
        return False

    return db.guardar_correccion(texto_original, categoria_erronea, categoria_correcta, confianza_erronea)


def calentar_qwen(timeout_segundos: int = 120) -> bool:
    """
    Precarga el modelo en memoria.
    """
    try:
        import requests
        requests.post(
            OLLAMA_URL,
            json={"model": QWEN_MODEL, "prompt": "hola", "stream": False, "think": False,
                  "keep_alive": _KEEP_ALIVE,
                  # MISMO num_ctx que _por_qwen: si cambia entre llamadas Ollama recarga el modelo entero
                  "options": {"num_predict": 5, "num_ctx": QWEN_MAX_CONTEXT}},
            timeout=timeout_segundos,
        )
        return True
    except Exception as e:
        _log_error("intent_engine", "calentar_qwen", e)
        return False


def ajustar_aprendizaje_por_resultado(resultado: dict, exito: bool) -> None:
    """
    Deshacer aprendizaje si la acción no funcionó o reforzar si fue exitosa.
    """
    if not exito:
        categoria = resultado.get("categoria")
        texto_limpio = resultado.get("texto_norm")
        if not categoria or not texto_limpio:
            return

        if categoria in CATEGORIAS_ARCHIVOS:
            db.eliminar_intenciones_por_texto(texto_limpio)
        return

    try:
        idh = resultado.get("id_historial")
        if idh:
            db.marcar_resultado_historial(idh, True)       # alimenta /calibrar y el fixture sin /confirmar manual
        cat, tl = resultado.get("categoria"), resultado.get("texto_norm")
        if resultado.get("metodo") in ("llm", "llm_blindaje") and cat and cat != CATEGORIA_DESCONOCIDA and tl:
            vector = _vector_memo(tl) if embeddings.esta_disponible() else None
            db.guardar_intencion_aprendida(cat, tl, cat, vector=vector,
                                           confianza=float(resultado.get("confianza") or 0.8), fuente="exito")
    except Exception as e:
        _log_error("intent_engine", "ajustar_aprendizaje (éxito)", e)
