# -*- coding: utf-8 -*-
"""
perceptor.py — Módulo de percepción y verificación de estado para SARA.

Responsabilidad principal:
    Verificar el estado real del sistema ANTES y DESPUÉS de que SARA actúe.
    No ejecuta acciones por sí mismo — solo observa, confirma y reporta.

Filosofía:
    SARA no debe asumir que una acción tuvo éxito solo porque no lanzó
    una excepción. perceptor.py cierra ese ciclo: percepción previa →
    (acción externa) → percepción posterior → respuesta honesta.

Dependencias clave:
    - utils.py       (normalización de texto, ya existente en SARA)
    - logger.py      (logging por niveles, ya existente en SARA)
    - database.py    (consulta de indice_archivos para sugerencias)
    - psutil         (opcional — procesos, batería, uso de RAM/CPU)

Convenciones respetadas (Documento Maestro SARA v0.3.0):
    - Formato de retorno estándar {"exito": bool, "mensaje": str} en toda
      función de verificación booleana, con campos adicionales cuando aporta
      información (ej. "detalle", "sugerencias").
    - normalizar_texto() antes de TODA comparación de strings.
    - Try/except obligatorio en toda función que toque disco, BD, red o
      procesos del sistema. Ninguna excepción debe propagarse fuera de
      este módulo.
    - SARA debe arrancar siempre aunque un módulo opcional falle: si
      psutil no está instalado, las funciones que lo requieren degradan
      a un resultado seguro en lugar de lanzar ImportError.
    - Imports dentro de función cuando hay riesgo de import circular
      (database.py, logger.py se importan así).
    - Nunca se ejecuta código arbitrario aquí. validar_sintaxis_python()
      y compilar_check() usan ast/compile en modo estático, sin exec().
"""

from __future__ import annotations

import ast
import os
import socket
import shutil
import subprocess
import time
from importlib import metadata as _importlib_metadata
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────
# DISPONIBILIDAD DE psutil (dependencia opcional)
# ──────────────────────────────────────────────────────────────────────────
# psutil ya está contemplado en el roadmap de SARA (sección 16, "Mayor
# conciencia del entorno"). Se importa de forma defensiva: si no está
# disponible, las funciones que lo necesitan devuelven un resultado
# seguro en lugar de romper el arranque de SARA.
try:
    import psutil  # type: ignore
    PSUTIL_DISPONIBLE = True
except ImportError:
    psutil = None  # type: ignore
    PSUTIL_DISPONIBLE = False


# ──────────────────────────────────────────────────────────────────────────
# CONSTANTES DE CONFIGURACIÓN LOCAL
# ──────────────────────────────────────────────────────────────────────────
# Timeout por defecto para verificaciones de red/servicios. Las verificaciones
# de percepción deben ser rápidas — nunca deben colgar el pipeline de brain.py.
TIMEOUT_PUERTO_SEGUNDOS = 1.5
TIMEOUT_SERVICIO_SEGUNDOS = 3.0

# Extensiones de audio reconocidas para la futura integración con
# intent_router.py (reproducción de música desde índice local).
EXTENSIONES_AUDIO = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".wma"}


# ──────────────────────────────────────────────────────────────────────────
# UTILIDAD INTERNA DE LOGGING SEGURO
# ──────────────────────────────────────────────────────────────────────────
def _log(nivel: str, mensaje: str, detalle: str = "") -> None:
    """
    Wrapper interno de logging. Igual que _emitir() en sara.py para la GUI,
    nunca debe lanzar excepciones ni romper el flujo de percepción aunque
    el logger no esté disponible (por ejemplo, en pruebas aisladas del módulo).
    """
    try:
        import logger  # import local — evita ciclo con módulos que importan perceptor
        if nivel == "debug":
            logger.debug("perceptor", mensaje)
        elif nivel == "warning":
            logger.warning("perceptor", mensaje, detalle)
        elif nivel == "error":
            logger.error("perceptor", mensaje, detalle)
        else:
            logger.info("perceptor", mensaje)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────
# NIVEL 1 — VERIFICACIÓN DE EXISTENCIA (archivos, carpetas, apps, procesos)
# ──────────────────────────────────────────────────────────────────────────

def existe_archivo(ruta: str) -> dict:
    """
    Verifica si un archivo existe físicamente en disco.

    Args:
        ruta: Ruta absoluta o relativa al archivo.

    Returns:
        {"exito": bool, "mensaje": str}
        exito=True si el archivo existe y es un archivo (no carpeta).
    """
    if not ruta or not isinstance(ruta, str):
        return {"exito": False, "mensaje": "Ruta vacía o inválida."}

    try:
        existe = os.path.isfile(ruta)
        if existe:
            return {"exito": True, "mensaje": f"El archivo existe: {ruta}"}
        return {"exito": False, "mensaje": f"No se encontró el archivo: {ruta}"}
    except Exception as e:
        _log("error", "Fallo al verificar existencia de archivo", str(e))
        return {"exito": False, "mensaje": "No se pudo verificar el archivo."}


def existe_carpeta(ruta: str) -> dict:
    """
    Verifica si una carpeta existe físicamente en disco.

    Args:
        ruta: Ruta absoluta o relativa a la carpeta.

    Returns:
        {"exito": bool, "mensaje": str}
    """
    if not ruta or not isinstance(ruta, str):
        return {"exito": False, "mensaje": "Ruta vacía o inválida."}

    try:
        existe = os.path.isdir(ruta)
        if existe:
            return {"exito": True, "mensaje": f"La carpeta existe: {ruta}"}
        return {"exito": False, "mensaje": f"No se encontró la carpeta: {ruta}"}
    except Exception as e:
        _log("error", "Fallo al verificar existencia de carpeta", str(e))
        return {"exito": False, "mensaje": "No se pudo verificar la carpeta."}


def existe_archivo_o_carpeta_similar(ruta_buscada: str, limite_sugerencias: int = 3) -> dict:
    """
    Cuando una ruta NO existe, busca en la base de datos de SARA
    (tabla indice_archivos, poblada por file_watcher.py) candidatos
    con nombre parecido, para que SARA pueda sugerir una alternativa
    en vez de simplemente reportar el fallo.

    Ejemplo de uso real: el usuario pide abrir "Proyecto X" pero la
    carpeta fue renombrada a "Proyecto_X_backup". En vez de fallar en
    silencio, SARA puede preguntar "¿Quisiste decir Proyecto_X_backup?".

    Args:
        ruta_buscada: Nombre o ruta que el usuario mencionó.
        limite_sugerencias: Máximo de candidatos a devolver.

    Returns:
        {"exito": bool, "mensaje": str, "sugerencias": list[dict]}
        "sugerencias" es una lista de {"nombre": str, "ruta": str, "tipo": str}.
    """
    try:
        from utils import normalizar_texto
        nombre_normalizado = normalizar_texto(ruta_buscada)
    except Exception:
        # Si utils no está disponible (uso aislado del módulo), se
        # degrada a una normalización mínima en lugar de fallar.
        nombre_normalizado = ruta_buscada.strip().lower()

    try:
        import database

        candidatos = database.buscar_en_indice_por_similitud(
            nombre_normalizado, limite=limite_sugerencias
        )
        # buscar_en_indice_por_similitud es la función equivalente al
        # patrón ya usado por file_intent.buscar_en_indice(). Se asume
        # que retorna una lista de dicts con nombre/ruta/tipo.
        sugerencias = candidatos or []

        if sugerencias:
            nombres = ", ".join(c.get("nombre", "?") for c in sugerencias)
            return {
                "exito": True,
                "mensaje": f"No encontré '{ruta_buscada}', pero hay coincidencias: {nombres}",
                "sugerencias": sugerencias,
            }

        return {
            "exito": False,
            "mensaje": f"No encontré '{ruta_buscada}' ni nada similar en el índice.",
            "sugerencias": [],
        }
    except Exception as e:
        _log("warning", "No se pudo consultar el índice de archivos para sugerencias", str(e))
        return {
            "exito": False,
            "mensaje": f"No encontré '{ruta_buscada}' y no pude consultar sugerencias.",
            "sugerencias": [],
        }


def existe_app_instalada(nombre_ejecutable: str) -> dict:
    """
    Verifica si un ejecutable está disponible en el PATH del sistema,
    sin necesidad de lanzarlo. Útil antes de intentar abrir una app
    o antes de ofrecer instalarla.

    Args:
        nombre_ejecutable: Nombre del ejecutable, con o sin extensión
                            (ej. "code", "python", "notepad.exe").

    Returns:
        {"exito": bool, "mensaje": str, "ruta": str|None}
    """
    if not nombre_ejecutable or not isinstance(nombre_ejecutable, str):
        return {"exito": False, "mensaje": "Nombre de aplicación vacío o inválido.", "ruta": None}

    try:
        ruta_encontrada = shutil.which(nombre_ejecutable)
        if ruta_encontrada:
            return {
                "exito": True,
                "mensaje": f"'{nombre_ejecutable}' está instalado.",
                "ruta": ruta_encontrada,
            }
        return {
            "exito": False,
            "mensaje": f"'{nombre_ejecutable}' no se encuentra instalado o no está en el PATH.",
            "ruta": None,
        }
    except Exception as e:
        _log("error", "Fallo al verificar app instalada", str(e))
        return {"exito": False, "mensaje": "No se pudo verificar la aplicación.", "ruta": None}


def app_esta_corriendo(nombre_proceso: str) -> dict:
    """
    Verifica si un proceso con el nombre dado está corriendo actualmente.

    Requiere psutil. Si psutil no está disponible, degrada a un resultado
    seguro indicándolo explícitamente en el mensaje (no lanza excepción).

    Args:
        nombre_proceso: Nombre del proceso, ej. "chrome.exe", "Spotify.exe".
                         La comparación ignora mayúsculas/minúsculas.

    Returns:
        {"exito": bool, "mensaje": str, "pids": list[int]}
    """
    if not nombre_proceso or not isinstance(nombre_proceso, str):
        return {"exito": False, "mensaje": "Nombre de proceso vacío o inválido.", "pids": []}

    if not PSUTIL_DISPONIBLE:
        _log("warning", "app_esta_corriendo() llamado sin psutil instalado")
        return {
            "exito": False,
            "mensaje": "No puedo verificar procesos activos: falta el paquete psutil.",
            "pids": [],
        }

    objetivo = nombre_proceso.strip().lower()
    pids_encontrados = []

    try:
        for proceso in psutil.process_iter(attrs=["pid", "name"]):
            try:
                nombre_actual = (proceso.info.get("name") or "").lower()
                if nombre_actual == objetivo or nombre_actual == f"{objetivo}.exe":
                    pids_encontrados.append(proceso.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # El proceso pudo terminar entre la enumeración y la
                # lectura de sus atributos — no es un error de SARA.
                continue

        if pids_encontrados:
            return {
                "exito": True,
                "mensaje": f"'{nombre_proceso}' está corriendo ({len(pids_encontrados)} proceso(s)).",
                "pids": pids_encontrados,
            }
        return {
            "exito": False,
            "mensaje": f"'{nombre_proceso}' no está corriendo actualmente.",
            "pids": [],
        }
    except Exception as e:
        _log("error", "Fallo al verificar proceso en ejecución", str(e))
        return {"exito": False, "mensaje": "No se pudo verificar el proceso.", "pids": []}


# ──────────────────────────────────────────────────────────────────────────
# NIVEL 2 — VERIFICACIÓN DE CÓDIGO GENERADO (sintaxis, sin ejecución)
# ──────────────────────────────────────────────────────────────────────────

def validar_sintaxis_python(codigo: str) -> dict:
    """
    Verifica que un fragmento de código Python sea sintácticamente
    válido, SIN ejecutarlo. Usa ast.parse() de la librería estándar.

    Pensado para validar código generado por external_service.py (Groq)
    antes de guardarlo en scripts/ o de presentarlo como "listo" al usuario.

    Args:
        codigo: Código fuente Python como string.

    Returns:
        {"exito": bool, "mensaje": str, "linea_error": int|None}
    """
    if not codigo or not isinstance(codigo, str) or not codigo.strip():
        return {"exito": False, "mensaje": "No hay código para validar.", "linea_error": None}

    try:
        ast.parse(codigo)
        return {"exito": True, "mensaje": "El código es sintácticamente válido.", "linea_error": None}
    except SyntaxError as e:
        return {
            "exito": False,
            "mensaje": f"Error de sintaxis: {e.msg} (línea {e.lineno}).",
            "linea_error": e.lineno,
        }
    except Exception as e:
        # Errores inesperados de ast.parse (codificación, recursión, etc.)
        # se reportan sin exponer trazas internas al usuario final.
        _log("error", "Fallo inesperado al validar sintaxis", str(e))
        return {"exito": False, "mensaje": "No se pudo analizar el código.", "linea_error": None}


def compilar_check(codigo: str, nombre_archivo: str = "<sara_check>") -> dict:
    """
    Verificación más profunda que validar_sintaxis_python(): intenta
    compilar el código a bytecode con compile(). Esto detecta, además
    de errores de sintaxis, algunos errores estructurales (por ejemplo,
    'return' fuera de una función) que ast.parse() por sí solo no atrapa
    en todos los casos.

    IMPORTANTE: compile() NO ejecuta el código. Es seguro. Nunca se
    invoca exec() ni eval() en este módulo.

    Args:
        codigo: Código fuente Python como string.
        nombre_archivo: Nombre simbólico para los mensajes de error
                         (no se escribe a disco).

    Returns:
        {"exito": bool, "mensaje": str, "linea_error": int|None}
    """
    if not codigo or not isinstance(codigo, str) or not codigo.strip():
        return {"exito": False, "mensaje": "No hay código para compilar.", "linea_error": None}

    try:
        compile(codigo, nombre_archivo, mode="exec")
        return {"exito": True, "mensaje": "El código compila sin errores.", "linea_error": None}
    except SyntaxError as e:
        return {
            "exito": False,
            "mensaje": f"Error al compilar: {e.msg} (línea {e.lineno}).",
            "linea_error": e.lineno,
        }
    except ValueError as e:
        # compile() puede lanzar ValueError con bytes nulos u otras
        # entradas malformadas que ast.parse() acepta pero compile() no.
        return {"exito": False, "mensaje": f"Código inválido: {e}", "linea_error": None}
    except Exception as e:
        _log("error", "Fallo inesperado al compilar código", str(e))
        return {"exito": False, "mensaje": "No se pudo compilar el código.", "linea_error": None}


def verificar_script_generado(codigo: str) -> dict:
    """
    Verificación combinada de conveniencia: aplica validar_sintaxis_python()
    y, solo si pasa, compilar_check(). Pensado como punto único de entrada
    para sara.py al terminar de recibir código de external_service.py.

    Args:
        codigo: Código fuente Python generado por un modelo externo.

    Returns:
        {"exito": bool, "mensaje": str, "linea_error": int|None}
    """
    resultado_sintaxis = validar_sintaxis_python(codigo)
    if not resultado_sintaxis["exito"]:
        return resultado_sintaxis

    resultado_compilacion = compilar_check(codigo)
    if not resultado_compilacion["exito"]:
        return resultado_compilacion

    return {"exito": True, "mensaje": "El script generado es válido y compila correctamente.", "linea_error": None}


# ──────────────────────────────────────────────────────────────────────────
# NIVEL 3 — VERIFICACIÓN DE SERVICIOS, PUERTOS Y SALUD DEL ENTORNO
# ──────────────────────────────────────────────────────────────────────────

def puerto_libre(puerto: int, host: str = "127.0.0.1") -> dict:
    """
    Verifica si un puerto TCP está libre (nadie escuchando en él) en
    el host indicado. Útil antes de que server.py intente levantar el
    WebSocket de la GUI, o antes de lanzar cualquier servicio propio.

    Args:
        puerto: Número de puerto a verificar.
        host: Host a verificar (por defecto localhost).

    Returns:
        {"exito": bool, "mensaje": str}
        exito=True significa que el puerto está LIBRE.
    """
    if not isinstance(puerto, int) or not (0 < puerto < 65536):
        return {"exito": False, "mensaje": f"Puerto inválido: {puerto}"}

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT_PUERTO_SEGUNDOS)
            resultado = sock.connect_ex((host, puerto))
            # connect_ex retorna 0 si la conexión tuvo éxito → algo está
            # escuchando en ese puerto → el puerto NO está libre.
            if resultado == 0:
                return {"exito": False, "mensaje": f"El puerto {puerto} está ocupado."}
            return {"exito": True, "mensaje": f"El puerto {puerto} está libre."}
    except Exception as e:
        _log("error", "Fallo al verificar disponibilidad de puerto", str(e))
        return {"exito": False, "mensaje": f"No se pudo verificar el puerto {puerto}."}


def servicio_responde(url: str, timeout: float = TIMEOUT_SERVICIO_SEGUNDOS) -> dict:
    """
    Verifica si un servicio HTTP responde en la URL indicada.

    Usa exclusivamente la librería estándar (urllib) para no introducir
    una dependencia nueva como requests solo para un chequeo de salud.

    Args:
        url: URL completa a verificar, ej. "http://127.0.0.1:8765/health".
        timeout: Tiempo máximo de espera en segundos.

    Returns:
        {"exito": bool, "mensaje": str, "codigo_estado": int|None}
    """
    if not url or not isinstance(url, str):
        return {"exito": False, "mensaje": "URL vacía o inválida.", "codigo_estado": None}

    try:
        import urllib.request
        import urllib.error

        peticion = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            codigo = respuesta.getcode()
            if 200 <= codigo < 400:
                return {"exito": True, "mensaje": f"El servicio respondió ({codigo}).", "codigo_estado": codigo}
            return {"exito": False, "mensaje": f"El servicio respondió con código {codigo}.", "codigo_estado": codigo}
    except urllib.error.URLError as e:
        return {"exito": False, "mensaje": f"El servicio no respondió: {e.reason}", "codigo_estado": None}
    except Exception as e:
        _log("error", "Fallo al verificar servicio HTTP", str(e))
        return {"exito": False, "mensaje": "No se pudo verificar el servicio.", "codigo_estado": None}


def ollama_esta_vivo(puerto: int = 11434) -> dict:
    """
    Verificación específica y de alta frecuencia de uso: comprueba si
    Ollama (motor del modelo Qwen local) está respondiendo. Pensado para
    usarse antes de delegar al árbitro Qwen en brain.py, evitando que
    SARA descubra que Qwen no responde solo cuando ya intentó usarlo.

    Args:
        puerto: Puerto de Ollama (por defecto 11434, el estándar de Ollama).

    Returns:
        {"exito": bool, "mensaje": str}
    """
    resultado = servicio_responde(f"http://127.0.0.1:{puerto}/", timeout=2.0)
    if resultado["exito"]:
        return {"exito": True, "mensaje": "Ollama está respondiendo correctamente."}
    return {"exito": False, "mensaje": "Ollama no está respondiendo. Puede no estar corriendo."}


def espacio_disco_libre(unidad: str = "C:\\") -> dict:
    """
    Verifica el espacio libre en disco de una unidad, y marca alerta
    cuando el porcentaje libre cae por debajo de un umbral crítico.

    Pensado para ser consultado por sentinel.py (vigilancia proactiva)
    o directamente por brain.py ante preguntas como "¿cuánto espacio
    me queda?".

    Args:
        unidad: Unidad o ruta a verificar. En Windows, ej. "C:\\".
                En Linux/macOS, puede pasarse "/" para compatibilidad
                con las plataformas secundarias mencionadas en el
                Documento Maestro.

    Returns:
        {"exito": bool, "mensaje": str, "libre_gb": float|None,
         "porcentaje_libre": float|None, "alerta": bool}
    """
    try:
        total, usado, libre = shutil.disk_usage(unidad)
        libre_gb = round(libre / (1024 ** 3), 2)
        total_gb = round(total / (1024 ** 3), 2)
        porcentaje_libre = round((libre / total) * 100, 1) if total > 0 else 0.0

        alerta = porcentaje_libre < 10.0

        mensaje = f"{unidad} tiene {libre_gb} GB libres de {total_gb} GB ({porcentaje_libre}%)."
        if alerta:
            mensaje += " Espacio crítico."

        return {
            "exito": True,
            "mensaje": mensaje,
            "libre_gb": libre_gb,
            "porcentaje_libre": porcentaje_libre,
            "alerta": alerta,
        }
    except Exception as e:
        _log("error", "Fallo al verificar espacio en disco", str(e))
        return {
            "exito": False,
            "mensaje": f"No se pudo verificar el espacio en {unidad}.",
            "libre_gb": None,
            "porcentaje_libre": None,
            "alerta": False,
        }


def ram_disponible() -> dict:
    """
    Verifica la memoria RAM disponible actualmente en el sistema.

    Requiere psutil. Degrada de forma segura si no está instalado.

    Returns:
        {"exito": bool, "mensaje": str, "disponible_gb": float|None,
         "porcentaje_uso": float|None, "alerta": bool}
    """
    if not PSUTIL_DISPONIBLE:
        return {
            "exito": False,
            "mensaje": "No puedo verificar la RAM: falta el paquete psutil.",
            "disponible_gb": None,
            "porcentaje_uso": None,
            "alerta": False,
        }

    try:
        memoria = psutil.virtual_memory()
        disponible_gb = round(memoria.available / (1024 ** 3), 2)
        porcentaje_uso = memoria.percent
        alerta = porcentaje_uso > 90.0

        mensaje = f"Hay {disponible_gb} GB de RAM disponible (uso actual {porcentaje_uso}%)."
        if alerta:
            mensaje += " Uso de memoria crítico."

        return {
            "exito": True,
            "mensaje": mensaje,
            "disponible_gb": disponible_gb,
            "porcentaje_uso": porcentaje_uso,
            "alerta": alerta,
        }
    except Exception as e:
        _log("error", "Fallo al verificar RAM disponible", str(e))
        return {
            "exito": False,
            "mensaje": "No se pudo verificar la memoria RAM.",
            "disponible_gb": None,
            "porcentaje_uso": None,
            "alerta": False,
        }


# ──────────────────────────────────────────────────────────────────────────
# VERIFICACIÓN DE PAQUETES (soporte para instalaciones controladas en shell.py)
# ──────────────────────────────────────────────────────────────────────────

def paquete_pip_instalado(nombre_paquete: str) -> dict:
    """
    Verifica si un paquete de Python ya está instalado, y con qué versión,
    SIN ejecutar pip install. Pensado para que shell.py consulte esto antes
    de proponer una instalación: evita decirle al usuario "voy a instalar X"
    cuando X ya está disponible.

    Usa importlib.metadata (librería estándar desde Python 3.8) en lugar
    de invocar "pip show" como subproceso, por velocidad y porque no
    depende de que pip esté en el PATH.

    Args:
        nombre_paquete: Nombre del paquete tal como se instalaría
                         (ej. "requests", "fastapi").

    Returns:
        {"exito": bool, "mensaje": str, "version": str|None}
        exito=True significa que el paquete YA está instalado.
    """
    if not nombre_paquete or not isinstance(nombre_paquete, str):
        return {"exito": False, "mensaje": "Nombre de paquete vacío o inválido.", "version": None}

    try:
        version = _importlib_metadata.version(nombre_paquete)
        return {
            "exito": True,
            "mensaje": f"'{nombre_paquete}' ya está instalado (versión {version}).",
            "version": version,
        }
    except _importlib_metadata.PackageNotFoundError:
        return {
            "exito": False,
            "mensaje": f"'{nombre_paquete}' no está instalado.",
            "version": None,
        }
    except Exception as e:
        _log("error", "Fallo al verificar paquete pip instalado", str(e))
        return {"exito": False, "mensaje": "No se pudo verificar el paquete.", "version": None}


def comando_disponible(nombre_comando: str) -> dict:
    """
    Verifica si un comando de terminal está disponible en el PATH,
    sin ejecutarlo. Es la base para que shell.py decida si una
    herramienta como "git", "node" o "npm" existe antes de intentar
    usarla.

    Es funcionalmente similar a existe_app_instalada(), pero se expone
    de forma separada porque semánticamente representa una pregunta
    distinta para quien llama (¿tengo esta herramienta de CLI? vs
    ¿tengo esta aplicación instalada?).

    Args:
        nombre_comando: Nombre del comando, ej. "git", "node", "npm".

    Returns:
        {"exito": bool, "mensaje": str, "ruta": str|None}
    """
    return existe_app_instalada(nombre_comando)


# ──────────────────────────────────────────────────────────────────────────
# PATRÓN DE CONVENIENCIA — VERIFICACIÓN POSTERIOR A UNA ACCIÓN
# ──────────────────────────────────────────────────────────────────────────

def verificar_resultado_apertura(
    ruta_o_nombre: str,
    proceso_esperado: Optional[str] = None,
    espera_segundos: float = 1.0,
) -> dict:
    """
    Verificación posterior genérica tras intentar abrir un archivo,
    carpeta o aplicación. Da tiempo a que el proceso arranque y luego
    comprueba que efectivamente esté corriendo, en lugar de asumir
    éxito solo porque subprocess.Popen() no lanzó una excepción.

    Esta función materializa el patrón "Acción + Verificación" descrito
    en el diseño de PRAXIS: cierra el ciclo entre commands.py/shell.py
    (que ejecutan) y la confirmación real del resultado.

    Args:
        ruta_o_nombre: Identificador de lo que se intentó abrir, usado
                        solo para los mensajes de respuesta.
        proceso_esperado: Nombre del proceso que debería haber arrancado
                            (ej. "explorer.exe", "Spotify.exe"). Si es
                            None, la función solo confirma que no hubo
                            errores evidentes y no puede garantizar el
                            arranque del proceso.
        espera_segundos: Cuánto esperar antes de verificar, para dar
                          tiempo al sistema operativo a lanzar el proceso.

    Returns:
        {"exito": bool, "mensaje": str}
    """
    try:
        if espera_segundos > 0:
            time.sleep(min(espera_segundos, 5.0))  # tope defensivo de 5s

        if proceso_esperado:
            chequeo = app_esta_corriendo(proceso_esperado)
            if chequeo["exito"]:
                return {"exito": True, "mensaje": f"Confirmado: {ruta_o_nombre} se abrió correctamente."}
            if not PSUTIL_DISPONIBLE:
                # Sin psutil no se puede confirmar con certeza — se
                # reporta éxito condicional sin afirmar de más.
                return {
                    "exito": True,
                    "mensaje": f"Intenté abrir {ruta_o_nombre}. No puedo confirmar el proceso sin psutil.",
                }
            return {
                "exito": False,
                "mensaje": f"Intenté abrir {ruta_o_nombre}, pero no detecto el proceso activo.",
            }

        # Sin proceso_esperado, no hay nada más que verificar de forma genérica.
        return {"exito": True, "mensaje": f"Se ejecutó la apertura de {ruta_o_nombre}."}

    except Exception as e:
        _log("error", "Fallo al verificar resultado de apertura", str(e))
        return {"exito": False, "mensaje": "No se pudo confirmar el resultado de la acción."}


# ──────────────────────────────────────────────────────────────────────────
# UTILIDAD — IDENTIFICACIÓN DE ARCHIVOS DE AUDIO (soporte a intent_router.py)
# ──────────────────────────────────────────────────────────────────────────

def es_archivo_audio(ruta: str) -> dict:
    """
    Verifica si una ruta corresponde a un archivo de audio reconocido,
    basándose en su extensión. Pensado como utilidad de apoyo para que
    intent_router.py distinga "reproducir música" de "abrir carpeta".

    Args:
        ruta: Ruta o nombre de archivo a evaluar.

    Returns:
        {"exito": bool, "mensaje": str}
    """
    if not ruta or not isinstance(ruta, str):
        return {"exito": False, "mensaje": "Ruta vacía o inválida."}

    try:
        _, extension = os.path.splitext(ruta)
        if extension.lower() in EXTENSIONES_AUDIO:
            return {"exito": True, "mensaje": f"{ruta} es un archivo de audio."}
        return {"exito": False, "mensaje": f"{ruta} no es un archivo de audio reconocido."}
    except Exception as e:
        _log("error", "Fallo al evaluar extensión de audio", str(e))
        return {"exito": False, "mensaje": "No se pudo evaluar el archivo."}
