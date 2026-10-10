"""
informativo.py – Gestión de preguntas informativas mediante APIs externas.

Este módulo se encarga de responder preguntas de tipo *informativa* (p.ej.
“¿Cuál es la capital de Francia?”) utilizando una de las APIs externas
disponibles (Gemini, Groq o DeepSeek).  Para evitar llamadas repetidas y
costosas, las respuestas se almacenan en una tabla SQLite de historial,
de modo que una pregunta ya atendida se sirve directamente desde la caché.

Principales componentes
-----------------------
* **_log_error** – Centraliza el registro de excepciones.  Intenta usar el
  módulo ``logger`` y, si no está disponible, escribe un mensaje en la
  salida estándar.

* **crear_tablas** – Crea (si no existen) la tabla ``respuestas_informativas_historial``
  con los campos necesarios para guardar la pregunta normalizada,
  la versión original, la respuesta, la fuente y estadísticas de uso.

* **_buscar_respuesta_cacheada** – Consulta la tabla SQLite para obtener una
  respuesta previamente guardada a partir de la pregunta normalizada.

* **_guardar_respuesta** – Inserta una nueva fila o actualiza la existente
  (incrementando ``veces_preguntada`` y refrescando la fecha) cuando se
  obtiene una respuesta nueva de la API externa.

* **resolver_pregunta_informativa** – Orquesta todo el flujo:
  1. Verifica que el ``resultado`` corresponde a la categoría
     ``PREGUNTA_INFORMATIVA``.
  2. Extrae y normaliza la pregunta usando ``utils.extraer_resto`` y
     ``utils.normalizar_texto``.
  3. Busca una respuesta en caché; si la encuentra, la devuelve inmediatamente.
  4. Comprueba la disponibilidad de al menos una API externa (``respaldo_externo``).
  5. Solicita la respuesta a la API, la guarda en la caché y la devuelve.

Dependencias
------------
- ``typing.Optional`` – Anotaciones de tipos.
- ``database.conectar`` – Context manager que abre una conexión SQLite.
- ``utils.normalizar_texto`` y ``utils.extraer_resto`` – Funciones auxiliares
  para procesar el texto de la pregunta.
- ``respaldo_externo`` (opcional) – Módulo que expone:
  ``gemini_disponible()``, ``groq_disponible()``, ``deepseek_disponible()``
  y ``responder_pregunta_informativa(pregunta)``.
- ``logger`` (opcional) – Para registro estructurado de excepciones.

Restricciones
-------------
El módulo **solo** lee y escribe en la tabla de historial; nunca ejecuta
acciones que alteren el sistema operativo ni realice operaciones de
escritura fuera de la base de datos de caché.  Su objetivo es proporcionar
respuestas informativas de forma eficiente y segura.
"""

from typing import Optional

from database import conectar
from utils import normalizar_texto, extraer_resto

try:
    import respaldo_externo
except ImportError:
    respaldo_externo = None


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    try:
        import logger as _logger
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[INFORMATIVO ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


def crear_tablas() -> None:
    try:
        with conectar() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS respuestas_informativas_historial (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pregunta_normalizada TEXT NOT NULL UNIQUE,
                pregunta_original TEXT NOT NULL,
                respuesta TEXT NOT NULL,
                fuente TEXT NOT NULL DEFAULT 'gemini',
                veces_preguntada INTEGER DEFAULT 1,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
    except Exception as e:
        _log_error("informativo", "crear_tablas", e)


def _buscar_respuesta_cacheada(pregunta_norm: str) -> Optional[str]:
    try:
        with conectar() as conn:
            fila = conn.execute(
                "SELECT respuesta FROM respuestas_informativas_historial WHERE pregunta_normalizada = ?",
                (pregunta_norm,),
            ).fetchone()
            return fila["respuesta"] if fila else None
    except Exception as e:
        _log_error("informativo", "_buscar_respuesta_cacheada", e)
        return None


def _guardar_respuesta(pregunta_norm: str, pregunta_original: str, respuesta: str) -> None:
    try:
        with conectar() as conn:
            conn.execute("""
                INSERT INTO respuestas_informativas_historial (pregunta_normalizada, pregunta_original, respuesta)
                VALUES (?, ?, ?)
                ON CONFLICT(pregunta_normalizada) DO UPDATE SET
                    veces_preguntada = veces_preguntada + 1, fecha = CURRENT_TIMESTAMP
            """, (pregunta_norm, pregunta_original, respuesta))
    except Exception as e:
        _log_error("informativo", "_guardar_respuesta", e)


def resolver_pregunta_informativa(resultado: dict) -> Optional[dict]:
    if resultado.get("categoria") != "PREGUNTA_INFORMATIVA":
        return None

    texto_original = resultado.get("texto_original", "")
    texto_norm = resultado.get("texto_norm", "")
    patron = resultado.get("patron")

    pregunta = (extraer_resto(texto_norm, patron) if patron else "").strip() or texto_norm
    pregunta_norm = normalizar_texto(pregunta)

    cacheada = _buscar_respuesta_cacheada(pregunta_norm)
    if cacheada:
        return {"estado": "respondido", "texto": cacheada, "exito": True}

    if respaldo_externo is None or not (respaldo_externo.gemini_disponible()
                                        or respaldo_externo.groq_disponible()
                                        or respaldo_externo.deepseek_disponible()):
        return {"estado": "no_disponible",
                "texto": "No tengo configurada ninguna API externa (Gemini, Groq o DeepSeek) para responder eso.", "exito": False}

    respuesta = respaldo_externo.responder_pregunta_informativa(pregunta)
    if not respuesta:
        return {"estado": "error", "texto": "No pude obtener una respuesta ahora mismo.", "exito": False}

    _guardar_respuesta(pregunta_norm, texto_original, respuesta)
    return {"estado": "respondido", "texto": respuesta, "exito": True}