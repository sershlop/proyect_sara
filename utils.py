# 📁 utils.py
import unicodedata
import re
from difflib import SequenceMatcher


def normalizar_texto(texto):
    """
    Pipeline completo de limpieza:
    1. Minúsculas
    2. Quita acentos
    3. Quita símbolos y puntuación
    4. Colapsa espacios
    5. Strip
    """
    if not texto or not isinstance(texto, str):
        return ""
    texto = texto.lower()
    texto = unicodedata.normalize('NFD', texto)
    texto = texto.encode('ASCII', 'ignore').decode('utf-8')
    texto = re.sub(r'[^a-z0-9\s]', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto)
    return texto.strip()


def similitud(texto1, texto2):
    """Retorna similitud 0.0-1.0 entre dos textos normalizados."""
    a = normalizar_texto(texto1)
    b = normalizar_texto(texto2)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def textos_iguales(texto1, texto2):
    return normalizar_texto(texto1) == normalizar_texto(texto2)


def contiene_palabra_clave(texto, palabra_clave):
    return normalizar_texto(palabra_clave) in normalizar_texto(texto)


def empieza_con_palabras(texto, palabras):
    texto_norm = normalizar_texto(texto)
    for palabra in palabras:
        if texto_norm.startswith(normalizar_texto(palabra)):
            return True
    return False


def print_debug(info, activo=True):
    if activo:
        print(f"[DEBUG] {info}")