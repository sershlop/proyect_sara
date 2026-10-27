# 🔐 transfer_seguro.py — Archivos y carpetas cifrados entre la laptop y la app SARA del celular (v1)
# Cifrado: AES-256-GCM por trozos de 256 KiB. Clave = HKDF-SHA256(secreto del emparejamiento, sal = id del archivo,
#   info = "sara-blob-v1-<down|up>"): una clave distinta por archivo y por sentido. Nonce = contador del trozo.
#   AAD = "id|idx|fin": nadie puede reordenar, repetir ni truncar trozos sin que falle la autenticación.
# Carpetas y archivos que pasan el tope (CEL_MAX_MB, 500) se comprimen en ZIP (en disco temporal) y luego se cifran.
# Si ya comprimido sigue pasando el tope, se rechaza con un mensaje claro.
# Seguridad de rutas: reutiliza envio_archivos.permitido() (carpetas autorizadas + lista negra de claves/credenciales).
# Lo que llega del celular va a <BASE_DIR>/carpetas_creadas/recibido_celular (CEL_CARPETA_RECIBIDO lo cambia).
TRANSFER_VERSION = "v1"
import base64, logging, math, mimetypes, os, re, shutil, tempfile, threading, time, uuid, zipfile
from typing import Dict, Optional, Tuple

log = logging.getLogger("SARA-TRANSFER")
try:
    import config
except Exception:
    config = None
try:
    import envio_archivos
except Exception:
    envio_archivos = None

TROZO = 256 * 1024
_RE_ID = re.compile(r"^[0-9a-f]{10}$")
_EXT_PELIGROSAS = (".exe", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".msi", ".scr", ".lnk", ".hta",
                   ".dll", ".com", ".pif", ".reg", ".jar", ".wsf", ".cpl")
_RESERVADOS = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


def _c(n, d):
    return getattr(config, n, d) if config is not None else d


def max_bytes() -> int:
    return int(float(_c("CEL_MAX_MB", 500)) * 1048576)


def _base() -> str:
    return str(getattr(config, "BASE_DIR", None) or os.path.dirname(os.path.abspath(__file__)))


def carpeta_recibido() -> str:
    d = str(_c("CEL_CARPETA_RECIBIDO", "") or "") or os.path.join(_base(), "carpetas_creadas", "recibido_celular")
    os.makedirs(d, exist_ok=True)
    return d


def _tmp_dir() -> str:
    return os.path.join(tempfile.gettempdir(), "sara_cel_blobs")


def cripto_disponible() -> bool:
    try:
        import cryptography  # noqa: F401
        return True
    except Exception:
        return False


# ─── CRIPTO ───────────────────────────────────────────────────────────────
def _clave(secret: str, blob_id: str, sentido: str) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=blob_id.encode("utf-8"),
                info=("sara-blob-v1-" + sentido).encode("utf-8")).derive(secret.encode("utf-8"))


def _nonce(idx: int) -> bytes:
    return b"\x00" * 4 + int(idx).to_bytes(8, "big")


def _aad(blob_id: str, idx: int, fin: bool) -> bytes:
    return f"{blob_id}|{idx}|{1 if fin else 0}".encode("utf-8")


def cifrar_trozo(secret: str, blob_id: str, sentido: str, idx: int, fin: bool, datos: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(_clave(secret, blob_id, sentido)).encrypt(_nonce(idx), datos, _aad(blob_id, idx, fin))


def descifrar_trozo(secret: str, blob_id: str, sentido: str, idx: int, fin: bool, datos: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(_clave(secret, blob_id, sentido)).decrypt(_nonce(idx), datos, _aad(blob_id, idx, fin))


# ─── LAPTOP → CELULAR ─────────────────────────────────────────────────────
_blobs: Dict[str, dict] = {}
_lock = threading.RLock()


def _adjunto(bid: str) -> dict:
    b = _blobs[bid]
    return {"id": bid, "nombre": b["nombre"], "mime": b["mime"], "tam": b["tam"],
            "trozos": max(1, math.ceil(b["tam"] / TROZO))}


def registrar_bytes(dev: str, datos: bytes, nombre: str, mime: Optional[str] = None) -> dict:
    bid = uuid.uuid4().hex[:10]
    with _lock:
        _blobs[bid] = {"dev": dev, "bytes": datos, "ruta": None, "nombre": nombre, "tam": len(datos), "t": time.time(),
                       "mime": mime or mimetypes.guess_type(nombre)[0] or "application/octet-stream", "borrar": False}
        return _adjunto(bid)


def _registrar_archivo(dev: str, ruta: str, nombre: str, borrar: bool, mime: Optional[str] = None) -> dict:
    bid = uuid.uuid4().hex[:10]
    with _lock:
        _blobs[bid] = {"dev": dev, "bytes": None, "ruta": ruta, "nombre": nombre, "tam": os.path.getsize(ruta),
                       "t": time.time(), "borrar": borrar,
                       "mime": mime or mimetypes.guess_type(nombre)[0] or "application/octet-stream"}
        return _adjunto(bid)


class _Grande(Exception):
    pass


def _comprimir(ruta: str, destino: str, tope: int) -> Tuple[int, int]:
    """(archivos incluidos, omitidos por seguridad). Lanza _Grande si el ZIP pasa el tope."""
    n = omitidos = 0
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        if os.path.isfile(ruta):
            z.write(ruta, os.path.basename(ruta))
            if os.path.getsize(destino) > tope:
                raise _Grande()
            return 1, 0
        raiz = os.path.dirname(ruta.rstrip("\\/"))
        for r, _ds, fs in os.walk(ruta):
            for f in fs:
                p = os.path.join(r, f)
                if os.path.islink(p) or (envio_archivos is not None and not envio_archivos.permitido(p)[0]):
                    omitidos += 1
                    continue
                try:
                    z.write(p, os.path.relpath(p, raiz))
                    n += 1
                except (OSError, ValueError):
                    omitidos += 1
                    continue
                if os.path.getsize(destino) > tope:
                    raise _Grande()
    return n, omitidos


def registrar_ruta(dev: str, ruta: str) -> Tuple[Optional[dict], str]:
    """Prepara un archivo o carpeta para que el celular lo descargue. (adjunto, '') o (None, motivo). Nunca lanza."""
    try:
        if envio_archivos is not None:
            ok, motivo = envio_archivos.permitido(ruta)
            if not ok:
                return None, motivo
        if not os.path.exists(ruta):
            return None, "Esa ruta ya no existe."
        tope = max_bytes()
        nombre = os.path.basename(ruta.rstrip("\\/")) or "archivo"
        if os.path.isfile(ruta) and os.path.getsize(ruta) <= tope:
            return _registrar_archivo(dev, ruta, nombre, False), ""
        os.makedirs(_tmp_dir(), exist_ok=True)
        tmp = os.path.join(_tmp_dir(), uuid.uuid4().hex + ".zip")
        try:
            n, omit = _comprimir(ruta, tmp, tope)
        except _Grande:
            _borrar(tmp)
            return None, f"Aun comprimido «{nombre}» pasa de {_c('CEL_MAX_MB', 500)} MB; no lo envío (sube CEL_MAX_MB en config.py si quieres)."
        if n == 0:
            _borrar(tmp)
            return None, "No había nada permitido que enviar en esa carpeta."
        if omit:
            log.warning("📤 %d archivo(s) omitidos por la lista negra al comprimir %s", omit, nombre)
        return _registrar_archivo(dev, tmp, nombre + ".zip", True, "application/zip"), ""
    except Exception as e:
        log.exception("registrar_ruta falló")
        return None, f"No pude preparar el envío ({type(e).__name__})."


def leer_trozo(dev: str, bid: str, idx: int, secret: str) -> dict:
    try:
        with _lock:
            b = _blobs.get(bid)
        if not _RE_ID.match(bid) or not b or b["dev"] != dev:
            return {"ok": False, "error": "desconocido"}
        total = max(1, math.ceil(b["tam"] / TROZO))
        if idx < 0 or idx >= total:
            return {"ok": False, "error": "fuera de rango"}
        if b["bytes"] is not None:
            datos = b["bytes"][idx * TROZO:(idx + 1) * TROZO]
        else:
            with open(b["ruta"], "rb") as f:
                f.seek(idx * TROZO)
                datos = f.read(TROZO)
        fin = idx == total - 1
        ct = cifrar_trozo(secret, bid, "down", idx, fin, datos)
        return {"ok": True, "idx": idx, "fin": fin, "total": total, "b64": base64.b64encode(ct).decode("ascii")}
    except Exception as e:
        log.exception("leer_trozo falló")
        return {"ok": False, "error": type(e).__name__}


def _borrar(ruta: Optional[str]) -> None:
    try:
        if ruta:
            os.remove(ruta)
    except OSError:
        pass


def liberar(dev: str, bid: str) -> None:
    with _lock:
        b = _blobs.get(bid)
        if b and b["dev"] == dev:
            _blobs.pop(bid, None)
            if b["borrar"]:
                _borrar(b["ruta"])


# ─── CELULAR → LAPTOP ─────────────────────────────────────────────────────
_subidas: Dict[str, dict] = {}


def _nombre_seguro(nombre: str) -> str:
    n = re.split(r"[\\/]", nombre)[-1]
    n = re.sub(r'[<>:"|?*\x00-\x1f]', "_", n).strip(" .")[:120] or "archivo"
    if n.split(".")[0].lower() in _RESERVADOS:
        n = "_" + n
    if n.lower().endswith(_EXT_PELIGROSAS):
        n += ".bloqueado"                       # lo que llega de fuera nunca queda ejecutable con un doble clic
    return n


def _carpeta_vista() -> str:
    d = os.path.join(tempfile.gettempdir(), "sara_cel_vista")
    os.makedirs(d, exist_ok=True)
    return d


def _destino_unico(nombre: str, base: Optional[str] = None) -> str:
    base = base or carpeta_recibido()
    raiz, ext = os.path.splitext(nombre)
    ruta, i = os.path.join(base, nombre), 1
    while os.path.exists(ruta):
        ruta = os.path.join(base, f"{raiz} ({i}){ext}")
        i += 1
    return ruta


def _abortar(bid: str) -> None:
    with _lock:
        s = _subidas.pop(bid, None)
    if s:
        _borrar(s["tmp"])


def recibir_trozo(dev: str, secret: str, d: dict) -> dict:
    """Un trozo cifrado que sube el celular. Devuelve {"ok", "fin", "ruta"?, "texto"?}. Nunca lanza."""
    bid = str(d.get("id", ""))
    try:
        if not _RE_ID.match(bid):
            return {"ok": False, "texto": "id inválido"}
        idx, fin = int(d.get("idx", 0)), bool(d.get("fin"))
        ct = base64.b64decode(str(d.get("b64", "")), validate=True)
        if len(ct) > TROZO + 16:
            return {"ok": False, "texto": "trozo demasiado grande"}
        with _lock:
            s = _subidas.get(bid)
            if idx == 0:
                if s:
                    return {"ok": False, "texto": "subida duplicada"}
                parciales = os.path.join(carpeta_recibido(), ".parciales")
                os.makedirs(parciales, exist_ok=True)
                tmp = os.path.join(parciales, bid + ".part")
                open(tmp, "wb").close()
                s = _subidas[bid] = {"dev": dev, "tmp": tmp, "next": 0, "tam": 0, "t": time.time(),
                                     "vista": bool(d.get("vista")),      # True = solo para mostrarla: va a una carpeta temporal
                                     "nombre": _nombre_seguro(str(d.get("nombre") or "archivo"))}
            if not s or s["dev"] != dev or idx != s["next"]:
                return {"ok": False, "texto": "trozo fuera de orden"}
        try:
            datos = descifrar_trozo(secret, bid, "up", idx, fin, ct)
        except Exception:
            _abortar(bid)
            log.warning("🚨 trozo con firma inválida de %s: subida abortada", dev)
            return {"ok": False, "texto": "El archivo llegó alterado o con otra clave; lo descarté."}
        s["tam"] += len(datos)
        if s["tam"] > max_bytes():
            _abortar(bid)
            return {"ok": False, "texto": f"Pasa del tope de {_c('CEL_MAX_MB', 500)} MB."}
        with open(s["tmp"], "ab") as f:
            f.write(datos)
        s["next"] += 1
        s["t"] = time.time()
        if not fin:
            return {"ok": True, "fin": False}
        vista = bool(s.get("vista"))
        destino = _destino_unico(s["nombre"], _carpeta_vista() if vista else None)
        shutil.move(s["tmp"], destino)
        with _lock:
            _subidas.pop(bid, None)
        log.warning("📥 recibido del celular%s: %s (%d KB)", " (vista previa)" if vista else "", os.path.basename(destino), s["tam"] // 1024)
        return {"ok": True, "fin": True, "ruta": destino, "nombre": os.path.basename(destino), "vista": vista,
                "texto": ("Foto recibida en la laptop (vista previa)." if vista
                          else f"Recibido en la laptop: {os.path.basename(destino)}")}
    except Exception as e:
        log.exception("recibir_trozo falló")
        _abortar(bid)
        return {"ok": False, "texto": f"No pude recibir el archivo ({type(e).__name__})."}


def purgar() -> None:
    """Quita blobs y subidas abandonados. Nunca lanza."""
    try:
        ttl = float(_c("CEL_BLOB_TTL_S", 1800))
        ahora = time.time()
        with _lock:
            for bid in [k for k, b in _blobs.items() if ahora - b["t"] > ttl]:
                b = _blobs.pop(bid)
                if b["borrar"]:
                    _borrar(b["ruta"])
            viejas = [k for k, s in _subidas.items() if ahora - s["t"] > ttl]
        for k in viejas:
            _abortar(k)
    except Exception:
        log.exception("purgar falló")


def iniciar() -> None:
    shutil.rmtree(_tmp_dir(), ignore_errors=True)
    shutil.rmtree(os.path.join(tempfile.gettempdir(), "sara_cel_vista"), ignore_errors=True)
    try:
        shutil.rmtree(os.path.join(carpeta_recibido(), ".parciales"), ignore_errors=True)
    except Exception:
        pass

    def _bucle():
        while True:
            time.sleep(300)
            purgar()
    threading.Thread(target=_bucle, name="sara-cel-purga", daemon=True).start()