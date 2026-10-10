import importlib.util, json, os, sys, types, logging
logging.basicConfig(level=logging.ERROR)
try: import requests
except Exception: sys.modules["requests"] = types.ModuleType("requests")
spec = importlib.util.spec_from_file_location("whatsapp", "whatsapp.py")
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)

# ── libreta SINTÉTICA con el mismo patrón que tu archivo: cada persona aparece como número Y como @lid ──
nombres = ["Mamá", "Juan", "Juan Pérez", "Ana López", "Luis", "María José", "Tía Carmen", "Pedro", "Amor", "Vicente Cruz"]
libreta = []
for i, n in enumerate(nombres):
    libreta.append({"jid": f"5215500000{i:03d}@s.whatsapp.net", "nombre": n, "notify": ""})
    libreta.append({"jid": f"9990000000{i:03d}@lid", "nombre": n, "notify": ""})
libreta.append({"jid": "5215599999999@s.whatsapp.net", "nombre": "", "notify": "Tigre"})
os.makedirs("wa_sidecar", exist_ok=True)
json.dump(libreta, open("wa_sidecar/contactos.json", "w", encoding="utf-8"), ensure_ascii=False)
w._dir = lambda: os.path.join(os.getcwd(), "wa_sidecar")

# sidecar simulado: mismo criterio que index.js (todos los tokens contenidos en «nombre notify», sin tildes)
def sidecar(q, limit=8):
    toks = w._plano(q).split()
    res = [c for c in libreta if toks and all(t in w._plano(c["nombre"] + " " + c["notify"]) for t in toks)]
    res.sort(key=lambda c: 0 if c["nombre"] else 1)
    return [{"jid": c["jid"], "nombre": c["nombre"] or c["notify"], "libreta": bool(c["nombre"])} for c in res[:limit]]
def http(metodo, ruta, datos=None, params=None, timeout=10):
    if ruta == "/contacts": return {"contactos": sidecar(params["q"], params.get("limit", 8))}
    return {"ok": True}
w._http = http
w._kv_get = lambda k, d=None: d
w._kv_set = lambda k, v: None

# ── 1) INTERPRETACIÓN: (frase, contacto, mensaje) ──
ENVIAR = [
 ("mándale un mensaje a Juan que ya voy", "Juan", "ya voy"),
 ("manda un mensaje a mamá diciendo que llego tarde", "mamá", "llego tarde"),
 ("manda un mensaje a mamá diciéndole que llego tarde", "mamá", "llego tarde"),
 ("mándale un whatsapp a Juan que ya voy", "Juan", "ya voy"),
 ("mándale un whatsapp a Juan diciéndole que ya voy", "Juan", "ya voy"),
 ("dile a Juan que ya voy", "Juan", "ya voy"),
 ("dile a Juan por whatsapp que ya voy", "Juan", "ya voy"),
 ("escríbele a Juan que ya voy", "Juan", "ya voy"),
 ("avísale a mamá por whatsapp que llego tarde", "mamá", "llego tarde"),
 ("envíale un mensaje a Pedro: nos vemos a las 5", "Pedro", "nos vemos a las 5"),
 ("mándale a Juan que ya voy", "Juan", "ya voy"),
 ("manda un wasap a Juan que ya voy", "Juan", "ya voy"),
 ("mándale un guasap a Juan que ya voy", "Juan", "ya voy"),
 ("Sara, mándale un mensaje a mi mamá que ya llegué", "mi mamá", "ya llegué"),
 ("oye sara escríbele a la tía Carmen que mañana voy", "la tía Carmen", "mañana voy"),
 ("por favor dile a Ana López que la junta es a las 3", "Ana López", "la junta es a las 3"),
 ("mándale un mensaje a Ana López ya estoy afuera", "Ana López", "ya estoy afuera"),     # sin separador: parte con tu libreta
 ("mándale un mensaje a Luis hola cómo estás", "Luis", "hola cómo estás"),
 ("dile a María José nos vemos mañana", "María José", "nos vemos mañana"),                # dile + nombre de libreta
 ("manda un whatsapp a Juan Pérez que ya salí", "Juan Pérez", "ya salí"),
 ("pregúntale a Luis si ya salió", "Luis", "¿Ya salió?"),
 ("pregúntale a mamá cuándo llega", "mamá", "¿Cuándo llega?"),
 ("mándale un mensaje a Pedro diciendole: llego en 5", "Pedro", "llego en 5"),
 ("avísale a Juan que hoy no voy, que me siento mal", "Juan", "hoy no voy, que me siento mal"),   # el 2.º «que» es del mensaje
 ("mándame un whatsapp que compre leche", "yo", "compre leche"),
 ("avísame por whatsapp que ya terminó la descarga", "yo", "ya terminó la descarga"),
 ("mándale un mensaje a Vicente Cruz que ya voy para allá", "Vicente Cruz", "ya voy para allá"),
]
NO = [
 "mándale el archivo a Juan", "manda a imprimir el documento", "escribe un correo a Juan que llego tarde",
 "manda un correo a Pedro que ya voy", "mándale un mensaje por telegram a Juan que ya voy", "escribe a mano",
 "dile a Sara que se calle y abre youtube y ya", "abre whatsapp", "pon música", "sube el volumen", "dime mi ip",
 "mándale a Juan al carajo", "escribe un archivo nuevo", "envía el reporte por correo", "crea una nota que diga hola",
 "avísame cuando termine", "recuérdame llamar a Juan", "responde", "busca un mensaje de bienvenida",
]
mal = 0
for frase, c, m in ENVIAR:
    a = w._analizar(frase)
    ok = a and a["accion"] == "enviar" and w._clave(a["contacto"]) == w._clave(c) and a["mensaje"] == m
    if not ok: mal += 1; print("❌ ENVIAR", repr(frase), "→", a, "| esperado", (c, m))
for frase in NO:
    if w.es_comando(frase):
        mal += 1; print("❌ NO     ", repr(frase), "→", w._analizar(frase))
print(f"interpretación: {len(ENVIAR)} positivos + {len(NO)} negativos → fallos: {mal}")

# ── 2) es_comando: lo que antes fallaba ahora entra por el atajo directo ──
for f in ["mándale un mensaje a Juan que ya voy", "dile a Juan que ya voy", "escríbele a Juan que ya voy", "mándale a Juan que ya voy"]:
    assert w.es_comando(f), f
assert w.es_comando("lee mis mensajes de whatsapp") and w.es_comando("lee los mensajes de Juan por whatsapp")
assert not w.es_comando("lee mis mensajes")                    # leer/responder siguen pidiendo decir whatsapp
assert w.es_comando("responde por whatsapp que ya voy") is True
print("es_comando: OK")

# ── 3) falta el mensaje ──
a = w._analizar("manda un mensaje a Juan"); assert a and a["accion"] == "falta_mensaje" and a["contacto"].lower() == "juan", a
w.estado = lambda: {"estado": "abierto"}
r = w.resolver({"texto_original": "manda un mensaje a Juan"}); assert r["exito"] is False and "¿Qué le mando a Juan?" in r["texto"], r
print("falta mensaje: OK ->", r["texto"])

# ── 4) CONTACTOS: duplicados @lid + número, ambigüedad real, y nombre mal oído ──
est, d = w._resolver_contacto("mamá");        assert est == "ok" and d["jid"].endswith("@s.whatsapp.net"), (est, d)   # antes: «varios contactos Mamá»
est, d = w._resolver_contacto("mi mamá");     assert est == "ok" and d["nombre"] == "Mamá", (est, d)
est, d = w._resolver_contacto("Ana López");   assert est == "ok" and d["nombre"] == "Ana López", (est, d)
est, d = w._resolver_contacto("tía Carmen");  assert est == "ok", (est, d)
est, d = w._resolver_contacto("Juan");        assert est == "ok" and d["nombre"] == "Juan", (est, d)                    # exacto gana a «Juan Pérez»
est, d = w._resolver_contacto("Maria");       assert est == "ok" and d["nombre"] == "María José", (est, d)             # único con ese nombre
est, d = w._resolver_contacto("Tigre");       assert est == "ok" and d["nombre"] == "Tigre", (est, d)                  # solo pushName
est, d = w._resolver_contacto("Perez");       assert est == "ok" and d["nombre"] == "Juan Pérez", (est, d)
est, d = w._resolver_contacto("5215512345678"); assert est == "ok" and d["jid"] == "5215512345678", (est, d)
est, d = w._resolver_contacto("Yuan");        assert est == "aprox" and d["nombre"] in ("Juan", "Juan Pérez"), (est, d)  # mal oído por el micrófono
est, d = w._resolver_contacto("Bicente");     assert est in ("aprox", "ok") and "Vicente" in d["nombre"], (est, d)
est, d = w._resolver_contacto("Zzzxq");       assert est == "no", (est, d)
print("resolución de contactos: OK (sin duplicados, exacto gana, difuso confirma)")

# ── 5) plan completo: confirmación con el contacto correcto ──
r = w.resolver({"texto_original": "mándale un mensaje a mamá que ya voy"})
assert r["estado"] == "candidato" and r["nombre"] == "Mamá" and r["mensaje"] == "ya voy" and "¿Envío por WhatsApp a Mamá?" in r["mensaje_confirmacion"], r
r = w.resolver({"texto_original": "dile a Yuan que ya voy"})
assert r["estado"] == "candidato" and "No encontré «Yuan» tal cual" in r["mensaje_confirmacion"], r
r = w.resolver({"texto_original": "dile a Zzzxq que ya voy"}); assert r["estado"] == "no_encontrado"
print("plan de envío: OK")
print(r["texto"])
