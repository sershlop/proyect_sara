"""Logging local minimo para SARA.

No importa database.py para evitar ciclos de importacion. Los logs se quedan
locales en el proyecto y sirven como evidencia de fallos y fallbacks.
"""

import logging
import os
from logging.handlers import RotatingFileHandler


_RUTA_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sara.log")
_LOGGER = logging.getLogger("sara")

if not _LOGGER.handlers:
    _LOGGER.setLevel(logging.INFO)
    _HANDLER = RotatingFileHandler(
        _RUTA_LOG,
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    _HANDLER.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s"
    ))
    _LOGGER.addHandler(_HANDLER)
    _LOGGER.propagate = False


def log_excepcion(origen: str, contexto: str, excepcion: Exception) -> None:
    """Registra una excepcion con su traceback sin depender de la base de datos."""
    try:
        _LOGGER.error(
            "%s.%s: %s",
            origen,
            contexto,
            excepcion,
            exc_info=(type(excepcion), excepcion, excepcion.__traceback__),
        )
    except Exception as error:
        print(f"[LOGGER ERROR] No pude escribir {origen}.{contexto}: {error}")
