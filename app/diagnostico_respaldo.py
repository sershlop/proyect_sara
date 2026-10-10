# 📁 diagnostico_respaldo.py — ¿Por qué SARA no habla con Gemini / Groq / DeepSeek?
# Uso (desde la carpeta del proyecto):   python diagnostico_respaldo.py
# No imprime tus claves: solo si existen, su longitud y el resultado de cada prueba.
import os, sys, re, importlib, json

RAIZ = os.path.dirname(os.path.abspath(__file__))
os.chdir(RAIZ)
sys.path.insert(0, RAIZ)
OK, MAL, AV = "[ OK ]", "[FALLA]", "[ AVISO]"
NOMBRES = ["USAR_GROQ_BACKUP", "GROQ_API_KEY", "GROQ_MODEL", "GROQ_MODEL_CODIGO", "GROQ_TEMPERATURA", "GROQ_MAX_TOKENS",
           "USAR_DEEPSEEK", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_TEMPERATURA", "DEEPSEEK_MAX_TOKENS",
           "USAR_GEMINI_BACKUP", "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_TEMPERATURA", "GEMINI_MAX_TOKENS",
           "RESPALDO_EXTERNO_TIMEOUT_SEGUNDOS"]

def masc(t, claves):
    t = str(t)
    for k in claves:
        if k and len(k) > 6:
            t = t.replace(k, "***")
    return t

print("\n== 1) Archivo .env ==")
env_path = os.path.join(RAIZ, ".env")
env = {}
if not os.path.exists(env_path):
    print(MAL, f".env NO existe en {RAIZ}")
else:
    print(OK, f".env encontrado ({os.path.getsize(env_path)} bytes)")
    raw = open(env_path, "rb").read()
    if raw.startswith(b"\xef\xbb\xbf"):
        print(MAL, "El .env tiene BOM (UTF-8 con firma): la primera variable queda con nombre corrupto. Guárdalo como UTF-8 sin BOM.")
    for n, linea in enumerate(raw.decode("utf-8-sig", "replace").splitlines(), 1):
        l = linea.strip()
        if not l or l.startswith("#"):
            continue
        if "=" not in l:
            print(AV, f"línea {n}: sin '=' → se ignora"); continue
        k, v = l.split("=", 1)
        k, v0 = k.strip(), v.strip()
        v = v0.strip('"').strip("'")
        env[k] = v
        if v0 != v0.strip('"').strip("'"):
            print(AV, f"{k}: valor con comillas (python-dotenv las quita, pero revisa que no sean parte de la clave)")
        if v != v.strip() or " " in v:
            print(AV, f"{k}: el valor contiene espacios")
        print(OK if v else MAL, f"{k} = {'(vacío)' if not v else f'presente, {len(v)} caracteres'}")

print("\n== 2) python-dotenv ==")
try:
    import dotenv
    print(OK, "python-dotenv instalado")
except ImportError:
    print(MAL, "python-dotenv NO instalado → nadie carga el .env.  pip install python-dotenv")

print("\n== 3) config.py ==")
cfg = None
try:
    cfg = importlib.import_module("config")
    print(OK, "config.py importa")
except Exception as e:
    print(MAL, f"config.py no importa: {type(e).__name__}: {e}")
claves = []
faltan = []
if cfg:
    src = open(os.path.join(RAIZ, "config.py"), encoding="utf-8", errors="replace").read()
    codigo = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))   # ignora comentarios
    i_dot = codigo.find("load_dotenv(")
    i_get = codigo.find("os.getenv(")
    src = codigo
    if i_dot < 0:
        print(MAL, "config.py NO llama a load_dotenv → el .env nunca se carga")
    elif i_get >= 0 and i_dot > i_get:
        print(MAL, "config.py llama a load_dotenv DESPUÉS del primer os.getenv → las claves se leen vacías (ESTE ES EL BUG CLÁSICO)")
    else:
        print(OK, "load_dotenv se ejecuta antes de leer las claves")
    for n in NOMBRES:
        if not hasattr(cfg, n):
            faltan.append(n)
    if faltan:
        print(MAL, "Faltan en config.py (basta UNA para que respaldo_externo se DESACTIVE en silencio):")
        for n in faltan:
            print("        -", n)
    else:
        print(OK, "config.py define todos los nombres que respaldo_externo importa")
    for n in ("USAR_GEMINI_BACKUP", "USAR_GROQ_BACKUP", "USAR_DEEPSEEK"):
        print(OK if getattr(cfg, n, False) else AV, f"{n} = {getattr(cfg, n, '(no definido)')}")
    for n in ("GEMINI_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY"):
        v = getattr(cfg, n, "")
        claves.append(v)
        print(OK if v else MAL, f"{n}: {'vacía' if not v else f'cargada ({len(v)} caracteres)'}")
    for n in ("GEMINI_MODEL", "GROQ_MODEL", "GROQ_MODEL_CODIGO", "DEEPSEEK_MODEL"):
        print("       ", n, "=", getattr(cfg, n, "(no definido)"))

print("\n== 4) respaldo_externo ==")
try:
    import respaldo_externo as r
    print(("gemini_disponible:", r.gemini_disponible()), ("groq:", r.groq_disponible()), ("deepseek:", r.deepseek_disponible()))
except Exception as e:
    print(MAL, f"respaldo_externo no importa: {type(e).__name__}: {e}"); r = None

print("\n== 5) Llamadas reales (1 token) ==")
try:
    import requests
except ImportError:
    print(MAL, "requests no instalado"); sys.exit()

def prueba(nombre, fn):
    try:
        resp = fn()
        cuerpo = masc(resp.text[:300].replace("\n", " "), claves)
        print(OK if resp.ok else MAL, f"{nombre}: HTTP {resp.status_code}  {'' if resp.ok else cuerpo}")
        if resp.status_code in (401, 403):
            print("        → clave inválida, revocada o sin permisos")
        if resp.status_code == 404:
            print("        → el MODELO ya no existe con ese nombre: cámbialo en config.py")
        if resp.status_code == 429:
            print("        → límite de cuota alcanzado")
    except Exception as e:
        print(MAL, f"{nombre}: {type(e).__name__}: {masc(e, claves)}")
        print("        → sin internet, firewall/antivirus o DNS bloqueando la conexión")

g = lambda n: getattr(cfg, n, "") if cfg else ""
if g("GEMINI_API_KEY") and g("GEMINI_MODEL"):
    prueba("Gemini", lambda: requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{g('GEMINI_MODEL')}:generateContent",
        headers={"x-goog-api-key": g("GEMINI_API_KEY")},
        json={"contents": [{"parts": [{"text": "hola"}]}], "generationConfig": {"maxOutputTokens": 5}}, timeout=20))
else:
    print(AV, "Gemini: sin clave o sin modelo en config → no se prueba")
if g("GROQ_API_KEY") and g("GROQ_MODEL"):
    prueba("Groq", lambda: requests.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {g('GROQ_API_KEY')}"},
        json={"model": g("GROQ_MODEL"), "messages": [{"role": "user", "content": "hola"}], "max_tokens": 5}, timeout=20))
else:
    print(AV, "Groq: sin clave o sin modelo en config → no se prueba")
if g("DEEPSEEK_API_KEY") and g("DEEPSEEK_MODEL"):
    prueba("DeepSeek", lambda: requests.post("https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {g('DEEPSEEK_API_KEY')}"},
        json={"model": g("DEEPSEEK_MODEL"), "messages": [{"role": "user", "content": "hola"}], "max_tokens": 5}, timeout=20))
else:
    print(AV, "DeepSeek: sin clave o sin modelo en config → no se prueba")

print("\n== 6) Modelos que tu clave SÍ puede usar (para elegir GEMINI_MODEL / GROQ_MODEL / DEEPSEEK_MODEL) ==")
def listar(nombre, fn, extraer):
    try:
        resp = fn()
        if not resp.ok:
            print(AV, f"{nombre}: no pude listar (HTTP {resp.status_code})"); return
        print(OK, f"{nombre}:", ", ".join(extraer(resp.json())[:25]) or "(vacío)")
    except Exception as e:
        print(AV, f"{nombre}: {type(e).__name__}: {masc(e, claves)}")
if g("GEMINI_API_KEY"):
    listar("Gemini (generateContent)", lambda: requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
        headers={"x-goog-api-key": g("GEMINI_API_KEY")}, timeout=20),
        lambda j: [m["name"].split("/")[-1] for m in j.get("models", [])
                   if "generateContent" in m.get("supportedGenerationMethods", []) and "flash" in m["name"]])
if g("GROQ_API_KEY"):
    listar("Groq", lambda: requests.get("https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {g('GROQ_API_KEY')}"}, timeout=20),
        lambda j: sorted(m["id"] for m in j.get("data", [])))
if g("DEEPSEEK_API_KEY"):
    listar("DeepSeek", lambda: requests.get("https://api.deepseek.com/models",
        headers={"Authorization": f"Bearer {g('DEEPSEEK_API_KEY')}"}, timeout=20),
        lambda j: sorted(m["id"] for m in j.get("data", [])))
print("\nListo. Copia TODA esta salida (no contiene tus claves) y pásamela.")
