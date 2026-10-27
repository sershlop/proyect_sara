"""
🌉 core_bridge.py — Puente GUI/WebSocket ⇄ núcleo SARA  (v6)

Contrato con server.py (todo SÍNCRONO, corre en un hilo del executor):

    inicializar() -> bool
    esta_listo() -> bool
    procesar_mensaje(texto, emit) -> {"exito": bool, "texto": str}
    resolver_confirmacion(id_confirmacion, respuesta, opcion=None) -> bool     (v5: opcion)
    obtener_eventos_proactivos() -> [(tipo_evento, {datos}), ...]
    responder_sugerencia(id_sugerencia, respuesta, emit) -> {"exito","texto"}

`emit(tipo, **datos)` es thread-safe y entrega eventos que app.js entiende:
estado_procesando, resultado_clasificacion, solicitud_confirmacion,
confirmacion_ya_resuelta. El `resultado_accion` final lo emite server.py.

Novedades v6 (sobre v5):
  * WhatsApp (whatsapp.py + wa_sidecar/): categoría WHATSAPP, comando /whatsapp, avisos de mensajes
    entrantes, recordatorios reenviados al propio WhatsApp. Envíos SIEMPRE con confirmación.

Novedades v5 (sobre v4):
  * Siembra de patrones corregida: "abre X" ya no compite entre ABRIR_ARCHIVO y ABRIR_APLICACION
    (ABRIR_ARCHIVO solo se siembra con frases de 2+ palabras) y se desactivan las filas
    obsoletas que ya estuvieran en la BD. Además se siembran los verbos de listar/completar/
    eliminar de TAREA/RECORDATORIO/NOTA (antes "mis tareas" nunca llegaba a productivity).
  * DESCRIPCIONES_CATEGORIAS se completa para todas las categorías sembradas: el árbitro
    (Llama vía Ollama) recibía "sin descripcion adicional" y clasificaba a ciegas.
  * "abre X" que no es una aplicación se reintenta como carpeta y como archivo.
  * resolver_confirmacion acepta la opción elegida (para confirmaciones de elección múltiple).
  * resultado_clasificacion incluye método e id_historial; nuevo comando /categorias.
"""

import importlib
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("SARA-BRIDGE")

CORE_BRIDGE_VERSION = "v7.6-multimedia-bloqueo-celular"   # se ve al final de /ayuda: confirma que este archivo es el aplicado
TIMEOUT_CONFIRMACION_S = 30
_NO_DISPONIBLE = object()   # la función pedida no existe en el módulo
_AFIRMATIVOS = ("si", "sí", "s", "yes", "y")
_ctx = threading.local()    # contexto del hilo que procesa una orden (origen: "whatsapp" = SARA-Net)
_emit_gui: Optional[Callable] = None   # emisor GLOBAL hacia la GUI (lo registra server.py); las órdenes de WhatsApp no traen uno
_ORIGENES_REMOTOS =  ("whatsapp", "telegram", "celular") # canales que llegan desde fuera del PC (más restringidos que la ventana)


# ─── IMPORTS (degradación elegante) ───────────────────────────────────────

def _importar(nombre: str, critico: bool = False):
    try:
        return importlib.import_module(nombre)
    except Exception as e:  # ImportError o cualquier error dentro del módulo
        if critico:
            logger.error(f"❌ Módulo CRÍTICO '{nombre}' falló: {e}", exc_info=True)
            raise
        logger.warning(f"⚠️  Módulo opcional no disponible: {nombre} ({type(e).__name__}: {e})")
        return None


config = _importar("config", critico=True)
db = _importar("database", critico=True)
engine = _importar("intent_engine", critico=True)

productivity = _importar("productivity")
system_info = _importar("system_info")
file_index = _importar("file_index")
file_manager = _importar("file_manager")
file_actions = _importar("file_actions")
app_index = _importar("app_index")
app_actions = _importar("app_actions")
shell = _importar("shell")
web_navigator = _importar("web_navigator")
habitos = _importar("habitos")
memoria_core = _importar("memoria_core")
optimizador = _importar("optimizador")   # Análisis + autooptimización de RAM/CPU (opcional)
salud_sistema = _importar("salud_sistema")
generador_codigo = _importar("generador_codigo")
informativo = _importar("informativo")
voz = _importar("voz")            # TTS (opcional: si falla, SARA sigue en texto)
lector_voz = _importar("lector_voz")   # Lector de texto a voz (canal aparte; la bandeja principal tiene prioridad)
stt = _importar("stt")                 # Voz de entrada (opcional: si falla, SARA sigue con texto)
conversacion = _importar("conversacion")   # Puente conversacional con IA externa (opcional: si falla, flujo de siempre)
memoria_local = _importar("memoria_local")     # recuerda lo que la IA ya resolvió (opcional)
abridor = _importar("abridor_inteligente") # Abrir archivos/carpetas con inteligencia + elección de editor (opcional)
navegador_activo = _importar("navegador_activo")  # Qué navegador usar si no dices cuál: el abierto / el más usado (opcional)
whatsapp = _importar("whatsapp")           # WhatsApp (dispositivo vinculado vía wa_sidecar; opcional)
telegram_bot = _importar("telegram_bot")        # Telegram (bot privado como control remoto; opcional)
celular = _importar("celular")
volumen = _importar("volumen")                 # Control del volumen del sistema en lenguaje natural (opcional)
multimedia = _importar("multimedia")         # Pausa / siguiente / anterior de lo que suena (Spotify, YouTube…) en lenguaje natural (opcional)
envio_archivos = _importar("envio_archivos")   # «mándame el archivo X» por Telegram (opcional)
pantalla = _importar("pantalla")               # «quiero ver la pantalla» por Telegram (opcional)
alertas = _importar("alertas")                 # alertas proactivas al celular + interruptor maestro (opcional)
desbloqueo = _importar("desbloqueo")
deshacer = _importar("deshacer")               # «deshaz lo último» + papelera propia (opcional)
modos = _importar("modos")                     # modos o escenas (opcional)
antirrobo = _importar("antirrobo")             # /donde y /foto por Telegram (opcional)
interruptores = _importar("interruptores")     # switches: descripción de pantalla con IA, verificación TOTP (opcional)           # ventana TOTP compartida por envío y pantalla (opcional)
apagado = _importar("apagado")                 # Apagar el equipo, con aviso y confirmación (opcional)
_listo = False
_ultimo_res: Optional[dict] = None      # última clasificación (para /confirmar /validar /corregir)


def esta_listo() -> bool:
    return _listo


def registrar_emit_gui(fn: Optional[Callable]) -> None:
    """server.py entrega su emisor global (thread-safe). Así la aureola reacciona cuando SARA habla por una
    orden que NO viene de la GUI (WhatsApp): el emit de esas órdenes solo habla con el chat del celular."""
    global _emit_gui
    _emit_gui = fn


def _ceder_lector(emit: Optional[Callable] = None) -> None:
    """PRIORIDAD de la bandeja principal: si el lector está leyendo, se detiene (queda pausado, reanudable
    desde el panel) y SARA avisa en voz corta. Si no hay lectura activa, no hace nada."""
    if lector_voz is None:
        return
    try:
        if lector_voz.detener("prioridad"):
            logger.info("🔇 Lector pausado: la bandeja principal tiene prioridad")
            _hablar("Lectura en pausa. Atiendo tu comando primero.", emit, False)
    except Exception as e:
        logger.warning(f"⚠️  ceder_lector falló: {type(e).__name__}: {e}")


def _emit_con_stt(emit: Optional[Callable]) -> Optional[Callable]:
    """Envuelve el emit de voz.py: al ver voz_estado avisa al STT para que no se transcriba a SARA."""
    if stt is None:
        return emit

    def _e(tipo: str, **datos) -> None:
        if tipo == "voz_estado":
            _seguro("stt.set_voz_hablando", stt.set_voz_hablando, bool(datos.get("hablando")))
        if emit is not None:
            emit(tipo, **datos)
    return _e


def _hablar(texto: Any, emit: Optional[Callable] = None, exito: bool = True, proactivo: bool = False,
            charla: bool = False) -> None:
    """La voz NUNCA debe romper el flujo de SARA: cualquier fallo se registra y se ignora.
    Si el lector está leyendo: los avisos proactivos NO lo interrumpen; una respuesta a un comando sí."""
    if voz is None or not texto:
        return
    origen_ctx = getattr(_ctx, "origen", None)
    es_wa = origen_ctx in _ORIGENES_REMOTOS
    if origen_ctx == "whatsapp" and not getattr(config, "WA_NET_VOZ", True):
        return        # SARA-Net con WA_NET_VOZ=False: la respuesta va solo al chat, no a la bocina del PC
    if origen_ctx == "telegram" and not (telegram_bot is not None
                                         and _seguro("telegram.bocina_activa", telegram_bot.bocina_activa)):
        return        # Telegram: por defecto la respuesta va solo al chat (/telegram bocina on para oírla en el PC)  # SARA-Net con WA_NET_VOZ=False: la respuesta va solo al chat, no a la bocina del PC
    if origen_ctx == "celular":
        return        # el celular recibe la respuesta en su pantalla, no en la bocina del PC
    if lector_voz is not None and lector_voz.esta_leyendo():
        if proactivo or es_wa:
            return    # un aviso proactivo o una orden remota (celular) NO interrumpen la lectura en curso del PC
        _ceder_lector(emit)
    if es_wa and _emit_gui is not None:
        emit = _emit_gui          # voz_estado debe llegar a la GUI (aureola violeta), no al emit del chat de WhatsApp
    try:
        if charla:                    # respuesta conversacional: sin muletilla ("Claro, aquí tienes.")
            try:
                voz.hablar(str(texto), emit=_emit_con_stt(emit), exito=exito, muletilla=False)
                return
            except TypeError:         # voz.py antiguo sin el parámetro 'muletilla'
                pass
        voz.hablar(str(texto), emit=_emit_con_stt(emit), exito=exito)
    except Exception as e:
        logger.warning(f"⚠️  voz.hablar falló: {type(e).__name__}: {e}")


# ─── DESCRIPCIONES DE CATEGORÍAS PARA EL ÁRBITRO (Capa 3) ─────────────────
# config.DESCRIPCIONES_CATEGORIAS no describe TAREA, RECORDATORIO, NOTA, ARCHIVO, CARPETA_ESPECIAL...
# Se completa EN SITIO (intent_engine guarda una referencia al mismo dict); nunca pisa lo ya definido.

_DESCRIPCIONES_EXTRA = {
    "TAREA": "Crear, listar, completar o eliminar tareas pendientes (lista de pendientes / to-do)",
    "RECORDATORIO": "Crear, listar o eliminar recordatorios con fecha u hora ('recuérdame en 10 minutos...')",
    "NOTA": "Crear, listar, buscar o eliminar notas de texto",
    "WHATSAPP": "Mandar, responder o leer mensajes de WhatsApp, o avisar al usuario por WhatsApp",
    "ARCHIVO": "Buscar un archivo o carpeta por nombre y mostrar dónde está, SIN abrirlo",
    "ABRIR_ARCHIVO": "Abrir un archivo concreto del usuario (documento, pdf, imagen) con su programa asociado",
    "CARPETA_ESPECIAL": "Abrir una carpeta (escritorio, descargas, documentos o cualquier carpeta indexada)",
    "ABRIR_APLICACION": "Abrir una aplicación o programa instalado (calculadora, chrome, spotify, word...)",
    "CERRAR_APLICACION": "Cerrar una aplicación o programa que está abierto",
    "CREAR_CARPETA": "Crear una carpeta nueva con un nombre dado",
    "ELIMINAR_ARCHIVO": "Eliminar un archivo o carpeta (pide confirmación)",
    "MOVER_ARCHIVO": "Mover un archivo o carpeta a otra carpeta",
    "COPIAR_ARCHIVO": "Copiar un archivo o carpeta a otra carpeta",
    "RENOMBRAR_ARCHIVO": "Cambiar el nombre de un archivo o carpeta",
    "COMPRIMIR_ARCHIVO": "Comprimir un archivo o carpeta en un .zip",
    "COMANDO_SISTEMA": "Ejecutar un comando o tarea del sistema operativo (PowerShell): procesos, red, disco...",
    "INFO_IP": "Decir la dirección IP local y pública del equipo",
    "INFO_BATERIA": "Decir el nivel y estado de la batería",
    "INFO_RECURSOS": "Decir el uso actual de CPU, RAM y disco",
    "INFO_NOMBRE": "Decir el nombre del equipo",
    "BUSCAR_EN_NAVEGADOR": "Abrir un sitio web o buscar algo en internet (Google, YouTube, Gmail, WhatsApp Web...)",
    "GENERAR_CODIGO": "Escribir código o un script a partir de una descripción y guardarlo en un archivo",
    "PREGUNTA_INFORMATIVA": "Pregunta de cultura general o conocimiento ('qué es...', 'quién fue...', 'explícame...')",
    "EJECUTAR_PAQUETE": "Ejecutar un comando personalizado del usuario que abre varias apps/archivos a la vez",
}


def _completar_descripciones() -> None:
    try:
        destino = getattr(config, "DESCRIPCIONES_CATEGORIAS", None)
        if isinstance(destino, dict):
            for cat, desc in _DESCRIPCIONES_EXTRA.items():
                destino.setdefault(cat, desc)
    except Exception as e:
        logger.warning(f"⚠️  no pude completar descripciones de categorías: {e}")


_completar_descripciones()


# ─── INICIALIZACIÓN ───────────────────────────────────────────────────────

def _seguro(descripcion: str, fn: Callable, *args) -> Any:
    try:
        return fn(*args)
    except Exception as e:
        logger.warning(f"⚠️  {descripcion}: {type(e).__name__}: {e}")
        return None


# Objetos con los que "abre/lee/edita..." se refiere a un ARCHIVO (no a una aplicación).
_VERBOS_ARCHIVO_APERTURA = ("abre", "abrir", "abreme", "lee", "leer", "edita", "editar", "visualiza", "muestra", "muestrame")
_OBJETOS_ARCHIVO = ("el archivo", "archivo", "el documento", "documento", "el pdf", "la imagen", "la foto")

# Frases (2+ palabras) que llevan a WhatsApp en la Capa 1.
_FRASES_WHATSAPP = {
    "manda un whatsapp", "mandale un whatsapp", "mandame un whatsapp", "envia un whatsapp",
    "enviale un whatsapp", "manda un mensaje por whatsapp", "manda un mensaje de whatsapp",
    "avisame por whatsapp", "por whatsapp", "mensajes de whatsapp", "mis mensajes de whatsapp",
    "tengo mensajes de whatsapp", "responde por whatsapp", "respondele por whatsapp",
}


def _desactivar_patrones_obsoletos() -> None:
    """Filas ya guardadas en la BD por siembras anteriores: keywords de UNA palabra en ABRIR_ARCHIVO
    ("abre", "muestra"...) empataban con ABRIR_APLICACION/TAREA y ganaban por orden de inserción."""
    try:
        from utils import normalizar_texto
        viejos = {normalizar_texto(v) for v in (getattr(config, "VERBOS_ABRIR_ARCHIVO", None) or ())
                  if len(normalizar_texto(v).split()) == 1}
        viejos |= {normalizar_texto(v) for v in _VERBOS_ARCHIVO_APERTURA}
        if not viejos:
            return
        marcas = ",".join("?" * len(viejos))
        with db.conectar() as conn:
            conn.execute(f"UPDATE patrones SET activo = 0 WHERE categoria = 'ABRIR_ARCHIVO' "
                         f"AND tipo = 'keyword' AND patron IN ({marcas})", tuple(viejos))
            conn.commit()
        if hasattr(db, "invalidar_caches"):
            db.invalidar_caches()
    except Exception as e:
        logger.warning(f"⚠️  desactivar patrones obsoletos: {type(e).__name__}: {e}")


def _sembrar_patrones() -> None:
    """Patrones de Capa 1 (equivale a main.sembrar_patrones_iniciales, sin importar main)."""
    def g(*nombres):
        s = set()
        for n in nombres:
            s |= set(getattr(config, n, None) or ())
        return s

    abrir_archivo = {f"{v} {o}" for v in _VERBOS_ARCHIVO_APERTURA for o in _OBJETOS_ARCHIVO}
    abrir_archivo |= {v for v in g("VERBOS_ABRIR_ARCHIVO") if len(v.split()) > 1}

    dominios = {
        "CREAR_CARPETA": g("VERBOS_CREAR_CARPETA"), "ELIMINAR_ARCHIVO": g("VERBOS_ELIMINAR_ARCHIVO"),
        "MOVER_ARCHIVO": g("VERBOS_MOVER_ARCHIVO"), "COPIAR_ARCHIVO": g("VERBOS_COPIAR_ARCHIVO"),
        "RENOMBRAR_ARCHIVO": g("VERBOS_RENOMBRAR_ARCHIVO"), "COMPRIMIR_ARCHIVO": g("VERBOS_COMPRIMIR_ARCHIVO"),
        "TAREA": g("VERBOS_TAREA", "VERBOS_TAREA_LISTAR", "VERBOS_TAREA_COMPLETAR", "VERBOS_TAREA_ELIMINAR"),
        "RECORDATORIO": g("VERBOS_RECORDATORIO", "VERBOS_RECORDATORIO_LISTAR", "VERBOS_RECORDATORIO_ELIMINAR"),
        "NOTA": g("VERBOS_NOTA", "VERBOS_NOTA_LISTAR", "VERBOS_NOTA_BUSCAR", "VERBOS_NOTA_ELIMINAR"),
        "COMANDO_SISTEMA": g("VERBOS_COMANDO_SISTEMA"), "INFO_IP": g("VERBOS_INFO_IP"),
        "INFO_BATERIA": g("VERBOS_INFO_BATERIA"), "INFO_RECURSOS": g("VERBOS_INFO_RECURSOS"),
        "INFO_NOMBRE": g("VERBOS_INFO_NOMBRE"), "ARCHIVO": g("VERBOS_ARCHIVO"),
        "ABRIR_ARCHIVO": abrir_archivo, "CARPETA_ESPECIAL": g("VERBOS_CARPETA_ESPECIAL"),
        "ABRIR_APLICACION": g("VERBOS_ABRIR_APLICACION"), "CERRAR_APLICACION": g("VERBOS_CERRAR_APLICACION"),
        "BUSCAR_EN_NAVEGADOR": g("VERBOS_BUSCAR_EN_NAVEGADOR"), "GENERAR_CODIGO": g("VERBOS_GENERAR_CODIGO"),
        "PREGUNTA_INFORMATIVA": g("VERBOS_PREGUNTA_INFORMATIVA"),
        "WHATSAPP": set(_FRASES_WHATSAPP),
    }
    _desactivar_patrones_obsoletos()
    n = 0
    for categoria, verbos in dominios.items():
        for verbo in verbos:
            db.agregar_patron(categoria, "keyword", verbo)
            n += 1
    for nombre_app in (getattr(config, "APLICACIONES_SISTEMA_FIJAS", None) or {}):
        for verbo in ("abre", "abrir", "ejecuta", "ejecutar", "inicia", "lanza"):
            db.agregar_patron("ABRIR_APLICACION", "keyword", f"{verbo} {nombre_app}")
            n += 1
    logger.info(f"✓ {n} patrones de Capa 1 sembrados")


# ─── AJUSTES QUE SOBREVIVEN AL REINICIO (sara_ajustes.json junto a config.py) ─────────────────────────
# Solo los interruptores de esta lista. Se aplican sobre `config` al arrancar; los comandos «/» los guardan al cambiarlos.
_AJUSTES_PERSISTENTES = ("IA_EXTERNA_PREFERIDA", "AUTO_OPTIMIZACION")


def _ruta_ajustes() -> str:
    base = getattr(config, "BASE_DIR", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(str(base), "sara_ajustes.json")


def _leer_ajustes() -> Dict[str, Any]:
    try:
        with open(_ruta_ajustes(), "r", encoding="utf-8") as f:
            datos = json.load(f)
        return datos if isinstance(datos, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"⚠️ sara_ajustes.json ilegible ({type(e).__name__}: {e}); se ignora")
        return {}


def _cargar_ajustes() -> None:
    datos = _leer_ajustes()
    for nombre in _AJUSTES_PERSISTENTES:
        if isinstance(datos.get(nombre), bool):
            setattr(config, nombre, datos[nombre])
            logger.info(f"⚙️ Ajuste restaurado: {nombre} = {datos[nombre]}")


def _guardar_ajuste(nombre: str, valor: bool) -> bool:
    if nombre not in _AJUSTES_PERSISTENTES:
        return False
    try:
        datos = _leer_ajustes()
        datos[nombre] = bool(valor)
        ruta = _ruta_ajustes()
        tmp = ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ruta)                      # escritura atómica: nunca queda un JSON a medias
        return True
    except Exception as e:
        logger.warning(f"⚠️ No pude guardar el ajuste {nombre}: {type(e).__name__}: {e}")
        return False

def _ganchos_optimizador() -> None:
    if optimizador is None or not hasattr(optimizador, "registrar_ganchos"):
        return

    def _disp() -> bool:
        return bool(conversacion is not None and
                    _seguro("conversacion._externa_disponible", getattr(conversacion, "_externa_disponible", lambda: False)))

    def _fijar(valor: bool) -> None:          # temporal: NO se guarda, al reiniciar vuelve a lo guardado
        config.IA_EXTERNA_PREFERIDA = bool(valor)

    def _activa() -> bool:
        return bool(getattr(config, "IA_EXTERNA_PREFERIDA", False))

    optimizador.registrar_ganchos(_disp, _fijar, _activa)
def inicializar() -> bool:
    """Crea tablas y siembra patrones (rápido). Lo pesado va a un hilo aparte."""
    global _listo
    _cargar_ajustes()
    try:
        for nombre, mod in (("database", db), ("productivity", productivity),
                            ("file_index", file_index), ("app_index", app_index),
                            ("shell", shell), ("habitos", habitos), ("generador_codigo", generador_codigo),
                            ("informativo", informativo), ("abridor", abridor), ("whatsapp", whatsapp),
                            ("telegram", telegram_bot), ("memoria_local", memoria_local)):
            if mod is not None and hasattr(mod, "crear_tablas"):
                _seguro(f"{nombre}.crear_tablas()", mod.crear_tablas)

        _seguro("sembrar patrones", _sembrar_patrones)

        for nombre, mod in (("memoria_core", memoria_core), ("habitos", habitos)):
            if mod is not None and hasattr(mod, "inicializar"):
                _seguro(f"{nombre}.inicializar()", mod.inicializar)
        if salud_sistema is not None and hasattr(salud_sistema, "iniciar_cache"):
            _seguro("salud_sistema.iniciar_cache()", salud_sistema.iniciar_cache)
            _seguro("ganchos optimizador", _ganchos_optimizador)
        threading.Thread(target=_arranque_pesado, name="sara-arranque", daemon=True).start()
        _listo = True
        return True
    except Exception as e:
        logger.error(f"✗ inicializar() falló: {e}", exc_info=True)
        return False


def _arranque_pesado() -> None:
    """Calentar el LLM e indexar: lento, nunca debe bloquear el servidor."""
    if voz is not None and hasattr(voz, "precalentar"):
        _seguro("voz.precalentar()", voz.precalentar)      # muletillas en caché → respuesta más rápida
    if navegador_activo is not None and hasattr(navegador_activo, "iniciar_rastreo"):
        _seguro("navegador_activo.iniciar_rastreo()", navegador_activo.iniciar_rastreo)   # aprende qué navegador usas más
    if getattr(config, "PULSO_CALOR_ACTIVO", True):
        threading.Thread(target=_pulso_calor, name="sara-calor", daemon=True).start()
    if hasattr(engine, "calentar_qwen"):
        _seguro("calentar_qwen()", engine.calentar_qwen)
    if app_index is not None and hasattr(app_index, "ejecutar_reindexado_aplicaciones"):
        _seguro("reindexado de apps", app_index.ejecutar_reindexado_aplicaciones)
    if file_index is not None and hasattr(file_index, "ejecutar_reindexado"):
        _seguro("reindexado de archivos", file_index.ejecutar_reindexado)
    if file_index is not None and hasattr(file_index, "iniciar_reindexado_automatico"):
        _seguro("reindexado auto archivos", file_index.iniciar_reindexado_automatico)
    if app_index is not None and hasattr(app_index, "iniciar_reindexado_automatico_aplicaciones"):
        _seguro("reindexado auto apps", app_index.iniciar_reindexado_automatico_aplicaciones)
    if whatsapp is not None and hasattr(whatsapp, "registrar_nucleo"):      # SARA-Net: órdenes remotas → núcleo
        _seguro("whatsapp.registrar_nucleo", whatsapp.registrar_nucleo,
                lambda t, e, voz=False: procesar_mensaje(t, e, "whatsapp", "voz" if voz else "texto"),
                resolver_confirmacion)
    if whatsapp is not None and hasattr(whatsapp, "arrancar"):
        _seguro("whatsapp.arrancar()", whatsapp.arrancar)   # reconecta solo si ya había un enlace
    if telegram_bot is not None and hasattr(telegram_bot, "registrar_nucleo"):   # Telegram: órdenes remotas → núcleo
        _seguro("telegram.registrar_nucleo", telegram_bot.registrar_nucleo,
                lambda t, e, voz=False: procesar_mensaje(t, e, "telegram", "voz" if voz else "texto"),
                resolver_confirmacion)
    if telegram_bot is not None and hasattr(telegram_bot, "arrancar"):
        _seguro("telegram.arrancar()", telegram_bot.arrancar)   # hilos listos si hay token (inactivos hasta /telegram net on)
    if celular is not None and hasattr(celular, "registrar_nucleo"):
        _seguro("celular.registrar_nucleo", celular.registrar_nucleo,
                lambda t, e, voz=False: procesar_mensaje(t, e, "celular", "voz" if voz else "texto"),
                resolver_confirmacion)
    if celular is not None and hasattr(celular, "arrancar"):
        _seguro("celular.arrancar()", celular.arrancar)
    if deshacer is not None and hasattr(deshacer, "purgar"):
        _seguro("deshacer.purgar()", deshacer.purgar)            # papelera de SARA: borra lo de más de 30 días
    if memoria_local is not None and hasattr(memoria_local, "purgar"):
        _seguro("memoria_local.purgar()", memoria_local.purgar)
    if alertas is not None and hasattr(alertas, "iniciar"):
        _seguro("alertas.iniciar()", alertas.iniciar)          # hilo de alertas al celular (respeta el maestro)
    logger.info("✓ Arranque pesado terminado (LLM calentado + índices)")

# ─── CONFIRMACIONES (bloquean SOLO el hilo del executor) ──────────────────

_pendientes: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def _pedir_confirmacion(emit: Callable, pregunta: str, timeout: int = TIMEOUT_CONFIRMACION_S) -> bool:
    id_conf = uuid.uuid4().hex[:8]
    slot = {"evento": threading.Event(), "respuesta": False, "opcion": None}
    with _lock:
        _pendientes[id_conf] = slot
    emit("solicitud_confirmacion", id_confirmacion=id_conf, pregunta=pregunta,
         opciones=[], timeout_segundos=timeout)
    _hablar(pregunta, emit)
    recibida = slot["evento"].wait(timeout)
    with _lock:
        _pendientes.pop(id_conf, None)
    if not recibida:
        emit("confirmacion_ya_resuelta", resultado="timeout")
        return False
    return bool(slot["respuesta"])


def _pedir_eleccion(emit: Callable, pregunta: str, opciones: List[str],
                    timeout: int = TIMEOUT_CONFIRMACION_S) -> Optional[int]:
    """Confirmación de elección múltiple: devuelve el índice elegido, o None (cancelado/timeout)."""
    id_conf = uuid.uuid4().hex[:8]
    slot = {"evento": threading.Event(), "respuesta": False, "opcion": None, "opciones": list(opciones)}
    with _lock:
        _pendientes[id_conf] = slot
    emit("solicitud_confirmacion", id_confirmacion=id_conf, pregunta=pregunta,
         opciones=list(opciones), timeout_segundos=timeout)
    _hablar(pregunta, emit)
    recibida = slot["evento"].wait(timeout)
    with _lock:
        _pendientes.pop(id_conf, None)
    if not recibida:
        emit("confirmacion_ya_resuelta", resultado="timeout")
        return None
    idx = slot.get("opcion")
    return idx if slot["respuesta"] and isinstance(idx, int) and 0 <= idx < len(opciones) else None


def resolver_confirmacion(id_confirmacion: str, respuesta: str, opcion: Any = None) -> bool:
    """Llamado desde el loop async (rápido, no bloquea). respuesta: 'si'/'no'; opcion: índice elegido."""
    with _lock:
        slot = _pendientes.get(id_confirmacion)
    if slot is None:
        logger.warning(f"Confirmación desconocida o expirada: {id_confirmacion}")
        return False
    logger.info(f"🔘 confirmación {id_confirmacion}: respuesta={respuesta!r} opcion={opcion!r} "
                f"opciones={slot.get('opciones')!r}")
    resp = str(respuesta).strip().lower()
    idx = None
    try:
        if opcion is not None and str(opcion).strip() != "":
            idx = int(opcion)
        elif resp.isdigit():
            idx = int(resp)                      # la GUI mandó el número como respuesta
    except (TypeError, ValueError):
        idx = None
    opciones = slot.get("opciones") or []
    if idx is None and opciones:                 # la GUI mandó el texto del botón
        textos = [str(o).strip().lower() for o in opciones]
        for candidato in (resp, str(opcion).strip().lower() if opcion is not None else ""):
            if candidato and candidato in textos:
                idx = textos.index(candidato)
                break
    slot["opcion"] = idx
    slot["respuesta"] = resp in _AFIRMATIVOS or idx is not None
    slot["evento"].set()
    return True


# ─── UTILIDADES DE DESPACHO ───────────────────────────────────────────────

_AVISADAS: set = set()


def _llamar(modulo, funcion: str, *args, **kwargs):
    """Llama modulo.funcion(...). Devuelve _NO_DISPONIBLE si no existe (avisa UNA vez)."""
    if modulo is None or not hasattr(modulo, funcion):
        if modulo is not None and (modulo.__name__, funcion) not in _AVISADAS:
            _AVISADAS.add((modulo.__name__, funcion))
            logger.warning(f"⚠️  {modulo.__name__}.{funcion}() no existe en el archivo actual")
        return _NO_DISPONIBLE
    return getattr(modulo, funcion)(*args, **kwargs)


def _aprender(res: dict, exito: bool) -> None:
    if hasattr(engine, "ajustar_aprendizaje_por_resultado"):
        _seguro("ajustar_aprendizaje_por_resultado", engine.ajustar_aprendizaje_por_resultado, res, exito)


def _registrar_habito(dominio: str, ruta: str, nombre: str) -> None:
    """habitos.registrar_ejecucion(dominio, identificador, nombre_mostrar): el identificador es la
    RUTA (así ejecutar_paquete puede reabrirlo); el nombre es solo para mostrar."""
    if habitos is None:
        return
    if hasattr(habitos, "registrar_ejecucion"):
        _seguro("habitos.registrar_ejecucion", habitos.registrar_ejecucion, dominio, ruta or nombre, nombre)
    if hasattr(habitos, "detectar_y_registrar_combo"):
        _seguro("habitos.detectar_y_registrar_combo", habitos.detectar_y_registrar_combo)


def _habito_app(acc: dict) -> None:
    """Registra el hábito según lo que se abrió: aplicación (ruta_ejecutable) o archivo/carpeta (ruta)."""
    mejor = acc.get("_mejor_coincidencia")
    if not (acc.get("abierto") and mejor):
        return
    if "ruta_ejecutable" in mejor:
        _registrar_habito("aplicacion", mejor.get("ruta_ejecutable", ""), mejor.get("nombre", "aplicación"))
    elif "ruta" in mejor:
        _registrar_habito("archivo", mejor.get("ruta", ""), mejor.get("nombre", "archivo"))


def _abrir_app_fallback(res: dict) -> Optional[dict]:
    """Solo si app_actions.ejecutar_desde_clasificacion no existe."""
    verbos = set(getattr(config, "VERBOS_ABRIR_APLICACION", []) or
                 ["abre", "abrir", "ejecuta", "ejecutar", "inicia", "iniciar", "lanza"])
    palabras = res.get("texto_norm", "").split()
    while palabras and (palabras[0] in verbos or palabras[0] in ("la", "el", "los", "las", "mi")):
        palabras.pop(0)
    nombre = " ".join(palabras)
    if not nombre:
        return {"abierto": False, "texto": "¿Qué aplicación quieres abrir?"}
    ok = bool(app_actions.abrir_aplicacion(nombre))
    return {"abierto": ok, "texto": f"Abriendo {nombre}..." if ok else f"No encontré la aplicación '{nombre}'."}


def _reintentar_como_objeto(res: dict) -> Optional[dict]:
    """'abre informe' / 'abre sara2' clasificado como ABRIR_APLICACION pero no es una app:
    se reintenta como carpeta y luego como archivo. Devuelve el resultado que abrió algo, un aviso de
    ambigüedad, o None si nada coincidió (se conserva el mensaje original de la app)."""
    textos: List[str] = []
    for cat_alt, funcion in (("CARPETA_ESPECIAL", "ejecutar_carpeta_especial"),
                             ("ABRIR_ARCHIVO", "ejecutar_desde_clasificacion")):
        r = _llamar(file_actions, funcion, dict(res, categoria=cat_alt))
        if r is None or r is _NO_DISPONIBLE:
            continue
        if r.get("abierto"):
            return r
        textos.append(str(r.get("texto", "")))
    for t in textos:
        if t.startswith("Encontré varias"):
            return {"abierto": False, "texto": t}
    return None


def _apertura_inteligente(res: dict, emit: Callable) -> Optional[Tuple[bool, str]]:
    """Abrir archivo/carpeta con carpeta contenedora y elección de editor. None = no es su asunto → flujo de siempre.
    El módulo RESUELVE y pregunta (modal); aquí se hacen las preguntas y se ejecuta (Principio 8)."""
    plan = _seguro("abridor.resolver_apertura", abridor.resolver_apertura, res)
    if not plan:
        return None
    while plan and plan.get("estado") in ("elegir", "confirmar"):
        if plan["estado"] == "elegir":
            idx = _pedir_eleccion(emit, plan["pregunta"], plan["opciones"])
            if idx is None:
                _aprender(res, True)
                return True, "Cancelado."
            plan = _seguro("abridor.continuar", plan["continuar"], idx)
        else:
            ok = _pedir_confirmacion(emit, plan["pregunta"])
            plan = _seguro("abridor.continuar", plan["continuar"], ok)
    if not plan:
        _aprender(res, False)
        return False, "No pude continuar con la apertura."
    est = plan.get("estado")
    if est == "abrir":
        r = _seguro("abridor.ejecutar_apertura", abridor.ejecutar_apertura, plan) or \
            {"exito": False, "texto": "No pude abrirlo."}
        _aprender(res, bool(r.get("exito")))
        if r.get("exito") and not r.get("no_habito"):
            _registrar_habito("archivo", r.get("ruta", ""), r.get("nombre", "archivo"))
        return bool(r.get("exito")), r.get("texto", "")
    if est == "cancelado":
        _aprender(res, True)
        return True, plan.get("texto", "Cancelado.")
    _aprender(res, False)                       # no_encontrado / no_entendido / rechazado
    return False, plan.get("texto", "No pude abrirlo.")


def _despachar_whatsapp(res: dict, emit: Callable) -> Optional[Tuple[bool, str]]:
    """WhatsApp: el módulo RESUELVE (contacto, mensaje); aquí se hacen las preguntas (modal) y se envía.
    Todo envío a un contacto exige confirmación explícita. None = no es asunto suyo → sigue el flujo."""
    plan = _seguro("whatsapp.resolver", whatsapp.resolver, res)
    if not plan:
        return None
    while plan and plan.get("estado") == "elegir":
        idx = _pedir_eleccion(emit, plan["pregunta"], plan["opciones"])
        if idx is None:
            _aprender(res, True)
            return True, "Cancelado."
        plan = _seguro("whatsapp.continuar", plan["continuar"], idx)
    if not plan:
        _aprender(res, False)
        return False, "No pude continuar con el envío."
    if plan.get("estado") == "candidato":
        if not _pedir_confirmacion(emit, plan["mensaje_confirmacion"]):
            _aprender(res, True)
            return True, "Cancelado, no envié nada."
        r = _seguro("whatsapp.ejecutar_envio", whatsapp.ejecutar_envio, plan) or \
            {"exito": False, "texto": "No pude enviarlo."}
        _aprender(res, bool(r.get("exito")))
        return bool(r.get("exito")), r.get("texto", "")
    ok = bool(plan.get("exito", plan.get("estado") in ("ok", "ejecutado")))
    _aprender(res, ok)
    return ok, plan.get("texto", "")


# ─── POLÍTICA DE LOS CANALES REMOTOS ──────────────────────────────────────

def _lista_cfg(nombre: str, defecto: tuple = ()) -> frozenset:
    return frozenset(str(x).strip().upper() for x in (getattr(config, nombre, defecto) or ()))


def _categoria_remota_bloqueada(cat: str, origen: Optional[str]) -> bool:
    """¿Esta categoría NO puede ejecutarse por el canal remoto `origen`? Reglas, en este orden:
    1) lista blanca (REMOTO_CATEGORIAS_PERMITIDAS): si tiene contenido, lo que no esté ahí se bloquea;
    2) lista negra común (REMOTO_CATEGORIAS_BLOQUEADAS) + la propia del canal (WA_NET_ / TG_CATEGORIAS_BLOQUEADAS)."""
    cat = str(cat or "").strip().upper()
    permitidas = _lista_cfg("REMOTO_CATEGORIAS_PERMITIDAS")
    if permitidas and cat not in permitidas:
        return True
    bloqueadas = set(_lista_cfg("REMOTO_CATEGORIAS_BLOQUEADAS", ("COMANDO_SISTEMA", "ELIMINAR_ARCHIVO")))
    if origen == "whatsapp":
        bloqueadas |= _lista_cfg("WA_NET_CATEGORIAS_BLOQUEADAS", ("COMANDO_SISTEMA", "ELIMINAR_ARCHIVO"))
    elif origen == "telegram":
        bloqueadas |= _lista_cfg("TG_CATEGORIAS_BLOQUEADAS", ("COMANDO_SISTEMA", "ELIMINAR_ARCHIVO"))
    return cat in bloqueadas


def _slash_remoto_permitido(texto: str) -> bool:
    """Los comandos «/» (ajustes, vincular cuentas, apagar el puente…) solo valen desde la ventana de SARA.
    Un canal remoto únicamente puede mandar los de solo lectura de REMOTO_SLASH_PERMITIDOS (por defecto «/salud»)."""
    partes = (texto or "").split()
    if not partes:
        return False
    permitidos = {str(x).strip().lower() for x in (getattr(config, "REMOTO_SLASH_PERMITIDOS", ("/salud",)) or ())}
    return partes[0].lower() in permitidos


# ─── DESPACHO POR CATEGORÍA (mismo orden que main.py) ─────────────────────

def _despachar(res: dict, emit: Callable) -> Tuple[bool, str]:
    cat = res.get("categoria", "DESCONOCIDA")
    res.setdefault("entrada", getattr(_ctx, "entrada", None))    # «voz» | «texto»: lo usa web_navigator para decidir si limpia
    logger.debug(f"despacho: cat={cat} conf={res.get('confianza')} texto_norm='{res.get('texto_norm', '')}'")

    # Canales remotos (WhatsApp / Telegram): hay acciones que no se ejecutan desde fuera del PC (ver config.REMOTO_*)
    _og = getattr(_ctx, "origen", None)
    if _og in _ORIGENES_REMOTOS and _categoria_remota_bloqueada(cat, _og):
        logger.warning(f"🚫 Canal remoto ({_og}): categoría {cat} bloqueada")
        return False, f"Esa acción no está permitida por {('WhatsApp' if _og == 'whatsapp' else 'Telegram' if _og == 'telegram' else 'el celular')}. Pídemela desde el PC."
    # 0) WhatsApp (mandar / responder / leer). None si la frase no es de su incumbencia.
    if whatsapp is not None and cat == "WHATSAPP":
        r = _despachar_whatsapp(res, emit)
        if r is not None:
            return r

    # 1) Comandos personalizados
    if habitos is not None and cat == "EJECUTAR_PAQUETE":
        pid = _seguro("buscar_paquete_fijo_por_nombre", habitos.buscar_paquete_fijo_por_nombre,
                      res.get("texto_norm", ""))
        if pid:
            r = habitos.ejecutar_paquete(pid)
            _aprender(res, r["exitoso"])
            return r["exitoso"], r["texto"]
        _aprender(res, False)
        return False, "No encontré ese comando personalizado."

    # 2) Productividad
    acc = _llamar(productivity, "registrar_desde_clasificacion", res)
    if acc not in (None, _NO_DISPONIBLE):
        est = acc.get("estado")
        if est == "candidato_eliminar":
            if not _pedir_confirmacion(emit, acc["texto"]):
                _aprender(res, True)
                return True, "Cancelado."
            c = productivity.confirmar_eliminacion(acc["tipo"], acc["id"])
            _aprender(res, c["exito"])
            return c["exito"], c["texto"]
        ok = est in ("creado", "completado", "listado")
        _aprender(res, ok)
        return ok, acc["texto"]

    # 3) Código generado / informativo (opcionales)
    if generador_codigo is not None:
        accion_codigo = _llamar(generador_codigo, "resolver_generar_codigo", res)
        if accion_codigo not in (None, _NO_DISPONIBLE):
            exito = accion_codigo.get("estado", "") in ("generado", "reutilizado")
            _aprender(res, exito)
            return exito, accion_codigo.get("texto", "Código procesado.")

    if informativo is not None:
        acc = _llamar(informativo, "resolver_pregunta_informativa", res)
        if acc not in (None, _NO_DISPONIBLE):
            _aprender(res, acc.get("exito", True))
            return acc.get("exito", True), acc["texto"]

    # 4) Información del sistema
    acc = _llamar(system_info, "responder_desde_clasificacion", res)
    if acc not in (None, _NO_DISPONIBLE):
        _aprender(res, acc.get("exito", True))
        return acc.get("exito", True), acc["texto"]

    # 4b) Apertura inteligente (archivo + carpeta contenedora, elección de editor). Devuelve None si la frase
    #     no es de su incumbencia ("abre chrome") y entonces todo sigue exactamente como antes.
    if abridor is not None and cat in ("ABRIR_ARCHIVO", "ABRIR_APLICACION", "CARPETA_ESPECIAL"):
        r = _apertura_inteligente(res, emit)
        if r is not None:
            return r

    # 5) Carpetas especiales
    acc = _llamar(file_actions, "ejecutar_carpeta_especial", res)
    if acc not in (None, _NO_DISPONIBLE):
        _aprender(res, acc["abierto"])
        _habito_app(acc)
        return acc["abierto"], acc["texto"]

    # 5b) Navegadores y sitios web: ANTES que las apps genéricas. web_navigator devuelve None si la
    #     frase no es asunto suyo ("abre spotify"), y entonces se sigue con app_actions.
    if web_navigator is not None and cat in ("ABRIR_APLICACION", "BUSCAR_EN_NAVEGADOR"):
        acc = _llamar(web_navigator, "ejecutar_desde_clasificacion", res)
        if acc not in (None, _NO_DISPONIBLE):
            _aprender(res, acc["abierto"])
            _habito_app(acc)
            return acc["abierto"], acc["texto"]

    # 6) Abrir aplicación (si no es una app, se reintenta como carpeta / archivo)
    acc = _llamar(app_actions, "ejecutar_desde_clasificacion", res)
    if acc is _NO_DISPONIBLE and cat == "ABRIR_APLICACION" and app_actions is not None:
        acc = _abrir_app_fallback(res)
    if acc not in (None, _NO_DISPONIBLE):
        if cat == "ABRIR_APLICACION" and not acc.get("abierto"):
            # "abre youtube" sin app de escritorio → sitio web; si tampoco, carpeta / archivo.
            web = _llamar(web_navigator, "ejecutar_desde_clasificacion", res, True) if web_navigator else None
            if web not in (None, _NO_DISPONIBLE) and web.get("abierto"):
                alt = web
            else:
                alt = _reintentar_como_objeto(res)
            if alt is not None:
                acc = alt
        _aprender(res, acc["abierto"])
        _habito_app(acc)
        return acc["abierto"], acc["texto"]

    # 7) Búsqueda en navegador (con reintento como app, igual que main.py)
    if web_navigator is not None and cat == "BUSCAR_EN_NAVEGADOR":
        acc = _llamar(web_navigator, "ejecutar_desde_clasificacion", res)
        if acc not in (None, _NO_DISPONIBLE):
            _aprender(res, acc["abierto"])
            return acc["abierto"], acc["texto"]
        if acc is None and app_actions is not None:
            # El navegador lo rechazó ("abre spotify" mal clasificado): reintentar como app.
            res_app = dict(res, categoria="ABRIR_APLICACION")
            acc = _llamar(app_actions, "ejecutar_desde_clasificacion", res_app)
            if acc not in (None, _NO_DISPONIBLE):
                _aprender(res, acc["abierto"])
                _habito_app(acc)
                return acc["abierto"], acc["texto"]

    # 8) Cerrar aplicación (pide confirmación)
    cierre = _llamar(app_actions, "resolver_cierre", res)
    if cierre not in (None, _NO_DISPONIBLE):
        est = cierre["estado"]
        if est in ("protegido", "ambiguo", "no_encontrado"):
            if est != "protegido":
                _aprender(res, False)
            return False, cierre["texto"]
        if est == "candidato":
            if not _pedir_confirmacion(emit, cierre["mensaje_confirmacion"]):
                _aprender(res, True)
                return True, "Se canceló el cierre."
            r = app_actions.cerrar_procesos(
                cierre["pids"], timeout_segundos=getattr(config, "TIMEOUT_CIERRE_NORMAL_SEGUNDOS", 5))
            _aprender(res, True)
            return True, r["texto"]

    # 9) Comandos de sistema (shell)
    cmd = _llamar(shell, "resolver_comando_sistema", res) if _og not in _ORIGENES_REMOTOS else None
    if cmd not in (None, _NO_DISPONIBLE):
        est = cmd["estado"]
        if est == "requiere_consola":
            cands = app_index.buscar_aplicacion(cmd["programa"], limite=1) if app_index else []
            if cands:
                ok = bool(app_actions.abrir_aplicacion(cands[0]))
                _aprender(res, ok)
                return ok, (f"Abriendo {cands[0]['nombre']} en su propia consola." if ok
                            else f"Encontré {cands[0]['nombre']} pero no pude abrirlo.")
            _aprender(res, False)
            return False, cmd["texto"]
        if est in ("rechazado", "no_entendido"):
            _aprender(res, False)
            return False, cmd["texto"]
        if est == "ejecutado":
            _aprender(res, cmd["exito"])
            return cmd["exito"], cmd["texto"]
        if est == "candidato":
            if not _pedir_confirmacion(emit, cmd["mensaje_confirmacion"]):
                _aprender(res, True)
                return True, "Se canceló el comando."
            ej = shell.confirmar_y_ejecutar(cmd["comando"], cmd["zona"], cmd["traduccion"], res["texto_original"])
            _aprender(res, ej["exitoso"])
            return ej["exitoso"], ej["resumen"]

    # 10) Gestión de archivos (crear/copiar/mover/eliminar/renombrar/comprimir)
    ges = _llamar(file_manager, "resolver_gestion_archivo", res)
    if ges not in (None, _NO_DISPONIBLE):
        est = ges["estado"]
        if est in ("no_entendido", "no_encontrado", "ambiguo", "rechazado", "no_escribible"):
            _aprender(res, False)
            return False, ges["texto"]
        if est == "ejecutado":
            _aprender(res, ges.get("exito", True))
            return ges.get("exito", True), ges["texto"]
        if est == "candidato":
            if not _pedir_confirmacion(emit, ges["mensaje_confirmacion"]):
                _aprender(res, True)
                return True, "Cancelado."
            c = file_manager.confirmar_gestion_archivo(ges["accion"], ges)
            _aprender(res, c["exito"])
            return c["exito"], c["texto"]

    # 11) Abrir archivo
    acc = _llamar(file_actions, "ejecutar_desde_clasificacion", res)
    if acc not in (None, _NO_DISPONIBLE):
        _aprender(res, acc["abierto"])
        _habito_app(acc)
        return acc["abierto"], acc["texto"]

    # 12) Buscar archivo
    acc = _llamar(file_index, "responder_desde_clasificacion", res)
    if acc not in (None, _NO_DISPONIBLE):
        _aprender(res, acc.get("exito", True))
        mejor = acc.get("_mejor_coincidencia")
        if acc.get("exito", False) and mejor:
            _registrar_habito("archivo", mejor.get("ruta", ""), mejor.get("nombre", "archivo"))
        return acc.get("exito", True), acc["texto"]

    if cat == "DESCONOCIDA":
        return False, ("No entendí eso. Escribe /ayuda para ver lo que sé hacer; "
                       "si sabes qué debía pasar, usa /corregir CATEGORIA (lista en /categorias).")
    return False, f"No supe qué hacer con eso (categoría: {cat})."


# ─── COMANDOS "/" (los mismos del REPL de main.py) ────────────────────────

_AYUDA = """Comandos disponibles:
/ayuda — esta ayuda
/voz — prueba y diagnóstico de voz (/voz on · /voz off)
/stt — diagnóstico de la voz de entrada (micrófono y modelo)
/conv — puente conversacional (IA externa): estado · /conv on · /conv off · /conv olvidar
/limpieza — reintento único con la frase limpiada por la IA externa (/limpieza on|off)
/ia — de dónde sale la limpieza de frases y búsquedas: /ia externa (rápido, sin Ollama) · /ia local · /ia (estado)
/abridor — editores detectados y lo aprendido · /abridor olvidar
/navegador — navegador que SARA usa si no dices cuál (abierto / más usado) · /navegador olvidar
/whatsapp — enlazar y usar WhatsApp (/whatsapp: número · estado · notificar on|off · desconectar)
/telegram — bot de Telegram como control remoto (vincular · net on|off · voz on|off · audio on|off · bocina on|off · estado)
/alertas — alertas al celular: on · off · estado (también: «silencia las alertas»)
/descripcion — IA lee la pantalla (error, «qué pasa»): /descripcion on · off · estado (también por Telegram)
/verificacion — pide el código del autenticador: /verificacion on · off · estado (apagarla solo desde aquí)
/deshacer — revierte la última operación sobre archivos (también: «deshaz lo último»)
/papelera — papelera reversible de SARA (/papelera vaciar confirmar borra definitivo)
/modos — modos disponibles · /modo NOMBRE (activar) · /modo nuevo NOMBRE = acción ; acción · /modo borrar NOMBRE
/donde — Wi-Fi, IP, batería de esta laptop (por Telegram: con código)
/foto — foto de la webcam (solo por Telegram, con código)
/pantalla — captura de la pantalla (solo por Telegram, con código)
/bloquear — bloquea la sesión de Windows (por Telegram: con botón)
/confirmar — marca la última clasificación como correcta
/validar — guarda la última clasificación como regla permanente
/corregir CATEGORIA — corrige la última clasificación
/categorias — lista las categorías que SARA reconoce
/reindexar — vuelve a escanear las carpetas
/reindexar_apps — vuelve a descubrir aplicaciones
/stats — estadísticas de aprendizaje
/calibrar — aciertos por nivel de confianza
/salud — CPU, RAM, disco y batería
/auto — SARA optimiza sola el equipo: /auto on · /auto off · /auto estado · /auto proteger APP · /auto soltar APP
/optimizar — diagnóstico de RAM/CPU y qué haría (/optimizar ahora · /optimizar cerrar · /optimizar ia)
/habitos — estadísticas de hábitos
/fixture — fixture de regresión
Cualquier otra cosa se envía al motor de clasificación."""


def _en_hilo(nombre: str, fn: Callable) -> None:
    def _run():
        r = _seguro(nombre, fn)
        logger.info(f"✓ {nombre} terminó: {r}")
    threading.Thread(target=_run, name=f"sara-{nombre}", daemon=True).start()


# Comandos cuya salida es larga/técnica: se muestran en pantalla pero no se leen en voz alta.
# (/whatsapp se excluye aparte con startswith: admite "/whatsapp: 52..." y muestra un código de enlace.)
_SLASH_SIN_VOZ = {"/ayuda", "/help", "/fixture", "/categorias", "/calibrar", "/voz", "/stt", "/conv", "/abridor", "/navegador", "/limpieza", "/ia", "/celular"}


def _comando_slash(texto: str) -> Optional[Tuple[bool, str]]:
    if not texto.startswith("/"):
        return None
    if texto.lower().startswith("/whatsapp"):          # acepta "/whatsapp", "/whatsapp: 52..." y "/whatsapp:52..."
        if whatsapp is None:
            return False, "El módulo de WhatsApp no está disponible (revisa whatsapp.py y el log)."
        return whatsapp.comando_slash(texto[len("/whatsapp"):])
    if texto.lower().startswith("/telegram"):
        if telegram_bot is None:
            return False, "El módulo de Telegram no está disponible (revisa telegram_bot.py y el log)."
        return telegram_bot.comando_slash(texto[len("/telegram"):])
    partes = texto.split(maxsplit=1)
    cmd, arg = partes[0].lower(), (partes[1].strip() if len(partes) > 1 else "")
    ult = _ultimo_res

    if cmd in ("/ayuda", "/help"):
        return True, f"{_AYUDA}\n\n(core_bridge {CORE_BRIDGE_VERSION})"
    if cmd == "/telegram":
        if telegram_bot is None:
            return False, "El módulo de Telegram no está disponible (revisa telegram_bot.py y el log)."
        return telegram_bot.comando_slash(texto[len("/telegram"):])
    if cmd == "/celular":
        if celular is None:
            return False, "celular.py no está disponible (revisa el log)."
        return celular.comando_slash(arg)
    partes = texto.split(maxsplit=1)
    if cmd == "/voz":
        if voz is None:
            return False, "El módulo de voz no está disponible (revisa voz.py y el log)."
        a = arg.lower()
        if a in ("off", "apagar", "silencio"):
            voz.silenciar()
            return True, "Voz desactivada. Seguiré contestando por texto."
        if a in ("on", "activar"):
            voz.activar()
            return True, "Voz activada."
        return True, voz.probar()          # /voz → diagnóstico + frase de prueba
    if cmd == "/stt":
        if stt is None:
            return False, "El módulo de voz de entrada no está disponible (revisa stt.py y el log)."
        return True, stt.diagnostico()
    if cmd == "/conv":
        if conversacion is None:
            return False, "El puente conversacional no está disponible (revisa conversacion.py y el log)."
        a = arg.lower()
        if a in ("off", "apagar"):
            conversacion.set_activa(False)
            return True, "Puente conversacional apagado: todo va directo al motor de SARA."
        if a in ("on", "activar"):
            conversacion.set_activa(True)
            return True, "Puente conversacional activado."
        if a in ("olvidar", "reset", "limpiar"):
            conversacion.olvidar()
            return True, "Listo, olvidé la conversación reciente."
        return True, conversacion.diagnostico()
    if cmd == "/abridor":
        if abridor is None:
            return False, "El abridor inteligente no está disponible (revisa abridor_inteligente.py y el log)."
        if arg.lower() in ("olvidar", "reset", "limpiar"):
            return True, f"Listo, olvidé {abridor.olvidar()} cosa(s) aprendida(s)."
        return True, abridor.diagnostico()
    if cmd == "/navegador":
        if navegador_activo is None:
            return False, "El navegador inteligente no está disponible (revisa navegador_activo.py y el log)."
        if arg.lower() in ("olvidar", "reset", "limpiar"):
            return True, f"Listo, olvidé {navegador_activo.olvidar()} registro(s) de uso de navegadores."
        inst = web_navigator.detectar_navegadores_instalados() if web_navigator is not None else None
        return True, navegador_activo.diagnostico(inst)
    if cmd == "/limpieza":
        a = arg.lower()
        if a in ("on", "activar", "off", "apagar"):
            config.REINTENTO_LIMPIEZA_ACTIVO = a in ("on", "activar")
        puede = conversacion is not None and hasattr(conversacion, "propuestas_limpieza")
        diag = conversacion.diagnostico_limpieza() if puede and hasattr(conversacion, "diagnostico_limpieza") else "-"
        return True, (f"Reintento con limpieza de frase: {'ACTIVO' if getattr(config, 'REINTENTO_LIMPIEZA_ACTIVO', True) else 'apagado'} · "
                      f"función de IA en conversacion.py: {'sí' if puede else 'NO (falta propuestas_limpieza)'} · "
                      f"correcciones fonéticas locales: {len(getattr(config, 'CORRECCIONES_FONETICAS', {}) or {})}\n"
                      f"{diag}\n"
                      "Uso: /limpieza on|off")
    if cmd == "/memoria":
        if memoria_local is None:
            return False, "memoria_local.py no está disponible (revisa el log)."
        if arg.lower() in ("olvidar", "reset", "limpiar"):
            return True, f"Listo, olvidé {memoria_local.olvidar()} frase(s) y dato(s) aprendidos."
        return True, memoria_local.diagnostico()
    if cmd == "/ia":
        a = arg.lower().strip()
        if a in ("externa on", "on externa"):
            a = "externa"
        elif a in ("externa off", "off externa"):
            a = "local"
        if a in ("externa", "on", "activar", "rapido", "rápido"):
            config.IA_EXTERNA_PREFERIDA = True
        elif a in ("local", "off", "apagar"):
            config.IA_EXTERNA_PREFERIDA = False
        elif a not in ("", "estado"):
            return True, "Uso: /ia externa (o /ia on) · /ia local (o /ia off) · /ia (ver estado)"
        guardado = ""
        if a not in ("", "estado"):
            guardado = " (guardado: se mantiene al reiniciar)" if _guardar_ajuste(
                "IA_EXTERNA_PREFERIDA", bool(config.IA_EXTERNA_PREFERIDA)) else " (¡no pude guardarlo! se perderá al reiniciar)"
        if conversacion is None:
            return True, "conversacion.py no está cargado: este switch no tiene efecto."
        ext_ok = bool(_seguro("conversacion._externa_disponible", getattr(conversacion, "_externa_disponible", lambda: False)))
        modo = "EXTERNA (rápido, sin esperar a Ollama)" if getattr(config, "IA_EXTERNA_PREFERIDA", False) \
            else "LOCAL primero y luego externa (como siempre)"
        return True, (f"Modo de limpieza de frases y búsquedas: {modo}{guardado}\n"
                      f"IA externa disponible ahora: {'sí' if ext_ok else 'NO (sin internet, sin claves o en pausa)'}\n"
                      "Cubre: limpiar frases mal dichas/escritas, las búsquedas web y el árbitro de clasificación (cuando "
                      "los patrones y el vectorial no deciden). Ollama queda de reserva. NO cubre la transcripción del "
                      "audio (Whisper), que es local.\n"
                      "Uso: /ia externa · /ia local · /ia (estado)")
    if cmd == "/auto":
        if optimizador is None:
            return False, "El optimizador no está disponible (revisa optimizador.py y el log)."
        return optimizador.comando_auto(arg, lambda v: _guardar_ajuste("AUTO_OPTIMIZACION", v))
    if cmd == "/optimizar":
        if optimizador is None:
            return False, "El optimizador no está disponible (revisa optimizador.py y el log)."
        return optimizador.comando_optimizar(arg)
    if cmd in ("/descripcion", "/descripción"):
        return interruptores.comando_descripcion(arg) if interruptores is not None else (False, "interruptores.py no está disponible.")
    if cmd in ("/verificacion", "/verificación"):
        return interruptores.comando_verificacion(arg) if interruptores is not None else (False, "interruptores.py no está disponible.")
    if cmd == "/papelera":
        return deshacer.comando_papelera(arg) if deshacer is not None else (False, "deshacer.py no está disponible.")
    if cmd in ("/modos", "/modo"):
        return modos.comando(arg) if modos is not None else (False, "modos.py no está disponible.")
    if cmd == "/donde":
        return (True, antirrobo.donde_texto()) if antirrobo is not None else (False, "antirrobo.py no está disponible.")
    if cmd in ("/foto", "/pantalla"):
        return False, f"{cmd} solo se pide por Telegram (con tu código). Aquí ya ves la pantalla."
    if cmd == "/alertas":
        return alertas.comando(arg) if alertas is not None else (False, "alertas.py no está disponible.")
    if cmd == "/categorias":
        cats = sorted(set(db.obtener_categorias_registradas()) | {"DESCONOCIDA"})
        return True, "Categorías conocidas:\n" + ", ".join(cats)
    if cmd == "/stats":
        s = db.obtener_stats_intenciones_aprendidas()
        return True, f"Intenciones aprendidas: {s.get('total', 0)} · usos totales: {s.get('usos_totales', 0)}"
    if cmd == "/calibrar":
        filas = db.obtener_calibracion_confianza()
        if not filas:
            return True, "Aún no hay clasificaciones confirmadas/corregidas suficientes. Usa /confirmar y /corregir."
        umbral = getattr(config, "UMBRAL_CONFIANZA_QWEN", 0.65)
        lin = [f"Calibración del árbitro (umbral actual: {umbral}):"]
        for f in filas:
            marca = " ← umbral" if f["bucket_desde"] <= umbral < f["bucket_hasta"] else ""
            lin.append(f"[{f['bucket_desde']:.2f}-{f['bucket_hasta']:.2f}) {f['aciertos']}/{f['total_evaluado']} "
                       f"({f['pct_acierto']}%){marca}")
        return True, "\n".join(lin)
    if cmd == "/salud":
        if salud_sistema is None or not hasattr(salud_sistema, "reporte_salud_para_usuario"):
            return False, "No puedo leer la salud del sistema ahora mismo."
        return True, str(salud_sistema.reporte_salud_para_usuario())
    if cmd == "/habitos":
        if habitos is None:
            return False, "El módulo de hábitos no está disponible."
        return True, "\n".join(f"{k.replace('_', ' ')}: {v}" for k, v in habitos.obtener_estadisticas_habitos().items())
    if cmd == "/fixture":
        js = json.dumps(db.generar_fixture_regresion(), ensure_ascii=False, indent=2)
        return True, js if len(js) < 4000 else js[:4000] + "\n… (recortado)"
    if cmd == "/reindexar":
        if file_index is None:
            return False, "file_index no disponible."
        _en_hilo("reindexado de archivos", file_index.ejecutar_reindexado)
        return True, "Reindexando archivos en segundo plano…"
    if cmd == "/reindexar_apps":
        if app_index is None:
            return False, "app_index no disponible."
        _en_hilo("reindexado de apps", app_index.ejecutar_reindexado_aplicaciones)
        return True, "Redescubriendo aplicaciones en segundo plano…"
    if cmd in ("/confirmar", "/validar", "/corregir"):
        if not ult or not ult.get("id_historial"):
            return False, "No hay una clasificación previa."
        if cmd == "/confirmar":
            engine.confirmar_clasificacion(ult["id_historial"])
            return True, "Confirmado."
        if cmd == "/validar":
            ok = engine.validar_clasificacion(ult["id_historial"], ult["texto_original"], ult["categoria"])
            return bool(ok), (f"Validado: '{ult['texto_original']}' es ahora regla permanente." if ok
                              else "No pude guardar la validación.")
        if not arg:
            return False, "Uso: /corregir CATEGORIA_CORRECTA (lista en /categorias)"
        cat = arg.upper()
        ok = engine.corregir_clasificacion(ult["id_historial"], ult["texto_original"], ult["categoria"],
                                           cat, ult.get("confianza", 0.0))
        if ok:
            return True, f"Corregido: se aprende como {cat}."
        return False, f"'{cat}' no es una categoría conocida: {', '.join(sorted(db.obtener_categorias_registradas()))}"
    return False, f"Comando desconocido: {cmd}. Escribe /ayuda."


# ─── EVENTOS PROACTIVOS (sugerencias, alertas, recordatorios) ─────────────

_alertas_enviadas: Dict[str, float] = {}
REENVIO_ALERTA_S = 600


def _sugerencia_permitida(sug: dict) -> Tuple[bool, str]:
    """Mismas reglas que main.py: restricción dura → no mostrar; guardia 🔴 → no mostrar; ⚠️ → cambia el texto."""
    miembros = sug.get("miembros", [])
    texto = sug.get("texto", "")
    if memoria_core is not None and hasattr(memoria_core, "verificar_restriccion"):
        for m in miembros:
            if m.get("dominio") in ("aplicacion", "archivo") and \
                    _seguro("verificar_restriccion", memoria_core.verificar_restriccion,
                            m.get("dominio"), m.get("identificador", "")):
                return False, ""
    if salud_sistema is not None and hasattr(salud_sistema, "evaluar_guardias_para_paquete"):
        g = _seguro("evaluar_guardias_para_paquete", salud_sistema.evaluar_guardias_para_paquete, miembros)
        if g:
            if g.get("tipo") == "bloqueado":
                return False, ""
            texto = g.get("texto", texto)
    return True, texto


def obtener_eventos_proactivos() -> List[Tuple[str, dict]]:
    """Lo llama server.py cada ~20 s mientras haya una GUI conectada."""
    if not _listo:
        return []
    eventos: List[Tuple[str, dict]] = []

    if productivity is not None and hasattr(productivity, "obtener_alertas_recordatorios_pendientes"):
        for t in _seguro("alertas recordatorios", productivity.obtener_alertas_recordatorios_pendientes) or []:
            eventos.append(("notificacion", {"texto": str(t), "nivel": "aviso"}))
            _hablar(t, proactivo=True)
            if whatsapp is not None and hasattr(whatsapp, "notificar_usuario"):   # también a tu WhatsApp
                _seguro("whatsapp.notificar_usuario", whatsapp.notificar_usuario, f"⏰ {t}")
            if telegram_bot is not None and hasattr(telegram_bot, "notificar_usuario"):   # y a Telegram (si /telegram avisos on)
                _seguro("telegram.notificar_usuario", telegram_bot.notificar_usuario, f"⏰ {t}")
            if celular is not None:
                _seguro("celular.notificar", celular.notificar, f"⏰ {t}")

    if salud_sistema is not None and hasattr(salud_sistema, "evaluar_alertas_proactivas"):
        ahora = time.time()
        optimiza_ram = optimizador is not None and hasattr(optimizador, "paso_proactivo")
        for a in _seguro("alertas salud", salud_sistema.evaluar_alertas_proactivas) or []:
            if optimiza_ram and isinstance(a, dict) and a.get("recurso") == "ram":
                continue          # la RAM ahora la analiza y resuelve optimizador.py
            t = str(a.get("texto", "")) if isinstance(a, dict) else str(a)
            if t and ahora - _alertas_enviadas.get(t, 0) > REENVIO_ALERTA_S:
                _alertas_enviadas[t] = ahora
                eventos.append(("notificacion", {"texto": t, "nivel": "aviso"}))
                _hablar(t, proactivo=True)

    if optimizador is not None and hasattr(optimizador, "paso_proactivo"):
        for tipo, datos in _seguro("optimizador.paso_proactivo", optimizador.paso_proactivo) or []:
            voz_txt = datos.pop("voz", None)
            eventos.append((tipo, datos))
            if voz_txt:
                _hablar(voz_txt, proactivo=True)

    if habitos is not None and hasattr(habitos, "sugerencia_pendiente"):
        sug = _seguro("sugerencia_pendiente", habitos.sugerencia_pendiente)
        if sug:
            ok, texto = _sugerencia_permitida(sug)
            if ok:
                _seguro("marcar_sugerencia_mostrada", habitos.marcar_sugerencia_mostrada, sug["id"])
                eventos.append(("sugerencia_proactiva", {
                    "id_sugerencia": sug["id"],
                    "titulo": str(sug.get("nombre", "Sugerencia")).replace("_", " "),
                    "descripcion": texto}))
                _hablar(f"Te sugiero algo: {texto}", proactivo=True)

    # WhatsApp: enlace completado y mensajes entrantes. Cada evento puede traer "voz" (lo que se dice en voz
    # alta); se quita antes de enviarlo a la GUI para no ensuciar el evento.
    if whatsapp is not None and hasattr(whatsapp, "eventos_proactivos"):
        for tipo, datos in _seguro("whatsapp.eventos_proactivos", whatsapp.eventos_proactivos) or []:
            voz_txt = datos.pop("voz", None)
            eventos.append((tipo, datos))
            if voz_txt:
                _hablar(voz_txt, proactivo=True)
    
    if telegram_bot is not None and hasattr(telegram_bot, "eventos_proactivos"):   # Telegram: vinculado / órdenes recibidas
        for tipo, datos in _seguro("telegram.eventos_proactivos", telegram_bot.eventos_proactivos) or []:
            voz_txt = datos.pop("voz", None)
            eventos.append((tipo, datos))
            if voz_txt:
                _hablar(voz_txt, proactivo=True)
    if celular is not None and hasattr(celular, "eventos_proactivos"):
        for tipo, datos in _seguro("celular.eventos_proactivos", celular.eventos_proactivos) or []:
            eventos.append((tipo, datos))
    return eventos
    

def _nombre_comando_valido(nombre: str) -> bool:
    return bool(nombre) and len(nombre) <= 50 and bool(re.match(r"^[a-zA-Z0-9_-]+$", nombre))


def _responder_sugerencia(id_sugerencia: Any, respuesta: str, emit: Callable) -> Dict[str, Any]:
    """'activar' = sí (abre el paquete) · cualquier otra cosa = 'ignorar' (no).
    Tras varias confirmaciones ofrece convertirlo en comando permanente (como main.py)."""
    if habitos is None:
        return {"exito": False, "texto": "El módulo de hábitos no está disponible."}
    try:
        pid = int(id_sugerencia)
    except (TypeError, ValueError):
        return {"exito": False, "texto": "Sugerencia desconocida."}
    if respuesta != "activar":
        habitos.rechazar_sugerencia(pid)
        return {"exito": True, "texto": "Entendido, no lo sugiero de nuevo hoy."}

    r = habitos.ejecutar_paquete(pid)
    habitos.confirmar_sugerencia(pid)
    texto, exito = r.get("texto", ""), bool(r.get("exitoso"))
    try:
        p = habitos.obtener_paquete(pid)
        umbral = getattr(config, "UMBRAL_CREAR_COMANDO", 3)
        if exito and p and p.get("estado") != "fijo" and p.get("veces_confirmado", 0) >= umbral:
            nombre = p.get("nombre_paquete", "")
            if _nombre_comando_valido(nombre) and _pedir_confirmacion(
                    emit, f"Has confirmado '{nombre}' {p['veces_confirmado']} veces. "
                          f"¿Lo convierto en un comando permanente? Después bastará con decir \"{nombre.replace('_', ' ')}\"."):
                if habitos.crear_comando_desde_paquete(pid, nombre):
                    texto += f" Comando '{nombre}' creado."
    except Exception as e:
        logger.warning(f"⚠️  oferta de comando permanente: {e}")
    return {"exito": exito, "texto": texto}


def responder_sugerencia(id_sugerencia: Any, respuesta: str, emit: Callable) -> Dict[str, Any]:
    if optimizador is not None and str(id_sugerencia).startswith("opt:"):
        r = _seguro("optimizador.responder_sugerencia", optimizador.responder_sugerencia,
                    str(id_sugerencia), respuesta) or {"exito": False, "texto": "No pude aplicar la optimización."}
    else:
        r = _responder_sugerencia(id_sugerencia, respuesta, emit)
    _hablar(r.get("texto"), emit, bool(r.get("exito")))
    return r


# ─── ENTRADA PRINCIPAL ────────────────────────────────────────────────────

# ─── LIMPIEZA DE FRASES MAL OÍDAS (STT ligero) ────────────────────────────
# Principio: el núcleo de SARA va PRIMERO. Solo si una orden de una categoría "con objeto" (app, carpeta,
# archivo, sitio) FALLA, se intenta UNA vez más con la frase limpiada por la IA externa. Un solo ciclo:
# si la frase limpia también falla, se devuelve el mensaje de error ORIGINAL.

_CATEGORIAS_REINTENTO = frozenset({"ABRIR_APLICACION", "CERRAR_APLICACION", "CARPETA_ESPECIAL",
                                   "ABRIR_ARCHIVO", "ARCHIVO", "BUSCAR_EN_NAVEGADOR"})
_VERBOS_APERTURA_CORR = frozenset({"abre", "abrir", "abreme", "cierra", "cerrar", "cierrame", "ejecuta",
                                   "ejecutar", "inicia", "iniciar", "lanza", "lanzar", "arranca",
                                   "pon", "ponme", "reproduce", "reproduceme", "toca",        # "pon X en yutub"
                                   "busca", "buscame", "buscar", "googlea"})


def _sin_tildes(t: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(t).lower()) if unicodedata.category(c) != "Mn")


def _corregir_fonetica(texto: str) -> str:
    """Diccionario local (config.CORRECCIONES_FONETICAS): 'opera geequis' → 'opera gx'. Instantáneo y sin red.
    Solo se aplica si la frase EMPIEZA con un verbo de abrir/cerrar (no toca el texto de un mensaje de WhatsApp)."""
    tabla = getattr(config, "CORRECCIONES_FONETICAS", None) or {}
    m = re.match(r"^\W*(\w+)", texto or "")
    if not tabla or not m or _sin_tildes(m.group(1)) not in _VERBOS_APERTURA_CORR:
        return texto
    out = texto
    for mal, bien in sorted(tabla.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(r"(?<!\w)" + re.escape(mal) + r"(?!\w)", bien, out, flags=re.IGNORECASE)
    if out != texto:
        logger.info(f"🔤 Corrección fonética: '{texto[:60]}' → '{out[:60]}'")
    return out

# ─── SEÑALES LOCALES: frases largas/variadas → frase canónica (sin red, sin IA) ───────────────────────
# Cada entrada: (frase canónica, [grupos: debe haber ≥1 palabra de CADA grupo], {palabras prohibidas}).
_V_PEDIR = {"toma", "tomar", "tomes", "tomame", "saca", "sacar", "saques", "sacame", "haz", "hacer", "hagas",
            "hazme", "ver", "veo", "veas", "muestra", "muestrame", "mostrar", "manda", "mandame", "pasa",
            "pasame", "envia", "enviame", "dame", "quiero", "necesito"}
_EQUIPO = {"laptop", "compu", "computadora", "pc", "equipo", "lap", "portatil"}
_SENALES = [
    ("toma una foto",
     [{"foto", "fotografia", "webcam", "camara"}, _V_PEDIR],
     {"whatsapp", "mensaje", "carpeta", "archivo", "abre", "abrir", "cierra", "cerrar", "pantalla"}),
    ("quiero ver la pantalla",
     [{"pantalla", "captura", "screenshot"}, _V_PEDIR],
     {"brillo", "bloquea", "bloquear", "apaga", "apagar", "suspende", "descripcion", "ia", "activa", "desactiva",
      "modo", "error", "whatsapp", "mensaje", "carpeta", "archivo", "pasa", "significa", "dice"}),
    ("que significa este error",
     [{"error"}, {"significa", "dice", "explica", "explicame", "pasa", "sale", "aparece", "quiere"}],
     {"whatsapp", "mensaje", "mandale", "dile", "escribele", "archivo", "carpeta", "codigo", "script"}),
    ("donde esta mi laptop",
     [{"donde", "ubicacion", "localiza", "localizame"}, _EQUIPO],
     {"carpeta", "archivo", "whatsapp", "mensaje", "app", "aplicacion", "programa"}),
]


def _canonizar_pedido(texto: str) -> str:
    """Si la frase (aunque venga con relleno: «procede a», «ahora quiero que»...) corresponde a UNA sola función
    nueva, devuelve su frase canónica; si no hay coincidencia o hay empate, devuelve el texto tal cual (→ IA)."""
    try:
        palabras = set(re.findall(r"[a-z0-9]+", _sin_tildes(texto)))
        if not palabras or len(palabras) > 14 or texto.lstrip().startswith("/"):
            return texto
        hits = [canon for canon, grupos, prohibidas in _SENALES
                if not (palabras & prohibidas) and all(palabras & g for g in grupos)]
        if len(hits) == 1:
            logger.info(f"🎯 Señal local: «{texto[:60]}» → «{hits[0]}»")
            return hits[0]
    except Exception as e:
        logger.warning(f"⚠️  _canonizar_pedido: {type(e).__name__}: {e}")
    return texto
def _objeto_de(res: dict) -> str:
    """La frase sin el verbo de apertura/cierre ni artículos: 'abre calculatora' → 'calculatora'."""
    verbos = set(getattr(config, "VERBOS_ABRIR_APLICACION", ()) or ()) | set(getattr(config, "VERBOS_CERRAR_APLICACION", ()) or ())
    palabras = (res.get("texto_norm") or _sin_tildes(res.get("texto_original", ""))).split()
    while palabras and (palabras[0] in verbos or palabras[0] in ("la", "el", "los", "las", "mi", "un", "una")):
        palabras.pop(0)
    return " ".join(palabras)


def _candidatos_para_ia(res: dict) -> List[str]:
    """Nombres de apps parecidos a lo dicho: le dan a la IA el vocabulario REAL del equipo (opera → 'Opera GX')."""
    if res.get("categoria") not in ("ABRIR_APLICACION", "CERRAR_APLICACION") or app_index is None:
        return []
    objeto = _objeto_de(res)
    if not objeto:
        return []
    cands = _seguro("app_index.buscar_aplicacion", lambda: app_index.buscar_aplicacion(objeto, limite=8)) or []
    nombres: List[str] = []
    for c in cands:
        n = c.get("nombre") if isinstance(c, dict) else None
        if n and n not in nombres:
            nombres.append(str(n))
    return nombres


_RE_NO_REINTENTAR = re.compile(r"encontr[eé] varias|del sistema|protegid|no est[aá] permitid", re.IGNORECASE)
_CATS_ABRIR = frozenset({"ABRIR_APLICACION", "CARPETA_ESPECIAL", "ABRIR_ARCHIVO", "ARCHIVO"})


def _reintentar_limpiando(texto: str, resp_original: str, res: dict, emit: Callable) -> Tuple[bool, str]:
    """UN solo ciclo de limpieza de la frase: IA local primero y, si no pudo o su propuesta no funcionó, IA externa
    como último filtro (máx. 2 propuestas, cada una se ejecuta UNA vez con reintento=False → jamás hay bucle).
    Si nada funciona: «No pude abrirlo…». Si la limpieza no puede ni intentarse, se conserva el fallo original."""
    fallo = (False, resp_original)
    cat = res.get("categoria")
    if not getattr(config, "REINTENTO_LIMPIEZA_ACTIVO", True):
        return fallo
    if cat not in _CATEGORIAS_REINTENTO or len(texto) > int(getattr(config, "REINTENTO_MAX_CARACTERES", 120)):
        return fallo
    if _RE_NO_REINTENTAR.search(str(resp_original or "")):
        return fallo                                  # ambigüedad / ruta protegida: otra frase no lo arregla
    propuestas = getattr(conversacion, "propuestas_limpieza", None) if conversacion is not None else None
    if propuestas is None:
        return fallo                                  # conversacion.py sin la función nueva: todo sigue como antes
    emit("estado_procesando", etapa="limpieza", timeout_segundos=TIMEOUT_CONFIRMACION_S)
    try:
        for fuente, limpio in propuestas(texto, _candidatos_para_ia(res)):
            logger.info(f"🧹 Reintento único ({fuente}): '{texto[:60]}' → '{limpio[:60]}'")
            ok, resp = _ejecutar_como_sara(limpio, emit, reintento=False)
            if ok:
                return True, resp
            # no funcionó: el generador pasa a la siguiente fuente (solo existe la externa) y luego se acaba
    except Exception as e:
        logger.error(f"✗ limpieza de frase: {e}", exc_info=True)
    accion = "cerrarlo" if cat == "CERRAR_APLICACION" else "abrirlo" if cat in _CATS_ABRIR else None
    if accion:
        return False, f"No pude {accion}: «{texto[:60]}»."
    return fallo


def _ejecutar_como_sara(texto: str, emit: Callable, reintento: bool = True) -> Tuple[bool, str]:
    """El circuito de SIEMPRE + un único reintento con la frase limpiada si una orden con objeto falla."""
    ok, resp, res = _ejecutar_como_sara_res(texto, emit)
    if ok or not reintento:
        return ok, resp
    return _reintentar_limpiando(texto, resp, res, emit)


def _ejecutar_volumen(texto: str, emit: Callable) -> Tuple[bool, str]:
    """Control de volumen del sistema (volumen.py): lenguaje natural, sin clasificador ni IA externa.
    Por los canales remotos (WhatsApp / Telegram) se puede apagar con config.VOL_PERMITIR_REMOTO = False."""
    og = getattr(_ctx, "origen", None)
    if og in _ORIGENES_REMOTOS and not getattr(config, "VOL_PERMITIR_REMOTO", True):
        logger.warning(f"🚫 Volumen: bloqueado para el canal remoto ({og})")
        return False, "El volumen no se controla por el canal remoto. Pídemelo desde el PC."
    emit("resultado_clasificacion", categoria="CONTROL_VOLUMEN", confianza=1.0, metodo="directo", id_historial=None)
    emit("estado_procesando", etapa="ejecución", timeout_segundos=TIMEOUT_CONFIRMACION_S)
    r = _seguro("volumen.ejecutar", volumen.ejecutar, texto)
    if r is None:
        return False, "No pude cambiar el volumen (revisa el log)."
    ok, msg = bool(r[0]), str(r[1])
    logger.info(f"🔊 volumen ({og or 'texto'}): «{texto[:60]}» → {msg}")
    return ok, msg


_RE_NOMBRA_CELULAR = re.compile(r"(?i)\b(?:celular|tel[eé]fono|m[oó]vil|cel)\b")


def _bloqueo_es_del_celular(texto: str) -> bool:
    """«bloquea la pantalla de mi celular»: apagado.py bloquearía la LAPTOP con solo oír «bloquea la pantalla». Si la frase
    nombra el celular, ese bloqueo es del teléfono y lo atiende celular.py (capacidad bloquear_celular), no apagado."""
    if apagado is None or not _RE_NOMBRA_CELULAR.search(texto or ""):
        return False
    plan = _seguro("apagado.interpretar", apagado.interpretar, texto)
    return bool(plan) and plan.get("accion") == "bloquear"


def _ejecutar_multimedia(texto: str, emit: Callable) -> Tuple[bool, str]:
    """Control de la reproducción del PC (multimedia.py): pausa, sigue, siguiente, anterior, adelantar, «qué suena».
    Lenguaje natural, sin clasificador ni IA externa. Por los canales remotos (WhatsApp / Telegram / celular) se puede
    apagar con config.MEDIA_PERMITIR_REMOTO = False."""
    og = getattr(_ctx, "origen", None)
    if og in _ORIGENES_REMOTOS and not getattr(config, "MEDIA_PERMITIR_REMOTO", True):
        logger.warning(f"🚫 Multimedia: bloqueado para el canal remoto ({og})")
        return False, "La reproducción no se controla por el canal remoto. Pídemelo desde el PC."
    emit("resultado_clasificacion", categoria="CONTROL_MULTIMEDIA", confianza=1.0, metodo="directo", id_historial=None)
    emit("estado_procesando", etapa="ejecución", timeout_segundos=TIMEOUT_CONFIRMACION_S)
    r = _seguro("multimedia.ejecutar", multimedia.ejecutar, texto)
    if r is None:
        return False, "No pude controlar la reproducción (revisa el log)."
    ok, msg = bool(r[0]), str(r[1])
    logger.info(f"🎵 multimedia ({og or 'texto'}): «{texto[:60]}» → {msg}")
    return ok, msg


def _ejecutar_apagado(texto: str, emit: Callable) -> Tuple[bool, str]:
    """Apagar el equipo (apagado.py): lenguaje natural, sin clasificador ni IA externa. SIEMPRE con la advertencia y la
    confirmación. Orígenes: la ventana de SARA (teclado o micrófono) y el CELULAR vinculado (ya pasó por la huella
    «fresca» de celular.py; aquí se confirma con la tarjeta del celular y la cuenta atrás nunca baja del mínimo remoto).
    Por WhatsApp no se permite. Por Telegram lo atiende telegram_bot (botones + código) y nunca llega aquí; si llega
    (p. ej. una orden destilada por el puente), se rechaza. «Cancelar el apagado» vale por cualquier canal."""
    og = getattr(_ctx, "origen", None)
    plan = _seguro("apagado.interpretar", apagado.interpretar, texto)
    if not plan:
        return False, "No entendí qué hacer con el apagado."
    emit("resultado_clasificacion", categoria="APAGAR_EQUIPO", confianza=1.0, metodo="directo", id_historial=None)
    emit("estado_procesando", etapa="ejecución", timeout_segundos=TIMEOUT_CONFIRMACION_S)
    if plan["accion"] == "cancelar":
        r = _seguro("apagado.cancelar", apagado.cancelar)
        if r is None:
            return False, "No pude cancelar el apagado (revisa el log)."
        logger.warning(f"⏻ cancelar apagado ({og or 'texto'}): {r[1]}")
        return bool(r[0]), str(r[1])
    if og in _ORIGENES_REMOTOS and og != "celular":
        logger.warning(f"🚫 Apagado: rechazado por el canal remoto ({og})")
        if og == "whatsapp":
            return False, "Eso no se puede por WhatsApp. Pídemelo desde el PC o por Telegram (te pediré confirmación)."
        return False, "Por Telegram escríbelo directamente (por ejemplo «apaga la computadora» o «suspende la laptop»): te pediré confirmación."
    if not getattr(config, "APAGADO_ACTIVO", True):
        return False, "El apagado del equipo está desactivado (APAGADO_ACTIVO en config.py)."
    accion = plan.get("accion", "apagar")
    remoto = (og == "celular")
    demora = 0 if accion == "bloquear" else apagado.normalizar_demora(plan.get("demora_s"), remoto=remoto)
    aviso = apagado.advertencia(demora, remoto=False, accion=accion)
    if remoto and accion != "bloquear":
        if accion in ("suspender", "hibernar"):
            aviso += "\n🚫 Desde el celular no podré despertarla: tendrás que abrir la tapa o pulsar el botón de encendido."
        else:
            aviso += "\n🚫 No podré responderte desde el celular hasta que el equipo vuelva a encenderse y SARA arranque."
    if not _pedir_confirmacion(emit, aviso):
        return True, "Cancelado, no hice nada."
    r = _seguro("apagado.programar", apagado.programar, demora, og or "texto", accion)
    if r is None:
        return False, "No pude programar el apagado (revisa el log)."
    logger.warning(f"⏻ apagado ({og or 'texto'}): {r[1]}")
    return bool(r[0]), str(r[1])

def _ejecutar_deshacer(texto: str, emit: Callable) -> Tuple[bool, str, dict]:
    if deshacer.interpretar(texto) == "listar":
        return True, deshacer.listar_texto(), {}
    op = deshacer.ultimo()
    if not op:
        return True, "No hay nada que deshacer.", {}
    if not _pedir_confirmacion(emit, f"¿Deshago lo último? Voy a {deshacer.describir(op)}."):
        return True, "Cancelado, no deshice nada.", {}
    ok, msg = deshacer.deshacer_ultimo()
    return bool(ok), str(msg), {}


def _ejecutar_modo(texto: str, emit: Callable, remoto: bool) -> Tuple[bool, str, dict]:
    p = modos.interpretar(texto)
    if not p or p.get("accion") == "listar":
        return True, modos.listar_texto(), {}
    emit("estado_procesando", etapa="ejecución", timeout_segundos=TIMEOUT_CONFIRMACION_S)
    ok, msg = modos.ejecutar(p["nombre"], lambda cmd: _ejecutar_como_sara(cmd, emit, reintento=False)[:2], remoto)
    return bool(ok), str(msg), {}

_RE_RELLENO = re.compile(
    r"^(?:(?:oye|hey|hola|ok)\s+)?(?:sara[\s,]+)?"
    r"(?:(?:ahora|ya|entonces|bueno|pues)\s+)*"
    r"(?:(?:procede|proceda|continua|sigue)\s+(?:a|con)\s+|"
    r"(?:quiero|necesito|me gustaria|deseo|te pido|podrias|puedes|quisiera)\s+(?:que\s+)?(?:me\s+)?)", re.I)
_INFINITIVO = {"tomar": "toma", "tomes": "toma", "tomarme": "toma", "sacar": "saca", "saques": "saca",
               "hacer": "haz", "hagas": "haz", "mostrar": "muestra", "muestres": "muestra",
               "mandar": "manda", "mandes": "manda", "enviar": "envia", "envies": "envia",
               "ver": "ver", "veas": "ver"}

def _normalizar_pedido(texto: str) -> str:
    n = _sin_tildes(texto).strip()
    m = _RE_RELLENO.match(n)
    if not m:
        return texto
    resto = n[m.end():]
    p = resto.split(maxsplit=1)
    if p and p[0] in _INFINITIVO:
        resto = _INFINITIVO[p[0]] + (" " + p[1] if len(p) > 1 else "")
    return resto or texto
def _atajos_nuevos(texto: str, emit: Callable) -> Optional[Tuple[bool, str, dict]]:
    """Switches y alertas (cualquier canal) · deshacer y modos (cualquier canal) · antirrobo, pantalla y envío de
    archivos (SOLO Telegram, con ventana TOTP salvo que la verificación esté apagada). None = no es asunto suyo."""
    try:
        og = getattr(_ctx, "origen", None)
        if celular is not None and og != "celular" and _seguro("celular.es_comando", celular.es_comando, texto):
            ok_c, msg_c = celular.ejecutar(texto, og, lambda p: _pedir_confirmacion(emit, p))
            return bool(ok_c), str(msg_c), {}
        if interruptores is not None and _seguro("interruptores.es_comando", interruptores.es_comando, texto):
            r = _seguro("interruptores.ejecutar", interruptores.ejecutar, texto, og) or (False, "No pude cambiar ese ajuste.")
            return bool(r[0]), str(r[1]), {}
        if alertas is not None and _seguro("alertas.es_comando", alertas.es_comando, texto):
            r = _seguro("alertas.ejecutar", alertas.ejecutar, texto) or (False, "No pude cambiar las alertas.")
            return bool(r[0]), str(r[1]), {}
        if deshacer is not None and _seguro("deshacer.es_comando", deshacer.es_comando, texto):
            return _ejecutar_deshacer(texto, emit)
        if modos is not None and _seguro("modos.es_comando", modos.es_comando, texto):
            return _ejecutar_modo(texto, emit, og in _ORIGENES_REMOTOS)
        if og not in ("telegram", "celular"):
            return None                      # desde la laptop esos verbos siguen como siempre
        es_cel = og == "celular"
        quiere_robo = antirrobo is not None and _seguro("antirrobo.es_comando", antirrobo.es_comando, texto)
        quiere_pant = (not quiere_robo) and pantalla is not None and _seguro("pantalla.es_comando", pantalla.es_comando, texto)
        quiere_env = (not quiere_robo and not quiere_pant) and envio_archivos is not None and \
            _seguro("envio.es_comando", envio_archivos.es_comando, texto)
        if not (quiere_robo or quiere_pant or quiere_env):
            return None
        if es_cel:
            # La huella (reto firmado con la clave del Keystore) ya se comprobó en celular.py: aquí no hay TOTP.
            if celular is None:
                return False, "Falta celular.py.", {}
            emit("estado_procesando", etapa="ejecución", timeout_segundos=TIMEOUT_CONFIRMACION_S)
            if quiere_robo:
                ok, msg = antirrobo.ejecutar(texto, celular.adjuntar_imagen)
            elif quiere_pant:
                ok, msg = pantalla.ejecutar(texto, celular.adjuntar_imagen, getattr(_ctx, "original", None))
            else:
                ok, msg = envio_archivos.ejecutar(texto, lambda p, o: _pedir_eleccion(emit, p, o), celular.adjuntar_ruta,
                                                  max_mb=1e9, permitir_carpetas=True)
                if not ok:
                    msg = celular.ultimo_error() or msg
            return bool(ok), str(msg), {}
        if desbloqueo is None or telegram_bot is None:
            return False, "Faltan desbloqueo.py o telegram_bot.py.", {}
        # ... (aquí siguen, sin cambios, las comprobaciones de desbloqueo y la rama de Telegram que ya tienes)
        if desbloqueo.bloqueado():
            return False, "🔒 Archivos, pantalla y cámara bloqueados por intentos fallidos. Espera unos minutos.", {}
        if not desbloqueo.activo():
            if not desbloqueo.disponible():
                return False, "🔒 Falta configurar el TOTP: en el PC ejecuta  python apagado.py totp", {}
            return False, "🔐 Escribe aquí el código de 6 dígitos de tu app autenticadora (cuenta «SARA») para continuar.", \
                {"requiere_codigo": True}
        emit("estado_procesando", etapa="ejecución", timeout_segundos=TIMEOUT_CONFIRMACION_S)
        if quiere_robo:
            ok, msg = antirrobo.ejecutar(texto, telegram_bot.enviar_foto)
        elif quiere_pant:
            ok, msg = pantalla.ejecutar(texto, telegram_bot.enviar_foto, getattr(_ctx, "original", None))
        else:
            ok, msg = envio_archivos.ejecutar(texto, lambda p, o: _pedir_eleccion(emit, p, o), telegram_bot.enviar_documento)
        return bool(ok), str(msg), {}
    except Exception as e:
        logger.error(f"✗ _atajos_nuevos: {e}", exc_info=True)
        return False, "Error interno (revisa el log).", {}


def _reescribir_slash(texto: str) -> str:
    """Comandos «/» que EJECUTAN cosas pasan por el circuito normal (confirmaciones incluidas): «/modo clase» →
    «activa el modo clase» · «/deshacer» → «deshaz lo último»."""
    low = texto.strip().lower()
    if low == "/deshacer":
        return "deshaz lo último"
    if low == "/bloquear":
        return "bloquea la pantalla"
    if modos is not None and low.startswith("/modo "):
        return modos.reescribir_slash(texto)
    return texto


def _ejecutar_como_sara_res(texto: str, emit: Callable) -> Tuple[bool, str, dict]:
    """El circuito de SIEMPRE, intacto: clasificar (Capas 0-3) → despachar por categoría.
    (Antes vivía dentro de procesar_mensaje; se extrajo para poder llamarlo también con las órdenes
    que destila el puente conversacional.) Devuelve además la clasificación (para decidir el reintento)."""
    global _ultimo_res
    if whatsapp is not None and _seguro("whatsapp.es_comando", whatsapp.es_comando, texto):
        # mandar / leer / responder por WhatsApp: también cuando la orden la destila el puente («mándale a Juan que…»)
        emit("estado_procesando", etapa="ejecución", timeout_segundos=TIMEOUT_CONFIRMACION_S)
        res_wa = {"categoria": "WHATSAPP", "texto_original": texto, "texto_norm": texto.lower(), "confianza": 1.0,
                  "metodo": "directo", "origen": getattr(_ctx, "origen", None)}
        r_wa = _despachar_whatsapp(res_wa, emit)
        if r_wa is not None:
            return bool(r_wa[0]), str(r_wa[1]), res_wa
    if apagado is not None and _seguro("apagado.es_comando", apagado.es_comando, texto) and not _bloqueo_es_del_celular(texto):
        ok_a, resp_a = _ejecutar_apagado(texto, emit)      # también cubre las órdenes que destila el puente
        return ok_a, resp_a, {"categoria": "APAGAR_EQUIPO", "texto_original": texto, "texto_norm": texto.lower(),
                              "confianza": 1.0, "metodo": "directo", "origen": getattr(_ctx, "origen", None)}
    # Multimedia ANTES de los atajos: «pausa la música» dicho en la laptop controla la LAPTOP (celular.es_comando,
    # que va primero en _atajos_nuevos, también conoce esas frases —nodo «ambos»— y las mandaría al reproductor del teléfono).
    if multimedia is not None and _seguro("multimedia.es_comando", multimedia.es_comando, texto):
        ok_m, resp_m = _ejecutar_multimedia(texto, emit)   # también cubre las órdenes que destila el puente
        return ok_m, resp_m, {"categoria": "CONTROL_MULTIMEDIA", "texto_original": texto, "texto_norm": texto.lower(),
                              "confianza": 1.0, "metodo": "directo", "origen": getattr(_ctx, "origen", None)}
    at = _atajos_nuevos(texto, emit)
    if at is not None:
        return at[0], at[1], {"categoria": "REMOTO_NUEVO", "texto_original": texto, "texto_norm": texto.lower(),
                              "confianza": 1.0, "metodo": "directo", "origen": getattr(_ctx, "origen", None)}
    if volumen is not None and _seguro("volumen.es_comando", volumen.es_comando, texto):
        ok_v, resp_v = _ejecutar_volumen(texto, emit)      # también cubre las órdenes que destila el puente
        return ok_v, resp_v, {"categoria": "CONTROL_VOLUMEN", "texto_original": texto, "texto_norm": texto.lower(),
                              "confianza": 1.0, "metodo": "directo", "origen": getattr(_ctx, "origen", None)}
    emit("estado_procesando", etapa="clasificación", timeout_segundos=TIMEOUT_CONFIRMACION_S)
    res = engine.clasificar(texto)
    res["texto_original"] = texto
    res["origen"] = getattr(_ctx, "origen", None)     # "voz" | "whatsapp" | "telegram" | "texto"
    if res["origen"] not in _ORIGENES_REMOTOS:        # una orden remota no debe poder ser «validada» por error desde la ventana
        _ultimo_res = res
    emit("resultado_clasificacion", categoria=res.get("categoria", "DESCONOCIDA"),
         confianza=float(res.get("confianza", 0.0) or 0.0),
         metodo=res.get("metodo", ""), id_historial=res.get("id_historial"))
    logger.info(f"  → {res.get('categoria')} ({res.get('confianza')}) vía {res.get('metodo')}")

    emit("estado_procesando", etapa="ejecución", timeout_segundos=TIMEOUT_CONFIRMACION_S)
    ok, resp = _despachar(res, emit)
    return ok, resp, res


_pool_rapido = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sara-rapido")


def _es_orden_conocida(texto: str) -> bool:
    """¿El motor de SARA ya domina esta orden? (Capas 0-2, sin LLM). Si sí, no hace falta la IA externa.
    v7: con PRESUPUESTO de tiempo (config.RAPIDO_TIMEOUT_S, 1.5 s). El log real mostró atípicos de 17-67 s
    (p90 = 23 s) en reconocer_rapido; esa espera ocurría ANTES de llegar a Groq. Si se pasa del presupuesto
    se trata como «no la conozco» → la frase sigue al puente (Groq la destila o responde) sin esperar más."""
    fn = getattr(engine, "reconocer_rapido", None)
    if fn is None:
        return False
    presupuesto = float(getattr(config, "RAPIDO_TIMEOUT_S", 1.5))
    t0 = time.perf_counter()
    try:
        r = _pool_rapido.submit(fn, texto).result(timeout=presupuesto)
    except _FutTimeout:
        logger.warning(f"⏱️ reconocer_rapido pasó de {presupuesto:.1f} s → sigo sin esperarlo (¿motor frío o CPU ocupada?)")
        return False
    except Exception as e:
        logger.warning(f"⚠️  engine.reconocer_rapido: {type(e).__name__}: {e}")
        return False
    ms = int((time.perf_counter() - t0) * 1000)
    (logger.info if ms > 800 else logger.debug)(
        f"reconocer_rapido: {ms} ms → {(r or {}).get('categoria')} ({(r or {}).get('metodo')})")
    return bool(r)


def _pulso_calor() -> None:
    """Mantiene «caliente» lo que más tarda en frío: el motor (embeddings / BD / cachés) y las conexiones HTTPS
    con Groq/Gemini (el servidor las cierra si pasan minutos sin uso). Es una hipótesis razonada, no confirmada:
    se mide con los avisos ⏱️ y 🔥 del log. Nunca lanza."""
    fn = getattr(engine, "reconocer_rapido", None)
    ext = _importar("respaldo_externo")
    intervalo = max(30.0, float(getattr(config, "PULSO_CALOR_S", 90)))
    ciclo = 0
    while True:
        t0 = time.perf_counter()
        if ext is not None and hasattr(ext, "precalentar_conexiones"):
            _seguro("precalentar_conexiones", ext.precalentar_conexiones)
        if fn is not None and ciclo % 3 == 0:          # el motor, cada ~3 pulsos (≈ 4.5 min)
            _seguro("pulso_calor", fn, "hola sara")
        ms = int((time.perf_counter() - t0) * 1000)
        (logger.info if ms > 1500 else logger.debug)(f"🔥 pulso de calor: {ms} ms")
        ciclo += 1
        time.sleep(intervalo)


def _via_conversacion(texto: str, origen: str, emit: Callable) -> Optional[Dict[str, Any]]:
    # 0) Memoria local: lo que la IA ya resolvió se repite SIN IA (también sin internet)
    if memoria_local is not None:
        m = _seguro("memoria_local.buscar", memoria_local.buscar, texto)
        if m and m["tipo"] == "info":
            logger.info(f"🧠 Respondido desde memoria local: «{texto[:60]}»")
            _hablar(m["respuesta"], emit, True, charla=True)
            return {"exito": True, "texto": m["respuesta"]}
        if m and m["tipo"] == "accion":
            logger.info(f"🧠 Frase conocida, sin IA: «{texto[:60]}» → {m['comandos']}")
            rs, ok_m = [], True
            for cmd in m["comandos"]:
                ok, resp = _ejecutar_como_sara(cmd, emit, reintento=False)
                rs.append(str(resp))
                ok_m = ok_m and bool(ok)
            _seguro("memoria_local.registrar_resultado", memoria_local.registrar_resultado, m["clave"], ok_m)
            txt = " ".join(r for r in rs if r)
            _hablar(txt, emit, ok_m)
            return {"exito": ok_m, "texto": txt}

    if conversacion is None or not _seguro("conversacion.usar_puente", conversacion.usar_puente, texto, origen,
                                           _es_orden_conocida):
        return None
    emit("estado_procesando", etapa="conversación", timeout_segundos=TIMEOUT_CONFIRMACION_S)
    d = _seguro("conversacion.decidir", conversacion.decidir, texto, origen)
    if not d:
        return None

    if d["tipo"] == "conversacion":
        _hablar(d["respuesta"], emit, True, charla=True)
        if d.get("guardar") and memoria_local is not None:
            _seguro("memoria_local.guardar_info", memoria_local.guardar_info, texto, d["respuesta"])
        return {"exito": True, "texto": d["respuesta"]}

    if d["tipo"] == "ruido":
        logger.info(f"🎙️ Tomado como ruido: '{texto[:80]}'")
        return {"exito": True, "texto": f"🎙️ Ignorado (no parece una orden): «{texto[:60]}»"}

    respuestas: List[str] = []
    todo_ok = True
    for cmd in d["comandos"]:
        logger.info(f"🧭 Orden destilada: '{texto[:60]}' → '{cmd}'")
        ok, resp = _ejecutar_como_sara(cmd, emit, reintento=False)
        respuestas.append(str(resp))
        todo_ok = todo_ok and bool(ok)
    respuesta = " ".join(r for r in respuestas if r)
    _seguro("conversacion.registrar_resultado", conversacion.registrar_resultado, d["comandos"], todo_ok, respuesta)
    if todo_ok and memoria_local is not None:
        _seguro("memoria_local.guardar_accion", memoria_local.guardar_accion, texto, d["comandos"])
    _hablar(respuesta, emit, todo_ok)
    return {"exito": todo_ok, "texto": respuesta}

def procesar_mensaje(texto_usuario: str, emit: Callable, origen: str = "texto",
                     entrada: Optional[str] = None) -> Dict[str, Any]:
    """SÍNCRONO. Nunca lanza excepción: siempre devuelve {"exito","texto"}.
    origen: el CANAL — "texto" (teclado), "voz" (micrófono), "whatsapp" (SARA-Net) o "telegram". Lo marca en el hilo
    para que _hablar y _despachar sepan de dónde vino la orden.
    entrada: cómo se produjo lo escrito — "voz" (dictado/nota de voz: puede venir mal oído) o "texto". Si no se da,
    sale del origen. Así una nota de voz de Telegram se limpia como voz aunque su canal sea remoto."""
    entrada = entrada if entrada in ("voz", "texto") else ("voz" if origen == "voz" else "texto")
    _ctx.origen = origen
    _ctx.entrada = entrada
    _ctx.original = texto_usuario
    t0 = time.perf_counter()
    try:
        return _procesar_mensaje_interno(texto_usuario, emit, origen)
    finally:
        _ctx.origen = None
        _ctx.entrada = None
        _ctx.original = None
        logger.info(f"⏱️ orden ({origen}/{entrada}) resuelta en {int((time.perf_counter() - t0) * 1000)} ms: «{(texto_usuario or '')[:50]}»")


def _procesar_mensaje_interno(texto_usuario: str, emit: Callable, origen: str = "texto") -> Dict[str, Any]:
    try:
        texto = (texto_usuario or "").strip()
        if not texto:
            return {"exito": False, "texto": "Mensaje vacío."}
        if not _listo:
            return {"exito": False, "texto": "SARA todavía está arrancando, intenta en unos segundos."}

        logger.info(f"📝 Procesando ({origen}): {texto[:80]}")
        if origen not in _ORIGENES_REMOTOS:     # una orden remota no interrumpe el lector del PC
     # una orden remota no interrumpe el lector del PC
            _ceder_lector(emit)      # prioridad de la bandeja principal sobre el lector de texto

        if origen not in _ORIGENES_REMOTOS:
            texto = _reescribir_slash(texto)
        if origen in _ORIGENES_REMOTOS and texto.startswith("/") and not _slash_remoto_permitido(texto):
            logger.warning(f"🚫 Comando «/» rechazado por el canal {origen}: {texto.split()[0][:30]}")
            return {"exito": False, "texto": "Los comandos con / solo funcionan desde la ventana de SARA. "
                                             "Pídemelo en lenguaje natural."}
        slash = _comando_slash(texto)
        if slash is not None:
            resp_slash = str(slash[1])
            if texto.split()[0].lower() not in _SLASH_SIN_VOZ and not texto.lower().startswith(("/whatsapp", "/telegram")):

                _hablar(resp_slash, emit, bool(slash[0]))
            return {"exito": bool(slash[0]), "texto": resp_slash}

        # Puente conversacional (IA externa): conversa, destila órdenes o descarta ruido de STT.
        # Devuelve None si no aplica → sigue el flujo de siempre, sin cambios.
                # Atajo WhatsApp: leer / mandar / responder va directo al módulo (sin clasificador ni IA externa).
        if whatsapp is not None and _seguro("whatsapp.es_comando", whatsapp.es_comando, texto):
            emit("estado_procesando", etapa="ejecución", timeout_segundos=TIMEOUT_CONFIRMACION_S)
            res_wa = {"categoria": "WHATSAPP", "texto_original": texto, "texto_norm": texto.lower(),
                      "confianza": 1.0, "metodo": "directo"}
            r_wa = _despachar_whatsapp(res_wa, emit)
            if r_wa is not None:
                _hablar(r_wa[1], emit, bool(r_wa[0]))
                return {"exito": bool(r_wa[0]), "texto": str(r_wa[1])}
        if apagado is not None and _seguro("apagado.es_comando", apagado.es_comando, texto) and not _bloqueo_es_del_celular(texto):
            ok_a, resp_a = _ejecutar_apagado(texto, emit)   # apagar el equipo: directo, con advertencia y confirmación
            _hablar(resp_a, emit, ok_a)
            return {"exito": ok_a, "texto": resp_a}
        if multimedia is not None and _seguro("multimedia.es_comando", multimedia.es_comando, texto):
            ok_m, resp_m = _ejecutar_multimedia(texto, emit)   # multimedia: directo, ANTES del gancho del celular
            _hablar(resp_m, emit, ok_m)
            return {"exito": ok_m, "texto": resp_m}
        at = _atajos_nuevos(texto, emit)
        if at is None:                      # no cuadró exacto: probar con la frase canónica de las señales locales
            canon = _canonizar_pedido(texto)
            if canon != texto:
                at = _atajos_nuevos(canon, emit)
        if at is not None:                  # alertas (todo canal) · pantalla y archivos (Telegram + TOTP)
            _hablar(at[1], emit, at[0])
            return dict({"exito": at[0], "texto": at[1]}, **at[2])
        if volumen is not None and _seguro("volumen.es_comando", volumen.es_comando, texto):
            ok_v, resp_v = _ejecutar_volumen(texto, emit)   # volumen: directo, sin clasificador ni IA externa
            _hablar(resp_v, emit, ok_v)
            return {"exito": ok_v, "texto": resp_v}
        texto = _corregir_fonetica(texto)       # 'abre opera geequis' → 'abre opera gx' (diccionario local, sin red)
        via = _via_conversacion(texto, getattr(_ctx, "entrada", None) or ("voz" if origen == "voz" else "texto"), emit)  # el puente solo conoce texto|voz
        if via is not None:
            return via

        exito, respuesta = _ejecutar_como_sara(texto, emit)
        _hablar(respuesta, emit, bool(exito))   # emit → sincroniza la aureola con la duración de la voz
        return {"exito": bool(exito), "texto": str(respuesta)}
    except Exception as e:
        logger.error(f"✗ procesar_mensaje: {e}", exc_info=True)
        return {"exito": False, "texto": f"Error interno: {str(e)[:120]}"}