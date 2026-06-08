# 📁 logger.py
import sys
import traceback
from database import guardar_log, guardar_log_comando, guardar_log_pregunta, guardar_log_error


def _console_supports_emoji():
    enc = sys.stdout.encoding or 'utf-8'
    try:
        "🔍".encode(enc)
        return True
    except Exception:
        return False

NIVELES = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
NIVEL_CONSOLA = "DEBUG"
NIVEL_BD      = "INFO"


def _log(nivel, tipo, mensaje, detalle=""):
    nivel_num         = NIVELES.get(nivel, 1)
    nivel_consola_num = NIVELES.get(NIVEL_CONSOLA, 0)
    nivel_bd_num      = NIVELES.get(NIVEL_BD, 1)

    if nivel_num >= nivel_consola_num:
        _imprimir_consola(nivel, tipo, mensaje, detalle)

    if nivel_num >= nivel_bd_num:
        try:
            guardar_log(f"{nivel}:{tipo}", mensaje, detalle)
        except Exception as e:
            print(f"[LOGGER BD ERROR] No se pudo guardar log: {e}")


def _imprimir_consola(nivel, tipo, mensaje, detalle):
    if _console_supports_emoji():
        iconos = {
            "DEBUG":    "🔍",
            "INFO":     "ℹ️ ",
            "WARNING":  "⚠️ ",
            "ERROR":    "❌",
            "CRITICAL": "🔥"
        }
    else:
        iconos = {
            "DEBUG":    "[DEBUG]",
            "INFO":     "[INFO] ",
            "WARNING":  "[WARN] ",
            "ERROR":    "[ERR]  ",
            "CRITICAL": "[CRIT] "
        }
    icono = iconos.get(nivel, "[LOG]")
    linea = f"{icono} [{nivel}][{tipo}] {mensaje}"
    if detalle:
        linea += f"\n    → {detalle}"
    print(linea)


def debug(tipo, mensaje, detalle=""):
    _log("DEBUG", tipo, mensaje, detalle)

def info(tipo, mensaje, detalle=""):
    _log("INFO", tipo, mensaje, detalle)

def warning(tipo, mensaje, detalle=""):
    _log("WARNING", tipo, mensaje, detalle)

def error(tipo, mensaje, detalle=""):
    _log("ERROR", tipo, mensaje, detalle)

def critical(tipo, mensaje, detalle=""):
    _log("CRITICAL", tipo, mensaje, detalle)

def log_inicio():
    info("sistema", "SARA iniciada correctamente")

def log_cierre():
    info("sistema", "SARA cerrada por el usuario")

def log_comando(comando, exito=True):
    try:
        registro = {
            "id_comando": comando.get("id"),
            "nombre":     comando.get("nombre", "desconocido"),
            "exito":      exito
        }
        guardar_log_comando(registro)
        if exito:
            info("comando", f"Ejecutado: {registro['nombre']}", f"id={registro['id_comando']}")
        else:
            warning("comando", f"Falló: {registro['nombre']}", f"id={registro['id_comando']}")
    except Exception as e:
        error("logger", "Error en log_comando", str(e))

def log_pregunta(pregunta, respuesta=None, correcta=True):
    try:
        registro = {
            "pregunta":          pregunta,
            "respuesta_usuario": respuesta,
            "correcta":          correcta
        }
        guardar_log_pregunta(registro)
        if correcta:
            debug("pregunta", f"Respondida: {pregunta[:50]}")
        else:
            warning("pregunta", f"Sin respuesta: {pregunta[:50]}")
    except Exception as e:
        error("logger", "Error en log_pregunta", str(e))

def log_error(tipo, item, descripcion=""):
    try:
        registro = {"tipo": tipo, "item": item, "descripcion": descripcion}
        guardar_log_error(registro)
        error(tipo, f"Error en: {str(item)[:50]}", descripcion[:100] if descripcion else "")
    except Exception as e:
        print(f"[LOGGER CRÍTICO] No se pudo registrar error: {e}")

def log_excepcion(tipo, item, excepcion):
    try:
        tb          = traceback.format_exc()
        descripcion = f"{type(excepcion).__name__}: {excepcion}\n{tb}"
        registro    = {"tipo": tipo, "item": item, "descripcion": descripcion}
        guardar_log_error(registro)
        error(tipo, f"Excepción en: {str(item)[:50]}", f"{type(excepcion).__name__}: {excepcion}")
    except Exception as e:
        print(f"[LOGGER CRÍTICO] No se pudo registrar excepción: {e}")

def log_intencion_desconocida(texto):
    warning("intencion", f"No clasificada: {texto[:60]}")