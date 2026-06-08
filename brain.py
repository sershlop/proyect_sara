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
    "crea", "crear", "elimina", "eliminar",          # ← ya existía
    "genera", "generar", "desarrolla", "desarrollar", # ← nuevo
    "construye", "construir", "escribe", "escribir",  # ← nuevo
    "hazme", "hazme un", "haz un", "haz una",         # ← nuevo
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
MARGEN_EMPATE  = 0.8
UMBRAL_ARCHIVO_CONFIRMACION_BRAIN = 0.75
MAPA_COMANDOS_SISTEMA = {
    "cuanta ram":        "info sistema",
    "uso de ram":        "info sistema",
    "memoria ram":       "info sistema",
    "uso cpu":           "info sistema",
    "uso del cpu":       "info sistema",
    "cuanto cpu":        "info sistema",
    "informacion sistema": "info sistema",
    "bateria":           "info sistema",
    "nivel bateria":     "info sistema",
}
def _resolver_intencion_con_destino(texto_original):
    """
    Resuelve intenciones del tipo 'abre X en Y'.
    Retorna un resultado ejecutable o None si no aplica.
    """
    if not texto_original.startswith("__DESTINO__"):
        return None

    # Parsear el marcador
    sin_prefijo = texto_original[len("__DESTINO__"):]
    if "|" not in sin_prefijo:
        return None

    destino, contenido = sin_prefijo.split("|", 1)
    destino   = destino.strip()
    contenido = contenido.strip()

    logger.debug("brain", f"Resolviendo destino='{destino}' contenido='{contenido}'")

    # Buscar la app destino en BD
    cmd_destino, score_destino = buscar_comando(destino)

    if not cmd_destino or score_destino < UMBRAL_COMANDO:
        # Intentar en file_intent (apps sistema, carpetas)
        from file_intent import detectar_intencion_archivo
        lista = detectar_intencion_archivo(destino, destino)
        if lista and lista[0]["confianza"] >= 0.75:
            arch = lista[0]
            cmd_destino = {
                "nombre":  arch["nombre"],
                "accion":  arch["ruta"],
                "tipo":    arch["tipo"],
            }
        else:
            logger.warning("brain", f"App destino '{destino}' no encontrada")
            return None

    # Construir acción compuesta: abrir app destino con URL/contenido
    accion_destino = cmd_destino.get("accion", "")
    tipo_destino   = cmd_destino.get("tipo", "app")

    # Resolver qué es el contenido (URL, búsqueda, archivo)
    url_contenido = _resolver_contenido_para_destino(contenido, destino)

    return _resultado(
        "comando_con_destino",
        f"Abriendo '{contenido}' en '{destino}'",
        comando={
            "nombre":        cmd_destino.get("nombre", destino),
            "accion":        accion_destino,
            "tipo":          tipo_destino,
            "url_contenido": url_contenido,
            "contenido_raw": contenido,
        },
        confianza=score_destino if score_destino else 0.90,
        query=texto_original
    )


def _resolver_contenido_para_destino(contenido, destino):
    """
    Dado un contenido y un destino, devuelve la URL o acción correcta.
    Ejemplos:
        contenido='google', destino='opera'    → 'https://www.google.com'
        contenido='python', destino='youtube'  → 'https://www.youtube.com/results?search_query=python'
        contenido='mis documentos', destino='explorador' → ruta de documentos
    """
    from urllib.parse import quote

    contenido_norm = normalizar_texto(contenido)

    # URLs directas conocidas
    URLS_DIRECTAS = {
        "google":      "https://www.google.com",
        "youtube":     "https://www.youtube.com",
        "facebook":    "https://www.facebook.com",
        "twitter":     "https://www.twitter.com",
        "instagram":   "https://www.instagram.com",
        "whatsapp":    "https://web.whatsapp.com",
        "gmail":       "https://mail.google.com",
        "github":      "https://www.github.com",
        "netflix":     "https://www.netflix.com",
        "spotify":     "https://open.spotify.com",
        "chatgpt":     "https://chat.openai.com",
        "claude":      "https://claude.ai",
        "gemini":      "https://gemini.google.com",
    }

    if contenido_norm in URLS_DIRECTAS:
        return URLS_DIRECTAS[contenido_norm]

    # Si el destino es youtube → búsqueda en youtube
    if "youtube" in normalizar_texto(destino):
        return f"https://www.youtube.com/results?search_query={quote(contenido)}"

    # Si el destino es un navegador → búsqueda en google
    NAVEGADORES = {"chrome", "opera", "firefox", "edge", "brave", "opera gx"}
    if any(nav in normalizar_texto(destino) for nav in NAVEGADORES):
        # Verificar si es una URL parcial
        if "." in contenido and " " not in contenido:
            return f"https://{contenido}" if not contenido.startswith("http") else contenido
        return f"https://www.google.com/search?q={quote(contenido)}"

    # Si es carpeta del sistema → ruta
    from file_intent import CARPETAS_SISTEMA
    if contenido_norm in CARPETAS_SISTEMA:
        return CARPETAS_SISTEMA[contenido_norm]

    # Fallback: búsqueda general
    return f"https://www.google.com/search?q={quote(contenido)}"
def detectar_intencion(texto_original, texto_limpio):
    # Prioridad absoluta — peticiones de creación de código
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


def buscar_respuesta(texto_limpio):
    datos = obtener_conocimientos()
    from database import obtener_correccion_por_pregunta
    correccion = obtener_correccion_por_pregunta(texto_limpio)
    if correccion:
        logger.debug("brain", f"Corrección prioritaria aplicada: '{texto_limpio[:40]}'")
        return correccion["respuesta_nueva"], 1.0, texto_limpio
    if not datos:
        return None, 0.0, None

    mejor_score     = 0.0
    mejor_respuesta = None
    mejor_pregunta  = None

    vectores        = obtener_vectores_conocimientos() if embeddings.esta_disponible() else []
    vector_consulta = embeddings.generar_vector(texto_limpio) if embeddings.esta_disponible() else None
    nucleo_consulta = _extraer_nucleo_interrogativo(texto_limpio)
    tema_consulta   = _extraer_tema_especifico(texto_limpio)

    for fila in datos:
        pregunta_bd   = normalizar_texto(fila["pregunta"])
        score_difflib = similitud(texto_limpio, pregunta_bd)
        score_bd      = score_difflib

        score_semantico = 0.0
        if vector_consulta and vectores:
            for preg, resp, vec in vectores:
                if normalizar_texto(preg) == pregunta_bd:
                    score_semantico = embeddings.similitud_coseno(vector_consulta, vec)
                    break

        nucleo_bd    = _extraer_nucleo_interrogativo(pregunta_bd)
        tema_bd      = _extraer_tema_especifico(pregunta_bd)
        penalizacion = 1.0

        if tema_consulta and tema_bd and tema_consulta != tema_bd:
            if similitud(tema_consulta, tema_bd) < 0.60:
                penalizacion = 0.20

        score_semantico_ajustado = score_semantico * penalizacion

        if embeddings.esta_disponible() and score_semantico > 0:
            score_final = (score_difflib * PESO_DIFFLIB +
                           score_bd      * PESO_BD +
                           score_semantico_ajustado * PESO_SEMANTICO)
        else:
            score_final = (score_difflib * 0.50 + score_bd * 0.50) * penalizacion

        if score_final > mejor_score:
            mejor_score     = score_final
            mejor_respuesta = fila["respuesta"]
            mejor_pregunta  = fila["pregunta"]

    if mejor_score >= UMBRAL_PREGUNTA:
        incrementar_consulta(mejor_pregunta)
        return mejor_respuesta, mejor_score, mejor_pregunta

    return None, mejor_score, None


def buscar_comando(texto_limpio):
    comandos = obtener_comandos()
    if not comandos:
        return None, 0.0

    mejor_score   = 0.0
    mejor_comando = None

    vectores        = obtener_vectores_comandos() if embeddings.esta_disponible() else []
    vector_consulta = embeddings.generar_vector(texto_limpio) if embeddings.esta_disponible() else None
    tema_consulta   = _extraer_tema_especifico(texto_limpio)

    comando_dinamico, score_dinamico = _buscar_comando_dinamico(texto_limpio, comandos)
    if comando_dinamico and score_dinamico > mejor_score:
        mejor_score   = score_dinamico
        mejor_comando = comando_dinamico
        if mejor_score >= UMBRAL_COMANDO:
            incrementar_uso_comando(mejor_comando["id"])
            return dict(mejor_comando), mejor_score

    for cmd in comandos:
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

        score_semantico = 0.0
        if vector_consulta and vectores:
            for cmd_dict, vec in vectores:
                if normalizar_texto(cmd_dict["nombre"]) == nombre:
                    score_semantico = embeddings.similitud_coseno(vector_consulta, vec)
                    break

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


def procesar(texto_original):

    # ── Intención con destino — verificar ANTES de normalizar ─────
    resultado_destino = _resolver_intencion_con_destino(texto_original)
    if resultado_destino:
        return resultado_destino

    texto_limpio = normalizar_texto(texto_original)
    if not texto_limpio:
        return _resultado("desconocido", "No entendí nada, ¿puedes repetirlo?")
    

    candidatos = []
    texto_limpio = normalizar_texto(texto_original)

    if not texto_limpio:
        return _resultado("desconocido", "No entendí nada, ¿puedes repetirlo?")

    # ── Intención con destino (abre X en Y) ───────────────────────
    resultado_destino = _resolver_intencion_con_destino(texto_original)
    if resultado_destino:
        return resultado_destino
    
    # Capa -1 — Mapeo directo de comandos sistema conocidos
    for frase, nombre_cmd in MAPA_COMANDOS_SISTEMA.items():
        if frase in texto_limpio:
            from database import obtener_comandos
            for cmd in obtener_comandos():
                if normalizar_texto(cmd["nombre"]) == nombre_cmd:
                    guardar_intencion(texto_original, texto_limpio, "comando", 0.95)
                    guardar_historial(texto_original, texto_limpio, cmd["nombre"], "comando", 0.95)
                    incrementar_uso_comando(cmd["id"])
                    return _resultado("comando", f"Ejecutando: {cmd['nombre']}",
                                    comando=dict(cmd), confianza=0.95, query=texto_limpio)
            break

    # Capa 0 — Comandos en BD
    comando_bd, score_cmd = buscar_comando(texto_limpio)
    if comando_bd and score_cmd >= UMBRAL_COMANDO:
        candidatos.append({
            "tipo":    "comando",
            "nombre":  comando_bd.get("nombre", ""),
            "score":   score_cmd,
            "payload": comando_bd
        })

    # Capa 0.5 — Índice de archivos
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

    # ── Ranking unificado ──────────────────────────────────────────────────────
    if candidatos:
        # ── Ranking unificado ──────────────────────────────────────────────────────
        if candidatos:
            logger.debug("brain", f"Total candidatos al ranking: {len(candidatos)}")
            for i, c in enumerate(candidatos):
                logger.debug("brain", f"  [{i}] tipo={c['tipo']} nombre='{c['nombre']}' score={c['score']:.2f}")
        candidatos.sort(key=lambda x: x["score"], reverse=True)
        ganador    = candidatos[0]
        _arbitrado = False

        _arbitrado = False
        if len(candidatos) == 1:
            # Un solo candidato — no necesita árbitro
            # Si confianza es alta, ejecutar directo; si es baja, pedir confirmación
            if ganador["score"] < UMBRAL_ARCHIVO_CONFIRMACION_BRAIN:
                ganador["payload"]["requiere_confirmacion"] = True
            logger.debug("brain", f"Candidato único ({ganador['score']:.2f}) → sin árbitro")

        elif len(candidatos) > 1:
            segundo  = candidatos[1]
            margen   = ganador["score"] - segundo["score"]

            es_prioridad_absoluta = (
                ganador["score"] >= 1.0 and (
                    # App o carpeta del sistema
                    (ganador["tipo"] == "archivo" and 
                    ganador["payload"].get("tipo") in ("carpeta", "app")) or
                    # Archivo con confianza perfecta encontrado en índice
                    (ganador["tipo"] == "archivo" and 
                    ganador["score"] == 1.0)
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
            guardar_intencion(texto_original, texto_limpio, "comando", ganador["score"])
            guardar_historial(texto_original, texto_limpio, cmd.get("nombre", ""), "comando", ganador["score"])
            incrementar_uso_comando(cmd["id"])
            return _resultado("comando", f"Ejecutando: {cmd['nombre']}",
                              comando=cmd, confianza=ganador["score"],
                              query=texto_limpio, arbitrado=_arbitrado)

        elif ganador["tipo"] == "archivo":
            arch = ganador["payload"]
            guardar_intencion(texto_original, texto_limpio, "archivo", ganador["score"])
            guardar_historial(texto_original, texto_limpio, arch["ruta"], "archivo", ganador["score"])
            if arch.get("requiere_confirmacion") or _arbitrado:
                return _resultado("archivo_confirmar", arch["nombre"],
                                  comando={"accion": arch["ruta"], "tipo": "app", "nombre": arch["nombre"]},
                                  confianza=ganador["score"], query=texto_limpio, arbitrado=_arbitrado)
            from database import incrementar_acceso_archivo
            incrementar_acceso_archivo(arch["ruta"])
            return _resultado("archivo", arch["ruta"],
                              comando={"accion": arch["ruta"], "tipo": "app", "nombre": arch["nombre"]},
                              confianza=ganador["score"], query=texto_limpio, arbitrado=_arbitrado)

    # Capa 1 — Búsqueda dinámica
    busqueda = searcher.analizar(texto_original)
    if busqueda.get("es_busqueda"):
        guardar_intencion(texto_original, texto_limpio, "busqueda", 0.95)
        guardar_historial(texto_original, texto_limpio, busqueda.get("mensaje", ""), "busqueda", 0.95)
        return _resultado("busqueda", busqueda.get("mensaje", ""),
                          confianza=0.95, query=texto_limpio, busqueda=busqueda)

    # Capa 2 — Detección de intención
    tipo_intencion, confianza_intencion = detectar_intencion(texto_original, texto_limpio)
    guardar_intencion(texto_original, texto_limpio, tipo_intencion, confianza_intencion)

    # Capa 3 — Buscar respuesta o comando desconocido
    if tipo_intencion == "pregunta":
        respuesta, confianza, _ = buscar_respuesta(texto_limpio)
        if respuesta:
            guardar_historial(texto_original, texto_limpio, respuesta, "pregunta", confianza)
            return _resultado("respuesta", respuesta, confianza=confianza, query=texto_limpio)
        guardar_historial(texto_original, texto_limpio, "sin_respuesta", "pregunta", 0.0)
        return _resultado("respuesta", "Aún no sé la respuesta a eso.",
                          confianza=0.0, query=texto_limpio)

    elif tipo_intencion == "comando":
        guardar_historial(texto_original, texto_limpio, "sin_comando", "comando", 0.0)
        return _resultado("comando", "No reconozco ese comando.",
                          confianza=0.0, query=texto_limpio)

    guardar_historial(texto_original, texto_limpio, "desconocido", "desconocido", 0.0)
    return _resultado("desconocido", "No entendí lo que dijiste.",
                      confianza=0.0, query=texto_limpio)

def _resultado(tipo, texto, comando=None, confianza=0.0, query="", busqueda=None, arbitrado=False):
    return {
        "tipo":      tipo,
        "texto":     texto,
        "comando":   comando,
        "confianza": round(confianza, 4),
        "query":     query,
        "busqueda":  busqueda,
        "arbitrado": arbitrado
    }