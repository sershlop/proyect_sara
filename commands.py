# 📁 commands.py
import webbrowser
import os
import sys
import subprocess
import logger
from database import buscar_en_indice
from utils import similitud, normalizar_texto

SISTEMA = sys.platform
EXE_EXTENSIONS = ('.exe', '.bat', '.cmd', '.msi', '.com')


def ejecutar_comando_con_destino(accion_app, tipo_app, url_contenido, nombre_app):
    import subprocess
    import time

    try:
        # ── NUEVO: múltiples URLs → abrir cada una ────────────────
        if isinstance(url_contenido, list):
            exitos = 0
            for url in url_contenido:
                try:
                    if accion_app.endswith(".exe"):
                        subprocess.Popen([accion_app, url],
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL,
                                         stdin=subprocess.DEVNULL)
                    else:
                        subprocess.Popen(["start", "", url], shell=True,
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
                    exitos += 1
                    time.sleep(0.4)  # pequeña pausa entre pestañas
                except Exception:
                    pass
            urls_str = ", ".join(url_contenido)
            logger.info("commands", f"Múltiples URLs en '{nombre_app}': {urls_str}")
            return {"exito": exitos > 0,
                    "mensaje": f"Abriendo {exitos} página(s) en {nombre_app}..."}

        # ── Caso simple — una sola URL ─────────────────────────────
        if url_contenido and accion_app.endswith(".exe"):
            try:
                subprocess.Popen([accion_app, url_contenido],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL)
                logger.info("commands",
                    f"App '{nombre_app}' abierta con contenido: {url_contenido}")
                return {"exito": True,
                        "mensaje": f"Abriendo '{url_contenido}' en {nombre_app}..."}
            except Exception:
                pass

        if url_contenido and tipo_app in ("web", "sistema"):
            subprocess.Popen(["start", "", url_contenido], shell=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return {"exito": True,
                    "mensaje": f"Abriendo '{url_contenido}' en {nombre_app}..."}

        resultado_app = ejecutar_comando({"accion": accion_app, "tipo": tipo_app, "nombre": nombre_app})
        if url_contenido:
            time.sleep(1.5)
            subprocess.Popen(["start", "", url_contenido], shell=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)

        return {"exito": True,
                "mensaje": f"Abriendo '{url_contenido}' en {nombre_app}..."}

    except Exception as e:
        logger.log_excepcion("commands", "ejecutar_comando_con_destino", e)
        return {"exito": False, "mensaje": f"Error: {e}"}
def _ejecutar_sistema(comando_str, nombre=""):
    # ── Protocolos especiales → start directo ─────
    PROTOCOLOS = ("ms-settings:", "steam://", "spotify:", "shell:")
    if any(comando_str.strip().startswith(p) for p in PROTOCOLOS):
        try:
            subprocess.Popen(f"start {comando_str.strip()}", shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("commands", f"Protocolo abierto: {comando_str}")
            return _resultado(True, f"Abriendo {nombre or comando_str}...", "sistema")
        except Exception as e:
            logger.log_excepcion("commands", comando_str, e)
            return _resultado(False, f"No se pudo abrir: {e}", "sistema")

    # ── Comandos interactivos → ventana nueva ─────
    COMANDOS_INTERACTIVOS = {
        "cmd", "powershell", "python", "node", "bash", "wsl", "ipython"
    }
    comando_base = comando_str.strip().lower().split()[0]

    if comando_base in COMANDOS_INTERACTIVOS:
        try:
            subprocess.Popen(f"start {comando_str}", shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("commands", f"Comando interactivo: {comando_str}")
            return _resultado(True, f"Abriendo {nombre or comando_str}...", "sistema")
        except Exception as e:
            logger.log_excepcion("commands", comando_str, e)
            return _resultado(False, f"No se pudo abrir: {e}", "sistema")

    # ── Comandos normales → subprocess directo ────
    try:
        resultado = subprocess.run(
            comando_str, shell=True,
            capture_output=True, text=True, timeout=10
        )
        if resultado.returncode == 0:
            salida = resultado.stdout.strip() or "Comando ejecutado correctamente."
            logger.info("commands", f"Sistema: {comando_str[:50]}")
            return _resultado(True, salida, "sistema")
        else:
            error_msg = resultado.stderr.strip() or "Error desconocido."
            logger.error("commands", f"Falló: {comando_str[:50]}", error_msg[:100])
            return _resultado(False, f"El comando falló: {error_msg}", "sistema")
    except subprocess.TimeoutExpired:
        return _resultado(False, "El comando tardó demasiado.", "sistema")
    except Exception as e:
        logger.log_excepcion("commands", comando_str, e)
        return _resultado(False, f"Error: {e}", "sistema")
    
def buscar_y_abrir_carpeta(nombre):
    try:
        nombre_limpio = _extraer_nombre_archivo(nombre)
        # Primero buscar en índice (milisegundos)
       
        resultados_indice = buscar_en_indice(nombre_limpio, limite=5)
        ruta = None
        mejor_score = 0.0
        for fila in resultados_indice:
            score = similitud(nombre_limpio, normalizar_texto(fila["nombre"]))
            if score > mejor_score and score >= 0.55:
                mejor_score = score
                ruta = fila["ruta"]
                
        # Fallback a os.walk solo si el índice no encontró nada
        if not ruta:
            ruta = _buscar_en_rutas(nombre_limpio, buscar_archivos=False)  # False para buscar carpetas
            # Guardar en índice para futuras búsquedas en milisegundos
            if ruta:
                from database import insertar_archivo_indice
                import os as _os
                ext = ""  # Las carpetas no tienen extensión
                tamanio = 0  # Las carpetas se inicializan en 0
                insertar_archivo_indice(
                    _os.path.basename(ruta), ruta,
                    "carpeta", ext, tamanio, 7, None
                )
                
        if ruta:
            resultado_apertura = _abrir_app(ruta, nombre_limpio)
            if resultado_apertura.get("exito"):
                import learning
                learning.aprender_comando(
                    nombre_limpio,
                    f"abre {nombre_limpio}, abrir {nombre_limpio}",
                    ruta,
                    "app",
                    "Encontrado automáticamente en archivero"
                )
            return resultado_apertura
        return _resultado(False, f"No encontré carpeta: '{nombre_limpio}'", "app")
    except Exception as e:
        logger.log_excepcion("commands", "buscar_y_abrir_carpeta", e)
        return _resultado(False, f"Error buscando carpeta: {e}", "app")

def _extraer_nombre_archivo(texto):
    VERBOS = {"abre", "abrir", "abre", "ejecuta", "ejecutar", "muestra", "mostrar",
               "busca", "buscar", "mi", "el", "la", "los", "las", "un", "una",
               "archivo", "carpeta", "folder"}
    palabras = texto.lower().strip().split()
    candidatos = [p for p in palabras if p not in VERBOS and len(p) > 1]
    return " ".join(candidatos) if candidatos else texto

def buscar_y_abrir_archivo(nombre):
    try:
        nombre_limpio = _extraer_nombre_archivo(nombre)
        # Primero buscar en índice (milisegundos)
        
        resultados_indice = buscar_en_indice(nombre_limpio, limite=5)
        ruta = None
        mejor_score = 0.0
        for fila in resultados_indice:
            score = similitud(nombre_limpio, normalizar_texto(fila["nombre"]))
            if score > mejor_score and score >= 0.55:
                mejor_score = score
                ruta = fila["ruta"]
                
        # Fallback a os.walk solo si el índice no encontró nada
        if not ruta:
            ruta = _buscar_en_rutas(nombre_limpio, buscar_archivos=True)
            # Guardar en índice para futuras búsquedas en milisegundos
            if ruta:
                from database import insertar_archivo_indice
                import os as _os
                ext = _os.path.splitext(ruta)[1].lower()
                try:
                    tamanio = _os.path.getsize(ruta) // 1024
                except Exception:
                    tamanio = 0
                insertar_archivo_indice(
                    _os.path.basename(ruta), ruta,
                    "archivo", ext, tamanio, 7, None
                )
                
        if not ruta:
            return _resultado(False, f"No encontré archivo: '{nombre_limpio}'", "app")

        nombre_encontrado = os.path.splitext(os.path.basename(ruta))[0].lower()
        es_exacto = nombre_encontrado == nombre_limpio.lower()

        if not es_exacto:
            return {
                "exito":               False,
                "requiere_confirmacion": True,
                "nombre_encontrado":   os.path.basename(ruta),
                "ruta":                ruta,
                "mensaje":             f"Encontré '{os.path.basename(ruta)}', ¿es esto lo que buscas?",
                "tipo":                "app"
            }

        resultado_apertura = _abrir_app(ruta, nombre_limpio)
        if resultado_apertura.get("exito"):
            from database import agregar_comando
            agregar_comando(nombre_limpio, f"abre {nombre_limpio}", ruta, "app", "Encontrado automáticamente")
        return resultado_apertura
    except Exception as e:
        logger.log_excepcion("commands", "buscar_y_abrir_archivo", e)
        return _resultado(False, f"Error buscando archivo: {e}", "app")

def _buscar_en_rutas(nombre, buscar_archivos=False):
    import os
    home = os.path.expanduser("~")

    RUTAS_PRIORITARIAS = [
        os.path.join(home, "Desktop"),
        os.path.join(home, "OneDrive", "Desktop"),
        os.path.join(home, "Documents"),
        os.path.join(home, "OneDrive", "Documents"),
        os.path.join(home, "Downloads"),
        os.path.join(home, "Pictures"),
        os.path.join(home, "imagenes"),
        os.path.join(home, "Videos"),
        os.path.join(home, "Music"),
        home,
        "C:\\Users",
        "C:\\"
    ]

    RUTAS_EXCLUIDAS = {
    "temp", "tmp", "temporary", "cache", "caches",
    "logs", "log", "__pycache__", ".git", "node_modules",
    "crash reports", "crashreports", "crashpad",
    "gpucache", "shader cache", "shadercache",
    "code cache", "codecache", "jscache",
    "service worker", "serviceworker",
    "application cache", "applicationcache",
}

    EXTENSIONES_EXCLUIDAS = {
        ".json", ".xml", ".cfg", ".log", ".db", ".pth", ".ini",
        ".dll", ".sys", ".dat", ".cache", ".lock", ".pyc"
    }

    nombre_norm = nombre.lower().strip()
    candidatos  = []

    for ruta_base in RUTAS_PRIORITARIAS:
        if not os.path.exists(ruta_base):
            continue
        try:
            for root, dirs, files in os.walk(ruta_base):
                root_lower = root.lower()
                if any(excluida in root_lower for excluida in RUTAS_EXCLUIDAS):
                    dirs.clear()
                    continue

                objetivos = files if buscar_archivos else dirs
                for obj in objetivos:
                    obj_lower = obj.lower()
                    nombre_base = os.path.splitext(obj_lower)[0]

                    # Opción 1 — coincidencia por inicio
                    if not nombre_base.startswith(nombre_norm):
                        continue

                    if buscar_archivos:
                        ext = os.path.splitext(obj)[1].lower()
                        if ext in EXTENSIONES_EXCLUIDAS:
                            continue

                    ruta_completa = os.path.join(root, obj)

                    # Opción 3 — score por tipo
                    if buscar_archivos:
                        ext = os.path.splitext(obj)[1].lower()
                        score_tipo = 3 if ext in (".exe", ".bat") else 2 if ext in (".py", ".docx", ".xlsx", ".pdf") else 1
                    else:
                        score_tipo = 3  # carpetas siempre alta prioridad

                    # Score por profundidad — rutas más cortas primero
                    score_profundidad = 10 - ruta_completa.count(os.sep)

                    candidatos.append({
                        "ruta":   ruta_completa,
                        "nombre": obj,
                        "score":  score_tipo + score_profundidad
                    })

                if ruta_base in ("C:\\Users", "C:\\"):
                    nivel = root.replace(ruta_base, "").count(os.sep)
                    if nivel >= 3:
                        dirs.clear()
        except PermissionError:
            continue

    if not candidatos:
        return None

    # Retornar el de mayor score
    candidatos.sort(key=lambda x: x["score"], reverse=True)
    mejor = candidatos[0]
    logger.info("commands", f"Encontrado: {mejor['ruta']}")
    return mejor["ruta"]




def ejecutar_comando(comando):
    # Doble verificación y limpieza al inicio por seguridad
    if not comando or not isinstance(comando, dict):
        return _resultado(False, "Comando inválido.", "error")
        
    tipo = comando.get("tipo", "").lower().strip()
    accion = comando.get("accion", "").strip()
    nombre = comando.get("nombre", "desconocido")
    
    logger.debug("commands", f"ejecutar_comando → tipo='{tipo}' accion='{accion}'")
    
    try:
        if not accion:
            return _resultado(False, f"El comando '{nombre}' no tiene acción.", tipo)

        if tipo == "web":
            return _abrir_web(accion, nombre)

        elif tipo == "app":
            # Si la ruta no existe, intentamos buscarla o ejecutarla como sistema
            if not os.path.exists(accion):
                logger.warning("commands", f"Ruta no existe, buscando: {accion}")
                
                # Intentar como comando sistema directo primero
                nombre_base = os.path.basename(accion).lower().replace(".exe", "")
                resultado_sistema = _ejecutar_sistema(nombre_base, nombre)
                if resultado_sistema and resultado_sistema.get("exito"):
                    return resultado_sistema
                    
                # Buscar en índice de archivos
                resultado_busqueda = buscar_y_abrir_archivo(nombre)
                if resultado_busqueda and resultado_busqueda.get("exito"):
                    return resultado_busqueda
                    
                # Buscar en índice de carpetas
                resultado_carpeta = buscar_y_abrir_carpeta(nombre)
                if resultado_carpeta and resultado_carpeta.get("exito"):
                    return resultado_carpeta
                    
                return _resultado(False, f"No encontré: '{nombre}'", "app")
            else:
                # Si la ruta existe, abrirla normalmente
                return _abrir_app(accion, nombre)

        elif tipo == "sistema":
            return _ejecutar_sistema(accion, nombre)

        elif tipo == "sistema_control":
            return _ejecutar_control_sistema(accion, nombre)
        elif tipo == "shell":
            # ── PRAXIS: delegar a shell.py para comandos de sistema directo ──
            try:
                import shell as _shell
                accion_shell = accion or nombre
                return _shell.ejecutar_controlado(accion_shell, contexto=nombre)
            except Exception as e:
                logger.log_excepcion("commands", "ejecutar_comando_shell", e)
                return _resultado(False, f"Error al ejecutar comando shell: {e}", "shell")

        elif tipo == "shell_info":
            # Shell de solo lectura — directo sin confirmación
            try:
                import shell as _shell
                return _shell.ejecutar_controlado(accion or nombre, contexto=nombre)
            except Exception as e:
                return _resultado(False, f"No pude ejecutar: {e}", "shell_info")
        elif tipo == "gestionar_archivo":
            # Gestión de archivos desde lenguaje natural via shell.py
            try:
                import shell as _shell
                return _shell.gestionar_archivo(accion or nombre)
            except Exception as e:
                logger.log_excepcion("commands", "gestionar_archivo", e)
                return _resultado(False, f"Error en gestión de archivo: {e}", "gestionar_archivo")
        elif tipo == "dev":
            try:
                import shell as _shell
                directorio = comando.get("directorio", ".")
                return _shell.gestionar_dev(accion or nombre, directorio)
            except Exception as e:
                logger.log_excepcion("commands", "gestionar_dev", e)
                return _resultado(False, f"Error en automatización de desarrollo: {e}", "dev")
        else:
            return _resultado(False, f"Tipo no reconocido: '{tipo}'", tipo)

    # Captura de errores inesperados como solicitaste
    except Exception as e:
        logger.log_excepcion("commands", comando.get("nombre", "?"), e)
        return _resultado(False, f"Error inesperado: {e}", "error")

def ejecutar_comando_compuesto(id_comando, nombre=""):
    from database import obtener_acciones_compuestas
    acciones = obtener_acciones_compuestas(id_comando)

    if not acciones:
        return _resultado(False, "El comando no tiene acciones guardadas.", "compuesto")

    todas_ok = True
    mensajes = []

    for accion in acciones:
        orden       = accion["orden"]
        ruta_accion = accion["accion"]
        tipo        = accion["tipo"]
        descripcion = accion["descripcion"] or ruta_accion

        logger.info("commands", f"Ejecutando acción {orden}/{len(acciones)}: {descripcion}")

        if tipo == "web":
            resultado = _abrir_web(ruta_accion, descripcion)
        elif tipo == "app":
            resultado = _abrir_app(ruta_accion, descripcion)
        elif tipo == "sistema":
            resultado = _ejecutar_sistema(ruta_accion, descripcion)
        elif tipo == "sistema_control":
            resultado = _ejecutar_control_sistema(ruta_accion, descripcion)
        else:
            resultado = _resultado(False, f"Tipo desconocido: {tipo}", tipo)

        mensajes.append(
            f"  {orden}. {descripcion} → {'✅' if resultado['exito'] else '❌'}"
        )
        if not resultado["exito"]:
            todas_ok = False

    resumen = f"Comando '{nombre}' ejecutado:\n" + "\n".join(mensajes)
    return {"exito": todas_ok, "mensaje": resumen, "tipo": "compuesto"}


def _limpiar_url_web(url):
    if not isinstance(url, str):
        return url
    url = url.strip().strip('"').strip("'")
    if url.lower().startswith(("start ", "open ")):
        partes = url.split(None, 1)
        if len(partes) > 1:
            url = partes[1].strip()
    if url.startswith("https//"):
        url = url.replace("https//", "https://", 1)
    if url.startswith("http//"):
        url = url.replace("http//", "http://", 1)
    return url


def _abrir_web(url, nombre=""):
    try:
        url = _limpiar_url_web(url)
        if not url.startswith(("http://", "https://", "steam://", "spotify:", "ms-settings:")):
            url = "https://" + url
        webbrowser.open(url)
        logger.info("commands", f"Web abierta: {url}", f"comando: {nombre}")
        return _resultado(True, f"Abriendo {nombre or url}...", "web")
    except Exception as e:
        logger.log_excepcion("commands", url, e)
        return _resultado(False, f"No se pudo abrir: {e}", "web")


def _abrir_app(ruta, nombre=""):
    try:
        ruta = ruta.strip('"').strip("'").strip()

        if ruta.lower().startswith(("start ", "open ")):
            return _abrir_web(ruta, nombre)

        if ruta.startswith(("http://", "https://", "steam://", "spotify:")):
            return _abrir_web(ruta, nombre)
        
        ruta_limpia = os.path.normpath(ruta)

        if not os.path.exists(ruta_limpia):
            logger.error("commands", f"No encontrado: {ruta_limpia}")
            return _resultado(False, f"No se encontró en: {ruta_limpia}", "app")

        if SISTEMA == "win32":
            directorio = os.path.dirname(ruta_limpia) or None
            subprocess.Popen(
                ["cmd", "/c", "start", "", ruta_limpia],
                cwd=directorio,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
            )
        
        elif SISTEMA == "darwin":
            subprocess.Popen(["open", ruta_limpia])
        elif SISTEMA.startswith("linux"):
            subprocess.Popen(["xdg-open", ruta_limpia])
        else:
            return _resultado(False, f"SO no soportado: {SISTEMA}", "app")

        logger.info("commands", f"App abierta: {nombre or ruta_limpia}")
        # Registrar acceso en índice para mejorar ranking
        try:
            from database import incrementar_acceso_archivo
            incrementar_acceso_archivo(ruta_limpia)
        except Exception:
            pass
        return _resultado(True, f"Abriendo {nombre or 'el archivo'}...", "app")

    except PermissionError:
        return _resultado(False, f"Sin permisos para abrir: {nombre}", "app")
    except Exception as e:
        logger.log_excepcion("commands", ruta, e)
        return _resultado(False, f"Error al abrir: {e}", "app")
    




def _parse_accion_funcion(accion):
    if not isinstance(accion, str):
        return accion, []

    accion = accion.strip()
    if "(" in accion and accion.endswith(")"):
        nombre = accion[:accion.index("(")].strip()
        contenido = accion[accion.index("(") + 1:-1].strip()
        if not nombre:
            return accion, []
        if not contenido:
            return nombre, []
        argumentos = [arg.strip().strip('"').strip("'")
                      for arg in contenido.split(",") if arg.strip()]
        return nombre, argumentos

    return accion, []


def _ejecutar_control_sistema(accion, nombre=""):
    MAPA_ACCIONES = {
        "multimedia_pausar":    "pausar_reproducir",
        "multimedia_siguiente": "siguiente_pista",
        "multimedia_anterior":  "pista_anterior",
        "volumen_subir":        "subir_volumen",
        "volumen_bajar":        "bajar_volumen",
        "volumen_silenciar":    "silenciar_volumen",
        "bateria":              "info_sistema",
        "cpu":                  "info_sistema",
        "ram":                  "info_sistema",
        "brillo_subir":         "subir_brillo",
        "brillo_bajar":         "bajar_brillo",
    }
    try:
        import sistema
        nombre_funcion, args = _parse_accion_funcion(accion)
        # Traducir si es nombre legacy
        nombre_funcion = MAPA_ACCIONES.get(nombre_funcion, nombre_funcion)
        return sistema.ejecutar_funcion_sistema(nombre_funcion, *args)
    except ImportError:
        return _resultado(False, "Módulo sistema no disponible.", "sistema_control")
    except Exception as e:
        logger.log_excepcion("commands", accion, e)
        return _resultado(False, f"Error en control: {e}", "sistema_control")


def formatear_comando(cmd):
    try:
        return {
            "id":             cmd["id"],
            "nombre":         cmd["nombre"],
            "palabras_clave": cmd["palabras_clave"],
            "accion":         cmd["accion"],
            "tipo":           cmd["tipo"],
            "descripcion":    cmd["descripcion"],
            "prioridad":      cmd["prioridad"],
            "activo":         cmd["activo"],
            "veces_usado":    cmd["veces_usado"]
        }
    except Exception as e:
        logger.error("commands", "Error formateando comando", str(e))
        return {}


def _resultado(exito, mensaje, tipo):
    return {"exito": exito, "mensaje": mensaje, "tipo": tipo}