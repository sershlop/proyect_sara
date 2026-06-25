# -*- coding: utf-8 -*-
"""
intent_router.py — Discriminador de intención para SARA v0.4.0 (Subsistema PRAXIS).

Responsabilidad principal:
    Interceptar entradas del usuario ANTES de que file_intent.detectar_intencion_archivo()
    las evalúe, y clasificar con precisión la categoría de acción que el usuario quiere
    ejecutar. Resuelve de raíz el bug documentado donde "pon música" → apertura de carpeta
    Música en lugar de reproducción.

Problema de fondo que este módulo soluciona:
    file_intent.py evalúa en Capa 0.5 y devuelve confianza 1.0 cuando detecta una keyword
    de CARPETAS_SISTEMA (incluida "musica"). Como esa capa tiene prioridad sobre la
    clasificación de brain.py (que sí sabe que "pon"/"reproduce" son verbos de reproducción
    pero lo descubre en Capa 2, demasiado tarde), el ranking siempre elige la apertura
    de carpeta. intent_router.py intercepta ANTES de ambas capas.

Arquitectura de decisión (dos capas en cascada):
    Capa A — Diccionario de verbos (determinista, <1ms):
        Clasifica por el verbo líder de la entrada. Sin ambigüedad de verbo → respuesta
        inmediata sin tocar embeddings. Cubre >90% de los casos reales.

    Capa B — Desambiguación semántica (solo en empates):
        Cuando el verbo no es suficiente para decidir (casos ambiguos reales), consulta
        embeddings.py con ejemplos prototipo de cada intención. Solo si el margen de
        similitud es < MARGEN_EMPATE (0.08, el mismo umbral de brain.py).

Categorías de intención gestionadas:
    - REPRODUCIR    : "pon música", "reproduce algo", "toca esa canción"
    - ABRIR         : "abre Chrome", "abre la carpeta documentos"
    - BUSCAR        : "busca en Google", "encuentra ese archivo"
    - SHELL_INFO    : "cuánta RAM tengo", "qué procesos están corriendo"
    - SHELL_ACCION  : "cierra Chrome", "instala requests"
    - CODIGO        : "crea un script", "escríbeme un programa"
    - ARCHIVO       : intención de archivo/carpeta genérica (delega a file_intent)
    - DESCONOCIDA   : ningún verbo líder coincide → flujo normal de brain.py

Integración en el pipeline de brain.procesar():
    La función clasificar() se llama como primera instrucción de brain.procesar(),
    ANTES de la Capa -1 (MAPA_COMANDOS_SISTEMA). Retorna un dict con la categoría
    y metadatos. Si la categoría es REPRODUCIR, brain.py actúa directamente sobre
    shell.py sin pasar por file_intent. Si la categoría es DESCONOCIDA o ARCHIVO,
    el flujo continúa exactamente como antes (sin cambios de comportamiento).

Dependencias:
    - utils.py       (normalizar_texto, empieza_con_palabras — ya en SARA)
    - config.py      (MARGEN_EMPATE, VERBOS_REPRODUCCION — nuevas constantes)
    - embeddings.py  (similitud semántica — solo en Capa B, import diferido)
    - logger.py      (logging por niveles — import local para evitar ciclos)
    - perceptor.py   (es_archivo_audio — para resolver "pon archivo.mp3" vs "pon música")

Convenciones respetadas (Documento Maestro SARA v0.3.0):
    - Formato de retorno estándar {"exito": bool, "mensaje": str} + campos de contexto.
    - normalizar_texto() antes de TODA comparación de strings.
    - Try/except en todo import diferido y toda operación que pueda fallar.
    - SARA arranca siempre: si embeddings no está disponible, Capa B degrada a DESCONOCIDA.
    - Nunca retorna None — siempre retorna un dict con categoría y confianza.
    - Sin efectos secundarios: este módulo solo clasifica, nunca ejecuta.
"""

from __future__ import annotations

from typing import Optional

# ──────────────────────────────────────────────────────────────────────────
# CONSTANTES — VERBOS Y KEYWORDS POR CATEGORÍA
# ──────────────────────────────────────────────────────────────────────────
# Estas constantes están aquí y también se exportan a config.py en la sección
# de integración. Centralizar en config.py es preferible a largo plazo (como
# CARPETAS_SISTEMA, APPS_SISTEMA, etc.), pero se definen aquí para que el
# módulo sea autocontenido y funcione desde el primer día sin modificar config.
#
# REGLA DE PRIORIDAD: el primer verbo que matchee en la entrada normalizada
# determina la intención (Capa A). El orden de evaluación de los sets es:
# REPRODUCCION > APERTURA > BUSQUEDA > SHELL_INFO > SHELL_ACCION > CODIGO
# Esto porque "pon música" y "abre música" son el par más crítico de separar.

# ─── VERBOS DE REPRODUCCIÓN ────────────────────────────────────────────────
# Palabras que, al inicio de la entrada, indican inequívocamente que el usuario
# quiere reproducir multimedia — no abrir carpetas ni apps.
VERBOS_REPRODUCCION: frozenset[str] = frozenset({
    "pon", "pone", "ponme", "reproduce", "reproducir", "toca", "tocar",
    "play", "escuchar", "escúchame", "suena", "sonar", "dale play",
    "activa", "arranca la música", "música aleatoria", "shuffle",
})

# Palabras de objeto que, combinadas con un verbo ambiguo, confirman reproducción.
# Ej: "música" sola es ambigua, pero "pon música" + este set confirma REPRODUCIR.
OBJETOS_MUSICALES: frozenset[str] = frozenset({
    "musica", "música", "cancion", "canción", "canciones", "song", "playlist",
    "lista de reproduccion", "lista de reproducción", "album", "álbum",
    "shuffle", "aleatorio", "mp3", "wav", "flac", "audio",
})

# Plataformas de streaming reconocidas — "pon spotify" → REPRODUCIR (abrir Spotify)
PLATAFORMAS_STREAMING: frozenset[str] = frozenset({
    "spotify", "youtube music", "amazon music", "apple music", "soundcloud",
    "deezer", "tidal", "pandora", "youtube",
})

# ─── VERBOS DE APERTURA ────────────────────────────────────────────────────
# Estos ya existen en file_intent.VERBOS_APERTURA. Se duplican aquí de forma
# explícita para que Capa A pueda discriminar sin importar file_intent.
VERBOS_APERTURA: frozenset[str] = frozenset({
    "abre", "abrir", "muéstrame", "muestrame", "muestra", "mostrar",
    "ve a", "entra a", "entra en", "entra", "entrar",
    "ir a", "accede", "acceder", "navega a", "lanza", "lanzar", "ejecuta",
    "open", "show", "inicia", "iniciar", "carga", "cargar",
})

# ─── VERBOS DE BÚSQUEDA ────────────────────────────────────────────────────
VERBOS_BUSQUEDA: frozenset[str] = frozenset({
    "busca", "buscar", "encuentra", "encontrar", "dónde está", "donde esta",
    "buscarme", "encuéntrame", "search", "localiza", "localizar",
    "dónde tengo", "donde tengo", "halla", "hallar",
})

# ─── VERBOS DE INFORMACIÓN DE SISTEMA (shell informacional) ────────────────
# Preguntas que se responden mejor con comandos shell de solo lectura.
VERBOS_SHELL_INFO: frozenset[str] = frozenset({
    "cuanta", "cuánta", "cuanto", "cuánto", "cuantos", "cuántos",
    "qué procesos", "que procesos", "qué hay", "que hay",
    "muéstrame los procesos", "muestrame los procesos",
    "listar", "lista", "listar procesos", "ver procesos",
    "ip", "dirección ip", "direccion ip", "mi ip",
    "versión de", "version de", "qué versión", "que version",
    "espacio libre", "espacio en disco", "disco", "cuánto disco",
    "ram", "memoria", "cpu", "procesador", "sistema",
    "batería", "bateria", "carga del equipo",
})

# Palabras de contexto que, detectadas junto a verbos ambiguos, orientan hacia
# shell_info. Ej: "dime la RAM" (verbo "dime" + keyword "ram" → SHELL_INFO)
KEYWORDS_SHELL_INFO: frozenset[str] = frozenset({
    "ram", "memoria ram", "memoria disponible", "procesador", "cpu",
    "disco", "almacenamiento", "espacio", "batería", "bateria",
    "ip", "red", "conexion", "conexión", "procesos", "temperatura",
    "temperatura", "temperatura del sistema", "temperatura cpu",
    "que temperatura", "cuanta temperatura",
    "uptime", "tiempo encendido",
    # Red
    "internet", "conexion", "conexión", "ping", "dns", "firewall",
    "adaptador", "velocidad de red", "mac address", "gateway",
    "tabla de rutas", "arp", "puerto abierto", "puerto libre",
    # Variables de entorno
    "path", "java home", "variable path", "variables del sistema",
})

# ─── VERBOS DE ACCIÓN DE SISTEMA (shell con efecto) ────────────────────────
# Acciones que modifican el estado del sistema — requieren confirmación
# según las listas blanca/negra de shell.py.
VERBOS_SHELL_ACCION: frozenset[str] = frozenset({
    "cierra", "cerrar", "mata", "matar", "termina", "terminar",
    "instala", "instalar", "desinstala", "desinstalar",
    "reinicia", "reiniciar", "apaga", "apagar",
    "kill", "close", "install", "uninstall",
    "borra el proceso", "borra el programa",
    "actualiza", "actualizar",
    # Red con efecto
    "abre el puerto", "abrir el puerto", "abre puerto",
    "cierra el puerto", "cerrar el puerto", "cierra puerto",
    "agrega regla", "añade regla", "elimina regla",
    "bloquea", "bloquear",
})

# ─── VERBOS DE CÓDIGO ───────────────────────────────────────────────────────
# Coinciden con INTENCIONES_UNICAS de splitter.py — si ya llegamos a
# intent_router DESPUÉS de splitter, estos ya fueron procesados. Se incluyen
# aquí como salvaguarda para el caso de que algún patrón escape a splitter.
# ─── VERBOS DE PRODUCTIVIDAD ───────────────────────────────────────────────
VERBOS_TAREA_IR: frozenset[str] = frozenset({
    "añade una tarea", "añade tarea", "agrega una tarea", "agrega tarea",
    "nueva tarea", "crea una tarea",
    "mis tareas", "ver tareas", "lista de tareas", "que tareas tengo",
    "completar tarea", "marcar como completada", "tarea completada",
    "eliminar tarea", "borrar tarea", "tareas pendientes",
})

VERBOS_RECORDATORIO_IR: frozenset[str] = frozenset({
    "recuerdame", "recuérdame", "pon un recordatorio",
    "crea un recordatorio", "nuevo recordatorio",
    "mis recordatorios", "ver recordatorios", "que recordatorios tengo",
    "eliminar recordatorio", "borrar recordatorio",
    "avisame a las", "avísame a las", "avisame cuando", "avísame cuando",
    "recordatorio para",
})

VERBOS_NOTA_IR: frozenset[str] = frozenset({
    "anota", "toma nota", "toma una nota", "nueva nota",
    "crea una nota", "guarda esto", "guarda una nota",
    "mis notas", "ver notas", "busca en mis notas",
    "fijar nota", "borrar nota", "eliminar nota",
})
# ─── VERBOS DE AUTOMATIZACIÓN DE DESARROLLO ────────────────────────────────
# Comandos orientados al flujo de trabajo de un desarrollador: ejecutar scripts,
# levantar servidores, correr tests, gestionar entornos virtuales, ver git log.
# Se separan de CAT_SHELL_ACCION porque tienen confirmación diferenciada
# y contexto de proyecto (directorio actual, entorno virtual activo, etc.)
VERBOS_DEV: frozenset[str] = frozenset({
    "ejecuta el script", "ejecuta mi script", "corre el script",
    "levanta el servidor", "inicia el servidor", "arranca el servidor",
    "levanta", "sube el servidor",
    "corre los tests", "ejecuta los tests", "lanza los tests",
    "crea el entorno virtual", "crea un entorno virtual",
    "activa el entorno", "activa el venv",
    "ver los commits", "log de git", "historial de git",
    "que rama estoy", "qué rama estoy", "rama actual",
    "ver errores del log", "ultimos errores del log",
    "listar dependencias del proyecto",
    "compila el proyecto", "construye el proyecto",
})
# ─── VERBOS DE GESTIÓN DE ARCHIVOS ─────────────────────────────────────────
# Acciones sobre archivos/carpetas que NO son apertura: crear, mover, copiar,
# renombrar, listar, saber tamaño. Se separan de CAT_ABRIR para que
# shell.py (no commands.py) las procese con lógica de confirmación correcta.
VERBOS_GESTION_ARCHIVO: frozenset[str] = frozenset({
    "crea la carpeta", "crea el archivo", "crea una carpeta", "crea un archivo",
    "mueve", "mover el", "mover la",
    "copia", "copiar el", "copiar la",
    "renombra", "renombrar",
    "lista el contenido", "lista la carpeta", "lista los archivos",
    "cuanto pesa", "cuánto pesa", "tamaño de", "peso de",
    "elimina el archivo", "elimina la carpeta",
    "borra el archivo", "borra la carpeta",
})
VERBOS_CODIGO: frozenset[str] = frozenset({
    "crea", "crear", "programa", "programa un", "programa una",
    "script", "codigo", "código", "genera", "desarrolla",
    "construye", "escribe un", "haz un", "hazme un",
    "necesito un programa", "quiero un programa", "automatiza",
})

# ─── NOMBRES DE CATEGORÍAS (constantes para comparación en brain.py) ────────
CAT_REPRODUCIR    = "REPRODUCIR"
CAT_ABRIR         = "ABRIR"
CAT_BUSCAR        = "BUSCAR"
CAT_SHELL_INFO    = "SHELL_INFO"
CAT_SHELL_ACCION  = "SHELL_ACCION"
CAT_CODIGO        = "CODIGO"
CAT_ARCHIVO          = "ARCHIVO"
CAT_GESTIONAR_ARCHIVO = "GESTIONAR_ARCHIVO"
CAT_DEV               = "DESARROLLO"
CAT_TAREA             = "TAREA"
CAT_RECORDATORIO      = "RECORDATORIO"
CAT_NOTA              = "NOTA"
CAT_DESCONOCIDA   = "DESCONOCIDA"

# ─── PROTOTIPOS SEMÁNTICOS PARA CAPA B ─────────────────────────────────────
# Ejemplos representativos de cada intención ambigua para embeddings.
# Solo se comparan cuando Capa A no puede decidir con certeza.
_PROTOTIPOS_CAPA_B: dict[str, list[str]] = {
    CAT_REPRODUCIR: [
        "pon música de fondo",
        "reproduce algo de rock",
        "quiero escuchar música",
        "toca una canción aleatoria",
    ],
    CAT_ABRIR: [
        "abre la carpeta de música",
        "muéstrame donde guardo la música",
        "ve a la carpeta músicas",
        "abre el explorador de archivos",
    ],
    CAT_SHELL_INFO: [
        "cuánta memoria ram tengo disponible",
        "qué procesos están usando más cpu",
        "dime el espacio libre en el disco c",
        "cuál es mi dirección ip local",
        "tengo conexión a internet ahora mismo",
        "qué adaptadores de red tengo activos",
        "muéstrame las conexiones tcp abiertas",
        "cuál es mi dirección dns configurada",
        "muéstrame la tabla de rutas de red",
    ],
    CAT_SHELL_ACCION: [
        "cierra chrome que está colgado",
        "instala la librería pandas",
        "mata el proceso que consume ram",
        "desinstala la aplicación de zoom",
    ],
    CAT_TAREA: [
        "agrega una tarea para revisar el código mañana",
        "muéstrame mis tareas pendientes de hoy",
        "marca la tarea número 3 como completada",
        "qué tareas tengo sin terminar",
    ],
    CAT_RECORDATORIO: [
        "recuérdame tomar agua a las 3 de la tarde",
        "pon un recordatorio para la reunión del lunes",
        "avísame en 30 minutos que revise el servidor",
        "quiero un recordatorio diario a las 8 am",
    ],
    CAT_NOTA: [
        "anota que el cliente pidió cambiar el logo",
        "toma nota de esta idea para el proyecto",
        "guarda mis notas sobre la arquitectura de sara",
        "muéstrame todas mis notas fijadas",
    ],
    CAT_DEV: [
        "ejecuta el script de pruebas del proyecto",
        "levanta el servidor de desarrollo en el puerto 8000",
        "crea un entorno virtual para este proyecto python",
        "muéstrame los últimos commits del repositorio git",
        "corre los tests con pytest y muéstrame el resultado",
    ],
    CAT_GESTIONAR_ARCHIVO: [
        "crea una carpeta llamada proyectos en el escritorio",
        "mueve los pdf de descargas a documentos",
        "renombra el archivo informe como informe final",
        "cuánto pesa la carpeta de videos",
        "lista los archivos de mi escritorio",
        "copia el archivo config a la carpeta backup",
    ],
    CAT_SHELL_INFO: [
        # Estos se suman a los ya existentes en la Capa B —
        # la lista se extiende, no se reemplaza. Añadir al final del bloque existente.
        "tengo conexión a internet ahora mismo",
        "qué adaptadores de red tengo activos",
        "muéstrame las conexiones tcp abiertas",
        "cuál es mi dirección dns configurada",
        "muéstrame la tabla de rutas de red",
    ],
}


# ──────────────────────────────────────────────────────────────────────────
# UTILIDAD INTERNA DE LOGGING SEGURO
# ──────────────────────────────────────────────────────────────────────────

def _log(nivel: str, mensaje: str, detalle: str = "") -> None:
    """Wrapper de logging seguro — mismo patrón que perceptor.py."""
    try:
        import logger
        if nivel == "debug":
            logger.debug("intent_router", mensaje)
        elif nivel == "warning":
            logger.warning("intent_router", mensaje, detalle)
        elif nivel == "error":
            logger.error("intent_router", mensaje, detalle)
        else:
            logger.info("intent_router", mensaje)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────
# UTILIDADES INTERNAS DE COMPARACIÓN
# ──────────────────────────────────────────────────────────────────────────

def _normalizar(texto: str) -> str:
    """
    Aplica normalizar_texto() de utils si está disponible; si no, degrada a
    strip + lower para que el módulo funcione en pruebas aisladas.
    """
    try:
        from utils import normalizar_texto
        return normalizar_texto(texto)
    except Exception:
        return texto.strip().lower()


def _empieza_con(texto_norm: str, verbos: frozenset[str]) -> Optional[str]:
    """
    Retorna el primer verbo del set que sea prefijo exacto del texto normalizado,
    o None si ninguno coincide. Equivale a empieza_con_palabras() de utils
    pero operando sobre un set arbitrario sin depender de utils.

    La comparación requiere que el verbo sea seguido por un espacio o sea
    la totalidad del texto — evita falsos positivos como "sonata" matcheando
    "sonar" o "reproduce" matcheando "reproducción".
    """
    for verbo in verbos:
        if texto_norm == verbo or texto_norm.startswith(verbo + " "):
            return verbo
    return None


def _contiene_alguna(texto_norm: str, keywords: frozenset[str]) -> Optional[str]:
    """
    Retorna la primera keyword del set encontrada como substring completo
    (separado por espacios) en el texto normalizado, o None si ninguna.
    """
    palabras = set(texto_norm.split())
    for kw in keywords:
        # Keywords de más de una palabra se buscan como substring completo
        if " " in kw:
            if kw in texto_norm:
                return kw
        elif kw in palabras:
            return kw
    return None


# ──────────────────────────────────────────────────────────────────────────
# CAPA A — CLASIFICACIÓN DETERMINISTA POR VERBO LÍDER
# ──────────────────────────────────────────────────────────────────────────

def _capa_a_clasificar(texto_norm: str) -> Optional[dict]:
    """
    Primera pasada de clasificación: puramente por verbo inicial.
    Retorna el dict de resultado si hay match claro, o None si la entrada
    es ambigua y debe pasar a Capa B.

    El verbo líder es el token más discriminante en español para comandos
    de acción: "abre X" vs "pon X" vs "busca X" difieren solo en ese verbo.

    Returns:
        dict con keys: categoria, confianza, verbo_detectado, metodo
        None si no hay verbo líder claro.
    """

    # ─── 1. REPRODUCCIÓN ────────────────────────────────────────────────
    verbo_rep = _empieza_con(texto_norm, VERBOS_REPRODUCCION)
    if verbo_rep:
        return {
            "categoria": CAT_REPRODUCIR,
            "confianza": 0.95,
            "verbo_detectado": verbo_rep,
            "metodo": "capa_a_verbo_reproduccion",
        }

    # ─── 2. REPRODUCCIÓN POR PLATAFORMA STREAMING ───────────────────────
    # "abre Spotify" con verbo de apertura → en realidad quiere reproducir.
    # Se captura aquí para que brain.py use shell.py (abrir URI spotify:) en
    # lugar de file_intent (que buscaría "spotify" en CARPETAS_SISTEMA o índice).
    verbo_aper = _empieza_con(texto_norm, VERBOS_APERTURA)
    if verbo_aper:
        plataforma = _contiene_alguna(texto_norm, PLATAFORMAS_STREAMING)
        if plataforma:
            return {
                "categoria": CAT_REPRODUCIR,
                "confianza": 0.90,
                "verbo_detectado": verbo_aper,
                "plataforma": plataforma,
                "metodo": "capa_a_apertura_streaming",
            }

    # ─── 3. APERTURA GENÉRICA ────────────────────────────────────────────
    if verbo_aper:
        return {
            "categoria": CAT_ABRIR,
            "confianza": 0.90,
            "verbo_detectado": verbo_aper,
            "metodo": "capa_a_verbo_apertura",
        }

    # ─── 4. BÚSQUEDA ────────────────────────────────────────────────────
    verbo_bus = _empieza_con(texto_norm, VERBOS_BUSQUEDA)
    if verbo_bus:
        return {
            "categoria": CAT_BUSCAR,
            "confianza": 0.90,
            "verbo_detectado": verbo_bus,
            "metodo": "capa_a_verbo_busqueda",
        }

    # ─── 5. INFORMACIÓN DE SISTEMA ──────────────────────────────────────
    verbo_info = _empieza_con(texto_norm, VERBOS_SHELL_INFO)
    if verbo_info:
        return {
            "categoria": CAT_SHELL_INFO,
            "confianza": 0.90,
            "verbo_detectado": verbo_info,
            "metodo": "capa_a_verbo_shell_info",
        }

    # Detección por keyword de sistema incluso sin verbo líder claro
    # (ej. "¿cuánta RAM?" sin verbo explícito)
    kw_info = _contiene_alguna(texto_norm, KEYWORDS_SHELL_INFO)
    if kw_info:
        return {
            "categoria": CAT_SHELL_INFO,
            "confianza": 0.75,
            "verbo_detectado": None,
            "keyword_detectada": kw_info,
            "metodo": "capa_a_keyword_shell_info",
        }

    # ─── 6. ACCIÓN DE SISTEMA ───────────────────────────────────────────
    verbo_acc = _empieza_con(texto_norm, VERBOS_SHELL_ACCION)
    if verbo_acc:
        return {
            "categoria": CAT_SHELL_ACCION,
            "confianza": 0.90,
            "verbo_detectado": verbo_acc,
            "metodo": "capa_a_verbo_shell_accion",
        }

    # ─── 7. CÓDIGO ──────────────────────────────────────────────────────
    # ─── 5b. PRODUCTIVIDAD ──────────────────────────────────────────
    # Antes de DEV y gestión de archivos para capturar "crea una tarea"
    # antes de que "crea" caiga en CAT_GESTIONAR_ARCHIVO o CAT_CODIGO.
    verbo_tarea = _empieza_con(texto_norm, VERBOS_TAREA_IR)
    if verbo_tarea:
        return {
            "categoria": CAT_TAREA,
            "confianza": 0.93,
            "verbo_detectado": verbo_tarea,
            "metodo": "capa_a_verbo_tarea",
        }

    verbo_rec = _empieza_con(texto_norm, VERBOS_RECORDATORIO_IR)
    if verbo_rec:
        return {
            "categoria": CAT_RECORDATORIO,
            "confianza": 0.95,
            "verbo_detectado": verbo_rec,
            "metodo": "capa_a_verbo_recordatorio",
        }

    verbo_nota = _empieza_con(texto_norm, VERBOS_NOTA_IR)
    if verbo_nota:
        return {
            "categoria": CAT_NOTA,
            "confianza": 0.93,
            "verbo_detectado": verbo_nota,
            "metodo": "capa_a_verbo_nota",
        }
    # ─── 6a. AUTOMATIZACIÓN DE DESARROLLO ───────────────────────────
    # Antes de gestión de archivos para capturar "ejecuta el script X"
    # antes de que "ejecuta" se clasifique como SHELL_ACCION genérico.
    verbo_dev = _empieza_con(texto_norm, VERBOS_DEV)
    if verbo_dev:
        return {
            "categoria": CAT_DEV,
            "confianza": 0.92,
            "verbo_detectado": verbo_dev,
            "metodo": "capa_a_verbo_dev",
        }
    # ─── 6b. GESTIÓN DE ARCHIVOS ────────────────────────────────────
    # Debe ir ANTES de CÓDIGO para capturar "crea una carpeta" antes de
    # que "crea" se clasifique como intención de código.
    verbo_gest = _empieza_con(texto_norm, VERBOS_GESTION_ARCHIVO)
    if verbo_gest:
        # Distinguir si es eliminación (zona amarilla) o lectura (zona blanca)
        es_destructivo = any(v in texto_norm for v in ("elimina", "borra", "borrar", "eliminar"))
        return {
            "categoria": CAT_GESTIONAR_ARCHIVO,
            "confianza": 0.92,
            "verbo_detectado": verbo_gest,
            "es_destructivo": es_destructivo,
            "metodo": "capa_a_verbo_gestion_archivo",
        }
    verbo_cod = _empieza_con(texto_norm, VERBOS_CODIGO)
    if verbo_cod:
        # Antes de clasificar como CODIGO, verificar si hay keywords de sistema
        # que indiquen que el usuario quiere información, no generar código.
        # Ej: "haz un diagnóstico" → SHELL_INFO, no CODIGO
        _KEYWORDS_SISTEMA_EN_CODIGO: frozenset[str] = frozenset({
            "diagnostico", "diagnostico del sistema", "diagnostico sistema",
            "reporte", "reporte del sistema", "estado del sistema",
            "informe del sistema", "analisis del sistema",
            "temperatura", "gpu", "ram", "cpu", "disco", "bateria",
            "resolucion", "pantalla", "procesos", "usb", "red", "ip",
        })
        if _contiene_alguna(texto_norm, _KEYWORDS_SISTEMA_EN_CODIGO):
            return {
                "categoria": CAT_SHELL_INFO,
                "confianza": 0.88,
                "verbo_detectado": verbo_cod,
                "keyword_detectada": "sistema_en_codigo",
                "metodo": "capa_a_codigo_redirigido_shell_info",
            }
        return {
            "categoria": CAT_CODIGO,
            "confianza": 0.95,
            "verbo_detectado": verbo_cod,
            "metodo": "capa_a_verbo_codigo",
        }

    # Ningún verbo líder claro → Capa B o DESCONOCIDA
    return None


# ──────────────────────────────────────────────────────────────────────────
# CAPA B — DESAMBIGUACIÓN SEMÁNTICA (solo en empates reales)
# ──────────────────────────────────────────────────────────────────────────

def _capa_b_semantica(texto_norm: str) -> Optional[dict]:
    """
    Segunda pasada: compara el texto contra prototipos semánticos de cada
    categoría ambigua usando embeddings.py. Solo se activa cuando Capa A
    devolvió None (no hubo verbo líder claro).

    Si embeddings no está disponible o hay error, degrada limpiamente a None
    (la función clasificar() devolverá DESCONOCIDA).

    Returns:
        dict con keys: categoria, confianza, metodo
        None si no se puede clasificar o los scores son insuficientes.
    """
    try:
        import embeddings as _emb
        from config import MARGEN_EMPATE
    except Exception as e:
        _log("warning", "Capa B no disponible (embeddings/config no cargado)", str(e))
        return None

    mejor_categoria: Optional[str] = None
    mejor_score: float = 0.0
    segundo_score: float = 0.0

    try:
        for categoria, prototipos in _PROTOTIPOS_CAPA_B.items():
            # Promedio de similitud coseno contra todos los prototipos de la categoría
            scores = []
            for prototipo in prototipos:
                try:
                    similitud = _emb.similitud_semantica(texto_norm, prototipo)   # ✅ genera vectores internamente
                    scores.append(similitud if isinstance(similitud, float) else 0.0)
                except Exception:
                    continue

            if not scores:
                continue

            score_categoria = sum(scores) / len(scores)

            if score_categoria > mejor_score:
                segundo_score = mejor_score
                mejor_score = score_categoria
                mejor_categoria = categoria
            elif score_categoria > segundo_score:
                segundo_score = score_categoria

        # Umbral mínimo de confianza para aceptar el resultado semántico
        UMBRAL_MINIMO_SEMANTICO = 0.40

        if mejor_categoria is None or mejor_score < UMBRAL_MINIMO_SEMANTICO:
            return None

        # Si los dos mejores están muy cerca, no decidir (empate real → árbitro Qwen)
        if (mejor_score - segundo_score) < MARGEN_EMPATE:
            _log("debug", f"Capa B: empate semántico ({mejor_score:.2f} vs {segundo_score:.2f}) — delegando a Qwen")
            return None

        return {
            "categoria": mejor_categoria,
            "confianza": round(mejor_score, 3),
            "metodo": "capa_b_semantica",
        }

    except Exception as e:
        _log("error", "Error inesperado en Capa B semántica", str(e))
        return None


# ──────────────────────────────────────────────────────────────────────────
# FUNCIÓN PRINCIPAL — clasificar()
# ──────────────────────────────────────────────────────────────────────────

def clasificar(texto: str) -> dict:
    """
    Punto de entrada principal de intent_router.py.

    Clasifica la entrada del usuario en una categoría de intención de acción,
    ejecutando las dos capas de clasificación en cascada. Es la función que
    brain.py debe llamar como primer paso de procesar().

    La función NUNCA retorna None ni lanza excepciones: siempre devuelve
    un dict con al menos {"categoria": str, "confianza": float, "exito": bool}.

    Args:
        texto: Entrada del usuario, tal como llega a brain.procesar().
               Puede estar sin normalizar — este módulo la normaliza internamente.

    Returns:
        dict con las siguientes keys garantizadas:
            - "exito"         (bool)   : True si se pudo clasificar, False si hay error
            - "categoria"     (str)    : Una de las constantes CAT_* definidas arriba
            - "confianza"     (float)  : Score 0.0–1.0 de certeza de la clasificación
            - "metodo"        (str)    : Qué capa tomó la decisión (para logging/debug)
            - "texto_norm"    (str)    : Texto normalizado usado en la clasificación

        Campos opcionales según la categoría:
            - "verbo_detectado"  (str|None) : Verbo que disparó la clasificación
            - "keyword_detectada" (str|None): Keyword de sistema detectada
            - "plataforma"       (str|None) : Plataforma de streaming detectada

    Ejemplo de uso en brain.py:
        resultado_router = intent_router.clasificar(texto_usuario)
        if resultado_router["categoria"] == intent_router.CAT_REPRODUCIR:
            return shell.reproducir_spotify()
        # ... continúa el flujo normal para otras categorías
    """
    if not texto or not isinstance(texto, str) or not texto.strip():
        return {
            "exito": False,
            "categoria": CAT_DESCONOCIDA,
            "confianza": 0.0,
            "metodo": "entrada_invalida",
            "texto_norm": "",
        }

    try:
        texto_norm = _normalizar(texto)

        # ─── CAPA A: clasificación por verbo líder ────────────────────
        resultado_a = _capa_a_clasificar(texto_norm)
        if resultado_a is not None:
            _log("debug", f"Capa A clasificó '{texto_norm[:40]}' como {resultado_a['categoria']} ({resultado_a['confianza']:.2f})")
            return {
                "exito": True,
                "texto_norm": texto_norm,
                **resultado_a,
            }

        # ─── CAPA B: desambiguación semántica ─────────────────────────
        resultado_b = _capa_b_semantica(texto_norm)
        if resultado_b is not None:
            _log("debug", f"Capa B clasificó '{texto_norm[:40]}' como {resultado_b['categoria']} ({resultado_b['confianza']:.2f})")
            return {
                "exito": True,
                "texto_norm": texto_norm,
                "verbo_detectado": None,
                **resultado_b,
            }

        # ─── DESCONOCIDA: el flujo normal de brain.py se encarga ──────
        _log("debug", f"Router no clasificó '{texto_norm[:40]}' — flujo normal")
        return {
            "exito": True,
            "categoria": CAT_DESCONOCIDA,
            "confianza": 0.0,
            "metodo": "no_clasificado",
            "texto_norm": texto_norm,
            "verbo_detectado": None,
        }

    except Exception as e:
        _log("error", "Error inesperado en clasificar()", str(e))
        return {
            "exito": False,
            "categoria": CAT_DESCONOCIDA,
            "confianza": 0.0,
            "metodo": "error_interno",
            "texto_norm": texto if isinstance(texto, str) else "",
        }


# ──────────────────────────────────────────────────────────────────────────
# UTILIDADES AUXILIARES — para uso de brain.py y sara.py
# ──────────────────────────────────────────────────────────────────────────

def es_reproduccion(resultado_clasificacion: dict) -> bool:
    """
    Helper de conveniencia para brain.py.
    Retorna True si el resultado de clasificar() indica intención de reproducir.

    Args:
        resultado_clasificacion: Dict retornado por clasificar().

    Returns:
        bool
    """
    return (
        resultado_clasificacion.get("exito", False)
        and resultado_clasificacion.get("categoria") == CAT_REPRODUCIR
    )


def es_shell(resultado_clasificacion: dict) -> bool:
    """
    Retorna True si la intención es de tipo shell (informacional o de acción).
    Útil para enrutar directamente a shell.py desde brain.py.
    """
    return resultado_clasificacion.get("categoria") in (CAT_SHELL_INFO, CAT_SHELL_ACCION)


def requiere_confirmacion(resultado_clasificacion: dict) -> bool:
    """
    Retorna True si la intención clasificada corresponde a una acción de
    sistema que debe requerir confirmación del usuario (según la arquitectura
    de riesgo de shell.py: zona amarilla).

    SHELL_INFO  → solo lectura, nunca requiere confirmación.
    SHELL_ACCION → depende de la lista blanca/negra de shell.py. Esta función
                   marca la acción como "potencialmente con efecto" para que
                   shell.py aplique su lógica de categorización de riesgo.
    REPRODUCIR  → no requiere confirmación (acción de bajo riesgo).
    """
    return resultado_clasificacion.get("categoria") == CAT_SHELL_ACCION
def es_tarea(resultado_clasificacion: dict) -> bool:
    """Retorna True si la intención es gestión de tareas."""
    return resultado_clasificacion.get("categoria") == CAT_TAREA


def es_recordatorio(resultado_clasificacion: dict) -> bool:
    """Retorna True si la intención es crear/ver/eliminar recordatorio."""
    return resultado_clasificacion.get("categoria") == CAT_RECORDATORIO


def es_nota(resultado_clasificacion: dict) -> bool:
    """Retorna True si la intención es gestión de notas."""
    return resultado_clasificacion.get("categoria") == CAT_NOTA
def es_dev(resultado_clasificacion: dict) -> bool:
    """
    Retorna True si la intención es automatización de desarrollo.
    Usado por brain.py para enrutar a shell.gestionar_dev().
    """
    return resultado_clasificacion.get("categoria") == CAT_DEV
def es_gestion_archivo(resultado_clasificacion: dict) -> bool:
    """
    Retorna True si la intención es gestión de archivos/carpetas
    (crear, mover, copiar, renombrar, listar, medir tamaño).
    Usado por brain.py para enrutar a shell.gestionar_archivo().
    """
    return resultado_clasificacion.get("categoria") == CAT_GESTIONAR_ARCHIVO
def obtener_plataforma_streaming(resultado_clasificacion: dict) -> Optional[str]:
    """
    Si la clasificación detectó una plataforma de streaming (Spotify, YouTube, etc.),
    retorna su nombre. Retorna None en caso contrario.

    Usado por brain.py para decidir si abrir Spotify via URI (spotify:) o
    reproducir desde el índice local de archivos de audio.
    """
    return resultado_clasificacion.get("plataforma")


# ──────────────────────────────────────────────────────────────────────────
# FUNCIÓN DE DIAGNÓSTICO — útil durante desarrollo y para sentinel.py
# ──────────────────────────────────────────────────────────────────────────

def diagnosticar(texto: str) -> dict:
    """
    Versión extendida de clasificar() que incluye el razonamiento completo
    de la clasificación. Útil para logs de debug, pruebas y para que SARA
    pueda explicar al desarrollador por qué clasificó algo de cierta manera.

    Args:
        texto: Entrada del usuario.

    Returns:
        dict con todo lo de clasificar() más:
            - "capa_a_resultado"  (dict|None)  : Resultado bruto de Capa A
            - "capa_b_resultado"  (dict|None)  : Resultado bruto de Capa B
            - "razonamiento"      (str)         : Explicación legible de la decisión
    """
    texto_norm = _normalizar(texto) if texto and isinstance(texto, str) else ""

    resultado_a = _capa_a_clasificar(texto_norm) if texto_norm else None
    resultado_b = None

    if resultado_a is None and texto_norm:
        resultado_b = _capa_b_semantica(texto_norm)

    resultado_final = clasificar(texto)

    if resultado_a is not None:
        razonamiento = (
            f"Capa A detectó el verbo '{resultado_a.get('verbo_detectado')}' "
            f"→ clasificado como {resultado_a['categoria']} (confianza {resultado_a['confianza']:.2f})."
        )
    elif resultado_b is not None:
        razonamiento = (
            f"Capa A no encontró verbo líder. "
            f"Capa B semántica clasificó como {resultado_b['categoria']} "
            f"(score {resultado_b['confianza']:.2f} contra prototipos)."
        )
    else:
        razonamiento = (
            "Ni Capa A ni Capa B pudieron clasificar la entrada con certeza. "
            "El flujo normal de brain.py toma el control."
        )

    return {
        **resultado_final,
        "capa_a_resultado": resultado_a,
        "capa_b_resultado": resultado_b,
        "razonamiento": razonamiento,
    }
