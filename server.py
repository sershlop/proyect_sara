# 📁 server.py — SARA GUI Bridge v1.0
# Servidor FastAPI + WebSocket que corre en hilo paralelo
# No modifica ni interfiere con sara.py ni run()
# Puerto por defecto: 8765

import asyncio
import json
import threading
import time
from typing import Optional, Set

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
    FASTAPI_DISPONIBLE = True
except ImportError:
    FASTAPI_DISPONIBLE = False

# ── Estado global ─────────────────────────────────────────────────────────────
_clientes_ws: Set[WebSocket]               = set()
_loop: Optional[asyncio.AbstractEventLoop] = None
_servidor_activo                           = False
_app                                       = None
_cola_eventos: list                        = []   # buffer antes de que el loop arranque
_recibir_comando_fn                        = None  # referencia a procesar_entrada_externa()


# ══════════════════════════════════════════════════════════════════════════════
#  API PÚBLICA — llamar desde cualquier módulo de SARA
# ══════════════════════════════════════════════════════════════════════════════

def emitir(tipo: str, **kwargs):
    """
    Emite un evento JSON a todos los clientes WebSocket conectados.
    Thread-safe. Nunca lanza excepciones.

    Uso:
        server.emitir("thinking", fase="procesando")
        server.emitir("respuesta", texto="Hecho.", confianza=0.95)
        server.emitir("estado", qwen=True, voz=False)
    """
    if not FASTAPI_DISPONIBLE or not _servidor_activo:
        return

    evento = {"tipo": tipo, "ts": time.time(), **kwargs}

    if _loop is None or _loop.is_closed():
        _cola_eventos.append(evento)
        return

    try:
        asyncio.run_coroutine_threadsafe(_broadcast(evento), _loop)
    except Exception:
        pass


def servidor_activo() -> bool:
    return _servidor_activo


def clientes_conectados() -> int:
    return len(_clientes_ws)


# ══════════════════════════════════════════════════════════════════════════════
#  BROADCAST INTERNO
# ══════════════════════════════════════════════════════════════════════════════

async def _broadcast(evento: dict):
    if not _clientes_ws:
        return
    texto  = json.dumps(evento, ensure_ascii=False, default=str)
    muertos = set()
    for ws in list(_clientes_ws):
        try:
            await ws.send_text(texto)
        except Exception:
            muertos.add(ws)
    for ws in muertos:
        _clientes_ws.discard(ws)


# ══════════════════════════════════════════════════════════════════════════════
#  FASTAPI APP
# ══════════════════════════════════════════════════════════════════════════════

def _crear_app() -> "FastAPI":
    app = FastAPI(
        title="SARA API",
        version="0.3.0",
        docs_url=None,
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        _clientes_ws.add(ws)

        # Estado inicial al conectar
        try:
            from config import USAR_QWEN, USAR_GEMINI_BACKUP, USAR_GROQ_BACKUP, MODO_VOZ, VERSION
            import external_service
            await ws.send_text(json.dumps({
                "tipo":    "conectado",
                "version": VERSION,
                "estado": {
                    "qwen":   external_service.qwen_disponible(),
                    "gemini": external_service.gemini_disponible(),
                    "groq":   external_service.groq_disponible(),
                    "voz":    MODO_VOZ,
                }
            }))
        except Exception:
            pass

        # Vaciar cola de eventos pendientes
        for evento in list(_cola_eventos):
            try:
                await ws.send_text(json.dumps(evento, ensure_ascii=False, default=str))
            except Exception:
                break
        _cola_eventos.clear()

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    datos  = json.loads(raw)
                    accion = datos.get("accion", "")

                    if accion == "comando":
                        texto = datos.get("texto", "").strip()
                        if texto and _recibir_comando_fn:
                            threading.Thread(
                                target=_recibir_comando_fn,
                                args=(texto,),
                                daemon=True
                            ).start()

                    elif accion == "activar_voz":
                        if _recibir_comando_fn:
                            threading.Thread(
                                target=_recibir_comando_fn,
                                args=("activar modo voz",),
                                daemon=True
                            ).start()

                    elif accion == "desactivar_voz":
                        if _recibir_comando_fn:
                            threading.Thread(
                                target=_recibir_comando_fn,
                                args=("desactivar modo voz",),
                                daemon=True
                            ).start()

                    elif accion == "ping":
                        await ws.send_text(json.dumps({"tipo": "pong"}))

                    elif accion == "get_estado":
                        try:
                            import external_service
                            from config import MODO_VOZ
                            await ws.send_text(json.dumps({
                                "tipo": "estado",
                                "qwen":   external_service.qwen_disponible(),
                                "gemini": external_service.gemini_disponible(),
                                "groq":   external_service.groq_disponible(),
                                "voz":    MODO_VOZ,
                            }))
                        except Exception:
                            pass

                except json.JSONDecodeError:
                    pass

        except WebSocketDisconnect:
            _clientes_ws.discard(ws)
        except Exception:
            _clientes_ws.discard(ws)

    @app.get("/health")
    async def health():
        return {"ok": True, "clientes": len(_clientes_ws)}

    @app.get("/estado")
    async def get_estado():
        try:
            import external_service
            from config import MODO_VOZ, VERSION
            return {
                "version": VERSION,
                "activo":  True,
                "agentes": {
                    "qwen":   external_service.qwen_disponible(),
                    "gemini": external_service.gemini_disponible(),
                    "groq":   external_service.groq_disponible(),
                },
                "voz": MODO_VOZ,
                "clientes_ws": len(_clientes_ws),
            }
        except Exception as e:
            return {"error": str(e)}

    return app


# ══════════════════════════════════════════════════════════════════════════════
#  ARRANQUE EN HILO PARALELO
# ══════════════════════════════════════════════════════════════════════════════

def iniciar_servidor(recibir_comando_fn, puerto: int = 8765):
    """
    Inicia FastAPI + uvicorn en un hilo daemon.
    No bloquea el hilo principal de SARA.

    Args:
        recibir_comando_fn: función que recibe un str y procesa la entrada
        puerto: puerto TCP (default 8765)

    Returns:
        True si arrancó, False si FastAPI no está instalado
    """
    global _loop, _servidor_activo, _app, _recibir_comando_fn

    if not FASTAPI_DISPONIBLE:
        print("[GUI] FastAPI/uvicorn no instalados — GUI desactivada.")
        print("[GUI] Instala con: pip install fastapi uvicorn")
        return False

    _recibir_comando_fn = recibir_comando_fn
    _app = _crear_app()

    def _run():
        global _loop, _servidor_activo
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        _servidor_activo = True

        config = uvicorn.Config(
            app=_app,
            host="127.0.0.1",
            port=puerto,
            loop="asyncio",
            log_level="error",
            access_log=False,
        )
        server = uvicorn.Server(config)
        _loop.run_until_complete(server.serve())

    hilo = threading.Thread(target=_run, name="SARA-GUI-Server", daemon=True)
    hilo.start()
    time.sleep(0.8)   # dar tiempo a que uvicorn arranque
    return True