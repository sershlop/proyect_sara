# 📁 utils.py — Funciones base de SARA v2.0 (OPTIMIZADO)

import re
import unicodedata
from typing import Optional

try:
    import logger as _logger
except ImportError:
    _logger = None


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    """Log seguro con fallback a print."""
    try:
        if _logger is None:
            raise ImportError("logger.py no disponible")
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[UTILS ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


# ═══════════════════════════════════════════════════════════════════════════
# NORMALIZACIÓN DE TEXTO
# ═══════════════════════════════════════════════════════════════════════════

def normalizar_texto(texto: str) -> str:
    """
    Convierte texto a forma canónica para búsquedas y comparaciones.
    OPTIMIZADO: una sola pasada por regex, sin dependencias externas.
    
    - Elimina tildes (é → e)
    - Convierte a minúsculas
    - Elimina espacios múltiples
    - Quita signos de puntuación (excepto guiones internos)
    """
    if not texto:
        return ""
    
    try:
        # NFD: descompone caracteres acentuados (é → e + ´)
        texto_nfd = unicodedata.normalize("NFD", texto)
        # Quita los diacríticos (marcas de acentos)
        texto_sin_tildes = "".join(
            char for char in texto_nfd 
            if unicodedata.category(char) != "Mn"
        )
        
        # Minúsculas + espacios múltiples + puntuación (excepto guiones)
        texto_limpio = texto_sin_tildes.lower()
        texto_limpio = re.sub(r"[^\w\s-]", "", texto_limpio)  # Quita caracteres especiales
        texto_limpio = re.sub(r"\s+", " ", texto_limpio).strip()  # Espacios múltiples → uno
        
        return texto_limpio
    except Exception as e:
        _log_error("utils", "normalizar_texto", e)
        # Fallback: solo minúsculas y espacios
        return texto.lower().strip()


# ═══════════════════════════════════════════════════════════════════════════
# SIMILITUD DE TEXTOS (Jaro-Winkler simplificado)
# ═══════════════════════════════════════════════════════════════════════════

def similitud(texto1: str, texto2: str, umbral_minimo: float = 0.0) -> float:
    """
    Calcula similitud entre dos textos (0 = nada, 1 = idéntico).
    OPTIMIZADO: Jaro-Winkler con prefijo, sin dependencias externas.
    
    Casos especiales:
    - Textos idénticos → 1.0
    - Uno vacío → 0.0
    - Prefijo coincidente → boost de 0.1 (ej. "abre" vs "abre la app" → 0.85+)
    """
    if not texto1 or not texto2:
        return 1.0 if texto1 == texto2 else 0.0
    
    if texto1 == texto2:
        return 1.0
    
    try:
        # Jaro-Winkler simplificado
        score_jaro = _jaro(texto1, texto2)
        
        # Boost por prefijo común
        prefijo_len = len(_prefijo_comun(texto1, texto2))
        boost_prefijo = min(0.1, prefijo_len * 0.01)
        
        score_final = min(1.0, score_jaro + boost_prefijo)
        
        return score_final if score_final >= umbral_minimo else 0.0
    except Exception as e:
        _log_error("utils", "similitud", e)
        return 0.0


def _jaro(s1: str, s2: str) -> float:
    """Calcula distancia Jaro sin Winkler."""
    len1, len2 = len(s1), len(s2)
    if len1 == 0 and len2 == 0:
        return 1.0
    if len1 == 0 or len2 == 0:
        return 0.0
    
    # Rango de búsqueda de caracteres coincidentes
    match_distance = max(len1, len2) // 2 - 1
    if match_distance < 0:
        match_distance = 0
    
    s1_matches = [False] * len1
    s2_matches = [False] * len2
    
    matches = 0
    transpositions = 0
    
    # Buscar coincidencias
    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break
    
    if matches == 0:
        return 0.0
    
    # Contar transposiciones
    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1
    
    return (matches / len1 + matches / len2 + 
            (matches - transpositions / 2) / matches) / 3.0


def _prefijo_comun(s1: str, s2: str, max_len: int = 4) -> str:
    """Extrae prefijo común hasta max_len caracteres."""
    for i in range(min(len(s1), len(s2), max_len)):
        if s1[i] != s2[i]:
            return s1[:i]
    return s1[:min(len(s1), len(s2), max_len)]


# ═══════════════════════════════════════════════════════════════════════════
# DETECCIÓN DE RUTAS CRÍTICAS
# ═══════════════════════════════════════════════════════════════════════════

def apunta_a_ruta_critica(comando_o_ruta: str) -> bool:
    """
    Detecta si un comando/ruta apunta a directorio del sistema.
    OPTIMIZADO: lista de patrones críticos, sin regex costosa.
    """
    if not comando_o_ruta:
        return False
    
    texto_norm = comando_o_ruta.lower()
    
    rutas_criticas = {
        "c:\\windows", "c:\\system32", "c:\\program files",
        "c:\\programdata", "c:\\pagefile", "c:\\hiberfil",
        "/windows", "/system32", "/usr/bin", "/usr/sbin",
        "/bin", "/sbin", "/boot", "/etc", "/root",
    }
    
    for ruta in rutas_criticas:
        if ruta in texto_norm:
            return True
    
    return False


# ═══════════════════════════════════════════════════════════════════════════
# EXTRACCIÓN DE PARÁMETROS
# ═══════════════════════════════════════════════════════════════════════════

def extraer_parametro_despues_de_verbo(texto: str, verbos: list) -> str:
    """
    Extrae el texto después del primer verbo encontrado.
    
    Ejemplo:
    - texto = "abre la calculadora"
    - verbos = ["abre", "abre la"]
    - retorna = "calculadora" (o "la calculadora" si el verbo es "abre")
    """
    if not texto or not verbos:
        return ""
    
    texto_norm = normalizar_texto(texto)
    
    # Ordenar verbos por longitud descendente para que más específicos vayan primero
    verbos_ordenados = sorted(
        [normalizar_texto(v) for v in verbos],
        key=len,
        reverse=True
    )
    
    for verbo in verbos_ordenados:
        if texto_norm.startswith(verbo):
            resto = texto_norm[len(verbo):].strip()
            return resto or texto_norm
    
    return texto_norm


def limpiar_espacios(texto: str) -> str:
    """Quita espacios múltiples y extremos."""
    if not texto:
        return ""
    return re.sub(r"\s+", " ", texto).strip()


# ═══════════════════════════════════════════════════════════════════════════
# LISTADO/PARSING
# ═══════════════════════════════════════════════════════════════════════════

def dividir_en_tokens(texto: str, separador: str = " ") -> list:
    """
    Divide texto en tokens, quitando vacíos.
    """
    if not texto:
        return []
    return [t.strip() for t in texto.split(separador) if t.strip()]


def primera_linea(texto: str) -> str:
    """Extrae la primera línea."""
    if not texto:
        return ""
    return texto.split("\n")[0].strip()


# ═══════════════════════════════════════════════════════════════════════════
# DETECCIÓN DE TIPOS
# ═══════════════════════════════════════════════════════════════════════════

def es_ruta_valida(ruta: str) -> bool:
    """
    Chequeo superficial de que parece una ruta (sin verificar existencia).
    """
    if not ruta:
        return False
    
    # Contiene caracteres típicos de ruta
    tiene_separador = "\\" in ruta or "/" in ruta
    tiene_puntos = "." in ruta or ".." in ruta
    
    return tiene_separador or tiene_puntos or (len(ruta) > 3 and ":" in ruta)


def es_url(texto: str) -> bool:
    """Detección rápida de URL."""
    if not texto:
        return False
    return bool(re.match(r"https?://", texto.lower()))


# ═══════════════════════════════════════════════════════════════════════════
# FUNCIONES DE COMPATIBILIDAD
# ═══════════════════════════════════════════════════════════════════════════

def truncar(texto: str, max_chars: int = 100, sufijo: str = "...") -> str:
    """Corta texto a máximo de caracteres."""
    if not texto or len(texto) <= max_chars:
        return texto
    return texto[:max_chars - len(sufijo)] + sufijo


def destacar_diferencias(texto1: str, texto2: str) -> tuple:
    """
    Retorna (marcas_en_1, marcas_en_2) mostrando dónde difieren.
    Útil para debugging de clasificación.
    """
    marcas1 = list(texto1)
    marcas2 = list(texto2)
    
    for i, (c1, c2) in enumerate(zip(texto1, texto2)):
        if c1 != c2:
            marcas1[i] = f"[{c1}]"
            marcas2[i] = f"[{c2}]"
    
    return ("".join(marcas1), "".join(marcas2))

# ═══════════════════════════════════════════════════════════════════════════
# AÑADIDO (integración GUI): quitar verbo + relleno del inicio de una frase
# ═══════════════════════════════════════════════════════════════════════════

def quitar_verbos_y_relleno(texto: str, frases_verbales=(), relleno=frozenset()) -> str:
    """
    Quita del INICIO de la frase las frases verbales y las palabras de relleno.
    'abre la carpeta sara2' -> 'sara2'.  Si no queda nada devuelve "" (a
    diferencia de extraer_parametro_despues_de_verbo, que devuelve la frase).
    """
    tokens = normalizar_texto(texto).split()
    frases = sorted((normalizar_texto(f).split() for f in frases_verbales), key=len, reverse=True)
    relleno_n = {normalizar_texto(r) for r in relleno}
    cambio = True
    while tokens and cambio:
        cambio = False
        for f in frases:
            if f and tokens[:len(f)] == f:
                tokens = tokens[len(f):]
                cambio = True
                break
        if not cambio and tokens and tokens[0] in relleno_n:
            tokens = tokens[1:]
            cambio = True
    while len(tokens) >= 2 and tokens[-2:] == ["por", "favor"]:
        tokens = tokens[:-2]
    return " ".join(tokens)
def extraer_resto(texto: str, palabras_a_quitar: list | tuple | set) -> str:
    """Extrae el resto de la frase quitando los verbos o prefijos iniciales."""
    if not texto: return ""
    t = texto.strip()
    # Ordenar por longitud descendente para quitar prefijos largos primero
    for p in sorted(palabras_a_quitar, key=lambda x: len(str(x)), reverse=True):
        if t.lower().startswith(p.lower() + " "):
            return t[len(p):].strip()
        elif t.lower() == p.lower():
            return ""
    return t
def limpiar_ruido(texto: str) -> str:
    """Elimina palabras de relleno que no aportan a la intención."""
    if not texto: return ""
    ruido = ["por favor", "sara", "oye", "hola", "podrias", "puedes", "quiero que", "necesito que"]
    t = texto.lower()
    for r in ruido:
        t = t.replace(r, "")
    return t.strip()

if __name__ == "__main__":
    # Tests rápidos
    print("Testing normalizar_texto:")
    print(f"  'Hola MUNDO' → '{normalizar_texto('Hola MUNDO')}'")
    print(f"  'café' → '{normalizar_texto('café')}'")
    
    print("\nTesting similitud:")
    print(f"  similitud('abre', 'abre la app') = {similitud('abre', 'abre la app'):.2f}")
    print(f"  similitud('hola', 'jola') = {similitud('hola', 'jola'):.2f}")
    
    print("\nTesting extraer_parametro_despues_de_verbo:")
    resultado = extraer_parametro_despues_de_verbo("abre la calculadora", ["abre", "abre la"])
    print(f"  'abre la calculadora' con verbos ['abre', 'abre la'] → '{resultado}'")
