# ⏻ apagado.py — Apagar, reiniciar, suspender, hibernar y bloquear el equipo en lenguaje natural (v2)
#
# Qué hace: entiende «apaga la computadora», «apaga el equipo en 5 minutos», «apágame la laptop» y
# «cancela el apagado», y programa el apagado de Windows con CUENTA ATRÁS (shutdown /s /t N), de modo que
# siempre hay un margen para arrepentirse. Este módulo NO pide permiso: quien lo llama sí lo hace.
#   · Desde la ventana de SARA: core_bridge pide la confirmación en el modal (con la advertencia de abajo).
#   · Desde Telegram: telegram_bot pide confirmación con botones + un código de 5 dígitos que llega por WhatsApp.
#   · Desde WhatsApp (SARA-Net): no se permite (core_bridge lo rechaza). Solo «cancelar» vale por cualquier canal.
#
# Contrato (igual que volumen.py; nada aquí lanza hacia fuera):
#   es_comando(texto)  -> bool          ¿es una orden de apagar o de cancelar el apagado? (barata, sin efectos)
#   interpretar(texto) -> dict | None   {"accion": "apagar", "demora_s": int|None} | {"accion": "cancelar"}
#   normalizar_demora(s, remoto)  -> int   segundos reales (con mínimos y máximos de config)
#   advertencia(demora_s, remoto) -> str   el texto de aviso que se le muestra al usuario antes de confirmar
#   programar(demora_s, origen)   -> (bool, str)   ejecuta «shutdown /s /t N»
#   cancelar()                    -> (bool, str)   ejecuta «shutdown /a»
#   programado()                  -> int | None    segundos que faltan si SARA programó un apagado
#   diagnostico()                 -> str
#
# v2 — más acciones (todas con cuenta atrás cancelable, salvo bloquear):
#   interpretar → {"accion": "apagar"|"reiniciar"|"suspender"|"hibernar"|"bloquear", "demora_s": int|None}
#   programar(demora_s, origen, accion="apagar") · advertencia(demora_s, remoto, modo, accion="apagar")
#   requiere_codigo(accion) → bool   ¿por Telegram hace falta el código del 2º factor? (APAGADO_CODIGO_PARA)
#   etiqueta(accion) → "apagar el equipo" · etiqueta_boton(accion) → "✔ Sí, apagar"
#   Energía de la laptop (modo mochila): python apagado.py energia mochila | restaurar | estado
#     → energia_mochila() / energia_restaurar() / energia_estado()   (powercfg; ver más abajo)
#   modo_codigo() -> "totp" | "whatsapp"   cómo se comprueba el segundo factor del apagado REMOTO (ver abajo)
#   totp_configurado() / totp_crear() / totp_uri(secreto) / totp_verificar(codigo)
#
# Segundo factor del apagado remoto (APAGADO_CODIGO_MODO en config.py):
#   "whatsapp" → SARA manda un código de 5 dígitos a tu WhatsApp «Tú».
#   "totp"     → NO se manda nada: el código de 6 dígitos lo muestra una app autenticadora de tu celular
#                (Google Authenticator, Microsoft Authenticator, Authy…). Funciona sin WhatsApp y sin internet en el PC.
#                Se configura UNA vez en el PC:  python apagado.py totp
#   "auto"     → totp si ya lo configuraste; si no, whatsapp.
# Por defecto es "totp": el envío del código por WhatsApp queda APAGADO (solo se usa si pones "whatsapp").
#
# Parámetros (config.py; todos tienen valor por defecto aquí):
#   APAGADO_ACTIVO = True · APAGADO_DEMORA_S = 30 · APAGADO_DEMORA_MIN_S = 10 · APAGADO_DEMORA_MAX_S = 86400
#   APAGADO_REMOTO_DEMORA_MIN_S = 30   (por un canal remoto nunca menos: hay que poder cancelar)
#
# Solo Windows. En otros sistemas responde que no está disponible (y las pruebas inyectan un ejecutor falso).

import base64
import hashlib
import hmac as _hmac
import logging
import os
import re
import secrets
import struct
import subprocess
import sys
import threading
import time
import unicodedata
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("SARA-APAGADO")

try:
    import config
except Exception:                                   # el módulo sigue funcionando con sus valores por defecto
    config = None

APAGADO_VERSION = "v2"


def _cfg(nombre: str, defecto):
    try:
        return getattr(config, nombre, defecto)
    except Exception:
        return defecto


# ─── NORMALIZACIÓN ────────────────────────────────────────────────────────

_NUMEROS_PALABRA = (                                # los más largos primero
    ("cuarenta y cinco", "45"), ("veinticinco", "25"), ("cincuenta", "50"), ("sesenta", "60"), ("treinta", "30"),
    ("cuarenta", "40"), ("veinte", "20"), ("quince", "15"), ("diez", "10"), ("nueve", "9"), ("ocho", "8"),
    ("siete", "7"), ("seis", "6"), ("cinco", "5"), ("cuatro", "4"), ("tres", "3"), ("dos", "2"),
    ("una", "1"), ("un", "1"),
)


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"(?<![a-z0-9])media hora(?![a-z0-9])", "30 minutos", t)
    t = re.sub(r"(?<![a-z0-9])medio minuto(?![a-z0-9])", "30 segundos", t)
    for palabra, cifra in _NUMEROS_PALABRA:
        t = re.sub(rf"(?<![a-z0-9]){palabra}(?![a-z0-9])", cifra, t)
    return t


# ─── SEÑALES DEL LENGUAJE ─────────────────────────────────────────────────
# Estrictas a propósito: apagar el equipo es lo más caro de equivocar. La frase debe EMPEZAR (tras cortesías) con el
# verbo y nombrar el equipo; «apaga la música», «apaga chrome», «cómo apago la compu» o «no apagues la compu» NO valen.

_CORT = (r"^(?:(?:oye|hey|hola|ok|okay|ey)\s+)?(?:sara\s+)?"
         r"(?:(?:por favor|porfa|porfavor|ya|ahora|entonces|pues|a ver|mira|oye|eh)\s+)*"
         r"(?:(?:puedes|podrias|me puedes|me podrias|quiero que|necesito que|quiero|necesito|vamos a|hay que|"
         r"te pido que|ayudame a)\s+)?"
         r"(?:(?:por favor|porfa|ya|ahora)\s+)*")
_ART = r"(?:(?:la|mi|esta|el|este|toda la|todo el|tu)\s+)?"
_OBJ = r"(?:computadora|computador|compu|pc|laptop|lap|portatil|notebook|ordenador|equipo|maquina|sistema|windows)"
_VERBO_APAGAR = r"(?:apaga|apagar|apagame|apagues|apague|apagalo|apagala)"
_COLA = r"(?:\s+(?:por favor|porfa|porfavor|ya|ahora|ahorita|de una vez|ya mismo|ahora mismo|sara|gracias))*"
_TIEMPO = (r"(?:\s+(?:en|dentro de|despues de|en unos|en como)\s+(?P<n>[0-9]{1,5})\s*"
           r"(?P<u>segundos?|segs?|s|minutos?|mins?|horas?|hrs?|h))?")

_RE_APAGAR = re.compile(_CORT + _VERBO_APAGAR + r"\s+" + _ART + _OBJ + _COLA + _TIEMPO + _COLA + r"\s*$")

_VERBO_REINICIAR = r"(?:reinicia|reiniciar|reiniciame|reinicies|reiniciala|reinicialo)"
_VERBO_SUSPENDER = r"(?:suspende|suspender|suspendeme|suspendela|suspendelo|duerme|dormir|duermete|duermela)"
_VERBO_HIBERNAR = r"(?:hiberna|hibernar|hibernala|hibernalo|hibernate)"
_VERBO_PONER = r"(?:pon|poner|ponme|ponla|ponlo)"
_VERBO_BLOQUEAR = r"(?:bloquea|bloquear|bloqueame|bloqueala)"
_REG = _COLA + _TIEMPO + _COLA + r"\s*$"
_RE_REINICIAR = re.compile(_CORT + _VERBO_REINICIAR + r"\s+" + _ART + _OBJ + _REG)
_RE_SUSPENDER = re.compile(
    _CORT + _VERBO_SUSPENDER + r"\s+" + _ART + _OBJ + _REG + r"|"
    + _CORT + _VERBO_PONER + r"\s+" + _ART + _OBJ + r"\s+(?:en|a)\s+(?:modo\s+)?"
    r"(?:suspension|reposo|descanso|dormir|sueno)" + _REG.replace("(?P<n>", "(?P<n2>").replace("(?P<u>", "(?P<u2>"))
_RE_HIBERNAR = re.compile(
    _CORT + _VERBO_HIBERNAR + r"\s+" + _ART + _OBJ + _REG + r"|"
    + _CORT + _VERBO_PONER + r"\s+" + _ART + _OBJ + r"\s+(?:en|a)\s+(?:modo\s+)?"
    r"(?:hibernacion|hibernar)" + _REG.replace("(?P<n>", "(?P<n2>").replace("(?P<u>", "(?P<u2>"))
_RE_BLOQUEAR = re.compile(_CORT + _VERBO_BLOQUEAR + r"\s+" + _ART + r"(?:" + _OBJ + r"|pantalla|sesion)" + _COLA + r"\s*$")

_V_CANCELAR = r"(?:cancela|cancelar|cancelalo|cancelame|deten|detener|detenlo|aborta|abortar|abortalo|parale|frena)"
_RE_CANCELAR = re.compile(
    _CORT + _V_CANCELAR + r"\s+(?:(?:el|la|ese|este|mi|tu)\s+)?(?:apagado|shutdown|reinicio|suspension|hibernacion)"
    r"(?:\s+(?:de|del)\s+(?:la\s+|el\s+|mi\s+)?" + _OBJ + r")?" + _COLA + r"\s*$"
    r"|" + _CORT + r"(?:ya\s+)?no\s+(?:vayas a\s+)?(?:apagues|apagar)\s+" + _ART + _OBJ + _COLA + r"\s*$")

_MULT = {"s": 1, "m": 60, "h": 3600}


def interpretar(texto: str) -> Optional[Dict]:
    """Frase → {"accion": "apagar", "demora_s": int|None} | {"accion": "cancelar"} | None. Determinista, sin efectos."""
    try:
        n = _norm(texto)
        if not n or len(n.split()) > 16:
            return None
        if _RE_CANCELAR.match(n):
            return {"accion": "cancelar"}
        m = _RE_BLOQUEAR.match(n)
        if m:
            return {"accion": "bloquear", "demora_s": None}
        for accion, rx in (("apagar", _RE_APAGAR), ("reiniciar", _RE_REINICIAR),
                           ("suspender", _RE_SUSPENDER), ("hibernar", _RE_HIBERNAR)):
            m = rx.match(n)
            if not m:
                continue
            g = m.groupdict()
            num = g.get("n") or g.get("n2")
            uni = g.get("u") or g.get("u2")
            demora = int(num) * _MULT[uni[0]] if num and uni else None
            return {"accion": accion, "demora_s": demora}
        return None
    except Exception:
        logger.exception("⚠️ apagado.interpretar falló")
        return None


def es_comando(texto: str) -> bool:
    return interpretar(texto) is not None


# ─── TEXTOS ───────────────────────────────────────────────────────────────

def duracion_texto(segundos: int) -> str:
    s = max(0, int(segundos))
    if s < 60:
        return f"{s} segundo{'s' if s != 1 else ''}"
    if s < 3600:
        m, r = divmod(s, 60)
        txt = f"{m} minuto{'s' if m != 1 else ''}"
        return txt + (f" {r} s" if r else "")
    h, r = divmod(s, 3600)
    m = r // 60
    txt = f"{h} hora{'s' if h != 1 else ''}"
    return txt + (f" {m} min" if m else "")


def normalizar_demora(demora_s: Optional[int], remoto: bool = False) -> int:
    """Segundos reales de la cuenta atrás. Por un canal remoto nunca menos de APAGADO_REMOTO_DEMORA_MIN_S."""
    try:
        d = int(demora_s) if demora_s is not None else int(_cfg("APAGADO_DEMORA_S", 30))
    except (TypeError, ValueError):
        d = 30
    minimo = int(_cfg("APAGADO_DEMORA_MIN_S", 10))
    if remoto:
        minimo = max(minimo, int(_cfg("APAGADO_REMOTO_DEMORA_MIN_S", 30)))
    maximo = max(minimo, int(_cfg("APAGADO_DEMORA_MAX_S", 86400)))
    return max(minimo, min(maximo, d))


ACCIONES = ("apagar", "reiniciar", "suspender", "hibernar", "bloquear")
_ETQ = {"apagar": "apagar el equipo", "reiniciar": "reiniciar el equipo", "suspender": "suspender el equipo",
        "hibernar": "hibernar el equipo", "bloquear": "bloquear la sesión"}
_BTN = {"apagar": "apagar", "reiniciar": "reiniciar", "suspender": "suspender", "hibernar": "hibernar", "bloquear": "bloquear"}


def etiqueta(accion: str) -> str:
    return _ETQ.get(accion, "apagar el equipo")


def etiqueta_boton(accion: str) -> str:
    return "✔ Sí, " + _BTN.get(accion, "apagar")


def requiere_codigo(accion: str) -> bool:
    """¿Por un canal remoto hace falta el código del 2º factor? Por defecto solo apagar y reiniciar (pierden lo no
    guardado); suspender, hibernar y bloquear piden solo el botón de confirmación."""
    try:
        lista = _cfg("APAGADO_CODIGO_PARA", ("apagar", "reiniciar"))
        if isinstance(lista, str):
            lista = [x.strip() for x in lista.split(",")]
        return accion in {str(x).strip().lower() for x in lista}
    except Exception:
        return True


def advertencia(demora_s: int, remoto: bool = False, modo: str = "whatsapp", accion: str = "apagar") -> str:
    dur = duracion_texto(demora_s)
    if accion == "bloquear":
        return ("⚠️ BLOQUEAR LA SESIÓN\nSi confirmas, Windows se bloquea ahora; SARA sigue funcionando.\n¿Confirmas?" if remoto
                else "¿Bloqueo la sesión de Windows ahora? SARA seguirá funcionando.")
    if accion in ("suspender", "hibernar"):
        verbo = "suspenderá" if accion == "suspender" else "hibernará"
        efecto = ("Las aplicaciones se quedan abiertas." if accion == "suspender"
                  else "Se guarda todo en disco y el equipo se apaga; al encenderlo vuelves a donde estabas.")
        if not remoto:
            return (f"¿Seguro que quieres {etiqueta(accion)}? Se {verbo} en {dur}. {efecto} Mientras esté así SARA no "
                    f"responderá. Podrás detenerlo diciendo «cancela la {'suspensión' if accion == 'suspender' else 'hibernación'}».")
        base = (f"⚠️ {etiqueta(accion).upper()} REMOTO\nSi confirmas, el equipo se {verbo} en {dur}. {efecto}\n"
                f"🚫 Desde ese momento NO podré responderte ni tú controlarme: el celular no puede despertar la laptop. "
                f"Tendrás que abrir la tapa o pulsar el botón de encendido.")
        if requiere_codigo(accion):
            base += ("\nPara ejecutarlo escribe aquí el código de 6 dígitos de tu app autenticadora." if modo == "totp"
                     else "\nPara ejecutarlo te mandaré un código a tu WhatsApp.")
        return base + "\n¿Confirmas?"
    verbo_c = "apagar" if accion == "apagar" else "reiniciar"
    futuro = "apagará" if accion == "apagar" else "reiniciará"
    extra = ("" if accion == "apagar" else
             "\nSARA solo volverá a estar disponible si Windows inicia sesión solo; si te pide contraseña, no podrás "
             "controlarla desde el celular hasta entrar.")
    if remoto and modo == "totp":
        return (f"⚠️ {verbo_c.upper()} EL EQUIPO (REMOTO)\nSi confirmas, escribe aquí el código de 6 dígitos que muestra tu app "
                f"autenticadora (SARA) y el equipo se {futuro} {dur} después. Se cerrarán TODAS las aplicaciones y se "
                f"perderá lo que no esté guardado.{extra}\n¿Confirmas?")
    if remoto:
        return (f"⚠️ {verbo_c.upper()} EL EQUIPO (REMOTO)\nSi confirmas, te mando un código de 5 dígitos a tu WhatsApp y, al "
                f"escribirlo aquí, el equipo se {futuro} {dur} después. Se cerrarán TODAS las aplicaciones y se perderá lo que no "
                f"esté guardado.{extra}\n¿Confirmas?")
    return (f"¿Seguro que quieres {verbo_c} el equipo? Se {futuro} en {dur} y se cerrarán todas las aplicaciones: "
            f"guarda tu trabajo. Podrás detenerlo diciendo «cancela el apagado».")


# ─── EJECUCIÓN ────────────────────────────────────────────────────────────

_lock = threading.Lock()
_ultimo: Optional[dict] = None          # {"hasta": epoch, "origen": str} del último apagado que programó SARA
_ultimo_error = ""
_ejecutor_externo: Optional[Callable[[List[str]], Tuple[int, str]]] = None   # solo pruebas

_RC_YA_PROGRAMADO = 1190                # ERROR_SHUTDOWN_IS_SCHEDULED
_RC_NADA_QUE_CANCELAR = 1116            # ERROR_NO_SHUTDOWN_IN_PROGRESS


def usar_ejecutor(fn: Optional[Callable[[List[str]], Tuple[int, str]]]) -> None:
    """Para pruebas: fn(args_de_shutdown) -> (código_de_salida, salida). None = el real."""
    global _ejecutor_externo
    _ejecutor_externo = fn


def _exe() -> str:
    raiz = os.environ.get("SystemRoot") or "C:\\Windows"
    ruta = os.path.join(raiz, "System32", "shutdown.exe")
    return ruta if os.path.exists(ruta) else "shutdown"


def _correr(args: List[str]) -> Tuple[int, str]:
    """shutdown.exe con lista de argumentos (sin shell: nada de lo que dijo el usuario llega a un intérprete)."""
    if _ejecutor_externo is not None:
        return _ejecutor_externo(list(args))
    if os.name != "nt":
        raise RuntimeError("el apagado solo funciona en Windows")
    banderas = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.run([_exe(), *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       errors="replace", timeout=15, creationflags=banderas)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def _correr_energia(accion: str) -> Tuple[int, str]:
    """Suspender / hibernar / bloquear llamando a la API de Windows (sin shell ni comandos de texto)."""
    if _ejecutor_externo is not None:
        return _ejecutor_externo(["__" + accion])
    if os.name != "nt":
        raise RuntimeError("el apagado solo funciona en Windows")
    import ctypes
    if accion == "hibernar":
        return _correr(["/h"])
    if accion == "bloquear":
        ok = ctypes.windll.user32.LockWorkStation()
        return (0 if ok else 1), ""
    if accion == "suspender":                       # SetSuspendState(hibernar=0, forzar=0, sin_despertar=0): suspende de verdad
        ok = ctypes.windll.powrprof.SetSuspendState(0, 0, 0)
        return (0 if ok else 1), ""
    raise ValueError(accion)


def _disparar_diferido(accion: str, marca: float) -> None:
    """Hilo del temporizador (suspender / hibernar): si nadie lo canceló, ejecuta."""
    global _ultimo, _ultimo_error
    with _lock:
        u = _ultimo
        if not u or u.get("marca") != marca:
            return                                    # cancelado o reemplazado
        _ultimo = None
    try:
        rc, salida = _correr_energia(accion)
        if rc != 0:
            _ultimo_error = f"{accion} devolvió {rc}: {salida[:150]}"
            logger.error(f"❌ {_ultimo_error}")
    except Exception as e:
        _ultimo_error = f"{type(e).__name__}: {e}"
        logger.exception(f"❌ apagado: {accion} falló")


_MSG_OK = {"apagar": "Apagando el equipo en {d}. Escribe o di «cancela el apagado» para detenerlo.",
           "reiniciar": "Reiniciando el equipo en {d}. Escribe o di «cancela el apagado» para detenerlo.",
           "suspender": "Suspendiendo el equipo en {d}. Di «cancela la suspensión» para detenerlo.",
           "hibernar": "Hibernando el equipo en {d}. Di «cancela la hibernación» para detenerlo."}


def programar(demora_s: Optional[int] = None, origen: str = "texto", accion: str = "apagar") -> Tuple[bool, str]:
    """Programa apagar / reiniciar / suspender / hibernar con cuenta atrás (bloquear es inmediato). NO pregunta: la
    confirmación es de quien llama. Nunca lanza."""
    global _ultimo, _ultimo_error
    try:
        if not _cfg("APAGADO_ACTIVO", True):
            return False, "El control de energía del equipo está desactivado (APAGADO_ACTIVO en config.py)."
        if accion not in ACCIONES:
            return False, "No sé hacer eso con el equipo."
        por = {"telegram": "Telegram", "whatsapp": "WhatsApp", "celular": "el celular"}.get(origen, "la ventana de SARA")
        if accion == "bloquear":
            with _lock:
                rc, salida = _correr_energia("bloquear")
            if rc == 0:
                logger.warning(f"🔒 Sesión bloqueada (origen: {origen})")
                return True, "Sesión bloqueada."
            _ultimo_error = f"bloquear devolvió {rc}: {salida[:150]}"
            return False, "No pude bloquear la sesión (revisa el log)."
        d = normalizar_demora(demora_s, remoto=origen in ("telegram", "whatsapp", "celular"))
        if accion in ("suspender", "hibernar"):
            with _lock:
                if _ultimo and _ultimo.get("hasta", 0) > time.time():
                    return False, "Ya hay una acción de energía programada. Di «cancela el apagado» si quieres detenerla."
                marca = time.time()
                t = threading.Timer(d, _disparar_diferido, args=(accion, marca))
                t.daemon = True
                _ultimo = {"hasta": marca + d, "origen": origen, "accion": accion, "marca": marca, "timer": t}
                t.start()
            logger.warning(f"⏻ {accion.capitalize()} programada en {d} s (origen: {origen})")
            return True, _MSG_OK[accion].format(d=duracion_texto(d))
        bandera = "/s" if accion == "apagar" else "/r"
        comentario = (f"SARA: {accion} pedido desde {por}. Ocurrira en {duracion_texto(d)}. "
                      f"Para detenerlo: dile a SARA 'cancela el apagado' o ejecuta shutdown /a")
        with _lock:
            rc, salida = _correr([bandera, "/t", str(d), "/c", comentario[:500]])
            if rc == 0:
                _ultimo = {"hasta": time.time() + d, "origen": origen, "accion": accion}
                _ultimo_error = ""
                logger.warning(f"⏻ {accion.capitalize()} programado en {d} s (origen: {origen})")
                return True, _MSG_OK[accion].format(d=duracion_texto(d))
        if rc == _RC_YA_PROGRAMADO:
            return False, "Ya hay un apagado programado. Di «cancela el apagado» si quieres detenerlo."
        _ultimo_error = f"shutdown.exe devolvió {rc}: {salida[:150]}"
        logger.error(f"❌ {_ultimo_error}")
        return False, "No pude programarlo (revisa el log)."
    except Exception as e:
        _ultimo_error = f"{type(e).__name__}: {e}"
        if isinstance(e, RuntimeError) and "solo funciona en Windows" in str(e):
            return False, "El control de energía del equipo solo funciona en Windows."
        logger.exception("❌ apagado.programar falló")
        return False, f"No pude programarlo ({type(e).__name__})."


def cancelar() -> Tuple[bool, str]:
    """Cancela la acción de energía en curso: el temporizador de suspender/hibernar y/o la cuenta atrás de Windows
    (shutdown /a), la haya programado SARA u otra cosa. Nunca lanza."""
    global _ultimo, _ultimo_error
    try:
        with _lock:
            u = _ultimo
            t = u.get("timer") if u else None
            if t is not None:
                t.cancel()
                _ultimo = None
                logger.warning(f"⏻ {u.get('accion', 'acción')} cancelada")
                return True, "Cancelado. El equipo sigue encendido."
            rc, salida = _correr(["/a"])
            if rc == 0:
                _ultimo = None
                logger.warning("⏻ Apagado cancelado")
                return True, "Apagado cancelado. El equipo sigue encendido."
        if rc == _RC_NADA_QUE_CANCELAR:
            _ultimo = None
            return False, "No hay ningún apagado programado."
        _ultimo_error = f"shutdown.exe /a devolvió {rc}: {salida[:150]}"
        logger.error(f"❌ {_ultimo_error}")
        return False, "No pude cancelar el apagado (revisa el log). Si sigue la cuenta atrás, escribe shutdown /a en una consola."
    except Exception as e:
        _ultimo_error = f"{type(e).__name__}: {e}"
        if isinstance(e, RuntimeError) and "solo funciona en Windows" in str(e):
            return False, "El apagado del equipo solo funciona en Windows."
        logger.exception("❌ apagado.cancelar falló")
        return False, f"No pude cancelar el apagado ({type(e).__name__})."


def programado() -> Optional[int]:
    """Segundos que faltan para el apagado que programó SARA (None si no hay)."""
    u = _ultimo
    if not u:
        return None
    resto = int(u["hasta"] - time.time())
    return resto if resto > 0 else None


# ─── SEGUNDO FACTOR: TOTP (RFC 6238, sin dependencias) ────────────────────

_TOTP_ARCHIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apagado_totp.key")
_totp_lock = threading.Lock()
_totp_ultimo = 0                        # último intervalo de 30 s aceptado: un código no se puede usar dos veces


def _totp_leer() -> Optional[str]:
    try:
        with open(_TOTP_ARCHIVO, encoding="ascii") as f:
            sec = f.read().strip().upper()
        base64.b32decode(sec + "=" * (-len(sec) % 8))
        return sec if len(sec) >= 16 else None
    except Exception:
        return None


def totp_configurado() -> bool:
    return _totp_leer() is not None


def totp_crear() -> str:
    """Crea el secreto (160 bits) y lo guarda en apagado_totp.key. Devuelve el secreto en base32."""
    sec = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
    with open(_TOTP_ARCHIVO, "w", encoding="ascii") as f:
        f.write(sec + "\n")
    try:
        os.chmod(_TOTP_ARCHIVO, 0o600)
    except OSError:
        pass
    return sec


def totp_uri(secreto: str) -> str:
    return f"otpauth://totp/SARA:apagado?secret={secreto}&issuer=SARA&digits=6&period=30"


def _hotp(secreto: str, contador: int) -> str:
    clave = base64.b32decode(secreto + "=" * (-len(secreto) % 8))
    h = _hmac.new(clave, struct.pack(">Q", contador), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000:06d}"


def totp_verificar(codigo: str, ahora: Optional[float] = None) -> bool:
    """¿Es el código de 6 dígitos vigente (±30 s de reloj) y no usado antes? Nunca lanza."""
    global _totp_ultimo
    try:
        sec = _totp_leer()
        cod = re.sub(r"\s+", "", str(codigo or ""))
        if not sec or not re.fullmatch(r"[0-9]{6}", cod):
            return False
        c0 = int((time.time() if ahora is None else ahora) // 30)
        with _totp_lock:
            for c in (c0 - 1, c0, c0 + 1):
                if c > _totp_ultimo and _hmac.compare_digest(_hotp(sec, c), cod):
                    _totp_ultimo = c
                    return True
        return False
    except Exception:
        logger.exception("⚠️ apagado.totp_verificar falló")
        return False


def modo_codigo() -> str:
    m = str(_cfg("APAGADO_CODIGO_MODO", "totp")).strip().lower()
    if m in ("totp", "whatsapp"):
        return m
    if m == "auto":
        return "totp" if totp_configurado() else "whatsapp"
    return "totp"                                           # valor raro → el modo seguro y sin envíos


# ─── ENERGÍA DE LA LAPTOP: «modo mochila» (powercfg) ──────────────────────
# Para llevar la laptop cerrada en la mochila y controlarla desde el celular: que no se suspenda al cerrar la tapa ni por
# inactividad, y que gaste poco (pantalla apagada, CPU limitada y sin turbo SOLO con batería, Wi‑Fi sin ahorro para no
# perder la conexión). Antes de cambiar nada guarda tus valores en apagado_energia_backup.json; «restaurar» los devuelve.
# Se ejecuta UNA vez en el PC:  python apagado.py energia mochila   (no requiere administrador).

_ENERGIA_BACKUP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apagado_energia_backup.json")
_WIFI_SUB, _WIFI_SET = "19cbb8fa-5279-450e-9fac-8a3d5fedd0c1", "12bbebe6-58d6-4636-95bb-3217ef867c1a"
# (clave, subgrupo, ajuste, valor con corriente, valor con batería, descripción)   None = no tocar
_AJUSTES = (
    ("tapa", "SUB_BUTTONS", "LIDACTION", 0, 0, "Al cerrar la tapa: no hacer nada"),
    ("suspender", "SUB_SLEEP", "STANDBYIDLE", 0, 0, "Suspender por inactividad: nunca"),
    ("hibernar", "SUB_SLEEP", "HIBERNATEIDLE", 0, 0, "Hibernar por inactividad: nunca"),
    ("pantalla", "SUB_VIDEO", "VIDEOIDLE", None, 60, "Apagar la pantalla: 1 min con batería"),
    ("cpu_max", "SUB_PROCESSOR", "PROCTHROTTLEMAX", None, 50, "CPU máxima con batería: 50 %"),
    ("turbo", "SUB_PROCESSOR", "PERFBOOSTMODE", None, 0, "Turbo de la CPU con batería: desactivado"),
    ("wifi", _WIFI_SUB, _WIFI_SET, 0, 0, "Wi‑Fi: máximo rendimiento (sin ahorro que corte la conexión)"),
)
_ejecutor_powercfg: Optional[Callable[[List[str]], Tuple[int, str]]] = None      # solo pruebas


def usar_powercfg(fn: Optional[Callable[[List[str]], Tuple[int, str]]]) -> None:
    global _ejecutor_powercfg
    _ejecutor_powercfg = fn


def _powercfg(args: List[str]) -> Tuple[int, str]:
    if _ejecutor_powercfg is not None:
        return _ejecutor_powercfg(list(args))
    if os.name != "nt":
        raise RuntimeError("la energía solo se configura en Windows")
    raiz = os.environ.get("SystemRoot") or "C:\\Windows"
    exe = os.path.join(raiz, "System32", "powercfg.exe")
    p = subprocess.run([exe if os.path.exists(exe) else "powercfg", *args], stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, errors="replace", timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def _leer_ajuste(sub: str, aj: str) -> Optional[Tuple[int, int]]:
    """(valor_con_corriente, valor_con_batería) del plan activo. Independiente del idioma de Windows: toma los dos
    últimos números 0x… de la salida de «powercfg /q» (primero CA, luego CC)."""
    rc, out = _powercfg(["/q", "SCHEME_CURRENT", sub, aj])
    if rc != 0:
        return None
    nums = re.findall(r"0x([0-9a-fA-F]{8})", out)
    if len(nums) < 2:
        return None
    return int(nums[-2], 16), int(nums[-1], 16)


def _poner_ajuste(sub: str, aj: str, ac: Optional[int], dc: Optional[int]) -> bool:
    ok = True
    for bandera, v in (("/setacvalueindex", ac), ("/setdcvalueindex", dc)):
        if v is None:
            continue
        rc, _ = _powercfg([bandera, "SCHEME_CURRENT", sub, aj, str(int(v))])
        ok = ok and rc == 0
    return ok


def energia_estado() -> List[str]:
    out = []
    for clave, sub, aj, _ac, _dc, texto in _AJUSTES:
        v = _leer_ajuste(sub, aj)
        out.append(f"  {texto}\n      ahora → corriente: {v[0] if v else '?'} · batería: {v[1] if v else '?'}"
                   + ("" if v else "  (este equipo no tiene ese ajuste)"))
    return out


def energia_mochila() -> Tuple[bool, List[str]]:
    """Aplica el modo mochila. Guarda antes tus valores (solo la primera vez, para no pisar el original)."""
    lineas: List[str] = []
    try:
        previos = {c: _leer_ajuste(sub, aj) for c, sub, aj, *_ in _AJUSTES}
        if not os.path.exists(_ENERGIA_BACKUP):
            import json
            with open(_ENERGIA_BACKUP, "w", encoding="utf-8") as f:
                json.dump({c: list(v) for c, v in previos.items() if v}, f)
            lineas.append(f"💾 Guardé tus valores actuales en {_ENERGIA_BACKUP}")
        for clave, sub, aj, ac, dc, texto in _AJUSTES:
            if previos.get(clave) is None:
                lineas.append(f"  – {texto}: este equipo no tiene ese ajuste, lo omito")
                continue
            ok = _poner_ajuste(sub, aj, ac, dc)
            lineas.append(f"  {'✓' if ok else '✗'} {texto}")
        rc, _ = _powercfg(["/setactive", "SCHEME_CURRENT"])
        lineas.append("  ✓ Plan aplicado" if rc == 0 else "  ✗ No pude activar el plan (cierra sesión y vuelve a entrar)")
        return True, lineas
    except Exception as e:
        logger.exception("❌ apagado.energia_mochila falló")
        return False, lineas + [f"✗ Error: {type(e).__name__}: {e}"]


def energia_restaurar() -> Tuple[bool, List[str]]:
    try:
        import json
        if not os.path.exists(_ENERGIA_BACKUP):
            return False, ["No hay copia de seguridad (apagado_energia_backup.json): no hice nada."]
        with open(_ENERGIA_BACKUP, encoding="utf-8") as f:
            ant = json.load(f)
        lineas = []
        for clave, sub, aj, _ac, _dc, texto in _AJUSTES:
            if clave in ant:
                ok = _poner_ajuste(sub, aj, ant[clave][0], ant[clave][1])
                lineas.append(f"  {'✓' if ok else '✗'} Restaurado: {texto.split(':')[0]}")
        _powercfg(["/setactive", "SCHEME_CURRENT"])
        os.remove(_ENERGIA_BACKUP)
        lineas.append("  ✓ Plan aplicado; borré la copia de seguridad")
        return True, lineas
    except Exception as e:
        logger.exception("❌ apagado.energia_restaurar falló")
        return False, [f"✗ Error: {type(e).__name__}: {e}"]


def _cli_energia(argv: List[str]) -> None:
    if os.name != "nt" and _ejecutor_powercfg is None:
        print("Esto solo funciona en Windows (ejecútalo en la laptop donde corre SARA).")
        return
    cmd = (argv[0] if argv else "estado").lower()
    if cmd == "mochila":
        ok, ls = energia_mochila()
        print("\n".join(ls))
        print("\nListo. La laptop ya no se suspende al cerrar la tapa. Para volver a lo de antes: python apagado.py energia restaurar")
        print("⚠️ Cerrada en la mochila se calienta: no la cubras ni la apiles contra la ropa; y vigila la batería.")
    elif cmd == "restaurar":
        print("\n".join(energia_restaurar()[1]))
    else:
        print("Ajustes actuales del plan de energía:")
        print("\n".join(energia_estado()))
        print("\n(Para aplicar el modo mochila: python apagado.py energia mochila)")



def diagnostico() -> str:
    p = programado()
    return (f"Apagado {APAGADO_VERSION} · activo: {'sí' if _cfg('APAGADO_ACTIVO', True) else 'no'} · "
            f"código remoto: {modo_codigo()} (pide código: {', '.join(a for a in ACCIONES if requiere_codigo(a))}) · TOTP {'configurado' if totp_configurado() else 'sin configurar'} · "
            f"cuenta atrás por defecto {_cfg('APAGADO_DEMORA_S', 30)} s (remoto ≥ {_cfg('APAGADO_REMOTO_DEMORA_MIN_S', 30)} s)"
            + (f" · apagado en curso: faltan {p} s" if p else "")
            + (f" · último error: {_ultimo_error}" if _ultimo_error else ""))


def _cli_totp(argv: List[str]) -> None:
    sec = _totp_leer()
    if sec is None or "nuevo" in argv:
        if sec is not None:
            print("⚠️ Ya había un secreto: se reemplaza (tendrás que volver a añadirlo a tu app autenticadora).")
        sec = totp_crear()
        print("✅ Secreto creado en", _TOTP_ARCHIVO)
    else:
        print("Ya está configurado. Para ver el secreto otra vez es el mismo; para reemplazarlo: python apagado.py totp nuevo")
    print("\nEn tu celular, en Google Authenticator / Microsoft Authenticator / Authy:")
    print("  «Añadir cuenta» → «Introducir clave de configuración» (o «Ingresar clave manualmente»)")
    print("  Nombre de la cuenta: SARA")
    print(f"  Clave: {sec}")
    print("  Tipo: basada en tiempo (por defecto) · 6 dígitos")
    print(f"\n(O como enlace otpauth para un lector de QR: {totp_uri(sec)})")
    print("\nGuarda apagado_totp.key solo en tu PC y no lo subas a ningún lado. Para usarlo: APAGADO_CODIGO_MODO = \"totp\" o \"auto\".")


if __name__ == "__main__" and sys.argv[1:2] == ["totp"]:        # python apagado.py totp
    _cli_totp(sys.argv[2:])
elif __name__ == "__main__" and sys.argv[1:2] == ["energia"]:   # python apagado.py energia mochila|restaurar|estado
    _cli_energia(sys.argv[2:])
elif __name__ == "__main__":                            # python apagado.py → prueba el intérprete (NO apaga nada)
    for frase in ("apaga la computadora", "apaga el equipo en 5 minutos", "cancela el apagado",
                  "apaga la música", "cómo apago la compu"):
        print(f"{frase!r:42} → {interpretar(frase)}")
    print(diagnostico())
