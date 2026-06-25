# 📁 brain.py
from config import (
    UMBRAL_PREGUNTA, UMBRAL_COMANDO, UMBRAL_INTENCION,
    PESO_DIFFLIB, PESO_BD, PESO_SEMANTICO,
    USAR_GEMINI_BACKUP, UMBRAL_MINIMO_GEMINI
)
from utils import normalizar_texto, similitud, empieza_con_palabras, contiene_palabra_clave
from database import (
    obtener_conocimientos, obtener_comandos,
    guardar_intencion, guardar_historial,
    incrementar_consulta, incrementar_uso_comando,
    obtener_vectores_conocimientos, obtener_vectores_comandos
)
import re
import searcher
import embeddings
import logger
from typing import Optional

# ── PRAXIS: intent_router ─────────────────────────────────────
try:
    import intent_router as _intent_router
    INTENT_ROUTER_DISPONIBLE = True
except ImportError:
    _intent_router = None
    INTENT_ROUTER_DISPONIBLE = False

PALABRAS_PREGUNTA = (
    "que", "como", "cuando", "donde", "por que", "para que",
    "quien", "cuanto", "cuantos", "cuales", "cual", "dime",
    "explicame", "sabes", "conoces", "puedes decirme"
)

PALABRAS_COMANDO = (
    "abre", "abrir", "ejecuta", "ejecutar", "pon", "poner",
    "inicia", "iniciar", "cierra", "cerrar", "busca", "buscar",
    "muestra", "mostrar", "reproduce", "reproducir",
    "descarga", "descargar", "instala", "instalar",
    "apaga", "apagar", "reinicia", "reiniciar",
    "crea", "crear", "elimina", "eliminar",
    "genera", "generar", "desarrolla", "desarrollar",
    "construye", "construir", "escribe", "escribir",
    "hazme", "hazme un", "haz un", "haz una",
    "quiero que", "necesito que", "quiero", "necesito",
    "sube", "subir", "baja", "bajar", "silencia", "silenciar",
    "pausa", "pausar", "siguiente", "anterior",
    "brillo", "volumen", "bateria", "cuanta ram",
    "uso cpu", "info sistema"
)

EQUIVALENCIAS_INTENCION = {
    "que es": {
        "dime", "explicame", "cuentame", "describe",
        "definicion", "como es", "platicame", "habla"
    },
    "como funciona": {"como trabaja", "como opera", "para que sirve"},
    "cual es":       {"dime el", "dime la", "cuanto mide", "cuanto pesa"},
    "donde esta":    {"ubicacion", "donde se encuentra"},
}

PESO_DIFFLIB   = PESO_DIFFLIB
PESO_BD        = PESO_BD
PESO_SEMANTICO = PESO_SEMANTICO
MARGEN_EMPATE: float = 0.15
UMBRAL_ARCHIVO_CONFIRMACION_BRAIN = 0.75

# ──────────────────────────────────────────────────────────────────────────
# MAPA DE COMANDOS DE SISTEMA
# ──────────────────────────────────────────────────────────────────────────
# Valores permitidos:
#   - str "shell_info_X" o "shell_diagnostico": despacha a función hardcoded
#   - tuple (str_tag, callable): ejecuta el callable directamente
#
# REGLA: MAPA_COMANDOS_SISTEMA_ORDENADO pre-ordena por longitud descendente
# para que frases específicas ganen sobre sub-cadenas más cortas.
# No hay entradas duplicadas — cada clave aparece UNA sola vez.
MAPA_COMANDOS_SISTEMA = {
    # ── RAM ──────────────────────────────────────────────────────────────
    "cuanta ram tengo":          "shell_info_ram",
    "cuanta ram":                "shell_info_ram",
    "uso de ram":                "shell_info_ram",
    "memoria ram":               "shell_info_ram",
    "ram disponible":            "shell_info_ram",
    "cuanta memoria tengo":      "shell_info_ram",
    "memoria disponible":        "shell_info_ram",
    # ── CPU ──────────────────────────────────────────────────────────────
    "uso del procesador":        "shell_info_cpu",
    "cuanto cpu uso":            "shell_info_cpu",
    "uso del cpu":               "shell_info_cpu",
    "porcentaje de cpu":         "shell_info_cpu",
    "cuanto cpu":                "shell_info_cpu",
    "uso cpu":                   "shell_info_cpu",
    "cpu estoy usando":          "shell_info_cpu",
    "porcentaje cpu":            "shell_info_cpu",
    # ── Disco ─────────────────────────────────────────────────────────────
    "espacio en disco":          "shell_info_disco",
    "cuanto espacio":            "shell_info_disco",
    "espacio libre":             "shell_info_disco",
    "espacio disponible":        "shell_info_disco",
    # ── IP / red ──────────────────────────────────────────────────────────
    "direccion ip":              "shell_info_ip",
    "mi ip":                     "shell_info_ip",
    "ip local":                  "shell_info_ip",
    # ── Procesos ──────────────────────────────────────────────────────────
    "procesos activos":          "shell_info_procesos",
    "que procesos":              "shell_info_procesos",
    "que esta corriendo":        "shell_info_procesos",
    "procesos corriendo":        "shell_info_procesos",
    # ── Batería ───────────────────────────────────────────────────────────
    "nivel de bateria":          "shell_info_bateria",
    "estado bateria":            "shell_info_bateria",
    "nivel bateria":             "shell_info_bateria",
    "bateria":                   "shell_info_bateria",
    "bateria me queda":          "shell_info_bateria",
    "cuanta bateria":            "shell_info_bateria",
    # ── Diagnóstico general ───────────────────────────────────────────────
    "diagnostico del sistema":   "shell_diagnostico",
    "diagnostico sistema":       "shell_diagnostico",
    "estado del sistema":        "shell_diagnostico",
    "informacion sistema":       "shell_diagnostico",
    "como estas sara":           "shell_diagnostico",
    "reporte del sistema":       "shell_diagnostico",
    "haz un diagnostico":        "shell_diagnostico",
    "como estas sara":        "shell_diagnostico",
    "haz diagnostico":           "shell_diagnostico",
    "como esta el sistema":      "shell_diagnostico",
    "como estoy":                "shell_diagnostico",
    "dame un reporte":           "shell_diagnostico",
    "informe del sistema":       "shell_diagnostico",
    "como esta el sistema":      "shell_diagnostico",
    "como esta todo":            "shell_diagnostico",
    "como va el sistema":        "shell_diagnostico",
    "como andas sara":           "shell_diagnostico",
    "como anda el sistema":      "shell_diagnostico",

    # ── GPU ───────────────────────────────────────────────────────────────
    "que tarjeta grafica tengo": ("shell_info", lambda: __import__('shell').info_gpu()),
    "que tarjeta grafica":       ("shell_info", lambda: __import__('shell').info_gpu()),
    "tarjeta grafica":           ("shell_info", lambda: __import__('shell').info_gpu()),
    "que gpu tengo":             ("shell_info", lambda: __import__('shell').info_gpu()),
    "cuanta vram":               ("shell_info", lambda: __import__('shell').info_gpu()),
    "informacion gpu":           ("shell_info", lambda: __import__('shell').info_gpu()),
    "gpu tengo":                 ("shell_info", lambda: __import__('shell').info_gpu()),
    # ── Temperatura ───────────────────────────────────────────────────────
    "temperatura del sistema":   ("shell_info", lambda: __import__('shell').info_temperatura()),
    "temperatura del cpu":       ("shell_info", lambda: __import__('shell').info_temperatura()),
    "temperatura cpu":           ("shell_info", lambda: __import__('shell').info_temperatura()),
    "que temperatura tiene":     ("shell_info", lambda: __import__('shell').info_temperatura()),
    "a que temperatura":         ("shell_info", lambda: __import__('shell').info_temperatura()),
    "esta caliente":             ("shell_info", lambda: __import__('shell').info_temperatura()),
    # ── Pantalla / resolución ─────────────────────────────────────────────
    "que resolucion tengo":      ("shell_info", lambda: __import__('shell').info_pantalla()),
    "cuantos monitores tengo":   ("shell_info", lambda: __import__('shell').info_pantalla()),
    "cuantos monitores":         ("shell_info", lambda: __import__('shell').info_pantalla()),
    "resolucion de pantalla":    ("shell_info", lambda: __import__('shell').info_pantalla()),
    "pantalla tengo":            ("shell_info", lambda: __import__('shell').info_pantalla()),
    # ── USB ───────────────────────────────────────────────────────────────
    "que usb tengo conectados":  ("shell_info", lambda: __import__('shell').info_usb()),
    "dispositivos usb":          ("shell_info", lambda: __import__('shell').info_usb()),
    "que usb tengo":             ("shell_info", lambda: __import__('shell').info_usb()),
    "usb conectados":            ("shell_info", lambda: __import__('shell').info_usb()),
    "usb tengo":                 ("shell_info", lambda: __import__('shell').info_usb()),
    # ── Servicios ─────────────────────────────────────────────────────────
    "servicios activos":         ("shell_info", lambda: __import__('shell').info_servicios()),
    "que servicios":             ("shell_info", lambda: __import__('shell').info_servicios()),
    "servicios corriendo":       ("shell_info", lambda: __import__('shell').info_servicios()),
    # ── Variables de entorno ──────────────────────────────────────────────
    "variables de entorno":      ("shell_info", lambda: __import__('shell').info_variables_entorno()),
    "variable de entorno":       ("shell_info", lambda: __import__('shell').info_variables_entorno()),
    # ── Red extendida ─────────────────────────────────────────────────────
    "adaptadores de red":        ("shell_info", lambda: __import__('shell').info_red_extendida()),
    "velocidad de red":          ("shell_info", lambda: __import__('shell').info_red_extendida()),
    "mac address":               ("shell_info", lambda: __import__('shell').info_red_extendida()),
    # ── DNS ───────────────────────────────────────────────────────────────
    "servidor dns":              ("shell_info", lambda: __import__('shell').info_dns()),
    "mi dns":                    ("shell_info", lambda: __import__('shell').info_dns()),
    # ── Conexiones ────────────────────────────────────────────────────────
    "conexiones activas":        ("shell_info", lambda: __import__('shell').info_conexiones_activas()),
    "conexiones tcp":            ("shell_info", lambda: __import__('shell').info_conexiones_activas()),
    # ── ARP ───────────────────────────────────────────────────────────────
    "dispositivos en red":       ("shell_info", lambda: __import__('shell').info_arp()),
    "tabla arp":                 ("shell_info", lambda: __import__('shell').info_arp()),
    # ── Versiones de herramientas ──────────────────────────────────────
    "version de python":         ("shell_info", lambda: __import__('shell').version_herramienta("python")),
    "que version de python":     ("shell_info", lambda: __import__('shell').version_herramienta("python")),
    "version de git":            ("shell_info", lambda: __import__('shell').version_herramienta("git")),
    "que version de git":        ("shell_info", lambda: __import__('shell').version_herramienta("git")),
    "version de node":           ("shell_info", lambda: __import__('shell').version_herramienta("node")),
    "version de npm":            ("shell_info", lambda: __import__('shell').version_herramienta("npm")),
    "version de docker":         ("shell_info", lambda: __import__('shell').version_herramienta("docker")),
    "esta instalado docker":     ("shell_info", lambda: __import__('shell').version_herramienta("docker")),
    "esta instalado git":        ("shell_info", lambda: __import__('shell').version_herramienta("git")),
    "version de java":           ("shell_info", lambda: __import__('shell').version_herramienta("java")),
    "version de pip":            ("shell_info", lambda: __import__('shell').version_herramienta("pip")),
    "version de ollama":         ("shell_info", lambda: __import__('shell').version_herramienta("ollama")),
}

# Pre-ordenar por longitud descendente para que frases específicas
# ganen sobre sub-cadenas más cortas (ej. "cuanta ram tengo" > "cuanta ram")
MAPA_COMANDOS_SISTEMA_ORDENADO = sorted(
    MAPA_COMANDOS_SISTEMA.items(), key=lambda item: len(item[0]), reverse=True
)


# ──────────────────────────────────────────────────────────────────────────
# MAPA SEMÁNTICO — permite encontrar comandos sin strings exactos
# ──────────────────────────────────────────────────────────────────────────
# Se construye UNA VEZ al cargar el módulo vectorizando las frases del MAPA.
# Permite que "hazme un análisis del sistema" encuentre "diagnostico sistema"
# por similitud semántica aunque ninguna palabra coincida exactamente.

_MAPA_VECTORIZADO: list[tuple[str, object, list]] = []  # (frase, nombre_cmd, vector)
_MAPA_VECTORIZADO_LISTO = False


def _construir_mapa_vectorizado() -> None:
    """
    Vectoriza las frases del MAPA_COMANDOS_SISTEMA una sola vez al arrancar.
    Solo se ejecuta si embeddings está disponible.
    Silencioso ante errores — nunca bloquea el arranque de SARA.
    """
    global _MAPA_VECTORIZADO, _MAPA_VECTORIZADO_LISTO
    if _MAPA_VECTORIZADO_LISTO:
        return
    try:
        if not embeddings.esta_disponible():
            return
        for frase, nombre_cmd in MAPA_COMANDOS_SISTEMA.items():
            vec = embeddings.generar_vector(frase)
            if vec:
                _MAPA_VECTORIZADO.append((frase, nombre_cmd, vec))
        _MAPA_VECTORIZADO_LISTO = True
        logger.debug("brain",
                     f"Mapa semántico construido: {len(_MAPA_VECTORIZADO)} frases vectorizadas")
    except Exception as e:
        logger.warning("brain", "No se pudo vectorizar el MAPA", str(e))


def _buscar_en_mapa_semantico(texto_limpio: str,
                               umbral: float = 0.72) -> tuple:
    """
    Busca en MAPA_COMANDOS_SISTEMA por similitud semántica.
    Retorna (nombre_cmd, score) o (None, 0.0) si no hay match suficiente.

    Umbral 0.72 deliberadamente alto para evitar falsos positivos.
    Ej: "hazme un diagnóstico completo" → "diagnostico sistema" ~0.85 ✅
        "abre chrome"                   → ningún match en mapa   ✅
    """
    if not _MAPA_VECTORIZADO_LISTO or not _MAPA_VECTORIZADO:
        return None, 0.0
    try:
        vec_consulta = embeddings.generar_vector(texto_limpio)
        if vec_consulta is None:
            return None, 0.0

        mejor_score = 0.0
        mejor_cmd   = None

        for frase, nombre_cmd, vec_frase in _MAPA_VECTORIZADO:
            score = embeddings.similitud_coseno(vec_consulta, vec_frase)
            if score > mejor_score:
                mejor_score = score
                mejor_cmd   = nombre_cmd

        if mejor_score >= umbral:
            logger.debug("brain",
                         f"Match semántico MAPA: '{texto_limpio[:40]}' → score {mejor_score:.2f}")
            return mejor_cmd, mejor_score
        return None, 0.0
    except Exception:
        return None, 0.0


# ──────────────────────────────────────────────────────────────────────────
# DESPACHO SHELL INFO POR KEYWORDS
# ──────────────────────────────────────────────────────────────────────────

def _despachar_shell_info_por_keywords(texto_limpio: str, _shell):
    """
    Mapea texto_limpio a una función específica de shell.py usando keywords.
    Se usa como fallback cuando CAT_SHELL_INFO no tiene match en el MAPA ni
    en el mapa semántico — evita que preguntas de sistema lleguen al CMD.
    Retorna dict resultado o None si no hay match.
    """
    t = texto_limpio

    if any(k in t for k in ("tarjeta grafica", "gpu", "vram", "grafica")):
        return _shell.info_gpu()
    if any(k in t for k in ("resolucion", "monitores", "pantalla", "monitor")):
        return _shell.info_pantalla()
    if any(k in t for k in ("temperatura", "caliente", "calor")):
        return _shell.info_temperatura()
    if any(k in t for k in ("usb", "dispositivos usb", "usb conectados")):
        return _shell.info_usb()
    if any(k in t for k in ("servicios", "servicio activo", "servicios corriendo")):
        return _shell.info_servicios()
    if any(k in t for k in ("variables de entorno", "variable de entorno",
                              "java home", "cual es mi path", "variable path",
                              "que vale", "valor de la variable")):
        # Si menciona una variable específica, buscarla
        VARS_CONOCIDAS = {
            "path": "PATH", "java home": "JAVA_HOME", "java_home": "JAVA_HOME",
            "temp": "TEMP", "tmp": "TMP", "userprofile": "USERPROFILE",
            "appdata": "APPDATA", "programfiles": "ProgramFiles",
            "computername": "COMPUTERNAME", "username": "USERNAME",
        }
        for alias, nombre_var in VARS_CONOCIDAS.items():
            if alias in t:
                return _shell.info_variables_entorno(nombre_var)
        return _shell.info_variables_entorno()
    if any(k in t for k in ("adaptadores", "velocidad red", "mac address", "adaptador de red")):
        return _shell.info_red_extendida()
    if any(k in t for k in ("dns", "servidor dns", "mi dns")):
        return _shell.info_dns()
    if any(k in t for k in ("conexiones activas", "conexiones tcp", "que conexiones")):
        return _shell.info_conexiones_activas()
    if any(k in t for k in ("tabla de rutas", "rutas de red", "gateway", "enrutamiento")):
        return _shell.info_tabla_rutas()
    if any(k in t for k in ("arp", "dispositivos en red", "dispositivos red")):
        return _shell.info_arp()
    if any(k in t for k in ("porcentaje de cpu", "porcentaje cpu",
                              "cuanto cpu uso", "cpu estoy usando")):
        return _shell.info_cpu()
    if any(k in t for k in ("estadisticas de red", "cuanto descargue",
                              "cuanto he descargado", "uso de red")):
        return _shell.info_estadisticas_red()
    if any(k in t for k in ("diagnostico", "reporte", "estado general",
                              "como esta", "analisis del sistema")):
        return _shell.diagnostico_sistema()
    # Versiones de herramientas
    HERRAMIENTAS = {
        "python": "python", "git": "git", "node": "node",
        "npm": "npm", "docker": "docker", "java": "java",
        "pip": "pip", "ollama": "ollama", "cargo": "cargo",
        "rust": "rust", "go": "go",
    }
    if any(k in t for k in ("version", "versión", "instalado", "instalada")):
        for nombre, cmd in HERRAMIENTAS.items():
            if nombre in t:
                return _shell.version_herramienta(cmd)
    return None


# ──────────────────────────────────────────────────────────────────────────
# DESPACHO CAT_SHELL_ACCION: traduce lenguaje natural → función real de shell.py
# ──────────────────────────────────────────────────────────────────────────

import re as _re_brain

_PALABRAS_NUMERO = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "quince": 15, "veinte": 20, "treinta": 30, "cuarenta": 40,
    "cuarenta y cinco": 45, "sesenta": 60,
}


def _extraer_minutos(texto: str) -> int:
    """Extrae minutos de frases como 'en 10 minutos', 'en cinco minutos'."""
    m = _re_brain.search(r"(\d+)\s*min", texto)
    if m:
        return int(m.group(1))
    for palabra, valor in sorted(_PALABRAS_NUMERO.items(), key=lambda x: -len(x[0])):
        if _re_brain.search(rf"\b{palabra}\b\s*min", texto):
            return valor
    return 0


def _extraer_nombre_proceso(texto: str) -> str:
    """
    Extrae el nombre del proceso/app de frases como
    'cierra chrome', 'mata el proceso notepad', 'termina firefox'.
    """
    t = texto
    for verbo in ("cierra", "cerrar", "mata", "matar", "termina", "terminar", "finaliza"):
        t = t.replace(verbo, " ")
    for relleno in ("el proceso", "la aplicacion", "la app", "de una vez", "por favor"):
        t = t.replace(relleno, " ")
    return t.strip()


def _extraer_paquete_pip(texto: str) -> str:
    """Extrae el nombre del paquete de 'instala requests', 'instala pandas con pip'."""
    t = texto.replace("con pip", "").replace("usando pip", "").strip()
    for verbo in ("instala", "instalar", "desinstala", "desinstalar"):
        if t.startswith(verbo):
            t = t[len(verbo):].strip()
            break
    return t.strip()


def _extraer_app_winget(texto: str) -> str:
    """Extrae el nombre de la app de 'instala VLC con winget'."""
    t = texto.replace("con winget", "").replace("usando winget", "").strip()
    for verbo in ("instala", "instalar", "desinstala", "desinstalar"):
        if t.startswith(verbo):
            t = t[len(verbo):].strip()
            break
    return t.strip()


def _despachar_shell_accion_por_keywords(texto_limpio: str, _shell):
    """
    Traduce frases en lenguaje natural a la función real de shell.py para
    CAT_SHELL_ACCION. Sin esto, brain.py pasaría el texto crudo del usuario
    (ej. 'apaga la computadora') a ejecutar_controlado(), que lo intentaría
    correr como comando literal de Windows y fallaría (returncode=1).
    Retorna dict resultado o None si no hay match (cae al flujo de texto crudo).
    """
    t = texto_limpio

    # ── Apagar equipo ───────────────────────────────────────────────────
    if any(k in t for k in ("apaga la computadora", "apaga el equipo",
                              "apagar la computadora", "apagar el equipo",
                              "apaga la pc", "apagar la pc", "apaga mi pc")):
        minutos = _extraer_minutos(t)
        return _shell.apagar_equipo(minutos=minutos)

    # ── Reiniciar equipo ──────────────────────────────────────────────────
    if any(k in t for k in ("reinicia la computadora", "reinicia el equipo",
                              "reiniciar la computadora", "reiniciar el equipo",
                              "reinicia la pc", "reiniciar la pc", "reinicia mi pc")):
        minutos = _extraer_minutos(t)
        return _shell.reiniciar_equipo(minutos=minutos)

    # ── Cerrar / matar proceso ───────────────────────────────────────────
    if any(k in t for k in ("cierra ", "cerrar ", "mata ", "matar ",
                              "termina ", "terminar ", "finaliza ")) and \
       not any(k in t for k in ("puerto", "sesion", "sesión")):
        nombre_proceso = _extraer_nombre_proceso(t)
        if nombre_proceso:
            return _shell.matar_proceso(nombre_proceso)

    # ── Limpiar temporales ────────────────────────────────────────────────
    if any(k in t for k in ("limpia los archivos temporales", "limpia temporales",
                              "borra los archivos temp", "borra temporales",
                              "limpiar temp")):
        return _shell.limpiar_temporales(confirmar=True)

    # ── Instalar / desinstalar con winget (prioridad: mención explícita) ──
    if "winget" in t and any(k in t for k in ("instala", "instalar")):
        app = _extraer_app_winget(t)
        if app:
            return _shell.instalar_winget(app)

    # ── Instalar / desinstalar con pip ────────────────────────────────────
    # Si dice "con pip" lo detecta explícito; si solo dice "instala <paquete>"
    # sin mencionar winget, se asume pip por defecto (paquete Python suelto).
    if any(k in t for k in ("instala", "instalar")) and "winget" not in t:
        paquete = _extraer_paquete_pip(t)
        if paquete:
            return _shell.instalar_pip(paquete)

    # ── Firewall — abrir/cerrar puerto ────────────────────────────────────
    m_puerto = _re_brain.search(r"puerto\s+(\d{1,5})", t)
    if m_puerto and any(k in t for k in ("abre", "abrir", "cierra", "cerrar",
                                            "bloquea", "bloquear", "regla de firewall")):
        puerto = int(m_puerto.group(1))
        accion = "cerrar" if any(k in t for k in ("cierra", "cerrar", "bloquea", "bloquear")) else "abrir"
        return _shell.gestionar_firewall(accion, puerto)

    return None


# ──────────────────────────────────────────────────────────────────────────
# DESPACHO CAPA -1: ejecutar nombre_cmd del MAPA
# ──────────────────────────────────────────────────────────────────────────

def _ejecutar_nombre_cmd(nombre_cmd, texto_original: str,
                          texto_limpio: str, frase: str = ""):
    """
    Ejecuta el comando asociado a una entrada del MAPA.
    Soporta dos formatos de valor:
        - str "shell_info_X" / "shell_diagnostico": función hardcoded
        - tuple (tag, callable): ejecuta el callable directamente

    Retorna el dict _resultado() ya formateado, o None si falla para que
    el caller pueda degradar al flujo normal.
    """
    import shell as _shell

    # ── Formato tupla (tag, callable) ─────────────────────────────────────
    if isinstance(nombre_cmd, tuple) and len(nombre_cmd) == 2 and callable(nombre_cmd[1]):
        try:
            r = nombre_cmd[1]()
            guardar_intencion(texto_original, texto_limpio, "shell_info", 0.95)
            guardar_historial(texto_original, texto_limpio,
                              frase or str(nombre_cmd[0]), "shell_info", 0.95)
            return _resultado("respuesta",
                              r.get("mensaje", "No pude obtener la información."),
                              confianza=0.95, query=texto_limpio)
        except Exception as _e:
            logger.error("brain", f"Fallo lambda MAPA '{frase}'", str(_e))
            return None

    # ── Formato string ─────────────────────────────────────────────────────
    if not isinstance(nombre_cmd, str):
        return None

    DISPATCH = {
        "shell_info_ram":      _shell.info_ram,
        "shell_info_cpu":      _shell.info_cpu,
        "shell_info_disco":    _shell.info_disco,
        "shell_info_ip":       _shell.info_ip,
        "shell_info_procesos": _shell.info_procesos,
        "shell_info_bateria":  _shell.info_bateria,
        "shell_diagnostico":   _shell.diagnostico_sistema,
    }

    if nombre_cmd in DISPATCH:
        try:
            r = DISPATCH[nombre_cmd]()
            guardar_intencion(texto_original, texto_limpio, "shell_info", 0.95)
            guardar_historial(texto_original, texto_limpio, nombre_cmd, "shell_info", 0.95)
            return _resultado("respuesta",
                              r.get("mensaje", "No pude obtener la información."),
                              confianza=0.95, query=texto_limpio)
        except Exception as _e:
            logger.error("brain", f"shell.py no disponible para {nombre_cmd}", str(_e))
            return None

    # Nombre de comando de BD — buscar y ejecutar
    from database import obtener_comandos as _get_cmds
    for cmd in _get_cmds():
        if normalizar_texto(cmd["nombre"]) == nombre_cmd:
            guardar_intencion(texto_original, texto_limpio, "comando", 0.95)
            guardar_historial(texto_original, texto_limpio,
                              cmd["nombre"], "comando", 0.95)
            incrementar_uso_comando(cmd["id"])
            return _resultado("comando", f"Ejecutando: {cmd['nombre']}",
                              comando=dict(cmd), confianza=0.95, query=texto_limpio)
    return None


# ──────────────────────────────────────────────────────────────────────────
# RESOLUCIÓN DE MARCADORES ESPECIALES
# ──────────────────────────────────────────────────────────────────────────

def _resolver_intencion_con_destino(texto_original):
    if not texto_original.startswith("__DESTINO__"):
        return None

    sin_prefijo = texto_original[len("__DESTINO__"):]
    if "|" not in sin_prefijo:
        return None

    destino, contenido = sin_prefijo.split("|", 1)
    destino   = destino.strip()
    contenido = contenido.strip()

    logger.debug("brain", f"Resolviendo destino='{destino}' contenido='{contenido}'")

    url_contenido = _resolver_contenido_para_destino(contenido, destino)

    DESTINOS_WEB = {
        "youtube":  "https://www.youtube.com",
        "google":   "https://www.google.com",
        "twitter":  "https://www.twitter.com",
        "facebook": "https://www.facebook.com",
        "twitch":   "https://www.twitch.tv",
        "spotify":  "https://open.spotify.com",
    }
    destino_norm = normalizar_texto(destino)
    if destino_norm in DESTINOS_WEB:
        return _resultado(
            "comando_con_destino",
            f"Abriendo '{contenido}' en {destino}...",
            comando={
                "nombre":        destino,
                "accion":        DESTINOS_WEB[destino_norm],
                "tipo":          "web",
                "url_contenido": url_contenido,
                "contenido_raw": contenido,
            },
            confianza=1.0,
            query=texto_original
        )

    cmd_destino, score_destino = buscar_comando(destino)

    if not cmd_destino or score_destino < UMBRAL_COMANDO:
        from file_intent import detectar_intencion_archivo
        lista = detectar_intencion_archivo(destino, destino)
        if lista and lista[0]["confianza"] >= 0.75:
            arch = lista[0]
            cmd_destino = {
                "nombre": arch["nombre"],
                "accion": arch["ruta"],
                "tipo":   arch["tipo"],
            }
        else:
            logger.warning("brain", f"App destino '{destino}' no encontrada")
            return None

    return _resultado(
        "comando_con_destino",
        f"Abriendo '{contenido}' en '{destino}'",
        comando={
            "nombre":        cmd_destino.get("nombre", destino),
            "accion":        cmd_destino.get("accion", ""),
            "tipo":          cmd_destino.get("tipo", "app"),
            "url_contenido": url_contenido,
            "contenido_raw": contenido,
        },
        confianza=score_destino if score_destino else 0.90,
        query=texto_original
    )


def _resolver_intencion_con_carpeta_ctx(texto_original):
    """
    Intercepta __CARPETA_CTX__nombre|texto_real antes de que
    normalizar_texto() lo destruya.
    """
    PREFIJO = "__CARPETA_CTX__"
    if not texto_original.startswith(PREFIJO):
        return None

    try:
        sin_prefijo = texto_original[len(PREFIJO):]
        carpeta_ctx, texto_real = sin_prefijo.split("|", 1)
        carpeta_ctx = carpeta_ctx.strip()
        texto_real  = texto_real.strip()
    except ValueError:
        return None

    logger.debug("brain",
                 f"Contexto de carpeta recibido: '{carpeta_ctx}'",
                 f"texto real: '{texto_real}'")

    texto_limpio_ctx = normalizar_texto(texto_real)

    from file_intent import detectar_intencion_archivo
    lista = detectar_intencion_archivo(texto_original, texto_real)

    if not lista:
        logger.debug("brain",
                     f"__CARPETA_CTX__: file_intent sin resultados para '{texto_real}'")
        return None

    mejor = lista[0]
    guardar_intencion(texto_real, texto_limpio_ctx, "archivo", mejor["confianza"])
    guardar_historial(texto_real, texto_limpio_ctx,
                      mejor["ruta"], "archivo", mejor["confianza"])

    if mejor.get("requiere_confirmacion"):
        return _resultado(
            "archivo_confirmar",
            mejor["nombre"],
            comando={"accion": mejor["ruta"], "tipo": "app", "nombre": mejor["nombre"]},
            confianza=mejor["confianza"],
            query=texto_limpio_ctx
        )

    from database import incrementar_acceso_archivo
    incrementar_acceso_archivo(mejor["ruta"])
    return _resultado(
        "archivo",
        mejor["ruta"],
        comando={"accion": mejor["ruta"], "tipo": "app", "nombre": mejor["nombre"]},
        confianza=mejor["confianza"],
        query=texto_limpio_ctx
    )


def _resolver_contenido_para_destino(contenido, destino):
    from urllib.parse import quote
    contenido_norm = normalizar_texto(contenido)

    URLS_DIRECTAS = {
        "google":    "https://www.google.com",
        "youtube":   "https://www.youtube.com",
        "facebook":  "https://www.facebook.com",
        "twitter":   "https://www.twitter.com",
        "instagram": "https://www.instagram.com",
        "whatsapp":  "https://web.whatsapp.com",
        "gmail":     "https://mail.google.com",
        "github":    "https://www.github.com",
        "netflix":   "https://www.netflix.com",
        "spotify":   "https://open.spotify.com",
        "chatgpt":   "https://chat.openai.com",
        "claude":    "https://claude.ai",
        "gemini":    "https://gemini.google.com",
        "twitch":    "https://www.twitch.tv",
        "reddit":    "https://www.reddit.com",
        "x":         "https://www.x.com",
    }

    separadores = [" y ", " and ", ", "]
    partes = [contenido_norm]
    for sep in separadores:
        if sep in contenido_norm:
            partes = [p.strip() for p in contenido_norm.split(sep) if p.strip()]
            break

    if len(partes) > 1:
        urls = []
        todas_conocidas = True
        for parte in partes:
            if parte in URLS_DIRECTAS:
                urls.append(URLS_DIRECTAS[parte])
            else:
                todas_conocidas = False
                break
        if todas_conocidas:
            return urls

    if contenido_norm in URLS_DIRECTAS:
        return URLS_DIRECTAS[contenido_norm]

    if "youtube" in normalizar_texto(destino):
        return f"https://www.youtube.com/results?search_query={quote(contenido)}"

    NAVEGADORES = {"chrome", "opera", "firefox", "edge", "brave", "opera gx"}
    if any(nav in normalizar_texto(destino) for nav in NAVEGADORES):
        if "." in contenido and " " not in contenido:
            return f"https://{contenido}" if not contenido.startswith("http") else contenido
        return f"https://www.google.com/search?q={quote(contenido)}"

    from file_intent import CARPETAS_SISTEMA
    if contenido_norm in CARPETAS_SISTEMA:
        return CARPETAS_SISTEMA[contenido_norm]

    return f"https://www.google.com/search?q={quote(contenido)}"


# ──────────────────────────────────────────────────────────────────────────
# DETECCIÓN DE INTENCIÓN
# ──────────────────────────────────────────────────────────────────────────

def detectar_intencion(texto_original, texto_limpio):
    PALABRAS_CODIGO = (
        "crea un programa", "crear un programa", "crea un script",
        "escribe un programa", "hazme un programa", "genera un script",
        "desarrolla un programa", "construye un programa",
        "programa en python", "script en python", "codigo en python",
        "programa que", "script que"
    )
    PALABRAS_SISTEMA = (
        "cuanta ram", "uso cpu", "uso del cpu", "info sistema",
        "informacion sistema", "cuanto cpu", "memoria ram",
        "uso de ram", "uso de cpu", "bateria", "nivel bateria"
    )
    for frase in PALABRAS_SISTEMA:
        if frase in texto_limpio:
            return "comando", 0.90
    for frase in PALABRAS_CODIGO:
        if frase in texto_limpio:
            return "comando", 0.90

    if "?" in texto_original or "¿" in texto_original:
        return "pregunta", 0.95
    if empieza_con_palabras(texto_limpio, PALABRAS_PREGUNTA):
        return "pregunta", 0.85
    if empieza_con_palabras(texto_limpio, PALABRAS_COMANDO):
        return "comando", 0.85
    for palabra in PALABRAS_PREGUNTA:
        if contiene_palabra_clave(texto_limpio, palabra):
            return "pregunta", 0.60
    for palabra in PALABRAS_COMANDO:
        if contiene_palabra_clave(texto_limpio, palabra):
            return "comando", 0.60
    tipo_bd, confianza_bd = _detectar_por_bd(texto_limpio)
    if tipo_bd:
        return tipo_bd, confianza_bd
    return "desconocido", 0.0


def _detectar_por_bd(texto_limpio):
    from database import obtener_comandos, obtener_conocimientos
    UMBRAL_BD   = 0.65
    comandos    = obtener_comandos()
    mejor_score = 0.0

    for cmd in comandos:
        nombre = normalizar_texto(cmd["nombre"])
        score  = similitud(texto_limpio, nombre)
        palabras_clave = cmd["palabras_clave"] or ""
        for palabra in palabras_clave.split(","):
            palabra = normalizar_texto(palabra.strip())
            if palabra:
                s = similitud(texto_limpio, palabra)
                if s > score:
                    score = s
                if contiene_palabra_clave(texto_limpio, palabra):
                    score = max(score, 0.80)
        if score > mejor_score:
            mejor_score = score

    if mejor_score >= UMBRAL_BD:
        return "comando", mejor_score

    conocimientos = obtener_conocimientos()
    mejor_score   = 0.0
    for fila in conocimientos:
        score = similitud(texto_limpio, normalizar_texto(fila["pregunta"]))
        if score > mejor_score:
            mejor_score = score

    if mejor_score >= UMBRAL_BD:
        return "pregunta", mejor_score

    return None, 0.0


# ──────────────────────────────────────────────────────────────────────────
# BÚSQUEDA DE COMANDOS
# ──────────────────────────────────────────────────────────────────────────

def buscar_comando(texto_limpio):
    if not texto_limpio or len(texto_limpio) < 2:
        return None, 0.0
    if not any(c.isalpha() for c in texto_limpio):
        return None, 0.0
    VOCALES = set('aeiouáéíóú')
    if len(texto_limpio) > 5:
        ratio_vocales = sum(1 for c in texto_limpio if c in VOCALES) / len(texto_limpio)
        if ratio_vocales < 0.15:
            logger.debug("brain", f"buscar_comando: ruido, skip → '{texto_limpio[:30]}'")
            return None, 0.0

    comandos = obtener_comandos()
    if not comandos:
        return None, 0.0

    mejor_score   = 0.0
    mejor_comando = None

    from collections import Counter
    _conteo  = Counter(texto_limpio.replace(' ', ''))
    _total   = sum(_conteo.values())
    _top2    = sum(v for _, v in _conteo.most_common(2))
    _usar_embeddings = (
        embeddings.esta_disponible() and
        (_total == 0 or _top2 / _total <= 0.65)
    )

    vectores        = obtener_vectores_comandos() if _usar_embeddings else []
    vector_consulta = embeddings.generar_vector(texto_limpio) if _usar_embeddings else None
    tema_consulta   = _extraer_tema_especifico(texto_limpio)

    comando_dinamico, score_dinamico = _buscar_comando_dinamico(texto_limpio, comandos)
    if comando_dinamico and score_dinamico > mejor_score:
        mejor_score   = score_dinamico
        mejor_comando = comando_dinamico
        if mejor_score >= UMBRAL_COMANDO:
            incrementar_uso_comando(mejor_comando["id"])
            return dict(mejor_comando), mejor_score

    for i, cmd in enumerate(comandos):
        nombre         = normalizar_texto(cmd["nombre"])
        palabras_clave = cmd["palabras_clave"] or ""

        score_difflib = similitud(texto_limpio, nombre)
        score_bd      = score_difflib

        for palabra_pen in palabras_clave.split(","):
            palabra_pen = normalizar_texto(palabra_pen.strip())
            if palabra_pen:
                s = similitud(texto_limpio, palabra_pen)
                if s > score_bd:
                    score_bd = s
                if contiene_palabra_clave(texto_limpio, palabra_pen):
                    score_bd = max(score_bd, 0.80)

        if mejor_score >= 0.95:
            break

        score_semantico = 0.0
        if vector_consulta and vectores and max(score_difflib, score_bd) >= 0.18:
            for cmd_dict, vec in vectores:
                if normalizar_texto(cmd_dict["nombre"]) == nombre:
                    score_semantico = embeddings.similitud_coseno(vector_consulta, vec)
                    break

        if i == 19 and mejor_score < 0.08:
            score_max_penalizado = mejor_score * 0.25
            if score_max_penalizado < UMBRAL_COMANDO:
                logger.debug("brain",
                             f"Early exit: mejor_score={mejor_score:.2f} tras 20 cmds")
            break

        tokens_texto   = set(texto_limpio.split())
        tokens_nombre  = set(nombre.split())
        overlap_tokens = bool(tokens_texto & tokens_nombre)

        penalizacion = 1.0
        tema_comando = _extraer_tema_especifico(nombre)

        if tema_consulta and tema_comando and tema_consulta != tema_comando:
            if similitud(tema_consulta, tema_comando) < 0.60:
                mejor_similitud_clave = 0.0
                for palabra_pen in palabras_clave.split(","):
                    palabra_pen = normalizar_texto(palabra_pen.strip())
                    if palabra_pen:
                        if contiene_palabra_clave(palabra_pen, tema_consulta):
                            mejor_similitud_clave = 1.0
                            break
                        tema_palabra = _extraer_tema_especifico(palabra_pen)
                        if tema_palabra and tema_consulta:
                            s = similitud(tema_consulta, tema_palabra)
                            if s > mejor_similitud_clave:
                                mejor_similitud_clave = s
                if mejor_similitud_clave < 0.60:
                    penalizacion = 0.20

        evidencia_explicita = overlap_tokens or texto_limpio.startswith(nombre)
        if not evidencia_explicita:
            for palabra_pen in palabras_clave.split(","):
                palabra_pen = normalizar_texto(palabra_pen.strip())
                if palabra_pen and contiene_palabra_clave(texto_limpio, palabra_pen):
                    evidencia_explicita = True
                    break

        if not evidencia_explicita and score_semantico < 0.45 and score_difflib < 0.45:
            penalizacion *= 0.25
            logger.debug(
                "brain",
                f"Comando penalizado por evidencia débil: '{nombre}' "
                f"score_difflib={score_difflib:.2f} score_bd={score_bd:.2f} "
                f"score_semantico={score_semantico:.2f} overlap={overlap_tokens}"
            )

        if embeddings.esta_disponible() and score_semantico > 0:
            score_final = (score_difflib   * PESO_DIFFLIB +
                           score_bd        * PESO_BD +
                           score_semantico * penalizacion * PESO_SEMANTICO)
        else:
            score_final = (score_difflib * 0.50 + score_bd * 0.50) * penalizacion

        if score_final > mejor_score:
            mejor_score   = score_final
            mejor_comando = cmd

    if mejor_score >= UMBRAL_COMANDO:
        incrementar_uso_comando(mejor_comando["id"])
        return dict(mejor_comando), mejor_score

    return None, mejor_score


def _buscar_comando_dinamico(texto_limpio, comandos):
    mejor_score   = 0.0
    mejor_comando = None

    for cmd in comandos:
        nombre_bd = normalizar_texto(cmd["nombre"])
        palabras_nombre = nombre_bd.split()
        palabras_texto  = texto_limpio.split()

        if len(palabras_nombre) > len(palabras_texto):
            continue

        coincide = True
        for i, palabra_nombre in enumerate(palabras_nombre):
            if i >= len(palabras_texto):
                coincide = False
                break
            if palabra_nombre != palabras_texto[i]:
                coincide = False
                break

        if coincide and len(palabras_texto) > len(palabras_nombre):
            parametros = " ".join(palabras_texto[len(palabras_nombre):])
            parametros = _limpiar_parametros_dinamicos(parametros)
            if not parametros:
                continue

            cmd_modificado  = dict(cmd)
            accion_original = cmd_modificado.get("accion", "")
            cmd_modificado["accion"] = f"{accion_original}({parametros})"

            score = 0.95
            if score > mejor_score:
                mejor_score   = score
                mejor_comando = cmd_modificado

    return mejor_comando, mejor_score


def _limpiar_parametros_dinamicos(parametros):
    parametros = parametros.strip().lower()
    if parametros.startswith(("a ", "al ", "a la ", "a los ", "a las ")):
        parametros = re.sub(r'^(a|al|a la|a los|a las)\s+', '', parametros)
    if parametros.endswith("%"):
        parametros = parametros[:-1].strip()
    match = re.search(r'\d+', parametros)
    if match:
        return match.group(0)
    return parametros


def _extraer_tema_especifico(texto):
    PALABRAS_ESTRUCTURA = {
        "cual", "es", "el", "la", "los", "las", "un", "una",
        "que", "como", "cuando", "donde", "quien", "cuanto",
        "planeta", "animal", "pais", "ciudad", "color", "nombre",
        "de", "del", "en", "con", "por", "para", "sobre",
        "dime", "explicame", "cuentame", "sabes", "conoces"
    }
    palabras   = normalizar_texto(texto).split()
    candidatos = [p for p in palabras
                  if p not in PALABRAS_ESTRUCTURA and len(p) > 2]
    return candidatos[-1] if candidatos else None


def _extraer_nucleo_interrogativo(texto):
    TEMAS_COMUNES = {"luna", "sol", "tierra", "marte", "venus", "planeta",
                     "estrella", "galaxia", "universo", "youtube", "google", "chrome"}
    PREPOSICIONES = {"de", "del", "en", "con", "por", "para", "sobre"}
    ARTICULOS     = {"el", "la", "los", "las", "un", "una"}
    palabras      = texto.split()
    resultado     = [p for p in palabras
                     if p not in TEMAS_COMUNES
                     and p not in PREPOSICIONES
                     and p not in ARTICULOS]
    return " ".join(resultado) if resultado else texto


def _extraer_parametro_comando(texto_limpio, comando):
    if not comando:
        return None

    nombre_bd = normalizar_texto(comando.get("nombre", ""))
    accion    = comando.get("accion", "")
    palabras_nombre = nombre_bd.split()
    palabras_texto  = texto_limpio.split()

    if len(palabras_texto) > len(palabras_nombre):
        parametros        = " ".join(palabras_texto[len(palabras_nombre):])
        comando_modificado = dict(comando)

        if "(" in accion and ")" in accion:
            accion_con_parametro = accion.replace("()", f"({parametros})")
            comando_modificado["accion"] = accion_con_parametro
            return comando_modificado
        elif any(palabra_num in parametros for palabra_num in ["50", "100", "75", "25", "0"] +
                 [str(i) for i in range(0, 101)]):
            accion_con_parametro = f"{accion}({parametros})"
            comando_modificado["accion"] = accion_con_parametro
            return comando_modificado

    return comando


# ──────────────────────────────────────────────────────────────────────────
# BÚSQUEDA DE RESPUESTAS (conocimientos)
# ──────────────────────────────────────────────────────────────────────────

def buscar_respuesta(texto_limpio):
    """
    Busca la mejor respuesta en la BD de conocimientos usando difflib + embeddings.
    Retorna (respuesta, confianza, pregunta_original) o (None, 0.0, None).
    """
    try:
        conocimientos = obtener_conocimientos()
        if not conocimientos:
            return None, 0.0, None

        mejor_score = 0.0
        mejor_fila  = None
        texto_norm  = normalizar_texto(texto_limpio)

        # Scoring difflib
        for fila in conocimientos:
            score = similitud(texto_norm, normalizar_texto(fila["pregunta"]))
            if score > mejor_score:
                mejor_score = score
                mejor_fila  = fila

        # Boost semántico si embeddings disponible
        # obtener_vectores_conocimientos() retorna (pregunta, vector) — 2 elementos
        if embeddings.esta_disponible():
            vectores = obtener_vectores_conocimientos()
            if vectores:
                resultado_sem = embeddings.buscar_mas_similar(texto_norm, vectores)
                mejor_id  = resultado_sem[0] if resultado_sem else None
                score_sem = resultado_sem[1] if resultado_sem and len(resultado_sem) > 1 else 0.0
                score_compuesto = mejor_score * PESO_DIFFLIB + score_sem * PESO_SEMANTICO
                if score_compuesto > mejor_score and mejor_id:
                    for fila in conocimientos:
                        if normalizar_texto(fila["pregunta"]) == normalizar_texto(mejor_id):
                            mejor_fila  = fila
                            mejor_score = score_compuesto
                            break

        UMBRAL_RESPUESTA = 0.60
        if mejor_fila and mejor_score >= UMBRAL_RESPUESTA:
            incrementar_consulta(mejor_fila["pregunta"])
            return mejor_fila["respuesta"], mejor_score, mejor_fila["pregunta"]

        return None, 0.0, None

    except Exception as e:
        logger.log_excepcion("brain", "buscar_respuesta", e)
        return None, 0.0, None


# ──────────────────────────────────────────────────────────────────────────
# PROCESAMIENTO PRINCIPAL
# ──────────────────────────────────────────────────────────────────────────

def procesar(texto_original):

    # ── Marcadores especiales — verificar ANTES de normalizar ─────────────
    resultado_destino = _resolver_intencion_con_destino(texto_original)
    if resultado_destino:
        return resultado_destino

    resultado_ctx = _resolver_intencion_con_carpeta_ctx(texto_original)
    if resultado_ctx:
        return resultado_ctx

    texto_limpio = normalizar_texto(texto_original)
    if not texto_limpio:
        return _resultado("desconocido", "No entendí nada, ¿puedes repetirlo?")

    # ── Caché — consultar ANTES de procesar ──────────────────────────────
    from database import obtener_cache_intencion
    resultado_cached = obtener_cache_intencion(texto_limpio)
    if resultado_cached:
        logger.debug("brain", f"✓ Caché encontrada para '{texto_limpio}'")
        return resultado_cached

    candidatos = []

    # ══════════════════════════════════════════════════════════════════════
    # CAPA -2 — intent_router: discriminador semántico de intención
    # Intercepta ANTES del MAPA para resolver clasificaciones de alto nivel:
    # reproducción, productividad, shell info/acción, gestión de archivos.
    # ══════════════════════════════════════════════════════════════════════
    if INTENT_ROUTER_DISPONIBLE and _intent_router:
        try:
            clasificacion = _intent_router.clasificar(texto_limpio)
            categoria     = clasificacion.get("categoria", "")

            # ── REPRODUCCIÓN ─────────────────────────────────────────────
            if categoria == _intent_router.CAT_REPRODUCIR:
                try:
                    import shell as _shell
                    plataforma    = clasificacion.get("plataforma")
                    resultado_rep = _shell.reproducir(plataforma=plataforma)
                    guardar_intencion(texto_original, texto_limpio, "reproduccion", 0.95)
                    guardar_historial(texto_original, texto_limpio,
                                      "reproducir", "reproduccion", 0.95)
                    return _resultado("respuesta",
                                      resultado_rep.get("mensaje", "Reproduciendo..."),
                                      confianza=0.95, query=texto_limpio)
                except Exception as _e:
                    logger.warning("brain", "shell.reproducir() falló", str(_e))

            # ── SHELL INFO ────────────────────────────────────────────────
            elif categoria == _intent_router.CAT_SHELL_INFO:
                # 1. Búsqueda exacta + vectorial en BD aprendida (instantáneo)
                try:
                    import shell_learner as _sl
                    r_aprendido = _sl.resolver_intencion_shell(texto_limpio, texto_original)
                    if r_aprendido and r_aprendido.get("exito"):
                        guardar_intencion(texto_original, texto_limpio, "shell_info", 0.92)
                        guardar_historial(texto_original, texto_limpio,
                                          "shell_info_aprendida", "shell_info", 0.92)
                        return _resultado("respuesta", r_aprendido.get("mensaje", ""),
                                          confianza=0.92, query=texto_limpio)
                except Exception as _e:
                    logger.warning("brain", "shell_learner falló — continuando", str(_e))

                # 2. MAPA semántico (frases vectorizadas al arrancar)
                nombre_cmd_sem, score_sem = _buscar_en_mapa_semantico(texto_limpio)
                if nombre_cmd_sem:
                    r_sem = _ejecutar_nombre_cmd(nombre_cmd_sem, texto_original,
                                                  texto_limpio)
                    if r_sem:
                        # Enseñar al learner este match para futuro
                        try:
                            import shell_learner as _sl
                            _sl._guardar_aprendizaje(
                                texto_limpio,
                                str(nombre_cmd_sem).split(":")[0] if isinstance(nombre_cmd_sem, str) else "diagnostico_sistema",
                                "",
                                score_sem,
                                fuente="mapa_semantico"
                            )
                        except Exception:
                            pass
                        return r_sem

                # 3. Keywords específicas
                try:
                    import shell as _shell
                    r_kw = _despachar_shell_info_por_keywords(texto_limpio, _shell)
                    if r_kw and r_kw.get("exito"):
                        guardar_intencion(texto_original, texto_limpio,
                                          "shell_info", 0.85)
                        guardar_historial(texto_original, texto_limpio,
                                          "shell_info_generica", "shell_info", 0.85)
                        return _resultado("respuesta", r_kw.get("mensaje", ""),
                                          confianza=0.85, query=texto_limpio)
                except Exception:
                    pass
                # 4. Si nada matcheó, Capa -1 (MAPA substring) lo intenta abajo

            

            # ── SHELL ACCIÓN ──────────────────────────────────────────────
            elif categoria == _intent_router.CAT_SHELL_ACCION:
                try:
                    import shell as _shell

                    # resolver_fn: el dispatcher de keywords ya existente.
                    # Se usa tal cual como "motor de ejecución" — intent_learner
                    # solo añade memoria vectorial encima, sin reemplazar la
                    # lógica determinista que ya funciona.
                    def _resolver_shell_accion(texto: str) -> Optional[dict]:
                        res = _despachar_shell_accion_por_keywords(texto, _shell)
                        if res is None:
                            # Último recurso: texto crudo (solo sirve si el
                            # usuario ya escribió un comando real de shell)
                            res = _shell.ejecutar_controlado(
                                texto, contexto=f"acción del sistema: {texto[:60]}"
                            )
                        return res

                    try:
                        import intent_learner as _ilearn
                        r = _ilearn.resolver_intencion(
                            categoria="shell_accion",
                            texto_limpio=texto_limpio,
                            resolver_fn=_resolver_shell_accion,
                            texto_original=texto_original,
                        )
                    except Exception:
                        # intent_learner no disponible → flujo directo sin memoria
                        r = _resolver_shell_accion(texto_limpio)

                    if r is None:
                        r = {"exito": False, "mensaje": "No se pudo resolver la acción."}

                    if r.get("cancelado"):
                        return _resultado("respuesta",
                                          r.get("mensaje", "Operación cancelada."),
                                          confianza=0.90, query=texto_limpio)

                    guardar_intencion(texto_original, texto_limpio,
                                      "shell_accion", 0.90)
                    guardar_historial(texto_original, texto_limpio,
                                      "shell_accion", "shell_accion", 0.90)
                    return _resultado("respuesta", r.get("mensaje", "Ejecutado."),
                                      confianza=0.90, query=texto_limpio)
                except Exception as _e:
                    logger.warning("brain", "shell acción falló", str(_e))

            # ── TAREAS ────────────────────────────────────────────────────
            elif categoria == _intent_router.CAT_TAREA:
                try:
                    import productivity as _prod
                    r = _prod.gestionar_tarea(texto_original)
                    guardar_intencion(texto_original, texto_limpio, "tarea", 0.93)
                    guardar_historial(texto_original, texto_limpio,
                                      "tarea", "tarea", 0.93)
                    return _resultado("respuesta",
                                      r.get("mensaje", "Tarea gestionada."),
                                      confianza=0.93, query=texto_limpio)
                except Exception as _e:
                    logger.warning("brain", "productivity.gestionar_tarea() falló", str(_e))

            # ── RECORDATORIOS ─────────────────────────────────────────────
            elif categoria == _intent_router.CAT_RECORDATORIO:
                try:
                    import productivity as _prod
                    r = _prod.gestionar_recordatorio(texto_original)
                    guardar_intencion(texto_original, texto_limpio,
                                      "recordatorio", 0.95)
                    guardar_historial(texto_original, texto_limpio,
                                      "recordatorio", "recordatorio", 0.95)
                    return _resultado("respuesta",
                                      r.get("mensaje", "Recordatorio gestionado."),
                                      confianza=0.95, query=texto_limpio)
                except Exception as _e:
                    logger.warning("brain",
                                   "productivity.gestionar_recordatorio() falló", str(_e))

            # ── NOTAS ────────────────────────────────────────────────────
            elif categoria == _intent_router.CAT_NOTA:
                try:
                    import productivity as _prod
                    r = _prod.gestionar_nota(texto_original)
                    guardar_intencion(texto_original, texto_limpio, "nota", 0.93)
                    guardar_historial(texto_original, texto_limpio,
                                      "nota", "nota", 0.93)
                    return _resultado("respuesta",
                                      r.get("mensaje", "Nota gestionada."),
                                      confianza=0.93, query=texto_limpio)
                except Exception as _e:
                    logger.warning("brain", "productivity.gestionar_nota() falló", str(_e))

            # CAT_ABRIR, CAT_BUSCAR, CAT_CODIGO, CAT_DESCONOCIDA →
            # flujo normal continúa abajo sin return

        except Exception as _e:
            logger.warning("brain",
                           "intent_router.clasificar() falló — flujo normal", str(_e))
    # ── Fin Capa -2 ────────────────────────────────────────────────────────

    # ══════════════════════════════════════════════════════════════════════
    # CAPA -1 — MAPA de comandos de sistema (substring + semántico)
    # ══════════════════════════════════════════════════════════════════════
    nombre_cmd_match = None
    frase_match      = ""

    # 1. Búsqueda por substring (determinista, <1ms)
    for frase, nombre_cmd in MAPA_COMANDOS_SISTEMA_ORDENADO:
        if frase in texto_limpio:
            nombre_cmd_match = nombre_cmd
            frase_match      = frase
            break

    # 2. Fallback semántico si no hubo substring match
    if nombre_cmd_match is None and _MAPA_VECTORIZADO_LISTO:
        nombre_cmd_match, score_sem_mapa = _buscar_en_mapa_semantico(texto_limpio)
        if nombre_cmd_match:
            frase_match = f"[semántico score={score_sem_mapa:.2f}]"

    # 3. Ejecutar si hubo match
    if nombre_cmd_match is not None:
        r_mapa = _ejecutar_nombre_cmd(nombre_cmd_match, texto_original,
                                       texto_limpio, frase_match)
        if r_mapa:
            return r_mapa
        # Si _ejecutar_nombre_cmd retornó None, degradar al flujo normal

    # ══════════════════════════════════════════════════════════════════════
    # CAPA 0 — Búsqueda en BD de comandos
    # ══════════════════════════════════════════════════════════════════════
    comando_bd, score_cmd = buscar_comando(texto_limpio)
    if comando_bd and score_cmd >= UMBRAL_COMANDO:
        candidatos.append({
            "tipo":    "comando",
            "nombre":  comando_bd.get("nombre", ""),
            "score":   score_cmd,
            "payload": comando_bd
        })

    # ══════════════════════════════════════════════════════════════════════
    # CAPA 0.5 — Índice de archivos
    # ══════════════════════════════════════════════════════════════════════
    from file_intent import detectar_intencion_archivo
    lista_archivos = detectar_intencion_archivo(texto_original, texto_limpio)
    if lista_archivos:
        mejor = lista_archivos[0]
        if mejor["confianza"] >= 0.45:
            candidatos.append({
                "tipo":    "archivo",
                "nombre":  mejor["nombre"],
                "score":   mejor["confianza"],
                "payload": mejor
            })

    # ══════════════════════════════════════════════════════════════════════
    # RANKING UNIFICADO
    # ══════════════════════════════════════════════════════════════════════
    if candidatos:
        logger.debug("brain", f"Total candidatos al ranking: {len(candidatos)}")
        for i, c in enumerate(candidatos):
            logger.debug("brain",
                         f"  [{i}] tipo={c['tipo']} nombre='{c['nombre']}' "
                         f"score={c['score']:.2f}")

        candidatos.sort(key=lambda x: x["score"], reverse=True)
        ganador    = candidatos[0]
        _arbitrado = False

        if len(candidatos) == 1:
            if ganador["score"] < UMBRAL_ARCHIVO_CONFIRMACION_BRAIN:
                ganador["payload"]["requiere_confirmacion"] = True
            logger.debug("brain",
                         f"Candidato único ({ganador['score']:.2f}) → sin árbitro")

        elif len(candidatos) > 1:
            segundo = candidatos[1]
            margen  = ganador["score"] - segundo["score"]

            es_prioridad_absoluta = (
                ganador["score"] >= 1.0 and (
                    (ganador["tipo"] == "archivo" and
                     ganador["payload"].get("tipo") in ("carpeta", "app")) or
                    (ganador["tipo"] == "archivo" and ganador["score"] == 1.0)
                )
            )

            if es_prioridad_absoluta:
                logger.debug("brain", "Prioridad absoluta → árbitro omitido")
            elif margen < MARGEN_EMPATE:
                logger.info("brain", f"Empate ({margen:.2f}) → árbitro Qwen")
                from external_service import arbitrar_candidatos_qwen
                idx_ganador = arbitrar_candidatos_qwen(texto_original, candidatos)
                ganador     = candidatos[idx_ganador]
                _arbitrado  = True

        

        if ganador["tipo"] == "comando":
            cmd = ganador["payload"]
            guardar_intencion(texto_original, texto_limpio,
                              "comando", ganador["score"])
            guardar_historial(texto_original, texto_limpio,
                              cmd.get("nombre", ""), "comando", ganador["score"])
            incrementar_uso_comando(cmd["id"])
            return _resultado("comando", f"Ejecutando: {cmd['nombre']}",
                              comando=cmd, confianza=ganador["score"],
                              query=texto_limpio, arbitrado=_arbitrado)

        elif ganador["tipo"] == "archivo":
            arch = ganador["payload"]
            guardar_intencion(texto_original, texto_limpio,
                              "archivo", ganador["score"])
            guardar_historial(texto_original, texto_limpio,
                              arch["ruta"], "archivo", ganador["score"])

            if arch.get("requiere_confirmacion") and not _arbitrado:
                return _resultado(
                    "archivo_confirmar", arch["nombre"],
                    comando={"accion": arch["ruta"], "tipo": "app",
                             "nombre": arch["nombre"]},
                    confianza=ganador["score"],
                    query=texto_limpio, arbitrado=_arbitrado
                )

            from database import incrementar_acceso_archivo
            incrementar_acceso_archivo(arch["ruta"])
            return _resultado(
                "archivo", arch["ruta"],
                comando={"accion": arch["ruta"], "tipo": "app",
                         "nombre": arch["nombre"]},
                confianza=ganador["score"],
                query=texto_limpio, arbitrado=_arbitrado
            )

    # ══════════════════════════════════════════════════════════════════════
    # CAPA 1 — Búsqueda web/dinámica
    # ══════════════════════════════════════════════════════════════════════
    busqueda = searcher.analizar(texto_original)
    if busqueda.get("es_busqueda"):
        guardar_intencion(texto_original, texto_limpio, "busqueda", 0.95)
        guardar_historial(texto_original, texto_limpio,
                          busqueda.get("mensaje", ""), "busqueda", 0.95)
        return _resultado("busqueda", busqueda.get("mensaje", ""),
                          confianza=0.95, query=texto_limpio, busqueda=busqueda)

    # ══════════════════════════════════════════════════════════════════════
    # CAPA 2 — Detección de intención
    # ══════════════════════════════════════════════════════════════════════
    tipo_intencion, confianza_intencion = detectar_intencion(texto_original, texto_limpio)
    guardar_intencion(texto_original, texto_limpio, tipo_intencion, confianza_intencion)

    # ══════════════════════════════════════════════════════════════════════
    # CAPA 3 — Respuesta final o desconocido
    # ══════════════════════════════════════════════════════════════════════
    if tipo_intencion == "pregunta":
        respuesta, confianza, _ = buscar_respuesta(texto_limpio)
        if respuesta:
            guardar_historial(texto_original, texto_limpio,
                              respuesta, "pregunta", confianza)
            return _resultado("respuesta", respuesta,
                              confianza=confianza, query=texto_limpio)
        guardar_historial(texto_original, texto_limpio,
                          "sin_respuesta", "pregunta", 0.0)
        return _resultado("respuesta", "Aún no sé la respuesta a eso.",
                          confianza=0.0, query=texto_limpio)

    elif tipo_intencion == "comando":
        guardar_historial(texto_original, texto_limpio,
                          "sin_comando", "comando", 0.0)
        return _resultado("comando", "No reconozco ese comando.",
                          confianza=0.0, query=texto_limpio)

    guardar_historial(texto_original, texto_limpio,
                      "desconocido", "desconocido", 0.0)
    return _resultado("desconocido", "No entendí lo que dijiste.",
                      confianza=0.0, query=texto_limpio)


def _resultado(tipo, texto, comando=None, confianza=0.0,
               query="", busqueda=None, arbitrado=False):
    return {
        "tipo":      tipo,
        "texto":     texto,
        "comando":   comando,
        "confianza": round(confianza, 4),
        "query":     query,
        "busqueda":  busqueda,
        "arbitrado": arbitrado
    }


# ──────────────────────────────────────────────────────────────────────────
# INICIALIZACIÓN DEL MÓDULO
# ──────────────────────────────────────────────────────────────────────────
# Precalentar el mapa semántico al cargar brain.py.
# Se ejecuta en segundo plano — no bloquea el arranque de SARA.
try:
    _construir_mapa_vectorizado()
except Exception:
    pass