"""
test_sara_net.py — Fixture de SARA-Net (whatsapp.py v2 + core_bridge.py parcheado).

NO toca tu base real ni WhatsApp: usa un sidecar HTTP simulado, una BD temporal y un motor de
clasificación falso. Ejecútalo desde la carpeta donde están whatsapp.py y core_bridge.py:

    python test_sara_net.py

Resultado esperado: todas las líneas "OK" y al final "TODO OK".
(Lo que NO cubre: WhatsApp real, Baileys, LID real, Windows, voz.)
"""
import itertools
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ─── dobles de módulos (se inyectan ANTES de importar nada de SARA) ───────
_dbfile = os.path.join(tempfile.mkdtemp(), "t.db")
PUERTO = 18765

cfg = types.ModuleType("config")
cfg.WA_ACTIVO = True
cfg.WA_PUERTO = PUERTO
cfg.WA_MAX_ENVIOS_MIN = 1000
cfg.WA_NET_ACTIVO = False
cfg.WA_NET_POLL_S = 0.05
cfg.WA_NET_MAX_EDAD_S = 180
cfg.WA_NET_MAX_COMANDOS_MIN = 4
cfg.WA_NET_VOZ = False
cfg.WA_NET_CATEGORIAS_BLOQUEADAS = ("COMANDO_SISTEMA", "ELIMINAR_ARCHIVO")
cfg.WA_CONFIRMAR_ENVIO = True
cfg.WA_IGNORAR_GRUPOS = True
sys.modules["config"] = cfg

# stt simulado: el "audio" es un archivo cuyo contenido ES la transcripción (así cada nota dice lo que queramos)
STT = {"llamadas": 0}
stt_fake = types.ModuleType("stt")
stt_fake.PALABRAS_ACTIVACION = ("sara", "sarah", "zara", "sarra")
def _transcribir_archivo(ruta, max_s=60.0):
    STT["llamadas"] += 1
    t = open(ruta, encoding="utf-8").read()
    if t == "__NONE__":
        return None
    if t.startswith("__SLOW__"):
        time.sleep(0.8); t = t[8:]
    return t
stt_fake.transcribir_archivo = _transcribir_archivo
stt_fake.precargar_modelo = lambda: True
sys.modules["stt"] = stt_fake

dbm = types.ModuleType("database")
dbm.conectar = lambda: sqlite3.connect(_dbfile)
sys.modules["database"] = dbm

CLASIF = {"cat": "ABRIR_APLICACION"}
eng = types.ModuleType("intent_engine")
eng.clasificar = lambda t: {"categoria": CLASIF["cat"], "confianza": 0.9, "metodo": "test", "texto_norm": t.lower()}
sys.modules["intent_engine"] = eng

import whatsapp as wa  # noqa: E402
_TMP = tempfile.mkdtemp()
wa._dir = lambda: _TMP                         # el fixture NUNCA toca tu carpeta wa_sidecar real
AUDIO_DIR = os.path.join(_TMP, "audio_tmp"); os.makedirs(AUDIO_DIR, exist_ok=True)
import core_bridge as cb  # noqa: E402

# ─── sidecar simulado ─────────────────────────────────────────────────────
S = {"inbox": [], "sent": [], "seq": 0, "boot": "b1", "audio_param": None}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _out(self, o, c=200):
        b = json.dumps(o).encode()
        self.send_response(c); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/status":
            return self._out({"estado": "abierto", "yo": "5216691645702@s.whatsapp.net", "boot": S["boot"], "contactos": 0})
        if u.path == "/messages":
            since = int(q.get("since", ["0"])[0])
            if "audio" in q:
                S["audio_param"] = q["audio"][0]
            return self._out({"boot": S["boot"], "mensajes": [m for m in S["inbox"] if m["id"] > since]})
        self._out({"error": "no existe"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/send":
            S["sent"].append(body)
            return self._out({"ok": True, "jid": body.get("to")})
        self._out({"ok": True, "estado": "abierto"})


srv = HTTPServer(("127.0.0.1", PUERTO), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()


_wid = itertools.count(1)   # ids de WhatsApp reales son únicos aunque el puente reinicie su contador


def msg(texto, *, mia=True, propio=True, grupo=False, edad=0, wid=None, jid="5216691645702@s.whatsapp.net"):
    S["seq"] += 1
    S["inbox"].append({"id": S["seq"], "wid": wid or f"w-{next(_wid)}", "jid": jid, "nombre": "x", "texto": texto,
                       "ts": int(time.time()) - edad, "grupo": grupo, "mia": mia, "propio": propio})


_naud = itertools.count(1)


def msg_voz(transcripcion, *, ruta=None, mia=True, propio=True, error=None, edad=0):
    """Nota de voz como la deja el sidecar: archivo en audio_tmp (con la transcripción dentro) o un audio_error."""
    n = next(_naud)
    if error is None and ruta is None:
        ruta = os.path.join(AUDIO_DIR, f"nota{n}.ogg")
        open(ruta, "w", encoding="utf-8").write(transcripcion)
    S["seq"] += 1
    S["inbox"].append({"id": S["seq"], "wid": f"v-{n}", "jid": "5216691645702@s.whatsapp.net", "nombre": "x",
                       "texto": "[nota de voz]", "ts": int(time.time()) - edad, "grupo": False, "mia": mia,
                       "propio": propio, "audio": ruta, "audio_error": error})
    return ruta


def enviados():
    return [s["text"] for s in S["sent"]]


def esperar(cond, t=3.0):
    t0 = time.time()
    while time.time() - t0 < t:
        if cond():
            return True
        time.sleep(0.05)
    return False


def sin_cambios(cond, t=0.6):
    time.sleep(t)
    return not cond()


fallos = []


def check(nombre, ok):
    print(("OK   " if ok else "FALLA"), nombre)
    if not ok:
        fallos.append(nombre)


# ─── núcleo simulado para los tests de whatsapp.py ────────────────────────
llamadas = []
conf_resp = []
evento_conf = threading.Event()


def nucleo(texto, emit):
    llamadas.append(texto)
    if texto == "borra todo":
        emit("solicitud_confirmacion", id_confirmacion="c1", pregunta="¿Seguro?", opciones=[], timeout_segundos=5)
        evento_conf.wait(3)
        return {"exito": bool(conf_resp and conf_resp[-1][1] == "si"), "texto": "hecho" if conf_resp and conf_resp[-1][1] == "si" else "cancelado"}
    if texto == "elige":
        emit("solicitud_confirmacion", id_confirmacion="c2", pregunta="¿Cuál?", opciones=["uno", "dos"], timeout_segundos=5)
        evento_conf.wait(3)
        return {"exito": True, "texto": f"opcion {conf_resp[-1][2]}"}
    return {"exito": True, "texto": f"ok {texto}"}


def confirmar(i, r, o=None):
    conf_resp.append((i, r, o)); evento_conf.set(); return True


wa.registrar_nucleo(nucleo, confirmar)
wa._kv_set("numero", "526691645702")

print("── 1. Apagado por defecto")
msg("Sara, abre chrome")
wa._asegurar_hilos_net()
check("net apagado: no ejecuta nada", sin_cambios(lambda: llamadas))

print("── 2. Encendido (/whatsapp net on) y resync")
ok, txt = wa.comando_slash(" net on")
check("net on responde ok", ok and "activado" in txt)
msg("Sara, abre chrome")      # escrita INMEDIATAMENTE después de activar: no debe perderse
check("la orden previa al encendido NO se ejecuta y la de justo después SÍ (sin carrera)",
      esperar(lambda: llamadas == ["abre chrome"]) and sin_cambios(lambda: len(llamadas) > 1))
check("responde al chat propio (jid 'yo') con tarjeta ✅", esperar(lambda: any(s["to"] == "yo" and s["text"].startswith("🤖 ✅ ok abre chrome") for s in S["sent"])))

print("── 3. Filtros")
n = len(llamadas)
msg("comprar leche y pan")
check("sin 'Sara' → se descarta", sin_cambios(lambda: len(llamadas) > n))
msg("Sara apaga todo", mia=False, propio=False)
check("mensaje de OTRA persona con 'Sara' → NUNCA ejecuta", sin_cambios(lambda: len(llamadas) > n))
msg("Sara apaga todo", grupo=True, mia=False, propio=False)
check("grupo → NUNCA ejecuta", sin_cambios(lambda: len(llamadas) > n))
msg("Sara apaga todo", mia=True, propio=False, jid="52999@s.whatsapp.net")
check("mío pero en OTRO chat → NUNCA ejecuta", sin_cambios(lambda: len(llamadas) > n))
check("…y queda anotado el motivo (diagnóstico)", wa._net_stats["propios_no_reconocidos"] == 1 and "otro chat" in wa._net_ultimo_descarte)
msg("🤖 ✅ ok abre chrome")
check("eco de una respuesta de SARA → ignorado", sin_cambios(lambda: len(llamadas) > n))
msg("Sara abre notas", wid="dup"); msg("Sara abre notas", wid="dup")
check("mismo wid dos veces → una sola ejecución", esperar(lambda: llamadas.count("abre notas") == 1) and sin_cambios(lambda: llamadas.count("abre notas") > 1))
msg("oye Sara, ¿qué hora es?")
check("variante 'oye Sara,' + puntuación (quita la ¿ de apertura)", esperar(lambda: "qué hora es?" in llamadas))
msg("Sarai, abre chrome")
check("'Sarai' (otra palabra) NO activa", sin_cambios(lambda: llamadas.count("abre chrome") > 1))

print("── 4. Seguridad temporal / formato")
n = len(S["sent"])
msg("Sara, abre chrome", edad=600)
check("orden de hace 10 min → no se ejecuta y avisa", esperar(lambda: len(S["sent"]) > n and "retraso" in S["sent"][-1]["text"]) and llamadas.count("abre chrome") == 1)
n = len(S["sent"])
msg("Sara, /whatsapp desconectar")
check("orden que empieza con / → bloqueada", esperar(lambda: len(S["sent"]) > n and "solo funcionan desde la ventana" in S["sent"][-1]["text"]) and "/whatsapp desconectar" not in llamadas)
n = len(S["sent"])
msg("Sara")
check("solo 'Sara' → responde que está lista", esperar(lambda: len(S["sent"]) > n and "Aquí estoy" in S["sent"][-1]["text"]))

print("── 5. Confirmaciones por el chat")
time.sleep(61 if False else 0)
wa._net_cmds.clear()
msg("Sara, borra todo")
check("pide confirmación al chat", esperar(lambda: any("¿Seguro?" in t and "«sí» o «no»" in t for t in enviados())))
msg("sí")
check("'sí' (sin 'Sara') se entrega como confirmación", esperar(lambda: conf_resp and conf_resp[-1] == ("c1", "si", None)))
check("la orden termina con ✅ hecho", esperar(lambda: any(t.endswith("✅ hecho") for t in enviados())))
evento_conf.clear(); conf_resp.clear(); wa._net_cmds.clear()
msg("Sara, elige")
check("opciones numeradas", esperar(lambda: any("1) uno" in t and "2) dos" in t for t in enviados())))
msg("2")
check("'2' → opción índice 1", esperar(lambda: conf_resp and conf_resp[-1] == ("c2", "si", 1)))
evento_conf.clear(); conf_resp.clear(); wa._net_cmds.clear()
msg("Sara, borra todo")
esperar(lambda: wa._net_conf is not None)
msg("no")
check("'no' → cancelado", esperar(lambda: conf_resp and conf_resp[-1][1] == "no") and esperar(lambda: any(t.endswith("⚠️ cancelado") for t in enviados())))
check("'sí' sin confirmación pendiente NO hace nada", (lambda n0: (msg("sí"), sin_cambios(lambda: len(llamadas) > n0))[1])(len(llamadas)))

print("── 6. Límite por minuto")
wa._net_cmds.clear()
base = len(llamadas)
for i in range(6):
    msg(f"Sara, tarea {i}")
check("máximo 4 órdenes por minuto", esperar(lambda: len(llamadas) - base == 4) and sin_cambios(lambda: len(llamadas) - base > 4)
      and any("Demasiadas órdenes" in t for t in enviados()))

print("── 7. Reinicio del puente (boot distinto)")
S["boot"] = "b2"; S["inbox"].clear(); S["seq"] = 0
time.sleep(0.4)
wa._net_cmds.clear()
msg("Sara, tras reinicio")
check("tras reiniciar el sidecar vuelve a leer desde 1", esperar(lambda: "tras reinicio" in llamadas))


print("── 7b. Notas de voz (SARA-Net voz)")
wa._net_cmds.clear()
base = len(llamadas)
r0 = msg_voz("Sara, abre la calculadora")
check("voz APAGADA (por defecto): la nota se ignora y su archivo se borra", sin_cambios(lambda: len(llamadas) > base) and not os.path.exists(r0) and STT["llamadas"] == 0)
check("con voz apagada el sondeo le pide al sidecar audio=0", esperar(lambda: S["audio_param"] == "0"))

ok, txt = wa.comando_slash(" net voz on")
check("net voz on responde y se activa", ok and "activadas" in txt and wa._voz_on())
check("el sondeo ahora le pide al sidecar audio=30 (descargar notas de hasta 30 s)", esperar(lambda: S["audio_param"] == "30"))

n0, nl = len(S["sent"]), len(llamadas)
r1 = msg_voz("Sara, abre la calculadora.")
check("nota «Sara, abre la calculadora.» → el núcleo recibe la orden SIN punto final", esperar(lambda: llamadas[nl:] == ["abre la calculadora"]))
env = enviados()[n0:]
check("primero el eco «🎙️ Entendí: …», después el resultado ✅ (en ese orden)",
      esperar(lambda: any(t.startswith("🤖 ✅") for t in enviados()[n0:])) and
      any("🎙️ Entendí: «abre la calculadora»" in t for t in enviados()[n0:]) and
      [i for i, t in enumerate(enviados()[n0:]) if "Entendí" in t][0] < [i for i, t in enumerate(enviados()[n0:]) if t.startswith("🤖 ✅")][0])
check("el archivo de audio se borró al terminar", esperar(lambda: not os.path.exists(r1)))

wa._net_cmds.clear(); nl = len(llamadas)
msg_voz("Zara, abre chrome")
check("variante que Whisper suele oír («Zara») también activa", esperar(lambda: llamadas[nl:] == ["abre chrome"]))

wa._net_cmds.clear(); nl = len(llamadas); sc = STT["llamadas"]
r2 = msg_voz("hay que comprar leche y pan")
check("nota SIN «Sara» → se descarta en silencio, y se borra", esperar(lambda: STT["llamadas"] == sc + 1) and sin_cambios(lambda: len(llamadas) > nl) and not os.path.exists(r2))
msg_voz("Sara apaga todo", mia=False, propio=False)
check("nota de OTRA persona: ni siquiera se transcribe", sin_cambios(lambda: STT["llamadas"] > sc + 1 or len(llamadas) > nl))

n0 = len(S["sent"])
msg_voz("", error="larga")
check("nota demasiado larga → avisa", esperar(lambda: any("demasiado larga" in t for t in enviados()[n0:])))
n0 = len(S["sent"])
msg_voz("", error="descarga")
check("descarga fallida (p. ej. Bad MAC) → avisa en vez de callarse", esperar(lambda: any("No pude descargar" in t for t in enviados()[n0:])))
n0 = len(S["sent"])
msg_voz("__NONE__")
check("el STT no pudo transcribir → avisa", esperar(lambda: any("No pude transcribir" in t for t in enviados()[n0:])))
n0 = len(S["sent"])
msg_voz("   ")
check("transcripción vacía → «No entendí nada»", esperar(lambda: any("No entendí nada" in t for t in enviados()[n0:])))
check("«última oída» queda solo en RAM y se ve en el estado", "net voz" in wa._net_resumen() or "Notas de voz: ACTIVAS" in wa._net_resumen())

print("   · Confirmaciones y voz")
evento_conf.clear(); conf_resp.clear(); wa._net_cmds.clear()
msg("Sara, borra todo")
esperar(lambda: wa._net_conf is not None)
n0 = len(S["sent"])
msg_voz("sí")
check("confirmación pendiente + voz «sí» → NO confirma (pide texto)", esperar(lambda: any("por texto" in t for t in enviados()[n0:])) and not conf_resp)
msg_voz("Sara, no")
check("…pero la voz SÍ puede cancelar", esperar(lambda: conf_resp and conf_resp[-1][1] == "no"))
evento_conf.clear(); conf_resp.clear()

print("   · Seguridad de rutas y concurrencia")
fuera = os.path.join(_TMP, "fuera.ogg"); open(fuera, "w", encoding="utf-8").write("Sara, abre chrome")
sc, nl = STT["llamadas"], len(llamadas); wa._net_cmds.clear()
msg_voz("", ruta=fuera)
check("ruta FUERA de audio_tmp → no se abre, no se transcribe y NO se borra", sin_cambios(lambda: STT["llamadas"] > sc or len(llamadas) > nl) and os.path.exists(fuera))
trav = os.path.join(AUDIO_DIR, "..", "fuera.ogg")
msg_voz("", ruta=trav)
check("ruta con «..» (path traversal) → tampoco", sin_cambios(lambda: STT["llamadas"] > sc) and os.path.exists(fuera))

wa._net_cmds.clear(); nl = len(llamadas)
msg_voz("__SLOW__Sara, orden lenta")
msg("Sara, orden rapida")
check("transcribir (lento) no bloquea el sondeo: la orden de texto se ejecuta ANTES", esperar(lambda: "orden rapida" in llamadas[nl:]) and "orden lenta" not in llamadas[nl:])
check("…y la nota lenta termina ejecutándose después", esperar(lambda: "orden lenta" in llamadas[nl:], 4.0))

wa._net_cmds.clear(); nl = len(llamadas)
msg_voz("Sara, /whatsapp desconectar")
msg_voz("Sara")
check("por voz tampoco pasan comandos con «/»", esperar(lambda: any("solo funcionan desde la ventana" in t for t in enviados()[-6:])) and "/whatsapp desconectar" not in llamadas)

wa._net_cmds.clear()
base = len(llamadas)
for i in range(6):
    msg_voz(f"Sara, tarea de voz {i}")
check("el límite de 4 órdenes/minuto también rige para voz", esperar(lambda: len(llamadas) - base == 4, 6.0) and sin_cambios(lambda: len(llamadas) - base > 4))
check("no quedan archivos de audio en disco", esperar(lambda: [f for f in os.listdir(AUDIO_DIR)] == []))

ok, _ = wa.comando_slash(" net voz off")
check("net voz off → vuelve a pedir audio=0 al sidecar", ok and esperar(lambda: S["audio_param"] == "0"))
sc = STT["llamadas"]; msg_voz("Sara, abre algo")
check("con la voz apagada otra vez, se ignora", sin_cambios(lambda: STT["llamadas"] > sc))

sys.modules["stt"] = types.ModuleType("stt")          # un stt.py viejo, sin transcribir_archivo
ok, txt = wa.comando_slash(" net voz on")
check("sin stt nuevo → net voz on se niega con un mensaje claro", (not ok) and "stt.py" in txt and not wa._voz_on())
sys.modules["stt"] = stt_fake

print("── 8. Apagar")
ok, _ = wa.comando_slash(" net off")
n = len(llamadas)
msg("Sara, abre algo")
check("net off → deja de obedecer", ok and sin_cambios(lambda: len(llamadas) > n))

# ─── Integración con core_bridge REAL (motor falso) ───────────────────────
print("── 9. core_bridge: bloqueo, voz y contexto (integración)")
habladas = []
cb.voz = types.SimpleNamespace(hablar=lambda t, **k: habladas.append(t))
cb._listo = True
eventos = []
emit = lambda tipo, **d: eventos.append(tipo)

CLASIF["cat"] = "COMANDO_SISTEMA"
r = cb.procesar_mensaje("apaga el equipo", emit, "whatsapp")
check("COMANDO_SISTEMA por whatsapp → bloqueado", (not r["exito"]) and "no está permitida por WhatsApp" in r["texto"])
check("no habló en el PC", habladas == [])
check("el contexto de hilo se limpió al terminar", getattr(cb._ctx, "origen", "x") is None)
r = cb.procesar_mensaje("apaga el equipo", emit, "texto")
check("el mismo comando por texto NO queda bloqueado", "no está permitida por WhatsApp" not in r["texto"])
habladas.clear()
cb._hablar("hola")
check("_hablar sin origen whatsapp sí habla", habladas == ["hola"])
cb._ctx.origen = "whatsapp"; cb._hablar("secreto"); cb._ctx.origen = None
check("_hablar con origen whatsapp calla", habladas == ["hola"])

print("── 10. Extremo a extremo: sidecar → whatsapp.py → core_bridge")
cb._arranque_pesado()
check("core_bridge registró su núcleo en whatsapp.py", wa._nucleo_procesar is not None)
wa.comando_slash(" net on")
time.sleep(0.4)
wa._net_cmds.clear()
CLASIF["cat"] = "COMANDO_SISTEMA"
n = len(S["sent"])
msg("Sara, apaga el equipo")
check("orden peligrosa remota → respuesta de bloqueo al chat",
      esperar(lambda: any("no está permitida por WhatsApp" in s["text"] for s in S["sent"][n:])))
check("…y SARA no habló en voz alta", habladas == ["hola"])
wa.comando_slash(" net off")

print()
if fallos:
    print(f"✗ {len(fallos)} fallo(s): {fallos}")
    sys.exit(1)
print("TODO OK")
