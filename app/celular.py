# 📱 celular.py — SARA (laptop) ⇄ SARA móvil (Android) (v2)
# Servidor de enlace SIN dependencias para el protocolo (solo librería estándar); la huella y los archivos usan `cryptography`.
# El celular inicia siempre la conexión:
#   POST /pair     → vincula con el código de 6 dígitos (/celular vincular); el celular manda su clave PÚBLICA de huella
#   POST /poll     → long-poll de ~25 s: órdenes y avisos de la laptop
#   POST /send     → orden / resultado / evento / confirmación / «descargado»
#   POST /archivo  → (v2) el celular descarga un trozo cifrado de un adjunto
#   POST /subida   → (v2) el celular sube un trozo cifrado a la laptop
# Todo viaja con firma HMAC-SHA256 (secreto del emparejamiento) + marca de tiempo + anti-repetición. Los archivos,
# además, van cifrados con AES-256-GCM (transfer_seguro.py): el enlace es HTTP, el contenido no.
# HUELLA (verificación fuerte): la clave privada vive en el Keystore del celular y solo firma tras la huella. Cuando una
# orden es sensible y VERIFICACION_ACTIVA, la laptop contesta {"requiere_auth", "reto"}; la app firma el reto y reenvía.
#   · nivel «ventana»: pantalla, foto, /donde, archivos, subidas → la huella abre DESBLOQUEO_VENTANA_S (300 s)
#   · nivel «fresca»: apagar/reiniciar/suspender/hibernar y APAGAR la verificación → huella cada vez
#   · laptop → celular: SIN huella por defecto; solo lo que pongas en config.CEL_HUELLA_CAPS (p. ej. ("llamar", "sms")) la pide EN el celular
CELULAR_VERSION = "v2"
import base64, hashlib, hmac, importlib, json, logging, math, os, queue, re, secrets, shutil, socket, struct, subprocess, sys, threading, time, unicodedata, uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger("SARA-CELULAR")
try:
    import config
except Exception:
    config = None
try:
    import transfer_seguro as transfer
except Exception as _e:
    transfer = None
    log.warning("transfer_seguro no disponible: %s", _e)


def _c(n, d):
    return getattr(config, n, d) if config is not None else d


def _plano(s):
    return re.sub(r"[^a-z0-9 ]", " ", "".join(unicodedata.normalize("NFD", ch)[0] for ch in (s or "")).lower()).strip()


def _base() -> str:
    return str(getattr(config, "BASE_DIR", None) or os.path.dirname(os.path.abspath(__file__)))


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _mod(nombre: str):
    try:
        return importlib.import_module(nombre)
    except Exception:
        return None


def _interaccion_escritorio(accion: str, x: float, y: float, dx: float = 0, dy: float = 0) -> bool:
    if (os.name != "nt" or accion not in ("toque", "clic_derecho", "desplazar")
            or not 0 <= x <= 1 or not 0 <= y <= 1):
        return False
    try:
        import ctypes
        usuario = ctypes.windll.user32
        usuario.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        usuario.SetCursorPos.restype = ctypes.c_bool
        usuario.mouse_event.argtypes = [
            ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_size_t
        ]
        usuario.mouse_event.restype = None
        ancho, alto = usuario.GetSystemMetrics(0), usuario.GetSystemMetrics(1)
        if ancho <= 0 or alto <= 0:
            return False
        if not usuario.SetCursorPos(round(x * (ancho - 1)), round(y * (alto - 1))):
            return False
        if accion == "desplazar":
            if (isinstance(dx, bool) or isinstance(dy, bool) or not isinstance(dx, (int, float))
                    or not isinstance(dy, (int, float)) or not -1 <= dx <= 1 or not -1 <= dy <= 1):
                return False
            if abs(dy) >= abs(dx):
                usuario.mouse_event(0x0800, 0, 0, ctypes.c_uint32(round(-dy * 120 * 4)).value, 0)
            else:
                usuario.mouse_event(0x1000, 0, 0, ctypes.c_uint32(round(dx * 120 * 4)).value, 0)
        else:
            abajo, arriba = (0x0008, 0x0010) if accion == "clic_derecho" else (0x0002, 0x0004)
            usuario.mouse_event(abajo, 0, 0, 0, 0)
            usuario.mouse_event(arriba, 0, 0, 0, 0)
        log.info("interacción remota en laptop: %s en (%.3f, %.3f)", accion, x, y)
        return True
    except Exception:
        log.exception("no pude enviar la interacción remota a la laptop")
        return False


def _clic_escritorio(x: float, y: float) -> bool:
    return _interaccion_escritorio("toque", x, y)


def _mostrar_escritorio() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        usuario = ctypes.windll.user32
        usuario.keybd_event.argtypes = [
            ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint, ctypes.c_size_t
        ]
        usuario.keybd_event.restype = None
        tecla_win, tecla_d, soltar = 0x5B, 0x44, 0x0002
        usuario.keybd_event(tecla_win, 0, 0, 0)
        usuario.keybd_event(tecla_d, 0, 0, 0)
        usuario.keybd_event(tecla_d, 0, soltar, 0)
        usuario.keybd_event(tecla_win, 0, soltar, 0)
        return True
    except Exception:
        log.exception("no pude mostrar el escritorio de Windows")
        return False


def _leer_deslizamiento(texto: str) -> Optional[Tuple[float, float, float, float]]:
    partes = texto.split("|")
    if len(partes) != 5 or partes[0] != "DESLIZAR":
        return None
    try:
        x, y, dx, dy = map(float, partes[1:])
    except ValueError:
        return None
    if (not all(math.isfinite(v) for v in (x, y, dx, dy))
            or not 0 <= x <= 1 or not 0 <= y <= 1
            or not -1 <= dx <= 1 or not -1 <= dy <= 1
            or (dx == 0 and dy == 0)):
        return None
    return x, y, dx, dy


def _leer_arrastre(texto: str) -> Optional[Tuple[float, float, float, float]]:
    partes = texto.split("|")
    if len(partes) != 5 or partes[0] != "ARRASTRAR":
        return None
    try:
        x, y, destino_x, destino_y = map(float, partes[1:])
    except ValueError:
        return None
    if (not all(math.isfinite(v) for v in (x, y, destino_x, destino_y))
            or any(not 0 <= v <= 1 for v in (x, y, destino_x, destino_y))
            or (x == destino_x and y == destino_y)):
        return None
    return x, y, destino_x, destino_y


def _procesar_control_visor(dev: str, stream_id: str, texto: str) -> bool:
    vivo = _mod("pantalla_celular")
    if vivo is None or not vivo.control_activo(dev, stream_id):
        return False
    if texto == "INICIO":
        accion = {"tipo": "pantalla_celular", "accion": "inicio", "stream_id": stream_id}
    elif texto.startswith("DESLIZAR|"):
        movimiento = _leer_deslizamiento(texto)
        if movimiento is None:
            return False
        x, y, dx, dy = movimiento
        accion = {
            "tipo": "pantalla_celular", "accion": "desplazar", "stream_id": stream_id,
            "x": x, "y": y, "dx": dx, "dy": dy,
        }
    elif texto.startswith("ARRASTRAR|"):
        movimiento = _leer_arrastre(texto)
        if movimiento is None:
            return False
        x, y, destino_x, destino_y = movimiento
        accion = {
            "tipo": "pantalla_celular", "accion": "arrastrar", "stream_id": stream_id,
            "x": x, "y": y, "destino_x": destino_x, "destino_y": destino_y,
        }
    else:
        return False
    _cola(dev).put(accion)
    return True


# ─── ESTADO ───────────────────────────────────────────────────────────────
_lock = threading.RLock()
_ctx = threading.local()                    # dev / adjuntos / error de la orden que se está procesando en este hilo
_nucleo: Optional[Callable] = None
_resolver: Optional[Callable] = None
_server: Optional[ThreadingHTTPServer] = None
_dispositivos: Dict[str, dict] = {}         # dev -> {"secret","nombre","creado","pub"}
_colas: Dict[str, "queue.Queue"] = {}
_esperando: Dict[str, dict] = {}
_estado: Dict[str, dict] = {}
_vistos: Dict[str, float] = {}
_codigo = {"codigo": "", "vence": 0.0}
_pair = {"n": 0, "bloq": 0.0}
_ordenes: Dict[str, deque] = {}
_eventos: List[Tuple[str, dict]] = []
_estaba: Dict[str, bool] = {}
_retos: Dict[str, Tuple[str, float]] = {}   # reto -> (dev, creado)
_auth: Dict[str, float] = {}                # dev -> fin de la ventana de huella
_pend: List[dict] = []                      # avisos para el celular mientras estaba desconectado
_foto_pedida: Dict[str, float] = {}         # dev -> cuándo pidió la laptop una foto (la vista previa llega sin pedir huella)
_visores_pantalla_celular: Dict[str, subprocess.Popen] = {}
_locks_visores_pantalla_celular: Dict[str, threading.Lock] = {}


def registrar_nucleo(fn_procesar: Callable, fn_resolver: Callable) -> None:
    global _nucleo, _resolver
    _nucleo, _resolver = fn_procesar, fn_resolver


def _ruta() -> str:
    return os.path.join(_base(), "sara_celular.json")


def _ruta_pend() -> str:
    return os.path.join(_base(), "sara_celular_pendientes.json")


def _cargar() -> None:
    try:
        with open(_ruta(), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            with _lock:
                _dispositivos.update({k: v for k, v in d.items() if isinstance(v, dict) and v.get("secret")})
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("sara_celular.json ilegible: %s", e)
    try:
        with open(_ruta_pend(), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, list):
            _pend[:] = [p for p in d if isinstance(p, dict) and p.get("texto")]
    except Exception:
        pass


def _guardar() -> None:
    try:
        tmp = _ruta() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_dispositivos, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _ruta())
    except Exception as e:
        log.warning("no pude guardar los celulares vinculados: %s", e)


def _guardar_pend() -> None:
    try:
        tmp = _ruta_pend() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_pend, f, ensure_ascii=False)
        os.replace(tmp, _ruta_pend())
    except Exception as e:
        log.warning("no pude guardar los avisos pendientes: %s", e)


def _cola(dev: str) -> "queue.Queue":
    with _lock:
        return _colas.setdefault(dev, queue.Queue())


def _firma(secret: str, dev: str, ts: str, cuerpo: str) -> str:
    return hmac.new(secret.encode("utf-8"), f"{dev}.{ts}.{cuerpo}".encode("utf-8"), hashlib.sha256).hexdigest()


def _conectado(dev: str) -> bool:
    return time.time() - _estado.get(dev, {}).get("visto", 0) < float(_c("CEL_POLL_S", 25)) + 15


def _dispositivo_activo() -> Optional[str]:
    with _lock:
        vivos = [(d, _estado.get(d, {}).get("visto", 0)) for d in _dispositivos if _conectado(d)]
    return max(vivos, key=lambda x: x[1])[0] if vivos else None


# ─── HUELLA: retos, firmas y ventana ──────────────────────────────────────
def _verif() -> bool:
    m = _mod("interruptores")
    try:
        return bool(m.verificacion_activa()) if m else True
    except Exception:
        return True


def _firma_valida(dev: str, datos: str, firma_b64: str) -> bool:
    """¿Firmó `datos` la clave de huella de este celular? Falla cerrado (False) ante cualquier duda."""
    try:
        pub = _dispositivos.get(dev, {}).get("pub")
        if not pub or not firma_b64:
            return False
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        k = serialization.load_der_public_key(base64.b64decode(pub))
        k.verify(base64.b64decode(firma_b64), datos.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False


def _nuevo_reto(dev: str) -> str:
    with _lock:
        ahora = time.time()
        for k in [k for k, (_d, t) in _retos.items() if ahora - t > 60]:
            del _retos[k]
        if len(_retos) > 200:
            _retos.clear()
        reto = secrets.token_hex(16)
        _retos[reto] = (dev, ahora)
        return reto


def _verificar_auth(dev: str, reto: str, firma: str, texto: str) -> bool:
    with _lock:
        r = _retos.pop(reto, None)                       # un reto sirve una sola vez
    if not r or r[0] != dev or time.time() - r[1] > 60:
        return False
    return _firma_valida(dev, f"{reto}|{_sha(texto)}", firma)


def _ventana(dev: str) -> bool:
    return time.time() < _auth.get(dev, 0)


def _abrir_ventana(dev: str) -> None:
    _auth[dev] = time.time() + int(_c("DESBLOQUEO_VENTANA_S", 300))


def cerrar_ventanas() -> None:
    """interruptores.py lo llama al cambiar la verificación."""
    _auth.clear()


def _nivel_auth(texto: str) -> Optional[str]:
    """None = libre · «ventana» = huella válida por un rato · «fresca» = huella nueva cada vez."""
    ap = _mod("apagado")
    if ap is not None:
        try:
            p = ap.interpretar(texto)
            if p:
                a = p.get("accion")
                return None if a == "cancelar" else "ventana" if a == "bloquear" else "fresca"
        except Exception:
            return "fresca"
    it = _mod("interruptores")
    if it is not None:
        try:
            r = it.interpretar(texto)
            if r:
                cual, v = r
                if cual == "verificacion":
                    return "fresca" if v is False else None      # apagar el candado exige huella nueva; encenderlo es libre
                return "ventana" if v is True else None
        except Exception:
            return "fresca"
    for nombre in ("pantalla", "antirrobo", "envio_archivos"):
        m = _mod(nombre)
        try:
            if m is not None and m.es_comando(texto):
                return "ventana"
        except Exception:
            return "ventana"
    vivo = _mod("pantalla_vivo")
    if vivo is not None and vivo.interpretar(texto):
        return "ventana"
    return None


def _exigir_auth(dev: str, d: dict, texto: str, nivel: str) -> Optional[dict]:
    """None = puede continuar. Si no, la respuesta que pide la huella (o explica por qué no se puede)."""
    if nivel is None or not _verif():
        return None
    if not _dispositivos.get(dev, {}).get("pub"):
        return {"exito": False, "ok": False,
                "texto": "🔐 Este celular no tiene clave de huella registrada. Desvincúlalo y vuelve a vincularlo (/celular vincular)."}
    a = d.get("auth")
    if isinstance(a, dict) and _verificar_auth(dev, str(a.get("reto", "")), str(a.get("firma", "")), texto):
        if nivel == "ventana":
            _abrir_ventana(dev)
        return None
    if nivel == "ventana" and _ventana(dev):
        return None
    return {"exito": False, "ok": False, "requiere_auth": True, "reto": _nuevo_reto(dev), "nivel": nivel,
            "texto": "🔐 Confirma con tu huella."}


# ─── SERVIDOR HTTP ────────────────────────────────────────────────────────
class _H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _leer(self) -> str:
        n = int(self.headers.get("Content-Length") or 0)
        ruta = self.path.split("?")[0]
        tope = int(_c("CEL_MAX_CUERPO_SUBIDA", 1_500_000)) if ruta == "/subida" else \
            int(_c("CEL_PANTALLA_CELULAR_MAX_BYTES", 1_000_000)) * 2 if ruta == "/pantalla-celular" else 65536
        if n > tope:
            raise ValueError("cuerpo demasiado grande")
        return self.rfile.read(n).decode("utf-8") if n else ""

    def _responder(self, codigo: int, datos: dict, dev: Optional[str] = None) -> None:
        cuerpo = json.dumps(datos, ensure_ascii=False)
        b = cuerpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        if dev and dev in _dispositivos:
            ts = str(int(time.time()))
            self.send_header("X-Sara-Ts", ts)
            self.send_header("X-Sara-Sig", _firma(_dispositivos[dev]["secret"], dev, ts, cuerpo))
        self.end_headers()
        self.wfile.write(b)

    def _autenticar(self, cuerpo: str) -> Optional[str]:
        dev = self.headers.get("X-Sara-Dev", "")
        ts = self.headers.get("X-Sara-Ts", "")
        sig = self.headers.get("X-Sara-Sig", "")
        d = _dispositivos.get(dev)
        if not d or not ts.isdigit() or abs(time.time() - int(ts)) > 120:
            return None
        if not hmac.compare_digest(sig, _firma(d["secret"], dev, ts, cuerpo)):
            return None
        clave = dev + sig
        with _lock:
            ahora = time.time()
            for k in [k for k, t in _vistos.items() if ahora - t > 150]:
                del _vistos[k]
            if clave in _vistos:
                return None
            _vistos[clave] = ahora
            _estado.setdefault(dev, {})["visto"] = ahora
        return dev

    def do_POST(self):
        try:
            cuerpo = self._leer()
            ruta = self.path.split("?")[0]
            if ruta == "/pair":
                return self._pair(cuerpo)
            dev = self._autenticar(cuerpo)
            if dev is None:
                return self._responder(401, {"error": "firma"})
            if ruta == "/poll":
                return self._poll(dev)
            if ruta == "/pantalla-vivo":
                return self._pantalla_vivo(dev, cuerpo)
            if ruta == "/pantalla-celular":
                return self._pantalla_celular(dev, cuerpo)
            if ruta == "/send":
                return self._send(dev, cuerpo)
            if ruta == "/archivo":
                return self._archivo(dev, cuerpo)
            if ruta == "/subida":
                return self._subida(dev, cuerpo)
            self._responder(404, {"error": "ruta"})
        except Exception:
            log.exception("celular: error en la petición")
            self.close_connection = True
            try:
                self._responder(500, {"error": "interno"})
            except Exception:
                pass

    def _pair(self, cuerpo: str) -> None:
        d = json.loads(cuerpo or "{}")
        with _lock:
            ahora = time.time()
            if ahora < _pair["bloq"]:
                return self._responder(429, {"error": "bloqueado"})
            ok = bool(_codigo["codigo"]) and ahora < _codigo["vence"] and \
                hmac.compare_digest(str(d.get("code", "")), _codigo["codigo"])
            if not ok:
                _pair["n"] += 1
                if _pair["n"] >= 5:
                    _pair["n"], _pair["bloq"] = 0, ahora + 600
                    log.warning("🚨 Demasiados códigos de vinculación incorrectos: bloqueo 10 min")
                return self._responder(403, {"error": "codigo"})
            _codigo["codigo"] = ""
            _pair["n"] = 0
            dev = uuid.uuid4().hex[:8]
            nombre = str(d.get("nombre") or "Celular")[:40]
            pub = str(d.get("pub") or "")
            if pub:                                           # solo se guarda una clave pública válida (EC P-256)
                try:
                    from cryptography.hazmat.primitives import serialization
                    serialization.load_der_public_key(base64.b64decode(pub))
                except Exception:
                    log.warning("clave pública de huella inválida o falta `cryptography`: se vincula SIN huella")
                    pub = ""
            _dispositivos[dev] = {"secret": secrets.token_hex(32), "nombre": nombre, "creado": ahora, "pub": pub}
            _guardar()
            secreto = _dispositivos[dev]["secret"]
        log.warning("📱 Celular vinculado: %s (%s) · huella: %s", nombre, dev, "sí" if pub else "NO")
        _eventos.append(("notificacion", {"texto": f"📱 Celular vinculado: {nombre}" + ("" if pub else " (sin clave de huella)"),
                                          "nivel": "info" if pub else "aviso"}))
        self._responder(200, {"dev": dev, "secret": secreto, "laptop": socket.gethostname(), "huella": bool(pub),
                              "ips": _direcciones()})

    def _poll(self, dev: str) -> None:
        q = _cola(dev)
        _volcar_pendientes(dev)
        msgs: List[dict] = []
        try:
            msgs.append(q.get(timeout=float(_c("CEL_POLL_S", 25))))
            while True:
                msgs.append(q.get_nowait())
        except queue.Empty:
            pass
        try:
            self._responder(200, {"msgs": msgs, "ips": _direcciones()}, dev)
        except Exception:
            for m in msgs:
                q.put(m)
            raise

    def _pantalla_vivo(self, dev: str, cuerpo: str) -> None:
        d = json.loads(cuerpo or "{}")
        sid = str(d.get("stream") or "")
        vivo = _mod("pantalla_vivo")
        evento = vivo.sesion(dev, sid) if vivo and re.fullmatch(r"[0-9a-f]{10}", sid) else None
        if evento is None:
            return self._responder(404, {"error": "sesion"}, dev)
        if transfer is None or not transfer.cripto_disponible():
            vivo.cerrar(dev, sid)
            return self._responder(503, {"error": "cifrado_no_disponible"}, dev)
        pantalla = _mod("pantalla")
        capturar = getattr(pantalla, "capturar", None) if pantalla else None
        if capturar is None:
            vivo.cerrar(dev, sid)
            return self._responder(503, {"error": "captura_no_disponible"}, dev)
        metadatos = vivo.metadatos(dev, sid) or {"fps": 2, "escala": 2}
        imagen, motivo = capturar(escala=metadatos["escala"])
        if not imagen:
            vivo.cerrar(dev, sid)
            return self._responder(503, {"error": motivo or "captura_fallida"}, dev)

        ts = str(int(time.time()))
        respuesta = f"pantalla-vivo|{sid}"
        self.send_response(200)
        self.send_header("Content-Type", "application/x-sara-screen-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Sara-Ts", ts)
        self.send_header("X-Sara-Sig", _firma(_dispositivos[dev]["secret"], dev, ts, respuesta))
        self.end_headers()
        self.close_connection = True
        tam_max = 6 * 1024 * 1024
        indice = 0
        try:
            while not evento.is_set():
                if len(imagen) > tam_max:
                    log.warning("pantalla en vivo: fotograma sobre el límite (%d bytes)", len(imagen))
                    break
                cifrado = transfer.cifrar_trozo(
                    _dispositivos[dev]["secret"], sid, "down", indice, False, imagen
                )
                self.wfile.write(struct.pack(">QI", indice, len(cifrado)))
                self.wfile.write(cifrado)
                self.wfile.flush()
                indice += 1
                metadatos = vivo.metadatos(dev, sid)
                if metadatos is None:
                    break
                intervalo = 1.0 / metadatos["fps"]
                if metadatos["fps"] == 2:
                    intervalo = max(
                        intervalo,
                        max(0.25, min(2.0, float(_c("CEL_PANTALLA_VIVO_INTERVALO_S", 0.6)))),
                    )
                if evento.wait(intervalo):
                    break
                imagen, motivo = capturar(escala=metadatos["escala"])
                if not imagen:
                    log.info("pantalla en vivo detenida: %s", motivo or "captura no disponible")
                    _cola(dev).put({"tipo": "aviso", "id": uuid.uuid4().hex[:10],
                                    "texto": "La captura de pantalla en vivo se detuvo: " +
                                             str(motivo or "no está disponible.")[:200]})
                    break
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            log.debug("pantalla en vivo desconectada por el celular")
        except Exception:
            log.exception("falló la transmisión de pantalla en vivo")
        finally:
            vivo.cerrar(dev, sid)

    def _pantalla_celular(self, dev: str, cuerpo: str) -> None:
        vivo = _mod("pantalla_celular")
        if vivo is None or not _c("CEL_PANTALLA_CELULAR_ACTIVA", True):
            return self._responder(403, {"ok": False, "error": "desactivada"}, dev)
        d = json.loads(cuerpo or "{}")
        sid = str(d.get("stream") or "")
        if not re.fullmatch(r"[0-9a-f]{10}", sid):
            return self._responder(400, {"ok": False, "error": "sesion"}, dev)
        if d.get("accion") == "fin":
            actual = vivo.detener(dev, sid)
            if actual == sid:
                _cerrar_visor_pantalla_celular(dev)
            return self._responder(200, {"ok": actual == sid}, dev)
        indice = d.get("indice")
        try:
            indice = int(indice)
        except (TypeError, ValueError):
            return self._responder(400, {"ok": False, "error": "indice"}, dev)
        if transfer is None or not transfer.cripto_disponible():
            return self._responder(503, {"ok": False, "error": "cifrado_no_disponible"}, dev)
        try:
            if not vivo.activa(dev, sid):
                return self._responder(404, {"ok": False, "error": "sesion"}, dev)
            cifrado = base64.b64decode(str(d.get("fotograma") or ""), validate=True)
            if not cifrado or len(cifrado) > int(_c("CEL_PANTALLA_CELULAR_MAX_BYTES", 1_000_000)) + 16:
                raise ValueError("fotograma fuera de límite")
            imagen = transfer.descifrar_trozo(_dispositivos[dev]["secret"], sid, "up", indice, False, cifrado)
            if not imagen.startswith(b"\xff\xd8") or not imagen.endswith(b"\xff\xd9"):
                raise ValueError("fotograma no es JPEG")
        except Exception as e:
            log.warning("pantalla celular: fotograma rechazado para %s (%s)", dev, type(e).__name__)
            return self._responder(400, {"ok": False, "error": "fotograma_invalido"}, dev)
        if not vivo.aceptar_fotograma(dev, sid, indice, imagen):
            return self._responder(409, {"ok": False, "error": "sesion_o_indice"}, dev)
        _enviar_fotograma_visor_pantalla_celular(dev, imagen)
        self._responder(200, {"ok": True}, dev)

    def _send(self, dev: str, cuerpo: str) -> None:
        d = json.loads(cuerpo or "{}")
        tipo = d.get("tipo")
        if tipo == "pantalla_control":
            vivo = _mod("pantalla_vivo")
            sid = str(d.get("stream") or "")
            if vivo is None or vivo.metadatos(dev, sid) is None:
                return self._responder(404, {"ok": False, "error": "sesion"}, dev)
            accion = d.get("accion")
            if accion == "control":
                activo = d.get("activo")
                ok = vivo.cambiar_control(dev, sid, activo)
                return self._responder(200 if ok else 400, {"ok": ok}, dev)
            if accion == "perfil":
                fps = d.get("fps")
                ok = vivo.cambiar_perfil(dev, sid, fps)
                return self._responder(200 if ok else 400, {"ok": ok}, dev)
            if accion == "inicio":
                if not vivo.control_activo(dev, sid):
                    return self._responder(403, {"ok": False, "error": "control_desactivado"}, dev)
                ok = _mostrar_escritorio()
                return self._responder(200 if ok else 503, {"ok": ok}, dev)
            if accion in ("toque", "clic_derecho", "desplazar"):
                x, y = d.get("x"), d.get("y")
                if not vivo.control_activo(dev, sid):
                    return self._responder(403, {"ok": False, "error": "control_desactivado"}, dev)
                if (isinstance(x, bool) or not isinstance(x, (int, float))
                        or isinstance(y, bool) or not isinstance(y, (int, float))
                        or not 0 <= x <= 1 or not 0 <= y <= 1):
                    return self._responder(400, {"ok": False, "error": "coordenadas"}, dev)
                if accion == "desplazar":
                    dx, dy = d.get("dx", 0), d.get("dy", 0)
                    if (isinstance(dx, bool) or isinstance(dy, bool) or not isinstance(dx, (int, float))
                            or not isinstance(dy, (int, float)) or not -1 <= dx <= 1 or not -1 <= dy <= 1):
                        return self._responder(400, {"ok": False, "error": "desplazamiento"}, dev)
                    ok = _interaccion_escritorio(accion, float(x), float(y), float(dx), float(dy))
                else:
                    ok = _interaccion_escritorio(accion, float(x), float(y))
                return self._responder(200 if ok else 503, {"ok": ok}, dev)
            return self._responder(400, {"ok": False, "error": "accion"}, dev)
        if tipo == "resultado":
            slot = _esperando.get(str(d.get("id")))
            if slot:
                slot["res"] = d
                slot["ev"].set()
            return self._responder(200, {"ok": True}, dev)
        if tipo == "evento":
            with _lock:
                _estado.setdefault(dev, {}).update({k: d[k] for k in ("bateria", "cargando", "red") if k in d})
            return self._responder(200, {"ok": True}, dev)
        if tipo == "pantalla_control_estado":
            vivo = _mod("pantalla_celular")
            sid = str(d.get("stream_id") or "")
            sesion_activa = vivo.activa(dev, sid) if vivo is not None else False
            activo = d.get("activo")
            accesibilidad = d.get("accesibilidad")
            solicitado_activo = d.get("solicitado_activo")
            if not _c("CEL_PANTALLA_CONTROL_AVISO_ACCESIBILIDAD", True):
                return self._responder(403, {"ok": False, "error": "funcion_desactivada"}, dev)
            if type(activo) is not bool or type(accesibilidad) is not bool or type(solicitado_activo) is not bool:
                return self._responder(400, {"ok": False, "error": "estado"}, dev)
            if not sesion_activa:
                return self._responder(404, {"ok": False, "error": "sesion"}, dev)
            if activo and not accesibilidad:
                activo = False
            if not vivo.cambiar_control(dev, sid, activo):
                return self._responder(409, {"ok": False, "error": "sesion"}, dev)
            mensaje = (
                "Control remoto no activado: habilita SARA en Ajustes → Accesibilidad del celular."
                if solicitado_activo and not accesibilidad else
                "Control remoto activado."
                if activo else
                "Control remoto desactivado."
            )
            _enviar_estado_control_visor(dev, sid, activo, mensaje)
            return self._responder(200, {"ok": True}, dev)
        if tipo == "confirmacion":
            if _resolver is not None:
                _resolver(str(d.get("id")), str(d.get("respuesta", "no")), d.get("opcion"))
            return self._responder(200, {"ok": True}, dev)
        if tipo == "descargado":
            if transfer is not None:
                transfer.liberar(dev, str(d.get("id", "")))
            return self._responder(200, {"ok": True}, dev)
        if tipo == "orden":
            return self._orden(dev, d)
        self._responder(400, {"error": "tipo"}, dev)

    def _archivo(self, dev: str, cuerpo: str) -> None:
        d = json.loads(cuerpo or "{}")
        if transfer is None or not transfer.cripto_disponible():
            return self._responder(200, {"ok": False, "error": "falta cryptography en la laptop"}, dev)
        r = transfer.leer_trozo(dev, str(d.get("id", "")), int(d.get("idx", 0)), _dispositivos[dev]["secret"])
        self._responder(200, r, dev)

    def _subida(self, dev: str, cuerpo: str) -> None:
        d = json.loads(cuerpo or "{}")
        if transfer is None or not transfer.cripto_disponible():
            return self._responder(200, {"ok": False, "texto": "Falta `cryptography` en la laptop (pip install cryptography)."}, dev)
        if int(d.get("idx", 0)) == 0:                         # la huella se exige al EMPEZAR; los trozos siguientes ya van dentro
            # Vista previa de una foto que PIDIÓ la laptop hace < 2 min: no se guarda en ningún lado, solo se muestra → sin huella
            pedida = bool(d.get("vista")) and time.time() - _foto_pedida.get(dev, 0) < 120
            pend = None if pedida else _exigir_auth(dev, d, "subida", "ventana")
            if pend:
                return self._responder(200, pend, dev)
        r = transfer.recibir_trozo(dev, _dispositivos[dev]["secret"], d)
        if r.get("ok") and r.get("fin"):
            if r.get("vista"):
                _mostrar_foto(dev, str(r.get("ruta", "")))
            else:
                _eventos.append(("notificacion", {"texto": f"📥 {r.get('texto')} (carpeta {transfer.carpeta_recibido()})", "nivel": "exito"}))
        self._responder(200, r, dev)

    def _orden(self, dev: str, d: dict) -> None:
        texto = str(d.get("texto") or "").strip()[:500]
        if not texto:
            return self._responder(200, {"exito": False, "texto": "No recibí ninguna orden."}, dev)
        with _lock:
            h = _ordenes.setdefault(dev, deque())
            ahora = time.time()
            while h and ahora - h[0] > 60:
                h.popleft()
            if len(h) >= int(_c("CEL_MAX_ORDENES_MIN", 20)):
                return self._responder(200, {"exito": False, "texto": "Demasiadas órdenes seguidas; espera un momento."}, dev)
            h.append(ahora)
        if _nucleo is None:
            return self._responder(200, {"exito": False, "texto": "SARA en la laptop todavía está arrancando."}, dev)
        nivel = _nivel_auth(texto) if _verif() else None
        if nivel:
            pend = _exigir_auth(dev, d, texto, nivel)
            if pend:
                return self._responder(200, pend, dev)

        vivo = _mod("pantalla_vivo")
        intencion_vivo = vivo.resolver_intencion(texto, "celular") if vivo else None
        if intencion_vivo:
            direccion, accion_vivo = intencion_vivo
            if direccion == "celular-a-laptop":
                texto_pantalla = ("activa la pantalla en vivo de mi celular" if accion_vivo == "iniciar"
                                  else "detén la pantalla en vivo de mi celular")
                ok, mensaje = ejecutar(texto_pantalla, "celular")
                return self._responder(200, {"exito": bool(ok), "texto": str(mensaje),
                                             "adjuntos": []}, dev)
        else:
            accion_vivo = None
        if accion_vivo == "iniciar":
            sid, motivo = vivo.iniciar(dev, solicitado_desde="celular")
            if sid:
                return self._responder(200, {
                    "exito": True,
                    "texto": "Abriendo la pantalla en vivo. Cierra el visor o di «detén la pantalla en vivo» para terminar.",
                    "pantalla_en_vivo": True,
                    "stream_id": sid,
                    "solicitado_desde": "celular",
                    "adjuntos": [],
                }, dev)
            return self._responder(200, {"exito": False, "texto": motivo, "adjuntos": []}, dev)
        if accion_vivo == "detener":
            detenida = vivo.detener(dev)
            texto_respuesta = "Pantalla en vivo detenida." if detenida else "No hay una pantalla en vivo activa."
            if detenida:
                _cola(dev).put({"tipo": "pantalla_vivo", "accion": "detener"})
            return self._responder(200, {
                "exito": True,
                "texto": texto_respuesta,
                "pantalla_en_vivo": False,
                "solicitado_desde": "celular",
                "adjuntos": [],
            }, dev)

        def emit(tipo_e, **datos):
            if tipo_e == "solicitud_confirmacion":
                _cola(dev).put({"tipo": "confirmar", "id": datos.get("id_confirmacion"),
                                "pregunta": datos.get("pregunta", ""), "opciones": datos.get("opciones") or [],
                                "timeout": datos.get("timeout_segundos", 30)})
        _ctx.dev, _ctx.adjuntos, _ctx.error = dev, [], ""
        try:
            r = _nucleo(texto, emit, bool(d.get("voz"))) or {}
        except Exception:
            log.exception("celular: el núcleo falló")
            r = {"exito": False, "texto": "Error interno en la laptop."}
        finally:
            adj = list(getattr(_ctx, "adjuntos", []))
            _ctx.dev = None
        self._responder(200, {"exito": bool(r.get("exito")), "texto": str(r.get("texto", ""))[:1500], "adjuntos": adj}, dev)


def arrancar() -> None:
    """Abre el servidor (idempotente). Lo llama core_bridge._arranque_pesado."""
    global _server
    if not _c("CEL_ACTIVO", True) or _server is not None:
        return
    _cargar()
    if transfer is not None:
        transfer.iniciar()
        if not transfer.cripto_disponible():
            log.warning("📱 Falta `cryptography`: sin huella ni archivos (pip install cryptography)")
    try:
        _server = ThreadingHTTPServer((str(_c("CEL_HOST", "0.0.0.0")), int(_c("CEL_PUERTO", 8770))), _H)
        _server.daemon_threads = True
        threading.Thread(target=_server.serve_forever, name="sara-celular", daemon=True).start()
        log.info("📱 Enlace con el celular escuchando en el puerto %s", _c("CEL_PUERTO", 8770))
    except OSError as e:
        _server = None
        log.warning("📱 No pude abrir el puerto %s: %s", _c("CEL_PUERTO", 8770), e)


# ─── ADJUNTOS (los usa core_bridge para devolver fotos, capturas y archivos al celular) ───
def adjuntar_imagen(png: bytes, caption: str = "", nombre: str = "captura.png") -> bool:
    """Misma firma que telegram_bot.enviar_foto: pantalla.ejecutar y antirrobo.ejecutar la usan tal cual."""
    dev = getattr(_ctx, "dev", None)
    if not dev or transfer is None:
        return False
    if not transfer.cripto_disponible():
        _ctx.error = "Falta `cryptography` en la laptop (pip install cryptography)."
        return False
    _ctx.adjuntos.append(transfer.registrar_bytes(dev, png, nombre))
    return True


def adjuntar_ruta(ruta: str, caption: str = "") -> bool:
    """Misma firma que telegram_bot.enviar_documento. Acepta archivos y carpetas (las comprime y cifra)."""
    dev = getattr(_ctx, "dev", None)
    if not dev or transfer is None:
        return False
    if not transfer.cripto_disponible():
        _ctx.error = "Falta `cryptography` en la laptop (pip install cryptography)."
        return False
    adj, err = transfer.registrar_ruta(dev, ruta)
    if adj is None:
        _ctx.error = err
        return False
    _ctx.adjuntos.append(adj)
    return True


def _mostrar_foto(dev: str, ruta: str) -> None:
    """Abre la ventana visor_foto.py con la foto que tomó el celular (Guardar en la laptop · Guardar en el celular · Cerrar).
    Corre en un hilo aparte: no bloquea la subida. Al terminar le dice al celular si debe guardarla en su Galería o borrarla."""
    visor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "visor_foto.py")

    def _hilo():
        enviado = False

        def _final(guardar: bool):
            _cola(dev).put({"tipo": "foto_final", "id": uuid.uuid4().hex[:10], "guardar": bool(guardar)})
        try:
            if not os.path.isfile(ruta):
                log.warning("📷 la foto de vista previa ya no existe: %s", ruta)
                return
            if not os.path.isfile(visor):
                log.warning("📷 falta visor_foto.py: abro la foto con el visor de Windows")
                os.startfile(ruta)
                return
            p = subprocess.Popen([sys.executable, visor, ruta, transfer.carpeta_recibido()], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for linea in p.stdout:
                linea = linea.strip()
                if linea == "CELULAR" and not enviado:
                    enviado = True
                    _final(True)
                elif linea.startswith("LAPTOP|"):
                    log.warning("📥 foto del celular guardada en la laptop: %s", linea[7:])
                elif linea.startswith("ERROR|"):
                    log.warning("📷 visor_foto: %s", linea[6:])
            p.wait()
        except Exception:
            log.exception("no pude mostrar la foto del celular")
        finally:
            if not enviado:
                _final(False)                                     # cerrar sin guardar: el celular borra su copia temporal
            try:
                os.remove(ruta)
            except OSError:
                pass
    threading.Thread(target=_hilo, name="sara-visor-foto", daemon=True).start()


def ultimo_error() -> str:
    return str(getattr(_ctx, "error", "") or "")


def max_mb() -> float:
    return float(_c("CEL_MAX_MB", 500))


# ─── CAPACIDADES (misma lógica que Motor.kt) ──────────────────────────────
_PALABRAS_CEL = {"celular", "telefono", "movil", "cel"}
_caps_cache: Optional[List[dict]] = None


def _capacidades() -> List[dict]:
    global _caps_cache
    if _caps_cache is None:
        try:
            ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capacidades.json")
            with open(ruta, encoding="utf-8") as f:
                _caps_cache = json.load(f).get("capacidades", [])
        except Exception as e:
            log.warning("no pude leer capacidades.json: %s", e)
            _caps_cache = []
    return _caps_cache


# Nombres de archivo («celular.py», «informe_celular.docx»): la palabra «celular» ahí es parte del NOMBRE, no un destino.
_RE_ARCHIVO = re.compile(r"(?i)[\w\-]*\w\.(?:py|pyw|kt|java|js|ts|json|md|txt|log|ini|cfg|bat|ps1|sh|exe|apk|zip|rar|7z|pdf|docx?|xlsx?|pptx?|csv|png|jpe?g|gif|webp|mp[34]|wav|html?|xml|yml|yaml)\b")
_PALABRAS_ARCHIVO = {"archivo", "archivos", "carpeta", "carpetas", "documento", "documentos", "fichero", "ficheros", "directorio"}


def _sin_archivos(texto: str) -> str:
    return _RE_ARCHIVO.sub(" ", texto or "")


def clasificar(texto: str) -> Optional[dict]:
    palabras = set(_plano(_sin_archivos(texto)).split())
    mejor = None
    for cap in _capacidades():
        for r in cap.get("reglas", []):
            if all(any(w in palabras for w in g) for g in r.get("todas", [])) and \
                    not any(w in palabras for w in r.get("no", [])):
                if mejor is None or cap.get("prioridad", 1) > mejor.get("prioridad", 1):
                    mejor = cap
                break
    return mejor


_RE_DESTINO = re.compile(r"(?i)\b(?:en|de|del|desde|con|a)\s+(?:mi|el|tu)?\s*(?:celular|tel[eé]fono|m[oó]vil|cel)\b")
_RE_CEL = re.compile(r"(?i)\b(?:celular|tel[eé]fono|m[oó]vil|cel)\b")


def interpretar(texto: str) -> Optional[dict]:
    """Solo es del celular si lo DICES (celular/teléfono/móvil/cel como palabra suelta) o si la capacidad es solo del
    celular (linterna, notificaciones...). Un nombre de archivo («celular.py») NO cuenta como destino, y con
    archivos/carpetas hace falta una frase de destino clara («en mi celular», «del celular»)."""
    n = _plano(texto)
    if not n or len(n.split()) > 30 or (texto or "").lstrip().startswith("/"):
        return None
    pantalla_celular = _mod("pantalla_celular")
    if pantalla_celular is not None:
        accion = pantalla_celular.interpretar(texto)
        if accion:
            return {"cap": {"id": f"pantalla_celular_vivo_{accion}", "nodo": "celular",
                            "riesgo": "medio", "confirmar": False}, "texto": texto}
    base = _sin_archivos(texto)                      # sin nombres de archivo: «celular.py» no es el celular
    cap = clasificar(texto)
    if cap is None:
        return None
    palabras = set(_plano(base).split())
    explicito = bool(palabras & _PALABRAS_CEL)
    if explicito and (palabras & _PALABRAS_ARCHIVO) and not _RE_DESTINO.search(base):
        explicito = False                            # «abre la carpeta celular de sara» = la carpeta, no el teléfono
    if cap.get("nodo") == "celular" or explicito:
        if (palabras & _PALABRAS_ARCHIVO) and not explicito:
            return None                              # «busca el archivo X»: nunca suena el teléfono por un nombre
        guardados: List[str] = []

        def _guardar(m):
            guardados.append(m.group(0))
            return f" \u0001{len(guardados) - 1}\u0001 "

        prot = _RE_ARCHIVO.sub(_guardar, texto)
        limpio = _RE_CEL.sub(" ", _RE_DESTINO.sub(" ", prot))
        limpio = re.sub(r"\s+", " ", limpio).strip()
        for i, g in enumerate(guardados):
            limpio = limpio.replace(f"\u0001{i}\u0001", g)
        limpio = re.sub(r"\s+", " ", limpio).strip() or texto
        return {"cap": cap, "texto": limpio}
    return None


def es_comando(texto: str) -> bool:
    if "pantalla" in _plano(texto).split():
        vivo = _mod("pantalla_vivo")
        if vivo and vivo.interpretar(texto):
            return True
    return interpretar(texto) is not None


# ─── LAPTOP → CELULAR ─────────────────────────────────────────────────────
def _enviar_y_esperar(dev: str, texto: str, confirmado: bool, espera: float = 25.0, huella: bool = False) -> Tuple[bool, str]:
    mid = uuid.uuid4().hex[:10]
    slot = {"ev": threading.Event(), "res": None}
    _esperando[mid] = slot
    msg = {"tipo": "orden", "id": mid, "texto": texto, "confirmado": bool(confirmado), "ts": int(time.time())}
    reto = None
    if huella:
        reto = secrets.token_hex(16)
        msg.update(huella=True, reto=reto)
        espera = max(espera, 70.0)                       # da tiempo a poner el dedo
    _cola(dev).put(msg)
    llego = slot["ev"].wait(espera)
    _esperando.pop(mid, None)
    if not llego or not slot["res"]:
        return False, "El celular no respondió a tiempo."
    res = slot["res"]
    ok, txt = bool(res.get("ok")), str(res.get("texto", ""))
    if huella and ok and not _firma_valida(dev, f"{reto}|{_sha(texto)}", str(res.get("firma", ""))):
        return False, "⚠️ El celular no presentó una firma de huella válida: lo trato como NO verificado."
    return ok, txt


def _abrir_visor_pantalla_celular(dev: str, stream_id: str, solicitado_desde: str = "laptop") -> bool:
    visor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "visor_pantalla_celular.py")
    if not os.path.isfile(visor):
        log.error("pantalla celular: falta visor_pantalla_celular.py")
        return False
    with _lock:
        anterior = _visores_pantalla_celular.get(dev)
        if anterior is not None and anterior.poll() is None:
            return True
        try:
            proceso = subprocess.Popen(
                [sys.executable, visor, solicitado_desde],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            log.exception("pantalla celular: no pude abrir la ventana")
            return False
        _visores_pantalla_celular[dev] = proceso
        _locks_visores_pantalla_celular[dev] = threading.Lock()

    listo = threading.Event()
    inicio_ok = {"valor": False}

    def vigilar():
        try:
            for linea in proceso.stdout:
                texto = linea.decode("utf-8", errors="replace").strip()
                if texto == "LISTO":
                    inicio_ok["valor"] = True
                    listo.set()
                elif texto.startswith("ERROR|"):
                    log.error("visor de pantalla del celular: %s", texto[6:])
                    listo.set()
                elif texto.startswith("CONTROL|"):
                    partes = texto.split("|")
                    activo = len(partes) == 2 and partes[1] == "1"
                    vivo = _mod("pantalla_celular")
                    if vivo is not None and vivo.cambiar_control(dev, stream_id, activo):
                        _cola(dev).put({"tipo": "pantalla_celular", "accion": "control",
                                        "stream_id": stream_id, "activo": activo,
                                        "accesibilidad_aviso": bool(_c(
                                            "CEL_PANTALLA_CONTROL_AVISO_ACCESIBILIDAD", True
                                        ))})
                    else:
                        _enviar_estado_control_visor(
                            dev, stream_id, False,
                            "La sesión de pantalla del celular ya no está activa.",
                        )
                elif texto.startswith("PERFIL|"):
                    partes = texto.split("|")
                    try:
                        fps = int(partes[1]) if len(partes) == 2 else -1
                    except ValueError:
                        fps = -1
                    vivo = _mod("pantalla_celular")
                    if vivo is not None and vivo.cambiar_perfil(dev, stream_id, fps):
                        _cola(dev).put({"tipo": "pantalla_celular", "accion": "perfil",
                                        "stream_id": stream_id, "fps": fps})
                elif texto.startswith("TOQUE|"):
                    partes = texto.split("|")
                    try:
                        x, y = float(partes[1]), float(partes[2])
                    except (IndexError, ValueError):
                        continue
                    vivo = _mod("pantalla_celular")
                    if (len(partes) == 3 and vivo is not None and vivo.control_activo(dev, stream_id)
                            and math.isfinite(x) and math.isfinite(y) and 0 <= x <= 1 and 0 <= y <= 1):
                        _cola(dev).put({"tipo": "pantalla_celular", "accion": "toque",
                                        "stream_id": stream_id, "x": x, "y": y})
                elif texto.startswith("DESLIZAR|"):
                    _procesar_control_visor(dev, stream_id, texto)
                elif texto.startswith("ARRASTRAR|"):
                    _procesar_control_visor(dev, stream_id, texto)
                elif texto == "INICIO":
                    _procesar_control_visor(dev, stream_id, texto)
                elif texto == "CERRADO":
                    vivo = _mod("pantalla_celular")
                    if vivo is not None:
                        vivo.detener(dev, stream_id)
                    _cola(dev).put({"tipo": "pantalla_celular", "accion": "detener", "stream_id": stream_id})
                    break
            proceso.wait(timeout=5)
        except Exception:
            log.exception("visor de pantalla del celular terminó con error")
        finally:
            listo.set()
            vivo = _mod("pantalla_celular")
            sid = vivo.detener(dev, stream_id) if vivo is not None else None
            if sid:
                _cola(dev).put({"tipo": "pantalla_celular", "accion": "detener", "stream_id": sid})
            with _lock:
                if _visores_pantalla_celular.get(dev) is proceso:
                    _visores_pantalla_celular.pop(dev, None)
                    _locks_visores_pantalla_celular.pop(dev, None)

    threading.Thread(target=vigilar, name="sara-visor-pantalla-celular", daemon=True).start()
    if not listo.wait(8.0):
        _cerrar_visor_pantalla_celular(dev)
        log.error("visor de pantalla del celular no confirmó su inicio")
        return False
    return inicio_ok["valor"]


def _enviar_fotograma_visor_pantalla_celular(dev: str, imagen: bytes) -> None:
    with _lock:
        proceso = _visores_pantalla_celular.get(dev)
        candado = _locks_visores_pantalla_celular.get(dev)
    if proceso is None or proceso.stdin is None or candado is None or proceso.poll() is not None:
        return
    try:
        linea = base64.b64encode(imagen) + b"\n"
        with candado:
            proceso.stdin.write(linea)
            proceso.stdin.flush()
    except (BrokenPipeError, OSError):
        log.warning("visor de pantalla del celular no acepta más fotogramas")


def _enviar_estado_control_visor(dev: str, sid: str, activo: bool, mensaje: str) -> None:
    vivo = _mod("pantalla_celular")
    if vivo is None or not vivo.activa(dev, sid):
        return
    with _lock:
        proceso = _visores_pantalla_celular.get(dev)
        candado = _locks_visores_pantalla_celular.get(dev)
    if proceso is None or proceso.stdin is None or candado is None or proceso.poll() is not None:
        return
    texto = base64.urlsafe_b64encode(mensaje[:300].encode("utf-8"))
    linea = b"CONTROL_ESTADO|" + (b"1" if activo else b"0") + b"|" + texto + b"\n"
    try:
        with candado:
            proceso.stdin.write(linea)
            proceso.stdin.flush()
    except (BrokenPipeError, OSError):
        log.warning("visor de pantalla del celular no acepta el estado del control remoto")


def _cerrar_visor_pantalla_celular(dev: str) -> None:
    with _lock:
        proceso = _visores_pantalla_celular.get(dev)
        candado = _locks_visores_pantalla_celular.get(dev)
    if proceso is None or proceso.stdin is None or candado is None or proceso.poll() is not None:
        return
    try:
        with candado:
            proceso.stdin.write(b"CERRAR\n")
            proceso.stdin.flush()
    except (BrokenPipeError, OSError):
        log.debug("el visor de pantalla del celular ya se había cerrado")


def _solicitar_pantalla_celular(
    dev: str, accion: str, sid: str, solicitado_desde: str = "laptop"
) -> Tuple[bool, str]:
    mid = uuid.uuid4().hex[:10]
    slot = {"ev": threading.Event(), "res": None}
    _esperando[mid] = slot
    _cola(dev).put({"tipo": "pantalla_celular", "id": mid, "accion": accion,
                    "stream_id": sid, "solicitado_desde": solicitado_desde})
    llego = slot["ev"].wait(30.0)
    _esperando.pop(mid, None)
    if not llego or not slot["res"]:
        return False, "El celular no confirmó la solicitud de pantalla en vivo."
    return bool(slot["res"].get("ok")), str(slot["res"].get("texto", ""))


def comando_pantalla_vivo(arg: str) -> Tuple[bool, str]:
    partes = (arg or "").strip().lower().split()
    if len(partes) != 2 or partes[0] not in ("laptop-a-celular", "celular-a-laptop") \
            or partes[1] not in ("on", "off", "estado"):
        return False, ("Uso: /pantalla-vivo laptop-a-celular on|off|estado · "
                       "/pantalla-vivo celular-a-laptop on|off|estado")
    direccion, accion = partes
    dev = _dispositivo_activo()
    if dev is None:
        return False, "Tu celular no está conectado ahora (¿sin internet o la app SARA cerrada?)."
    if direccion == "celular-a-laptop":
        if not _c("CEL_PANTALLA_CELULAR_ACTIVA", True):
            return False, "La recepción de pantalla en vivo desde el celular está desactivada."
        vivo = _mod("pantalla_celular")
        if vivo is None:
            return False, "La recepción de pantalla en vivo no está disponible."
        activa = vivo.activa_dispositivo(dev)
        if accion == "estado":
            return True, f"Pantalla del celular hacia la laptop: {'ACTIVA' if activa else 'apagada'}."
        if accion == "on":
            if activa:
                return True, "La pantalla del celular ya está activa en la laptop."
            return ejecutar("activa la pantalla en vivo de mi celular")
        if not activa:
            return True, "La pantalla del celular ya está apagada."
        return ejecutar("detén la pantalla en vivo de mi celular")

    vivo = _mod("pantalla_vivo")
    if vivo is None or not _c("CEL_PANTALLA_VIVO_ACTIVA", True):
        return False, "La transmisión de pantalla de la laptop está desactivada."
    if accion == "on" and (transfer is None or not transfer.cripto_disponible()):
        return False, "La pantalla en vivo necesita el cifrado cryptography disponible en la laptop."
    activa = vivo.activa(dev)
    if accion == "estado":
        return True, f"Pantalla de la laptop hacia el celular: {'ACTIVA' if activa else 'apagada'}."
    if accion == "on":
        if activa:
            return True, "La pantalla de la laptop ya está activa para este celular."
        sid, motivo = vivo.iniciar(dev, solicitado_desde="laptop")
        if sid is None:
            return False, motivo
        _cola(dev).put({
            "tipo": "pantalla_vivo",
            "id": uuid.uuid4().hex[:10],
            "accion": "iniciar",
            "stream_id": sid,
            "solicitado_desde": "laptop",
        })
        return True, ("Solicitud enviada al celular. Toca la notificación de SARA para abrir el visor; "
                      "la transmisión empieza al conectarse.")
    if not activa:
        return True, "La pantalla de la laptop ya está apagada para el celular."
    vivo.detener(dev)
    _cola(dev).put({
        "tipo": "pantalla_vivo",
        "id": uuid.uuid4().hex[:10],
        "accion": "detener",
    })
    return True, "Transmisión de la laptop detenida."


def ejecutar(texto: str, origen: Optional[str] = None, confirmar: Optional[Callable] = None) -> Tuple[bool, str]:
    """confirmar(pregunta) -> bool: lo da core_bridge (modal de la ventana). Nunca lanza."""
    try:
        accion_vivo = None
        if "pantalla" in _plano(texto).split():
            pantalla_vivo = _mod("pantalla_vivo")
            accion_vivo = pantalla_vivo.interpretar(texto) if pantalla_vivo else None
        if accion_vivo:
            return comando_pantalla_vivo(
                f"laptop-a-celular {'on' if accion_vivo == 'iniciar' else 'off'}"
            )
        iv = interpretar(texto)
        if iv is None:
            return False, "No entendí qué hacer en el celular."
        cap = iv["cap"]
        if cap["id"] in ("pantalla_celular_vivo_iniciar", "pantalla_celular_vivo_detener"):
            vivo = _mod("pantalla_celular")
            if vivo is None:
                return False, "La recepción de pantalla en vivo no está disponible."
            if cap["id"].endswith("_iniciar") and (transfer is None or not transfer.cripto_disponible()):
                return False, "La pantalla en vivo necesita el cifrado cryptography disponible en la laptop."
            dev = _dispositivo_activo()
            if dev is None:
                return False, "Tu celular no está conectado ahora (¿sin internet o la app SARA cerrada?)."
            if cap["id"].endswith("_iniciar"):
                sid, motivo = vivo.iniciar(
                    dev, solicitado_desde=origen if origen in ("laptop", "celular") else "laptop"
                )
                if sid is None:
                    return False, motivo
                if not _abrir_visor_pantalla_celular(dev, sid, origen or "laptop"):
                    vivo.detener(dev)
                    return False, "No pude abrir el visor de pantalla en la laptop."
                ok, mensaje = _solicitar_pantalla_celular(
                    dev, "iniciar", sid,
                    solicitado_desde=origen if origen in ("laptop", "celular") else "laptop",
                )
                if not ok:
                    vivo.detener(dev)
                    _cerrar_visor_pantalla_celular(dev)
                    return False, mensaje or "No pude solicitar el permiso de captura en el celular."
                return True, mensaje or "Solicitud enviada al celular. Acepta allí el aviso y el permiso de Android para iniciar la transmisión."
            sid = vivo.detener(dev)
            if sid is None:
                return True, "No hay una transmisión de pantalla del celular activa."
            _cerrar_visor_pantalla_celular(dev)
            ok, mensaje = _solicitar_pantalla_celular(dev, "detener", sid)
            return ok, mensaje or "Transmisión de pantalla del celular detenida."
        bloqueadas = {str(x) for x in _c("CEL_REMOTO_BLOQUEADAS", ("llamar", "sms"))}
        if origen in ("whatsapp", "telegram") and cap["id"] in bloqueadas:
            return False, "Eso solo lo hago si me lo pides desde la ventana de SARA o desde el propio celular."
        dev = _dispositivo_activo()
        if dev is None:
            if not _dispositivos:
                return False, "Aún no hay un celular vinculado. En SARA escribe /celular vincular."
            return False, "Tu celular no está conectado ahora (¿sin internet o la app SARA cerrada?)."
        confirmado = False
        if cap.get("confirmar"):
            if confirmar is None or not confirmar(f"¿Confirmas hacer esto en tu celular? «{iv['texto']}»"):
                return True, "Cancelado, no hice nada en el celular."
            confirmado = True
        huella = cap["id"] in {str(x) for x in _c("CEL_HUELLA_CAPS", ())} \
            and _verif() and bool(_dispositivos.get(dev, {}).get("pub"))
        log.info("📱 → celular [%s]%s: «%s»", cap["id"], " 🔐" if huella else "", iv["texto"][:60])
        espera = 70.0 if cap["id"] == "foto" else 25.0
        if cap["id"] == "foto":
            _foto_pedida[dev] = time.time()
        return _enviar_y_esperar(dev, iv["texto"], confirmado, espera=espera, huella=huella)
    except Exception:
        log.exception("celular.ejecutar falló")
        return False, "No pude comunicarme con el celular (revisa el log)."


def _volcar_pendientes(dev: str) -> None:
    with _lock:
        ahora = time.time()
        items = [p for p in _pend if ahora - float(p.get("t", 0)) < 86400]
        if _pend:
            _pend.clear()
            _guardar_pend()
    for p in items:
        _cola(dev).put({"tipo": "aviso", "id": uuid.uuid4().hex[:10], "texto": str(p["texto"])[:300]})


def notificar(texto: str) -> bool:
    """Aviso de la laptop como notificación en el celular. Si no hay conexión se guarda (24 h) y llega al reconectar."""
    if not texto:
        return False
    dev = _dispositivo_activo()
    if dev is not None:
        _cola(dev).put({"tipo": "aviso", "id": uuid.uuid4().hex[:10], "texto": str(texto)[:300]})
        return True
    if not _dispositivos:
        return False
    with _lock:
        _pend.append({"texto": str(texto)[:300], "t": time.time()})
        del _pend[:-50]
        _guardar_pend()
    return True


# ─── /celular ─────────────────────────────────────────────────────────────
def _ips() -> List[str]:
    ips: List[str] = []
    try:
        import psutil
        for _nic, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                    ips.append(a.address)
    except Exception:
        pass
    if not ips:
        try:
            ips.append(socket.gethostbyname(socket.gethostname()))
        except Exception:
            pass
    return ips


_dir_cache: Tuple[float, List[str]] = (0.0, [])


def _es_direccion_util(ip: str) -> bool:
    """Solo direcciones por las que el celular puede llegar: red local privada o Tailscale (100.64.0.0/10)."""
    try:
        p = [int(x) for x in ip.split(".")]
        return (p[0] == 10 or (p[0] == 192 and p[1] == 168) or (p[0] == 172 and 16 <= p[1] <= 31)
                or (p[0] == 100 and 64 <= p[1] <= 127))
    except Exception:
        return False


def _direcciones() -> List[str]:
    """Lista «ip:puerto» que se le da al celular para que pruebe en orden: primero la red local, al final Tailscale.
    Va en la respuesta de /pair y de /poll: así el celular aprende solo todas las rutas (en casa y fuera de casa)."""
    global _dir_cache
    try:
        t, lista = _dir_cache
        if lista and time.time() - t < 60:
            return list(lista)
        puerto = _c("CEL_PUERTO", 8770)
        ips = [ip for ip in dict.fromkeys(_ips()) if _es_direccion_util(ip)]
        ips.sort(key=lambda ip: 1 if _etiqueta_ip(ip) else 0)
        lista = [f"{ip}:{puerto}" for ip in ips]
        _dir_cache = (time.time(), lista)
        return list(lista)
    except Exception:
        return []


def _etiqueta_ip(ip: str) -> str:
    try:
        p = ip.split(".")
        return " (Tailscale)" if p[0] == "100" and 64 <= int(p[1]) <= 127 else ""
    except Exception:
        return ""


def estado_texto() -> str:
    if not _dispositivos:
        return "📱 No hay celulares vinculados. Usa /celular vincular."
    cr = transfer is not None and transfer.cripto_disponible()
    lineas = [f"📱 Servidor: {'activo, puerto ' + str(_c('CEL_PUERTO', 8770)) if _server else 'APAGADO (revisa el log: ¿puerto ocupado?)'}"
              f" · cryptography: {'sí' if cr else 'NO (sin huella ni archivos)'} · verificación: {'ACTIVA' if _verif() else 'apagada'}"]
    for dev, d in _dispositivos.items():
        e = _estado.get(dev, {})
        extra = ""
        if "bateria" in e:
            extra = f" · batería {e['bateria']} %{' (cargando)' if e.get('cargando') else ''} · {e.get('red', '?')}"
        lineas.append(f"• {d.get('nombre', dev)} [{dev}]: {'CONECTADO' if _conectado(dev) else 'desconectado'}"
                      f" · huella: {'🔐 registrada' if d.get('pub') else 'SIN clave (revincula)'}{extra}")
    if transfer is not None:
        lineas.append(f"📥 Lo que mandes desde el celular llega a: {transfer.carpeta_recibido()} · tope {_c('CEL_MAX_MB', 500)} MB")
    return "\n".join(lineas)


def comando_slash(arg: str) -> Tuple[bool, str]:
    """/celular · /celular vincular · /celular desvincular [NOMBRE|todos] · /celular probar"""
    a = (arg or "").strip()
    bajo = a.lower()
    if bajo in ("", "estado"):
        return True, estado_texto()
    if bajo == "vincular":
        if _server is None:
            arrancar()
        if _server is None:
            return False, "No pude abrir el servidor del celular (¿puerto ocupado? cambia CEL_PUERTO en config.py)."
        with _lock:
            _codigo["codigo"] = f"{secrets.randbelow(1000000):06d}"
            _codigo["vence"] = time.time() + 300
        puerto = _c("CEL_PUERTO", 8770)
        dirs = "\n".join(f"   {ip}:{puerto}{_etiqueta_ip(ip)}" for ip in _ips()) or f"   (tu-ip):{puerto}"
        return True, ("📱 Vincular el celular (válido 5 minutos):\n"
                      f"1) En la app SARA escribe UNA dirección que alcances ahora (la app aprende las demás sola):\n{dirs}\n"
                      f"2) Código: {_codigo['codigo']}\n"
                      "La app registrará aquí la clave de tu huella (necesita huella configurada en el celular).\n"
                      "Si no conecta: permite el puerto en el Firewall de Windows (red privada).\n"
                      + ("🌐 Tailscale detectado: el celular podrá conectarse también fuera de casa (con Tailscale activo en el celular)."
                         if any(_etiqueta_ip(ip) for ip in _ips()) else
                         "🌐 Para usarla fuera de casa (datos móviles u otro Wi-Fi) instala Tailscale en la laptop y en el celular, y vuelve a ejecutar /celular vincular."))
    if bajo.startswith("desvincular"):
        quien = a[len("desvincular"):].strip().lower()
        with _lock:
            borrar = [d for d, v in _dispositivos.items()
                      if quien in ("", "todos", "todo") or quien == d or quien in str(v.get("nombre", "")).lower()]
            for d in borrar:
                vivo = _mod("pantalla_vivo")
                if vivo is not None:
                    vivo.detener(d)
                _dispositivos.pop(d, None)
                _estado.pop(d, None)
                _auth.pop(d, None)
            _guardar()
        return True, f"📱 Desvinculados: {len(borrar)}."
    if bajo == "probar":
        return ejecutar("cuánta batería tiene mi celular")
    return True, "Uso: /celular · /celular vincular · /celular desvincular [nombre|todos] · /celular probar"


def eventos_proactivos() -> List[Tuple[str, dict]]:
    global _eventos
    for dev, d in list(_dispositivos.items()):
        ahora = _conectado(dev)
        antes = _estaba.get(dev)
        if antes is not None and antes != ahora:
            _eventos.append(("notificacion", {"texto": f"📱 {d.get('nombre', 'Celular')} " + ("conectado." if ahora else "se desconectó."),
                                              "nivel": "info"}))
        _estaba[dev] = ahora
    salida, _eventos = _eventos, []
    return salida