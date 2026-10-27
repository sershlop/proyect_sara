# 🎬 modos.py — Modos o escenas: una frase dispara varias acciones («modo clase», «modo mochila», «modo casa») (v1)
# Cada acción es una FRASE NORMAL de SARA («abre chrome», «pon el volumen al 0»): se ejecuta por el mismo circuito
# que lo que escribes (confirmaciones y política de canales remotos incluidas). Acciones especiales:
#   @mochila   → energía de la laptop en modo mochila (apagado.energia_mochila)     [solo desde la ventana]
#   @restaurar → vuelve a tu plan de energía original (apagado.energia_restaurar)   [solo desde la ventana]
# Se guardan en sara_modos.json. Los de fábrica (abajo) se pueden redefinir con el mismo nombre.
#   «modo clase» · «activa el modo casa» · /modo clase · /modos · /modo nuevo clase = abre chrome ; mutea · /modo borrar clase
MODOS_VERSION = "v1"
import json, logging, os, re, threading, unicodedata
from typing import Callable, Dict, List, Optional, Tuple
log = logging.getLogger("SARA-MODOS")
try:
    import config
except Exception:
    config = None

def _c(n, d): return getattr(config, n, d) if config is not None else d
def _plano(s): return re.sub(r"[^a-z0-9 ]", " ", "".join(unicodedata.normalize("NFD", ch)[0] for ch in (s or "")).lower()).strip()
def _ruta() -> str:
    base = getattr(config, "BASE_DIR", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(str(base), "sara_modos.json")

DE_FABRICA: Dict[str, List[str]] = {
    "mochila": ["@mochila", "mutea"],
    "clase": ["abre chrome", "mutea"],
    "casa": ["@restaurar", "pon el volumen al 40"],
}
_local = threading.local()

def _leer() -> Dict[str, List[str]]:
    try:
        with open(_ruta(), encoding="utf-8") as f:
            d = json.load(f)
        return {str(k): [str(a) for a in v] for k, v in d.items() if isinstance(v, list)} if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("sara_modos.json ilegible: %s", e)
        return {}

def todos() -> Dict[str, List[str]]:
    d = dict(DE_FABRICA)
    d.update(_leer())
    return {_plano(k): v for k, v in d.items()}

def _guardar(d: Dict[str, List[str]]) -> bool:
    try:
        tmp = _ruta() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _ruta())
        return True
    except Exception as e:
        log.warning("no pude guardar modos: %s", e)
        return False

# ─── INTÉRPRETE ───────────────────────────────────────────────────────────
_CORT = r"^(?:(?:oye|hey|hola|ok)\s+)?(?:sara[\s,]+)?(?:(?:por favor|porfa|ya|ahora)\s+)*(?:(?:puedes|podrias|quiero que|necesito que)\s+)?"
_RE_ACT = re.compile(_CORT + r"(?:(?:activa|activar|pon|poner|inicia|iniciar|entra\s+(?:a|al)|cambia\s+(?:a|al)|ponme)\s+)?(?:el\s+)?modo\s+(?P<n>[a-z0-9 _-]{1,30}?)(?:\s+(?:por favor|porfa|ya))?$")
_RE_LISTAR = re.compile(_CORT + r"(?:que|cuales)\s+modos\s+(?:tengo|hay|existen)$|^modos$")

def interpretar(texto: str) -> Optional[dict]:
    if getattr(_local, "dentro", False):
        return None                                  # un modo no dispara otros modos (evita bucles)
    n = _plano(texto)
    if not n or len(n.split()) > 10:
        return None
    if _RE_LISTAR.match(n):
        return {"accion": "listar"}
    m = _RE_ACT.match(n)
    if m and m.group("n").strip() in todos():
        return {"accion": "activar", "nombre": m.group("n").strip()}
    return None

def es_comando(texto: str) -> bool:
    return interpretar(texto) is not None

def listar_texto() -> str:
    t = todos()
    return ("Modos disponibles:\n" + "\n".join(f"• {k}: {' → '.join(v)}" for k, v in sorted(t.items())) +
            "\nUso: «modo clase» · /modo clase · /modo nuevo NOMBRE = acción ; acción · /modo borrar NOMBRE")

# ─── EJECUCIÓN ────────────────────────────────────────────────────────────
def _especial(accion: str, remoto: bool) -> Tuple[bool, str]:
    if remoto:
        return False, f"{accion}: solo se puede desde la ventana de SARA (omitido)"
    try:
        import apagado
        if accion == "@mochila":
            ok, ls = apagado.energia_mochila()
            return ok, "energía en modo mochila" if ok else "no pude aplicar el modo mochila"
        if accion == "@restaurar":
            ok, ls = apagado.energia_restaurar()
            return ok, "energía restaurada" if ok else (ls[0] if ls else "no pude restaurar la energía")
    except Exception as e:
        log.exception("acción especial falló")
        return False, f"{accion} falló ({type(e).__name__})"
    return False, f"acción desconocida {accion}"

def ejecutar(nombre: str, correr: Callable[[str], Tuple[bool, str]], remoto: bool = False) -> Tuple[bool, str]:
    """correr(frase) -> (ok, respuesta): lo da core_bridge (circuito completo de SARA). Nunca lanza."""
    acciones = todos().get(_plano(nombre))
    if not acciones:
        return False, f"No conozco el modo «{nombre}». Mira /modos."
    resultados, todo_ok = [], True
    _local.dentro = True
    try:
        for a in acciones[:int(_c("MODOS_MAX_ACCIONES", 10))]:
            try:
                ok, txt = _especial(a, remoto) if a.startswith("@") else correr(a)
            except Exception as e:
                ok, txt = False, f"{type(e).__name__}"
                log.exception("acción de modo falló")
            todo_ok = todo_ok and bool(ok)
            resultados.append(f"{'✓' if ok else '✗'} {a} → {str(txt)[:80]}")
    finally:
        _local.dentro = False
    return todo_ok, f"Modo «{nombre}» {'listo' if todo_ok else 'aplicado con avisos'}:\n" + "\n".join(resultados)

# ─── /modo ────────────────────────────────────────────────────────────────
def comando(arg: str) -> Tuple[bool, str]:
    """Solo lo que NO ejecuta: listar, nuevo, borrar. (Activar pasa por el circuito de SARA.)"""
    a = (arg or "").strip()
    m = re.match(r"(?i)^nuevo\s+([^=]{1,30}?)\s*=\s*(.+)$", a)
    if m:
        nombre = _plano(m.group(1))
        acciones = [x.strip() for x in m.group(2).split(";") if x.strip()]
        if not nombre or not acciones:
            return False, "Uso: /modo nuevo NOMBRE = abre chrome ; mutea ; pon el volumen al 30"
        if len(acciones) > int(_c("MODOS_MAX_ACCIONES", 10)) or any(x.startswith("/") or x.lower().startswith("modo ") for x in acciones):
            return False, "Máximo 10 acciones; no pueden ser comandos «/» ni otros modos."
        d = _leer()
        d[nombre] = acciones
        return (True, f"Modo «{nombre}» guardado: {' → '.join(acciones)}") if _guardar(d) else (False, "No pude guardar el modo.")
    m = re.match(r"(?i)^borrar\s+(.+)$", a)
    if m:
        nombre = _plano(m.group(1))
        d = _leer()
        if nombre in d:
            del d[nombre]
            _guardar(d)
            return True, f"Modo «{nombre}» borrado" + (" (volvió el de fábrica)" if nombre in DE_FABRICA else ".")
        return False, "Ese modo no es tuyo (los de fábrica se redefinen con /modo nuevo, no se borran)." if nombre in DE_FABRICA else f"No existe el modo «{nombre}»."
    return True, listar_texto()

def reescribir_slash(texto: str) -> str:
    """«/modo clase» → «activa el modo clase» (para que pase por el circuito). nuevo/borrar/lista no se tocan."""
    m = re.match(r"(?i)^/modo\s+(.+)$", (texto or "").strip())
    if m and not re.match(r"(?i)^(nuevo|borrar)\b", m.group(1)):
        return f"activa el modo {m.group(1).strip()}"
    return texto
