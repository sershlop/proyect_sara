# ↩️ deshacer.py — «deshaz lo último»: papelera propia de SARA + diario de operaciones sobre archivos (v1)
# Eliminar NO borra: mueve a papelera_sara/<id>/ (reversible). Mover, copiar, renombrar, comprimir y crear se anotan en
# sara_deshacer.json (últimas DESHACER_MAX). Lo que una copia o un movimiento SOBRESCRIBE también va a la papelera.
# Quien llama (core_bridge) pide la confirmación antes de deshacer. La papelera se purga tras DESHACER_PAPELERA_DIAS.
DESHACER_VERSION = "v1"
import json, logging, os, re, shutil, threading, time, unicodedata, uuid
from typing import Callable, List, Optional, Tuple
log = logging.getLogger("SARA-DESHACER")
try:
    import config
except Exception:
    config = None

def _c(n, d): return getattr(config, n, d) if config is not None else d
def _plano(s): return re.sub(r"[^a-z0-9 ]", " ", "".join(unicodedata.normalize("NFD", ch)[0] for ch in (s or "")).lower()).strip()
def _base() -> str:
    return str(getattr(config, "BASE_DIR", None) or os.path.dirname(os.path.abspath(__file__)))
def _papelera() -> str: return os.path.join(_base(), "papelera_sara")
def _diario() -> str: return os.path.join(_base(), "sara_deshacer.json")
def activo() -> bool: return bool(_c("DESHACER_ACTIVO", True))

_lock = threading.RLock()

def _leer() -> List[dict]:
    try:
        with open(_diario(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning("diario ilegible: %s", e)
        return []

def _guardar(lst: List[dict]) -> None:
    try:
        tmp = _diario() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lst[-int(_c("DESHACER_MAX", 20)):], f, ensure_ascii=False, indent=1)
        os.replace(tmp, _diario())
    except Exception as e:
        log.warning("no pude guardar el diario: %s", e)

def registrar(op: dict) -> None:
    """Anota una operación ya hecha. Nunca lanza."""
    if not activo():
        return
    try:
        with _lock:
            lst = _leer()
            lst.append(dict(op, t=time.time()))
            _guardar(lst)
    except Exception:
        log.exception("registrar falló")

# ─── PAPELERA PROPIA ──────────────────────────────────────────────────────
def a_papelera(ruta: str) -> Optional[str]:
    """Mueve a la papelera de SARA. Devuelve el id o None si no se pudo."""
    try:
        pid = uuid.uuid4().hex[:10]
        carpeta = os.path.join(_papelera(), pid)
        os.makedirs(carpeta)
        shutil.move(ruta, os.path.join(carpeta, os.path.basename(ruta.rstrip("\\/"))))
        return pid
    except Exception:
        log.exception("a_papelera falló")
        return None

def _de_papelera(pid: str, destino: str) -> Tuple[bool, str]:
    carpeta = os.path.join(_papelera(), pid)
    try:
        items = os.listdir(carpeta)
        if not items:
            return False, "La papelera ya no tiene ese elemento."
        if os.path.exists(destino):
            raiz, ext = os.path.splitext(destino)
            destino = f"{raiz} (restaurado){ext}"
        os.makedirs(os.path.dirname(destino) or ".", exist_ok=True)
        shutil.move(os.path.join(carpeta, items[0]), destino)
        shutil.rmtree(carpeta, ignore_errors=True)
        return True, destino
    except Exception as e:
        log.exception("_de_papelera falló")
        return False, f"No pude restaurar ({type(e).__name__})."

def eliminar(ruta: str) -> dict:
    """Sustituye a send2trash: manda a la papelera de SARA y lo anota. -> {"exito","texto"}"""
    pid = a_papelera(ruta)
    if pid is None:
        return {"exito": False, "texto": f"No pude mover «{os.path.basename(ruta)}» a la papelera."}
    registrar({"tipo": "eliminar", "ruta": ruta, "pid": pid})
    return {"exito": True, "texto": f"Enviado a la papelera de SARA: {os.path.basename(ruta)} (di «deshaz lo último» para recuperarlo)."}

def sobrescrito(final: str) -> Optional[str]:
    """Antes de que una copia/movimiento pise algo: lo guarda. Devuelve el id (o None si no había nada)."""
    return a_papelera(final) if os.path.exists(final) and activo() else None

def restaurar_sobrescrito(pid: Optional[str], final: str) -> None:
    if pid:
        _de_papelera(pid, final)

# ─── DESHACER ─────────────────────────────────────────────────────────────
def ultimo() -> Optional[dict]:
    with _lock:
        lst = _leer()
        return lst[-1] if lst else None

def describir(op: dict) -> str:
    n = os.path.basename
    t = op.get("tipo")
    if t == "eliminar": return f"recuperar «{n(op['ruta'])}» de la papelera"
    if t == "mover": return f"devolver «{n(op['final'])}» a {os.path.dirname(op['origen'])}"
    if t == "copiar": return f"quitar la copia «{n(op['final'])}» (irá a la papelera)"
    if t == "crear": return f"quitar «{n(op['final'])}», que creé (irá a la papelera)"
    if t == "renombrar": return f"volver a llamar «{n(op['viejo'])}» a «{n(op['nuevo'])}»"
    return "la última operación"

def deshacer_ultimo() -> Tuple[bool, str]:
    """Revierte la última operación (ya confirmada por quien llama). Nunca lanza."""
    try:
        with _lock:
            lst = _leer()
            if not lst:
                return True, "No hay nada que deshacer."
            op = lst.pop()
            _guardar(lst)
        t, n = op.get("tipo"), os.path.basename
        if t == "eliminar":
            ok, r = _de_papelera(op["pid"], op["ruta"])
            return ok, f"Recuperé «{n(op['ruta'])}»." + ("" if r == op["ruta"] else f" (ya había algo igual: quedó en {r})") if ok else r
        if t == "mover":
            if not os.path.exists(op["final"]):
                return False, f"«{n(op['final'])}» ya no está donde lo moví; no puedo deshacerlo."
            destino = op["origen"] if not os.path.exists(op["origen"]) else os.path.splitext(op["origen"])[0] + " (restaurado)" + os.path.splitext(op["origen"])[1]
            os.makedirs(os.path.dirname(destino) or ".", exist_ok=True)
            shutil.move(op["final"], destino)
            restaurar_sobrescrito(op.get("sobrescrito"), op["final"])
            return True, f"Devolví «{n(destino)}» a su carpeta original."
        if t in ("copiar", "crear"):
            if not os.path.exists(op["final"]):
                return False, f"«{n(op['final'])}» ya no existe."
            if a_papelera(op["final"]) is None:
                return False, "No pude quitarlo."
            restaurar_sobrescrito(op.get("sobrescrito"), op["final"])
            return True, f"Quité «{n(op['final'])}» (quedó en la papelera de SARA)."
        if t == "renombrar":
            if not os.path.exists(op["nuevo"]):
                return False, f"«{n(op['nuevo'])}» ya no existe con ese nombre."
            if os.path.exists(op["viejo"]):
                return False, f"Ya existe «{n(op['viejo'])}»; no lo piso."
            os.rename(op["nuevo"], op["viejo"])
            return True, f"Volví a llamarlo «{n(op['viejo'])}»."
        return False, "No sé deshacer esa operación."
    except Exception as e:
        log.exception("deshacer falló")
        return False, f"No pude deshacerlo ({type(e).__name__})."

def listar_texto() -> str:
    lst = _leer()
    if not lst:
        return "No tengo nada anotado para deshacer."
    hoy = time.strftime("%d/%m %H:%M")
    filas = [f"{i}. {time.strftime('%d/%m %H:%M', time.localtime(o['t']))} — {describir(o)}" for i, o in enumerate(reversed(lst[-8:]), 1)]
    return "Puedo deshacer (de lo más reciente a lo más viejo; «deshaz lo último» revierte el 1):\n" + "\n".join(filas)

def purgar() -> None:
    """Borra de la papelera lo que lleva más de DESHACER_PAPELERA_DIAS días y limpia el diario. Nunca lanza."""
    try:
        limite = time.time() - int(_c("DESHACER_PAPELERA_DIAS", 30)) * 86400
        with _lock:
            lst = [o for o in _leer() if o.get("t", 0) >= limite]
            _guardar(lst)
        if os.path.isdir(_papelera()):
            for d in os.listdir(_papelera()):
                ruta = os.path.join(_papelera(), d)
                if os.path.getmtime(ruta) < limite:
                    shutil.rmtree(ruta, ignore_errors=True)
    except Exception:
        log.exception("purgar falló")

def comando_papelera(arg: str) -> Tuple[bool, str]:
    """/papelera (resumen) · /papelera vaciar confirmar (borra DEFINITIVAMENTE; solo desde la ventana)."""
    a = (arg or "").strip().lower()
    if a == "vaciar confirmar":
        shutil.rmtree(_papelera(), ignore_errors=True)
        with _lock:
            _guardar([o for o in _leer() if o.get("tipo") not in ("eliminar",)])
        return True, "Papelera de SARA vaciada definitivamente."
    n = 0
    tam = 0
    if os.path.isdir(_papelera()):
        for r, _d, fs in os.walk(_papelera()):
            for f in fs:
                n += 1
                try: tam += os.path.getsize(os.path.join(r, f))
                except OSError: pass
    return True, (f"Papelera de SARA: {n} archivo(s), {tam / 1048576:.1f} MB en {_papelera()}\n"
                  f"Se purga sola tras {_c('DESHACER_PAPELERA_DIAS', 30)} días.\n{listar_texto()}\n"
                  "Uso: «deshaz lo último» · /deshacer · /papelera vaciar confirmar (definitivo)")

# ─── INTÉRPRETE ───────────────────────────────────────────────────────────
_CORT = r"^(?:(?:oye|hey|hola|ok)\s+)?(?:sara[\s,]+)?(?:(?:por favor|porfa|ya|ahora)\s+)*(?:(?:puedes|podrias|quiero que|necesito que)\s+)?"
_RE_DESHACER = re.compile(_CORT + r"(?:deshaz|deshacer|deshace|revierte|revertir|vuelve\s+atras|regresa\s+atras)"
                          r"(?:\s+(?:lo\s+ultimo|eso|esto|ultimo|lo\s+que\s+hiciste|el\s+ultimo\s+cambio|la\s+ultima\s+(?:accion|operacion)))?(?:\s+(?:por favor|porfa|ya))?$")
_RE_LISTAR = re.compile(_CORT + r"(?:que\s+(?:puedo|se\s+puede)\s+deshacer|que\s+(?:has|hiciste)\s+(?:hecho|con\s+mis\s+archivos)|muestrame\s+(?:lo\s+que\s+puedo\s+deshacer|la\s+papelera))$")

def interpretar(texto: str) -> Optional[str]:
    n = _plano(texto)
    if not n or len(n.split()) > 12:
        return None
    if _RE_DESHACER.match(n): return "deshacer"
    if _RE_LISTAR.match(n): return "listar"
    return None

def es_comando(texto: str) -> bool:
    return interpretar(texto) is not None
