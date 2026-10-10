# 📱 whatsapp.py — Puente WhatsApp de SARA (v1)
#
# Habilidades: enlazar con /whatsapp: <número> · avisar al usuario · leer mensajes nuevos ·
# mandar y responder mensajes a contactos · reenviar recordatorios al propio WhatsApp.
#
# Cómo: proceso auxiliar Node (wa_sidecar/, Baileys = dispositivo vinculado, SIN WhatsApp Web abierto).
# Principio 8: resolver ≠ ejecutar. resolver() decide; core_bridge pregunta (modal); ejecutar_envio() envía.
# SARA-Net (v2): tu chat contigo mismo como canal de control remoto. Solo cuenta lo que TÚ escribes en ese chat y
# empieza con «Sara»; el resto se descarta en silencio. Interruptor propio: /whatsapp net on|off (por defecto apagado).
# SARA-Net voz (v3): tus NOTAS DE VOZ en ese mismo chat se transcriben en local (stt.py / faster-whisper) y pasan por el
# mismo filtro de «Sara». Interruptor propio: /whatsapp net voz on|off (por defecto apagado).
# Aislado y opcional: si falta este archivo, core_bridge sigue igual.
#
# Contrato con core_bridge:
#   registrar_nucleo(procesar, confirmar)  ← SARA-Net: core_bridge entrega sus funciones (sin import circular)
#   crear_tablas() · arrancar() · comando_slash(arg) -> (ok, texto)
#   resolver(res) -> None | {"estado": elegir|candidato|ok|ejecutado|no_encontrado|no_vinculado|no_entendido, ...}
#   ejecutar_envio(plan) -> {"exito","texto"} · eventos_proactivos() -> [(tipo, datos)] · notificar_usuario(txt)

WHATSAPP_VERSION = "v3.1-baileys-sidecar+sara-net-voz+envio-natural"

import atexit
import inspect
import logging
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
import unicodedata
from collections import deque
from typing import Dict, List, Optional, Tuple

import requests

log = logging.getLogger("SARA-WA")

try:
    import config as _cfg
except Exception:
    _cfg = None
try:
    from database import conectar as _conectar
except Exception:
    _conectar = None


def _c(nombre: str, defecto):
    return getattr(_cfg, nombre, defecto) if _cfg is not None else defecto


def _log_error(contexto: str, exc: Exception) -> None:
    try:
        import logger as _sl
        _sl.log_excepcion("whatsapp", contexto, exc)
    except Exception:
        log.warning("%s: %s: %s", contexto, type(exc).__name__, exc)


def _activo() -> bool:
    return bool(_c("WA_ACTIVO", True))


_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_BIENVENIDA = (
    "Activaste el comando WhatsApp. Si quieres enlazar tu WhatsApp conmigo para notificarte, leerte "
    "mensajes o mandarlos por ti, escribe «/whatsapp: » seguido de tu número con código de país, solo "
    "dígitos (ej. /whatsapp: 52XXXXXXXXXX). Te daré un código de 8 caracteres para vincularme desde tu "
    "teléfono; no necesitas tener WhatsApp Web abierto."
)

# ─── MEMORIA (sara.db) ────────────────────────────────────────────────────

_tabla_ok = False


def crear_tablas() -> None:
    global _tabla_ok
    if _tabla_ok or _conectar is None:
        return
    try:
        with _conectar() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS wa_memoria (clave TEXT PRIMARY KEY, valor TEXT NOT NULL)")
            conn.commit()
        _tabla_ok = True
    except Exception as e:
        _log_error("crear_tablas", e)


def _kv_get(clave: str, defecto: Optional[str] = None) -> Optional[str]:
    if _conectar is None:
        return defecto
    try:
        crear_tablas()
        with _conectar() as conn:
            f = conn.execute("SELECT valor FROM wa_memoria WHERE clave = ?", (clave,)).fetchone()
        return f[0] if f else defecto
    except Exception as e:
        _log_error("_kv_get", e)
        return defecto


def _kv_set(clave: str, valor: str) -> None:
    if _conectar is None:
        return
    try:
        crear_tablas()
        with _conectar() as conn:
            conn.execute("INSERT INTO wa_memoria (clave, valor) VALUES (?, ?) "
                         "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (clave, valor))
            conn.commit()
    except Exception as e:
        _log_error("_kv_set", e)


def _kv_del(prefijo: str) -> None:
    if _conectar is None:
        return
    try:
        with _conectar() as conn:
            conn.execute("DELETE FROM wa_memoria WHERE clave = ? OR clave LIKE ?", (prefijo, prefijo + "%"))
            conn.commit()
    except Exception as e:
        _log_error("_kv_del", e)


def _token() -> str:
    t = _kv_get("token")
    if not t:
        t = secrets.token_hex(16)
        _kv_set("token", t)
    return t


# ─── PROCESO AUXILIAR (Node) ──────────────────────────────────────────────

_proc: Optional[subprocess.Popen] = None
_lock = threading.Lock()


def _dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "wa_sidecar")


def _puerto() -> int:
    return int(_c("WA_PUERTO", 8765))


def _http(metodo: str, ruta: str, datos: Optional[dict] = None, params: Optional[dict] = None,
          timeout: float = 10) -> Optional[dict]:
    try:
        r = requests.request(metodo, f"http://127.0.0.1:{_puerto()}{ruta}", json=datos, params=params,
                             headers={"x-sara-token": _token()}, timeout=timeout)
        if r.status_code == 401:
            return {"error": "token"}
        return r.json()
    except Exception as e:
        log.debug("sidecar %s %s: %s", metodo, ruta, e)
        return None


def iniciar_sidecar() -> Tuple[bool, str]:
    global _proc
    with _lock:
        r = _http("GET", "/status", timeout=2)
        if r is not None:
            if r.get("error") == "token":
                return False, f"Hay otro programa usando el puerto {_puerto()}. Ciérralo o cambia WA_PUERTO."
            return True, "ok"
        d = _dir()
        if not os.path.isfile(os.path.join(d, "index.js")):
            return False, "Falta la carpeta wa_sidecar (index.js) junto a whatsapp.py."
        node = shutil.which(str(_c("WA_NODE", "node")))
        if not node:
            return False, "Necesito Node.js 18 o superior instalado (nodejs.org)."
        if not os.path.isdir(os.path.join(d, "node_modules")):
            return False, "Falta instalar dependencias: abre una terminal en la carpeta wa_sidecar y ejecuta: npm install"
        try:
            env = dict(os.environ, WA_PORT=str(_puerto()), WA_TOKEN=_token())
            logf = open(os.path.join(d, "sidecar.log"), "a", encoding="utf-8")
            _proc = subprocess.Popen([node, "index.js"], cwd=d, env=env, stdout=logf, stderr=logf,
                                     stdin=subprocess.DEVNULL, creationflags=_SIN_VENTANA)
        except Exception as e:
            _log_error("iniciar_sidecar", e)
            return False, "No pude iniciar el puente de WhatsApp (revisa el log)."
        for _ in range(40):
            time.sleep(0.3)
            if _http("GET", "/status", timeout=1) is not None:
                return True, "ok"
        return False, "El puente de WhatsApp no respondió; revisa wa_sidecar/sidecar.log"


def _detener_sidecar() -> None:
    try:
        if _proc is not None and _proc.poll() is None:
            _proc.terminate()
    except Exception:
        pass


atexit.register(_detener_sidecar)


def estado() -> dict:
    r = _http("GET", "/status", timeout=3)
    return r if (r and not r.get("error")) else {"estado": "apagado"}


def arrancar() -> None:
    """Llamado en el arranque pesado: si ya había un enlace, se reconecta solo."""
    if not _activo() or not _kv_get("numero"):
        return
    ok, msg = iniciar_sidecar()
    if ok:
        _http("POST", "/connect", {}, timeout=20)
        _asegurar_hilos_net()
    else:
        log.warning("WhatsApp no arrancó: %s", msg)


# ─── TEXTO ────────────────────────────────────────────────────────────────

def _plano(s: str) -> str:
    """Minúsculas sin tildes, MISMA longitud que el original (para recortar el original con los índices)."""
    return "".join(unicodedata.normalize("NFD", ch)[0] for ch in (s or "")).lower()


def _clave(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _plano(s))


# ─── /whatsapp ────────────────────────────────────────────────────────────

_esperando_enlace = False


def _resumen() -> str:
    st = estado()
    num = _kv_get("numero", "")
    e = st.get("estado")
    if e == "abierto":
        return (f"WhatsApp enlazado (+{num}). Puedo:\n"
                "• «mándale a Juan que ya voy» · «dile a mamá que llego tarde» · «escríbele a Ana: nos vemos» · «pregúntale a Luis si ya salió» (no hace falta decir WhatsApp; siempre te confirmo antes de enviar)\n"
                "• «lee mis mensajes de whatsapp» · «mensajes de Juan»\n"
                "• «respóndele que ya voy» (al último que te escribió)\n"
                "• «avísame por whatsapp que ...» (a tu propio chat)\n"
                "• SARA-Net: escríbete «Sara, abre Chrome» en tu chat contigo mismo (actívalo con /whatsapp net on)\n"
                "Ajustes: /whatsapp notificar on|off · /whatsapp net on|off · /whatsapp estado · /whatsapp desconectar")
    return f"WhatsApp: estado «{e}». Si no conecta, usa /whatsapp estado o vuelve a enlazar con /whatsapp: {num or 'número'}."


def comando_slash(arg: str) -> Tuple[bool, str]:
    global _esperando_enlace
    if not _activo():
        return False, "El módulo de WhatsApp está desactivado (WA_ACTIVO)."
    a = (arg or "").strip().lstrip(":").strip()
    low = a.lower()
    num = _kv_get("numero")

    if low in ("desconectar", "salir", "logout", "olvidar"):
        _http("POST", "/logout", {}, timeout=10)
        _kv_del("numero")
        _kv_del("alias:")
        return True, "WhatsApp desvinculado. Puedes quitar el dispositivo «SARA» en WhatsApp → Dispositivos vinculados."
    if low.startswith("notificar"):
        on = not low.endswith("off")
        _kv_set("notificar", "1" if on else "0")
        return True, f"Avisos de mensajes entrantes {'activados' if on else 'desactivados'}."
    if low.startswith("net"):
        return _comando_net(low[3:].strip(" :"))
    if low in ("estado", "diagnostico", "diagnóstico"):
        return True, diagnostico()
    if low.startswith(("alias", "contacto")):
        m = re.match(r"^(?:alias|contacto)\s+(.+?)\s+\+?([\d\s\-()]{10,})$", a, re.I)
        dig = re.sub(r"\D", "", m.group(2)) if m else ""
        if not m or not 11 <= len(dig) <= 15:
            return False, "Uso: /whatsapp alias <nombre> <número con código de país>, ej. /whatsapp alias mamá 52XXXXXXXXXX"
        nombre = _limpiar_nombre(m.group(1))
        _kv_set("alias:" + _clave(nombre), dig)
        return True, f"Listo: «{nombre}» → +{dig}. Ya puedes decir «manda un whatsapp a {nombre} diciendo…»."
    if not a:
        return True, (_resumen() if num else _BIENVENIDA)

    dig = re.sub(r"\D", "", a)
    if not 11 <= len(dig) <= 15:
        return False, ("Escribe tu número completo con código de país y solo dígitos, "
                       "ej. /whatsapp: 52XXXXXXXXXX (código de país + número).")
    ok, msg = iniciar_sidecar()
    if not ok:
        return False, msg
    _asegurar_hilos_net()
    _kv_set("numero", dig)
    r = _http("POST", "/connect", {"phone": dig}, timeout=45)
    if r is None or r.get("error"):
        return False, f"No pude pedir el código de enlace: {(r or {}).get('error', 'sin respuesta')}"
    if r.get("estado") == "abierto":
        return True, "Ya estoy enlazada a tu WhatsApp."
    code = str(r.get("code") or "")
    if not code:
        return False, "WhatsApp no me dio un código. Reintenta en unos segundos."
    _esperando_enlace = True
    code = f"{code[:4]}-{code[4:]}" if len(code) == 8 else code
    return True, (f"Tu código de enlace: {code}\n"
                  "En tu teléfono: WhatsApp → Ajustes → Dispositivos vinculados → Vincular un dispositivo → "
                  "«Vincular con número de teléfono» e ingresa el código. Te aviso cuando quede enlazado.")


def diagnostico() -> str:
    st = estado()
    return ("WhatsApp:\n"
            f"Versión de whatsapp.py: {WHATSAPP_VERSION}\n"
            f"Puente: {st.get('estado')} · contactos conocidos: {st.get('contactos', '—')} "
            f"(con nombre de tu libreta: {st.get('libreta', '—')})\n"
            f"Número: {_kv_get('numero') or '—'} · avisos entrantes: "
            f"{'sí' if _kv_get('notificar', '1') == '1' else 'no'} · confirmar envíos: "
            f"{'sí' if _c('WA_CONFIRMAR_ENVIO', True) else 'no'}\n"
            f"{_net_resumen()}\n"
            "Uso: /whatsapp · /whatsapp: número · /whatsapp estado|notificar on|off|net on|off|net voz on|off|desconectar")


# ─── INTERPRETAR FRASES ───────────────────────────────────────────────────
# Lenguaje natural: «mándale un mensaje a Juan que ya voy», «escríbele a mamá: llego tarde», «dile a Juan que…»,
# «avísale a mi jefe por wasap que…», «pregúntale a Ana si ya salió». No hace falta decir «WhatsApp» ni usar
# un separador fijo: si no lo hay, el límite entre el nombre y el mensaje se busca con TU libreta de contactos.

_PREF = r"^(?:(?:oye\s+|hey\s+|ok\s+)?sara[,\s]+|por\s+favor\s+|porfa\s+|podrias\s+|puedes\s+|me\s+puedes\s+|quiero\s+que\s+|"
_PREF += r"necesito\s+que\s+)*"
_WA = r"(?:whatsapp|whatsap|wassap|wasap|wasapp|watsap|watsapp|guasap|guatsap|guasapp|whats\s+app|wats\s+app)"
_RE_WA = re.compile(r"\b" + _WA + r"\b")
_OTRO_CANAL = re.compile(r"\b(?:telegram|correo|email|e\s*mail|gmail|outlook|sms|messenger|facebook|instagram|signal|"
                         r"teams|slack|discord|twitter)\b")

# Verbos de enviar (con y sin «le»); «dile/avísale» llevan el mensaje con «que». «mándame…» = a tu propio chat.
_V_ENVIAR = (r"(?:manda|mandale|mandar|mandarle|mande|envia|enviale|enviar|enviarle|escribe|escribele|"
             r"escribir|escribirle|pasale|textea|textale|mensajea|mensajeale)")
_V_YO = r"(?:mandame|enviame|escribeme|mensajeame)"
_V_DILE = r"(?:dile|decile|avisale|avisa|comunicale|hazle\s+saber|hazle\s+llegar)"
_V_PREGUNTA = r"(?:preguntale|pregunta)"
_V_RESP = r"(?:responde|respondele|contesta|contestale)"
_NOUN = r"(?:mensajito|mensaje|" + _WA + r"|texto|recado|aviso)"
_NOMBRE_MSG = r"(?:(?:un|una|el)\s+)?(?:" + _NOUN + r")?"

# Separadores entre el contacto y el mensaje (el mensaje empieza DESPUÉS de ellos).
_SEP = (r"(?:(?:diciendole|diciendo|avisandole|para\s+decirle|para\s+avisarle)(?:\s+que|\s*:)?|que\s+diga|que\s+dice|"
        r"que\s+ponga|con\s+el\s+texto|con\s+el\s+mensaje|que|:|;)")
_RE_SEP = re.compile(r"(?:^|\s)" + _SEP + r"(?:\s+|$)|:\s*")

_RE_ENVIAR = re.compile(_PREF + r"(?P<v>" + _V_ENVIAR + r")\s+(?P<n>(?:(?:un|una|el)\s+)?" + _NOUN + r"\s+)?"
                        r"(?:(?:por|de)\s+" + _WA + r"\s+)?a\s+(?P<resto>.+)$")
_RE_YO = re.compile(_PREF + r"(?P<v>" + _V_YO + r")\s+(?:un\s+|una\s+)?(?P<n>" + _NOUN + r")\s*"
                    r"(?:(?:diciendo\s+que|diciendo|que\s+diga|que\s+dice|que|:)\s*)?(?P<m>.*)$")
_RE_DILE = re.compile(_PREF + r"(?P<v>" + _V_DILE + r")\s+a\s+(?P<resto>.+)$")
_RE_PREGUNTA = re.compile(_PREF + r"(?P<v>" + _V_PREGUNTA + r")\s+a\s+(?P<resto>.+)$")
_RE_AVISAME = re.compile(_PREF + r"avisame\s+por\s+" + _WA + r"\s+(?:que\s+)?(?P<m>.+)$")
_RE_RESP = re.compile(_PREF + r"(?:" + _V_RESP + r")(?:\s+a\s+(?P<c>.+?))?"
                      r"(?:\s+por\s+" + _WA + r")?\s+(?:diciendo\s+|diciendole\s+|que\s+|con\s+)?(?P<m>.+)$")
_RE_LEER = re.compile(r"\b(?:lee|leeme|leer|dime|revisa|revisame|checa|chequea|muestrame|mostrar|ver|tengo|hay|cuales|que)\b"
                      r"(?=.*\b(?:mensajes|mensaje|notificaciones|chats)\b)(?=.*\b" + _WA + r"\b)")
_RE_QUIEN = re.compile(r"\bquien(?:es)?\s+me\s+(?:escribio|escribieron|mando|mandaron)\b")
_COMPUESTO = re.compile(r"\b(?:y|luego|despues|ademas|tambien)\s+(?:abre|cierra|busca|pon|ponme|reproduce|toca|ejecuta|crea|"
                        r"muestra|apaga|enciende|sube|baja|silencia|mutea)\b")


def _sin_wa(texto: str) -> str:
    """Quita «por whatsapp» / «de wasap» del texto (la palabra sola no es parte del contacto ni del mensaje)."""
    return re.sub(r"\s+(?:por|de|en|via)\s+" + _WA + r"\b", "", texto, flags=re.I)


def _dividir_resto(resto: str, original_resto: str, guess: bool, libreta_ok: bool = True) -> Optional[Tuple[str, str]]:
    """«Juan que ya voy» → ('Juan', 'ya voy').
    1) separador explícito (que / diciendo / :)  2) sin separador: el nombre más largo de tu libreta que abra la frase
    3) solo si guess=True (dijo «mensaje»/«whatsapp»): la primera palabra es el contacto."""
    m = _RE_SEP.search(resto)
    if m and m.start() > 0 and resto[:m.start()].strip():
        c = original_resto[:m.start()].strip(" ,:;")
        msg = re.sub(r"^(?:que\s+)", "", original_resto[m.end():].strip(), flags=re.I).strip()
        if c and msg and len(c.split()) <= 6:
            return c, msg
    palabras = original_resto.split()
    if len(palabras) < 2:
        return None
    k = _nombre_que_abre(palabras) if libreta_ok else 0
    if k:
        return " ".join(palabras[:k]), " ".join(palabras[k:]).strip()
    if guess:
        return palabras[0], " ".join(palabras[1:]).strip()
    return None


def _nombre_que_abre(palabras: List[str]) -> int:
    """Cuántas palabras iniciales forman el nombre de contacto de tu libreta más largo (0 = ninguno)."""
    nombres = _claves_libreta()
    if not nombres:
        return 0
    for k in range(min(5, len(palabras) - 1), 0, -1):
        if _clave(_limpiar_nombre(" ".join(palabras[:k]))) in nombres:
            return k
    return 0


def _pregunta_a_mensaje(frase: str) -> str:
    """«si ya salió» → «¿Ya salió?» · «cuándo llegas» → «¿Cuándo llegas?» (se le confirma al usuario antes de enviar)."""
    f = re.sub(r"^(?:que\s+|si\s+)", "", frase.strip(), flags=re.I).strip(" ¿?")
    return ("¿" + f[:1].upper() + f[1:] + "?") if f else ""


def _analizar(orig: str) -> Optional[dict]:
    p = _plano(orig).strip()
    o = orig.strip()
    if len(p) > 700 or _OTRO_CANAL.search(p):
        return None
    if _COMPUESTO.search(p) and not _RE_LEER.search(p):
        return None                                      # «dile a X que… y abre youtube»: el puente la separa en órdenes
    # 1) avísame por whatsapp que … / mándame un mensaje …  (a tu propio chat)
    m = _RE_AVISAME.match(p) or _RE_YO.match(p)
    if m:
        msg = o[m.start("m"):m.end("m")].strip().strip('"«»').strip()
        if msg:
            return {"accion": "enviar", "contacto": "yo", "mensaje": msg}
    # 2) responder al último que escribió (o a alguien concreto)
    m = _RE_RESP.match(p)
    if m:
        msg = o[m.start("m"):m.end("m")].strip().strip('"«»').strip()
        c = o[m.start("c"):m.end("c")].strip() if m.group("c") else None
        if msg:
            return {"accion": "responder", "contacto": c, "mensaje": msg}
    # 3) mandar / escribir / decir / preguntar a un contacto
    for rx, tipo in ((_RE_ENVIAR, "enviar"), (_RE_DILE, "dile"), (_RE_PREGUNTA, "pregunta")):
        m = rx.match(p)
        if not m:
            continue
        con_noun = bool(tipo == "enviar" and m.group("n")) or bool(_RE_WA.search(p))
        resto_o = _sin_wa(o[m.start("resto"):m.end("resto")])
        resto_p = _plano(resto_o)
        corte = _dividir_resto(resto_p, resto_o, guess=con_noun, libreta_ok=(con_noun or tipo in ("dile", "pregunta")))
        if corte is None:
            # «manda un mensaje a Juan» sin texto: se le pide el mensaje (solo si dijo mensaje/whatsapp)
            if con_noun and resto_o.strip():
                return {"accion": "falta_mensaje", "contacto": _limpiar_nombre(resto_o.strip(" ,:;")), "mensaje": None}
            continue
        contacto, mensaje = corte
        mensaje = mensaje.strip().strip('"«»').strip()
        if tipo == "pregunta":
            mensaje = _pregunta_a_mensaje(mensaje)
        if contacto and mensaje:
            return {"accion": "enviar", "contacto": contacto.strip(), "mensaje": mensaje}
    # 4) leer
    if _RE_LEER.search(p) or _RE_QUIEN.search(p):
        q = re.sub(r"\s+(?:de|por|en)\s+" + _WA + r"\s*$", "", p)
        m = re.search(r"\bmensajes\s+de\s+(.+)$", q)
        c = m.group(1).strip() if m else None
        if c:
            c = re.sub(r"\s+(?:en|por|de)\s+" + _WA + r"$", "", c).strip()
        if c and (_RE_WA.fullmatch(c) or c in ("hoy", "ahora", "nuevos")):
            c = None
        return {"accion": "leer", "contacto": c, "mensaje": None}
    return None


# ─── CONTACTOS ────────────────────────────────────────────────────────────

_ultimo_remitente: Optional[Tuple[str, str]] = None


def _limpiar_nombre(q: str) -> str:
    """'mi mamá' / 'a mamá' / 'la tía Carmen' → sin el artículo (como se dice en voz natural)."""
    return re.sub(r"^(?:mi|el|la|a|al|los|las)\s+", "", (q or "").strip(), flags=re.I).strip()


# Tu libreta, leída del propio archivo del sidecar (wa_sidecar/contactos.json) y cacheada por fecha de modificación.
_cache_lib: Dict[str, object] = {"mtime": None, "lista": []}


def _libreta() -> List[dict]:
    ruta = os.path.join(_dir(), "contactos.json")
    try:
        mt = os.path.getmtime(ruta)
        if _cache_lib["mtime"] != mt:
            import json
            with open(ruta, encoding="utf-8") as f:
                datos = json.load(f)
            _cache_lib["lista"] = [c for c in datos if isinstance(c, dict) and c.get("jid") and (c.get("nombre") or c.get("notify"))]
            _cache_lib["mtime"] = mt
    except Exception:
        pass
    return _cache_lib["lista"]      # type: ignore[return-value]


def _claves_libreta() -> set:
    """Claves (sin tildes ni símbolos) de los nombres que TÚ guardaste; sirven para partir «Ana López ya voy»."""
    try:
        return {_clave(c["nombre"]) for c in _libreta() if c.get("nombre")} - {""}
    except Exception:
        return set()


def _unificar(lista: List[dict]) -> List[dict]:
    """Un mismo contacto llega 2 veces (número @s.whatsapp.net y @lid) con el MISMO nombre: se deja uno por persona,
    prefiriendo el número. Si hay más de un número con ese nombre son personas distintas y se conservan."""
    grupos: Dict[str, List[dict]] = {}
    for c in lista:
        grupos.setdefault(_clave(c.get("nombre", "")) or c["jid"], []).append(c)
    salida: List[dict] = []
    for _, g in grupos.items():
        pn = [c for c in g if str(c["jid"]).endswith("@s.whatsapp.net")]
        lid = [c for c in g if not str(c["jid"]).endswith("@s.whatsapp.net")]
        salida.extend(pn + lid[len(pn):])
    return salida


def _foneticamente(s: str) -> str:
    """Clave fonética sencilla para nombres mal oídos por el STT («Yuan»→«Juan», «Bicente»→«Vicente»)."""
    t = _clave(s)
    for a, b in (("ll", "y"), ("y", "j"), ("qu", "k"), ("ce", "se"), ("ci", "si"), ("ca", "ka"), ("co", "ko"), ("cu", "ku"),
                 ("v", "b"), ("z", "s"), ("h", ""), ("w", "g"), ("j", "g"), ("x", "s")):
        t = t.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", t)


def _difuso(q: str) -> List[dict]:
    """Sin coincidencia exacta: nombres de tu libreta MUY parecidos (hasta 3). Siempre se confirma con el usuario."""
    from difflib import SequenceMatcher
    qf = _foneticamente(q)
    if len(qf) < 3:
        return []
    puntos: List[Tuple[float, dict]] = []
    for c in _unificar(_libreta()):
        nombre = c.get("nombre") or c.get("notify") or ""
        mejores = [SequenceMatcher(None, qf, _foneticamente(nombre)).ratio()]
        for palabra in re.split(r"\s+", nombre):                     # «Yuan» ~ «Juan Pérez»
            if len(_clave(palabra)) >= 3:
                mejores.append(0.95 * SequenceMatcher(None, qf, _foneticamente(palabra)).ratio())   # el nombre completo pesa más
        r = max(mejores)
        if r >= 0.8:
            puntos.append((r, {"jid": c["jid"], "nombre": nombre, "libreta": bool(c.get("nombre"))}))
    puntos.sort(key=lambda x: -x[0])
    if len(puntos) > 1 and puntos[0][0] - puntos[1][0] >= 0.04:      # uno claramente mejor: se propone solo ese
        return [puntos[0][1]]
    return [d for _, d in puntos[:3]]


def _resolver_contacto(q: str) -> Tuple[str, object]:
    q0 = _limpiar_nombre(q)
    if re.fullmatch(r"[\d\s+\-()]{10,}", q0):
        dig = re.sub(r"\D", "", q0)
        return "ok", {"jid": dig, "nombre": "+" + dig}
    ali = _kv_get("alias:" + _clave(q0))
    if ali:
        return "ok", {"jid": ali, "nombre": q0}
    r = _http("GET", "/contacts", params={"q": q0, "limit": 40}, timeout=8) or {}
    lista = _unificar(r.get("contactos") or [])
    if not lista:
        cand = _difuso(q0)                                  # ¿lo oyó mal el micrófono? se propone y se confirma
        if len(cand) == 1:
            return "aprox", cand[0]
        if cand:
            return "ambiguo", cand
        return "no", None
    exactos = [c for c in lista if _clave(c["nombre"]) == _clave(q0)]
    if len(exactos) == 1 or (not exactos and len(lista) == 1):
        return "ok", (exactos or lista)[0]
    libreta = [c for c in (exactos or lista) if c.get("libreta")]
    pool = libreta if (not exactos and 0 < len(libreta) < len(lista) and len(libreta) <= 5) else (exactos or lista)
    if len(pool) == 1:
        return "ok", pool[0]
    return "ambiguo", pool[:5]


def _candidato(dato: dict, mensaje: str, aprox: Optional[str] = None) -> dict:
    mensaje = mensaje[:1000]
    cabeza = (f"No encontré «{aprox}» tal cual. ¿Envío por WhatsApp a {dato['nombre']}?" if aprox
              else f"¿Envío por WhatsApp a {dato['nombre']}?")
    plan = {"estado": "candidato", "jid": dato["jid"], "nombre": dato["nombre"], "mensaje": mensaje,
            "mensaje_confirmacion": f"{cabeza}\n    «{mensaje}»"}
    if not _c("WA_CONFIRMAR_ENVIO", True):
        r = ejecutar_envio(plan)
        return {"estado": "ejecutado", **r}
    return plan


def _plan_envio(contacto: Optional[str], mensaje: str) -> dict:
    if contacto == "yo":
        r = ejecutar_envio({"jid": "yo", "nombre": "ti", "mensaje": mensaje[:1000]})
        return {"estado": "ejecutado", **r}
    if not contacto:
        if not _ultimo_remitente:
            return {"estado": "no_entendido", "exito": False,
                    "texto": "¿A quién? Nadie te ha escrito todavía; dime el nombre o el número."}
        return _candidato({"jid": _ultimo_remitente[0], "nombre": _ultimo_remitente[1]}, mensaje)
    est, dato = _resolver_contacto(contacto)
    if est == "no":
        return {"estado": "no_encontrado", "exito": False,
                "texto": f"No encontré a «{contacto}» en tus contactos de WhatsApp. "
                         "Dime su número con código de país."}
    if est == "aprox":
        return _candidato(dato, mensaje, aprox=contacto)
    if est == "ambiguo":
        cands = dato

        def _eligio(i):
            c = cands[int(i)]
            _kv_set("alias:" + _clave(contacto), c["jid"])
            return _candidato(c, mensaje)
        return {"estado": "elegir", "pregunta": f"Encontré varios contactos «{contacto}». ¿A cuál?",
                "opciones": [f"{c['nombre']}  ({c['jid'].split('@')[0][-4:].rjust(4, '·')})" for c in cands], "continuar": _eligio}
    return _candidato(dato, mensaje)


# ─── LECTURA ──────────────────────────────────────────────────────────────

_boot: Optional[str] = None
_cursor_lectura = 0
_cursor_aviso = 0


def _traer(desde: int) -> List[dict]:
    global _boot, _cursor_lectura, _cursor_aviso
    r = _http("GET", "/messages", params={"since": desde, "limit": 80}, timeout=8)
    if not r or r.get("error"):
        return []
    if _boot and r.get("boot") != _boot:          # el puente se reinició: los ids vuelven a empezar
        _cursor_lectura = _cursor_aviso = 0
        _boot = r.get("boot")
        return _traer(0)
    _boot = r.get("boot")
    return r.get("mensajes") or []


_DOMINIOS_PERSONA = ("s.whatsapp.net", "lid", "c.us")      # número normal, LID y el formato antiguo


def _es_persona(jid) -> bool:
    """True solo para chats de PERSONAS (conocidas o desconocidas). Canales (@newsletter), listas de difusión
    (@broadcast), estados y demás → False. Los grupos tienen su propio ajuste (WA_IGNORAR_GRUPOS).
    Doble seguro: index.js ya los descarta, pero si el sidecar es una versión vieja esto los frena igual."""
    dom = str(jid or "").rsplit("@", 1)[-1].lower()
    return dom in _DOMINIOS_PERSONA or dom == "g.us"


def _validos(msgs: List[dict]) -> List[dict]:
    ign = bool(_c("WA_IGNORAR_GRUPOS", True))
    return [m for m in msgs
            if not m.get("mia") and _es_persona(m.get("jid")) and not (ign and m.get("grupo"))]


def _leer_bandeja(filtro: Optional[str]) -> str:
    global _cursor_lectura
    msgs = _traer(_cursor_lectura)
    if msgs:
        tope = max(m["id"] for m in msgs)
    msgs = _validos(msgs)
    if filtro:
        msgs = [m for m in msgs if _clave(filtro) in _clave(m["nombre"])]
    elif msgs:
        _cursor_lectura = tope
    if not msgs:
        return "No tienes mensajes nuevos en WhatsApp." if not filtro else f"No hay mensajes nuevos de «{filtro}»."
    grupos: Dict[str, List[str]] = {}
    for m in msgs:
        grupos.setdefault(m["nombre"], []).append(m["texto"])
    lin = [f"Tienes {len(msgs)} mensaje(s) nuevo(s) en WhatsApp:"]
    for nombre, textos in list(grupos.items())[:8]:
        lin.append(f"• {nombre}: " + " / ".join(t[:160] for t in textos[-3:]))
    return "\n".join(lin)


# ─── SARA-NET: control remoto por tu chat contigo mismo ───────────────────
# Flujo: hilo sondea /messages → filtra (solo TU chat propio + palabra «Sara» al inicio + reciente) →
# cola → hilo ejecutor llama al núcleo con origen "whatsapp" → la respuesta vuelve a tu chat.
# Las confirmaciones (acciones de riesgo) también se preguntan y se contestan por ese chat.

_net_lock = threading.Lock()
_net_hilos_ok = False
_net_resync = True                 # al arrancar / al activar: ignora lo que ya estaba en el buzón
_net_cursor = 0
_net_boot: Optional[str] = None
_net_cola: "queue.Queue" = queue.Queue(maxsize=3)
_net_eventos: deque = deque(maxlen=20)       # avisos para la GUI (los recoge eventos_proactivos)
_net_cmds: deque = deque(maxlen=30)          # instantes de órdenes aceptadas (límite por minuto)
_net_vistos: deque = deque(maxlen=300)       # ids de mensaje ya atendidos (anti-duplicado)
_net_stats = {"vistos": 0, "aceptados": 0, "sin_activacion": 0, "ajenos": 0, "viejos": 0, "propios_no_reconocidos": 0,
              "voz_aceptadas": 0, "voz_errores": 0}
_net_cola_voz: "queue.Queue" = queue.Queue(maxsize=3)   # notas de voz pendientes de transcribir
_net_ultima_voz = ""                                     # lo último que oyó (SOLO en RAM, nunca a disco ni al log)
_net_ultimo_descarte = "—"
_net_conf: Optional[dict] = None             # confirmación pendiente de respuesta por WhatsApp
_nucleo_procesar = None                      # (texto, emit) -> {"exito","texto"}
_nucleo_confirmar = None                     # (id, respuesta, opcion) -> bool
_nucleo_acepta_voz = False                   # ¿el núcleo registrado admite el parámetro `voz`? (se decide al registrar)

_SI = {"si", "s", "ok", "okay", "dale", "va", "sip", "claro", "confirmo", "confirmar", "yes", "y", "adelante", "hazlo",
       "envialo"}
_NO = {"no", "n", "cancela", "cancelar", "cancelalo", "nel", "nop", "detente", "para"}
_MARCAS_PROPIAS = ("🤖", "✅", "⚠️", "⌛", "🎙️")


def registrar_nucleo(procesar, confirmar) -> None:
    """core_bridge entrega aquí sus funciones (así whatsapp.py no importa core_bridge: sin ciclos)."""
    global _nucleo_procesar, _nucleo_confirmar, _nucleo_acepta_voz
    _nucleo_procesar, _nucleo_confirmar = procesar, confirmar
    try:       # se mira la firma UNA vez: un TypeError al ejecutar una orden no debe provocar un segundo intento
        p = inspect.signature(procesar).parameters
        _nucleo_acepta_voz = "voz" in p or any(x.kind == inspect.Parameter.VAR_KEYWORD for x in p.values())
    except (TypeError, ValueError):
        _nucleo_acepta_voz = False


def _net_on() -> bool:
    v = _kv_get("net")
    return bool(_c("WA_NET_ACTIVO", False)) if v is None else v == "1"


def _modulo_stt():
    """stt.py (opcional). None si falta o es una versión sin transcribir_archivo."""
    try:
        import stt
        return stt if hasattr(stt, "transcribir_archivo") else None
    except Exception:
        return None


def _voz_on() -> bool:
    v = _kv_get("net_voz")
    return bool(_c("WA_NET_AUDIO_ACTIVO", False)) if v is None else v == "1"


def _voz_max_s() -> int:
    return max(5, int(_c("WA_NET_AUDIO_MAX_S", 30)))


def _palabras_net(voz: bool = False) -> Tuple[str, ...]:
    """Palabras de activación. En notas de voz se suman las variantes que Whisper suele oír (zara, sarra...)."""
    base = list(_c("WA_NET_PALABRAS", ("sara", "sarah")))
    if voz:
        s_ = _modulo_stt()
        if s_ is not None:
            base += list(getattr(s_, "PALABRAS_ACTIVACION", ()) or ())
    return tuple(dict.fromkeys(w for w in (_clave(x) for x in base) if w))


def _extraer_orden(texto: str, voz: bool = False) -> Optional[str]:
    """None si NO empieza con «Sara» (o variante). Si sí: la orden pura, sin la palabra de activación."""
    ws = _palabras_net(voz)
    if not ws:
        return None
    p = _plano(texto)
    m = re.match(r"^[\s¡¿\"'«.,\-–—]*(?:(?:oye|hey|ey|hola)[\s,.]+)?(?:" + "|".join(re.escape(w) for w in ws)
                 + r")\b[\s,:;.!¡¿?\-–—]*", p)
    return texto[m.end():].strip() if m else None


def _responder_net(texto: str) -> bool:
    """Tarjeta de respuesta a tu propio chat. Nunca lanza."""
    try:
        r = ejecutar_envio({"jid": "yo", "nombre": "ti", "mensaje": ("🤖 " + str(texto))[:1500]})
        if not r.get("exito"):
            log.warning("SARA-Net: no pude responder: %s", r.get("texto"))
        return bool(r.get("exito"))
    except Exception as e:
        _log_error("_responder_net", e)
        return False


def _emit_net(tipo: str, **datos) -> None:
    """El 'emit' que recibe el núcleo para órdenes de WhatsApp: solo las confirmaciones viajan por el chat."""
    global _net_conf
    try:
        if tipo == "solicitud_confirmacion":
            ops = [str(o) for o in (datos.get("opciones") or [])]
            seg = int(datos.get("timeout_segundos") or 30)
            _net_conf = {"id": datos.get("id_confirmacion"), "opciones": ops, "expira": time.time() + seg + 5}
            lin = [str(datos.get("pregunta", ""))]
            if ops:
                lin += [f"{i}) {o}" for i, o in enumerate(ops, 1)]
                lin.append(f"Responde con el número, o «no» para cancelar ({seg} s).")
            else:
                lin.append(f"Responde «sí» o «no» ({seg} s).")
            _responder_net("\n".join(lin))
        elif tipo == "confirmacion_ya_resuelta":
            hubo = _net_conf is not None
            _net_conf = None
            if hubo and datos.get("resultado") == "timeout":
                _responder_net("⌛ Sin respuesta a tiempo: cancelé la acción.")
    except Exception as e:
        _log_error("_emit_net", e)


def _intentar_confirmacion(texto: str) -> bool:
    """True si el mensaje era la respuesta a una confirmación pendiente (y ya se entregó al núcleo)."""
    global _net_conf
    c = _net_conf
    if not c:
        return False
    if time.time() > c["expira"]:
        _net_conf = None
        return False
    p = re.sub(r"[^a-z0-9 ]", "", _plano(texto)).strip()
    if not p or len(p.split()) > 3:
        return False
    ops = c["opciones"]
    resp, idx = None, None
    if p in _NO:
        resp = "no"
    elif ops and p.isdigit() and 1 <= int(p) <= len(ops):
        resp, idx = "si", int(p) - 1
    elif not ops and p in _SI:
        resp = "si"
    if resp is None:
        _responder_net("Para esa pregunta responde con el número de la opción o «no»." if ops
                       else "Para esa pregunta responde «sí» o «no».")
        return True
    _net_conf = None
    try:
        if _nucleo_confirmar is not None:
            _nucleo_confirmar(c["id"], resp, idx)
    except Exception as e:
        _log_error("_intentar_confirmacion", e)
    return True


def _descartar(motivo: str, clave: str) -> None:
    global _net_ultimo_descarte
    _net_stats[clave] = _net_stats.get(clave, 0) + 1
    _net_ultimo_descarte = motivo
    log.debug("SARA-Net descarta: %s", motivo)


def _net_atender(m: dict) -> None:
    """Decide qué hacer con UN mensaje del buzón. Nunca registra su contenido en el log."""
    wid = m.get("wid")
    if wid:
        if wid in _net_vistos:
            return
        _net_vistos.append(wid)
    _net_stats["vistos"] += 1
    texto = str(m.get("texto") or "").strip()
    if m.get("grupo") or not m.get("mia"):
        _borrar_audio(m.get("audio"))                      # el sidecar no debería descargarlo, pero si llega: fuera del disco
        return _descartar("mensaje de otra persona/grupo (nunca ejecuta)", "ajenos")
    if not m.get("propio"):
        _borrar_audio(m.get("audio"))
        dom = str(m.get("jid") or "").split("@")[-1] or "?"
        return _descartar(f"mensaje tuyo en otro chat o jid no reconocido como tu chat (@{dom})", "propios_no_reconocidos")
    if not texto or texto.startswith(_MARCAS_PROPIAS):
        return                                             # eco de las respuestas de SARA
    if m.get("audio") or m.get("audio_error"):
        return _net_atender_audio(m)                       # nota de voz propia (el sidecar solo las descarga si la voz está activa)
    if _intentar_confirmacion(texto):
        return
    orden = _extraer_orden(texto)
    if orden is None:
        return _descartar("sin palabra de activación", "sin_activacion")
    _net_encolar(orden, m.get("ts"))


def _net_encolar(orden: str, ts, voz: bool = False) -> None:
    """Últimas comprobaciones y entrega al ejecutor. Texto y voz comparten TODAS las reglas."""
    edad = time.time() - float(ts or time.time())
    if edad > float(_c("WA_NET_MAX_EDAD_S", 180)):
        _net_stats["viejos"] += 1
        _responder_net(f"⌛ Ignoré una orden tuya porque llegó con {int(edad // 60)} min de retraso (por seguridad no ejecuto órdenes viejas).")
        return
    if not orden:
        _responder_net("Aquí estoy. Dime qué hacer, por ejemplo: «Sara, abre Chrome».")
        return
    if orden.startswith("/"):
        _responder_net("Los comandos con / solo funcionan desde la ventana de SARA. Pídemelo en lenguaje natural.")
        return
    ahora = time.time()
    while _net_cmds and ahora - _net_cmds[0] > 60:
        _net_cmds.popleft()
    if len(_net_cmds) >= int(_c("WA_NET_MAX_COMANDOS_MIN", 4)):
        _responder_net("Demasiadas órdenes seguidas; espera un minuto.")
        return
    if voz:                                                # antes de ejecutar: lo que se entendió (Whisper puede errar)
        orden = orden.rstrip(" .…")                        # Whisper termina las frases con punto
        _responder_net(f"🎙️ Entendí: «{orden[:200]}»")
    try:
        _net_cola.put_nowait((orden, bool(voz)))      # la bandera de voz viaja con la orden
    except queue.Full:
        _responder_net("Sigo ocupada con tus órdenes anteriores; reintenta en un momento.")
        return
    _net_cmds.append(ahora)
    _net_stats["aceptados"] += 1
    if voz:
        _net_stats["voz_aceptadas"] += 1


# ─── NOTAS DE VOZ ─────────────────────────────────────────────────────────

def _ruta_audio_valida(ruta) -> Optional[str]:
    """Solo se abre/borra un archivo que esté DENTRO de wa_sidecar/audio_tmp (nunca una ruta arbitraria)."""
    try:
        if not ruta:
            return None
        base = os.path.realpath(os.path.join(_dir(), "audio_tmp"))
        real = os.path.realpath(str(ruta))
        return real if os.path.commonpath([base, real]) == base and os.path.isfile(real) else None
    except Exception:
        return None


def _borrar_audio(ruta) -> None:
    r = _ruta_audio_valida(ruta)
    if r:
        try:
            os.remove(r)
        except OSError:
            pass


def _net_atender_audio(m: dict) -> None:
    ruta, err = m.get("audio"), m.get("audio_error")
    if not _voz_on():
        _borrar_audio(ruta)
        return
    if err == "larga":
        _net_stats["voz_errores"] += 1
        _responder_net(f"🎙️ Tu nota de voz es demasiado larga (máximo {_voz_max_s()} s); no la procesé.")
        return
    if err:
        _net_stats["voz_errores"] += 1
        _responder_net("🎙️ No pude descargar tu nota de voz. Si se repite, enlaza WhatsApp de nuevo (/whatsapp desconectar).")
        return
    if not _ruta_audio_valida(ruta):
        return
    try:
        _net_cola_voz.put_nowait(m)
    except queue.Full:
        _borrar_audio(ruta)
        _responder_net("🎙️ Sigo transcribiendo notas anteriores; reenvía la tuya en un momento.")


def _net_procesar_nota_interna(m: dict) -> None:
    global _net_ultima_voz
    st = _modulo_stt()
    if st is None:
        _net_stats["voz_errores"] += 1
        _responder_net("🎙️ No puedo transcribir: falta stt.py (o su versión nueva) o faster-whisper.")
        return
    ruta = _ruta_audio_valida(m.get("audio"))
    if not ruta:
        return
    texto = st.transcribir_archivo(ruta, max_s=_voz_max_s() + 5)
    if texto is None:
        _net_stats["voz_errores"] += 1
        _responder_net("🎙️ No pude transcribir tu nota de voz (revisa /stt y el log).")
        return
    texto = str(texto).strip()
    _net_ultima_voz = texto
    if not texto:
        _responder_net("🎙️ No entendí nada en tu nota de voz.")
        return
    cand = _extraer_orden(texto, voz=True)
    if _net_conf is not None and time.time() <= _net_conf["expira"]:
        # Hay una confirmación pendiente: por voz SOLO se acepta cancelar (un «sí» mal oído no debe confirmar nada).
        p = re.sub(r"[^a-z0-9 ]", "", _plano(cand if cand is not None else texto)).strip()
        if p in _NO:
            _intentar_confirmacion(p)
        else:
            _responder_net("Hay una confirmación pendiente: respóndela por texto («sí», «no» o el número). Por voz solo acepto cancelar.")
        return
    if cand is None:
        return _descartar("nota de voz sin palabra de activación", "sin_activacion")
    _net_encolar(cand, m.get("ts"), voz=True)


def _net_procesar_nota(m: dict) -> None:
    _gui_inicio("🎙️ nota de voz")
    try:
        _net_procesar_nota_interna(m)
    finally:
        _gui_fin(True)


def _net_voz_trabajador() -> None:
    """Hilo propio: transcribir tarda segundos y no debe frenar el sondeo (ahí se leen tus confirmaciones)."""
    while True:
        m = _net_cola_voz.get()
        try:
            _net_procesar_nota(m)
        except Exception as e:
            _log_error("_net_procesar_nota", e)
            _responder_net("🎙️ Error interno al procesar tu nota de voz.")
        finally:
            _borrar_audio(m.get("audio"))                  # el audio nunca se queda en disco


def _net_bucle() -> None:
    global _net_cursor, _net_boot, _net_resync
    while True:
        try:
            if not _activo() or not _net_on():
                time.sleep(3)
                continue
            r = _http("GET", "/messages", params={"since": 0 if _net_resync else _net_cursor, "limit": 80,
                                                  "audio": _voz_max_s() if _voz_on() else 0}, timeout=8)
            if not r or r.get("error"):
                time.sleep(5)                              # puente apagado o sin enlace
                continue
            boot, msgs = r.get("boot"), (r.get("mensajes") or [])
            if _net_resync:                                # saltar lo que ya estaba: nunca se re-ejecuta
                _net_resync, _net_boot = False, boot
                _net_cursor = max([int(x.get("id", 0)) for x in msgs] + [0])
                continue
            if _net_boot and boot != _net_boot:            # el puente se reinició: los ids vuelven a empezar
                _net_boot, _net_cursor = boot, 0
                continue
            _net_boot = boot
            for m in msgs:
                _net_cursor = max(_net_cursor, int(m.get("id", 0)))
                try:
                    _net_atender(m)
                except Exception as e:
                    _log_error("_net_atender", e)
            time.sleep(float(_c("WA_NET_POLL_S", 1.0)))
        except Exception as e:
            _log_error("_net_bucle", e)
            time.sleep(5)



# ─── AUREOLA DE LA GUI (pensar = amarillo, hablar = violeta) ─────────────
# server.py llama a registrar_gui(emit_desde_hilo). Si nadie lo llama, todo esto es un no-op.
_gui = None
_gui_n = 0
_gui_lock = threading.Lock()


def registrar_gui(emit) -> None:
    global _gui
    _gui = emit


def _gui_emit(fase: str, **datos) -> None:
    try:
        if _gui is not None:
            _gui("actividad_remota", canal="whatsapp", fase=fase, **datos)
    except Exception as e:
        _log_error("_gui_emit", e)


def _gui_inicio(texto: str = "") -> None:
    global _gui_n
    with _gui_lock:
        _gui_n += 1
    _gui_emit("pensando", texto=str(texto)[:60])


def _gui_fin(ok: bool = True) -> None:
    global _gui_n
    with _gui_lock:
        _gui_n = max(0, _gui_n - 1)
        libre = _gui_n == 0
    if libre:
        _gui_emit("fin", ok=bool(ok))


def _gui_habla(texto: str) -> None:
    """Violeta mientras SARA 'contesta' por este canal (duración estimada por la longitud del texto)."""
    dur = max(2.0, min(14.0, len(str(texto)) / 14.0))
    _gui_emit("hablando", duracion=round(dur, 1))


def _net_ejecutor() -> None:
    """Ejecuta UNA orden a la vez (el núcleo tiene estado global). Aparte del sondeo: mientras espera una
    confirmación, el sondeo sigue leyendo tu respuesta."""
    global _net_conf
    while True:
        orden, voz = _net_cola.get()
        ok = False
        iniciado = False
        try:
            if _nucleo_procesar is None:
                _responder_net("⚠️ El núcleo de SARA todavía no está listo; reintenta en unos segundos.")
                continue
            _gui_inicio(orden)
            iniciado = True
            # Una nota de voz le dice al núcleo que es voz (puede venir mal oída): se limpia como el micrófono.
            r = (_nucleo_procesar(orden, _emit_net, voz=voz) if _nucleo_acepta_voz else _nucleo_procesar(orden, _emit_net)) or {}
            ok = bool(r.get("exito"))
            txt = str(r.get("texto") or "").strip() or ("Listo." if ok else "No pude hacerlo.")
            _net_eventos.append(("notificacion", {"texto": f"📱 WhatsApp → «{orden[:60]}»: {txt[:160]}",
                                                  "nivel": "exito" if ok else "aviso"}))
            if not _c("WA_NET_VOZ", True):       # con bocina, core_bridge ya emite el voz_estado real
                _gui_habla(txt)
            _responder_net(("✅ " if ok else "⚠️ ") + txt)
        except Exception as e:
            ok = False
            _log_error("_net_ejecutor", e)
            _responder_net("⚠️ Error interno al ejecutar tu orden.")
        finally:
            _net_conf = None
            if iniciado:
                _gui_fin(ok)


def _asegurar_hilos_net() -> None:
    global _net_hilos_ok
    with _net_lock:
        if _net_hilos_ok:
            return
        threading.Thread(target=_net_bucle, name="sara-wa-net", daemon=True).start()
        threading.Thread(target=_net_ejecutor, name="sara-wa-net-ejecutor", daemon=True).start()
        threading.Thread(target=_net_voz_trabajador, name="sara-wa-net-voz", daemon=True).start()
        _net_hilos_ok = True


def _net_sincronizar() -> None:
    """Salta lo que ya estaba en el buzón (nunca se re-ejecuta una orden vieja). Es SÍNCRONO a propósito: si lo
    hiciera el hilo de sondeo (que duerme cuando net está apagado), podría tragarse la primera orden que escribas
    justo después de activar. Si el puente no responde, queda pendiente para el primer sondeo exitoso."""
    global _net_cursor, _net_boot, _net_resync
    r = _http("GET", "/messages", params={"since": 0, "limit": 80}, timeout=8)
    if r and not r.get("error"):
        _net_boot = r.get("boot")
        _net_cursor = max([int(x.get("id", 0)) for x in (r.get("mensajes") or [])] + [0])
        _net_resync = False
    else:
        _net_resync = True


def _net_resumen() -> str:
    s = _net_stats
    linea1 = (f"SARA-Net: {'ACTIVO' if _net_on() else 'apagado'} · órdenes aceptadas {s['aceptados']} · "
              f"sin «Sara» {s['sin_activacion']} · ajenos {s['ajenos']} · viejos {s['viejos']} · "
              f"propios no reconocidos {s['propios_no_reconocidos']} · último descarte: {_net_ultimo_descarte}")
    linea2 = (f"Notas de voz: {'ACTIVAS' if _voz_on() else 'apagadas'} (máx. {_voz_max_s()} s) · "
              f"aceptadas {s['voz_aceptadas']} · errores {s['voz_errores']}")
    if _net_ultima_voz:                                    # solo RAM: ayuda a ver si Whisper oyó bien «Sara»
        linea2 += f" · última oída: «{_net_ultima_voz[:80]}»"
    return linea1 + "\n" + linea2


def _precargar_stt() -> None:
    try:
        st = _modulo_stt()
        if st is not None and hasattr(st, "precargar_modelo"):
            st.precargar_modelo()                          # que la primera nota de voz no pague la carga del modelo
    except Exception as e:
        _log_error("_precargar_stt", e)


def _comando_voz(sub: str) -> Tuple[bool, str]:
    if sub in ("on", "off"):
        if sub == "on":
            if _modulo_stt() is None:
                return False, "No puedo activar las notas de voz: falta stt.py (versión con transcribir_archivo) o faster-whisper."
            threading.Thread(target=_precargar_stt, name="sara-wa-net-precarga", daemon=True).start()
        _kv_set("net_voz", "1" if sub == "on" else "0")
        if sub == "off":
            return True, "Notas de voz apagadas: las notas de voz ya no se descargan ni se transcriben."
        aviso = "" if _net_on() else " OJO: SARA-Net está apagado; actívalo con /whatsapp net on."
        return True, (f"Notas de voz activadas (máx. {_voz_max_s()} s). Graba una nota en tu chat contigo mismo que empiece con "
                      "«Sara…»; te contesto primero «🎙️ Entendí: …» y luego ejecuto. Lo que no empiece con «Sara» lo ignoro. "
                      "Si hay una confirmación pendiente, por voz solo acepto cancelar." + aviso)
    return True, _net_resumen() + "\nUso: /whatsapp net voz on|off"


def _comando_net(arg: str) -> Tuple[bool, str]:
    if arg.split()[:1] == ["voz"]:
        return _comando_voz(arg[3:].strip())
    if arg == "aprender":
        r = _http("POST", "/aprender", {"segundos": 90}, timeout=8)
        if not r or r.get("error") or not r.get("ok"):
            return False, "No pude activar el modo aprender. ¿Puente apagado o index.js viejo? Mata node.exe y reinicia SARA."
        return True, ("Modo aprender activo 90 s. En tu celular abre el chat «Tú» (tu chat contigo mismo) y escribe «Sara». "
                      "Ese chat queda registrado como tuyo.")
    if arg in ("on", "off"):
        on = arg == "on"
        if on:
            _net_sincronizar()                             # ANTES de encender: el sondeo no puede adelantarse
        _kv_set("net", "1" if on else "0")
        if on:
            _asegurar_hilos_net()
            bloq = ", ".join(_c("WA_NET_CATEGORIAS_BLOQUEADAS", ("COMANDO_SISTEMA", "ELIMINAR_ARCHIVO"))) or "ninguna"
            return True, ("SARA-Net activado. En tu chat contigo mismo escribe «Sara, <orden>» (ej. «Sara, abre Chrome»); "
                          "lo que no empiece con «Sara» lo ignoro. Las acciones de riesgo te piden confirmación en ese mismo "
                          f"chat. Categorías bloqueadas por este canal: {bloq}. "
                          "Para órdenes por nota de voz: /whatsapp net voz on.")
        return True, "SARA-Net apagado: ya no obedezco órdenes por WhatsApp."
    return True, _net_resumen() + "\nUso: /whatsapp net on|off · /whatsapp net voz on|off"


# ─── API PARA core_bridge ─────────────────────────────────────────────────
def es_comando(texto: str) -> bool:
    """Atajo directo: mandar a un contacto («mándale a Juan que…», «dile a mamá que…», «escríbele a Ana: …») no necesita
    decir WhatsApp; leer y responder sí. Evita que el clasificador o la IA conversacional la confundan con 'abre whatsapp'
    o contesten «no tengo esa habilidad»."""
    if not _activo():
        return False
    try:
        a = _analizar(texto)
        if a is None:
            return False
        if a["accion"] in ("leer", "responder"):         # «ver mensajes» / «responde…» son demasiado comunes sin la palabra
            return bool(_RE_WA.search(_plano(texto)))
        return True                                      # enviar / falta_mensaje: «a <contacto> que <mensaje>» ya es inequívoco
    except Exception as e:
        _log_error("es_comando", e)
        return False
def resolver(res: dict) -> Optional[dict]:
    if not _activo():
        return None
    try:
        p = _analizar(res.get("texto_original") or "")
        if p is None:
            return None
        if estado().get("estado") != "abierto":
            return {"estado": "no_vinculado", "exito": False,
                    "texto": "WhatsApp no está enlazado o conectado ahora. Escribe /whatsapp para ver cómo."}
        if p["accion"] == "falta_mensaje":
            c = p.get("contacto") or "ese contacto"
            return {"estado": "no_entendido", "exito": False,
                    "texto": f"¿Qué le mando a {c}? Dímelo en la misma frase, por ejemplo: «mándale a {c} que ya voy»."}
        if p["accion"] == "leer":
            return {"estado": "ok", "exito": True, "texto": _leer_bandeja(p["contacto"])}
        return _plan_envio(p["contacto"], p["mensaje"])
    except Exception as e:
        _log_error("resolver", e)
        return None


_envios: deque = deque(maxlen=50)


def ejecutar_envio(plan: dict) -> dict:
    """Nunca lanza. Límite de ritmo para no parecer un bot (riesgo de bloqueo de la cuenta)."""
    ahora = time.time()
    if sum(1 for t in _envios if ahora - t < 60) >= int(_c("WA_MAX_ENVIOS_MIN", 8)):
        return {"exito": False, "texto": "Demasiados envíos seguidos; espera un minuto."}
    r = _http("POST", "/send", {"to": plan["jid"], "text": plan["mensaje"]}, timeout=25)
    if r and r.get("ok"):
        _envios.append(ahora)
        return {"exito": True, "texto": f"Enviado a {plan.get('nombre', 'el contacto')}."}
    return {"exito": False, "texto": f"No pude enviarlo: {(r or {}).get('error', 'el puente no responde')}."}


def notificar_usuario(texto: str) -> bool:
    """SARA → tu propio chat de WhatsApp («Tú»). Silencioso si no está enlazado."""
    if not _activo() or not texto or not _kv_get("numero"):
        return False
    try:
        if estado().get("estado") != "abierto":
            return False
        return bool(ejecutar_envio({"jid": "yo", "nombre": "ti", "mensaje": str(texto)[:1000]}).get("exito"))
    except Exception as e:
        _log_error("notificar_usuario", e)
        return False


def eventos_proactivos() -> List[Tuple[str, dict]]:
    """Cada ~20 s (lo llama core_bridge). Enlace completado + mensajes entrantes."""
    global _esperando_enlace, _cursor_aviso, _ultimo_remitente
    out: List[Tuple[str, dict]] = []
    while _net_eventos:                                    # avisos de órdenes recibidas por SARA-Net
        out.append(_net_eventos.popleft())
    if _activo() and _kv_get("numero"):
        _asegurar_hilos_net()
    if not _activo() or _proc is None and not _kv_get("numero"):
        return out
    st = estado()
    if st.get("estado") != "abierto":
        return out
    if _esperando_enlace:
        _esperando_enlace = False
        out.append(("notificacion", {"texto": "✅ WhatsApp enlazado. Escribe /whatsapp para ver qué puedo hacer.",
                                     "nivel": "exito", "voz": "WhatsApp enlazado."}))
        notificar_usuario("✅ SARA enlazada a tu WhatsApp.")
    msgs = _traer(_cursor_aviso)
    if msgs:
        _cursor_aviso = max(m["id"] for m in msgs)
    if _kv_get("notificar", "1") != "1":
        for m in _validos(msgs):
            _ultimo_remitente = (m["jid"], m["nombre"])
        return out
    for m in _validos(msgs)[-5:]:
        _ultimo_remitente = (m["jid"], m["nombre"])
        voz = (f"Mensaje de {m['nombre']}: {m['texto'][:120]}" if _c("WA_LEER_CONTENIDO_EN_VOZ", False)
               else f"Tienes un mensaje de WhatsApp de {m['nombre']}.")
        out.append(("notificacion", {"texto": f"💬 WhatsApp · {m['nombre']}: {m['texto'][:200]}",
                                     "nivel": "aviso", "voz": voz}))
    return out


if __name__ == "__main__":
    print(diagnostico())
