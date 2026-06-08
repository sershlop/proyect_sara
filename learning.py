# 📁 learning.py
from config import UMBRAL_DUPLICADO
from utils import normalizar_texto, similitud
from database import (
    guardar_conocimiento, agregar_pregunta, agregar_comando,
    actualizar_respuesta, marcar_pregunta_incorrecta,
    marcar_comando_incorrecto, 
    obtener_conocimientos, obtener_comandos
)
import embeddings
import logger


def _ya_existe_pregunta(pregunta_nueva):
    conocimientos = obtener_conocimientos()
    pregunta_norm = normalizar_texto(pregunta_nueva)
    mejor_score   = 0.0
    mejor_pregunta = None
    for fila in conocimientos:
        score = similitud(pregunta_norm, normalizar_texto(fila["pregunta"]))
        if score > mejor_score:
            mejor_score    = score
            mejor_pregunta = fila["pregunta"]
    return mejor_score >= UMBRAL_DUPLICADO, mejor_pregunta, mejor_score


def _ya_existe_comando(nombre_nuevo):
    comandos    = obtener_comandos()
    nombre_norm = normalizar_texto(nombre_nuevo)
    mejor_score = 0.0
    mejor_nombre = None
    for cmd in comandos:
        score = similitud(nombre_norm, normalizar_texto(cmd["nombre"]))
        if score > mejor_score:
            mejor_score  = score
            mejor_nombre = cmd["nombre"]
    return mejor_score >= UMBRAL_DUPLICADO, mejor_nombre, mejor_score


def aprender_pregunta(pregunta, respuesta):
    try:
        if not pregunta or not pregunta.strip():
            return _resultado(False, "error", "La pregunta no puede estar vacía.")
        if not respuesta or not respuesta.strip():
            return _resultado(False, "error", "La respuesta no puede estar vacía.")

        existe, similar, score = _ya_existe_pregunta(pregunta)
        if existe:
            logger.warning("learning", f"Pregunta duplicada ({round(score*100,1)}%)",
                           f"Nueva: '{pregunta}' | Similar: '{similar}'")
            return _resultado(False, "duplicada",
                              f"Ya existe una similar ({round(score*100,1)}%): '{similar}'")

        agregar_pregunta(pregunta.strip(), respuesta.strip())

        if embeddings.esta_disponible():
            vector = embeddings.vector_desde_texto(normalizar_texto(pregunta.strip()))
            if vector:
                from database import guardar_vector_conocimiento
                guardar_vector_conocimiento(pregunta.strip(), vector)
                logger.debug("learning", f"Vector generado: '{pregunta[:40]}'")

        logger.info("learning", f"Nueva pregunta aprendida: '{pregunta[:50]}'")
        return _resultado(True, "guardada", "Pregunta aprendida correctamente.")

    except Exception as e:
        logger.log_excepcion("learning", pregunta, e)
        return _resultado(False, "error", f"Error al aprender pregunta: {e}")


def aprender_comando(nombre, palabras_clave, accion, tipo, descripcion=""):
    try:
        if not nombre or not nombre.strip():
            return _resultado(False, "error", "El nombre no puede estar vacío.")
        if not accion or not accion.strip():
            return _resultado(False, "error", "La acción no puede estar vacía.")

        TIPOS_VALIDOS = {"web", "app", "sistema", "compuesto", "sistema_control"}
        if tipo not in TIPOS_VALIDOS:
            return _resultado(False, "error", f"Tipo inválido '{tipo}'.")

        existe, similar, score = _ya_existe_comando(nombre)
        if existe:
            logger.warning("learning", f"Comando duplicado ({round(score*100,1)}%)",
                           f"Nuevo: '{nombre}' | Similar: '{similar}'")
            return _resultado(False, "duplicada",
                              f"Ya existe uno similar ({round(score*100,1)}%): '{similar}'")

        agregar_comando(nombre.strip(),
                        palabras_clave.strip() if palabras_clave else "",
                        accion.strip(), tipo,
                        descripcion.strip() if descripcion else "")

        if embeddings.esta_disponible():
            vector = embeddings.vector_desde_texto(normalizar_texto(nombre.strip()))
            if vector:
                from database import guardar_vector_comando
                guardar_vector_comando(nombre.strip(), vector)
                logger.debug("learning", f"Vector generado para comando: '{nombre}'")

        logger.info("learning", f"Nuevo comando aprendido: '{nombre}'")
        return _resultado(True, "guardada", "Comando aprendido correctamente.")

    except Exception as e:
        logger.log_excepcion("learning", nombre, e)
        return _resultado(False, "error", f"Error al aprender comando: {e}")


def aprender_comando_compuesto(nombre, palabras_clave, acciones, descripcion=""):
    try:
        if not nombre or not nombre.strip():
            return _resultado(False, "error", "El nombre no puede estar vacío.")
        if not acciones:
            return _resultado(False, "error", "Debe tener al menos una acción.")

        existe, similar, score = _ya_existe_comando(nombre)
        if existe:
            return _resultado(False, "duplicada", f"Ya existe uno similar: '{similar}'")

        agregar_comando(nombre.strip(),
                        palabras_clave.strip() if palabras_clave else "",
                        "COMPUESTO", "compuesto",
                        descripcion.strip() if descripcion else f"Comando compuesto: {nombre}")

        from database import conectar, guardar_accion_compuesta
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id FROM comandos WHERE nombre = ?
                ORDER BY id DESC LIMIT 1
            """, (nombre.strip(),))
            fila       = cursor.fetchone()
            id_comando = fila["id"] if fila else None

        if not id_comando:
            return _resultado(False, "error", "No se pudo obtener el ID.")

        for accion in acciones:
            guardar_accion_compuesta(
                id_comando,
                accion.get("orden", 1),
                accion.get("accion", ""),
                accion.get("tipo", "app"),
                accion.get("descripcion", "")
            )

        if embeddings.esta_disponible():
            vector = embeddings.vector_desde_texto(normalizar_texto(nombre.strip()))
            if vector:
                from database import guardar_vector_comando
                guardar_vector_comando(nombre.strip(), vector)

        logger.info("learning", f"Comando compuesto guardado: '{nombre}'",
                    f"{len(acciones)} acciones")
        return _resultado(True, "guardada",
                          f"Comando '{nombre}' guardado con {len(acciones)} acciones.")

    except Exception as e:
        logger.log_excepcion("learning", nombre, e)
        return _resultado(False, "error", f"Error: {e}")


def corregir_pregunta(pregunta, respuesta_nueva):
    try:
        if not pregunta or not respuesta_nueva:
            return _resultado(False, "error", "Pregunta y respuesta nueva son obligatorias.")

        
        actualizar_respuesta(pregunta.strip(), respuesta_nueva.strip())

        logger.info("learning", f"Corrección aplicada: '{pregunta[:50]}'",
                    f"Nueva: '{respuesta_nueva[:50]}'")
        return _resultado(True, "corregida", "Respuesta corregida correctamente.")

    except Exception as e:
        logger.log_excepcion("learning", pregunta, e)
        return _resultado(False, "error", f"Error al corregir: {e}")


def marcar_error(tipo, item):
    try:
        if tipo == "pregunta":
            marcar_pregunta_incorrecta(item)
            logger.warning("learning", f"Pregunta marcada incorrecta: '{item[:50]}'")
            return _resultado(True, "marcada", "Pregunta marcada como incorrecta.")
        elif tipo == "comando":
            marcar_comando_incorrecto(item)
            logger.warning("learning", f"Comando desactivado: '{item[:50]}'")
            return _resultado(True, "marcada", "Comando desactivado.")
        else:
            return _resultado(False, "error", f"Tipo inválido: '{tipo}'.")
    except Exception as e:
        logger.log_excepcion("learning", item, e)
        return _resultado(False, "error", f"Error: {e}")


def obtener_estadisticas():
    try:
        conocimientos = obtener_conocimientos()
        comandos      = obtener_comandos()
        stats = {
            "total_conocimientos": len(conocimientos),
            "total_comandos":      len(comandos),
        }
        logger.debug("learning", f"Estadísticas: {stats}")
        return stats
    except Exception as e:
        logger.log_excepcion("learning", "obtener_estadisticas", e)
        return {}


def verificar_similitud(texto1, texto2):
    return similitud(normalizar_texto(texto1), normalizar_texto(texto2))


def _resultado(exito, accion, mensaje):
    return {"exito": exito, "accion": accion, "mensaje": mensaje}

    vdc