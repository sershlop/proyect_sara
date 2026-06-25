# 📁 splitter.py
from utils import normalizar_texto
import logger
import re

INTENCIONES_UNICAS = (
    "crea", "crear", "programa", "script", "codigo", "genera",
    "desarrolla", "construye", "escribe un", "haz un", "hazme un",
    "necesito un programa", "quiero un programa"
)

PREFIJOS_SOCIALES = (
    "oye sara", "sara dime", "oye dime", "sara",
    "oye", "a ver", "mira", "escucha", "hey sara",
    "hey", "dime", "cuentame", "explicame", "platicame"
)

SEPARADORES = (
    " y ademas ", " y tambien ", " y despues ",
    " ademas ", " tambien ", " igualmente ",
    " y ", " pero tambien ", " aunque tambien "
)

INICIO_PREGUNTA = {
    "que", "como", "cuando", "donde", "quien", "cual",
    "cuanto", "cuantos", "cuanta", "cuantas",
    "dime", "explicame", "cuentame", "por que", "para que"
}

INICIO_COMANDO = {
    "abre", "abrir", "ejecuta", "pon", "inicia",
    "cierra", "busca", "muestra", "reproduce",
    "silencia", "silenciar", "pausa", "pausar",
    "sube", "subir", "baja", "bajar",
    "siguiente", "anterior", "bloquea", "suspende",
    "vacía", "vaciar", "crea", "crear", "genera"
}

PALABRAS_ELIMINAR_SEGMENTO = {
    "que", "como", "cuando", "donde", "quien", "cual",
    "es", "son", "fue", "se", "de", "del", "la", "el",
    "los", "las", "un", "una", "tiene", "esta", "hace",
    "abre", "abrir", "dime", "explicame"
}

TIPOS_CODIGO = {
    "crea", "crear", "genera", "generar", "desarrolla",
    "construye", "escribe", "hazme", "haz"
}

TIPOS_ARCHIVO = {
    "abre", "abrir", "muestra", "mostrar", "entra",
    "accede", "encuentra", "busca"
}

FALSOS_DESTINO = {
    "modo incognito", "modo privado", "modo seguro", "segundo plano",
    "pantalla completa", "nueva pestana", "nueva ventana"
}

# ── Patrón "abre X en Y" — un solo bloque ────────────────────────────────────
PATRON_EN_APP = re.compile(
    r'^(?:abre?|pon|muestra|entra a?|accede a?|busca)\s+(.+?)\s+en\s+(.+)$',
    re.IGNORECASE
)

# ── Patrón para extraer múltiples "abre X en Y" de una entrada compuesta ─────
# Captura cada sub-intención de la forma "verbo contenido en destino"
PATRON_MULTI_DESTINO = re.compile(
    r'(?:abre?|pon|muestra|entra a?|accede a?|busca)\s+(.+?)\s+en\s+(\S+)',
    re.IGNORECASE
)
PATRON_ARCHIVO_EN_CARPETA = re.compile(
    r'(?:abre?|muestra|busca|encuentra)\s+(?:el\s+)?(?:archivo\s+)?([^\s]+\.[^\s]+)\s+'
    r'(?:de|en|dentro de|en la carpeta|de la carpeta|dentro de la carpeta)\s+'
    r'(?:la\s+carpeta\s+|carpeta\s+)?([^\s]+)',
    re.IGNORECASE
)

# ══════════════════════════════════════════════════════════════════════════════
#  DETECCIÓN DE PATRONES CON DESTINO
# ══════════════════════════════════════════════════════════════════════════════

def _detectar_intencion_con_destino(texto_norm):
    """
    Detecta patrones como 'abre X en Y', 'busca X en Y'.
    Retorna (contenido, destino) o None si no aplica.

    Solo aplica cuando TODA la cadena es un único patrón.
    Para múltiples patrones en una cadena usa _detectar_multiples_destinos().
    """
    match = PATRON_EN_APP.match(texto_norm.strip())
    if not match:
        return None

    contenido = match.group(1).strip()
    destino   = match.group(2).strip()

    if any(f in destino for f in FALSOS_DESTINO):
        return None

    return contenido, destino

def dividir_entrada(texto):
    texto_lower  = texto.strip().lower()
    texto_norm   = normalizar_texto(texto)
    texto_limpio = _quitar_prefijos(texto_norm)

    # ── 0. Patrón "abre archivo.ext de/en carpeta" — entrada única ───────────
    # Usa texto ORIGINAL para preservar extensiones y guiones
    marcador_archivo_carpeta = _detectar_archivo_en_carpeta(texto)
    if marcador_archivo_carpeta:
        logger.debug("splitter", f"Ruta directa archivo→carpeta: {marcador_archivo_carpeta}")
        return [marcador_archivo_carpeta]

    # ── 1. Múltiples patrones "abre X en Y y abre Z en W" ────────────────────
    # ... resto del código existente sin cambios
def _detectar_multiples_destinos(texto_norm):
    """
    Detecta múltiples patrones "abre X en Y" en una misma entrada.

    Casos que resuelve:
        'abre claude en opera y abre claude en chrome'
        → ['__DESTINO__opera|claude', '__DESTINO__chrome|claude']

        'abre youtube en opera y abre gmail en chrome'
        → ['__DESTINO__opera|youtube', '__DESTINO__chrome|gmail']

    Retorna lista de marcadores __DESTINO__ o None si no hay múltiples.
    """
    matches = list(PATRON_MULTI_DESTINO.finditer(texto_norm))
    if len(matches) < 2:
        return None

    marcadores = []
    for m in matches:
        contenido = m.group(1).strip()
        destino   = m.group(2).strip()
        if any(f in destino for f in FALSOS_DESTINO):
            continue
        # Limpiar posibles separadores que hayan quedado en el contenido
        for sep in (" y ", " and ", ", "):
            contenido = contenido.replace(sep, " ").strip()
        marcadores.append(f"__DESTINO__{destino}|{contenido}")

    return marcadores if len(marcadores) >= 2 else None

def _detectar_archivo_en_carpeta(texto_original):
    """
    Detecta frases como:
        'abre brain.py de sara.2'
        'abre el archivo brain.py en la carpeta sara_gui'
        'muestra config.py en sara_github'

    Convierte directamente a marcador __CARPETA_CTX__ sin necesidad
    de dividir en dos segmentos — la carpeta y el archivo van juntos.

    Retorna el marcador listo, o None si no aplica.
    """
    match = PATRON_ARCHIVO_EN_CARPETA.search(texto_original)
    if not match:
        return None

    nombre_archivo = match.group(1).strip()
    nombre_carpeta = match.group(2).strip()

    # Sanidad mínima
    if not nombre_archivo or not nombre_carpeta:
        return None
    if nombre_carpeta.lower() in {"carpeta", "folder", "directorio"}:
        return None

    logger.debug("splitter",
                 f"Archivo en carpeta detectado: '{nombre_archivo}' en '{nombre_carpeta}'")
    return f"__CARPETA_CTX__{nombre_carpeta}|abre el archivo {nombre_archivo}"
# ══════════════════════════════════════════════════════════════════════════════
#  CONTEXTO DE CARPETA PADRE (para resolver ambigüedad de archivos)
# ══════════════════════════════════════════════════════════════════════════════

# REEMPLAZA la función _extraer_contexto_carpeta() existente

def _extraer_contexto_carpeta(texto_original):
    """
    Detecta si en la entrada se menciona una carpeta específica antes de un archivo.
    IMPORTANTE: recibe texto_original (sin normalizar) para preservar guiones bajos,
    puntos y caracteres especiales en nombres de carpeta como sara_github, sara.2, etc.

    Requiere la keyword explícita 'carpeta/folder/directorio' para evitar
    falsos positivos en entradas simples como 'abre brain.py'.

    Retorna el nombre de la carpeta mencionada, o None.
    """
    PATRON_CARPETA = re.compile(
        r'(?:abre?|entra a?|ve a?|accede a?)\s+(?:la\s+)?(?:carpeta|folder|directorio)\s+([^\s,]+)',
        re.IGNORECASE
    )
    IGNORAR = {"carpeta", "folder", "directorio", "archivo", "fichero",
               "el", "la", "los", "las"}

    match = PATRON_CARPETA.search(texto_original)
    if not match:
        return None
    nombre_carpeta = match.group(1).strip()
    if nombre_carpeta.lower() in IGNORAR:
        return None
    return nombre_carpeta


def _inyectar_contexto_carpeta(segmentos, carpeta_ctx):
    """
    Añade el marcador __CARPETA_CTX__nombre al inicio de los segmentos
    que abren archivos (no carpetas), para que file_intent los filtre
    por carpeta padre.

    El marcador se elimina en file_intent antes de hacer la búsqueda.
    """
    if not carpeta_ctx:
        return segmentos

    PALABRAS_ARCHIVO_CTX = {"archivo", "fichero", "script", "py", "js", "txt", "json", "md"}

    resultado = []
    for seg in segmentos:
        palabras = set(seg.lower().split())
        # Aplica el contexto si el segmento menciona 'archivo' o tiene extensión conocida
        tiene_extension = any("." in p for p in seg.split())
        tiene_keyword   = bool(palabras & PALABRAS_ARCHIVO_CTX)
        # No inyectar en segmentos que ya abren una carpeta
        abre_carpeta = any(p in palabras for p in {"carpeta", "folder", "directorio"})

        if (tiene_extension or tiene_keyword) and not abre_carpeta:
            resultado.append(f"__CARPETA_CTX__{carpeta_ctx}|{seg}")
        else:
            resultado.append(seg)

    return resultado


# ══════════════════════════════════════════════════════════════════════════════
#  FUNCIÓN PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def dividir_entrada(texto):
    texto_lower  = texto.strip().lower()
    texto_norm   = normalizar_texto(texto)
    texto_limpio = _quitar_prefijos(texto_norm)

    # ── 1. Múltiples patrones "abre X en Y y abre Z en W" ────────────────────
    # Tiene prioridad sobre todo lo demás — detecta antes de intentar match único
    multiples_destinos = _detectar_multiples_destinos(texto_limpio)
    if multiples_destinos:
        logger.debug("splitter",
                     f"Múltiples destinos detectados: {len(multiples_destinos)} marcadores",
                     str(multiples_destinos))
        return multiples_destinos

    # ── 2. Patrón único "abre X en Y" ────────────────────────────────────────
    intencion_destino = _detectar_intencion_con_destino(texto_limpio)
    if intencion_destino:
        contenido, destino = intencion_destino
        logger.debug("splitter", f"Patrón con destino: '{contenido}' en '{destino}'")

        # Contenido múltiple con mismo destino: "abre gmail y youtube en chrome"
        SEPS_CONTENIDO = [" y ", " and ", ", "]
        partes_contenido = [contenido]
        for sep in SEPS_CONTENIDO:
            if sep in contenido:
                partes_contenido = [p.strip() for p in contenido.split(sep) if p.strip()]
                break

        if len(partes_contenido) > 1:
            logger.debug("splitter",
                         f"Contenido múltiple → mismo destino '{destino}'",
                         str(partes_contenido))
            return [f"__DESTINO__{destino}|{parte}" for parte in partes_contenido]

        return [f"__DESTINO__{destino}|{contenido}"]

    # ── 3. Detectar contexto de carpeta padre para ambigüedad de archivos ────
    carpeta_ctx = _extraer_contexto_carpeta(texto)

    # ── 4. Intenciones únicas (código) — no dividir salvo excepción ──────────
    if any(texto_lower.startswith(p) or f" {p} " in texto_lower for p in INTENCIONES_UNICAS):
        debe_dividir = False
        for separador in SEPARADORES:
            if separador in texto_norm:
                partes = texto_norm.split(separador, 1)
                if len(partes) == 2:
                    tipo_despues = _clasificar_tipo_segmento(partes[1].strip())
                    if tipo_despues in ("archivo", "comando"):
                        debe_dividir = True
                        break
        if not debe_dividir:
            return [texto]

    # ── 5. Flujo normal — dividir por separadores ─────────────────────────────
    if not _tiene_multiples(texto_limpio):
        return [texto_limpio]

    segmentos           = _dividir_segmentos(texto_limpio)
    segmentos_completos = _completar_segmentos(segmentos)
    resultado = [s.strip() for s in segmentos_completos if s.strip() and len(s.strip()) > 2]

    if not resultado:
        return [texto_limpio]

    # ── 6. Inyectar contexto de carpeta padre si se detectó ──────────────────
    if carpeta_ctx:
        # Reconstruir el primer segmento (carpeta) desde el texto original
        # para preservar guiones bajos y caracteres especiales
        resultado_corregido = []
        for seg in resultado:
            if seg.startswith("__CARPETA_CTX__"):
                resultado_corregido.append(seg)
            else:
                # Si el segmento menciona la carpeta normalizada, buscar
                # la versión original en el texto de entrada
                carpeta_norm = normalizar_texto(carpeta_ctx)
                if carpeta_norm in seg:
                    seg = seg.replace(carpeta_norm, carpeta_ctx)
                resultado_corregido.append(seg)
        resultado = _inyectar_contexto_carpeta(resultado_corregido, carpeta_ctx)
        logger.debug("splitter",
                     f"Contexto de carpeta inyectado: '{carpeta_ctx}'",
                     str(resultado))

    logger.debug("splitter",
                 f"Entrada dividida en {len(resultado)} parte(s)",
                 f"original: '{texto[:50]}'")
    return resultado


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def es_entrada_simple(texto):
    texto_norm   = normalizar_texto(texto)
    texto_limpio = _quitar_prefijos(texto_norm)
    return not _tiene_multiples(texto_limpio)


def _quitar_prefijos(texto):
    for prefijo in PREFIJOS_SOCIALES:
        prefijo_norm = normalizar_texto(prefijo)
        if texto.startswith(prefijo_norm):
            texto = texto[len(prefijo_norm):].strip()
            break
    return texto.strip()


def _clasificar_tipo_segmento(segmento):
    primera = segmento.strip().split()[0] if segmento.strip() else ""
    if primera in TIPOS_CODIGO:
        return "codigo"
    if primera in TIPOS_ARCHIVO:
        return "archivo"
    if primera in INICIO_PREGUNTA:
        return "pregunta"
    return "comando"


def _tiene_multiples(texto):
    for separador in SEPARADORES:
        if separador not in texto:
            continue
        partes = texto.split(separador, 1)
        if len(partes) < 2:
            continue
        antes   = partes[0].strip()
        despues = partes[1].strip()
        if not despues:
            continue
        primera_palabra = despues.split()[0] if despues.split() else ""
        if primera_palabra in INICIO_PREGUNTA:
            return True
        if primera_palabra in INICIO_COMANDO:
            return True
        tipo_antes   = _clasificar_tipo_segmento(antes)
        tipo_despues = _clasificar_tipo_segmento(despues)
        if tipo_antes != tipo_despues:
            return True
    return False


def _dividir_segmentos(texto):
    segmentos = [texto]
    for separador in SEPARADORES:
        nuevos_segmentos = []
        for segmento in segmentos:
            if separador not in segmento:
                nuevos_segmentos.append(segmento)
                continue
            partes = segmento.split(separador)
            for i, parte in enumerate(partes):
                parte = parte.strip()
                if not parte:
                    continue
                if i > 0:
                    primera       = parte.split()[0] if parte.split() else ""
                    tipo_parte    = _clasificar_tipo_segmento(parte)
                    tipo_anterior = _clasificar_tipo_segmento(nuevos_segmentos[-1]) if nuevos_segmentos else None
                    if primera in INICIO_PREGUNTA or primera in INICIO_COMANDO or tipo_parte != tipo_anterior:
                        nuevos_segmentos.append(parte)
                    else:
                        if nuevos_segmentos:
                            nuevos_segmentos[-1] += f" {parte}"
                        else:
                            nuevos_segmentos.append(parte)
                else:
                    nuevos_segmentos.append(parte)
        segmentos = nuevos_segmentos
    return segmentos


def _completar_segmentos(segmentos):
    if not segmentos:
        return segmentos
    tema = _extraer_tema_segmento(segmentos[0])
    if not tema:
        return segmentos
    resultado = [segmentos[0]]
    for segmento in segmentos[1:]:
        tema_propio = _extraer_tema_segmento(segmento)
        if tema_propio:
            resultado.append(segmento)
        else:
            resultado.append(_completar_con_tema(segmento, tema))
    return resultado


def _extraer_tema_segmento(segmento):
    palabras   = segmento.split()
    candidatos = [p for p in palabras
                  if p not in PALABRAS_ELIMINAR_SEGMENTO and len(p) > 2]
    return candidatos[-1] if candidatos else None


def _completar_con_tema(segmento, tema):
    if not tema or tema in segmento:
        return segmento
    if segmento.endswith(("de", "sobre", "en", "con")):
        return f"{segmento} {tema}"
    return f"{segmento} de la {tema}"
