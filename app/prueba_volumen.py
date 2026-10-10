import logging
logging.basicConfig(level=logging.ERROR)
import volumen as v

# ── 1) Interpretación: (frase, acción, pct) — pct None = no se exige ──
SI = [
 # subir
 ("sube el volumen", "subir", 10), ("Sara, sube el volumen por favor", "subir", 10),
 ("súbele al volumen", "subir", 10), ("súbele tantito", "subir", 5), ("subele un poco", "subir", 5),
 ("sube mucho el volumen", "subir", 25), ("sube el volumen 20", "subir", 20), ("súbele 15 al sonido", "subir", 15),
 ("aumenta el volumen", "subir", 10), ("ponle más volumen", "subir", 10), ("más fuerte", "subir", 10),
 ("ponlo más fuerte", "subir", 10), ("más alto por favor", "subir", 10), ("sube la música", "subir", 10),
 ("no se oye", "subir", 10), ("casi no se escucha nada", "subir", 10), ("no te escucho", "subir", 10),
 ("se oye muy bajito", "subir", 10), ("está muy bajo el volumen", "subir", 10), ("sube el bolumen", "subir", 10),
 ("on, sube el volumen", "subir", 10),
 # bajar
 ("baja el volumen", "bajar", 10), ("bájale", "bajar", 10), ("bájale tantito", "bajar", 5),
 ("bájale un poquito a la música", "bajar", 5), ("menos volumen", "bajar", 10), ("más bajito", "bajar", 10),
 ("está muy fuerte la música", "bajar", 10), ("se oye muy fuerte", "bajar", 10), ("baja el sonido", "bajar", 10),
 ("reduce el volumen bastante", "bajar", 25), ("baja el audio 30", "bajar", 30),
 # fijar
 ("pon el volumen al 50", "fijar", 50), ("pon el volumen al 50 por ciento", "fijar", 50), ("volumen al 30%", "fijar", 30),
 ("sube el volumen al 80", "fijar", 80), ("baja el volumen a 20", "fijar", 20), ("volumen 40", "fijar", 40),
 ("ponle el volumen en 70", "fijar", 70), ("pon el volumen a la mitad", "fijar", 50),
 ("sube el volumen al máximo", "fijar", 100), ("volumen al máximo", "fijar", 100),
 ("baja el volumen al mínimo", "fijar", 0), ("pon el volumen a cero", "fijar", 0),
 ("pon el volumen al cincuenta por ciento", "fijar", 50), ("deja el sonido en setenta y cinco", "fijar", 75),
 # mutear
 ("silencia", "mutear", None), ("mutea", "mutear", None), ("mute", "mutear", None), ("silencia el sonido", "mutear", None),
 ("quita el sonido", "mutear", None), ("apaga el sonido", "mutear", None), ("pon en silencio", "mutear", None),
 ("ponlo en mute", "mutear", None), ("apaga las bocinas", "mutear", None), ("sin sonido por favor", "mutear", None),
 ("Sara mutea eso", "mutear", None),
 # desmutear
 ("quita el mute", "desmutear", None), ("quítale el mute", "desmutear", None), ("desmutea", "desmutear", None),
 ("activa el sonido", "desmutear", None), ("ponle sonido", "desmutear", None), ("quita el silencio", "desmutear", None),
 ("reactiva el audio", "desmutear", None), ("pon el sonido", "desmutear", None), ("que vuelva el sonido", "desmutear", None),
 # consultar
 ("a cuánto está el volumen", "consultar", None), ("cuánto volumen tengo", "consultar", None),
 ("qué volumen tengo", "consultar", None), ("está muteado", "consultar", None),
 ("dime el volumen", "consultar", None), ("dime el volumen exacto", "consultar", None), ("cuál es el volumen", "consultar", None),
 ("cuál es el volumen actual", "consultar", None), ("en qué volumen estamos", "consultar", None),
 ("en cuánto está el volumen", "consultar", None), ("qué nivel de volumen tengo", "consultar", None),
 ("qué tan alto está el volumen", "consultar", None), ("Sara, dime a cuánto está el volumen", "consultar", None),
 ("dime si está muteado", "consultar", None), ("volumen actual", "consultar", None), ("cómo está el volumen", "consultar", None),
]
NO = [
 "abre el archivo volumen.txt", "sube el archivo a drive", "baja la carpeta", "busca cómo subir el volumen en windows",
 "qué es el volumen de un cubo", "cómo subo el volumen", "pon música", "pon música en youtube", "pon el volumen",
 "volumen", "sube", "baja", "abre youtube y sube el volumen", "sube el volumen de la tele", "baja el volumen del carro",
 "apaga la música", "apaga la computadora", "quita la música", "hola sara cómo estás", "dime mi ip",
 "pon menea tu chapa en youtube", "abre spotify", "mi mamá dice que el volumen de una caja es largo por ancho por alto",
 "sube el brillo", "baja el brillo de la pantalla", "silencio en la sala por favor mientras explico el proyecto a todos",
 "cierra chrome", "crea una tarea subir el volumen", "reproduce reguetón en youtube", "baja el archivo informe",
 "qué significa mute", "explícame cómo funciona el volumen", "cuál es el volumen de una esfera", "dime el volumen de un cilindro",
 "dime mi ip y el volumen de la caja", "dime el volumen de agua que cabe en un litro",
]
mal = 0
for frase, acc, pct in SI:
    p = v.interpretar(frase)
    ok = p is not None and p["accion"] == acc and (pct is None or p["pct"] == pct)
    if not ok:
        mal += 1
        print("❌ SI ", repr(frase), "→", p, "| esperado", acc, pct)
for frase in NO:
    p = v.interpretar(frase)
    if p is not None:
        mal += 1
        print("❌ NO ", repr(frase), "→", p)
print(f"interpretación: {len(SI)} positivos + {len(NO)} negativos → fallos: {mal}")

# ── 2) Ejecución con backend simulado EXACTO (pycaw) ──
class Exacto:
    nombre, exacto = "pycaw", True
    def __init__(s, n=50, m=False): s.n, s.m, s.log = n, m, []
    def get_level(s): return s.n
    def set_level(s, p): s.n = p; s.log.append(("nivel", p))
    def get_mute(s): return s.m
    def set_mute(s, x): s.m = x; s.log.append(("mute", x))

def correr(b, frase):
    v.usar_backend(b); ok, msg = v.ejecutar(frase); return ok, msg

b = Exacto(50);            r = correr(b, "sube el volumen");               assert r == (True, "Volumen al 60 %."), r
b = Exacto(95);            r = correr(b, "súbele mucho");                  assert r == (True, "Volumen al máximo."), r
b = Exacto(100);           r = correr(b, "sube el volumen");               assert r == (True, "Ya está al máximo."), r
b = Exacto(50, m=True);    r = correr(b, "sube el volumen");               assert r == (True, "Volumen al 60 %.") and b.m is False, (r, b.m)
b = Exacto(3);             r = correr(b, "baja el volumen");               assert r == (True, "Volumen al mínimo."), r
b = Exacto(0);             r = correr(b, "baja el volumen");               assert r == (True, "Ya está al mínimo."), r
b = Exacto(50);            r = correr(b, "pon el volumen al 35");          assert r == (True, "Volumen al 35 %."), r
b = Exacto(50, m=True);    r = correr(b, "pon el volumen al 35");          assert b.m is False and b.n == 35, (b.m, b.n)
b = Exacto(50);            r = correr(b, "sube el volumen al máximo");     assert r == (True, "Volumen al máximo.") and b.n == 100, r
b = Exacto(50);            r = correr(b, "silencia");                      assert r == (True, "Silenciado.") and b.m is True, r
b = Exacto(50, m=True);    r = correr(b, "silencia");                      assert r == (True, "Ya estaba silenciado."), r
b = Exacto(40, m=True);    r = correr(b, "quita el mute");                 assert r == (True, "Sonido activado, volumen en 40 %.") and b.m is False, r
b = Exacto(40);            r = correr(b, "quita el mute");                 assert r == (True, "El sonido ya estaba activo, volumen en 40 %."), r
b = Exacto(40);            r = correr(b, "a cuánto está el volumen");      assert r == (True, "El volumen está en 40 %."), r
b = Exacto(40, m=True);    r = correr(b, "cuánto volumen tengo");          assert r == (True, "Está silenciado; el volumen está en 40 %."), r
print("backend exacto: OK (15 escenarios)")

# ── 3) Ejecución con backend de TECLAS simulado ──
class Teclas(v._BackendTeclas):
    def __init__(s):
        s.pulsos, s._mute_asumido = [], False
    def _tecla(s, vk, veces=1): s.pulsos.append((vk, veces))
b = Teclas(); r = correr(b, "sube el volumen");        assert r == (True, "Subí el volumen.") and b.pulsos == [(0xAF, 5)], (r, b.pulsos)
b = Teclas(); r = correr(b, "bájale tantito");         assert r == (True, "Bajé el volumen.") and b.pulsos == [(0xAE, 3)], (r, b.pulsos)   # 5% → round(2.5)=2 o 3
b = Teclas(); r = correr(b, "pon el volumen al 30");   assert b.pulsos == [(0xAE, 50), (0xAF, 15)], b.pulsos
b = Teclas(); r = correr(b, "silencia");               assert r == (True, "Silenciado.") and b.pulsos == [(0xAD, 1)], b.pulsos
b = Teclas(); correr(b, "silencia"); b.pulsos.clear(); r = correr(b, "silencia"); assert b.pulsos == [], b.pulsos   # no alterna dos veces
b = Teclas(); r = correr(b, "quita el mute");          assert b.pulsos == [(0xAF, 1), (0xAE, 1)], b.pulsos
b = Teclas(); r = correr(b, "a cuánto está el volumen"); assert r[0] is False and "pycaw" in r[1], r
print("backend teclas: OK (7 escenarios)")

# ── 4) Fallos: pycaw se rompe en pleno uso → degrada a teclas; sin backend → mensaje claro ──
class Roto(Exacto):
    def get_level(s): raise RuntimeError("COM no inicializado")
original = v._obtener_backend
llamadas = []
def _simulado():
    llamadas.append(1)
    if len(llamadas) == 1:
        return Roto()                       # 1.ª vez: pycaw (roto)
    v._backend, v._backend_probado = Teclas(), True
    return v._backend                       # 2.ª vez (tras degradar): teclas
v._obtener_backend = _simulado
v._pycaw_roto = False
ok, msg = v.ejecutar("sube el volumen")
assert ok and msg == "Subí el volumen." and v._pycaw_roto is True and len(llamadas) == 2, (ok, msg, v._pycaw_roto, llamadas)
v._obtener_backend = original
v._backend, v._backend_probado, v._pycaw_roto = None, False, False
ok, msg = v.ejecutar("sube el volumen")        # en Linux no hay backend
assert ok is False and "Windows" in msg, msg
print("degradación pycaw → teclas y sin backend: OK ->", msg)
print("diagnóstico:", v.diagnostico())
