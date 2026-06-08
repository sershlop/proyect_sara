# 📁 context.py
from utils import normalizar_texto
import logger

_contexto = {
    "tema_actual":      None,
    "ultima_pregunta":  None,
    "ultima_respuesta": None,
    "turno":            0
}

CONECTORES_INICIO = ("y ", "pero ", "tambien ", "ademas ", "aunque ", "entonces ", "por eso ")
POSESIVOS         = {"su", "sus"}
PRONOMBRES        = {"el", "ella", "ello", "lo", "la", "ese", "esa", "eso", "este", "esta", "esto"}
VERBOS_SER_ESTAR  = {"es", "son", "fue", "eran", "sera", "esta", "estan", "tiene", "tienen",
                     "mide", "miden", "pesa", "pesan", "orbita", "gira", "existe", "vive"}
INTERROGATIVAS    = {"cual", "cuales", "como", "cuando", "donde", "cuanto", "cuantos",
                     "cuanta", "cuantas", "quien", "quienes", "que", "por que", "para que"}


def actualizar(pregunta_original, respuesta, tema=None):
    global _contexto
    _contexto["ultima_pregunta"]  = pregunta_original
    _contexto["ultima_respuesta"] = respuesta
    _contexto["turno"]           += 1
    if tema:
        _contexto["tema_actual"] = tema
    else:
        tema_extraido = _extraer_tema(pregunta_original)
        if tema_extraido:
            _contexto["tema_actual"] = tema_extraido
    logger.debug("context", f"Contexto actualizado — tema: '{_contexto['tema_actual']}'",
                 f"turno: {_contexto['turno']}")


def obtener_contexto():
    return dict(_contexto)


def tiene_contexto():
    return _contexto["tema_actual"] is not None


def limpiar():
    global _contexto
    _contexto = {"tema_actual": None, "ultima_pregunta": None, "ultima_respuesta": None, "turno": 0}
    logger.debug("context", "Contexto limpiado.")


def necesita_contexto(texto):
    if not tiene_contexto():
        return False
    texto_norm = normalizar_texto(texto)
    palabras   = texto_norm.split()
    if not palabras:
        return False

    # Verificar conectores + interrogativa PRIMERO
    if palabras[0] == "y" and len(palabras) > 1:
        if palabras[1] in INTERROGATIVAS:
            return True

    # Después verificar tema
    tema_actual_norm = normalizar_texto(_contexto["tema_actual"] or "")
    tema_nuevo       = _extraer_tema(texto_norm)

    if tema_nuevo and tema_nuevo != tema_actual_norm:
        return False

    if palabras[0] == "y" and len(palabras) > 1:
        if palabras[1] in INTERROGATIVAS:
            return True

    if palabras[0] in ("y", "pero", "tambien", "ademas"):
        if not tema_nuevo or tema_nuevo == tema_actual_norm:
            return True

    primeras = set(palabras[:3])
    if primeras & POSESIVOS:
        if not tema_nuevo or tema_nuevo == tema_actual_norm:
            return True

    if palabras[0] in INTERROGATIVAS and tema_actual_norm not in texto_norm:
        if _contiene_posesivo_o_pronombre(palabras):
            if not tema_nuevo or tema_nuevo == tema_actual_norm:
                return True

    return False


def _contiene_posesivo_o_pronombre(palabras):
    for p in palabras:
        if p in POSESIVOS or p in PRONOMBRES:
            return True
    return False


def resolver(texto_original):
    if not tiene_contexto():
        return texto_original, None
    tema              = _contexto["tema_actual"]
    texto_norm        = normalizar_texto(texto_original)
    nucleo            = _quitar_conectores_inicio(texto_norm)
    estructura        = _analizar_estructura(nucleo)
    pregunta_resuelta = _construir_pregunta(estructura, tema)
    logger.debug("context", f"Referencia resuelta",
                 f"'{texto_original}' → '{pregunta_resuelta}' (tema: {tema})")
    return pregunta_resuelta, tema


def _quitar_conectores_inicio(texto):
    for conector in CONECTORES_INICIO:
        if texto.startswith(conector):
            return texto[len(conector):].strip()
    return texto.strip()


def _analizar_estructura(nucleo):
    palabras   = nucleo.split()
    estructura = {"interrogativa": None, "verbo": None, "posesivo": None,
                  "nucleo": None, "resto": [], "original": nucleo}
    resto = []
    for i, palabra in enumerate(palabras):
        if i == 0 and palabra in INTERROGATIVAS:
            estructura["interrogativa"] = palabra
        elif palabra in POSESIVOS:
            estructura["posesivo"] = palabra
        elif palabra in PRONOMBRES:
            siguiente = palabras[i+1] if i+1 < len(palabras) else ""
            if siguiente in VERBOS_SER_ESTAR:
                estructura["posesivo"] = palabra
            else:
                resto.append(palabra)
        elif palabra in VERBOS_SER_ESTAR and estructura["verbo"] is None:
            estructura["verbo"] = palabra
            resto.append(palabra)
        else:
            if estructura["nucleo"] is None and len(palabra) > 2:
                estructura["nucleo"] = palabra
            resto.append(palabra)
    estructura["resto"] = resto
    return estructura


def _construir_pregunta(estructura, tema):
    original      = estructura["original"]
    nucleo        = estructura["nucleo"]
    posesivo      = estructura["posesivo"]
    interrogativa = estructura["interrogativa"]
    articulo      = _articulo_para_tema(tema)

    if posesivo and nucleo:
        partes = [p for p in original.split() if p not in POSESIVOS and p not in PRONOMBRES]
        base   = " ".join(partes)
        return f"{base} de {articulo} {tema}".strip()

    if interrogativa and not posesivo:
        tema_norm = normalizar_texto(tema)
        if tema_norm not in original:
            return f"{original} {tema}".strip()

    tema_norm = normalizar_texto(tema)
    if tema_norm not in original:
        if original.endswith(("de", "sobre", "en", "con")):
            return f"{original} {articulo} {tema}".strip()
        else:
            return f"{original} de {articulo} {tema}".strip()
    return original


def _articulo_para_tema(tema):
    tema_lower = tema.lower().strip()
    NOMBRES_PROPIOS = {"marte", "venus", "jupiter", "saturno", "mercurio", "neptuno",
                       "urano", "pluton", "tierra", "youtube", "google", "chrome",
                       "firefox", "python", "java", "windows", "linux"}
    if tema_lower in NOMBRES_PROPIOS:
        return ""
    MASCULINOS = {"sol", "mar", "rio", "cielo", "universo", "planeta",
                  "sistema", "espacio", "cosmos", "satelite"}
    if tema_lower in MASCULINOS:
        return "el"
    FEMENINOS = {"luna", "tierra", "estrella", "galaxia", "nebulosa",
                 "atmosfera", "gravedad", "orbita"}
    if tema_lower in FEMENINOS:
        return "la"
    return "la"


def _extraer_tema(pregunta):
    texto_norm = normalizar_texto(pregunta)
    PALABRAS_VACIAS = {
        "que", "como", "cuando", "donde", "quien", "cual", "cuanto",
        "cuantos", "cuanta", "cuantas", "por", "para", "es", "son",
        "fue", "eran", "se", "de", "del", "la", "el", "los", "las",
        "un", "una", "unos", "unas", "tiene", "tienen", "esta", "estan",
        "hace", "hacen", "abre", "abrir", "ejecuta", "pon", "inicia",
        "dime", "explicame", "sabes", "sobre", "acerca", "hay", "existe",
        "tamano", "color", "forma", "nombre", "masa", "peso",
        "distancia", "temperatura", "velocidad",
        "cual", "cuales", "segundo", "primero", "tercero",
        "mayor", "menor", "mejor", "peor"
    }
    palabras   = texto_norm.split()
    candidatos = [p for p in palabras if p not in PALABRAS_VACIAS and len(p) > 2]
    if not candidatos:
        return None
    # Retornar frase completa de candidatos en vez de solo el último
    return candidatos[-1] if candidatos else None