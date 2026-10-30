"""Pruebas de envio_archivos, pantalla, alertas y desbloqueo (sin red, sin Telegram, sin Windows)."""
import os, sys, tempfile, time, zlib, struct, base64, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
fallos = []
def ok(n, c, d=""):
    print(("  ✅ " if c else "  ❌ ") + n + ("" if c else f"  → {d}"))
    if not c: fallos.append(n)

import envio_archivos as ea, pantalla as pa, alertas as al, desbloqueo as db
import apagado as ap
import config as cfg_mod
cfg = types.SimpleNamespace()

print("[1] envio.interpretar")
for f in ["mándame el PDF de la tarea de física", "pásame el archivo config.py de la carpeta saradefinitiva",
          "compárteme el documento informe final", "envíame la foto de las vacaciones", "Sara, mándame main.py al celular",
          "oye mándame el excel de gastos en descargas"]:
    ok(f"SÍ «{f}»", ea.es_comando(f), str(ea.interpretar(f)))
for f in ["mándale un whatsapp a Juan", "mándame un mensaje", "mándame una captura de pantalla", "mándale a Pedro el archivo",
          "envíame un recordatorio", "abre el archivo config.py", "pásame la sal", "mándame"]:
    ok(f"NO «{f}»", not ea.es_comando(f), str(ea.interpretar(f)))
r = ea.interpretar("mándame el pdf de la tarea de física")
ok("pdf → archivo con extensión", r["frase_abrir"] == "abre el archivo tarea de física.pdf", r["frase_abrir"])
r = ea.interpretar("mándame el pdf de la tarea en descargas")
ok("pdf + carpeta mantiene el separador", r["frase_abrir"] == "abre el archivo tarea.pdf en descargas", r["frase_abrir"])
r = ea.interpretar("pásame el archivo config.py de la carpeta sara 2")
ok("cadena de carpetas intacta", r["frase_abrir"] == "abre el archivo config.py de la carpeta sara 2", r["frase_abrir"])

print("[2] envio.permitido")
# fuera de AppData (en Windows %TEMP% cuelga de AppData, que está en la lista negra a propósito)
d = tempfile.mkdtemp(dir=os.path.expanduser("~")); cfg_mod.ENVIO_CARPETAS_AUTORIZADAS = (d,)
for n in ["tarea.pdf", ".env", "apagado_totp.key", "sara.db", "mis_passwords.txt", "x.pem"]:
    open(os.path.join(d, n), "w").write("x")
ok("archivo normal dentro", ea.permitido(os.path.join(d, "tarea.pdf"))[0])
for n in [".env", "apagado_totp.key", "sara.db", "mis_passwords.txt", "x.pem"]:
    ok(f"negra {n}", not ea.permitido(os.path.join(d, n))[0])
ok("fuera de autorizadas", not ea.permitido(os.path.join(tempfile.gettempdir(), "otro.txt"))[0])
ok("traversal ..", not ea.permitido(os.path.join(d, "..", "x.txt"))[0])

print("[3] envio.ejecutar con abridor simulado")
enviados = []
ea.abridor = types.SimpleNamespace(resolver_apertura=lambda res: {"estado": "abrir", "ruta": os.path.join(d, "tarea.pdf")})
okk, t = ea.ejecutar("mándame el pdf de tarea", lambda p, o: 0, lambda r, c: enviados.append(r) or True)
ok("envía lo resuelto", okk and enviados and "tarea.pdf" in t, t)
ea.abridor = types.SimpleNamespace(resolver_apertura=lambda res: {"estado": "abrir", "ruta": os.path.join(d, ".env")})
enviados.clear(); okk, t = ea.ejecutar("mándame el archivo .env", lambda p, o: 0, lambda r, c: enviados.append(r) or True)
ok(".env jamás se envía", not okk and not enviados, t)
ea.abridor = types.SimpleNamespace(resolver_apertura=lambda res: {"estado": "elegir", "pregunta": "¿cuál?", "opciones": ["a", "b"],
              "continuar": lambda i: {"estado": "abrir", "ruta": os.path.join(d, "tarea.pdf")}})
okk, t = ea.ejecutar("mándame el archivo tarea.pdf", lambda p, o: 1, lambda r, c: True)
ok("ambigüedad pregunta y luego envía", okk, t)
okk, t = ea.ejecutar("mándame el archivo tarea.pdf", lambda p, o: None, lambda r, c: True)
ok("cancelar la elección no envía", okk and "Cancelado" in t, t)
ea.abridor = types.SimpleNamespace(resolver_apertura=lambda res: {"estado": "no_encontrado", "texto": "x"}); ea.file_index = None
okk, t = ea.ejecutar("mándame el archivo fantasma.pdf", lambda p, o: 0, lambda r, c: True)
ok("no encontrado lo dice", not okk and "No encontré" in t, t)

print("[4] pantalla")
for f in ["quiero ver la pantalla", "muéstrame la pantalla", "¿qué está pasando en la pantalla?", "dime qué pasa en la pantalla",
          "qué hay en la pantalla", "mándame una captura de pantalla", "/pantalla", "Sara, enséñame... ver pantalla"][:7]:
    ok(f"SÍ «{f}»", pa.es_comando(f), str(pa.interpretar(f)))
for f in ["abre la pantalla de configuración", "apaga la pantalla", "cambia el brillo de la pantalla", "hola"]:
    ok(f"NO «{f}»", not pa.es_comando(f))
ok("«qué pasa» pide descripción", pa.interpretar("dime qué pasa en la pantalla")["describir"] is True)
ok("«quiero ver» no describe", pa.interpretar("quiero ver la pantalla")["describir"] is False)
w, h = 7, 5
bgra = bytes([10, 20, 30, 255]) * (w * h)
png = pa.png_desde_bgra(w, h, bgra)
ok("PNG firma", png[:8] == b"\x89PNG\r\n\x1a\n")
i = png.index(b"IHDR"); ww, hh = struct.unpack(">II", png[i + 4:i + 12])
j = png.index(b"IDAT"); ln = struct.unpack(">I", png[j - 4:j])[0]
raw = zlib.decompress(png[j + 4:j + 4 + ln])
ok("PNG dimensiones y datos", (ww, hh) == (7, 5) and len(raw) == 5 * (1 + 7 * 3) and raw[1:4] == bytes([30, 20, 10]), f"{ww}x{hh} {raw[:4]}")
p2 = pa.png_desde_bgra(8, 6, bytes([1, 2, 3, 255]) * 48, 2); j = p2.index(b"IHDR")
ok("reducción x2", struct.unpack(">II", p2[j + 4:j + 12]) == (4, 3))
ok("detecta negro", pa._es_negra(bytes([0, 0, 0, 255]) * 5000) and not pa._es_negra(bytes([200, 200, 200, 255]) * 5000))
if os.name != "nt":
    ok("fuera de Windows lo dice", "Windows" in pa.capturar()[1])
pa.capturar = lambda: (png, "")
fotos = []
okk, t = pa.ejecutar("quiero ver la pantalla", lambda b, c: fotos.append(b) or True)
ok("ver → manda imagen", okk and fotos)
okk, t = pa.ejecutar("dime qué pasa en la pantalla", lambda b, c: True)
ok("describir apagado por defecto → avisa y NO llama a la IA", okk and "descripción de pantalla" in t, t)
pa.capturar = lambda: (None, "🔒 bloqueada")
okk, t = pa.ejecutar("quiero ver la pantalla", lambda b, c: True)
ok("pantalla bloqueada → no manda nada", not okk and "bloqueada" in t)

print("[5] alertas")
for f in ["silencia todo", "silencia las alertas", "desactiva las alertas", "apaga las notificaciones", "no me avises", "que no digas nada"]:
    ok(f"off «{f}»", al.interpretar(f) == "off", str(al.interpretar(f)))
for f in ["activa las alertas", "enciende los avisos", "reactiva las notificaciones"]:
    ok(f"on «{f}»", al.interpretar(f) == "on", str(al.interpretar(f)))
for f in ["silencia", "apaga la computadora", "apaga todo", "activa el modo clase", "pon música"]:
    ok(f"NO «{f}»", al.interpretar(f) is None, str(al.interpretar(f)))
al._ruta = lambda: os.path.join(d, "aj.json")
al.config = cfg_mod
al.fijar(False); ok("apagar persiste", not al.activas() and '"ALERTAS_ACTIVAS": false' in open(al._ruta()).read())
cfg_mod.ALERTAS_ACTIVAS = True; al._cargar(); ok("al reiniciar se restaura lo guardado", not al.activas())
al.fijar(True)
enviadas = []
sys.modules["telegram_bot"] = types.SimpleNamespace(enviar_alerta=lambda t: enviadas.append(t) or True)
al._ult.clear(); al._alerta("x", "uno"); al._alerta("x", "dos")
ok("antirrebote: la misma alerta no se repite", enviadas == ["uno"])
al._rearmar("x"); al._alerta("x", "tres")
ok("rearmar permite volver a avisar", enviadas == ["uno", "tres"])
al.fijar(False); al._ult.clear(); al._alerta("y", "no debe salir")
ok("silenciado: no envía NADA", "no debe salir" not in enviadas)
al.fijar(True)
# tick con psutil simulado
class B: percent = 12.0; power_plugged = False
fake = types.SimpleNamespace(sensors_battery=lambda: B, cpu_percent=lambda interval=None: 95.0,
          disk_usage=lambda p: types.SimpleNamespace(free=5, total=100))
al.psutil = fake; al._internet = lambda: True; al._ult.clear(); enviadas.clear(); al._prev_enchufe = True
al.tick()
ok("tick: cargador desconectado", any("desconectado" in t for t in enviadas), str(enviadas))
ok("tick: batería baja", any("Batería baja" in t for t in enviadas), str(enviadas))
ok("tick: disco casi lleno", any("Disco" in t for t in enviadas))
for _ in range(7): al.tick()
ok("tick: calor por CPU sostenida", any("calentándose" in t for t in enviadas))
n = len(enviadas); al.tick(); al.tick()
ok("tick: sin spam al repetir", len(enviadas) == n)
al._ult.clear(); enviadas.clear(); al._net_caido = time.time() - 300
al.tick()
ok("internet: avisa al recuperarse con la duración", any("sin internet" in t for t in enviadas), str(enviadas))

print("[6] desbloqueo (TOTP)")
ap._TOTP_ARCHIVO = os.path.join(d, "t.key"); ap._totp_ultimo = 0
db.apagado = ap; db._hasta = 0; db._fallos = 0; db._bloq = 0
ok("sin TOTP no disponible", not db.disponible() and not db.intentar("123456")[0])
sec = ap.totp_crear(); cfg_mod.DESBLOQUEO_VENTANA_S = 300
ok("disponible tras configurar", db.disponible() and not db.activo())
okk, m = db.intentar(ap._hotp(sec, int(time.time() // 30)))
ok("código bueno abre ventana de ~5 min", okk and db.activo() and 295 <= db.restante() <= 300, m)
db.cerrar(); ok("cerrar la ventana", not db.activo())
for _ in range(3): db.intentar("000000")
ok("3 fallos → bloqueo aun con el código bueno", db.bloqueado() and not db.intentar(ap._hotp(sec, int(time.time() // 30) + 1))[0])

import shutil; shutil.rmtree(d, ignore_errors=True)
print("\n" + ("TODO OK" if not fallos else f"FALLARON {len(fallos)}: " + "; ".join(fallos)))
sys.exit(1 if fallos else 0)
