# 📁 social.py
import random
from utils import normalizar_texto
from database import guardar_interaccion_social
import logger

MAX_PALABRAS_CORTAS = 3

SALUDOS = {
    "hola", "hello", "hi", "hey", "buenas", "buenos dias",
    "buenas tardes", "buenas noches", "buen dia",
    "que tal", "que onda", "que pedo", "que hay", "que hubo",
    "como estas", "como esta", "como andas", "como te va",
    "como estas sara", "hola sara", "hey sara", "buenas sara",
    "saludos", "alo", "bueno"
}

DESPEDIDAS = {
    "adios", "hasta luego", "bye", "chao", "chau",
    "nos vemos", "hasta pronto", "hasta manana",
    "me voy", "ahi nos vemos", "cuidate",
    "hasta la proxima", "bye bye"
}

AGRADECIMIENTOS = {
    "gracias", "muchas gracias", "thank you", "thanks",
    "te lo agradezco", "muy amable", "genial gracias",
    "perfecto gracias", "ok gracias", "bien gracias",
    "excelente gracias", "de lujo gracias"
}

AFIRMACIONES = {
    "si", "sip", "yes", "claro", "ok", "okey", "okay",
    "dale", "va", "sale", "entendido", "perfecto",
    "de acuerdo", "esta bien", "andale", "simon"
}

NEGACIONES = {
    "no", "nel", "nop", "nope", "para nada",
    "negativo", "de ninguna manera", "no gracias"
}

ELOGIOS = {
    "eres buena", "que inteligente", "muy bien sara",
    "bien hecho", "excelente sara", "que lista",
    "me gustas sara", "eres util", "buen trabajo",
    "te pasas", "chida", "chido", "cool"
} 

INSULTOS_LEVES = {
    "tonto", "tonta", "mensa", "menso", "inutil",
    "no sirves", "que mala eres", "estas mal"
}

CORRECCIONES = {
    "eso esta mal", "estas mal", "te equivocaste",
    "no es correcto", "eso es incorrecto", "error",
    "no es asi", "incorrecto", "eso no es",
    "no es eso", "esta mal", "te equivocas",
    "eso no es correcto", "falso", "no es verdad",
    "corrijo", "correccion", "en realidad",
    "en realidad es", "lo correcto es",
    "deberia ser", "no es exactamente"
}

RESPUESTAS_SALUDO = [
    "¡Hola! ¿En qué puedo ayudarte?",
    "¡Buenas! Estoy lista para ayudarte.",
    "¡Hola! ¿Qué necesitas saber?",
    "¡Hey! ¿Cómo puedo ayudarte hoy?",
    "¡Hola! Dime en qué te puedo ayudar."
]

RESPUESTAS_DESPEDIDA = [
    "¡Hasta luego! Fue un gusto ayudarte.",
    "¡Nos vemos! Aquí estaré cuando me necesites.",
    "¡Cuídate! Vuelve cuando quieras.",
    "¡Adiós! Fue un placer.",
    "¡Hasta la próxima!"
]

RESPUESTAS_AGRADECIMIENTO = [
    "¡Con gusto! ¿Hay algo más en que pueda ayudarte?",
    "¡Para eso estoy! ¿Algo más?",
    "¡De nada! Es un placer ayudarte.",
    "¡No hay de qué! ¿Necesitas algo más?"
]

RESPUESTAS_AFIRMACION = [
    "Entendido. ¿En qué más te puedo ayudar?",
    "De acuerdo. ¿Hay algo más?",
    "Perfecto. Dime si necesitas algo más."
]

RESPUESTAS_NEGACION = [
    "Está bien. Aquí estaré si me necesitas.",
    "De acuerdo. Dime si cambias de opinión.",
    "Sin problema. ¿Hay algo más en que pueda ayudar?"
]

RESPUESTAS_ELOGIO = [
    "¡Gracias! Hago mi mejor esfuerzo.",
    "¡Qué amable! Seguiré aprendiendo para ayudarte mejor.",
    "¡Muchas gracias! ¿En qué más te puedo ayudar?"
]

RESPUESTAS_INSULTO = [
    "Entiendo tu frustración. ¿En qué puedo mejorar?",
    "Lo siento si no pude ayudarte bien. ¿Intentamos de nuevo?",
    "Haré mi mejor esfuerzo para mejorar. ¿Qué necesitas?"
]

RESPUESTAS_CORTA_SIN_TEMA = [
    "¿Puedes darme más detalles?",
    "No entendí bien. ¿Puedes explicarte un poco más?",
    "¿Podrías ser más específico?",
    "Necesito un poco más de información para ayudarte."
]


def detectar_entrada_social(texto):
    if not texto or not texto.strip():
        return False, ""

    texto_norm = normalizar_texto(texto)

    if texto_norm in SALUDOS or _empieza_con_saludo(texto_norm):
        _registrar("saludo", texto)
        return True, random.choice(RESPUESTAS_SALUDO)

    if texto_norm in DESPEDIDAS:
        _registrar("despedida", texto)
        return True, random.choice(RESPUESTAS_DESPEDIDA)

    if texto_norm in AGRADECIMIENTOS or _contiene_agradecimiento(texto_norm):
        _registrar("agradecimiento", texto)
        return True, random.choice(RESPUESTAS_AGRADECIMIENTO)

    if texto_norm in AFIRMACIONES:
        _registrar("afirmacion", texto)
        return True, random.choice(RESPUESTAS_AFIRMACION)

    if texto_norm in NEGACIONES:
        _registrar("negacion", texto)
        return True, random.choice(RESPUESTAS_NEGACION)

    if texto_norm in ELOGIOS or _contiene_elogio(texto_norm):
        _registrar("elogio", texto)
        return True, random.choice(RESPUESTAS_ELOGIO)

    if texto_norm in INSULTOS_LEVES or _contiene_insulto(texto_norm):
        _registrar("insulto", texto)
        return True, random.choice(RESPUESTAS_INSULTO)

    if _es_entrada_corta_sin_tema(texto_norm.split()):
        _registrar("corta", texto)
        return True, random.choice(RESPUESTAS_CORTA_SIN_TEMA)

    return False, ""


def es_correccion(texto):
    texto_norm = normalizar_texto(texto)
    if texto_norm in CORRECCIONES:
        return True
    PALABRAS_CORRECCION = ("mal", "incorrecto", "equivocaste", "error",
                           "falso", "no es", "correccion", "en realidad")
    for palabra in PALABRAS_CORRECCION:
        if palabra in texto_norm.split():
            return True
    return False


def _registrar(tipo_social, texto):
    try:
        guardar_interaccion_social(texto, tipo_social)
        logger.debug("social", f"{tipo_social} registrado: '{texto[:30]}'")
    except Exception as e:
        logger.error("social", f"Error registrando social: {e}")


def _empieza_con_saludo(texto):
    SALUDOS_INICIO = ("hola ", "hey ", "buenas ", "buenos ", "que tal ", "que onda ", "como estas ")
    for saludo in SALUDOS_INICIO:
        if texto.startswith(saludo):
            return True
    return False


def _contiene_agradecimiento(texto):
    for palabra in ("gracias", "agradezco", "thank"):
        if palabra in texto.split():
            return True
    return False


def _contiene_elogio(texto):
    for palabra in ("inteligente", "lista", "util", "chida", "buena sara"):
        if palabra in texto:
            return True
    return False


def _contiene_insulto(texto):
    for palabra in ("inutil", "tonta", "mensa", "no sirves"):
        if palabra in texto:
            return True
    return False


def _es_entrada_corta_sin_tema(palabras):
    if len(palabras) > MAX_PALABRAS_CORTAS:
        return False
    PALABRAS_VACIAS_SOLAS = {
        "este", "eso", "esa", "asi", "pues",
        "mmm", "hmm", "ah", "oh", "uh",
        "que", "como", "cuando", "donde"
    }
    return all(p in PALABRAS_VACIAS_SOLAS for p in palabras)