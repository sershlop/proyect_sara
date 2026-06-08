# 📁 file_intent.py
import os
import logger
from database import buscar_en_indice, incrementar_acceso_archivo
from utils import (
    normalizar_texto, 
    similitud, 
    empieza_con_palabras, 
    contiene_palabra_clave
)

# ── CONSTANTES ───────────────────────────────────────────────────────
UMBRAL_ARCHIVO_PATRON = 0.80
UMBRAL_ARCHIVO_SIMILITUD = 0.65
UMBRAL_ARCHIVO_CONFIRMACION = 0.75

VERBOS_APERTURA = (
    "abre", "abrir", "muestra", "mostrar", "entra", "entrar",
    "ve a", "accede", "acceder", "encuentra", "abre mi", "busca mi",
    "ir a", "ir al", "ir a la", "abre el", "abre la", "abre los"
)

PALABRAS_ARCHIVO = (
    "archivo", "carpeta", "folder", "documento", "proyecto",
    "foto", "imagen", "video", "música", "descarga", "script"
)

PALABRAS_FUERZA_CARPETA = {"carpeta", "folder", "directorio"}
PALABRAS_FUERZA_ARCHIVO = {"archivo", "fichero", "documento", "script"}
PALABRAS_FUERZA_APP     = {"app", "aplicacion", "programa", "ejecutable"}

PLATAFORMAS_WEB = {
    "google", "chrome", "firefox", "edge", "brave", "opera",
    "youtube", "spotify", "discord", "telegram", "whatsapp",
    "gemini", "chatgpt", "claude", "netflix", "twitch"
}

def _resolver_ruta_carpeta(relativa):
    """Busca la carpeta en rutas estándar y OneDrive."""
    candidatos = [
        os.path.expanduser(f"~/{relativa}"),
        os.path.expanduser(f"~/OneDrive/{relativa}"),
        os.path.expanduser(f"~/OneDrive - Personal/{relativa}"),
    ]
    for ruta in candidatos:
        if os.path.exists(ruta):
            return ruta
    return os.path.expanduser(f"~/{relativa}")  # fallback aunque no exista

CARPETAS_SISTEMA = {
    "documentos": _resolver_ruta_carpeta("Documents"),
    "documents":  _resolver_ruta_carpeta("Documents"),
    "descargas":  _resolver_ruta_carpeta("Downloads"),
    "downloads":  _resolver_ruta_carpeta("Downloads"),
    "escritorio": _resolver_ruta_carpeta("Desktop"),
    "desktop":    _resolver_ruta_carpeta("Desktop"),
    "imagenes":   _resolver_ruta_carpeta("Pictures"),
    "pictures":   _resolver_ruta_carpeta("Pictures"),
    "musica":     _resolver_ruta_carpeta("Music"),
    "music":      _resolver_ruta_carpeta("Music"),
    "videos":     _resolver_ruta_carpeta("Videos"),
    
}
# Si la petición es de creación de código, no buscar en apps sistema


APPS_SISTEMA = {
    "cmd":             "cmd.exe",
    "terminal":        "cmd.exe",
    "consola":         "cmd.exe",
    "powershell":      "powershell.exe",
    "explorador":      "explorer.exe",
    "explorador de archivos": "explorer.exe",
    "bloc de notas":   "notepad.exe",
    "notepad":         "notepad.exe",
    "calculadora":     "calc.exe",
    "calc":            "calc.exe",
    "paint":           "mspaint.exe",
    "task manager":    "taskmgr.exe",
    "administrador de tareas": "taskmgr.exe",
}

# ── FUNCIONES DE APOYO ────────────────────────────────────────────────
def _extraer_nombre_busqueda(texto_limpio):
    STOPWORDS = {
        "abre", "abrir", "muestra", "mostrar", "entra", "accede",
        "mi", "el", "la", "los", "las", "un", "una", "al", "del",
        "archivo", "carpeta", "folder", "documento", "proyecto",
        "por", "favor", "porfavor", "busca", "encuentra", "ve", "ir"
    }
    palabras = texto_limpio.split()
    return " ".join(p for p in palabras if p not in STOPWORDS and len(p) > 1)


# ── LÓGICA PRINCIPAL ─────────────────────────────────────────────────
def detectar_intencion_archivo(texto_original, texto_limpio):
    try:
        texto_sin_acentos = normalizar_texto(texto_limpio)
        tiene_verbo = empieza_con_palabras(texto_sin_acentos, VERBOS_APERTURA)
        tiene_keyword = any(contiene_palabra_clave(texto_limpio, p) for p in PALABRAS_ARCHIVO)

        # Si es petición de código, no interferir
        VERBOS_CODIGO = (
            "crea", "crear", "genera", "generar", "desarrolla", "escribe",
            "hazme", "haz", "construye", "programa"
        )
        es_peticion_codigo = any(texto_sin_acentos.startswith(v) for v in VERBOS_CODIGO)
        if es_peticion_codigo:
            return None

        if not tiene_verbo and not tiene_keyword:
            return None
        # ... resto igual

        # Extraer el nombre de búsqueda una sola vez
        nombre_busqueda = _extraer_nombre_busqueda(texto_sin_acentos)
        if not nombre_busqueda or len(nombre_busqueda) < 3:
            return None

        # Detectar intenciones explícitas del usuario primero (Evita el UnboundLocalError)
        palabras_entrada = set(texto_limpio.split())
        fuerza_carpeta = bool(palabras_entrada & PALABRAS_FUERZA_CARPETA)
        fuerza_archivo = bool(palabras_entrada & PALABRAS_FUERZA_ARCHIVO)
        fuerza_app = bool(palabras_entrada & PALABRAS_FUERZA_APP)

        # Si coincide con plataforma web conocida sin fuerza_carpeta → no es archivo
        if nombre_busqueda.lower() in PLATAFORMAS_WEB and not fuerza_carpeta and not fuerza_app:
            return None

       # Prioridad absoluta — carpetas del sistema
        for alias, ruta in CARPETAS_SISTEMA.items():
            if alias in nombre_busqueda or alias in texto_sin_acentos:
                logger.debug("file_intent", f"Carpeta sistema detectada: '{alias}' → '{ruta}'")
                return [{
                    "nombre":                alias,
                    "ruta":                  ruta,
                    "tipo":                  "carpeta",
                    "score_raw":             1.0,
                    "confianza":             1.0,
                    "requiere_confirmacion": False
                }]

        # Prioridad absoluta — apps del sistema
        for alias, ejecutable in APPS_SISTEMA.items():
            if alias in nombre_busqueda or alias in texto_sin_acentos:
                logger.debug("file_intent", f"App sistema detectada: '{alias}' → '{ejecutable}'")
                return [{
                    "nombre":                alias,
                    "ruta":                  ejecutable,
                    "tipo":                  "app",
                    "score_raw":             1.0,
                    "confianza":             1.0,
                    "requiere_confirmacion": False
                }]

        # Búsqueda en índice
        
        resultados = buscar_en_indice(nombre_busqueda, limite=5)
        if not resultados:
            return None

        candidatos = []
        for fila in resultados:
            score = similitud(nombre_busqueda, normalizar_texto(fila["nombre"]))
            if score < 0.40:
                continue

            # Bonus base si es ejecutable
            if fila["nombre"].lower().endswith(".exe"):
                score = min(1.0, score + 0.10)

            # Ajuste por tipo explícito solicitado
            tipo_fila = fila["tipo"].lower()
            ruta_lower = fila["ruta"].lower()
            es_exe = fila["nombre"].lower().endswith(".exe")
            es_prog = any(p in ruta_lower for p in ["program files", "programfiles", "appdata", "localappdata"])

            if fuerza_carpeta:
                if tipo_fila == "carpeta":
                    score = min(1.0, score + 0.20)
                else:
                    score = score * 0.30
            elif fuerza_app:
                if es_exe:
                    score = min(1.0, score + 0.20)
                elif es_prog and not es_exe:
                    score = score * 0.30
            elif fuerza_archivo:
                if tipo_fila == "carpeta":
                    score = score * 0.30
                else:
                    if es_prog and not es_exe:
                        score = score * 0.40

            # Calcular confianza final tras los ajustes de score
            confianza_final = min(1.0, score + (0.15 if tiene_verbo else 0.0))
            
            candidatos.append({
                "nombre": fila["nombre"],
                "ruta": fila["ruta"],
                "tipo": fila["tipo"],
                "score_raw": round(score, 4),
                "confianza": round(confianza_final, 4),
                "requiere_confirmacion": confianza_final < UMBRAL_ARCHIVO_CONFIRMACION
            })

        if not candidatos:
            return None

        # Ordenar por confianza descendente, priorizar .exe en empates
        candidatos.sort(
            key=lambda x: (x["confianza"], 1 if x["nombre"].lower().endswith(".exe") else 0),
            reverse=True
        )
        
        logger.debug("file_intent", f"{len(candidatos)} candidatos para '{nombre_busqueda}'")
        return candidatos

    except Exception as e:
        logger.log_excepcion("file_intent", "detectar_intencion_archivo", e)
        return None




def mejor_candidato_archivo(candidatos):
    """Retorna el candidato con mayor confianza de la lista."""
    if not candidatos:
        return None
    return candidatos[0]
