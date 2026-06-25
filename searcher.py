# 📁 searcher.py
# Módulo de búsquedas dinámicas de SARA
# Detecta intenciones de búsqueda, extrae términos
# y construye URLs para abrir en el navegador
# Compatible con: Google, YouTube, Wikipedia,
#                 Spotify, DuckDuckGo y más

from urllib.parse import quote
from utils import normalizar_texto
import logger

# ──────────────────────────────────────────────
# 🔹 VERBOS DE BÚSQUEDA
# ──────────────────────────────────────────────

# Verbos que indican intención de búsqueda general
VERBOS_BUSQUEDA = {
    "busca", "buscar", "encuentra", "encontrar",
    "muestra", "mostrar", "dame", "dime",
    "busqueda", "investigar", "investiga",
    "consulta", "consultar", "que hay de",
    "que dice", "que es lo que"
}

# Verbos que indican reproducción multimedia
VERBOS_REPRODUCCION = {
    "reproduce", "reproducir", "pon", "poner",
    "play", "escucha", "escuchar", "toca", "tocar",
    "quiero escuchar", "quiero ver", "quiero oir",
    "ponme", "pónme", "quiero", "ver"
}

# Verbos que indican abrir plataforma específica
VERBOS_ABRIR = {
    "abre", "abrir", "entra", "entrar",
    "ve a", "ir a", "navega", "navegar"
}


# ──────────────────────────────────────────────
# 🔹 PLATAFORMAS SOPORTADAS
# ──────────────────────────────────────────────

PLATAFORMAS = {
    # Nombre → (keywords de detección, URL de búsqueda, URL base)
    "youtube": {
        "keywords": {"youtube", "yotube", "yutube", "yt"},
        "url_busqueda": "https://www.youtube.com/results?search_query={}",
        "url_base": "https://www.youtube.com",
        "tipo": "video"
    },
    "google": {
        "keywords": {"google", "buscador", "web", "internet"},
        "url_busqueda": "https://www.google.com/search?q={}",
        "url_base": "https://www.google.com",
        "tipo": "web"
    },
    "wikipedia": {
        "keywords": {"wikipedia", "wiki", "enciclopedia"},
        "url_busqueda": "https://es.wikipedia.org/wiki/Special:Search?search={}",
        "url_base": "https://es.wikipedia.org",
        "tipo": "informacion"
    },
    "spotify": {
        "keywords": {"spotify", "musica", "música", "cancion", "canción"},
        "url_busqueda": "https://open.spotify.com/search/{}",
        "url_base": "https://open.spotify.com",
        "tipo": "musica"
    }, 
   
    "bing": {
        "keywords": {"bing", "bingbuscador"},
        "url_busqueda": "https://www.bing.com/search?q={}",
        "url_base": "https://www.bing.com",
        "tipo": "web"
    },
    "duckduckgo": {
        "keywords": {"duckduckgo", "duck", "ddg"},
        "url_busqueda": "https://duckduckgo.com/?q={}",
        "url_base": "https://duckduckgo.com",
        "tipo": "web"
    },
}


# Plataforma por defecto cuando no se especifica
PLATAFORMA_DEFAULT = "google"

# Plataforma por defecto para reproducción
PLATAFORMA_REPRODUCCION_DEFAULT = "youtube"

# Palabras a eliminar al extraer el término
PALABRAS_ELIMINAR = {
    "en", "de", "del", "la", "el", "los", "las",
    "un", "una", "para", "por", "con", "sobre",
    "busca", "buscar", "encuentra", "muestra",
    "reproduce", "reproducir", "pon", "poner",
    "escucha", "escuchar", "toca", "tocar",
    "abre", "abrir", "entra", "entrar",
    "quiero", "quiero ver", "quiero escuchar",
    "ponme", "dame", "dime", "play", "ver",
    "youtube", "google", "wikipedia", "spotify",
    "wiki", "yt",
    "cancion", "canción", "buscador", "web",
    "internet", "enciclopedia", "privado",
    "bing", "duckduckgo", "duck", "ddg"
}


# ──────────────────────────────────────────────
# 🔹 FUNCIÓN PRINCIPAL
# ──────────────────────────────────────────────
def analizar(texto):
    """
    Analiza el texto y determina si es una búsqueda.

    Retorna: dict con resultado
    {
        "es_busqueda":  bool,
        "plataforma":   str,
        "termino":      str,
        "url":          str,
        "tipo_verbo":   "busqueda" | "reproduccion" | "abrir",
        "mensaje":      str
    }

    Ejemplos:
        "busca despacito en youtube"
        → plataforma: youtube, termino: despacito

        "reproduce beethoven en spotify"
        → plataforma: spotify, termino: beethoven

        "busca como hacer tacos"
        → plataforma: google (default), termino: como hacer tacos

        "busca en wikipedia que es la luna"
        → plataforma: wikipedia, termino: que es la luna

        "abre cmd"
        → no es búsqueda → flujo de comandos
    """
    texto_norm = normalizar_texto(texto)
    palabras   = texto_norm.split()

    if not palabras:
        return _no_busqueda()

    # PASO 1: Detectar tipo de verbo
    tipo_verbo = _detectar_verbo(palabras)
    if not tipo_verbo:
        return _no_busqueda()

    # PASO 2: Validar verbo "abrir"
    # Solo es búsqueda si menciona plataforma conocida
    # Si no → derivar a flujo de comandos
    if tipo_verbo == "abrir":
        if not _tiene_plataforma_explicita(texto_norm):
            logger.debug(
                "searcher",
                f"Verbo abrir sin plataforma conocida: '{texto[:40]}'",
                "→ derivando a flujo de comandos"
            )
            return _no_busqueda()

    # PASO 3: Detectar plataforma
    plataforma = _detectar_plataforma(texto_norm, tipo_verbo)

    # PASO 4: Extraer término
    termino = _extraer_termino(texto_norm, plataforma)

    if not termino:
        # Sin término → abrir plataforma directamente
        url     = PLATAFORMAS[plataforma]["url_base"]
        mensaje = f"Abriendo {plataforma.capitalize()}..."
        logger.info("searcher", f"Abriendo plataforma: {plataforma}")
        return _resultado(True, plataforma, "", url, tipo_verbo, mensaje)

    # PASO 5: Construir URL y mensaje
    url     = _construir_url(plataforma, termino)
    mensaje = _construir_mensaje(tipo_verbo, plataforma, termino)

    logger.info(
        "searcher",
        f"Búsqueda: '{termino}' en {plataforma}",
        f"url: {url[:60]}"
    )

    return _resultado(True, plataforma, termino, url, tipo_verbo, mensaje)


def _tiene_plataforma_explicita(texto_norm):
    """
    Verifica si el texto menciona explícitamente
    una plataforma conocida.

    "abre youtube"     → True  ✅
    "abre wikipedia"   → True  ✅
    "abre cmd"         → False ❌
    "abre calculadora" → False ❌
    """
    palabras = texto_norm.split()
    for nombre, datos in PLATAFORMAS.items():
        for keyword in datos["keywords"]:
            if keyword in palabras:
                return True
    return False

# ──────────────────────────────────────────────
# 🔹 DETECCIÓN DE VERBO
#  ──────────────────────────────────────────────
def _detectar_verbo(palabras):
    """
    Detecta si el texto empieza con un verbo de búsqueda.

    REGLA:
    VERBOS_BUSQUEDA/REPRODUCCION → siempre búsqueda
    VERBOS_ABRIR → solo búsqueda si hay plataforma conocida
                   si no hay plataforma → no es búsqueda
                   → deja que brain.py lo maneje como comando

    Retorna: "busqueda" | "reproduccion" | "abrir" | None
    """
    primera = palabras[0] if palabras else ""

    # Verbos de búsqueda y reproducción
    # → siempre son búsqueda sin importar plataforma
    if primera in VERBOS_BUSQUEDA:
        return "busqueda"
    if primera in VERBOS_REPRODUCCION:
        return "reproduccion"

    # Verbos de abrir → SOLO si hay plataforma conocida
    # Si no hay plataforma → retorna None
    # → brain.py lo tratará como comando desconocido
    if primera in VERBOS_ABRIR:
        return "abrir"  # marcamos como abrir pero
                        # se validará en analizar()

    # Verificar primeras dos palabras
    if len(palabras) >= 2:
        dos_palabras = f"{palabras[0]} {palabras[1]}"
        if dos_palabras in VERBOS_BUSQUEDA:
            return "busqueda"
        if dos_palabras in VERBOS_REPRODUCCION:
            return "reproduccion"

    return None
# ──────────────────────────────────────────────
# 🔹 DETECCIÓN DE PLATAFORMA
# ──────────────────────────────────────────────

def _detectar_plataforma(texto_norm, tipo_verbo):
    """
    Detecta la plataforma mencionada en el texto.
    Si no se menciona → usa plataforma por defecto según tipo de verbo.
    """
    for nombre, datos in PLATAFORMAS.items():
        for keyword in datos["keywords"]:
            if keyword in texto_norm.split():
                return nombre

    # Sin plataforma explícita → usar default según verbo
    if tipo_verbo == "reproduccion":
        return PLATAFORMA_REPRODUCCION_DEFAULT
    else:
        return PLATAFORMA_DEFAULT


# ──────────────────────────────────────────────
# 🔹 EXTRACCIÓN DE TÉRMINO
# ──────────────────────────────────────────────

def _extraer_termino(texto_norm, plataforma):
    """
    Extrae el término de búsqueda eliminando:
    - Verbos de búsqueda
    - Nombre de plataforma
    - Preposiciones y artículos

    "busca despacito en youtube"
    → elimina: "busca", "en", "youtube"
    → término: "despacito"

    "reproduce musica de beethoven en spotify"
    → elimina: "reproduce", "musica", "de", "en", "spotify"
    → término: "beethoven"
    """
    palabras  = texto_norm.split()
    resultado = []

    for palabra in palabras:
        if palabra not in PALABRAS_ELIMINAR:
            resultado.append(palabra)

    termino = " ".join(resultado).strip()

    # Limpiar conectores sobrantes al inicio
    CONECTORES_INICIO = ("de ", "del ", "la ", "el ", "un ", "una ")
    for conector in CONECTORES_INICIO:
        if termino.startswith(conector):
            termino = termino[len(conector):]

    return termino.strip()


# ──────────────────────────────────────────────
# 🔹 CONSTRUCCIÓN DE URL
# ──────────────────────────────────────────────

def _construir_url(plataforma, termino):
    """
    Construye la URL de búsqueda para la plataforma dada.
    Codifica el término para URL válida.

    "despacito" → "despacito"
    "como hacer tacos" → "como%20hacer%20tacos"
    """
    termino_codificado = quote(termino)
    url_template       = PLATAFORMAS[plataforma]["url_busqueda"]
    return url_template.format(termino_codificado)


# ──────────────────────────────────────────────
# 🔹 CONSTRUCCIÓN DE MENSAJE
# ──────────────────────────────────────────────

def _construir_mensaje(tipo_verbo, plataforma, termino):
    """
    Construye el mensaje que verá el usuario.
    """
    plataforma_nombre = plataforma.capitalize()

    if tipo_verbo == "reproduccion":
        return f"Reproduciendo '{termino}' en {plataforma_nombre}..."
    elif tipo_verbo == "busqueda":
        return f"Buscando '{termino}' en {plataforma_nombre}..."
    else:
        return f"Abriendo '{termino}' en {plataforma_nombre}..."


# ──────────────────────────────────────────────
# 🔹 HELPERS INTERNOS
# ──────────────────────────────────────────────

def _resultado(es_busqueda, plataforma, termino, url, tipo_verbo, mensaje):
    """Dict estándar de resultado."""
    return {
        "es_busqueda": es_busqueda,
        "plataforma":  plataforma,
        "termino":     termino,
        "url":         url,
        "tipo_verbo":  tipo_verbo,
        "mensaje":     mensaje
    }


def _no_busqueda():
    """Retorna resultado negativo estándar."""
    return {
        "es_busqueda": False,
        "plataforma":  None,
        "termino":     None,
        "url":         None,
        "tipo_verbo":  None,
        "mensaje":     ""
    }


def plataformas_disponibles():
    """
    Retorna lista de plataformas soportadas.
    Útil para /ayuda en sara.py
    """
    return list(PLATAFORMAS.keys())