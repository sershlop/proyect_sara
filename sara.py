# 📁 sara.py — v0.3.0 con GUI WebSocket
# Cambios respecto al original marcados con # ← GUI
import sys
import random
import time
from utils import normalizar_texto
import database
import brain
import io_manager
import commands
import learning
import external_service
import logger
import embeddings
import context
import splitter
import social
import searcher
import validator
import voice
import file_watcher
# ── PRAXIS: nuevos módulos v0.4.0 ────────────────────────────
# ── PRAXIS: nuevos módulos v0.4.0 ────────────────────────────
try:
    import shell
    SHELL_DISPONIBLE = True
except ImportError as _e:
    shell = None
    SHELL_DISPONIBLE = False
    print(f"[SARA] shell.py no disponible: {_e}")

try:
    import intent_router
    INTENT_ROUTER_DISPONIBLE = True
except ImportError as _e:
    intent_router = None
    INTENT_ROUTER_DISPONIBLE = False
    print(f"[SARA] intent_router.py no disponible: {_e}")

try:
    import perceptor
    PERCEPTOR_DISPONIBLE = True
except ImportError as _e:
    perceptor = None
    PERCEPTOR_DISPONIBLE = False
    print(f"[SARA] perceptor.py no disponible: {_e}")
try:
    import shell_learner
    SHELL_LEARNER_DISPONIBLE = True
except ImportError as _e:
    shell_learner = None
    SHELL_LEARNER_DISPONIBLE = False
    print(f"[SARA] shell_learner.py no disponible: {_e}")

try:
    import productivity
    PRODUCTIVITY_DISPONIBLE = True
except ImportError as _e:
    productivity = None
    PRODUCTIVITY_DISPONIBLE = False
    print(f"[SARA] productivity.py no disponible: {_e}")

except ImportError as _e:
    shell_learner = None
    SHELL_LEARNER_DISPONIBLE = False
    logger.warning("sara", f"shell_learner.py no disponible: {_e}") \
        if 'logger' in dir() else print(f"[SARA] shell_learner.py no disponible: {_e}")

# PRAXIS_DISPONIBLE se mantiene por compatibilidad — ahora refleja con
# precisión si los TRES están disponibles, sin ocultar a los que sí cargaron.
PRAXIS_DISPONIBLE = SHELL_DISPONIBLE and INTENT_ROUTER_DISPONIBLE and PERCEPTOR_DISPONIBLE
# sentinel se importa más abajo en inicializar() para no bloquear el arranque
import sistema
import threading
from database import actualizar_resultado_intencion, indice_vacio
from urllib.parse import quote
from config import (
    USAR_QWEN, VERSION, MOSTRAR_CONFIANZA,
    BUSQUEDA_EXTERNA_ACTIVA,
    USAR_GEMINI_BACKUP, MODO_VOZ
)

# ── GUI: import servidor (opcional — no falla si no está) ────────────────────  ← GUI
try:
    import server as _server
    GUI_DISPONIBLE = True
except ImportError:
    GUI_DISPONIBLE = False

# ── Constantes watchdog ───────────────────────────────────────────────────────
TIMEOUT_PROCESAMIENTO  = 15
TIMEOUT_AVISO_LENTITUD = 5

_INTENCION_CACHE   = {}
CACHE_TTL_SEGUNDOS = 30

_ultima_interaccion = {
    "pregunta":         None,
    "respuesta":        None,
    "tipo":             None,
    "confianza":        0.0,
    "entrada_original": None
}
_modo_voz_activo = False


# ══════════════════════════════════════════════════════════════════════════════
#  GUI: función que el frontend llama para enviar comandos                      ← GUI
# ══════════════════════════════════════════════════════════════════════════════

def procesar_entrada_externa(texto: str):
    """
    Punto de entrada para comandos llegados desde el frontend WebSocket.
    Emula la misma entrada que escribiría el usuario en terminal.
    Se ejecuta en hilo separado — no bloquea el servidor WS.
    """
    if not texto or not texto.strip():
        return
    # Emitir eco al frontend para que aparezca como mensaje del usuario
    _emitir("mensaje_usuario", texto=texto)
    # Procesar igual que la terminal
    _procesar_texto(texto)


def _emitir(tipo: str, **kwargs):
    """Wrapper seguro para emitir eventos GUI. Nunca lanza excepciones."""       # ← GUI
    if not GUI_DISPONIBLE:
        return
    try:
        _server.emitir(tipo, **kwargs)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  INICIALIZACIÓN
# ══════════════════════════════════════════════════════════════════════════════

def inicializar():
    try:
        database.crear_tablas()
        database.migrar_bd()
        logger.debug("sara", "Base de datos inicializada.")

        comandos_sistema = sistema.registrar_comandos_sistema()
        for cmd in comandos_sistema:
            from database import agregar_comando_si_no_existe
            agregar_comando_si_no_existe(cmd)

        modelo_ok = embeddings.cargar_modelo()
        if modelo_ok:
            logger.info("sara", "Motor semántico activo.")
        else:
            logger.warning("sara", "Motor semántico no disponible.")

        if USAR_GEMINI_BACKUP:
            gemini_ok = external_service.inicializar_gemini()
            if gemini_ok:
                logger.info("sara", "Respaldo con Gemini activado.")
            else:
                logger.warning("sara", "Gemini no disponible.")

        if USAR_QWEN:
            qwen_ok = external_service.inicializar_qwen()
            if qwen_ok:
                logger.info("sara", "Qwen local activado.")
            else:
                logger.warning("sara", "Qwen no disponible — usando Groq/Gemini.")

        if BUSQUEDA_EXTERNA_ACTIVA:
            if not external_service.verificar_conexion():
                logger.warning("sara", "Sin conexión a internet.")

        if MODO_VOZ:
            voz_ok = voice.inicializar()
            if voz_ok:
                io_manager.activar_modo_voz(voice)
                logger.info("sara", "Modo voz activado al arranque.")
            else:
                logger.warning("sara", "No se pudo inicializar el modo voz.")

        # ── GUI: arrancar servidor WebSocket ─────────────────────────────────  ← GUI
        if GUI_DISPONIBLE:
            from config import GUI_PORT  # añadir GUI_PORT = 8765 en config.py
            ok = _server.iniciar_servidor(procesar_entrada_externa, puerto=GUI_PORT)
            if ok:
                logger.info("sara", f"GUI WebSocket activo en ws://127.0.0.1:{GUI_PORT}/ws")
            else:
                logger.warning("sara", "GUI WebSocket no disponible — solo terminal.")

        logger.log_inicio()
        logger.info("sara", f"SARA v{VERSION} iniciada correctamente.")
        io_manager.mostrar_bienvenida()

        if indice_vacio():
            print("\nSARA: No tengo indexadas tus carpetas y archivos.")
            print("  Escanear ahora permite encontrar archivos en milisegundos.")
            print("  ⚠️  Puede tardar unos minutos según cuántos archivos tengas.")
            from io_manager import preguntar_si_no
            if preguntar_si_no("¿Quieres que escanee tus carpetas importantes ahora?"):
                def _progreso(total, ruta_actual):
                    print(f"\r  Indexando... {total} elementos | {ruta_actual[-50:]}", end="")
                def _fin(total):
                    print(f"\n  ✅ Listo. {total} elementos indexados.")
                file_watcher.escanear_en_hilo(_progreso, _fin)
            else:
                logger.info("sara", "Usuario omitió escaneo inicial.")

        file_watcher.iniciar_watchdog()
        # ── Shell Learner: verificar tabla y reportar estado ──────────────────
        try:
            database.migrar_bd()  # asegura que intenciones_shell_aprendidas existe
            from shell_learner import estado_aprendizaje as _sl_estado
            _stats = _sl_estado()
            if _stats.get("total_aprendido", 0) > 0:
                logger.info("sara",
                            f"Shell Learner activo — {_stats['total_aprendido']} intenciones "
                            f"aprendidas ({_stats['con_vector']} vectorizadas)")
            else:
                logger.info("sara", "Shell Learner activo — iniciando aprendizaje desde cero")
        except Exception as _e:
            logger.warning("sara", "Shell Learner no disponible", str(_e))
        # ── PRAXIS: arrancar sentinel en hilo daemon ──────────────────────────
        try:
            import sentinel as _sentinel
            _sentinel.iniciar()
            logger.info("sara", "Sentinel PRAXIS activo — vigilancia proactiva iniciada.")
        except ImportError:
            logger.warning("sara", "sentinel.py no encontrado — vigilancia proactiva no disponible.")
        except Exception as _e:
            logger.warning("sara", "Sentinel no pudo iniciar.", str(_e))
        # ── Fin PRAXIS ────────────────────────────────────────────────────────
        return True

    except Exception as e:
        logger.log_excepcion("sara", "inicializar", e)
        print(f"[SARA CRÍTICO] Error al inicializar: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  CACHÉ DE INTENCIONES
# ══════════════════════════════════════════════════════════════════════════════

def _obtener_cache_intencion(texto_limpio):
    item = _INTENCION_CACHE.get(texto_limpio)
    if item:
        if time.time() - item["ts"] <= CACHE_TTL_SEGUNDOS:
            return item["resultado"]
        _INTENCION_CACHE.pop(texto_limpio, None)
    try:
        resultado = database.obtener_cache_intencion(texto_limpio)
        if resultado is not None:
            _INTENCION_CACHE[texto_limpio] = {"ts": time.time(), "resultado": resultado}
            return resultado
    except Exception:
        pass
    return None


def _guardar_cache_intencion(texto_limpio, resultado):
    _INTENCION_CACHE[texto_limpio] = {"ts": time.time(), "resultado": resultado}
    try:
        database.guardar_cache_intencion(texto_limpio, resultado, ttl_segundos=CACHE_TTL_SEGUNDOS)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  FLUJOS DE AGENTES Y COMANDOS
# ══════════════════════════════════════════════════════════════════════════════

def _consultar_gemini_para_pregunta(entrada_original):
    if not USAR_GEMINI_BACKUP or not external_service.gemini_disponible():
        return None
    io_manager.mostrar_respuesta("Consultando con Gemini...")
    respuesta_gemini = external_service.obtener_respuesta_gemini(entrada_original)
    if not respuesta_gemini:
        logger.warning("sara", "Gemini no pudo responder (cuota agotada o error)")
        return None
    if io_manager.preguntar_si_no("¿Quieres que guarde esta respuesta?"):
        learning.aprender_pregunta(entrada_original, respuesta_gemini)
    return respuesta_gemini


def _flujo_opcion_agentes(entrada_original):
    io_manager.mostrar_respuesta("Consultando agentes externos...")
    _emitir("thinking", fase="agentes")                                         # ← GUI

    resultado_agentes = external_service.resolver_con_agentes(entrada_original)
    _emitir("thinking_stop")                                                    # ← GUI

    if not resultado_agentes.get("exito"):
        return "Los agentes no pudieron generar el comando."

    tipo_resultado  = resultado_agentes.get("tipo")
    resultado_final = resultado_agentes.get("resultado")

    if tipo_resultado == "codigo":
        if isinstance(resultado_final, str):
            try:
                import json, re
                limpio = re.sub(r'```json|```', '', resultado_final).strip()
                resultado_final = json.loads(limpio)
            except Exception:
                pass
        if isinstance(resultado_final, dict):
            nombre = resultado_final.get("nombre_archivo", "script.py")
            io_manager.mostrar_respuesta(f"Script creado: scripts/{nombre}")
            return f"He creado el archivo '{nombre}' con el código solicitado."
        return "Script creado correctamente."

    if tipo_resultado in ("comando", "accion") and isinstance(resultado_final, dict):
        if not _modo_voz_activo:
            print(f"\n  Agentes proponen:")
            print(f"  Nombre:  {resultado_final.get('nombre', '?')}")
            print(f"  Acción:  {resultado_final.get('accion', '?')}")
            print(f"  Tipo:    {resultado_final.get('tipo', '?')}")
            guardar = io_manager.preguntar_si_no("¿Quieres guardar este comando?")
        else:
            guardar = True

        if guardar:
            palabras_clave = resultado_final.get("palabras_clave", entrada_original)
            if isinstance(palabras_clave, list):
                palabras_clave = ", ".join(palabras_clave)
            resultado_guardado = learning.aprender_comando(
                resultado_final.get("nombre", entrada_original),
                palabras_clave,
                resultado_final.get("accion", ""),
                resultado_final.get("tipo", "sistema"),
                resultado_final.get("descripcion", "")
            )
            if resultado_guardado.get("exito"):
                return f"¡Listo! Guardé el comando '{resultado_final.get('nombre')}'."
            return f"No pude guardar: {resultado_guardado.get('mensaje', '')}"
        return "De acuerdo, no lo guardé."

    if isinstance(resultado_final, str):
        return resultado_final

    return "Los agentes no pudieron generar el comando."


def _flujo_menu_comando_desconocido(entrada_original, texto_mostrar):
    io_manager.mostrar_respuesta(f"No reconozco ese comando: '{texto_mostrar}'")
    logger.log_intencion_desconocida(entrada_original)

    if _modo_voz_activo:
        logger.info("sara", "Modo voz: derivando a agentes automáticamente")
        return _flujo_opcion_agentes(entrada_original)

    print("\nSARA: ¿Qué quieres hacer?")
    print("  1. Enseñarme cómo ejecutar este comando")
    print("  2. Pedirle a los agentes que generen el comando")
    print("  3. Ignorar")

    try:
        opcion = input("\n  Selecciona una opción: ").strip()
    except KeyboardInterrupt:
        return "De acuerdo."

    if opcion == "1":
        nombre = input("  → Nombre: ").strip()
        if not nombre:
            return "Nombre vacío. Registro cancelado."

        print("\nSARA: ¿Este comando ejecutará más de una acción?")
        print("  1. No — acción simple")
        print("  2. Sí — comando compuesto")
        try:
            tipo_cmd = input("\n  Elige (1/2): ").strip()
        except KeyboardInterrupt:
            return "Registro cancelado."

        if tipo_cmd == "2":
            palabras_clave = input("  → Palabras clave: ").strip()
            descripcion    = input("  → Descripción general (opcional): ").strip()
            acciones       = io_manager.solicitar_acciones_multiples()
            if acciones:
                resultado_cmd = learning.aprender_comando_compuesto(
                    nombre, palabras_clave, acciones, descripcion
                )
                exito = resultado_cmd.get("exito", False)
                if exito:
                    return f"¡Listo! '{nombre}' ejecutará {len(acciones)} acciones."
                elif resultado_cmd.get("accion") == "duplicada":
                    return resultado_cmd.get("mensaje", "")
                else:
                    return f"No pude guardar. {resultado_cmd.get('mensaje', '')}"
            return "Registro cancelado."
        else:
            datos = io_manager.solicitar_datos_comando()
            if datos:
                resultado_cmd = learning.aprender_comando(
                    nombre,
                    datos.get("palabras_clave", ""),
                    datos.get("accion", ""),
                    datos.get("tipo", ""),
                    datos.get("descripcion", "")
                )
                exito = resultado_cmd.get("exito", False)
                if exito:
                    logger.info("sara", f"Comando aprendido: '{nombre}'")
                    return f"¡Listo! Aprendí el comando '{nombre}'."
                elif resultado_cmd.get("accion") == "duplicada":
                    return resultado_cmd.get("mensaje", "")
                else:
                    return f"No pude guardar. {resultado_cmd.get('mensaje', '')}"
            return "Registro cancelado."

    elif opcion == "2":
        return _flujo_opcion_agentes(entrada_original)

    else:
        logger.info("sara", f"Usuario ignoró comando: '{entrada_original[:50]}'")
        return "De acuerdo, lo omito por ahora."


# ══════════════════════════════════════════════════════════════════════════════
#  MANEJAR RESULTADO
# ══════════════════════════════════════════════════════════════════════════════

def _manejar_resultado(resultado, entrada_original, entrada_usuario=None):
    texto_mostrar = entrada_usuario or entrada_original
    tipo          = resultado.get("tipo")
    texto         = resultado.get("texto", "")
    comando       = resultado.get("comando")
    confianza     = resultado.get("confianza", 0.0)
    query         = resultado.get("query", "")

    # ── RESPUESTA ─────────────────────────────────────────────────────────────
    if tipo == "respuesta":
        if confianza >= brain.UMBRAL_PREGUNTA:
            logger.log_pregunta(entrada_original, respuesta=texto, correcta=True)
            try:
                actualizar_resultado_intencion(query, "correcto")
            except Exception:
                pass
            return texto

        io_manager.mostrar_respuesta(f"No tengo respuesta para: '{texto_mostrar}'")
        logger.log_pregunta(entrada_original, respuesta=None, correcta=False)
        try:
            actualizar_resultado_intencion(query, "sin_respuesta")
        except Exception:
            pass

        print("\nSARA: ¿Qué quieres hacer?")
        print("  1. Consultar con agentes externos (Groq + Gemini)")
        print("  2. Enseñarme tú la respuesta")
        print("  3. Ignorar")

        try:
            opcion = input("\n  Selecciona una opción: ").strip()
        except KeyboardInterrupt:
            return "De acuerdo, lo dejamos para después."

        if opcion == "1":
            io_manager.mostrar_respuesta("Consultando con agentes externos...")
            _emitir("thinking", fase="agentes")                                 # ← GUI
            resultado_agentes = external_service.resolver_con_agentes(entrada_original)
            _emitir("thinking_stop")                                            # ← GUI

            if resultado_agentes.get("exito"):
                tipo_resultado  = resultado_agentes.get("tipo")
                resultado_final = resultado_agentes.get("resultado")
                fuente          = resultado_agentes.get("fuente")

                if tipo_resultado == "codigo" and isinstance(resultado_final, dict):
                    nombre = resultado_final.get("nombre_archivo", "script.py")
                    io_manager.mostrar_respuesta(f"Script creado: scripts/{nombre}")
                    return f"He creado el archivo '{nombre}' con el código solicitado."

                if isinstance(resultado_final, str):
                    guardar = io_manager.preguntar_si_no(
                        "¿Quieres que guarde esta respuesta para la próxima vez?"
                    )
                    if guardar:
                        learning.aprender_pregunta(entrada_original, resultado_final)
                        logger.info("sara", f"Respuesta de {fuente} guardada en BD.")
                    return resultado_final

            return "Los agentes externos no pudieron ayudarme esta vez."

        elif opcion == "2":
            respuesta_nueva = io_manager.solicitar_respuesta_nueva()
            if respuesta_nueva:
                resultado_ap = learning.aprender_pregunta(entrada_original, respuesta_nueva)
                exito        = resultado_ap.get("exito", False)
                accion       = resultado_ap.get("accion", "")
                if exito:
                    logger.info("sara", f"Aprendizaje exitoso: '{entrada_original[:50]}'")
                    return "¡Entendido! Lo recordaré para la próxima."
                elif accion == "duplicada":
                    return resultado_ap.get("mensaje", "")
                else:
                    return f"No pude guardar eso. {resultado_ap.get('mensaje', '')}"
            return "De acuerdo, lo omito por ahora."

        else:
            logger.info("sara", f"Usuario ignoró: '{entrada_original[:50]}'")
            return "De acuerdo, lo tendré en cuenta para mejorar."

    # ── COMANDO CON DESTINO ───────────────────────────────────────────────────
    elif tipo == "comando_con_destino":
        if comando and isinstance(comando, dict):
            resultado_cmd = commands.ejecutar_comando_con_destino(
                comando.get("accion", ""),
                comando.get("tipo", "app"),
                comando.get("url_contenido", ""),
                comando.get("nombre", "")
            )
            exito   = resultado_cmd.get("exito", False)
            mensaje = resultado_cmd.get("mensaje", "")
            logger.log_comando(comando, exito=exito)
            return mensaje if mensaje else "Ejecutado correctamente."
        return "No pude ejecutar ese comando."

    # ── COMANDO ───────────────────────────────────────────────────────────────
    elif tipo == "comando":
        if comando and isinstance(comando, dict):
            from database import es_comando_compuesto
            id_cmd = comando.get("id")
            if id_cmd and es_comando_compuesto(id_cmd):
                resultado_cmd = commands.ejecutar_comando_compuesto(id_cmd, comando.get("nombre", ""))
            else:
                resultado_cmd = commands.ejecutar_comando(comando)

            exito   = resultado_cmd.get("exito", False)
            mensaje = resultado_cmd.get("mensaje", "")
            logger.log_comando(comando, exito=exito)

            if exito and resultado.get("arbitrado"):
                confirmar = io_manager.preguntar_si_no(
                    "¿Era esto lo que pediste? (lo guardaré para la próxima)"
                )
                if confirmar:
                    palabras_clave = f"{entrada_original}, {entrada_usuario or ''}".strip(", ")
                    clave_cache    = resultado.get("query") or normalizar_texto(entrada_original)
                    from database import agregar_palabras_clave_comando, guardar_cache_intencion
                    if id_cmd:
                        agregar_palabras_clave_comando(id_cmd, palabras_clave)
                        logger.info("sara", f"Palabras clave agregadas a cmd id={id_cmd}")
                    else:
                        resultado_ap = learning.aprender_comando(
                            comando.get("nombre", entrada_original),
                            palabras_clave,
                            comando.get("accion", ""),
                            comando.get("tipo", "app"),
                            f"Aprendido por arbitraje desde: '{entrada_original}'"
                        )
                        if resultado_ap.get("exito") or resultado_ap.get("accion") == "duplicada":
                            logger.info("sara", f"Arbitraje guardado: '{entrada_original[:50]}'")
                    guardar_cache_intencion(clave_cache, resultado, ttl_segundos=3600)
                else:
                    logger.info("sara", f"Usuario no confirmó arbitraje: '{entrada_original[:50]}'")

            if not exito:
                logger.warning("sara", f"Comando falló: {comando.get('nombre', '?')}", mensaje)
            return mensaje if mensaje else "Comando ejecutado."

        resultado_archivo = commands.buscar_y_abrir_archivo(texto_mostrar)
        if resultado_archivo and resultado_archivo.get("exito"):
            return resultado_archivo.get("mensaje", "Abierto correctamente.")
        resultado_archivo = commands.buscar_y_abrir_carpeta(texto_mostrar)
        if resultado_archivo and resultado_archivo.get("exito"):
            return resultado_archivo.get("mensaje", "Abierto correctamente.")

        return _flujo_menu_comando_desconocido(entrada_original, texto_mostrar)

    # ── ARCHIVO ───────────────────────────────────────────────────────────────
    elif tipo == "archivo":
        if comando and isinstance(comando, dict):
            resultado_cmd = commands.ejecutar_comando(comando)
            exito         = resultado_cmd.get("exito", False)
            mensaje       = resultado_cmd.get("mensaje", "")
            if exito:
                from database import incrementar_acceso_archivo
                incrementar_acceso_archivo(comando.get("accion", ""))
            return mensaje if mensaje else "Abierto correctamente."
        return "No pude abrir el archivo."

    # ── ARCHIVO CON CONFIRMACIÓN ──────────────────────────────────────────────
    elif tipo == "archivo_confirmar":
        nombre_archivo = resultado.get("texto", "ese archivo")
        confianza      = resultado.get("confianza", 0.0)

        io_manager.mostrar_respuesta(
            f"Encontré '{nombre_archivo}' ({round(confianza*100, 1)}% de coincidencia). "
            f"¿Es esto lo que buscas?"
        )

        if io_manager.preguntar_si_no(""):
            cmd = resultado.get("comando")
            if cmd:
                from database import incrementar_acceso_archivo, guardar_cache_intencion
                incrementar_acceso_archivo(cmd.get("accion", ""))
                resultado_cmd = commands.ejecutar_comando(cmd)
                if resultado_cmd.get("exito"):
                    palabras_clave_nuevas = f"{entrada_original}, {entrada_usuario or ''}".strip(", ")
                    clave_cache           = resultado.get("query") or normalizar_texto(entrada_original)
                    id_cmd                = cmd.get("id")
                    if id_cmd:
                        from database import agregar_palabras_clave_comando
                        agregar_palabras_clave_comando(id_cmd, palabras_clave_nuevas)
                        logger.info("sara", f"Palabras clave confirmadas para cmd id={id_cmd}")
                    else:
                        resultado_ap = learning.aprender_comando(
                            cmd.get("nombre", entrada_original),
                            palabras_clave_nuevas,
                            cmd.get("accion", ""),
                            cmd.get("tipo", "app"),
                            f"Confirmado por usuario desde: '{entrada_original}'"
                        )
                        if resultado_ap.get("exito") or resultado_ap.get("accion") == "duplicada":
                            logger.info("sara", f"Archivo confirmado guardado: '{cmd.get('nombre')}'")
                    guardar_cache_intencion(clave_cache, resultado, ttl_segundos=3600)
                return resultado_cmd.get("mensaje", "Abierto correctamente.")
            return "No pude abrir el archivo."

        return _flujo_menu_comando_desconocido(entrada_original, texto_mostrar)

    # ── BÚSQUEDA ──────────────────────────────────────────────────────────────
    # ── SHELL INFO (respuesta de sistema — ya viene formateada de shell.py) ───
    # Se maneja igual que "respuesta" pero con logging de tipo shell_info.
    # brain.py ya retorna tipo "respuesta" para shell_info, así que esta
    # rama es solo por si en el futuro se diferencia el tipo de forma explícita.
    # (No requiere cambios adicionales — ya funciona vía tipo "respuesta")
    elif tipo == "busqueda":
        busqueda = resultado.get("busqueda", {})
        url      = busqueda.get("url", "")
        mensaje  = busqueda.get("mensaje", "")
        if url:
            resultado_cmd = commands._abrir_web(url, busqueda.get("plataforma", ""))
            if resultado_cmd.get("exito"):
                logger.info("sara", f"Búsqueda: '{busqueda.get('termino')}'",
                            f"plataforma: {busqueda.get('plataforma')}")
                return mensaje
            return f"No pude abrir la búsqueda. {resultado_cmd.get('mensaje', '')}"
        return "No pude construir la búsqueda."

    # ── EXTERNO ───────────────────────────────────────────────────────────────
    elif tipo == "externo":
        if BUSQUEDA_EXTERNA_ACTIVA:
            resultado_ext = external_service.buscar_web(query)
            resultados    = resultado_ext.get("resultados", [])
            if resultados:
                external_service.guardar_resultados_web(query, resultados)
                return "\n".join(f"  • {r}" for r in resultados)
        return "No encontré información externa sobre eso."

    # ── DESCONOCIDO ───────────────────────────────────────────────────────────
    else:
        resultado_archivo = commands.buscar_y_abrir_archivo(texto_mostrar)
        if resultado_archivo and resultado_archivo.get("exito"):
            return resultado_archivo.get("mensaje", "Abierto correctamente.")
        resultado_archivo = commands.buscar_y_abrir_carpeta(texto_mostrar)
        if resultado_archivo and resultado_archivo.get("exito"):
            return resultado_archivo.get("mensaje", "Abierto correctamente.")
        logger.log_intencion_desconocida(entrada_original)
        if not texto or len(texto.strip()) < 3:
            return ""
        return texto


# ══════════════════════════════════════════════════════════════════════════════
#  FUSIÓN DE RESPUESTAS MÚLTIPLES
# ══════════════════════════════════════════════════════════════════════════════

def _fusionar_respuestas(resultados):
    for entrada, resultado, respuesta in resultados:
        if resultado.get("tipo") != "respuesta":
            return False, "", 0.0
        if resultado.get("confianza", 0.0) < brain.UMBRAL_PREGUNTA:
            return False, "", 0.0
        if not respuesta or respuesta.startswith("No tengo"):
            return False, "", 0.0

    if len(resultados) > 1 and embeddings.esta_disponible():
        respuesta_base = resultados[0][2]
        for _, _, respuesta in resultados[1:]:
            if embeddings.similitud_semantica(respuesta_base, respuesta) < 0.30:
                return False, "", 0.0

    CONECTORES = ["Además, ", "También, ", "Asimismo, ", "Por otro lado, "]
    partes, confianzas = [], []

    for i, (_, resultado, respuesta) in enumerate(resultados):
        confianzas.append(resultado.get("confianza", 0.0))
        if i == 0:
            parte = respuesta[0].upper() + respuesta[1:] if respuesta else ""
        else:
            conector = random.choice(CONECTORES)
            parte    = conector + respuesta[0].lower() + respuesta[1:] if respuesta else ""
        if parte and not parte.endswith((".", "!", "?")):
            parte += "."
        partes.append(parte)

    return True, " ".join(partes), sum(confianzas) / len(confianzas)


# ══════════════════════════════════════════════════════════════════════════════
#  COMANDOS INTERNOS
# ══════════════════════════════════════════════════════════════════════════════

def _manejar_comando_interno(texto):
    texto_lower = texto.strip().lower()

    if texto_lower in ("/ayuda", "/help"):
        return True, (
            "\n Comandos internos de SARA:\n"
            "  /ayuda       → Muestra esta ayuda\n"
            "  /stats       → Estadísticas del sistema\n"
            "  /aprender    → Enseñar nueva pregunta\n"
            "  añadir comando → Crear un comando nuevo\n"
            "  /escanear    → Re-escanear carpetas y archivos\n"
            "  /aprendizaje → Ver cuántas intenciones ha aprendido SARA\n"
            "  añadir pregunta → Guardar una pregunta nueva\n"
            "  añadir búsqueda → Guardar una búsqueda o atajo web\n"
            "  /version     → Versión actual\n"
            "  /plataformas → Plataformas de búsqueda\n"
            "  /voz         → Activar modo voz\n"
            "  /texto       → Desactivar modo voz\n"
            "  /microfonos  → Ver micrófonos disponibles\n"
        )

    elif texto_lower == "/plataformas":
        respuesta = "\n Plataformas disponibles:\n"
        for p in searcher.plataformas_disponibles():
            respuesta += f"  → {p}\n"
        return True, respuesta

    elif texto_lower == "/escanear":
        def _progreso(total, ruta_actual):
            print(f"\r  Indexando... {total} elementos | {ruta_actual[-50:]}", end="")
        def _fin(total):
            print(f"\n  ✅ Listo. {total} elementos indexados.")
        file_watcher.escanear_en_hilo(_progreso, _fin)
        return True, "Iniciando re-escaneo en segundo plano..."

    elif texto_lower == "/version":
        return True, f"SARA — Sistema Autónomo de Razonamiento Artificial v{VERSION}"
    elif texto_lower in ("/aprendizaje", "/shell-learner", "cuanto has aprendido",
                         "estadisticas de aprendizaje"):
        if SHELL_LEARNER_DISPONIBLE:
            r = shell_learner.estado_aprendizaje()
            return True, r.get("mensaje", "No pude obtener estadísticas de aprendizaje.")
        return True, "Shell Learner no disponible."
    elif texto_lower in ("/sistema", "estado sistema", "como estas", "como estás",
                         "como esta el sistema", "como va el sistema", "diagnostico"):
        if SHELL_DISPONIBLE:
            r = shell.diagnostico_sistema()
            return True, r.get("mensaje", "No pude obtener el estado del sistema.")
        return True, "El subsistema PRAXIS no está disponible."

    elif texto_lower.startswith("instala ") or texto_lower.startswith("instalar "):
        if SHELL_DISPONIBLE:
            paquete = texto_lower.replace("instala ", "").replace("instalar ", "").strip()
            r = shell.instalar_pip(paquete)
            return True, r.get("mensaje", "No pude completar la instalación.")
        return True, "El subsistema PRAXIS no está disponible."

    elif texto_lower.startswith("cierra ") or texto_lower.startswith("mata el proceso "):
        if SHELL_DISPONIBLE:
            proceso = texto_lower.replace("cierra ", "").replace("mata el proceso ", "").strip()
            r = shell.matar_proceso(proceso)
            return True, r.get("mensaje", "No pude cerrar el proceso.")
        return True, "El subsistema PRAXIS no está disponible."

    elif texto_lower.startswith("versión de ") or texto_lower.startswith("version de "):
        if SHELL_DISPONIBLE:
            herramienta = texto_lower.replace("versión de ", "").replace("version de ", "").strip()
            r = shell.version_herramienta(herramienta)
            return True, r.get("mensaje", f"No encontré información de versión para '{herramienta}'.")
        return True, "El subsistema PRAXIS no está disponible."

    elif texto_lower == "/stats":
        stats        = learning.obtener_estadisticas()
        stats_social = database.obtener_stats_sociales()
        if stats:
            respuesta = (
                f"\n Estadísticas de SARA:\n"
                f"  Conocimientos: {stats.get('total_conocimientos', 0)}\n"
                f"  Comandos:      {stats.get('total_comandos', 0)}\n"
            )
            if stats_social:
                respuesta += "\n Interacciones sociales:\n"
                for fila in stats_social:
                    respuesta += f"  {fila['tipo_social']:15} → {fila['total']}\n"
        else:
            respuesta = "No se pudieron obtener estadísticas."

        # Añadir stats del Shell Learner
        if SHELL_LEARNER_DISPONIBLE:
            try:
                sl_stats = shell_learner.estado_aprendizaje()
                if sl_stats.get("total_aprendido", 0) > 0:
                    respuesta += (
                        f"\n Shell Learner (aprendizaje automático):\n"
                        f"  Intenciones aprendidas: {sl_stats['total_aprendido']}\n"
                        f"  Vectorizadas:           {sl_stats['con_vector']}\n"
                        f"  Usos acumulados:        {sl_stats['usos_totales']}\n"
                    )
            except Exception:
                pass

        return True, respuesta

    elif texto_lower in ("activar modo voz", "activar voz", "/voz"):
        if not voice.esta_disponible():
            voz_ok = voice.inicializar()
            if not voz_ok:
                return True, "No pude inicializar el micrófono."
        io_manager.activar_modo_voz(voice)
        logger.info("sara", "Modo voz activado.")
        global _modo_voz_activo
        _modo_voz_activo = True
        _emitir("voz_estado", activo=True)                                      # ← GUI
        return True, "🎤 Modo voz activado. Di 'sara' para activarme."

    elif texto_lower in ("desactivar modo voz", "desactivar voz", "/texto"):
        io_manager.desactivar_modo_voz()
        logger.info("sara", "Modo voz desactivado.")
        _modo_voz_activo = False
        _emitir("voz_estado", activo=False)                                     # ← GUI
        return True, "⌨️ Modo voz desactivado. Volviendo a modo texto."

    elif texto_lower == "/microfonos":
        print("\n Micrófonos disponibles:")
        voice.listar_microfonos()
        return True, ""

    elif texto_lower == "/aprender":
        return True, _flujo_aprendizaje()

    elif texto_lower in ("añadir comando", "agregar comando", "añadir un comando", "agregar un comando"):
        return True, _flujo_agregar_comando()

    elif texto_lower in ("añadir pregunta", "agregar pregunta", "añadir una pregunta", "agregar una pregunta"):
        return True, _flujo_agregar_pregunta()

    elif texto_lower in ("añadir búsqueda", "agregar búsqueda", "añadir busqueda", "agregar busqueda",
                         "añadir una búsqueda", "agregar una búsqueda"):
        return True, _flujo_agregar_busqueda()

    return False, ""


def _es_gemini_disponible():
    return USAR_GEMINI_BACKUP and external_service.gemini_disponible()


def _flujo_aprendizaje():
    try:
        io_manager.mostrar_respuesta("Modo aprendizaje activado.")
        io_manager.mostrar_respuesta("Escribe la pregunta (o 'cancelar'):")
        pregunta = input("  Pregunta: ").strip()
        if pregunta.lower() == "cancelar":
            return "Aprendizaje cancelado."
        io_manager.mostrar_respuesta("Escribe la respuesta correcta:")
        respuesta = input("  Respuesta: ").strip()
        if not pregunta or not respuesta:
            return "Datos vacíos. Cancelado."
        resultado = learning.aprender_pregunta(pregunta, respuesta)
        return resultado.get("mensaje", "Error en aprendizaje.")
    except KeyboardInterrupt:
        return "\nAprendizaje cancelado."
    except Exception as e:
        logger.log_excepcion("sara", "_flujo_aprendizaje", e)
        return f"Error: {e}"


def _construir_url_busqueda(plataforma, termino):
    termino = termino.strip()
    if not termino:
        return ""
    consulta = quote(termino)
    if plataforma == "google":
        return f"https://www.google.com/search?q={consulta}"
    if plataforma == "bing":
        return f"https://www.bing.com/search?q={consulta}"
    if plataforma == "youtube":
        return f"https://www.youtube.com/results?search_query={consulta}"
    if plataforma == "duckduckgo":
        return f"https://duckduckgo.com/?q={consulta}"
    return f"https://www.google.com/search?q={consulta}"


def _confirmar_y_guardar_comando(comando):
    if not comando or not isinstance(comando, dict):
        return False, "Comando inválido."
    nombre         = comando.get("nombre") or input("  Nombre del comando: ").strip()
    accion         = comando.get("accion", "").strip()
    tipo           = comando.get("tipo", "web").strip()
    palabras_clave = comando.get("palabras_clave", "").strip()
    descripcion    = comando.get("descripcion", accion).strip()
    io_manager.mostrar_respuesta("Gemini propone este comando:")
    io_manager.mostrar_respuesta(f"  Nombre: {nombre}")
    io_manager.mostrar_respuesta(f"  Tipo: {tipo}")
    io_manager.mostrar_respuesta(f"  Acción: {accion}")
    io_manager.mostrar_respuesta(f"  Descripción: {descripcion}")
    if not io_manager.preguntar_si_no("¿Deseas guardar este comando?"):
        return False, "No guardé el comando."
    if tipo not in {"web", "app", "sistema", "sistema_control"}:
        return False, f"Tipo de comando inválido: {tipo}."
    resultado = learning.aprender_comando(nombre, palabras_clave, accion, tipo, descripcion)
    return resultado.get("exito", False), resultado.get("mensaje", "Error al guardar.")


def _flujo_agregar_pregunta():
    try:
        io_manager.mostrar_respuesta("Vamos a añadir una nueva pregunta.")
        manual = True
        if _es_gemini_disponible():
            manual = not io_manager.preguntar_si_no(
                "¿Quieres que Gemini sugiera la respuesta para esta pregunta?"
            )
        pregunta = input("  Pregunta: ").strip()
        if pregunta.lower() == "cancelar":
            return "Operación cancelada."
        if not pregunta:
            return "La pregunta no puede estar vacía."
        if manual:
            respuesta = input("  Respuesta: ").strip()
            if respuesta.lower() == "cancelar":
                return "Operación cancelada."
            if not respuesta:
                return "La respuesta no puede estar vacía."
        else:
            respuesta = external_service.obtener_respuesta_gemini(pregunta)
            if not respuesta:
                return "Gemini no está disponible, no pude obtener una sugerencia."
            io_manager.mostrar_respuesta(f"Gemini sugiere: {respuesta}")
            if not io_manager.preguntar_si_no("¿Deseas guardar esta respuesta?"):
                return "No guardé la pregunta."
        resultado = learning.aprender_pregunta(pregunta, respuesta)
        return resultado.get("mensaje", "Error al guardar la pregunta.")
    except KeyboardInterrupt:
        return "Operación cancelada."
    except Exception as e:
        logger.log_excepcion("sara", "_flujo_agregar_pregunta", e)
        return f"Error: {e}"


def _flujo_agregar_comando():
    try:
        io_manager.mostrar_respuesta("Vamos a añadir un nuevo comando.")
        if _es_gemini_disponible():
            if io_manager.preguntar_si_no("¿Quieres que Gemini sugiera el comando?"):
                descripcion = input("  Describe qué debe hacer el comando: ").strip()
                if descripcion.lower() == "cancelar":
                    return "Operación cancelada."
                comando = external_service.generar_comando_gemini(descripcion)
                if not comando:
                    return "Gemini no pudo generar un comando válido."
                exito, mensaje = _confirmar_y_guardar_comando(comando)
                return mensaje
        nombre = input("  Nombre del comando: ").strip()
        if nombre.lower() == "cancelar":
            return "Operación cancelada."
        if not nombre:
            return "El nombre no puede estar vacío."
        datos = io_manager.solicitar_datos_comando()
        if not datos:
            return "Operación cancelada."
        resultado = learning.aprender_comando(
            nombre, datos.get("palabras_clave", ""), datos.get("accion", ""),
            datos.get("tipo", "web"), datos.get("descripcion", "")
        )
        return resultado.get("mensaje", "Error al guardar el comando.")
    except KeyboardInterrupt:
        return "Operación cancelada."
    except Exception as e:
        logger.log_excepcion("sara", "_flujo_agregar_comando", e)
        return f"Error: {e}"


def _flujo_agregar_busqueda():
    try:
        io_manager.mostrar_respuesta("Vamos a añadir una búsqueda como comando rápido.")
        if _es_gemini_disponible():
            if io_manager.preguntar_si_no("¿Quieres que Gemini genere el comando de búsqueda?"):
                descripcion = input("  Describe la búsqueda que quieres guardar: ").strip()
                if descripcion.lower() == "cancelar":
                    return "Operación cancelada."
                comando = external_service.generar_comando_gemini(descripcion)
                if comando and comando.get("tipo") == "web":
                    exito, mensaje = _confirmar_y_guardar_comando(comando)
                    return mensaje
                return "Gemini no generó un comando web válido. Intenta ingresarlo manualmente."
        nombre = input("  Nombre del comando de búsqueda: ").strip()
        if nombre.lower() == "cancelar":
            return "Operación cancelada."
        if not nombre:
            return "El nombre no puede estar vacío."
        termino = input("  ¿Qué quieres buscar? ").strip()
        if termino.lower() == "cancelar":
            return "Operación cancelada."
        if not termino:
            return "El término de búsqueda no puede estar vacío."
        io_manager.mostrar_respuesta("Selecciona la plataforma de búsqueda:")
        io_manager.mostrar_respuesta("  1. Google\n  2. Bing\n  3. YouTube\n  4. DuckDuckGo")
        opcion      = input("  Elige 1, 2, 3 o 4: ").strip()
        plataformas = {"1": "google", "2": "bing", "3": "youtube", "4": "duckduckgo"}
        plataforma  = plataformas.get(opcion, "google")
        url = _construir_url_busqueda(plataforma, termino)
        if not url:
            return "No se pudo construir la URL de búsqueda."
        palabras_clave = input("  Palabras clave para el comando: ").strip()
        descripcion    = input("  Descripción breve: ").strip() or f"Buscar '{termino}' en {plataforma.capitalize()}"
        resultado = learning.aprender_comando(nombre, palabras_clave, url, "web", descripcion)
        return resultado.get("mensaje", "Error al guardar la búsqueda.")
    except KeyboardInterrupt:
        return "Operación cancelada."
    except Exception as e:
        logger.log_excepcion("sara", "_flujo_agregar_busqueda", e)
        return f"Error: {e}"


def _manejar_correccion():
    global _ultima_interaccion
    if not _ultima_interaccion["pregunta"]:
        return "No recuerdo qué respondí antes."
    pregunta_confundida = _ultima_interaccion["pregunta"]
    respuesta_vieja     = _ultima_interaccion["respuesta"]
    confianza_erronea   = _ultima_interaccion.get("confianza", 0.0)
    tipo                = _ultima_interaccion["tipo"]
    if tipo != "pregunta":
        return "Solo puedo corregir respuestas a preguntas."
    io_manager.mostrar_respuesta(
        f"Entendido. Respondí:\n  '{respuesta_vieja}'\n¿Cuál es la respuesta correcta?"
    )
    try:
        respuesta_nueva = input("  → ").strip()
    except KeyboardInterrupt:
        return "Corrección cancelada."
    if not respuesta_nueva or respuesta_nueva.lower() == "cancelar":
        return "Corrección cancelada."
    pregunta_usuario = _ultima_interaccion.get("entrada_original", pregunta_confundida)
    from utils import similitud, normalizar_texto
    sim        = similitud(normalizar_texto(pregunta_usuario), normalizar_texto(pregunta_confundida))
    tipo_error = "confusion" if sim < 0.85 else "respuesta_incorrecta"
    from database import guardar_correccion_completa
    guardar_correccion_completa(
        pregunta_usuario=pregunta_usuario, pregunta_confundida=pregunta_confundida,
        respuesta_antigua=respuesta_vieja, respuesta_nueva=respuesta_nueva,
        tipo_error=tipo_error, confianza_erronea=confianza_erronea
    )
    learning.corregir_pregunta(pregunta_confundida, respuesta_nueva)
    if tipo_error == "confusion":
        resultado_nuevo = learning.aprender_pregunta(pregunta_usuario, respuesta_nueva)
        if resultado_nuevo.get("exito"):
            logger.info("sara", f"Nueva entrada creada: '{pregunta_usuario[:40]}'")
    if embeddings.esta_disponible():
        vector = embeddings.vector_desde_texto(normalizar_texto(pregunta_confundida))
        if vector:
            from database import guardar_vector_conocimiento
            guardar_vector_conocimiento(pregunta_confundida, vector)
    try:
        actualizar_resultado_intencion(normalizar_texto(pregunta_usuario), "corregido")
    except Exception:
        pass
    logger.info("sara", f"Corrección tipo '{tipo_error}': '{pregunta_usuario[:40]}'")
    _ultima_interaccion = {"pregunta": None, "respuesta": None, "tipo": None,
                           "confianza": 0.0, "entrada_original": None}
    return "¡Gracias! Aprendí de mi error y lo recordaré correctamente."


# ══════════════════════════════════════════════════════════════════════════════
#  PROCESAMIENTO DE TEXTO — extraído para reutilizar desde GUI y terminal
# ══════════════════════════════════════════════════════════════════════════════

def _procesar_texto(entrada: str):
    """
    Procesa una entrada de texto completa.
    Usado tanto por run() (terminal) como por procesar_entrada_externa() (GUI).
    """
    global _ultima_interaccion

    if not entrada or not entrada.strip():
        return

    if io_manager.es_comando_salida(entrada):
        logger.log_cierre()
        io_manager.mostrar_despedida()
        return

    manejado, respuesta_interna = _manejar_comando_interno(entrada)
    if manejado:
        io_manager.mostrar_respuesta(respuesta_interna)
        io_manager.mostrar_separador()
        return

    if social.es_correccion(entrada) and _ultima_interaccion.get("pregunta"):
        respuesta_correccion = _manejar_correccion()
        io_manager.mostrar_respuesta(respuesta_correccion)
        io_manager.mostrar_separador()
        return

    es_social, respuesta_social = social.detectar_entrada_social(entrada)
    if es_social:
        io_manager.mostrar_respuesta(respuesta_social)
        io_manager.mostrar_separador()
        return

    try:
        es_valida, motivo = validator.validar_entrada(entrada)
    except Exception:
        es_valida, motivo = True, ""
    if not es_valida:
        if motivo:
            io_manager.mostrar_respuesta(motivo)
        try:
            sugerencia = validator.obtener_sugerencia(entrada)
            if sugerencia:
                io_manager.mostrar_respuesta(sugerencia)
        except Exception:
            pass
        io_manager.mostrar_separador()
        return

    # ── Emitir thinking al frontend ───────────────────────────────────────────  ← GUI
    _emitir("thinking", fase="procesando")

    entradas = splitter.dividir_entrada(entrada)

    if len(entradas) > 1:
        resultados_multiples = []
        for entrada_individual in entradas:
            entrada_procesada = entrada_individual
            tema_resuelto     = None

            # Proteger marcadores especiales — no normalizar ni resolver contexto
            es_marcador = (
                entrada_individual.startswith("__DESTINO__") or
                entrada_individual.startswith("__CARPETA_CTX__")
            )

            if not es_marcador:
                if context.necesita_contexto(entrada_individual):
                    entrada_procesada, tema_resuelto = context.resolver(entrada_individual)

            # Caché solo para entradas sin marcador (los marcadores nunca se cachean
            # porque contienen rutas absolutas dependientes del índice actual)
            if es_marcador:
                resultado = brain.procesar(entrada_procesada)
            else:
                texto_limpia = normalizar_texto(entrada_procesada)
                resultado = _obtener_cache_intencion(texto_limpia)
                if resultado is None:
                    resultado = brain.procesar(entrada_procesada)
                    _guardar_cache_intencion(texto_limpia, resultado)
            respuesta_final = _manejar_resultado(resultado, entrada_procesada, entrada_individual)
            if resultado.get("tipo") == "respuesta" and resultado.get("confianza", 0) >= brain.UMBRAL_PREGUNTA:
                _ultima_interaccion.update({
                    "pregunta": entrada_procesada, "respuesta": respuesta_final, "tipo": "pregunta"
                })
            if resultado.get("tipo") == "respuesta":
                context.actualizar(entrada_procesada, respuesta_final, tema=tema_resuelto)
            resultados_multiples.append((entrada_individual, resultado, respuesta_final))

        _emitir("thinking_stop")                                                # ← GUI
        fusionado, respuesta_fusion, confianza_fusion = _fusionar_respuestas(resultados_multiples)
        if fusionado:
            io_manager.mostrar_respuesta(respuesta_fusion)
            if MOSTRAR_CONFIANZA:
                io_manager.mostrar_confianza(confianza_fusion)
        else:
            for _, resultado, respuesta_final in resultados_multiples:
                io_manager.mostrar_respuesta(respuesta_final)
                if MOSTRAR_CONFIANZA:
                    io_manager.mostrar_confianza(resultado.get("confianza", 0.0))
        io_manager.mostrar_separador()
        return

    # ── Entrada única ─────────────────────────────────────────────────────────
    entrada_procesada = entradas[0]
    tema_resuelto     = None
    if not entrada_procesada.startswith("__DESTINO__"):
        if context.necesita_contexto(entrada_procesada):
            entrada_procesada, tema_resuelto = context.resolver(entrada_procesada)

    texto_limpia  = normalizar_texto(entrada_procesada)
    resultado_ref = [None]
    excepcion_ref = [None]

    def _en_hilo():
        try:
            res = _obtener_cache_intencion(texto_limpia)
            if res is None:
                res = brain.procesar(entrada_procesada)
                _guardar_cache_intencion(texto_limpia, res)
            resultado_ref[0] = res
        except Exception as e:
            excepcion_ref[0] = e

    hilo = threading.Thread(target=_en_hilo, daemon=True)
    hilo.start()
    hilo.join(timeout=TIMEOUT_AVISO_LENTITUD)

    if hilo.is_alive():
        io_manager.mostrar_respuesta(
            f"⏳ Procesando '{entrada[:40]}{'...' if len(entrada) > 40 else ''}' — espera..."
        )
        _emitir("thinking", fase="procesando")                                  # ← GUI
        hilo.join(timeout=TIMEOUT_PROCESAMIENTO - TIMEOUT_AVISO_LENTITUD)
        if hilo.is_alive():
            _emitir("thinking_stop")                                            # ← GUI
            logger.error("sara", f"Hilo colgado ({TIMEOUT_PROCESAMIENTO}s): '{entrada[:50]}'")
            io_manager.mostrar_respuesta(
                f"⚠️  Procesamiento tardó demasiado. Intenta de nuevo."
            )
            io_manager.mostrar_separador()
            return

    _emitir("thinking_stop")                                                    # ← GUI

    if excepcion_ref[0]:
        e = excepcion_ref[0]
        logger.log_excepcion("sara", "_en_hilo", e)
        io_manager.mostrar_respuesta(f"⚠️  Error procesando la entrada: {type(e).__name__}.")
        io_manager.mostrar_separador()
        return

    resultado = resultado_ref[0]
    if resultado is None:
        io_manager.mostrar_respuesta("No pude procesar esa entrada.")
        io_manager.mostrar_separador()
        return

    respuesta_final = _manejar_resultado(resultado, entrada_procesada, entrada)

    if resultado.get("tipo") == "respuesta" and resultado.get("confianza", 0) >= brain.UMBRAL_PREGUNTA:
        _ultima_interaccion.update({
            "pregunta": entrada_procesada, "respuesta": respuesta_final,
            "tipo": "pregunta", "confianza": resultado.get("confianza", 0.0),
            "entrada_original": entrada
        })

    if resultado.get("tipo") == "respuesta":
        context.actualizar(entrada_procesada, respuesta_final, tema=tema_resuelto)

    # ── Emitir respuesta final al frontend con confianza ─────────────────────  ← GUI
    _emitir(
    "respuesta_completa",
    texto=respuesta_final,
    confianza=resultado.get("confianza", 0.0),
    tipo_resultado=resultado.get("tipo", ""),
    arbitrado=resultado.get("arbitrado", False)
    )   

    io_manager.mostrar_respuesta(respuesta_final)
    if MOSTRAR_CONFIANZA:
        io_manager.mostrar_confianza(resultado.get("confianza", 0.0))
    io_manager.mostrar_separador()


# ══════════════════════════════════════════════════════════════════════════════
#  BUCLE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def run():
    while True:
        try:
            entrada = io_manager.obtener_input()
            if not entrada or not entrada.strip():
                continue
            if io_manager.es_comando_salida(entrada):
                logger.log_cierre()
                io_manager.mostrar_despedida()
                break
            _procesar_texto(entrada)

        except KeyboardInterrupt:
            logger.log_cierre()
            io_manager.mostrar_despedida()
            break
        except Exception as e:
            logger.log_excepcion("sara", "run", e)
            io_manager.mostrar_error(f"Error inesperado: {e}")


if __name__ == "__main__":
    if not inicializar():
        logger.critical("sara", "Fallo en inicialización.")
        sys.exit(1)
    run()

#.\sara.bat