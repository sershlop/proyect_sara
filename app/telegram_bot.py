# 📨 telegram_bot.py — Canal Telegram de SARA (v1)
#
# Qué es: un bot PRIVADO de Telegram que es solo un canal de control remoto de SARA (tu laptop):
#   · le escribes en lenguaje natural o le mandas una NOTA DE VOZ → SARA ejecuta en el PC y te contesta en el chat
#   · las acciones de riesgo te piden confirmación con BOTONES (Sí / No / opciones) en el mismo chat
# Qué NO es (a propósito): no lee tus chats de Telegram, no revisa notificaciones, no manda mensajes a nadie.
#   Solo habla con UNA cuenta: la tuya (vinculada con un código de un solo uso).
#
# Cómo: Bot API oficial con long-polling (getUpdates). Solo conexiones SALIENTES a api.telegram.org:
#   no hay servidor, no hay puertos abiertos, no hay proceso Node. Solo usa `requests` (ya lo tienes).
#
# Seguridad (misma filosofía que SARA-Net de WhatsApp):
#   · lista blanca de UN id numérico de usuario; cualquier otra persona es ignorada EN SILENCIO (ni responde)
#   · solo chat privado; mensajes reenviados, editados o de grupos: descartados
#   · órdenes viejas (> TG_MAX_EDAD_S) no se ejecutan · límite de órdenes por minuto
#   · las categorías de riesgo (TG_CATEGORIAS_BLOQUEADAS) las bloquea core_bridge para origen "telegram"
#   · los comandos "/" de SARA no se aceptan por aquí (salvo TG_SLASH_PERMITIDOS, solo lectura)
#   · por voz, una confirmación pendiente solo se puede CANCELAR (un «sí» mal oído no confirma nada)
#   · el token vive en .env (TELEGRAM_BOT_TOKEN) y se oculta de cualquier log
#   · interruptor propio, por defecto APAGADO: /telegram net on|off
#
# Aislado y opcional: si falta este archivo, core_bridge sigue igual (y WhatsApp no se toca).
#
# Contrato con core_bridge (igual que whatsapp.py):
#   registrar_nucleo(procesar, confirmar) · crear_tablas() · arrancar() · comando_slash(arg) -> (ok, texto)
#   eventos_proactivos() -> [(tipo, datos)] · notificar_usuario(txt) -> bool · bocina_activa() -> bool

TELEGRAM_VERSION = "v1.1-botapi-longpolling+apagado"

import hmac
import inspect
import logging
import os
import queue
import re
import secrets
import tempfile
import threading
import time
import unicodedata
from collections import deque
from typing import List, Optional, Tuple

log = logging.getLogger("SARA-TG")

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


def _activo() -> bool:
    return bool(_c("TG_ACTIVO", True))


# ─── TOKEN (solo .env; nunca en el log) ───────────────────────────────────

def _token() -> str:
    t = (os.getenv("TELEGRAM_BOT_TOKEN") or str(_c("TG_BOT_TOKEN", "") or "")).strip()
    return t if re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", t) else ""


def _ocultar(s: str) -> str:
    """Las excepciones de `requests` incluyen la URL (y por tanto el token): siempre pasar por aquí."""
    t = _token()
    return str(s).replace(t, "<token>") if t else str(s)


def _log_error(contexto: str, exc: Exception) -> None:
    msg = _ocultar(f"{type(exc).__name__}: {exc}")
    try:
        import logger as _sl
        _sl.log_excepcion("telegram", contexto, RuntimeError(msg))
    except Exception:
        log.warning("%s: %s", contexto, msg)


# ─── MEMORIA (sara.db) ────────────────────────────────────────────────────

_tabla_ok = False


def crear_tablas() -> None:
    global _tabla_ok
    if _tabla_ok or _conectar is None:
        return
    try:
        with _conectar() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS tg_memoria (clave TEXT PRIMARY KEY, valor TEXT NOT NULL)")
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
            f = conn.execute("SELECT valor FROM tg_memoria WHERE clave = ?", (clave,)).fetchone()
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
            conn.execute("INSERT INTO tg_memoria (clave, valor) VALUES (?, ?) "
                         "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (clave, valor))
            conn.commit()
    except Exception as e:
        _log_error("_kv_set", e)


def _kv_del(clave: str) -> None:
    if _conectar is None:
        return
    try:
        with _conectar() as conn:
            conn.execute("DELETE FROM tg_memoria WHERE clave = ?", (clave,))
            conn.commit()
    except Exception as e:
        _log_error("_kv_del", e)


def _flag(nombre: str, cfg_nombre: str, defecto: bool = False) -> bool:
    v = _kv_get(nombre)
    return bool(_c(cfg_nombre, defecto)) if v is None else v == "1"


def _net_on() -> bool:
    return _flag("net", "TG_NET_ACTIVO", False)


def _voz_on() -> bool:
    return _flag("voz", "TG_VOZ_ACTIVO", False)


def _audio_on() -> bool:
    return _flag("audio", "TG_RESPUESTA_AUDIO", False)


def _palabra_on() -> bool:
    return _flag("palabra", "TG_REQUERIR_PALABRA", False)


def bocina_activa() -> bool:
    """¿Las respuestas a órdenes de Telegram también se dicen en voz alta en el PC? (core_bridge._hablar)"""
    return _flag("bocina", "TG_BOCINA", False)


def _avisos_on() -> bool:
    return _flag("avisos", "TG_AVISOS", False)


def _owner_id() -> Optional[int]:
    v = _kv_get("owner")
    try:
        return int(v) if v else None
    except ValueError:
        return None


# ─── API DE TELEGRAM ──────────────────────────────────────────────────────

_ultimo_error = ""
_bot_user = ""
_sesion = None
_sesion_lock = threading.Lock()


def _http_session():
    """Crea el cliente HTTP solo tras una operación explícita con Telegram."""
    global _sesion
    with _sesion_lock:
        if _sesion is None:
            import requests
            _sesion = requests.Session()
            _sesion.headers.update({"Connection": "keep-alive"})
        return _sesion


def _api(metodo: str, datos: Optional[dict] = None, timeout: float = 15, archivos: Optional[dict] = None) -> Optional[dict]:
    """None = sin red / sin token. Si Telegram contesta: {"ok": True, "result": ...} o {"ok": False, "codigo", "error"}."""
    global _ultimo_error
    tok = _token()
    if not tok:
        _ultimo_error = "falta TELEGRAM_BOT_TOKEN en .env"
        return None
    try:
        url = f"https://api.telegram.org/bot{tok}/{metodo}"
        if archivos:
            r = _http_session().post(url, data=datos, files=archivos, timeout=timeout)
        else:
            r = _http_session().post(url, json=datos or {}, timeout=timeout)
        j = r.json()
    except Exception as e:                       # NO usar str(e): lleva el token en la URL
        _ultimo_error = f"red: {type(e).__name__}"
        return None
    if j.get("ok"):
        _ultimo_error = ""
        return j
    codigo = int(j.get("error_code") or r.status_code or 0)
    _ultimo_error = f"Telegram {codigo}: {j.get('description', '')}"[:200]
    return {"ok": False, "codigo": codigo, "error": j.get("description", "")}


def _trozos(texto: str, n: int = 3900) -> List[str]:
    texto = str(texto or "").strip() or "…"
    out = []
    while len(texto) > n:
        corte = texto.rfind("\n", 0, n)
        corte = corte if corte > n // 2 else n
        out.append(texto[:corte].rstrip())
        texto = texto[corte:].lstrip()
    out.append(texto)
    return out


def _enviar(texto: str, teclado: Optional[list] = None) -> Optional[int]:
    """Texto plano (sin Markdown: nada que pueda fallar al formatear) al dueño. Devuelve message_id del último trozo."""
    owner = _owner_id()
    if owner is None:
        return None
    mid = None
    partes = _trozos(texto)
    for i, parte in enumerate(partes):
        datos = {"chat_id": owner, "text": parte, "link_preview_options": {"is_disabled": True}}
        if teclado and i == len(partes) - 1:
            datos["reply_markup"] = {"inline_keyboard": teclado}
        j = _api("sendMessage", datos, timeout=20)
        if not j or not j.get("ok"):
            log.warning("TG: no pude enviar (%s)", _ultimo_error)
            return None
        mid = (j.get("result") or {}).get("message_id")
    return mid


def _responder(texto: str) -> bool:
    """Respuesta al dueño. Nunca lanza."""
    try:
        return _enviar(texto) is not None
    except Exception as e:
        _log_error("_responder", e)
        return False


def _accion(accion: str = "typing") -> None:
    owner = _owner_id()
    if owner is not None:
        _api("sendChatAction", {"chat_id": owner, "action": accion}, timeout=5)


def _enviar_audio(texto: str) -> None:
    """Respuesta hablada (voz de SARA) como nota de voz. Voz neuronal (mp3) si Edge-TTS responde; si no, la voz de
    Windows (ogg/opus, o wav como documento si no se pudo convertir). Si no se puede, queda en el log (no en silencio)."""
    owner = _owner_id()
    try:
        import voz
        if owner is None or not hasattr(voz, "sintetizar"):
            return
        try:
            ruta = voz.sintetizar(texto, completo=True)       # completo: sin el recorte de 300 caracteres del PC
        except TypeError:                                     # voz.py anterior a la v3.3
            ruta = voz.sintetizar(texto)
        if not ruta:
            log.info("TG: sin nota de voz de respuesta (ni la voz neuronal ni la de Windows respondieron); va solo el texto")
            return
        ext = os.path.splitext(ruta)[1].lower()
        with open(ruta, "rb") as f:
            if ext == ".wav":                                 # sendVoice no acepta WAV: se manda como archivo de audio
                _api("sendDocument", {"chat_id": owner}, timeout=90, archivos={"document": ("sara.wav", f, "audio/wav")})
            elif ext == ".ogg":
                _api("sendVoice", {"chat_id": owner}, timeout=60, archivos={"voice": ("sara.ogg", f, "audio/ogg")})
            else:
                _api("sendVoice", {"chat_id": owner}, timeout=60, archivos={"voice": ("sara.mp3", f, "audio/mpeg")})
    except Exception as e:
        _log_error("_enviar_audio", e)


_audio_lock = threading.Lock()


def _enviar_audio_async(texto: str) -> None:
    """La nota de voz se sintetiza y se sube APARTE: no frena la siguiente orden del ejecutor (que es de una en una)."""
    def _run():
        with _audio_lock:               # una a la vez y en orden de llegada
            _enviar_audio(texto)
    threading.Thread(target=_run, name="sara-tg-audio", daemon=True).start()


# ─── TEXTO ────────────────────────────────────────────────────────────────

def _plano(s: str) -> str:
    """Minúsculas sin tildes, MISMA longitud que el original (para recortar el original con los índices)."""
    return "".join(unicodedata.normalize("NFD", ch)[0] for ch in (s or "")).lower()


def _clave(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _plano(s))


_SI = {"si", "s", "ok", "okay", "dale", "va", "sip", "claro", "confirmo", "confirmar", "yes", "y", "adelante", "hazlo"}
_NO = {"no", "n", "cancela", "cancelar", "cancelalo", "nel", "nop", "detente", "para"}


def _modulo_stt():
    try:
        import stt
        return stt if hasattr(stt, "transcribir_archivo") else None
    except Exception:
        return None


def _palabras(voz: bool = False) -> Tuple[str, ...]:
    base = list(_c("TG_PALABRAS", ("sara", "sarah")))
    if voz:
        s_ = _modulo_stt()
        if s_ is not None:
            base += list(getattr(s_, "PALABRAS_ACTIVACION", ()) or ())
    return tuple(dict.fromkeys(w for w in (_clave(x) for x in base) if w))


def _extraer_orden(texto: str, voz: bool = False) -> Optional[str]:
    """None si NO empieza con «Sara». Si sí: la orden sin la palabra."""
    ws = _palabras(voz)
    if not ws:
        return None
    p = _plano(texto)
    m = re.match(r"^[\s¡¿\"'«.,\-–—]*(?:(?:oye|hey|ey|hola)[\s,.]+)?(?:" + "|".join(re.escape(w) for w in ws)
                 + r")\b[\s,:;.!¡¿?\-–—]*", p)
    return texto[m.end():].strip() if m else None


def _orden_de(texto: str, voz: bool = False) -> Optional[str]:
    """Aquí el chat es SOLO tuyo y del bot, así que «Sara» es opcional (se quita si la dices).
    Con /telegram palabra on se vuelve obligatoria."""
    orden = _extraer_orden(texto, voz)
    if orden is None:
        return None if _palabra_on() else texto.strip()
    return orden


# ─── ESTADO ───────────────────────────────────────────────────────────────

_lock = threading.Lock()
_hilos_ok = False
_hilo_poll: Optional[threading.Thread] = None
_hilo_ejecutor: Optional[threading.Thread] = None
_hilo_voz: Optional[threading.Thread] = None
_poll_wake = threading.Event()
_PARAR = object()
_offset = 0
_sincronizado = False
_nucleo_procesar = None                       # (texto, emit) -> {"exito","texto"}
_nucleo_confirmar = None                      # (id, respuesta, opcion) -> bool
_cola: "queue.Queue" = queue.Queue(maxsize=3)
_cola_voz: "queue.Queue" = queue.Queue(maxsize=3)
_eventos: deque = deque(maxlen=20)            # avisos para la GUI (los recoge eventos_proactivos)
_cmds: deque = deque(maxlen=30)
_vistos: deque = deque(maxlen=300)
_conf: Optional[dict] = None                  # confirmación pendiente
_vinculo: Optional[dict] = None               # vinculación en curso {"codigo","expira","intentos"}
_ultima_voz = ""                              # SOLO en RAM
_ultimo_descarte = "—"
_stats = {"vistos": 0, "aceptados": 0, "ajenos": 0, "viejos": 0, "reenviados": 0, "voz_aceptadas": 0,
          "voz_errores": 0, "botones": 0}
_DIR_AUDIO = os.path.join(tempfile.gettempdir(), "sara_tg_audio")


_nucleo_acepta_voz = False                     # ¿el núcleo registrado admite el parámetro `voz`? (se decide al registrar)


def registrar_nucleo(procesar, confirmar) -> None:
    global _nucleo_procesar, _nucleo_confirmar, _nucleo_acepta_voz
    _nucleo_procesar, _nucleo_confirmar = procesar, confirmar
    try:       # se mira la firma UNA vez: un TypeError al ejecutar una orden no debe provocar un segundo intento
        p = inspect.signature(procesar).parameters
        _nucleo_acepta_voz = "voz" in p or any(x.kind == inspect.Parameter.VAR_KEYWORD for x in p.values())
    except (TypeError, ValueError):
        _nucleo_acepta_voz = False


def _descartar(motivo: str, clave: str) -> None:
    global _ultimo_descarte
    _stats[clave] = _stats.get(clave, 0) + 1
    _ultimo_descarte = motivo
    log.debug("TG descarta: %s", motivo)


def _vinculando() -> bool:
    global _vinculo
    if _vinculo and time.time() > _vinculo["expira"]:
        _vinculo = None
    return _vinculo is not None


# ─── ARRANQUE / SONDEO ────────────────────────────────────────────────────

def arrancar() -> None:
    """Solo inicia el sondeo si el canal fue activado explícitamente."""
    if not _activo() or not _token() or not (_net_on() or _vinculando()):
        return
    try:
        if os.path.isdir(_DIR_AUDIO):
            for f in os.listdir(_DIR_AUDIO):
                try:
                    os.remove(os.path.join(_DIR_AUDIO, f))
                except OSError:
                    pass
    except OSError:
        pass
    _asegurar_hilos()


def _asegurar_hilos() -> None:
    global _hilos_ok, _hilo_poll
    with _lock:
        if _hilo_poll is not None and _hilo_poll.is_alive():
            return
        _poll_wake.clear()
        _hilo_poll = threading.Thread(target=_bucle, name="sara-tg-poll", daemon=True)
        _hilos_ok = True
        _hilo_poll.start()


def _asegurar_trabajador(nombre: str) -> None:
    global _hilo_ejecutor, _hilo_voz
    with _lock:
        if nombre == "sara-tg-ejecutor":
            attr, fn = "_hilo_ejecutor", _ejecutor
        else:
            attr, fn = "_hilo_voz", _voz_trabajador
        hilo = globals()[attr]
        if hilo is None or not hilo.is_alive():
            hilo = threading.Thread(target=fn, name=nombre, daemon=True)
            globals()[attr] = hilo
            hilo.start()


def _vaciar_cola(cola: queue.Queue) -> None:
    while True:
        try:
            cola.get_nowait()
        except queue.Empty:
            return


def _detener_trabajadores() -> None:
    with _lock:
        trabajadores = ((_hilo_ejecutor, _cola), (_hilo_voz, _cola_voz))
    for hilo, cola in trabajadores:
        if hilo is not None and hilo.is_alive():
            _vaciar_cola(cola)
            cola.put_nowait(_PARAR)


def _esperar_poll(segundos: float) -> None:
    _poll_wake.wait(segundos)
    _poll_wake.clear()


def _sincronizar() -> bool:
    """Salta TODO lo que ya estaba en la cola de Telegram: una orden vieja nunca se ejecuta al encender."""
    global _offset, _sincronizado
    _api("deleteWebhook", {"drop_pending_updates": False}, timeout=10)     # getUpdates falla si quedó un webhook
    j = _api("getUpdates", {"offset": -1, "timeout": 0, "limit": 1}, timeout=15)
    if j and j.get("ok"):
        res = j.get("result") or []
        if res:
            _offset = int(res[-1]["update_id"]) + 1
        _sincronizado = True
        return True
    return False


def _esperar_sync(segundos: float = 8.0) -> bool:
    fin = time.time() + segundos
    while time.time() < fin:
        if _sincronizado:
            return True
        time.sleep(0.2)
    return _sincronizado


def _bucle() -> None:
    global _offset, _sincronizado, _hilos_ok, _hilo_poll
    try:
        while _activo() and _token() and (_net_on() or _vinculando()):
            try:
                if not _activo() or not _token() or not (_net_on() or _vinculando()):
                    _sincronizado = False
                    return
                if not _sincronizado:
                    if not _sincronizar():
                        _esperar_poll(5)
                    continue
                j = _api("getUpdates", {"offset": _offset, "timeout": 25,
                                        "allowed_updates": ["message", "callback_query"]}, timeout=35)
                if not (_net_on() or _vinculando()):
                    _sincronizado = False
                    return
                if j is None:
                    _esperar_poll(5)                # sin red
                    continue
                if not j.get("ok"):
                    cod = j.get("codigo")
                    if cod == 409:                  # otra instancia de SARA (o un webhook) usa este bot
                        _sincronizado = False
                        _esperar_poll(15)
                    elif cod in (401, 404):         # token inválido o revocado
                        _esperar_poll(30)
                    else:
                        _esperar_poll(5)
                    continue
                for u in j.get("result") or []:
                    _offset = max(_offset, int(u.get("update_id", 0)) + 1)
                    try:
                        _atender(u)
                    except Exception as e:
                        _log_error("_atender", e)
            except Exception as e:
                _log_error("_bucle", e)
                _esperar_poll(5)
    finally:
        _sincronizado = False
        _detener_trabajadores()
        with _lock:
            if _hilo_poll is threading.current_thread():
                _hilo_poll = None
            _hilos_ok = _hilo_poll is not None and _hilo_poll.is_alive()


# ─── ATENDER UN UPDATE ────────────────────────────────────────────────────

def _atender(u: dict) -> None:
    if not (_net_on() or _vinculando()):
        return
    if u.get("callback_query"):
        return _atender_boton(u["callback_query"])
    m = u.get("message")
    if not m:
        return
    chat, frm = m.get("chat") or {}, m.get("from") or {}
    if chat.get("type") != "private":
        return _descartar("mensaje de grupo/canal (nunca ejecuta)", "ajenos")
    owner = _owner_id()
    if owner is None or frm.get("id") != owner or chat.get("id") != owner or frm.get("is_bot"):
        if _vinculando():
            return _intentar_vinculo(m)
        return _descartar("mensaje de una cuenta que no es la vinculada (ignorado en silencio)", "ajenos")

    mid = m.get("message_id")
    if mid is not None:
        if mid in _vistos:
            return
        _vistos.append(mid)
    _stats["vistos"] += 1

    if m.get("forward_origin") or m.get("forward_date") or m.get("forward_from"):
        _stats["reenviados"] += 1
        _responder("↪️ Ignoré un mensaje reenviado: solo obedezco lo que tú escribes aquí.")
        return
    if m.get("voice") or m.get("audio"):
        return _atender_voz(m)
    texto = str(m.get("text") or "").strip()
    if not texto:
        _responder("Solo entiendo texto y notas de voz.")
        return
    if texto.startswith("/"):
        return _atender_slash(texto)
    if _intentar_codigo_apagado(texto):
        return
    if _intentar_desbloqueo(texto):
        return
    if _intentar_confirmacion(texto):
        return
    orden = _orden_de(texto)
    if orden is None:
        _responder("Tengo activada la palabra de activación: empieza con «Sara», p. ej. «Sara, abre Chrome».")
        return
    _encolar(orden, m.get("date"))


_AYUDA_BOT = ("Soy el canal de control de SARA. Escríbeme o mándame una nota de voz con lo que quieras hacer en tu "
              "laptop («abre Chrome», «¿cuánta batería tengo?», «crea un recordatorio…») o conversa conmigo.\n"
              "Puedes pedir acciones por lenguaje natural, incluida la pantalla en vivo de la laptop. Para recibir un "
              "archivo o ver una captura/pantalla se pide el código del autenticador.\n"
              "Comandos: /start · /help · /ayuda · /estado · /cancelar · /salud\n"
              "Laptop: /pantalla · /donde · /foto · /bloquear · /deshacer · /papelera\n"
              "Modos y ajustes: /modos · /modo NOMBRE · /alertas on|off · /descripcion on|off · /verificacion on\n"
              "Crear/borrar modos y apagar la verificación solo desde la ventana de SARA. Las acciones de apagado "
              "requieren confirmación y el código de seguridad configurado.\n"
              "Los ajustes del canal (net, voz, audio, bocina, avisos y palabra de activación) se cambian desde la "
              "ventana con /telegram.")


def _atender_slash(texto: str) -> None:
    cmd = re.sub(r"@\w+$", "", texto.split()[0].lower())          # "/estado@MiBot" → "/estado"
    if cmd in ("/start", "/ayuda", "/help"):
        _responder(_AYUDA_BOT)
    elif cmd == "/estado":
        _responder(f"✅ Conectada. SARA-Telegram {'ACTIVO' if _net_on() else 'apagado'} · "
                   f"notas de voz {'sí' if _voz_on() else 'no'} · respuesta en audio {'sí' if _audio_on() else 'no'}.")
    elif cmd == "/cancelar":
        global _conf
        c = _conf
        if c and time.time() <= c["expira"]:
            _conf = None
            _decidir(c, "no", None)
            _responder("Cancelado.")
        else:
            _msg_ap = _cancelar_apagado_todo()
            _responder(_msg_ap or "No hay nada pendiente que cancelar.")
    elif cmd == "/pantalla":
        _encolar("quiero ver la pantalla", time.time())
    elif cmd == "/donde":
        _encolar("dónde está mi laptop", time.time())
    elif cmd == "/foto":
        _encolar("toma una foto con la webcam", time.time())
    elif cmd == "/bloquear":
        _encolar("bloquea la pantalla", time.time())
    elif cmd == "/deshacer":
        _encolar("deshaz lo último", time.time())
    elif cmd == "/papelera":
        try:
            import deshacer
            _responder(deshacer.comando_papelera("")[1])          # solo lectura: vaciarla es desde la ventana
        except Exception as e:
            _log_error("/papelera", e)
            _responder("El módulo de deshacer no está disponible.")
    elif cmd == "/modos":
        _encolar("qué modos tengo", time.time())
    elif cmd == "/modo":
        arg = texto.split(maxsplit=1)[1].strip() if len(texto.split()) > 1 else ""
        if not arg:
            _encolar("qué modos tengo", time.time())
        elif re.match(r"(?i)^(nuevo|borrar)\b", arg):
            _responder("Crear o borrar modos solo se puede desde la ventana de SARA.")
        else:
            _encolar(f"activa el modo {arg}", time.time())
    elif cmd in ("/descripcion", "/descripción", "/verificacion", "/verificación"):
        try:
            import interruptores
            arg = texto.split(maxsplit=1)[1] if len(texto.split()) > 1 else ""
            f = interruptores.comando_descripcion if "descrip" in cmd else interruptores.comando_verificacion
            _responder(f(arg, "telegram")[1])
        except Exception as e:
            _log_error(cmd, e)
            _responder("El módulo de ajustes no está disponible.")
    elif cmd == "/alertas":
        try:
            import alertas
            _responder(alertas.comando(texto.split(maxsplit=1)[1] if len(texto.split()) > 1 else "")[1])
        except Exception as e:
            _log_error("/alertas", e)
            _responder("El módulo de alertas no está disponible.")
    elif cmd in tuple(str(x).lower() for x in _c("TG_SLASH_PERMITIDOS", ("/salud",))):
        _encolar(texto.strip(), time.time())                      # solo lectura: pasa por el núcleo normal
    else:
        _responder("Ese comando solo funciona desde la ventana de SARA. Pídemelo en lenguaje natural.")


def _encolar(orden: str, ts, voz: bool = False) -> None:
    """Últimas comprobaciones y entrega al ejecutor. Texto y voz comparten TODAS las reglas."""
    edad = time.time() - float(ts or time.time())
    if edad > float(_c("TG_MAX_EDAD_S", 180)):
        _stats["viejos"] += 1
        _responder(f"⌛ Ignoré una orden porque llegó con {int(edad // 60)} min de retraso "
                   "(por seguridad no ejecuto órdenes viejas).")
        return
    if not orden:
        _responder("Aquí estoy. Dime qué hacer, por ejemplo: «abre Chrome».")
        return
    if orden.startswith("/") and orden.split()[0].lower() not in tuple(str(x).lower() for x in _c("TG_SLASH_PERMITIDOS", ("/salud",))):
        _responder("Los comandos con / solo funcionan desde la ventana de SARA. Pídemelo en lenguaje natural.")
        return
    if _intentar_apagado(orden, voz):          # apagar / cancelar el apagado: flujo propio con código por WhatsApp
        return
    ahora = time.time()
    while _cmds and ahora - _cmds[0] > 60:
        _cmds.popleft()
    if len(_cmds) >= int(_c("TG_MAX_COMANDOS_MIN", 6)):
        _responder("Demasiadas órdenes seguidas; espera un minuto.")
        return
    if voz:
        orden = orden.rstrip(" .…")
        _responder(f"🎙️ Entendí: «{orden[:200]}»")
    try:
        _asegurar_trabajador("sara-tg-ejecutor")
        _cola.put_nowait((orden, voz))
    except queue.Full:
        _responder("Sigo ocupada con tus órdenes anteriores; reintenta en un momento.")
        return
    _cmds.append(ahora)
    _stats["aceptados"] += 1
    if voz:
        _stats["voz_aceptadas"] += 1



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
            _gui("actividad_remota", canal="telegram", fase=fase, **datos)
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


def _ejecutor() -> None:
    """UNA orden a la vez (el núcleo tiene estado global). Aparte del sondeo: mientras espera una confirmación,
    el sondeo sigue leyendo tus botones."""
    global _conf
    while True:
        item = _cola.get()
        if item is _PARAR:
            if not _net_on():
                return
            continue
        orden, voz = item
        if not _net_on():
            continue
        ok = False
        iniciado = False
        try:
            if _nucleo_procesar is None:
                _responder("⚠️ El núcleo de SARA todavía no está listo; reintenta en unos segundos.")
                continue
            threading.Thread(target=_accion, daemon=True).start()     # «escribiendo…» sin retrasar la orden
            _gui_inicio(orden)
            iniciado = True
            # Una nota de voz le dice al núcleo que es voz (puede venir mal oída): así se limpia como el micrófono.
            r = (_nucleo_procesar(orden, _emit_tg, voz=voz) if _nucleo_acepta_voz else _nucleo_procesar(orden, _emit_tg)) or {}
            ok = bool(r.get("exito"))
            txt = str(r.get("texto") or "").strip() or ("Listo." if ok else "No pude hacerlo.")
            _eventos.append(("notificacion", {"texto": f"📨 Telegram → «{orden[:60]}»: {txt[:160]}",
                                              "nivel": "exito" if ok else "aviso"}))
            if not bocina_activa():            # con bocina, core_bridge ya emite voz_estado
                _gui_habla(txt)
            _responder(("✅ " if ok else "⚠️ ") + txt)
            if r.get("requiere_codigo"):       # envío / pantalla: queda esperando el código TOTP
                _pedir_desbloqueo(orden)
            if _audio_on():
                _enviar_audio_async(txt)
        except Exception as e:
            ok = False
            _log_error("_ejecutor", e)
            _responder("⚠️ Error interno al ejecutar tu orden.")
        finally:
            _conf = None
            if iniciado:
                _gui_fin(ok)


# ─── CONFIRMACIONES (botones en el chat) ──────────────────────────────────

def _emit_tg(tipo: str, **datos) -> None:
    """El 'emit' que recibe el núcleo para órdenes de Telegram: solo viajan las confirmaciones."""
    global _conf
    try:
        if tipo == "solicitud_confirmacion":
            ops = [str(o) for o in (datos.get("opciones") or [])]
            seg = int(datos.get("timeout_segundos") or 30)
            idc = str(datos.get("id_confirmacion") or "")
            if ops:
                teclado = [[{"text": f"{i}) {o}"[:60], "callback_data": f"c|{idc}|{i - 1}"}] for i, o in enumerate(ops, 1)]
                teclado.append([{"text": "✖ Cancelar", "callback_data": f"c|{idc}|no"}])
                cuerpo = f"{datos.get('pregunta', '')}\n({seg} s para elegir)"
            else:
                teclado = [[{"text": "✔ Sí", "callback_data": f"c|{idc}|si"},
                            {"text": "✖ No", "callback_data": f"c|{idc}|no"}]]
                cuerpo = f"{datos.get('pregunta', '')}\n({seg} s para responder)"
            _conf = {"id": idc, "opciones": ops, "expira": time.time() + seg + 5, "mid": None}
            _conf["mid"] = _enviar("❓ " + cuerpo, teclado)
        elif tipo == "confirmacion_ya_resuelta":
            c, _conf = _conf, None
            if c and datos.get("resultado") == "timeout":
                _quitar_botones(c, "⌛ Sin respuesta a tiempo: cancelé la acción.")
                _responder("⌛ Sin respuesta a tiempo: cancelé la acción.")
    except Exception as e:
        _log_error("_emit_tg", e)


def _quitar_botones(c: dict, nota: str) -> None:
    owner = _owner_id()
    if owner is not None and c.get("mid"):
        _api("editMessageReplyMarkup", {"chat_id": owner, "message_id": c["mid"], "reply_markup": {"inline_keyboard": []}}, timeout=8)


def _decidir(c: dict, resp: str, idx: Optional[int]) -> None:
    try:
        if _nucleo_confirmar is not None:
            _nucleo_confirmar(c["id"], resp, idx)
    except Exception as e:
        _log_error("_decidir", e)


def _atender_boton(cq: dict) -> None:
    global _conf
    owner = _owner_id()
    frm = cq.get("from") or {}
    msg = cq.get("message") or {}
    cid = cq.get("id")
    if owner is None or frm.get("id") != owner or (msg.get("chat") or {}).get("id") != owner:
        return _descartar("botón pulsado por una cuenta no vinculada", "ajenos")
    partes = str(cq.get("data") or "").split("|")
    if partes and partes[0] == "a":
        return _boton_apagado(cq, partes)
    c = _conf
    if len(partes) != 3 or partes[0] != "c" or not c or c["id"] != partes[1] or time.time() > c["expira"]:
        _api("answerCallbackQuery", {"callback_query_id": cid, "text": "Esa pregunta ya expiró."}, timeout=8)
        if msg.get("message_id"):
            _api("editMessageReplyMarkup", {"chat_id": owner, "message_id": msg["message_id"],
                                            "reply_markup": {"inline_keyboard": []}}, timeout=8)
        return
    _stats["botones"] += 1
    acc = partes[2]
    _conf = None
    if acc == "no":
        resp, idx, nota = "no", None, "Cancelado"
    elif acc == "si":
        resp, idx, nota = "si", None, "Confirmado"
    elif acc.isdigit() and int(acc) < len(c["opciones"]):
        resp, idx, nota = "si", int(acc), f"Elegiste: {c['opciones'][int(acc)]}"
    else:
        return
    _api("answerCallbackQuery", {"callback_query_id": cid, "text": nota}, timeout=8)
    _quitar_botones(c, nota)
    _decidir(c, resp, idx)


def _intentar_confirmacion(texto: str) -> bool:
    """También se puede contestar escribiendo («sí», «no», «2»). True si era la respuesta a una confirmación."""
    global _conf
    c = _conf
    if not c:
        return False
    if time.time() > c["expira"]:
        _conf = None
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
        _responder("Responde con los botones, con el número de la opción o «no»." if ops
                   else "Responde con los botones, o escribe «sí» / «no».")
        return True
    _conf = None
    _quitar_botones(c, "")
    _decidir(c, resp, idx)
    return True


# ─── APAGADO REMOTO (confirmación con botones + código de 5 dígitos por WhatsApp) ─────────────────────
# Flujo (nada de esto pasa por el núcleo de SARA; lo resuelve este archivo con apagado.py):
#   1) «apaga la computadora» (SOLO texto; por nota de voz se rechaza) → mensaje con la advertencia y botones Sí / Cancelar
#   2) «Sí, apagar» → SARA manda un código de 5 dígitos a TU WhatsApp (chat «Tú»). Si WhatsApp no está enlazado, NO apaga.
#   3) escribes el código aquí → shutdown /s /t N (cuenta atrás ≥ 30 s) → «cancela el apagado» lo detiene
# Seguridad: el código es de un solo uso, vale APAGADO_CODIGO_VALIDEZ_S, admite APAGADO_CODIGO_INTENTOS fallos y después
# se bloquea el apagado remoto APAGADO_BLOQUEO_S (y te avisa por WhatsApp). El código jamás se escribe en el log ni en Telegram.
# Cancelar siempre es posible (botón, «cancelar», /cancelar, «cancela el apagado», incluso por voz).

_apag: Optional[dict] = None          # solicitud en curso {"id","fase": confirmar|enviando|codigo,"expira","demora","codigo","intentos","mid"}
_apag_bloq = 0.0                      # epoch hasta el que el apagado remoto está bloqueado
_apag_lock = threading.Lock()         # el sondeo, el hilo de voz y los botones tocan este estado


def _modulo_apagado():
    try:
        import apagado
        return apagado
    except Exception:
        return None


def _evento_pc(texto: str, nivel: str = "aviso", voz: Optional[str] = None) -> None:
    """Aviso en la ventana de SARA (y en voz alta en el PC): quien esté frente a la laptop debe enterarse."""
    d = {"texto": texto, "nivel": nivel}
    if voz:
        d["voz"] = voz
    _eventos.append(("notificacion", d))


def _whatsapp_listo() -> bool:
    try:
        import whatsapp
        return whatsapp.estado().get("estado") == "abierto"
    except Exception:
        return False


def _avisar_whatsapp(texto: str) -> bool:
    try:
        import whatsapp
        return bool(whatsapp.notificar_usuario(texto))
    except Exception as e:
        _log_error("apagado→whatsapp", e)
        return False


def _cancelar_apagado_todo(forzar: bool = False) -> Optional[str]:
    """Aborta la solicitud pendiente y/o la cuenta atrás de Windows. Devuelve el aviso, o None si no había nada.
    forzar=True intenta `shutdown /a` aunque SARA no recuerde haber programado nada (p. ej. tras reiniciar SARA)."""
    global _apag
    with _apag_lock:
        a, _apag = _apag, None
    partes: List[str] = []
    if a and time.time() <= a["expira"]:
        partes.append("Solicitud de apagado cancelada.")
    ap = _modulo_apagado()
    if ap is not None and (forzar or ap.programado() is not None):
        partes.append(ap.cancelar()[1])
    if partes:
        _evento_pc("🛑 " + " ".join(partes), "exito")
    return " ".join(partes) or None


def _intentar_apagado(orden: str, voz: bool = False) -> bool:
    """True = la frase era de apagar/cancelar el apagado y ya se atendió (no sigue al núcleo)."""
    ap = _modulo_apagado()
    if ap is None:
        return False
    try:
        plan = ap.interpretar(orden)
    except Exception as e:
        _log_error("apagado.interpretar", e)
        return False
    if plan is None:
        return False
    try:
        _atender_apagado(ap, plan, voz)
    except Exception as e:
        _log_error("apagado", e)
        _responder("⚠️ Error interno con el apagado; no apagué nada.")
    return True


def _atender_apagado(ap, plan: dict, voz: bool) -> None:
    global _apag
    if plan["accion"] == "cancelar":                         # cancelar es lo seguro: vale por voz y sin código
        _responder("🛑 " + (_cancelar_apagado_todo(forzar=True) or "No hay ningún apagado programado ni pendiente."))
        return
    accion = plan.get("accion", "apagar")
    if voz:                                                  # una orden mal oída NO debe iniciar nada
        _responder("🎙️ Por seguridad no hago eso por nota de voz (un error de transcripción podría apagar o dormir tu equipo). "
                   "Escríbelo, por ejemplo: «apaga la computadora» o «suspende la laptop».")
        return
    if not _c("APAGADO_ACTIVO", True) or not _c("APAGADO_REMOTO_PERMITIDO", True):
        _responder("🔒 El apagado remoto está desactivado en la configuración de SARA. Hazlo desde el PC.")
        return
    ahora = time.time()
    if ahora < _apag_bloq:
        _responder(f"🔒 El apagado remoto está bloqueado por intentos fallidos (faltan ~{int((_apag_bloq - ahora) // 60) + 1} min). "
                   "Hazlo desde el PC.")
        return
    with _apag_lock:
        a = _apag
        if a and ahora <= a["expira"]:
            _responder("Ya hay una solicitud de apagado en curso: respóndela o escribe /cancelar.")
            return
    pide_codigo = ap.requiere_codigo(accion) and _verif_activa()
    if pide_codigo and _modo_codigo() == "totp" and not ap.totp_configurado():
        _responder("🔒 El apagado remoto usa tu app autenticadora y aún no está configurada. En el PC, en la carpeta de SARA, "
                   "ejecuta una vez:  python apagado.py totp  y añade la clave a Google/Microsoft Authenticator. "
                   "Mientras tanto no apago nada desde aquí.")
        return
    if pide_codigo and _modo_codigo() != "totp" and not _whatsapp_listo():
        _responder("🔒 El apagado remoto necesita tu WhatsApp enlazado (ahí te llega el código) y ahora no lo está, así que "
                   "por seguridad no apago nada. Enlázalo con /whatsapp en la ventana de SARA o hazlo desde el PC.")
        return
    demora = 0 if accion == "bloquear" else ap.normalizar_demora(plan.get("demora_s"), remoto=True)
    seg = int(_c("APAGADO_CONFIRM_S", 60))
    idc = secrets.token_hex(4)
    with _apag_lock:
        _apag = {"id": idc, "fase": "confirmar", "expira": ahora + seg + 5, "demora": demora, "accion": accion,
                 "codigo": "", "intentos": 0, "mid": None}
    teclado = [[{"text": ap.etiqueta_boton(accion), "callback_data": f"a|{idc}|si"},
                {"text": "✖ Cancelar", "callback_data": f"a|{idc}|no"}]]
    aviso = (ap.advertencia(demora, remoto=True, modo=_modo_codigo(), accion=accion) if _verif_activa()
             else _advertencia_sin_codigo(ap, demora, accion))
    mid = _enviar(aviso + f"\n({seg} s para responder)", teclado)
    with _apag_lock:
        if mid is None:
            if _apag and _apag["id"] == idc:
                _apag = None
        elif _apag and _apag["id"] == idc:
            _apag["mid"] = mid
    if mid is not None:
        _evento_pc(f"⚠️ Alguien con tu Telegram pidió {ap.etiqueta(accion)} (falta tu confirmación"
                   f"{' y el código de 2º factor' if pide_codigo else ''}).", "aviso", f"Se pidió {ap.etiqueta(accion)} por Telegram.")


def _quitar_botones_de(msg: dict) -> None:
    owner = _owner_id()
    if owner is not None and msg.get("message_id"):
        _api("editMessageReplyMarkup", {"chat_id": owner, "message_id": msg["message_id"],
                                        "reply_markup": {"inline_keyboard": []}}, timeout=8)


def _boton_apagado(cq: dict, partes: List[str]) -> None:
    """Botones «a|<id>|si» / «a|<id>|no». El llamador ya comprobó que es el dueño."""
    global _apag
    msg = cq.get("message") or {}
    cid = cq.get("id")
    with _apag_lock:
        a = _apag
        valido = bool(len(partes) == 3 and a and a["fase"] == "confirmar" and a["id"] == partes[1]
                      and time.time() <= a["expira"])
        if valido and partes[2] == "no":
            _apag = None
        elif valido and partes[2] == "si":
            a["fase"] = "enviando"                           # un segundo toque no genera un segundo código
        elif valido:
            return
    if not valido:
        _api("answerCallbackQuery", {"callback_query_id": cid, "text": "Esa solicitud ya expiró."}, timeout=8)
        _quitar_botones_de(msg)
        return
    _quitar_botones_de(msg)
    if partes[2] == "no":
        _api("answerCallbackQuery", {"callback_query_id": cid, "text": "Cancelado"}, timeout=8)
        _responder("Cancelado. No apagué nada.")
        _evento_pc("🛑 Solicitud de apagado remoto cancelada.", "exito")
        return
    ap = _modulo_apagado()
    accion = a.get("accion", "apagar")
    if ap is not None and (not ap.requiere_codigo(accion) or not _verif_activa()):
        with _apag_lock:
            if _apag is a:
                _apag = None                                  # solo botón: confirmación única
        _api("answerCallbackQuery", {"callback_query_id": cid, "text": "Confirmado"}, timeout=8)
        ok, txt = ap.programar(a["demora"], "telegram", accion)
        _responder(("✅ " if ok else "⚠️ ") + txt)
        _evento_pc(("⏻ " if ok else "⚠️ ") + f"{accion.capitalize()} por Telegram: " + txt, "aviso" if ok else "error",
                   (txt if ok else None))
        return
    _api("answerCallbackQuery", {"callback_query_id": cid, "text": "Confirmado: te pido el código" if _modo_codigo() == "totp" else "Confirmado: te mando el código"}, timeout=8)
    _enviar_codigo_apagado(a)


def _verif_activa() -> bool:
    """¿El protocolo de verificación (código TOTP) está encendido? (interruptores.py; por defecto sí)"""
    try:
        import interruptores
        return interruptores.verificacion_activa()
    except Exception:
        return True


def _advertencia_sin_codigo(ap, demora: int, accion: str) -> str:
    """Aviso del apagado remoto cuando la verificación está APAGADA: solo botón, sin hablar de códigos."""
    dur = ap.duracion_texto(demora)
    if accion == "bloquear":
        return "⚠️ BLOQUEAR LA SESIÓN\nSi confirmas, Windows se bloquea ahora; SARA sigue funcionando.\n¿Confirmas?"
    futuro = {"apagar": "se apagará", "reiniciar": "se reiniciará", "suspender": "se suspenderá", "hibernar": "se hibernará"}.get(accion, "se apagará")
    extra = ("Se cerrarán TODAS las aplicaciones y se perderá lo que no esté guardado." if accion in ("apagar", "reiniciar")
             else "Desde ese momento no podré responderte hasta que abras la tapa o pulses el botón de encendido.")
    return f"⚠️ {ap.etiqueta(accion).upper()} (REMOTO · verificación apagada)\nSi confirmas, el equipo {futuro} en {dur}. {extra}\n¿Confirmas?"


def _modo_codigo() -> str:
    ap = _modulo_apagado()
    try:
        return ap.modo_codigo() if ap else "whatsapp"
    except Exception:
        return "whatsapp"


def _digitos_codigo(modo: str) -> int:
    return 6 if modo == "totp" else max(4, min(8, int(_c("APAGADO_CODIGO_DIGITOS", 5))))


def _enviar_codigo_apagado(a: dict) -> None:
    global _apag
    if _modo_codigo() == "totp":
        # Sin envío: el código lo genera la app autenticadora del celular. Nada viaja por ningún canal.
        valida = int(_c("APAGADO_CODIGO_VALIDEZ_S", 120))
        with _apag_lock:
            if _apag is a:
                a["fase"], a["modo"], a["codigo"], a["expira"], a["intentos"] = "codigo", "totp", None, time.time() + valida, 0
        _responder(f"🔐 Escribe aquí el código de 6 dígitos que muestra tu app autenticadora (cuenta «SARA») "
                   f"en los próximos {valida} s para {(_modulo_apagado().etiqueta(a.get('accion', 'apagar')) if _modulo_apagado() else 'apagar el equipo')} ({int(_c('APAGADO_CODIGO_INTENTOS', 3))} intentos). "
                   f"Para abortar: /cancelar.")
        return
    n = max(4, min(8, int(_c("APAGADO_CODIGO_DIGITOS", 5))))
    valida = int(_c("APAGADO_CODIGO_VALIDEZ_S", 120))
    codigo = f"{secrets.randbelow(10 ** n):0{n}d}"
    ap = _modulo_apagado()
    dur = ap.duracion_texto(valida) if ap else f"{valida} s"
    enviado = _avisar_whatsapp(f"🔐 SARA · código para apagar tu equipo: {codigo}\n"
                               f"Vale {dur}. Escríbelo en tu chat de Telegram con el bot. Si no lo pediste, ignóralo y no lo compartas.")
    with _apag_lock:
        if not enviado:
            if _apag is a:
                _apag = None
        elif _apag is a:
            a["fase"], a["codigo"], a["expira"], a["intentos"] = "codigo", codigo, time.time() + valida, 0
    if not enviado:
        _responder("⚠️ No pude mandar el código a tu WhatsApp, así que por seguridad no apago nada. "
                   "Revisa que WhatsApp siga enlazado (/whatsapp en la ventana de SARA) y vuelve a pedirlo.")
        _evento_pc("⚠️ No pude mandar el código de apagado a tu WhatsApp; no se apagó nada.", "aviso")
        return
    _responder(f"🔐 Te mandé un código de {n} dígitos a tu WhatsApp (tu chat «Tú»). Escríbelo aquí en los próximos {dur} "
               f"para apagar el equipo ({int(_c('APAGADO_CODIGO_INTENTOS', 3))} intentos). Para abortar: /cancelar.")


def _intentar_codigo_apagado(texto: str) -> bool:
    """True = el mensaje era para el apagado (código o «no»). Cualquier otra frase sigue su camino normal y la
    solicitud sigue esperando su código."""
    global _apag, _apag_bloq
    ap_ = _apag
    n = _digitos_codigo("totp" if (ap_ and ap_.get("modo") == "totp") else "whatsapp")
    t = re.sub(r"\s+", "", texto or "")
    es_codigo = bool(re.fullmatch(r"[0-9]{%d}" % n, t))
    with _apag_lock:
        a = _apag
        if not a or a["fase"] != "codigo":
            return False
        vencida = time.time() > a["expira"]
        if vencida:
            _apag = None
    if vencida:
        _responder("⌛ La solicitud de apagado venció; no apagué nada. Si aún quieres apagar, pídelo de nuevo.")
        return es_codigo
    if not es_codigo:
        p = re.sub(r"[^a-z0-9 ]", "", _plano(texto)).strip()
        if p in _NO:
            with _apag_lock:
                _apag = None
            _responder("Cancelado. No apagué nada.")
            _evento_pc("🛑 Solicitud de apagado remoto cancelada.", "exito")
            return True
        return False
    maxint = max(1, int(_c("APAGADO_CODIGO_INTENTOS", 3)))
    with _apag_lock:
        a = _apag
        if a is None or a["fase"] != "codigo":
            return True
        if a.get("modo") == "totp":
            ap0 = _modulo_apagado()
            bueno = bool(ap0 and ap0.totp_verificar(t))
        else:
            bueno = hmac.compare_digest(t, a["codigo"])
        quedan = 0
        if bueno:
            _apag = None                                      # un solo uso
        else:
            a["intentos"] += 1
            quedan = maxint - a["intentos"]
            if quedan <= 0:
                _apag = None
                _apag_bloq = time.time() + int(_c("APAGADO_BLOQUEO_S", 600))
    if bueno:
        ap = _modulo_apagado()
        ok, txt = (ap.programar(a["demora"], "telegram", a.get("accion", "apagar")) if ap
                   else (False, "Falta apagado.py; no hice nada."))
        _responder(("✅ Código correcto. " if ok else "⚠️ ") + txt)
        _evento_pc(("⏻ " if ok else "⚠️ ") + "Apagado por Telegram: " + txt, "aviso" if ok else "error",
                   (txt if ok else None))
        return True
    if quedan > 0:
        _responder(f"❌ Código incorrecto. Te quedan {quedan} intento{'s' if quedan != 1 else ''}.")
        return True
    bloqueo = int(_c("APAGADO_BLOQUEO_S", 600))
    _responder(f"❌ Código incorrecto otra vez. Cancelé la solicitud y bloqueé el apagado remoto {max(1, bloqueo // 60)} min.")
    _evento_pc("🚨 Varios códigos de apagado incorrectos por Telegram: solicitud cancelada y apagado remoto bloqueado.",
               "error", "Atención: hubo códigos de apagado incorrectos por Telegram. El apagado remoto quedó bloqueado.")
    _avisar_whatsapp(f"🚨 SARA: hubo {maxint} códigos incorrectos para apagar tu equipo por Telegram. "
                     f"El apagado remoto quedó bloqueado {max(1, bloqueo // 60)} min. Si no fuiste tú, revisa tu Telegram.")
    return True


# ─── DESBLOQUEO TOTP (envío de archivos y pantalla; ver desbloqueo.py) ─────────────────────────────────

_pend_unlock: Optional[dict] = None


def _pedir_desbloqueo(orden: str) -> None:
    global _pend_unlock
    _pend_unlock = {"orden": orden, "expira": time.time() + int(_c("DESBLOQUEO_PEDIR_S", 120))}


def _intentar_desbloqueo(texto: str) -> bool:
    """True = era el código de desbloqueo. Si acierta, se ejecuta la orden que lo pidió."""
    global _pend_unlock
    p = _pend_unlock
    if not p:
        return False
    if time.time() > p["expira"]:
        _pend_unlock = None
        return False
    t = re.sub(r"\s+", "", texto or "")
    if not re.fullmatch(r"[0-9]{6}", t):
        return False
    import desbloqueo
    ok, msg = desbloqueo.intentar(t)
    if ok:
        _pend_unlock = None
        _responder(msg)
        _encolar(p["orden"], time.time())
        return True
    if desbloqueo.bloqueado():
        _pend_unlock = None
    _responder(msg)
    return True


# ─── NOTAS DE VOZ ─────────────────────────────────────────────────────────

def _voz_max_s() -> int:
    return max(5, int(_c("TG_VOZ_MAX_S", 30)))


def _atender_voz(m: dict) -> None:
    if not _voz_on():
        _responder("🎙️ Las notas de voz están apagadas. Actívalas desde la ventana de SARA con /telegram voz on.")
        return
    v = m.get("voice") or m.get("audio") or {}
    if int(v.get("duration") or 0) > _voz_max_s():
        _stats["voz_errores"] += 1
        _responder(f"🎙️ Tu nota de voz es demasiado larga (máximo {_voz_max_s()} s); no la procesé.")
        return
    if int(v.get("file_size") or 0) > int(_c("TG_VOZ_MAX_BYTES", 3_000_000)):
        _stats["voz_errores"] += 1
        _responder("🎙️ Ese audio pesa demasiado; no lo procesé.")
        return
    try:
        _asegurar_trabajador("sara-tg-voz")
        _cola_voz.put_nowait((v.get("file_id"), m.get("date")))
    except queue.Full:
        _responder("🎙️ Sigo transcribiendo notas anteriores; reenvía la tuya en un momento.")


def _descargar(file_id: str) -> Optional[str]:
    """Baja la nota a una carpeta temporal con nombre PROPIO (nunca el que dicta el remoto). None si falla."""
    j = _api("getFile", {"file_id": file_id}, timeout=15)
    if not j or not j.get("ok"):
        return None
    ruta_remota = str((j.get("result") or {}).get("file_path") or "")
    ext = os.path.splitext(ruta_remota)[1].lower()
    ext = ext if ext in (".oga", ".ogg", ".opus", ".mp3", ".m4a", ".wav") else ".oga"
    os.makedirs(_DIR_AUDIO, exist_ok=True)
    destino = os.path.join(_DIR_AUDIO, f"nota_{secrets.token_hex(6)}{ext}")
    tope = int(_c("TG_VOZ_MAX_BYTES", 3_000_000))
    try:
        with _http_session().get(f"https://api.telegram.org/file/bot{_token()}/{ruta_remota}",
                                 stream=True, timeout=30) as r:
            if r.status_code != 200:
                return None
            total = 0
            with open(destino, "wb") as f:
                for trozo in r.iter_content(65536):
                    total += len(trozo)
                    if total > tope:
                        raise ValueError("audio demasiado grande")
                    f.write(trozo)
        return destino
    except Exception as e:
        _log_error("_descargar", e)
        try:
            os.remove(destino)
        except OSError:
            pass
        return None


def _procesar_nota_interna(file_id: str, ts) -> None:
    global _ultima_voz
    st = _modulo_stt()
    if st is None:
        _stats["voz_errores"] += 1
        _responder("🎙️ No puedo transcribir: falta stt.py (versión con transcribir_archivo) o faster-whisper.")
        return
    _accion("typing")
    ruta = _descargar(file_id)
    if not ruta:
        _stats["voz_errores"] += 1
        _responder("🎙️ No pude descargar tu nota de voz. Reenvíala.")
        return
    try:
        texto = st.transcribir_archivo(ruta, max_s=_voz_max_s() + 5)
    finally:
        try:
            os.remove(ruta)                                  # el audio nunca se queda en disco
        except OSError:
            pass
    if texto is None:
        _stats["voz_errores"] += 1
        _responder("🎙️ No pude transcribir tu nota de voz (revisa /stt y el log).")
        return
    texto = str(texto).strip()
    _ultima_voz = texto
    if not texto:
        _responder("🎙️ No entendí nada en tu nota de voz.")
        return
    ap_pend = _apag
    if ap_pend and time.time() <= ap_pend["expira"]:
        p = re.sub(r"[^a-z0-9 ]", "", _plano(_extraer_orden(texto, True) or texto)).strip()
        if p in _NO:
            _cancelar_apagado_todo()
            _responder("Cancelado. No apagué nada.")
        else:
            _responder("Hay una solicitud de apagado pendiente: respóndela con los botones o escribe el código. "
                       "Por voz solo acepto cancelar.")
        return
    c = _conf
    if c and time.time() <= c["expira"]:
        p = re.sub(r"[^a-z0-9 ]", "", _plano(_extraer_orden(texto, True) or texto)).strip()
        if p in _NO:
            _intentar_confirmacion(p)
        else:
            _responder("Hay una confirmación pendiente: respóndela con los botones. Por voz solo acepto cancelar.")
        return
    orden = _orden_de(texto, voz=True)
    if orden is None:
        _responder("🎙️ Tu nota no empezó con «Sara» y tengo esa palabra obligatoria; no ejecuté nada.")
        return
    _encolar(orden, ts, voz=True)


def _procesar_nota(file_id: str, ts) -> None:
    _gui_inicio("🎙️ nota de voz")
    try:
        _procesar_nota_interna(file_id, ts)
    finally:
        _gui_fin(True)


def _voz_trabajador() -> None:
    """Hilo propio: transcribir tarda segundos y no debe frenar el sondeo (ahí se leen tus botones)."""
    while True:
        item = _cola_voz.get()
        if item is _PARAR:
            if not _net_on():
                return
            continue
        file_id, ts = item
        if not _net_on():
            continue
        try:
            _procesar_nota(file_id, ts)
        except Exception as e:
            _log_error("_procesar_nota", e)
            _responder("🎙️ Error interno al procesar tu nota de voz.")


def _precargar_stt() -> None:
    try:
        st = _modulo_stt()
        if st is not None and hasattr(st, "precargar_modelo"):
            st.precargar_modelo()
    except Exception as e:
        _log_error("_precargar_stt", e)


# ─── VINCULACIÓN (código de un solo uso) ──────────────────────────────────

def _intentar_vinculo(m: dict) -> None:
    global _vinculo
    v = _vinculo
    if not v:
        return
    frm = m.get("from") or {}
    texto = re.sub(r"^/start\s+", "", str(m.get("text") or "").strip(), flags=re.I).strip()
    if not frm.get("id") or frm.get("is_bot"):
        return
    if re.fullmatch(r"/start(?:@\w+)?", texto, flags=re.I):
        return                                               # el «Iniciar» del bot no es un intento fallido
    if not re.fullmatch(r"\d{8}", texto):
        v["intentos"] += 1                                   # cualquier otra cosa cuenta como intento fallido (sin responder)
    elif hmac.compare_digest(texto, v["codigo"]):
        _vinculo = None
        _kv_set("owner", str(frm["id"]))
        _kv_set("owner_nombre", str(frm.get("first_name") or "")[:40])
        log.info("Telegram: cuenta vinculada")
        _responder("✅ Vinculado. Solo obedeceré a esta cuenta. Desde la ventana de SARA activa el canal con /telegram net on.")
        _eventos.append(("notificacion", {"texto": "✅ Telegram vinculado. Activa el canal con /telegram net on.",
                                          "nivel": "exito", "voz": "Telegram vinculado."}))
        return
    else:
        v["intentos"] += 1
    if v["intentos"] >= 5:
        _vinculo = None
        log.warning("Telegram: vinculación cancelada por demasiados intentos fallidos")
        _eventos.append(("notificacion", {"texto": "⚠️ Vinculación de Telegram cancelada: demasiados intentos con código incorrecto.",
                                          "nivel": "aviso"}))


def _cmd_vincular() -> Tuple[bool, str]:
    global _vinculo, _sincronizado, _bot_user
    if not _token():
        return False, _AYUDA_TOKEN
    j = _api("getMe", timeout=10)
    if not j or not j.get("ok"):
        return False, f"No pude contactar a Telegram con ese token ({_ultimo_error or 'sin respuesta'}). Revisa .env y tu internet."
    _bot_user = str((j.get("result") or {}).get("username") or "")
    _vinculo = {"codigo": f"{secrets.randbelow(10 ** 8):08d}", "expira": time.time() + 300, "intentos": 0}
    _sincronizado = False
    _asegurar_hilos()
    _esperar_sync(8)                                         # ya escuchando antes de que escribas el código
    previo = " (esto reemplazará a la cuenta ya vinculada)" if _owner_id() else ""
    return True, (f"Código de vinculación: {_vinculo['codigo']} (válido 5 min, un solo uso){previo}.\n"
                  f"En Telegram abre t.me/{_bot_user} → pulsa Iniciar y envíame ese código tal cual. "
                  "Te confirmo aquí y en el chat.")


# ─── /telegram (desde la ventana de SARA) ─────────────────────────────────

_AYUDA_TOKEN = ("Falta el token del bot. Pasos: 1) en Telegram abre @BotFather → /newbot → elige nombre y usuario; "
                "2) copia el token que te da; 3) en el archivo .env de SARA añade la línea "
                "TELEGRAM_BOT_TOKEN=tu_token; 4) cierra SARA por completo y ábrela; 5) escribe /telegram vincular.")


def _onoff(tok: str) -> Optional[bool]:
    return True if tok in ("on", "si", "sí", "activar", "1") else False if tok in ("off", "no", "apagar", "0") else None


def _resumen() -> str:
    own = _owner_id()
    if not _token():
        return "Telegram: " + _AYUDA_TOKEN
    if own is None:
        return "Telegram: bot configurado pero sin cuenta vinculada. Escribe /telegram vincular."
    return (f"Telegram vinculado. Canal {'ACTIVO' if _net_on() else 'apagado'}. Desde el chat con tu bot puedes pedirme "
            "cualquier cosa del PC o conversar, por texto o nota de voz.\n"
            "Ajustes: /telegram net on|off · voz on|off · audio on|off · bocina on|off · avisos on|off · palabra on|off · "
            "estado · vincular · desvincular")


def diagnostico() -> str:
    own = _owner_id()
    s = _stats
    bloq = ", ".join(_c("TG_CATEGORIAS_BLOQUEADAS", ("COMANDO_SISTEMA", "ELIMINAR_ARCHIVO"))) or "ninguna"
    return ("Telegram:\n"
            f"Versión de telegram_bot.py: {TELEGRAM_VERSION}\n"
            f"Token: {'configurado' if _token() else 'FALTA (TELEGRAM_BOT_TOKEN en .env)'}"
            f"{' · bot @' + _bot_user if _bot_user else ''} · hilos: {'sí' if _hilos_ok else 'no'}\n"
            f"Cuenta vinculada: {'sí (' + (_kv_get('owner_nombre') or 'sin nombre') + ')' if own else 'no'}"
            f"{' · vinculación en curso' if _vinculando() else ''}\n"
            f"Canal: {'ACTIVO' if _net_on() else 'apagado'} · notas de voz: {'sí' if _voz_on() else 'no'} (máx. {_voz_max_s()} s) · "
            f"respuesta en audio: {'sí' if _audio_on() else 'no'} · bocina del PC: {'sí' if bocina_activa() else 'no'} · "
            f"avisos: {'sí' if _avisos_on() else 'no'} · palabra «Sara» obligatoria: {'sí' if _palabra_on() else 'no'}\n"
            f"Órdenes aceptadas {s['aceptados']} (voz {s['voz_aceptadas']}) · ajenos {s['ajenos']} · viejos {s['viejos']} · "
            f"reenviados {s['reenviados']} · botones {s['botones']} · errores de voz {s['voz_errores']} · "
            f"último descarte: {_ultimo_descarte}"
            f"{' · última oída: «' + _ultima_voz[:80] + '»' if _ultima_voz else ''}\n"
            f"Bloqueado por este canal: {bloq} · último error de red: {_ultimo_error or 'ninguno'}\n"
            f"Apagado remoto: {'permitido' if _c('APAGADO_REMOTO_PERMITIDO', True) and _c('APAGADO_ACTIVO', True) else 'desactivado'}"
            f"{' · BLOQUEADO ' + str(int(_apag_bloq - time.time()) // 60 + 1) + ' min por intentos fallidos' if time.time() < _apag_bloq else ''}"
            f"{' · solicitud en curso' if _apag else ''}")


def comando_slash(arg: str) -> Tuple[bool, str]:
    if not _activo():
        return False, "El módulo de Telegram está desactivado (TG_ACTIVO)."
    a = (arg or "").strip().lstrip(":").strip().lower()
    partes = a.split()
    sub = partes[0] if partes else ""
    val = _onoff(partes[1]) if len(partes) > 1 else None
    if not a:
        return True, _resumen()
    if sub in ("estado", "diagnostico", "diagnóstico"):
        return True, diagnostico()
    if sub in ("vincular", "enlazar"):
        return _cmd_vincular()
    if sub in ("desvincular", "olvidar", "desconectar", "salir"):
        for k in ("owner", "owner_nombre"):
            _kv_del(k)
        _kv_set("net", "0")
        return True, "Telegram desvinculado y canal apagado. Si quieres revocar el bot por completo: @BotFather → /revoke."
    if sub in ("on", "off") or (sub == "net" and val is not None):
        on = (sub == "on") if sub in ("on", "off") else bool(val)
        return _set_net(on)
    if sub in ("voz", "audio", "bocina", "avisos", "palabra", "pc") and val is not None:
        return _set_flag("bocina" if sub == "pc" else sub, bool(val))
    return True, _resumen() + "\n" + diagnostico()


def _set_net(on: bool) -> Tuple[bool, str]:
    global _sincronizado
    if not on:
        _kv_set("net", "0")
        _sincronizado = False
        _poll_wake.set()
        _detener_trabajadores()
        return True, "Canal de Telegram apagado: ya no obedezco órdenes por ahí."
    if not _token():
        return False, _AYUDA_TOKEN
    if _owner_id() is None:
        return False, "Primero vincula tu cuenta con /telegram vincular."
    _sincronizado = False                                    # al encender se salta lo anterior: nada viejo se ejecuta
    _kv_set("net", "1")
    _asegurar_hilos()
    if not _esperar_sync(8):
        return False, f"Quedó activado pero no pude conectar con Telegram ({_ultimo_error or 'sin respuesta'}). Reviso solo cada pocos segundos."
    bloq = ", ".join(_c("TG_CATEGORIAS_BLOQUEADAS", ("COMANDO_SISTEMA", "ELIMINAR_ARCHIVO"))) or "ninguna"
    _enviar("✅ SARA conectada. Escríbeme o mándame una nota de voz (si la activaste).")
    return True, ("Canal de Telegram ACTIVO. Abre el chat con tu bot y escribe lo que quieras («abre Chrome», «¿cuánta batería "
                  f"tengo?»). Las acciones de riesgo te piden confirmación con botones. Bloqueado por este canal: {bloq}. "
                  "Notas de voz: /telegram voz on · respuestas en audio: /telegram audio on.")


def _set_flag(nombre: str, on: bool) -> Tuple[bool, str]:
    if nombre == "voz" and on:
        if _modulo_stt() is None:
            return False, "No puedo activar las notas de voz: falta stt.py (versión con transcribir_archivo) o faster-whisper."
        threading.Thread(target=_precargar_stt, name="sara-tg-precarga", daemon=True).start()
    _kv_set(nombre, "1" if on else "0")
    estado = "activado" if on else "apagado"
    textos = {
        "voz": f"Notas de voz {estado} (máx. {_voz_max_s()} s): graba una nota en el chat del bot; te contesto primero "
               "«🎙️ Entendí: …» y luego ejecuto. Por voz, una confirmación solo se puede cancelar.",
        "audio": f"Respuesta en audio {estado}: además del texto, te mando la respuesta como nota de voz con la voz de SARA.",
        "bocina": f"Bocina del PC {estado}: las respuestas a tus órdenes de Telegram "
                  f"{'también se dicen' if on else 'ya no se dicen'} en voz alta en la laptop.",
        "avisos": f"Avisos {estado}: los recordatorios vencidos {'también' if on else 'ya no'} te llegan al chat del bot.",
        "palabra": f"Palabra de activación {estado}: " + ("cada mensaje debe empezar con «Sara»." if on else "ya no hace falta decir «Sara»."),
    }
    extra = "" if _net_on() or nombre in ("bocina",) else " OJO: el canal está apagado; actívalo con /telegram net on."
    return True, textos.get(nombre, f"{nombre} {estado}.") + extra


# ─── API PARA core_bridge ─────────────────────────────────────────────────

def enviar_documento(ruta: str, caption: str = "") -> bool:
    owner = _owner_id()
    if owner is None:
        return False
    try:
        with open(ruta, "rb") as f:
            j = _api("sendDocument", {"chat_id": owner, "caption": caption[:200]}, timeout=180,
                     archivos={"document": (os.path.basename(ruta), f)})
        return bool(j and j.get("ok"))
    except Exception as e:
        _log_error("enviar_documento", e)
        return False


def enviar_foto(png: bytes, caption: str = "", nombre: str = "pantalla.png") -> bool:
    owner = _owner_id()
    if owner is None:
        return False
    try:
        if len(png) < 9_000_000:
            j = _api("sendPhoto", {"chat_id": owner, "caption": caption[:200]}, timeout=60, archivos={"photo": (nombre, png)})
        else:
            j = _api("sendDocument", {"chat_id": owner, "caption": caption[:200]}, timeout=90, archivos={"document": (nombre, png)})
        return bool(j and j.get("ok"))
    except Exception as e:
        _log_error("enviar_foto", e)
        return False


def enviar_alerta(texto: str) -> bool:
    """Alertas proactivas (alertas.py). Respeta el maestro ALERTAS_ACTIVAS; no depende de /telegram avisos."""
    if not _activo() or not texto or not _c("ALERTAS_ACTIVAS", True) or _owner_id() is None:
        return False
    try:
        return _enviar(str(texto)[:1000]) is not None
    except Exception as e:
        _log_error("enviar_alerta", e)
        return False


def notificar_usuario(texto: str) -> bool:
    """SARA → tu chat con el bot (recordatorios vencidos). Solo si activaste /telegram avisos on. Silencioso si no."""
    if not _activo() or not texto or not _c("ALERTAS_ACTIVAS", True) or not _avisos_on() or _owner_id() is None:
        return False
    try:
        return _enviar(str(texto)[:1000]) is not None
    except Exception as e:
        _log_error("notificar_usuario", e)
        return False


def eventos_proactivos() -> List[Tuple[str, dict]]:
    out: List[Tuple[str, dict]] = []
    while _eventos:
        out.append(_eventos.popleft())
    return out


if __name__ == "__main__":
    print(diagnostico())