"""Pruebas v3: interruptores, deshacer (+file_manager), modos, antirrobo, pantalla/errores, telegram con verificación apagada."""
import os, sys, tempfile, time, types, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
fallos = []
def ok(n, c, d=""):
    print(("  ✅ " if c else "  ❌ ") + n + ("" if c else f"  → {d}"))
    if not c: fallos.append(n)

import config
D = tempfile.mkdtemp(); config.BASE_DIR = D
import interruptores as it, desbloqueo as db, deshacer as de, modos as mo, antirrobo as ar, pantalla as pa

print("[1] interruptores")
for f, esp in [("activa la descripción de pantalla con IA", ("descripcion", True)), ("apaga la descripción con IA", ("descripcion", False)),
               ("desactiva la descripción de la pantalla", ("descripcion", False)), ("enciende la lectura de pantalla con ia", ("descripcion", True)),
               ("desactiva la verificación", ("verificacion", False)), ("ya no me pidas el código", ("verificacion", False)),
               ("activa la verificación", ("verificacion", True)), ("pídeme el código otra vez", ("verificacion", True)),
               ("apaga el código de verificación", ("verificacion", False)), ("¿está activa la verificación?", ("verificacion", None))]:
    ok(f"«{f}»", it.interpretar(f) == esp, str(it.interpretar(f)))
for f in ["apaga la computadora", "apaga la descripción", "activa el modo clase", "apaga la pantalla", "pon música", "activa chrome"]:
    ok(f"NO «{f}»", it.interpretar(f) is None, str(it.interpretar(f)))
config.VERIFICACION_ACTIVA = True; config.PANTALLA_DESCRIBIR = False
ok("descripción: encender y persistir", it.ejecutar("activa la descripción de pantalla con IA", "telegram")[0] and config.PANTALLA_DESCRIBIR
   and json.load(open(os.path.join(D, "sara_ajustes.json")))["PANTALLA_DESCRIBIR"] is True)
ok("descripción: apagar desde Telegram SÍ se puede", it.ejecutar("apaga la descripción con IA", "telegram")[0] and not config.PANTALLA_DESCRIBIR)
okk, m = it.ejecutar("desactiva la verificación", "telegram")
ok("verificación: apagar desde Telegram NO", not okk and config.VERIFICACION_ACTIVA and "ventana" in m, m)
okk, m = it.ejecutar("desactiva la verificación", "whatsapp")
ok("verificación: apagar desde WhatsApp NO", not okk and config.VERIFICACION_ACTIVA)
okk, m = it.ejecutar("desactiva la verificación", "texto")
ok("verificación: apagar desde la ventana SÍ y persiste", okk and not config.VERIFICACION_ACTIVA and json.load(open(os.path.join(D, "sara_ajustes.json")))["VERIFICACION_ACTIVA"] is False)
config.VERIFICACION_ACTIVA = True; it.cargar()
ok("al reiniciar se restaura lo guardado (apagada)", config.VERIFICACION_ACTIVA is False)
ok("desbloqueo no pide nada con la verificación apagada", db.activo() and db.disponible())
ok("encender desde Telegram SÍ", it.ejecutar("activa la verificación", "telegram")[0] and config.VERIFICACION_ACTIVA)
db._hasta = 0
ok("verificación encendida → vuelve a pedir código", not db.activo())
ok("/verificacion estado y /descripcion estado", "ACTIVADA" in it.comando_verificacion("")[1] and "apagada" in it.comando_descripcion("")[1])

print("[2] pantalla: errores")
for f in ["¿qué significa este error?", "qué dice este error", "explícame este error", "por qué me sale este error", "qué error es", "ayúdame con este error"]:
    r = pa.interpretar(f); ok(f"error «{f}»", bool(r) and r["describir"] and r["error"], str(r))
ok("«qué pasa en la pantalla» no es de error", pa.interpretar("dime qué pasa en la pantalla")["error"] is False)
ok("«quiero ver la pantalla» no describe", pa.interpretar("quiero ver la pantalla")["describir"] is False)
pa.capturar = lambda: (b"png", ""); usado = []
pa.describir = lambda png, error=False: usado.append(error) or "Es un error de permisos."
config.PANTALLA_DESCRIBIR = False
okk, t = pa.ejecutar("qué significa este error", lambda b, c: True)
ok("descripción apagada: manda imagen, NO llama a la IA y explica cómo activarla", okk and not usado and "descripción de pantalla" in t, t)
config.PANTALLA_DESCRIBIR = True
okk, t = pa.ejecutar("qué significa este error", lambda b, c: True)
ok("descripción encendida: usa la IA en modo error", usado == [True] and "permisos" in t, t)

print("[3] deshacer")
base = tempfile.mkdtemp(); cfgd = types.SimpleNamespace()
a = os.path.join(base, "a.txt"); open(a, "w").write("A")
r = de.eliminar(a)
ok("eliminar → a la papelera, ya no está", r["exito"] and not os.path.exists(a))
ok("describe lo que va a deshacer", "recuperar" in de.describir(de.ultimo()))
okk, m = de.deshacer_ultimo()
ok("deshacer eliminar → vuelve con su contenido", okk and open(a).read() == "A", m)
ok("diario vacío tras deshacer", de.ultimo() is None and "No hay nada" in de.deshacer_ultimo()[1])
carp = os.path.join(base, "dest"); os.makedirs(carp); b = os.path.join(base, "b.txt"); open(b, "w").write("B")
open(os.path.join(carp, "b.txt"), "w").write("VIEJO")
fin = os.path.join(carp, "b.txt"); sobre = de.sobrescrito(fin)
os.replace(b, fin); de.registrar({"tipo": "mover", "origen": b, "final": fin, "sobrescrito": sobre})
okk, m = de.deshacer_ultimo()
ok("deshacer mover: vuelve el original Y se recupera lo sobrescrito", okk and open(b).read() == "B" and open(fin).read() == "VIEJO", m)
c1 = os.path.join(base, "c.txt"); c2 = os.path.join(base, "c2.txt"); open(c1, "w").write("C")
os.rename(c1, c2); de.registrar({"tipo": "renombrar", "viejo": c1, "nuevo": c2})
okk, m = de.deshacer_ultimo(); ok("deshacer renombrar", okk and os.path.exists(c1) and not os.path.exists(c2), m)
z = os.path.join(base, "z.zip"); open(z, "w").write("zip"); de.registrar({"tipo": "crear", "final": z})
okk, m = de.deshacer_ultimo(); ok("deshacer crear → a la papelera (no borra definitivo)", okk and not os.path.exists(z) and os.listdir(de._papelera()), m)
os.rename(c1, c2); de.registrar({"tipo": "renombrar", "viejo": c1, "nuevo": c2}); open(c1, "w").write("otro")
okk, m = de.deshacer_ultimo(); ok("renombrar: no pisa si ya existe el viejo", not okk and os.path.exists(c2), m)
for f, esp in [("deshaz lo último", "deshacer"), ("deshacer", "deshacer"), ("revierte eso", "deshacer"), ("Sara, deshaz lo último por favor", "deshacer"), ("qué puedo deshacer", "listar")]:
    ok(f"«{f}»", de.interpretar(f) == esp, str(de.interpretar(f)))
for f in ["deshaz la cama", "abre el archivo", "regresa a casa", "deshacer el paquete de viaje"]:
    ok(f"NO «{f}»", de.interpretar(f) is None, f)
for i in range(25):
    de.registrar({"tipo": "crear", "final": f"/x/{i}"})
ok("el diario solo guarda DESHACER_MAX", len(de._leer()) == 20)
config.DESHACER_PAPELERA_DIAS = 0; time.sleep(0.01); de.purgar()
ok("purgar vacía diario y papelera vieja", de._leer() == [] and not os.listdir(de._papelera()))
config.DESHACER_PAPELERA_DIAS = 30

print("[4] file_manager con deshacer (file_actions simulado)")
fa = types.SimpleNamespace(
    _valida_ruta_critica=lambda r: False,
    mover_archivo=lambda o, d: (os.replace(o, os.path.join(d, os.path.basename(o))), {"exito": True, "mensaje": "movido"})[1],
    renombrar=lambda r, n: (os.rename(r, os.path.join(os.path.dirname(r), n)), {"exito": True, "mensaje": "renombrado"})[1],
    copiar_archivo=lambda o, d: (open(os.path.join(d, os.path.basename(o)), "wb").write(open(o, "rb").read()), {"exito": True, "mensaje": "copiado"})[1])
sys.modules["file_actions"] = fa; sys.modules["file_index"] = types.SimpleNamespace(registrar_ruta=lambda r: None)
import file_manager as fm
fm.file_actions = fa; fm.deshacer = de
w = tempfile.mkdtemp(); d1 = os.path.join(w, "dest"); os.makedirs(d1)
x = os.path.join(w, "x.txt"); open(x, "w").write("X")
r = fm.confirmar_gestion_archivo("eliminar", {"ruta": x}); ok("file_manager: eliminar usa la papelera propia", r["exito"] and not os.path.exists(x) and "papelera de SARA" in r["texto"], r)
de.deshacer_ultimo(); ok("…y se deshace", os.path.exists(x))
open(os.path.join(d1, "x.txt"), "w").write("PISADO")
r = fm.confirmar_gestion_archivo("mover", {"origen": x, "destino": d1}); ok("mover registra y guarda lo pisado", r["exito"] and open(os.path.join(d1, "x.txt")).read() == "X")
okk, m = de.deshacer_ultimo(); ok("deshacer el mover recupera ambos", okk and open(x).read() == "X" and open(os.path.join(d1, "x.txt")).read() == "PISADO", m)
r = fm.confirmar_gestion_archivo("renombrar", {"ruta": x, "nuevo": "y.txt"}); okk, m = de.deshacer_ultimo()
ok("renombrar se deshace", r["exito"] and okk and os.path.exists(x))
r = fm.confirmar_gestion_archivo("copiar", {"origen": x, "destino": d1}); ok("copiar se registra", r["exito"] and de.ultimo()["tipo"] == "copiar")
de.deshacer_ultimo()
ok("la copia se quitó", not os.path.exists(os.path.join(d1, "x.txt")) or open(os.path.join(d1, "x.txt")).read() == "PISADO")
config.DESHACER_ACTIVO = False; fm.deshacer = de
de.registrar({"tipo": "crear", "final": "/q"}); u = de.ultimo()
ok("DESHACER_ACTIVO=False apaga el diario", u is None or u["final"] != "/q")
config.DESHACER_ACTIVO = True

print("[5] modos")
config.BASE_DIR = D
for f, esp in [("modo clase", "clase"), ("activa el modo casa", "casa"), ("Sara, pon el modo mochila", "mochila"), ("entra al modo clase por favor", "clase")]:
    r = mo.interpretar(f); ok(f"«{f}»", bool(r) and r["accion"] == "activar" and r["nombre"] == esp, str(r))
for f in ["modo avión", "activa el modo desconocido", "abre chrome", "el modo de empleo"]:
    ok(f"NO «{f}»", mo.interpretar(f) is None, str(mo.interpretar(f)))
ok("«qué modos tengo» lista", mo.interpretar("qué modos tengo") == {"accion": "listar"})
ok("/modo nuevo guarda", mo.comando("nuevo estudio = abre word ; pon el volumen al 20")[0] and "estudio" in mo.todos())
ok("el modo nuevo ya se reconoce", mo.interpretar("modo estudio")["nombre"] == "estudio")
ok("rechaza acciones «/» o modos anidados", not mo.comando("nuevo malo = /apagar")[0] and not mo.comando("nuevo malo2 = modo clase")[0])
corridos = []
okk, t = mo.ejecutar("estudio", lambda c: (corridos.append(c) or (True, "ok")))
ok("ejecuta cada acción por el circuito", okk and corridos == ["abre word", "pon el volumen al 20"], str(corridos))
ok("durante un modo, otro modo no se dispara (sin bucles)", (setattr(mo._local, "dentro", True), mo.interpretar("modo clase"))[1] is None); mo._local.dentro = False
okk, t = mo.ejecutar("mochila", lambda c: (True, "ok"), remoto=True)
ok("acciones @ desde canal remoto: se omiten con aviso", not okk and "solo se puede desde la ventana" in t, t)
okk, t = mo.ejecutar("clase", lambda c: (False, "falló") if c == "abre chrome" else (True, "ok"))
ok("si una acción falla lo dice pero sigue", not okk and "✗ abre chrome" in t and "✓ mutea" in t, t)
ok("/modo clase → activa el modo clase", mo.reescribir_slash("/modo clase") == "activa el modo clase" and mo.reescribir_slash("/modo nuevo a = b") == "/modo nuevo a = b")
ok("borrar", mo.comando("borrar estudio")[0] and "estudio" not in mo.todos())

print("[6] antirrobo")
for f, esp in [("dónde está mi laptop", "donde"), ("a qué wifi está conectada la laptop", "donde"), ("/donde", "donde"), ("localiza mi laptop", "donde"),
               ("toma una foto", "foto"), ("toma una foto con la webcam", "foto"), ("mándame una foto con la cámara", "foto"), ("quién está frente a la laptop", "foto"), ("/foto", "foto")]:
    ok(f"«{f}»", ar.interpretar(f) == esp, str(ar.interpretar(f)))
for f in ["mándame una foto", "mándame la foto de las vacaciones", "dónde está el archivo", "toma el control"]:
    ok(f"NO «{f}»", ar.interpretar(f) is None, str(ar.interpretar(f)))
ok("parsea SSID (en/es) y no confunde BSSID", ar.parsear_ssid("    Name : Wi-Fi\n    SSID                   : MiCasa_5G\n    BSSID                  : aa:bb:cc") == "MiCasa_5G")
sys.modules["system_info"] = types.SimpleNamespace(obtener_ip_local=lambda: "192.168.1.5", obtener_ip_publica=lambda t=4: "187.1.2.3",
                                                    obtener_bateria=lambda: {"porcentaje": 55.0, "cargando": False})
ar._ssid = lambda: "MiCasa_5G"
t = ar.donde_texto(); ok("/donde incluye Wi-Fi, IPs y batería", all(s in t for s in ("MiCasa_5G", "192.168.1.5", "187.1.2.3", "55")), t)
okk, t = ar.ejecutar("dónde está mi laptop", lambda *a: True); ok("ejecutar donde", okk)
ar.capturar_foto = lambda: (None, "Falta opencv-python"); okk, t = ar.ejecutar("toma una foto", lambda *a: True)
ok("sin opencv lo dice", not okk and "opencv" in t)
ar.capturar_foto = lambda: (b"jpg", ""); env = []
okk, t = ar.ejecutar("toma una foto", lambda b, c, n: env.append(n) or True); ok("foto se envía como webcam.jpg", okk and env == ["webcam.jpg"])

print("[7] telegram_bot con verificación apagada + slashes")
import telegram_bot as tb
ap_stub = types.SimpleNamespace(duracion_texto=lambda s: f"{s} segundos", etiqueta=lambda a: "apagar el equipo" if a == "apagar" else a)
config.VERIFICACION_ACTIVA = False
ok("_verif_activa refleja el switch", tb._verif_activa() is False)
txt = tb._advertencia_sin_codigo(ap_stub, 30, "apagar")
ok("el aviso sin verificación no habla de códigos", "código" not in txt.lower() and "WhatsApp" not in txt and "verificación apagada" in txt, txt)
config.VERIFICACION_ACTIVA = True; ok("encendida", tb._verif_activa() is True)
store = {"owner": "111", "net": "1"}
tb._token = lambda: "123456:" + "A" * 35; tb._kv_get = lambda k, d=None: store.get(k, d)
resp = []; tb._responder = lambda t: resp.append(t) or True
tb._api = lambda *a, **k: {"ok": True, "result": {"message_id": 1}}      # nunca tocar la red real
_apag_llamadas = []
_orig_apag = tb._intentar_apagado
tb._intentar_apagado = lambda o, v=False: (_apag_llamadas.append(o) or True) if "bloquea" in o else _orig_apag(o, v)
for cmd, esperado in [("/donde", "dónde está mi laptop"), ("/foto", "toma una foto con la webcam"), ("/bloquear", "bloquea la pantalla"),
                      ("/deshacer", "deshaz lo último"), ("/modo clase", "activa el modo clase"), ("/modos", "qué modos tengo"), ("/pantalla", "quiero ver la pantalla")]:
    while not tb._cola.empty(): tb._cola.get_nowait()
    tb._cmds.clear(); tb._atender_slash(cmd)
    got = tb._cola.get_nowait()[0] if not tb._cola.empty() else None
    if cmd == "/bloquear":          # lo atiende apagado.py (botón de confirmación), no la cola del núcleo
        ok("/bloquear → flujo de bloqueo con botón (apagado.py)", _apag_llamadas == ["bloquea la pantalla"], str(_apag_llamadas))
        continue
    ok(f"{cmd} → «{esperado}»", got == esperado, str(got))
tb._atender_slash("/modo nuevo x = abre chrome"); ok("crear modo desde Telegram: rechazado", "solo se puede desde la ventana" in resp[-1])
tb._atender_slash("/verificacion off"); ok("/verificacion off desde Telegram: rechazado", config.VERIFICACION_ACTIVA and "ventana" in resp[-1], resp[-1])
tb._atender_slash("/descripcion on"); ok("/descripcion on desde Telegram funciona", config.PANTALLA_DESCRIBIR is True)
tb._atender_slash("/descripcion off"); ok("/descripcion off desde Telegram funciona", config.PANTALLA_DESCRIBIR is False)
tb._atender_slash("/ayuda"); ok("la ayuda del bot lista los comandos nuevos", all(c in resp[-1] for c in ("/donde", "/foto", "/deshacer", "/modos", "/descripcion", "/verificacion", "/bloquear", "/pantalla", "/alertas")), resp[-1])

print("[8] core_bridge (estático)")
src = open("core_bridge.py", encoding="utf-8").read()
ayuda = src[src.index('_AYUDA = """'):src.index('Cualquier otra cosa se envía')]
ok("/ayuda lista todos los comandos nuevos", all(c in ayuda for c in ("/descripcion", "/verificacion", "/deshacer", "/papelera", "/modos", "/donde", "/foto", "/pantalla", "/bloquear", "/alertas")))
ok("slash: /descripcion /verificacion /papelera /modos /donde /foto implementados", all(f'"{c}"' in src for c in ("/papelera", "/donde", "/foto", "/modos")) and "comando_descripcion" in src and "comando_verificacion" in src)
ok("gancho antes de volumen en las dos rutas", src.count("_atajos_nuevos(texto, emit)") == 2)
ok("purgar en el arranque", "deshacer.purgar()" in src)
print("\n" + ("TODO OK" if not fallos else f"FALLARON {len(fallos)}: " + "; ".join(fallos)))
sys.exit(1 if fallos else 0)
