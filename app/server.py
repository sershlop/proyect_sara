"""
🌐 server.py — FastAPI + WebSocket para la GUI de SARA (v5)

Cambios v5 (fusión backend ⇄ frontend):
  1. Puerto: si lanzador.py define SARA_PORT, se usa ese (antes el lanzador
     asumía 8000 aunque el servidor saltara a 8001 → ventana en blanco).
  2. /static/app.js y /static/style.css tienen fallback: se sirven desde
     static/ o, si no existe esa carpeta, junto a server.py.
  3. Bucle proactivo (cada 20 s): recordatorios vencidos, alertas de salud y
     sugerencias de hábitos llegan a la GUI (eventos `notificacion` y
     `sugerencia_proactiva`).
  4. `respuesta_sugerencia` ya tiene handler (activar / ignorar).
"""

import asyncio
import importlib
import json
import logging
import os
import re
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, Set

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def _archivo_gui(nombre: str) -> Optional[Path]:
    """Busca un archivo de la GUI en static/, templates/ o junto a server.py."""
    for base in (STATIC_DIR, BASE_DIR / "templates", BASE_DIR):
        p = base / nombre
        if p.is_file():
            return p
    return None


INDEX_HTML = _archivo_gui("index.html") or (STATIC_DIR / "index.html")
HOST = "127.0.0.1"
PUERTO_BASE = 8000
TIMEOUT_MENSAJE_S = 90          # incluye una posible confirmación de 30 s
INTERVALO_PROACTIVO_S = 20
MAX_CARACTERES_MENSAJE = 4000    # tope de una orden escrita o dictada (evita mensajes absurdos)


def get_puerto_libre(host: str, puerto_inicio: int) -> int:
    for puerto in range(puerto_inicio, puerto_inicio + 10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, puerto))
                return puerto
            except OSError:
                print(f"[AVISO] Puerto {puerto} en uso. Probando el siguiente...")
    return puerto_inicio


try:
    _puerto_env = int(os.environ.get("SARA_PORT", "0"))
except ValueError:
    _puerto_env = 0
PUERTO_ACTIVO = _puerto_env or get_puerto_libre(HOST, PUERTO_BASE)

logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s] %(name)s — %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BASE_DIR / "sara_server_debug.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("SARA-SERVER")

# Lector de texto a voz (módulo opcional y aislado: si falla, SARA sigue igual)
try:
    import lector_voz
except Exception as _e_lector:
    lector_voz = None
    logger.warning("⚠️  lector_voz no disponible: %s", _e_lector)

# Voz de entrada (STT, faster-whisper): módulo opcional y aislado
try:
    import stt
except Exception as _e_stt:
    stt = None
    logger.warning("⚠️  stt no disponible: %s", _e_stt)

executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sara-worker")
core_bridge = None                    # se asigna en el hilo de carga
_estado_core = {"estado": "cargando", "error": None}
_loop: Optional[asyncio.AbstractEventLoop] = None
_tareas: Set[asyncio.Task] = set()


# ─── CONEXIONES ───────────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()   # un envío a la vez (mensajes no se entremezclan)

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active_connections.add(ws)
        logger.info(f"✓ Cliente conectado. Total: {len(self.active_connections)}")

    def disconnect(self, ws: WebSocket):
        self.active_connections.discard(ws)
        logger.info(f"✗ Cliente desconectado. Total: {len(self.active_connections)}")

    async def broadcast_json(self, tipo: str, **datos) -> None:
        mensaje = {"tipo": tipo, "timestamp": datetime.now().isoformat(), **datos}
        async with self._lock:
            for conn in list(self.active_connections):
                try:
                    await conn.send_json(mensaje)
                except Exception as e:
                    logger.debug(f"[DEBUG_WS] Error emitiendo JSON: {e}")
                    self.active_connections.discard(conn)


manager = ConnectionManager()


def emit_desde_hilo(tipo: str, **datos) -> None:
    """Callable thread-safe que recibe core_bridge. No espera el envío."""
    if _loop is not None and _loop.is_running():
        asyncio.run_coroutine_threadsafe(manager.broadcast_json(tipo, **datos), _loop)


# ─── CARGA DEL NÚCLEO EN SEGUNDO PLANO ────────────────────────────────────

def _cargar_core() -> None:
    global core_bridge
    try:
        logger.info("⏳ Cargando core_bridge (puede tardar: embeddings, BD, índices)...")
        core_bridge = importlib.import_module("core_bridge")
        if hasattr(core_bridge, "registrar_emit_gui"):
            core_bridge.registrar_emit_gui(emit_desde_hilo)     # la aureola reacciona a la voz de órdenes de WhatsApp
        for _nombre in ("telegram_bot", "whatsapp"):          # la aureola reacciona a Telegram y WhatsApp
            try:
                _m = importlib.import_module(_nombre)
                if hasattr(_m, "registrar_gui"):
                    _m.registrar_gui(emit_desde_hilo)
            except Exception as _e:
                logger.warning("registrar_gui(%s) no disponible: %s", _nombre, _e)
        if core_bridge.inicializar():
            _estado_core["estado"] = "listo"
            logger.info("✓ core_bridge listo")
        else:
            _estado_core.update(estado="error", error="inicializar() devolvió False")
            logger.error("❌ core_bridge no pudo inicializar")
    except Exception as e:
        _estado_core.update(estado="error", error=f"{type(e).__name__}: {e}")
        logger.error(f"❌ No se pudo cargar core_bridge: {e}", exc_info=True)
    if _loop is not None:
        asyncio.run_coroutine_threadsafe(
            manager.broadcast_json("servidor_listo", core=_estado_core["estado"],
                                   error=_estado_core["error"]), _loop)


# ─── ESTADO DEL SISTEMA (barra inferior) ──────────────────────────────────

_ollama_ok = False


def _leer_metricas() -> dict:
    datos = {"red": True, "ollama": _ollama_ok}
    try:
        import psutil
        datos["cpu"] = round(psutil.cpu_percent(interval=None))
        datos["ram"] = round(psutil.virtual_memory().percent)
        datos["disco"] = round(psutil.disk_usage(Path.home().anchor or "/").percent)
    except Exception as e:
        logger.debug(f"[DEBUG_METRICAS] Error leyendo métricas: {e}")
    return datos


def _sondear_ollama() -> None:
    global _ollama_ok
    try:
        import requests
        _ollama_ok = requests.get("http://localhost:11434/", timeout=1).status_code == 200
    except Exception:
        _ollama_ok = False


async def _bucle_estado_sistema():
    ciclo = 0
    while True:
        await asyncio.sleep(3)
        if not manager.active_connections:
            continue
        if ciclo % 5 == 0:
            await _loop.run_in_executor(executor, _sondear_ollama)
        ciclo += 1
        datos = await _loop.run_in_executor(executor, _leer_metricas)
        await manager.broadcast_json("estado_sistema", data=datos)


async def _bucle_proactivo():
    """Recordatorios vencidos, alertas de salud y sugerencias de hábitos → GUI."""
    await asyncio.sleep(10)
    while True:
        await asyncio.sleep(INTERVALO_PROACTIVO_S)
        if not manager.active_connections or core_bridge is None or _estado_core["estado"] != "listo":
            continue
        try:
            eventos = await _loop.run_in_executor(executor, core_bridge.obtener_eventos_proactivos)
            for tipo, datos in eventos:
                await manager.broadcast_json(tipo, **datos)
        except Exception as e:
            logger.debug(f"[DEBUG_PROACTIVO] {e}")


# ─── CICLO DE VIDA ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()

    if PUERTO_ACTIVO != PUERTO_BASE:
        logger.warning(f"⚠️ Puerto {PUERTO_BASE} no disponible/no usado. SARA corre en http://{HOST}:{PUERTO_ACTIVO}")
    else:
        logger.info("🚀 SARA Server arriba en http://%s:%s", HOST, PUERTO_ACTIVO)

    for f in ("index.html", "app.js", "style.css"):
        ruta = _archivo_gui(f)
        if ruta:
            logger.info("✓ %s: %s", f, ruta)
        else:
            logger.error("❌ No encuentro %s (busqué en static/, templates/ y junto a server.py)", f)

    threading.Thread(target=_cargar_core, name="sara-core-loader", daemon=True).start()
    tareas = [asyncio.create_task(_bucle_estado_sistema()), asyncio.create_task(_bucle_proactivo())]
    yield
    for t in tareas:
        t.cancel()
    if stt is not None:
        try:
            stt.desactivar()
        except Exception:
            pass
    executor.shutdown(wait=False, cancel_futures=True)
    logger.info("🛑 SARA Server detenido")


app = FastAPI(lifespan=lifespan)


def _servir(nombre: str, tipo: str):
    ruta = _archivo_gui(nombre)
    if ruta is None:
        return HTMLResponse(f"No encuentro {nombre}", status_code=404)
    return FileResponse(ruta, media_type=tipo, headers={"Cache-Control": "no-store"})


@app.get("/static/app.js")
async def static_app_js():
    return _servir("app.js", "application/javascript")


@app.get("/static/style.css")
async def static_style_css():
    return _servir("style.css", "text/css")


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    ruta = _archivo_gui("index.html")
    if ruta:
        return FileResponse(ruta, headers={"Cache-Control": "no-store"})
    return HTMLResponse("<h3 style='font-family:sans-serif;color:#eee;background:#0a0e27;padding:2em'>"
                        "No encuentro index.html</h3>", status_code=500)


@app.get("/health")
async def health():
    return {"status": "ok", "core": _estado_core["estado"], "error": _estado_core["error"],
            "puerto": PUERTO_ACTIVO,
            "index": _archivo_gui("index.html") is not None,
            "app_js": _archivo_gui("app.js") is not None,
            "style_css": _archivo_gui("style.css") is not None,
            "conexiones_activas": len(manager.active_connections)}


# ─── APARIENCIA DE LA GUI (menú de personalización: forma de la aureola y fondo) ───
# Datos del usuario, creados en tiempo de ejecución (apariencia_gui.json no viaja en el paquete de distribución).

ARCHIVO_APARIENCIA = BASE_DIR / "apariencia_gui.json"
_ID_APARIENCIA = re.compile(r"^[a-z0-9_]{1,24}$")


def _http_permitido(request: Request) -> bool:
    """Misma política que el WebSocket: solo el host/origen propios (frena también el «DNS rebinding»)."""
    hosts, origenes = _hosts_origenes_permitidos()
    host = (request.headers.get("host") or "").strip().lower()
    if host and host not in hosts:
        return False
    origin = request.headers.get("origin")
    return origin is None or origin.strip().lower().rstrip("/") in origenes


@app.get("/api/apariencia")
async def apariencia_leer(request: Request):
    if not _http_permitido(request):
        return JSONResponse({"error": "origen no permitido"}, status_code=403)
    try:
        datos = json.loads(ARCHIVO_APARIENCIA.read_text(encoding="utf-8"))
        return {"forma": str(datos.get("forma", "")), "fondo": str(datos.get("fondo", ""))}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("No pude leer %s: %s", ARCHIVO_APARIENCIA.name, e)
        return {}


@app.post("/api/apariencia")
async def apariencia_guardar(request: Request):
    if not _http_permitido(request):
        return JSONResponse({"error": "origen no permitido"}, status_code=403)
    try:
        datos = await request.json()
        forma, fondo = str(datos.get("forma", "")), str(datos.get("fondo", ""))
        if not (_ID_APARIENCIA.match(forma) and _ID_APARIENCIA.match(fondo)):
            return JSONResponse({"error": "valores no válidos"}, status_code=400)
        ARCHIVO_APARIENCIA.write_text(json.dumps({"forma": forma, "fondo": fondo}, ensure_ascii=False), encoding="utf-8")
        return {"ok": True}
    except Exception as e:
        logger.error("No pude guardar la apariencia: %s", e)
        return JSONResponse({"error": "no se pudo guardar"}, status_code=500)


# ─── VOZ DE SARA (menú «Voz de SARA»: lista de voces y cambio en caliente) ───
# voz.py guarda la elección en voz_config.json; esa voz la usan también Telegram, WhatsApp y el lector.

try:
    import voz as _voz
except Exception as _e_voz:                                    # módulo opcional: sin él, el menú oculta la sección
    _voz = None
    logger.warning("⚠️  voz no disponible para el menú de voces: %s", _e_voz)

_MUESTRA_VOZ = "Hola, soy Sara. Así suena mi voz."


@app.get("/api/voz")
async def voz_leer(request: Request):
    if not _http_permitido(request):
        return JSONResponse({"error": "origen no permitido"}, status_code=403)
    if _voz is None or not hasattr(_voz, "set_voz"):
        return JSONResponse({"error": "voz no disponible"}, status_code=503)
    # listar_voces() puede consultar las voces de Windows (PowerShell, ~1 s la primera vez): fuera del bucle de eventos
    voces = await asyncio.to_thread(_voz.listar_voces)
    edge = _voz.estado_edge() if hasattr(_voz, "estado_edge") else {"estado": "desconocido", "error": ""}
    suena = await asyncio.to_thread(_voz.nombre_que_suena) if hasattr(_voz, "nombre_que_suena") else ""
    return {"actual": _voz.get_voz(), "voces": voces, "edge": edge, "suena_con": suena}


@app.post("/api/voz")
async def voz_cambiar(request: Request):
    if not _http_permitido(request):
        return JSONResponse({"error": "origen no permitido"}, status_code=403)
    if _voz is None or not hasattr(_voz, "set_voz"):
        return JSONResponse({"error": "voz no disponible"}, status_code=503)
    try:
        datos = await request.json()
        if datos.get("accion") == "reintentar":                # «Reintentar neuronales»: prueba Edge-TTS ya, sin esperar la pausa
            if not hasattr(_voz, "reintentar_edge"):
                return JSONResponse({"error": "voz.py anterior a la v3.3"}, status_code=503)
            recuperado = await asyncio.to_thread(_voz.reintentar_edge)
            return {"ok": True, "recuperado": bool(recuperado), "edge": _voz.estado_edge()}
        elegida = str(datos.get("voz", ""))
        if not await asyncio.to_thread(_voz.set_voz, elegida):   # lista cerrada: cualquier otro id se rechaza
            return JSONResponse({"error": "voz no válida"}, status_code=400)
        if datos.get("probar"):                                # muestra con la voz nueva (la aureola reacciona)
            _voz.hablar(_MUESTRA_VOZ, emit=emit_desde_hilo, forzar=True, interrumpir=True, muletilla=False)
        return {"ok": True, "actual": _voz.get_voz()}
    except Exception as e:
        logger.error("No pude cambiar la voz: %s", e)
        return JSONResponse({"error": "no se pudo cambiar la voz"}, status_code=500)


# ─── PROCESAMIENTO DE UN MENSAJE (tarea independiente) ────────────────────

async def _avisar_respuesta_tardia(texto: str, fut) -> None:
    """La operación ya se dio por perdida (timeout) pero el hilo terminó después: se muestra su respuesta."""
    try:
        if fut.cancelled() or fut.exception():
            return
        r = fut.result() or {}
        await manager.broadcast_json(
            "notificacion", nivel="exito" if r.get("exito") else "aviso",
            texto=f"Respuesta tardía a «{texto[:40]}»: {r.get('texto', '')}")
    except Exception as e:
        logger.debug(f"[DEBUG_TARDIA] {e}")


async def _manejar_mensaje(texto: str, origen: str = "texto") -> None:
    """SIEMPRE termina emitiendo resultado_accion, o la GUI queda bloqueada.
    origen: "texto" (teclado) o "voz" (micrófono). El puente conversacional solo puede declarar «ruido» si es voz."""
    resultado = {"exito": False, "texto": "Error desconocido"}
    ctx = {"vivo": True}
    fut = None

    def emit(tipo: str, **datos) -> None:
        # Tras un timeout el hilo del núcleo sigue vivo y seguiría emitiendo estado_procesando /
        # resultado_clasificacion: eso re-bloqueaba la GUI. Una vez terminada la operación solo se deja
        # pasar el estado de voz (la respuesta tardía se sigue leyendo en voz alta).
        if ctx["vivo"] or tipo == "voz_estado":
            emit_desde_hilo(tipo, **datos)

    try:
        if core_bridge is None or _estado_core["estado"] != "listo":
            resultado = {"exito": False,
                         "texto": "SARA todavía está cargando su núcleo, intenta en unos segundos."
                         if _estado_core["estado"] == "cargando"
                         else f"El núcleo no arrancó: {_estado_core['error']}"}
            return
        await manager.broadcast_json("estado_procesando", etapa="normalización",
                                     timeout_segundos=TIMEOUT_MENSAJE_S)
        fut = _loop.run_in_executor(executor, core_bridge.procesar_mensaje, texto, emit, origen)
        resultado = await asyncio.wait_for(asyncio.shield(fut), timeout=TIMEOUT_MENSAJE_S)
    except asyncio.TimeoutError:
        logger.error("✗ Timeout procesando '%s'", texto[:60])
        resultado = {"exito": False, "texto": f"La operación tardó más de {TIMEOUT_MENSAJE_S}s."}
        if fut is not None:
            fut.add_done_callback(lambda f: _lanzar(_avisar_respuesta_tardia(texto, f)))
    except Exception as e:
        logger.error("✗ Error interno procesando mensaje: %s", e, exc_info=True)
        resultado = {"exito": False, "texto": "Error interno detectado. Revisa sara_server_debug.log."}
    finally:
        ctx["vivo"] = False
        await manager.broadcast_json("resultado_accion", exito=bool(resultado.get("exito")),
                                     texto=resultado.get("texto", ""))


async def _manejar_sugerencia(data: dict) -> None:
    """Respuesta del usuario a una sugerencia de hábitos (activar / ignorar)."""
    if core_bridge is None or _estado_core["estado"] != "listo":
        return
    try:
        r = await asyncio.wait_for(
            _loop.run_in_executor(executor, core_bridge.responder_sugerencia,
                                  data.get("id_sugerencia"), data.get("respuesta", "ignorar"), emit_desde_hilo),
            timeout=TIMEOUT_MENSAJE_S)
        await manager.broadcast_json("notificacion", texto=r.get("texto", ""),
                                     nivel="exito" if r.get("exito") else "error")
    except Exception as e:
        logger.error("✗ Error en respuesta_sugerencia: %s", e, exc_info=True)
        await manager.broadcast_json("notificacion", texto="No pude procesar tu respuesta a la sugerencia.",
                                     nivel="error")


def _lanzar(coro) -> None:
    t = asyncio.create_task(coro)
    _tareas.add(t)
    t.add_done_callback(_tareas.discard)


# ─── SEGURIDAD DEL WEBSOCKET ──────────────────────────────────────────────
# Los navegadores NO aplican la política de mismo origen a los WebSockets: cualquier página web abierta en tu
# navegador podría conectarse a ws://127.0.0.1:<puerto>/ws y mandar órdenes a SARA como si las escribieras tú
# (ataque «cross-site WebSocket hijacking»). Por eso se exige que Host y Origin sean los de la propia GUI.
# Sin cabecera Origin (cliente que no es un navegador: scripts locales, pruebas) se acepta, porque un proceso
# local ya tiene acceso a tu equipo. Si tu ventana carga la GUI desde otro origen, agrégalo en la variable de
# entorno SARA_WS_ORIGENES_EXTRA (separados por comas); el rechazo queda en el log con el origen exacto.

def _hosts_origenes_permitidos():
    hosts = {f"127.0.0.1:{PUERTO_ACTIVO}", f"localhost:{PUERTO_ACTIVO}"}
    origenes = {f"http://{h}" for h in hosts}
    extra = os.environ.get("SARA_WS_ORIGENES_EXTRA", "")
    origenes |= {o.strip().lower().rstrip("/") for o in extra.split(",") if o.strip()}
    return hosts, origenes


def _ws_permitido(ws: WebSocket) -> bool:
    hosts, origenes = _hosts_origenes_permitidos()
    host = (ws.headers.get("host") or "").strip().lower()
    if host and host not in hosts:                       # también frena el «DNS rebinding»
        return False
    origin = ws.headers.get("origin")
    if origin is not None and origin.strip().lower().rstrip("/") not in origenes:
        return False
    return True


# ─── WEBSOCKET ────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if not _ws_permitido(websocket):
        logger.warning("🚫 WebSocket rechazado (host=%r, origin=%r): no es la GUI de SARA",
                       websocket.headers.get("host"), websocket.headers.get("origin"))
        await websocket.close(code=1008)
        return
    await manager.connect(websocket)
    try:
        await websocket.send_json({"tipo": "servidor_listo", "core": _estado_core["estado"],
                                   "error": _estado_core["error"],
                                    "timestamp": datetime.now().isoformat()})
        if stt is not None:
            await websocket.send_json({"tipo": "stt_estado", **stt.estado_actual()})
        while True:
            data = await websocket.receive_json()
            tipo = data.get("tipo", "unknown")

            if tipo == "mensaje_usuario":
                texto = str(data.get("texto") or "").strip()[:MAX_CARACTERES_MENSAJE]
                # Solo se aceptan «texto» y «voz»: la GUI nunca puede declararse un canal remoto (whatsapp / telegram).
                origen = "voz" if data.get("origen") == "voz" else "texto"
                logger.info("📨 mensaje_usuario (%s): %s", origen, texto[:80])
                if not texto:
                    await manager.broadcast_json("resultado_accion", exito=False, texto="Mensaje vacío")
                else:
                    _lanzar(_manejar_mensaje(texto, origen))      # NO se espera: el loop sigue recibiendo

            elif tipo == "respuesta_confirmacion":
                ok = core_bridge is not None and core_bridge.resolver_confirmacion(
                    data.get("id_confirmacion"), data.get("respuesta", "no"), data.get("opcion"))

            elif tipo == "respuesta_sugerencia":
                logger.info("📨 respuesta_sugerencia: %s", data.get("respuesta"))
                _lanzar(_manejar_sugerencia(data))

            elif tipo == "lector_leer":
                texto_lector = data.get("texto") or ""
                logger.info("📨 lector_leer: %d caracteres", len(texto_lector))
                if lector_voz is None:
                    await manager.broadcast_json("lector_estado", estado="error", actual=0, total=0,
                                                 puede_reanudar=False, mensaje="El lector de voz no está disponible.")
                else:
                    lector_voz.leer(texto_lector, emit_desde_hilo)      # retorna al instante (hilo propio)

            elif tipo == "lector_detener":
                if lector_voz is not None:
                    lector_voz.detener("usuario")

            elif tipo == "lector_reanudar":
                if lector_voz is not None:
                    lector_voz.reanudar(emit_desde_hilo)

            elif tipo == "stt_activar":
                if stt is None:
                    await manager.broadcast_json("stt_estado", estado="error", activo=False, umbral=0,
                                                 mensaje="La voz de entrada no está disponible (revisa stt.py y el log).")
                else:
                    stt.activar(emit_desde_hilo, data.get("umbral"))      # retorna al instante (hilo propio)

            elif tipo == "stt_desactivar":
                if stt is not None:
                    stt.desactivar()

            elif tipo == "stt_umbral":
                if stt is not None:
                    stt.set_umbral(data.get("valor"), bool(data.get("guardar")))

            elif tipo == "ping":
                await websocket.send_json({"tipo": "pong"})

            else:
                logger.warning("⚠️  Tipo desconocido: %s", tipo)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("Error en WebSocket: %s", e, exc_info=True)
    finally:
        manager.disconnect(websocket)


if __name__ == "__main__":
    try:
        uvicorn.run(app, host=HOST, port=PUERTO_ACTIVO, log_level="debug")
    except OSError as e:
        logger.error("❌ No se pudo abrir %s:%s (%s).", HOST, PUERTO_ACTIVO, e)
