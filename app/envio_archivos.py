# 📤 envio_archivos.py — «mándame / pásame / compárteme / envíame el archivo X» por Telegram (v1)
# Reutiliza abridor_inteligente.resolver_apertura (misma lógica que «abre el archivo X de la carpeta Y») y, si no
# encuentra, el índice de archivos. Lo único nuevo: ENVIAR el resultado. Solo origen «telegram» (desde la laptop
# esos verbos siguen igual que antes). Seguridad: carpetas autorizadas + lista negra fija + tope de tamaño.
ENVIO_VERSION = "v1"
import logging, os, re, unicodedata
from typing import Callable, Optional, Tuple
log = logging.getLogger("SARA-ENVIO")
try:
    import config as _cfg
except Exception:
    _cfg = None
try:
    import abridor_inteligente as abridor
except Exception:
    abridor = None
try:
    import file_index
except Exception:
    file_index = None

def _c(n, d): return getattr(_cfg, n, d) if _cfg is not None else d
def _plano(s): return "".join(unicodedata.normalize("NFD", ch)[0] for ch in (s or "")).lower()   # misma longitud

# ─── INTÉRPRETE (determinista, sin efectos) ───────────────────────────────
_CORT = (r"^[\s¡¿\"']*(?:(?:oye|hey|hola|ok)\s+)?(?:sara[\s,]+)?(?:(?:por favor|porfa|ya)\s+)*"
         r"(?:(?:puedes|podrias|me puedes|me podrias|quiero que|necesito que)\s+)?(?:(?:por favor|porfa)\s+)*")
_VERBO = r"(?:manda|mandame|mandes|pasa|pasame|pases|envia|enviame|envies|comparte|comparteme|compartime|compartas)"
_RE = re.compile(_CORT + _VERBO + r"\s+(?:me\s+)?(?P<r>.+)$")
_RE_COLA = re.compile(r"\s+(?:por favor|porfa|ya|al celular|a mi celular|al telefono|a mi telefono|por telegram|a telegram|aqui)$")
_RE_OBJ = re.compile(r"\b(?:archivo|archivos|documento|fichero|pdf|foto|imagen|word|excel|powerpoint|presentacion|carpeta)\b"
                     r"|\.[a-z0-9]{2,5}\b")
_RE_NO = re.compile(r"\b(?:whatsapp|correo|mail|mensaje|recordatorio|nota|captura|pantalla|screenshot)\b")
_RE_TIPO = re.compile(r"^(?:(?:el|la|un|una|mi)\s+)?(?P<t>pdf|word|excel|powerpoint|presentacion)\s+(?:(?:de|del|sobre)\s+)?(?:(?:la|el|los|las)\s+)?(?P<x>.+)$")
_EXT = {"pdf": "pdf", "word": "docx", "excel": "xlsx", "powerpoint": "pptx", "presentacion": "pptx"}
_RE_SEP = re.compile(r"\s(?:de\s+la\s+carpeta|en\s+la\s+carpeta|dentro\s+de|en)\s")

def interpretar(texto: str) -> Optional[dict]:
    try:
        p = _plano(texto)
        m = _RE.match(p)
        if not m or len(p.split()) > 24:
            return None
        rest = texto[m.start("r"):].strip()
        pr = _plano(rest)
        while True:
            c = _RE_COLA.search(pr)
            if not c:
                break
            rest, pr = rest[:c.start()], pr[:c.start()]
        if _RE_NO.search(pr) or not _RE_OBJ.search(pr):
            return None
        t = _RE_TIPO.match(pr)
        if t:                                           # «el pdf de la tarea de fisica» → «el archivo tarea de fisica.pdf»
            x = rest[t.start("x"):]
            s = _RE_SEP.search(" " + pr[t.start("x"):] + " ")
            if s:
                i = max(0, s.start() - 1)
                x = x[:i] + "." + _EXT[t.group("t")] + x[i:]
            else:
                x = x + "." + _EXT[t.group("t")]
            frase = "abre el archivo " + x
        else:
            frase = "abre " + rest
        return {"rest": rest, "frase_abrir": frase}
    except Exception:
        log.exception("envio.interpretar falló")
        return None

def es_comando(texto: str) -> bool:
    return interpretar(texto) is not None

# ─── SEGURIDAD ────────────────────────────────────────────────────────────
_NEGRA_NOMBRES = {".env", "apagado_totp.key", "sara.db", "sara_ajustes.json", "optimizador_estado.json",
                  "apagado_energia_backup.json", "id_rsa", "id_ed25519", "known_hosts"}
_NEGRA_EXT = (".key", ".pem", ".pfx", ".p12", ".kdbx", ".ovpn", ".db-wal", ".db-shm")
_NEGRA_PARTES = {"wa_sidecar", ".ssh", ".aws", ".gnupg", "appdata", "windows", "system32"}
_NEGRA_SUBCAD = ("password", "contrasen", "credential", "secret", "token", "cookies")

def _autorizadas() -> list:
    cfg = list(_c("ENVIO_CARPETAS_AUTORIZADAS", ()) or ())
    if not cfg:
        h = os.path.expanduser("~")
        for n in ("Desktop", "Documents", "Downloads", "Pictures"):
            cfg += [os.path.join(h, n), os.path.join(h, "OneDrive", n)]
    return [os.path.normcase(os.path.realpath(c)) for c in cfg if c and os.path.isdir(c)]

def permitido(ruta: str) -> Tuple[bool, str]:
    try:
        real = os.path.normcase(os.path.realpath(ruta))
        nombre = os.path.basename(real)
        if (nombre in _NEGRA_NOMBRES or nombre.endswith(_NEGRA_EXT) or any(s in nombre for s in _NEGRA_SUBCAD)
                or any(p in _NEGRA_PARTES for p in real.split(os.sep))):
            return False, "Ese archivo es sensible (claves, credenciales o datos de SARA); no lo envío."
        for base in _autorizadas():
            try:
                if os.path.commonpath([real, base]) == base:
                    return True, ""
            except ValueError:
                continue
        return False, "Ese archivo está fuera de las carpetas autorizadas (ENVIO_CARPETAS_AUTORIZADAS en config.py)."
    except Exception:
        log.exception("permitido falló")
        return False, "No pude validar la ruta."

# ─── RESOLUCIÓN Y ENVÍO ───────────────────────────────────────────────────
def _resolver_abridor(frase: str, pedir_eleccion) -> Tuple[Optional[str], str]:
    """(ruta, texto_error). ruta None + texto '' = nada encontrado (probar índice); texto != '' = cancelado/rechazado."""
    if abridor is None:
        return None, ""
    plan = abridor.resolver_apertura({"texto_original": frase, "categoria": "ABRIR_ARCHIVO", "solo_resolver": True})
    while plan and plan.get("estado") in ("elegir", "confirmar"):
        if plan["estado"] == "elegir":
            i = pedir_eleccion(plan["pregunta"], plan["opciones"])
            if i is None:
                return None, "Cancelado."
            plan = plan["continuar"](i)
        else:
            plan = plan["continuar"](False)
    if not plan or plan.get("estado") in ("no_encontrado", "no_entendido"):
        return None, ""
    if plan.get("estado") == "abrir":
        return plan["ruta"], ""
    return None, plan.get("texto", "No pude resolver el archivo.")

def _resolver_indice(rest: str, pedir_eleccion) -> Tuple[Optional[str], str]:
    if file_index is None:
        return None, ""
    limpio = re.sub(r"^(?:(?:el|la|los|las|un|una|mi)\s+)?(?:archivo|documento|fichero|pdf|foto|imagen)\s+(?:de\s+|del\s+)?", "", rest.strip(), flags=re.I)
    try:
        r = [x for x in file_index.buscar_archivo(limpio, limite=8, solo_carpetas=False)
             if not x.get("es_carpeta") and os.path.isfile(x.get("ruta", "")) and permitido(x["ruta"])[0]]
    except Exception:
        log.exception("índice falló")
        return None, ""
    if not r:
        return None, ""
    if len(r) == 1:
        return r[0]["ruta"], ""
    r = r[:5]
    i = pedir_eleccion(f"Encontré varios parecidos a «{limpio}». ¿Cuál te mando?", [x.get("nombre", x["ruta"]) for x in r])
    return (r[i]["ruta"], "") if i is not None else (None, "Cancelado.")

def ejecutar(texto: str, pedir_eleccion: Callable, enviar: Callable) -> Tuple[bool, str]:
    """pedir_eleccion(pregunta, opciones) -> índice|None · enviar(ruta, caption) -> bool. Nunca lanza."""
    try:
        iv = interpretar(texto)
        if not iv:
            return False, "No entendí qué archivo quieres."
        ruta, err = _resolver_abridor(iv["frase_abrir"], pedir_eleccion)
        if ruta is None and not err:
            ruta, err = _resolver_indice(iv["rest"], pedir_eleccion)
        if ruta is None:
            return (err == "Cancelado."), (err or f"No encontré «{iv['rest'][:60]}».")
        if os.path.isdir(ruta):
            return False, "Eso es una carpeta; por ahora solo envío archivos. Dime cuál archivo de dentro."
        ok, motivo = permitido(ruta)
        if not ok:
            log.warning("🚫 envío rechazado: %s", os.path.basename(ruta))
            return False, motivo
        mb = os.path.getsize(ruta) / 1048576
        if mb > float(_c("ENVIO_MAX_MB", 45)):
            return False, f"«{os.path.basename(ruta)}» pesa {mb:.0f} MB; Telegram solo deja bots enviar hasta 50 MB."
        log.warning("📤 enviando por Telegram: %s", os.path.basename(ruta))
        if enviar(ruta, os.path.basename(ruta)):
            return True, f"Te mandé «{os.path.basename(ruta)}»."
        return False, "No pude subir el archivo a Telegram."
    except Exception as e:
        log.exception("envio.ejecutar falló")
        return False, f"Error al enviar ({type(e).__name__})."
