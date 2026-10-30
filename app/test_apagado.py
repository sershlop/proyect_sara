"""Pruebas del apagado con WhatsApp, Windows y Telegram SIMULADOS (no apaga nada, no toca la red)."""
import ast
import os
import re
import sys
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("TELEGRAM_BOT_TOKEN", None)

fallos = []


def comprobar(nombre, condicion, detalle=""):
    print(("  ✅ " if condicion else "  ❌ ") + nombre + ("" if condicion else f"   → {detalle}"))
    if not condicion:
        fallos.append(nombre)


# ─────────────────────────── 1. el intérprete de frases ───────────────────────────
print("\n[1] apagado.interpretar")
import apagado as ap

DEBE_APAGAR = {
    "apaga la computadora": None, "Apaga la compu": None, "apágame la laptop": None, "apaga el equipo": None,
    "Sara, apaga la computadora por favor": None, "oye sara apaga el pc": None, "puedes apagar la laptop": None,
    "quiero que apagues la compu": None, "apaga el equipo en 5 minutos": 300, "apaga la compu en cinco minutos": 300,
    "apaga la computadora en media hora": 1800, "apaga la laptop en una hora": 3600, "apaga el pc dentro de 90 segundos": 90,
    "apagar la computadora ya": None, "apaga mi computadora ahora mismo": None, "apaga el sistema": None,
}
for frase, dem in DEBE_APAGAR.items():
    r = ap.interpretar(frase)
    comprobar(f"apagar ← «{frase}»", bool(r) and r["accion"] == "apagar" and r["demora_s"] == dem, str(r))

NO_DEBE = ["apaga la música", "apaga chrome", "apaga el volumen", "apaga el wifi", "apaga", "apágate",
           "cómo apago la compu", "por qué se apaga la compu", "se apaga sola la laptop",
           "apaga la computadora y abre chrome", "mándale a Juan que apague la compu", "abre la computadora",
           "cierra la computadora", "apaga la luz", "apaga la tele", "qué hace apaga la computadora",
           "ejecuta el comando Stop-Computer", "shutdown /s /t 0", "apaga todo"]
for frase in NO_DEBE:
    comprobar(f"NO es apagado ← «{frase}»", ap.interpretar(frase) is None, str(ap.interpretar(frase)))

for frase in ["cancela el apagado", "Cancelar apagado", "detén el apagado", "aborta el apagado", "sara cancela el apagado",
              "cancela el apagado de la computadora", "ya no apagues la compu", "no apagues la computadora"]:
    r = ap.interpretar(frase)
    comprobar(f"cancelar ← «{frase}»", bool(r) and r["accion"] == "cancelar", str(r))
for frase in ["cancela la alarma", "cancela el recordatorio", "para la música", "detén el lector", "cancela"]:
    comprobar(f"NO es cancelar ← «{frase}»", ap.interpretar(frase) is None, str(ap.interpretar(frase)))

# ─────────────────────────── 2. ejecución (shutdown.exe falso) ───────────────────────────
print("\n[2] apagado.programar / cancelar")
runs = []
rc_siguiente = [0]
ap.usar_ejecutor(lambda a: (runs.append(a) or (rc_siguiente[0], "")))
ok, msg = ap.programar(None, "texto")
comprobar("programar por defecto = 30 s", ok and runs[-1][:3] == ["/s", "/t", "30"], str(runs[-1:]))
comprobar("el comentario explica cómo cancelar", "cancela el apagado" in runs[-1][4].lower() and runs[-1][3] == "/c")
comprobar("programado() cuenta hacia atrás", 0 < (ap.programado() or 0) <= 30)
ok, msg = ap.programar(5, "texto")
comprobar("local: mínimo 10 s", runs[-1][2] == "10", runs[-1][2])
ok, msg = ap.programar(5, "telegram")
comprobar("remoto: mínimo 30 s", runs[-1][2] == "30", runs[-1][2])
ok, msg = ap.programar(10 ** 7, "texto")
comprobar("máximo 24 h", runs[-1][2] == "86400", runs[-1][2])
rc_siguiente[0] = 1190
ok, msg = ap.programar(30, "texto")
comprobar("ya programado → aviso claro", not ok and "Ya hay un apagado" in msg, msg)
rc_siguiente[0] = 1116
ok, msg = ap.cancelar()
comprobar("cancelar sin nada que cancelar", not ok and "No hay ningún apagado" in msg, msg)
rc_siguiente[0] = 0
ok, msg = ap.cancelar()
comprobar("cancelar ejecuta shutdown /a", ok and runs[-1] == ["/a"] and ap.programado() is None, str(runs[-1]))
_os_apagado = ap.os
ap.os = types.SimpleNamespace(name="posix")
ap.usar_ejecutor(None)
try:
    ok, msg = ap.programar(30, "texto")
finally:
    ap.os = _os_apagado
comprobar("fuera de Windows no hace nada y lo dice", not ok and "Windows" in msg, msg)
ap.usar_ejecutor(lambda a: (runs.append(a) or (0, "")))

# ─────────────────────────── 3. flujo completo de Telegram ───────────────────────────
print("\n[3] telegram_bot: botones + código por WhatsApp")
import telegram_bot as tb
import config

store = {"owner": "111", "net": "1"}
tb._token = lambda: "123456:" + "A" * 35
tb._kv_get = lambda k, d=None: store.get(k, d)
tb._kv_set = lambda k, v: store.__setitem__(k, v)
api = []
_mid = [1000]


def fake_api(metodo, datos=None, timeout=15, archivos=None):
    api.append((metodo, datos or {}))
    _mid[0] += 1
    return {"ok": True, "result": {"message_id": _mid[0]}}


tb._api = fake_api
wa_enviados, wa_estado, wa_ok = [], [{"estado": "abierto"}], [True]
wa = types.SimpleNamespace(estado=lambda: wa_estado[0],
                           notificar_usuario=lambda t: (wa_enviados.append(t) or wa_ok[0]))
sys.modules["whatsapp"] = wa


def reset():
    tb._apag, tb._apag_bloq = None, 0.0
    api.clear(); wa_enviados.clear(); runs.clear()
    wa_estado[0], wa_ok[0] = {"estado": "abierto"}, True
    tb._eventos.clear(); tb._cmds.clear()
    while not tb._cola.empty():
        tb._cola.get_nowait()
    config.APAGADO_REMOTO_PERMITIDO = True
    config.APAGADO_CODIGO_MODO = "whatsapp"


_n = [0]


def escribe(texto):
    _n[0] += 1
    tb._atender({"message": {"chat": {"type": "private", "id": 111}, "from": {"id": 111, "is_bot": False},
                             "message_id": _n[0], "text": texto, "date": time.time()}})


def toca(data, de=111):
    tb._atender({"callback_query": {"id": "cq", "from": {"id": de}, "message": {"chat": {"id": de}, "message_id": 7},
                                    "data": data}})


def textos():
    return [d.get("text", "") for m, d in api if m == "sendMessage"]


def botones():
    for m, d in api:
        if m == "sendMessage" and d.get("reply_markup"):
            return [b["callback_data"] for fila in d["reply_markup"]["inline_keyboard"] for b in fila]
    return []


def codigo_wa():
    m = re.search(r"\b(\d{5})\b", wa_enviados[-1]) if wa_enviados else None
    return m.group(1) if m else None


def todo_telegram():
    return " ".join(textos())


# A. camino feliz
reset()
escribe("apaga la computadora")
b = botones()
comprobar("A1 pide confirmación con botones Sí / Cancelar", len(b) == 2 and b[0].endswith("|si") and b[1].endswith("|no"), str(b))
comprobar("A2 la advertencia dice que se pierde lo no guardado", "no esté guardado" in todo_telegram() and "WhatsApp" in todo_telegram())
comprobar("A3 todavía NO hay código ni apagado", not wa_enviados and not runs)
comprobar("A4 la ventana de SARA recibe aviso del intento", any("pidió apagar" in e[1]["texto"] for e in tb._eventos))
idc = b[0].split("|")[1]
toca(f"a|{idc}|si")
cod = codigo_wa()
comprobar("A5 al confirmar llega un código de 5 dígitos a WhatsApp", cod is not None and len(wa_enviados) == 1, str(wa_enviados))
comprobar("A6 el código NO aparece en Telegram", cod not in todo_telegram() and "5 dígitos" in todo_telegram())
comprobar("A7 todavía no apagó", not runs)
toca(f"a|{idc}|si")
comprobar("A8 un segundo toque no genera otro código", len(wa_enviados) == 1)
mal = "00000" if cod != "00000" else "11111"
escribe(mal)
comprobar("A9 código incorrecto → quedan 2 intentos y no apaga", "Te quedan 2" in textos()[-1] and not runs, textos()[-1])
escribe(cod)
comprobar("A10 código correcto → shutdown /s /t 30", runs and runs[-1][:3] == ["/s", "/t", "30"], str(runs))
comprobar("A11 responde con la cuenta atrás y cómo cancelar", "30 segundos" in textos()[-1] and "cancela el apagado" in textos()[-1], textos()[-1])
n_runs = len(runs)
escribe(cod)
comprobar("A12 el código es de un solo uso", len(runs) == n_runs)
escribe("cancela el apagado")
comprobar("A13 «cancela el apagado» ejecuta shutdown /a", runs[-1] == ["/a"], str(runs[-1]))

# B. botón con id equivocado / cancelar
reset()
escribe("apaga la computadora")
toca("a|zzzzzzzz|si")
comprobar("B1 botón con id ajeno → «ya expiró», sin código", not wa_enviados and any(m == "answerCallbackQuery" and "expiró" in d.get("text", "") for m, d in api))
idc = botones()[0].split("|")[1]
toca(f"a|{idc}|no")
comprobar("B2 botón Cancelar → no apaga ni manda código", not wa_enviados and not runs and tb._apag is None and "Cancelado" in textos()[-1])
toca(f"a|{idc}|si")
comprobar("B3 pulsar Sí después de cancelar no hace nada", not wa_enviados)
reset()
escribe("apaga la computadora"); idc = botones()[0].split("|")[1]
toca(f"a|{idc}|si", de=999)
comprobar("B4 un botón pulsado por OTRA cuenta se ignora", not wa_enviados and tb._apag and tb._apag["fase"] == "confirmar")

# C. fuerza bruta
reset()
escribe("apaga la computadora"); idc = botones()[0].split("|")[1]; toca(f"a|{idc}|si"); cod = codigo_wa()
mal = "00000" if cod != "00000" else "11111"
for _ in range(3):
    escribe(mal)
comprobar("C1 tras 3 fallos se cancela y se bloquea", tb._apag is None and tb._apag_bloq > time.time() and "bloqueé" in textos()[-1], textos()[-1])
comprobar("C2 avisa por WhatsApp del intento", len(wa_enviados) == 2 and "incorrectos" in wa_enviados[-1])
n = len(api)
escribe(cod)
comprobar("C3 ni el código bueno sirve después", not runs)
api.clear(); escribe("apaga la computadora")
comprobar("C4 una solicitud nueva está bloqueada", "bloqueado" in textos()[-1] and not botones())

# D. voz, desactivado, WhatsApp caído
reset()
tb._encolar("apaga la computadora", time.time(), voz=True)
comprobar("D1 por nota de voz NO inicia nada", not botones() and "voz" in textos()[-1] and tb._apag is None)
tb._encolar("cancela el apagado", time.time(), voz=True)
comprobar("D2 cancelar sí vale por voz", runs and runs[-1] == ["/a"])
reset(); config.APAGADO_REMOTO_PERMITIDO = False
escribe("apaga la computadora")
comprobar("D3 APAGADO_REMOTO_PERMITIDO=False → rechaza", not botones() and "desactivado" in textos()[-1], textos()[-1])
reset(); wa_estado[0] = {"estado": "apagado"}
escribe("apaga la computadora")
comprobar("D4 sin WhatsApp enlazado → no apaga (no hay segundo factor)", not botones() and "no apago" in textos()[-1], textos()[-1])
reset(); wa_ok[0] = False
escribe("apaga la computadora"); idc = botones()[0].split("|")[1]; toca(f"a|{idc}|si")
comprobar("D5 si falla el envío del código → aborta, NO lo manda por Telegram", tb._apag is None and not runs and "no pude mandar" in textos()[-1].lower(), textos()[-1])

# E. caducidad y otras frases mientras se espera
reset()
escribe("apaga la computadora"); idc = botones()[0].split("|")[1]; toca(f"a|{idc}|si"); cod = codigo_wa()
escribe("abre chrome")
comprobar("E1 otra orden mientras espera el código sigue su camino (al núcleo)", tb._cola.qsize() == 1 and tb._apag is not None)
tb._apag["expira"] = time.time() - 1
escribe(cod)
comprobar("E2 código vencido → no apaga", not runs and tb._apag is None and "venció" in textos()[-1], textos()[-1])
reset()
escribe("apaga la computadora"); idc = botones()[0].split("|")[1]; toca(f"a|{idc}|si")
escribe("no")
comprobar("E3 «no» cancela la solicitud", tb._apag is None and "Cancelado" in textos()[-1])
reset()
escribe("apaga la computadora"); api_n = len(api)
escribe("apaga la computadora")
comprobar("E4 segunda solicitud con una en curso → avisa", "en curso" in textos()[-1])
escribe("/cancelar")
comprobar("E5 /cancelar aborta la solicitud", tb._apag is None and "cancelada" in textos()[-1], textos()[-1])

# F. demoras remotas
reset()
escribe("apaga la compu en 10 minutos"); idc = botones()[0].split("|")[1]; toca(f"a|{idc}|si"); escribe(codigo_wa())
comprobar("F1 «en 10 minutos» → /t 600", runs[-1][2] == "600", str(runs))
reset()
escribe("apaga la compu en 5 segundos"); idc = botones()[0].split("|")[1]; toca(f"a|{idc}|si"); escribe(codigo_wa())
comprobar("F2 por Telegram nunca menos de 30 s", runs[-1][2] == "30", str(runs))

# G. lo demás no se rompe
reset()
escribe("abre chrome")
comprobar("G1 «abre chrome» sigue yendo al núcleo", tb._cola.qsize() == 1 and not botones())
escribe("sube el volumen")
comprobar("G2 «sube el volumen» sigue yendo al núcleo", tb._cola.qsize() == 2)
reset()
tb._atender({"message": {"chat": {"type": "private", "id": 5}, "from": {"id": 5}, "message_id": 99, "text": "apaga la computadora",
                         "date": time.time()}})
comprobar("G3 un mensaje de otra cuenta se ignora en silencio", not api and tb._apag is None)
reset()
tb._encolar("apaga la computadora", time.time() - 999)
comprobar("G4 una orden vieja de apagado no se atiende", not botones() and "retraso" in textos()[-1], str(textos()))
comprobar("G5 el diagnóstico menciona el apagado", "Apagado remoto" in tb.diagnostico())

# ─────────────────────────── 3b. segundo factor TOTP ───────────────────────────
print("\n[3b] TOTP (app autenticadora)")
import tempfile
ap._TOTP_ARCHIVO = os.path.join(tempfile.mkdtemp(), "t.key")
config.APAGADO_CODIGO_MODO = "auto"
config.APAGADO_CODIGO_MODO = "auto"
comprobar("T1 sin configurar, auto → whatsapp", ap.modo_codigo() == "whatsapp" and not ap.totp_configurado())
sec = ap.totp_crear()
comprobar("T2 configurado, auto → totp", ap.modo_codigo() == "totp" and ap.totp_configurado())
# vector RFC 6238 (SHA1, secreto ASCII "12345678901234567890", t=59 → 94287082 → 6 dígitos 287082)
import base64 as _b64
sec_rfc = _b64.b32encode(b"12345678901234567890").decode().rstrip("=")
comprobar("T3 vector RFC 6238 (t=59)", ap._hotp(sec_rfc, 59 // 30) == "287082")
ahora = 1_700_000_000.0
cod = ap._hotp(sec, int(ahora // 30))
ap._totp_ultimo = 0
comprobar("T4 código vigente válido", ap.totp_verificar(cod, ahora))
comprobar("T5 no se puede reutilizar", not ap.totp_verificar(cod, ahora))
ap._totp_ultimo = 0
comprobar("T6 código de otra ventana lejana inválido", not ap.totp_verificar(ap._hotp(sec, int(ahora // 30) + 5), ahora))
comprobar("T7 formato malo inválido", not ap.totp_verificar("12345", ahora) and not ap.totp_verificar("abcdef", ahora) and not ap.totp_verificar("", ahora))
config.APAGADO_CODIGO_MODO = "whatsapp"
comprobar("T8 modo forzado whatsapp", ap.modo_codigo() == "whatsapp")
config.APAGADO_CODIGO_MODO = "totp"

# flujo completo por Telegram con TOTP: no se manda nada por WhatsApp
reset(); ap._totp_ultimo = 0; config.APAGADO_CODIGO_MODO = "totp"
wa_estado[0] = {"estado": "cerrado"}                     # ni siquiera hace falta WhatsApp
escribe("apaga la computadora")
b = botones()
comprobar("T9 con TOTP no exige WhatsApp y pide confirmación", len(b) == 2 and "autenticadora" in todo_telegram(), todo_telegram())
toca(f"a|{b[0].split('|')[1]}|si")
comprobar("T10 no se manda ningún código por WhatsApp ni por Telegram", not wa_enviados and "autenticadora" in textos()[-1])
escribe("123456")
comprobar("T11 código malo no apaga y quedan intentos", not runs and "incorrecto" in textos()[-1])
escribe(ap._hotp(ap._totp_leer(), int(time.time() // 30)))
comprobar("T12 código TOTP bueno apaga", len(runs) == 1, str(runs))
reset(); ap._totp_ultimo = 0; config.APAGADO_CODIGO_MODO = "totp"
escribe("apaga la computadora"); b = botones(); toca(f"a|{b[0].split('|')[1]}|si")
escribe("111111"); escribe("222222"); escribe("333333")
comprobar("T13 3 fallos → bloqueo y nada se apaga", not runs and tb._apag is None and tb._apag_bloq > time.time())
config.APAGADO_CODIGO_MODO = "whatsapp"


# TOTP sin configurar: se niega y NO cae a WhatsApp
reset(); config.APAGADO_CODIGO_MODO = "totp"
os.remove(ap._TOTP_ARCHIVO)
escribe("apaga la computadora")
comprobar("T14 TOTP sin configurar: se niega, sin botones ni WhatsApp", not botones() and not wa_enviados and "autenticadora" in todo_telegram(), todo_telegram())
comprobar("T15 el valor por defecto de config es totp",
          "\"totp\"" in open(os.path.join(os.path.dirname(__file__), "config.py"), encoding="utf-8").read()
          .split("APAGADO_CODIGO_MODO")[1].split("\n")[0])
config.APAGADO_CODIGO_MODO = "whatsapp"

# ─────────────────────────── 3c. acciones de energía v2 ───────────────────────────
print("\n[3c] reiniciar / suspender / hibernar / bloquear + modo mochila")
CASOS = {"reinicia la computadora": ("reiniciar", None), "reinicia la compu en 5 minutos": ("reiniciar", 300),
         "suspende la laptop": ("suspender", None), "duerme la computadora": ("suspender", None),
         "pon la compu en suspensión": ("suspender", None), "pon la laptop en reposo en 10 minutos": ("suspender", 600),
         "hiberna la pc": ("hibernar", None), "pon la laptop en hibernación": ("hibernar", None),
         "bloquea la pantalla": ("bloquear", None), "bloquea la sesión": ("bloquear", None)}
for frase, (acc, dem) in CASOS.items():
    r = ap.interpretar(frase)
    comprobar(f"v2 {acc} ← «{frase}»", bool(r) and r["accion"] == acc and r["demora_s"] == dem, str(r))
for frase in ["duerme al bebe", "reinicia el router", "suspende el examen", "bloquea el número", "pon la música en reposo",
              "cómo hibernar la compu", "hiberna", "reinicia", "pon la compu a cargar"]:
    comprobar(f"v2 NO ← «{frase}»", ap.interpretar(frase) is None, str(ap.interpretar(frase)))
for frase in ["cancela la suspensión", "cancela la hibernación", "cancela el reinicio"]:
    r = ap.interpretar(frase)
    comprobar(f"v2 cancelar ← «{frase}»", bool(r) and r["accion"] == "cancelar", str(r))

# ejecución con ejecutor falso
runs.clear(); ap.usar_ejecutor(lambda a: (runs.append(a) or (0, "")))
ap._ultimo = None
ok, msg = ap.programar(30, "texto", "reiniciar")
comprobar("v2 reiniciar usa shutdown /r /t 30", ok and runs[-1][:3] == ["/r", "/t", "30"] and "Reiniciando" in msg, str(runs[-1:]))
ap._ultimo = None; runs.clear()
ok, msg = ap.programar(1, "texto", "suspender")
comprobar("v2 suspender: se programa sin ejecutar todavía", ok and not runs and ap.programado() is not None, msg)
ok_c, msg_c = ap.cancelar()
comprobar("v2 cancelar corta el temporizador de suspender", ok_c and ap.programado() is None, msg_c)
time.sleep(1.4)
comprobar("v2 …y nunca se ejecutó", not runs, str(runs))
ap._ultimo = None; runs.clear()
ap.normalizar_demora = ap.normalizar_demora
config.APAGADO_DEMORA_MIN_S = 1
ok, msg = ap.programar(1, "texto", "suspender")
time.sleep(1.6)
comprobar("v2 suspender sin cancelar sí se ejecuta tras la cuenta atrás", runs == [["__suspender"]] and ap.programado() is None, str(runs))
ap._ultimo = None; runs.clear()
ok, _ = ap.programar(1, "texto", "hibernar"); time.sleep(1.6)
comprobar("v2 hibernar se ejecuta", runs == [["__hibernar"]], str(runs))
ap._ultimo = None; runs.clear()
ok, msg = ap.programar(None, "texto", "bloquear")
comprobar("v2 bloquear es inmediato", ok and runs == [["__bloquear"]], str(runs))
ap._ultimo = None; runs.clear()
ap.programar(60, "texto", "suspender")
ok2, msg2 = ap.programar(60, "texto", "hibernar")
comprobar("v2 no se encolan dos acciones de energía", not ok2 and "Ya hay" in msg2, msg2)
ap.cancelar(); ap._ultimo = None; runs.clear()
config.APAGADO_DEMORA_MIN_S = 10
comprobar("v2 acción inventada se rechaza", ap.programar(30, "texto", "formatear")[0] is False)

# textos
comprobar("v2 aviso remoto de suspender advierte que se pierde el control",
          "NO podré responderte" in ap.advertencia(30, True, "totp", "suspender"))
comprobar("v2 aviso remoto de apagar sigue igual", "no esté guardado" in ap.advertencia(30, True, "whatsapp", "apagar"))
comprobar("v2 reiniciar avisa de la contraseña de Windows", "contraseña" in ap.advertencia(30, True, "totp", "reiniciar"))
comprobar("v2 requiere_codigo por defecto", ap.requiere_codigo("apagar") and ap.requiere_codigo("reiniciar")
          and not ap.requiere_codigo("suspender") and not ap.requiere_codigo("hibernar") and not ap.requiere_codigo("bloquear"))

# telegram: suspender solo con botón (sin código ni WhatsApp)
reset(); config.APAGADO_CODIGO_MODO = "totp"; config.APAGADO_DEMORA_MIN_S = 10; config.APAGADO_REMOTO_DEMORA_MIN_S = 30
ap._ultimo = None; runs.clear()
os.path.exists(ap._TOTP_ARCHIVO) or ap.totp_crear()
escribe("suspende la laptop")
b = botones()
comprobar("v2 TG suspender: pide solo botones", len(b) == 2 and "suspender" in str(api[-1][1]["reply_markup"]).lower()
          and "NO podré responderte" in todo_telegram(), todo_telegram())
toca(f"a|{b[0].split('|')[1]}|si")
comprobar("v2 TG suspender: tras el botón queda programado sin pedir código", ap.programado() is not None and tb._apag is None
          and "Suspendiendo" in todo_telegram() and not wa_enviados, todo_telegram())
escribe("/cancelar")
comprobar("v2 TG /cancelar corta la suspensión", ap.programado() is None and not runs, str(runs))
reset(); ap._ultimo = None; config.APAGADO_CODIGO_MODO = "totp"
escribe("reinicia la computadora"); b = botones(); toca(f"a|{b[0].split('|')[1]}|si")
comprobar("v2 TG reiniciar SÍ pide código", tb._apag and tb._apag["fase"] == "codigo" and not runs, todo_telegram())
ap._totp_ultimo = 0
escribe(ap._hotp(ap._totp_leer(), int(time.time() // 30)))
comprobar("v2 TG reiniciar con código → shutdown /r", runs and runs[-1][0] == "/r", str(runs))
reset(); ap._ultimo = None; config.APAGADO_CODIGO_MODO = "totp"
escribe("bloquea la pantalla"); b = botones(); toca(f"a|{b[0].split('|')[1]}|si")
comprobar("v2 TG bloquear: inmediato tras el botón", runs == [["__bloquear"]], str(runs))
reset(); ap._ultimo = None
tb._atender({"message": {"chat": {"type": "private", "id": 111}, "from": {"id": 111, "is_bot": False}, "message_id": 998,
                         "text": "suspende la laptop", "date": time.time(), "voice": {"file_id": "x"}}})
config.APAGADO_CODIGO_MODO = "whatsapp"
ap._ultimo = None; runs.clear()

# modo mochila con powercfg falso (salida en inglés y en español)
estado_pc = {("SUB_BUTTONS", "LIDACTION"): [1, 1], ("SUB_SLEEP", "STANDBYIDLE"): [900, 600], ("SUB_SLEEP", "HIBERNATEIDLE"): [0, 0],
             ("SUB_VIDEO", "VIDEOIDLE"): [600, 300], ("SUB_PROCESSOR", "PROCTHROTTLEMAX"): [100, 100],
             ("SUB_PROCESSOR", "PERFBOOSTMODE"): [2, 2]}
cmds_pc, idioma = [], ["en"]


def fake_powercfg(a):
    cmds_pc.append(a)
    if a[0] == "/q":
        k = (a[2], a[3])
        if k not in estado_pc:
            return 1, "El valor no existe"
        ac, dc = estado_pc[k]
        if idioma[0] == "en":
            return 0, (f"  Power Setting GUID: x  (Algo)\n    Possible Setting Index: 0x00000000\n    Possible Setting Index: 0x00000001\n"
                       f"    Current AC Power Setting Index: 0x{ac:08x}\n    Current DC Power Setting Index: 0x{dc:08x}")
        return 0, (f"  GUID: x  (Algo)\n    Índice de configuración posible: 0x00000000\n"
                   f"    Índice de configuración de energía de CA actual: 0x{ac:08x}\n    Índice de configuración de energía de CC actual: 0x{dc:08x}")
    if a[0] in ("/setacvalueindex", "/setdcvalueindex"):
        k = (a[2], a[3])
        if k in estado_pc:
            estado_pc[k][0 if a[0] == "/setacvalueindex" else 1] = int(a[4])
        return 0, ""
    return 0, ""


ap.usar_powercfg(fake_powercfg)
ap._ENERGIA_BACKUP = os.path.join(tempfile.mkdtemp(), "b.json")
for leng in ("en", "es"):
    idioma[0] = leng
    comprobar(f"E1 lee CA/CC en {leng}", ap._leer_ajuste("SUB_SLEEP", "STANDBYIDLE") == (900, 600))
idioma[0] = "es"
ok, ls = ap.energia_mochila()
comprobar("E2 mochila: tapa no hace nada (CA y CC)", ok and estado_pc[("SUB_BUTTONS", "LIDACTION")] == [0, 0], str(estado_pc))
comprobar("E3 mochila: nunca suspender/hibernar", estado_pc[("SUB_SLEEP", "STANDBYIDLE")] == [0, 0])
comprobar("E4 mochila: batería con CPU 50 %, sin turbo, pantalla 60 s; corriente intacta",
          estado_pc[("SUB_PROCESSOR", "PROCTHROTTLEMAX")] == [100, 50] and estado_pc[("SUB_PROCESSOR", "PERFBOOSTMODE")] == [2, 0]
          and estado_pc[("SUB_VIDEO", "VIDEOIDLE")] == [600, 60])
comprobar("E5 ajuste inexistente (Wi‑Fi) se omite sin fallar", any("no tiene ese ajuste" in l for l in ls) and ok)
comprobar("E6 se activó el plan", ["/setactive", "SCHEME_CURRENT"] in cmds_pc)
ap.energia_mochila()
import json as _json
bk = _json.load(open(ap._ENERGIA_BACKUP))
comprobar("E7 repetir NO pisa la copia original", bk["tapa"] == [1, 1] and bk["suspender"] == [900, 600], str(bk))
ok, ls = ap.energia_restaurar()
comprobar("E8 restaurar devuelve los valores originales",
          ok and estado_pc[("SUB_BUTTONS", "LIDACTION")] == [1, 1] and estado_pc[("SUB_SLEEP", "STANDBYIDLE")] == [900, 600]
          and estado_pc[("SUB_PROCESSOR", "PROCTHROTTLEMAX")] == [100, 100] and estado_pc[("SUB_PROCESSOR", "PERFBOOSTMODE")] == [2, 2]
          and not os.path.exists(ap._ENERGIA_BACKUP), str(estado_pc))
comprobar("E9 restaurar sin copia no hace nada", ap.energia_restaurar()[0] is False)
ap.usar_powercfg(None)
ap.usar_ejecutor(lambda a: (runs.append(a) or (rc_siguiente[0], "")))

# ─────────────────────────── 4. core_bridge: flujo local ───────────────────────────
print("\n[4] core_bridge._ejecutar_apagado (ventana de SARA)")
src = open(os.path.join(os.path.dirname(__file__), "core_bridge.py"), encoding="utf-8").read()
arbol = ast.parse(src)
fn = next(n for n in arbol.body if isinstance(n, ast.FunctionDef) and n.name == "_ejecutar_apagado")
from typing import Callable, Tuple
import logging
confirma = {"respuesta": True, "preguntas": []}
ns = {"Callable": Callable, "Tuple": Tuple, "logger": logging.getLogger("t"), "_ctx": threading.local(),
      "_ORIGENES_REMOTOS": ("whatsapp", "telegram"), "TIMEOUT_CONFIRMACION_S": 30,
      "config": types.SimpleNamespace(APAGADO_ACTIVO=True), "apagado": ap,
      "_seguro": lambda d, f, *a: f(*a),
      "_pedir_confirmacion": lambda emit, preg, timeout=30: (confirma["preguntas"].append(preg) or confirma["respuesta"])}
exec(compile(ast.Module([fn], []), "core_bridge", "exec"), ns)
ejecutar = ns["_ejecutar_apagado"]
emitidos = []
emit = lambda tipo, **d: emitidos.append(tipo)


def local(texto, origen=None, acepta=True):
    ns["_ctx"].origen = origen
    confirma["respuesta"] = acepta
    confirma["preguntas"].clear(); runs.clear()
    return ejecutar(texto, emit)


ok, msg = local("apaga la computadora")
comprobar("L1 local: muestra la advertencia y, al aceptar, programa 30 s",
          ok and runs[-1][:3] == ["/s", "/t", "30"] and "guarda tu trabajo" in confirma["preguntas"][0], f"{msg} {runs}")
ok, msg = local("apaga la computadora", acepta=False)
comprobar("L2 local: si dices que no, no apaga", ok and not runs and "Cancelado" in msg, f"{msg} {runs}")
ok, msg = local("apaga el equipo en 2 minutos")
comprobar("L3 local: «en 2 minutos» → 120 s y lo dice en el aviso", runs[-1][2] == "120" and "2 minutos" in confirma["preguntas"][0])
ok, msg = local("apaga la computadora", origen="voz")
comprobar("L4 por micrófono también pide confirmación en el modal", runs and confirma["preguntas"])
ok, msg = local("apaga la computadora", origen="whatsapp")
comprobar("L5 WhatsApp: rechazado, sin preguntar ni apagar", not ok and not runs and not confirma["preguntas"] and "WhatsApp" in msg, msg)
ok, msg = local("apaga la computadora", origen="telegram")
comprobar("L6 si una orden de Telegram llega al núcleo: rechazada", not ok and not runs and not confirma["preguntas"], msg)
ok, msg = local("cancela el apagado", origen="whatsapp")
comprobar("L7 cancelar sí vale por WhatsApp", runs and runs[-1] == ["/a"], f"{msg} {runs}")
ns["config"].APAGADO_ACTIVO = False
ok, msg = local("apaga la computadora")
comprobar("L8 APAGADO_ACTIVO=False → no apaga", not ok and not runs and "desactivado" in msg, msg)
ns["config"].APAGADO_ACTIVO = True
comprobar("L9 los dos ganchos están en core_bridge", src.count('_seguro("apagado.es_comando"') == 2 and 'apagado = _importar("apagado")' in src)
i_ap, i_vol = src.index('"apagado.es_comando"'), src.index('"volumen.es_comando"')
comprobar("L10 el apagado se evalúa ANTES que el volumen", i_ap < i_vol)

print("\n" + ("TODO OK" if not fallos else f"FALLARON {len(fallos)}: " + "; ".join(fallos)))
sys.exit(1 if fallos else 0)
