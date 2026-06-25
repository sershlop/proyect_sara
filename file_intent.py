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

# ── UMBRALES ─────────────────────────────────────────────────────────
UMBRAL_ARCHIVO_PATRON        = 0.80
UMBRAL_ARCHIVO_SIMILITUD     = 0.65
UMBRAL_ARCHIVO_CONFIRMACION  = 0.75

# ── VERBOS Y PALABRAS CLAVE ───────────────────────────────────────────
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

VERBOS_CODIGO = (
    "genera", "generar", "desarrolla", "escribe",
    "hazme", "haz", "construye", "programa",
    "crea un script", "crea un programa", "crear un script",
    "crea un codigo", "crear un codigo",
)
# NOTA: "crea" genérico se retiró de aquí porque intent_router.py lo
# captura primero como CAT_GESTIONAR_ARCHIVO cuando va acompañado de
# "carpeta" o "archivo". Solo llega a file_intent si el router devuelve
# CAT_DESCONOCIDA o CAT_ABRIR.

# ── CARPETAS Y APPS DEL SISTEMA ───────────────────────────────────────

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
    return os.path.expanduser(f"~/{relativa}")


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

APPS_SISTEMA = {
    "cmd":                       "cmd.exe",
    "terminal":                  "cmd.exe",
    "consola":                   "cmd.exe",
    "powershell":                "powershell.exe",
    "explorador":                "explorer.exe",
    "explorador de archivos":    "explorer.exe",
    "bloc de notas":             "notepad.exe",
    "notepad":                   "notepad.exe",
    "calculadora":               "calc.exe",
    "calc":                      "calc.exe",
    "paint":                     "mspaint.exe",
    "task manager":              "taskmgr.exe",
    "administrador de tareas":   "taskmgr.exe",
}


# ══════════════════════════════════════════════════════════════════════════════
#  MANEJO DEL MARCADOR __CARPETA_CTX__
# ══════════════════════════════════════════════════════════════════════════════

def _extraer_carpeta_ctx(texto_original):
    """
    Si el texto viene con el marcador __CARPETA_CTX__nombre|texto_real,
    extrae la carpeta contexto y devuelve (carpeta_ctx, texto_limpio).

    Si no hay marcador, devuelve (None, texto_original).
    """
    PREFIJO = "__CARPETA_CTX__"
    if not texto_original.startswith(PREFIJO):
        return None, texto_original

    try:
        sin_prefijo    = texto_original[len(PREFIJO):]
        carpeta_ctx, texto_real = sin_prefijo.split("|", 1)
        return carpeta_ctx.strip(), texto_real.strip()
    except ValueError:
        return None, texto_original


def _filtrar_por_carpeta_padre(candidatos, carpeta_ctx):
    """
    Dado un contexto de carpeta padre (ej: 'sara.2'), filtra y reordena
    los candidatos priorizando aquellos cuya ruta contiene esa carpeta.

    Estrategia:
    1. Candidatos cuya ruta contiene carpeta_ctx → boost de score +0.30
    2. Si ninguno contiene carpeta_ctx → devuelve lista original sin modificar
       (degradación elegante: SARA sigue funcionando aunque no encuentre el ctx)
    3. Reordena por confianza descendente tras el boost.

    Este filtro es universal: funciona para cualquier nombre de carpeta,
    no solo para sara.2/sara_gui/sara_github.
    """
    if not carpeta_ctx or not candidatos:
        return candidatos

    carpeta_norm = normalizar_texto(carpeta_ctx)
    boosteados   = []

    for c in candidatos:
        ruta_norm = normalizar_texto(c.get("ruta", ""))
        # Comprobar si la ruta contiene el nombre de la carpeta como segmento
        if carpeta_norm in ruta_norm:
            c_nuevo = dict(c)
            c_nuevo["confianza"]  = min(1.0, c["confianza"] + 0.30)
            c_nuevo["score_raw"]  = min(1.0, c["score_raw"] + 0.30)
            c_nuevo["ctx_match"]  = True   # señal para logging
            boosteados.append(c_nuevo)
        else:
            boosteados.append(dict(c))

    # Si ninguno hizo match con el contexto, degradar elegantemente
    tiene_match = any(c.get("ctx_match") for c in boosteados)
    if not tiene_match:
        logger.debug("file_intent",
                     f"Contexto de carpeta '{carpeta_ctx}' no encontrado en índice — "
                     f"usando ranking original sin filtro")
        return candidatos

    # Reordenar por confianza tras el boost
    boosteados.sort(
        key=lambda x: (x["confianza"], 1 if x["nombre"].lower().endswith(".exe") else 0),
        reverse=True
    )

    logger.debug("file_intent",
                 f"Filtro de carpeta ctx '{carpeta_ctx}' aplicado — "
                 f"mejor candidato: '{boosteados[0]['nombre']}' en '{boosteados[0]['ruta']}'")
    return boosteados


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS INTERNOS
# ══════════════════════════════════════════════════════════════════════════════

# En file_intent.py, reemplaza _extraer_nombre_busqueda():

def _extraer_nombre_busqueda(texto_limpio):
    STOPWORDS = {
        "abre", "abrir", "muestra", "mostrar", "entra", "accede",
        "mi", "el", "la", "los", "las", "un", "una", "al", "del",
        "archivo", "carpeta", "folder", "documento", "proyecto",
        "por", "favor", "porfavor", "busca", "encuentra", "ve", "ir"
    }
    palabras = texto_limpio.split()
    resultado = []
    for p in palabras:
        if p in STOPWORDS and len(p) > 1:
            continue
        # Preservar palabras con punto (extensiones: brain.py, config.py, etc.)
        # aunque sean cortas
        if "." in p:
            resultado.append(p)
        elif len(p) > 1:
            resultado.append(p)
    return " ".join(resultado)


# ══════════════════════════════════════════════════════════════════════════════
#  LÓGICA PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def detectar_intencion_archivo(texto_original, texto_limpio):
    """
    Detecta si la entrada busca abrir un archivo, carpeta o app.

    Soporta el marcador __CARPETA_CTX__nombre|texto para filtrar
    candidatos por carpeta padre cuando el usuario especificó contexto.

    Retorna lista de candidatos ordenados por confianza, o None.
    """
    try:
        # ── PRAXIS: verificar con intent_router antes de continuar ────────────
        # Si el texto ya fue clasificado como REPRODUCIR, no interferir con carpetas.
        # Esto impide que "pon música" active la prioridad absoluta de CARPETAS_SISTEMA.
        try:
            import intent_router as _ir
            _cls = _ir.clasificar(texto_limpio)
            if _cls.get("categoria") == _ir.CAT_REPRODUCIR:
                logger.debug("file_intent",
                             f"Router detectó REPRODUCIR — omitiendo detección de carpeta para: '{texto_limpio[:40]}'")
                return None
        except Exception:
            pass
        # ── Fin guardia PRAXIS ─────────────────────────────────────────────────
        # ── Extraer contexto de carpeta si viene inyectado por splitter ───────
        carpeta_ctx, texto_limpio_real = _extraer_carpeta_ctx(texto_limpio)
        if carpeta_ctx:
            logger.debug("file_intent",
                         f"Contexto de carpeta recibido: '{carpeta_ctx}'",
                         f"texto real: '{texto_limpio_real}'")
            texto_limpio = texto_limpio_real

        texto_sin_acentos = normalizar_texto(texto_limpio)
        tiene_verbo       = empieza_con_palabras(texto_sin_acentos, VERBOS_APERTURA)
        tiene_keyword     = any(contiene_palabra_clave(texto_limpio, p) for p in PALABRAS_ARCHIVO)

        # Peticiones de código → no interferir
        es_peticion_codigo = any(texto_sin_acentos.startswith(v) for v in VERBOS_CODIGO)
        if es_peticion_codigo:
            return None

        if not tiene_verbo and not tiene_keyword:
            return None

        nombre_busqueda = _extraer_nombre_busqueda(texto_sin_acentos)
        if not nombre_busqueda or len(nombre_busqueda) < 2:
            return None

        # Intenciones explícitas del usuario
        palabras_entrada = set(texto_limpio.split())
        fuerza_carpeta   = bool(palabras_entrada & PALABRAS_FUERZA_CARPETA)
        fuerza_archivo   = bool(palabras_entrada & PALABRAS_FUERZA_ARCHIVO)
        fuerza_app       = bool(palabras_entrada & PALABRAS_FUERZA_APP)

        # Plataforma web sin fuerza → no es archivo
        if nombre_busqueda.lower() in PLATAFORMAS_WEB and not fuerza_carpeta and not fuerza_app:
            return None

        # ── Prioridad absoluta — carpetas del sistema ─────────────────────────
        for alias, ruta in CARPETAS_SISTEMA.items():
            if alias in nombre_busqueda or alias in texto_sin_acentos:
                logger.debug("file_intent",
                             f"Carpeta sistema: '{alias}' → '{ruta}'")
                return [{
                    "nombre":                alias,
                    "ruta":                  ruta,
                    "tipo":                  "carpeta",
                    "score_raw":             1.0,
                    "confianza":             1.0,
                    "requiere_confirmacion": False
                }]

        # ── Prioridad absoluta — apps del sistema ─────────────────────────────
        for alias, ejecutable in APPS_SISTEMA.items():
            if alias in nombre_busqueda or alias in texto_sin_acentos:
                logger.debug("file_intent",
                             f"App sistema: '{alias}' → '{ejecutable}'")
                return [{
                    "nombre":                alias,
                    "ruta":                  ejecutable,
                    "tipo":                  "app",
                    "score_raw":             1.0,
                    "confianza":             1.0,
                    "requiere_confirmacion": False
                }]

        # ── Búsqueda en índice ────────────────────────────────────────────────
        resultados = buscar_en_indice(nombre_busqueda, limite=10)
        if not resultados:
            return None

        candidatos = []
        for fila in resultados:
            score     = similitud(nombre_busqueda, normalizar_texto(fila["nombre"]))
            if score < 0.40:
                continue

            tipo_fila  = fila["tipo"].lower()
            ruta_lower = fila["ruta"].lower()
            es_exe     = fila["nombre"].lower().endswith(".exe")
            es_prog    = any(p in ruta_lower for p in [
                "program files", "programfiles", "appdata", "localappdata"
            ])

            # Bonus por ejecutable
            if es_exe:
                score = min(1.0, score + 0.10)

            # Ajuste por tipo explícito solicitado
            if fuerza_carpeta:
                score = min(1.0, score + 0.20) if tipo_fila == "carpeta" else score * 0.30
            elif fuerza_app:
                if es_exe:
                    score = min(1.0, score + 0.20)
                elif es_prog and not es_exe:
                    score = score * 0.30
            elif fuerza_archivo:
                if tipo_fila == "carpeta":
                    score = score * 0.30
                elif es_prog and not es_exe:
                    score = score * 0.40

            confianza_final = min(1.0, score + (0.15 if tiene_verbo else 0.0))

            candidatos.append({
                "nombre":                fila["nombre"],
                "ruta":                  fila["ruta"],
                "tipo":                  fila["tipo"],
                "score_raw":             round(score, 4),
                "confianza":             round(confianza_final, 4),
                "requiere_confirmacion": confianza_final < UMBRAL_ARCHIVO_CONFIRMACION,
                "ctx_match":             False,
            })

        if not candidatos:
            return None

        # Ordenar inicial por confianza
        candidatos.sort(
            key=lambda x: (x["confianza"], 1 if x["nombre"].lower().endswith(".exe") else 0),
            reverse=True
        )

        # ── Aplicar filtro de carpeta padre si hay contexto ───────────────────
        if carpeta_ctx:
            candidatos = _filtrar_por_carpeta_padre(candidatos, carpeta_ctx)

        logger.debug("file_intent",
                     f"{len(candidatos)} candidatos para '{nombre_busqueda}'",
                     f"mejor: '{candidatos[0]['nombre']}' ({candidatos[0]['confianza']:.2f})")
        return candidatos

    except Exception as e:
        logger.log_excepcion("file_intent", "detectar_intencion_archivo", e)
        return None


def mejor_candidato_archivo(candidatos):
    """Retorna el candidato con mayor confianza de la lista."""
    if not candidatos:
        return None
    return candidatos[0]
