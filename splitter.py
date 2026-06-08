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


PATRON_EN_APP = re.compile(
    r'^(?:abre?|pon|muestra|entra a?|accede a?|busca)\s+(.+?)\s+en\s+(.+)$',
    re.IGNORECASE
)

def _detectar_intencion_con_destino(texto_norm):
    """
    Detecta patrones como 'abre X en Y', 'busca X en Y'.
    Retorna (contenido, destino) o None si no aplica.
    Ejemplos:
        'abre google en opera'     → ('google', 'opera')
        'busca python en youtube'  → ('python', 'youtube')
        'abre mis documentos en el explorador' → ('mis documentos', 'el explorador')
    """
    match = PATRON_EN_APP.match(texto_norm.strip())
    if not match:
        return None

    contenido = match.group(1).strip()
    destino   = match.group(2).strip()

    # Evitar falsos positivos: "abre chrome en modo incógnito" no es destino de app
    FALSOS_DESTINO = {
        "modo incognito", "modo privado", "modo seguro", "segundo plano",
        "pantalla completa", "nueva pestana", "nueva ventana"
    }
    if any(f in destino for f in FALSOS_DESTINO):
        return None

    return contenido, destino
def dividir_entrada(texto):
    texto_lower  = texto.strip().lower()
    texto_norm   = normalizar_texto(texto)
    texto_limpio = _quitar_prefijos(texto_norm)

    # ── Detección de patrón "abre X en Y" ────────────────────────
    intencion_destino = _detectar_intencion_con_destino(texto_limpio)
    if intencion_destino:
        contenido, destino = intencion_destino
        logger.debug("splitter", f"Patrón con destino: '{contenido}' en '{destino}'")
        # Retornar como entrada especial marcada para brain
        return [f"__DESTINO__{destino}|{contenido}"]

    

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

    if not _tiene_multiples(texto_limpio):
        return [texto_limpio]

    segmentos           = _dividir_segmentos(texto_limpio)
    segmentos_completos = _completar_segmentos(segmentos)
    resultado = [s.strip() for s in segmentos_completos if s.strip() and len(s.strip()) > 2]

    if not resultado:
        return [texto_limpio]

    logger.debug("splitter", f"Entrada dividida en {len(resultado)} parte(s)",
                 f"original: '{texto[:50]}'")
    return resultado


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
        antes  = partes[0].strip()
        despues = partes[1].strip()
        if not despues:
            continue
        primera_palabra = despues.split()[0] if despues.split() else ""
        if primera_palabra in INICIO_PREGUNTA:
            return True
        if primera_palabra in INICIO_COMANDO:
            return True
        # NUEVO — tipos distintos siempre se separan
        tipo_antes  = _clasificar_tipo_segmento(antes)
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