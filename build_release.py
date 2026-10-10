# -*- coding: utf-8 -*-
"""
build_release.py - Arma el .zip compartible de SARA a partir de tu carpeta de desarrollo.

Uso (desde C:\\SARA_release, con cualquier Python 3.10+):
    python build_release.py --src C:\\saradefinitiva --version 2.0.1
    python build_release.py --src C:\\saradefinitiva --version 2.0.1 --solo-revisar   (no crea el zip)

Filosofia: LISTA BLANCA. Solo viaja lo que esta permitido; todo lo demas (tu .env, sara.db, logs,
la sesion de WhatsApp, tests...) se queda fuera aunque aparezca en la carpeta. Despues:
  1. verifica que ningun archivo incluido importe un modulo que quedo excluido,
  2. escanea secretos (API keys, tokens) y ABORTA si encuentra alguno,
  3. descarga (una vez) el Python embebido y get-pip, y fija sus hashes.
"""
import argparse
import ast
import hashlib
import json
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PYTHON_EMBED_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
PINS = AQUI / "release_pins.json"          # hashes fijados de lo que se descarga

# --- que viaja de la carpeta de desarrollo (raiz) a app/
EXCLUIR_PY = [r"^test_.*\.py$", r"^prueba_.*\.py$", r"^aplicar_parche_.*\.py$", r"^diagnostico_groq\.py$",
              r"^navegadores\.py$", r"^main\.py$"]          # main.py = terminal heredada; navegadores.py = huerfano
CARPETAS_GUI = ["static"]                                   # la interfaz vive en static/ (index.html, app.js, style.css, aureola*.js)
GUI_OBLIGATORIA = ["static/index.html", "static/app.js", "static/style.css"]   # lo que server.py /health espera
WA_PERMITIDOS = ["index.js", "package.json", "package-lock.json"]   # NUNCA auth/, audio_tmp/, contactos.json...
NUNCA = {".env", "sara.db", "sara.db-wal", "sara.db-shm", ".nucleo_instalado", "stt_config.json",
         "navegadores_uso.json"}

SECRETOS = [
    ("clave Groq", re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    ("clave Google/Gemini", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("clave tipo sk-", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("token de Telegram", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")),
    ("API key asignada", re.compile(r"(?:GROQ|GEMINI|DEEPSEEK|OPENAI|ANTHROPIC)_API_KEY\s*=\s*['\"]?[A-Za-z0-9_\-]{12,}")),
]


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def recopilar(src: Path) -> dict:
    """Devuelve {ruta_relativa_en_app: ruta_origen}."""
    out = {}
    excl = [re.compile(p, re.I) for p in EXCLUIR_PY]
    for p in sorted(src.glob("*.py")):
        if not any(e.match(p.name) for e in excl) and p.name not in NUNCA:
            out[p.name] = p
    for carpeta in CARPETAS_GUI:
        base = src / carpeta
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts and p.name not in NUNCA:
                    out[p.relative_to(src).as_posix()] = p
    wa = src / "wa_sidecar"
    for nombre in WA_PERMITIDOS:
        if (wa / nombre).is_file():
            out[f"wa_sidecar/{nombre}"] = wa / nombre
    return out


def revisar_imports(src: Path, incluidos: dict) -> list:
    """Avisa si un archivo incluido importa un modulo local que se quedo fuera."""
    locales = {p.stem for p in src.glob("*.py")}
    dentro = {Path(k).stem for k in incluidos if k.endswith(".py") and "/" not in k}
    problemas = []
    for rel, ruta in incluidos.items():
        if not rel.endswith(".py") or "/" in rel:
            continue
        try:
            arbol = ast.parse(ruta.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            problemas.append(f"{rel}: NO COMPILA ({e.msg}, linea {e.lineno})")
            continue
        usados = set()
        for n in ast.walk(arbol):
            if isinstance(n, ast.Import):
                usados |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                usados.add(n.module.split(".")[0])
            elif isinstance(n, ast.Call):
                nombre = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
                if nombre in ("_importar", "import_module", "__import__") and n.args \
                        and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                    usados.add(n.args[0].value.split(".")[0])
        for m in sorted((usados & locales) - dentro):
            problemas.append(f"{rel} importa '{m}', que NO viaja en el paquete")
    return problemas


def revisar_gui(incluidos: dict) -> list:
    """La interfaz debe viajar completa: lo obligatorio y todo lo que index.html referencia en /static/."""
    errores = [f"falta {f}" for f in GUI_OBLIGATORIA if f not in incluidos]
    idx = incluidos.get("static/index.html")
    if idx:
        html = idx.read_text(encoding="utf-8", errors="replace")
        for ref in sorted(set(re.findall(r"""(?:src|href)=["']/static/([^"'?#]+)""", html))):
            if f"static/{ref}" not in incluidos:
                errores.append(f"index.html referencia /static/{ref} y no viaja")
    return errores


def _valor(texto: str, patron: str):
    m = re.search(patron, texto)
    return m.group(1) if m else None


def revisar_modelos(src: Path) -> list:
    """Los modelos que descarga bootstrap.py deben ser los mismos que usa config.py."""
    avisos = []
    cfg = (src / "config.py").read_text(encoding="utf-8", errors="replace")
    boot = (AQUI / "bootstrap" / "bootstrap.py").read_text(encoding="utf-8")
    pares = [("modelo de embeddings", r'^MODELO_EMBEDDINGS\s*=\s*["\']([^"\']+)', r'^MODELO_EMBEDDINGS\s*=\s*["\']([^"\']+)'),
             ("modelo de Ollama", r'^QWEN_MODEL\s*=\s*["\']([^"\']+)', r'^OLLAMA_MODELO\s*=\s*["\']([^"\']+)'),
             ("modelo Whisper", r'_d\(\s*["\']STT_MODELO["\']\s*,\s*["\']([^"\']+)', r'^MODELO_WHISPER\s*=\s*["\']([^"\']+)')]
    for nombre, pc, pb in pares:
        c = _valor(cfg, pc) if "^" not in pc else (re.search(pc, cfg, re.M).group(1) if re.search(pc, cfg, re.M) else None)
        b = re.search(pb, boot, re.M)
        b = b.group(1) if b else None
        if c is None or b is None:
            avisos.append(f"{nombre}: no pude comparar (config={c}, bootstrap={b})")
        elif c != b:
            avisos.append(f"{nombre}: config.py usa '{c}' pero bootstrap.py descargaria '{b}'")
    return avisos


def escanear_secretos(incluidos: dict) -> list:
    hallazgos = []
    for rel, ruta in incluidos.items():
        if ruta.suffix.lower() in {".png", ".jpg", ".ico", ".woff", ".woff2", ".ttf", ".mp3"}:
            continue
        try:
            texto = ruta.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for nombre, rx in SECRETOS:
            m = rx.search(texto)
            if m:
                hallazgos.append(f"{rel}: parece una {nombre} ({m.group(0)[:6]}...)")
    return hallazgos


def descargar_cacheado(url: str, nombre: str, offline: bool) -> Path | None:
    cache = AQUI / "cache"
    cache.mkdir(exist_ok=True)
    destino = cache / nombre
    if destino.exists():
        return destino
    if offline:
        return None
    print(f"   descargando {nombre} ...")
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(destino, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception as e:                                      # noqa: BLE001
        destino.unlink(missing_ok=True)
        print(f"   [!!] no pude descargar {url}: {e}")
        return None
    return destino


def verificar_pin(nombre: str, ruta: Path, pins: dict) -> bool:
    h = sha256(ruta)
    if nombre in pins and pins[nombre] != h:
        print(f"   [XX] {nombre}: el hash CAMBIO ({h[:12]}... vs fijado {pins[nombre][:12]}...). Abortando.")
        return False
    if nombre not in pins:
        pins[nombre] = h
        print(f"   [..] {nombre}: primera vez; hash fijado {h[:16]}... (guardado en release_pins.json)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="carpeta de desarrollo de SARA (p. ej. C:\\saradefinitiva)")
    ap.add_argument("--version", default="dev")
    ap.add_argument("--out", default=str(AQUI / "dist"))
    ap.add_argument("--solo-revisar", action="store_true")
    ap.add_argument("--offline", action="store_true", help="no descargar Python/get-pip (para pruebas)")
    a = ap.parse_args()

    src = Path(a.src).resolve()
    if not (src / "server.py").exists() or not (src / "config.py").exists():
        print(f"[XX] {src} no parece la carpeta de SARA (faltan server.py / config.py).")
        return 1

    print(f"== Revisando {src}")
    incl = recopilar(src)
    py = [k for k in incl if k.endswith(".py")]
    print(f"   {len(incl)} archivos incluidos ({len(py)} .py)")

    problemas = revisar_imports(src, incl)
    for p in problemas:
        print("   [!!]", p)
    gui = revisar_gui(incl)
    for g in gui:
        print("   [XX] GUI:", g)
    problemas += [f"MODELOS: {m}" for m in revisar_modelos(src)]
    secretos = escanear_secretos(incl)
    for s in secretos:
        print("   [XX] SECRETO:", s)
    if secretos:
        print("\n[XX] Hay secretos dentro de archivos que viajarian. Quitalos (usa .env) y vuelve a correr.")
        return 2
    if gui:
        print("\n[XX] La interfaz (static/) esta incompleta; el paquete abriria una ventana en blanco.")
        return 3
    if any("NO COMPILA" in p for p in problemas):
        print("\n[XX] Hay archivos con errores de sintaxis; no se arma el paquete.")
        return 3
    if a.solo_revisar:
        print("\n[OK] Revision terminada (no se creo ningun zip).")
        return 0 if not problemas else 4

    nombre = f"SARA-{a.version}"
    out = Path(a.out)
    carpeta = out / nombre
    if carpeta.exists():
        shutil.rmtree(carpeta)
    (carpeta / "app").mkdir(parents=True)

    print("== Copiando app/")
    for rel, ruta in incl.items():
        dst = carpeta / "app" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ruta, dst)

    print("== Copiando arrancador y bootstrap")
    shutil.copy2(AQUI / "SARA.bat", carpeta / "SARA.bat")
    for extra in ("LEEME.txt",):
        if (AQUI / extra).exists():
            shutil.copy2(AQUI / extra, carpeta / extra)
    shutil.copytree(AQUI / "bootstrap", carpeta / "bootstrap",
                    ignore=shutil.ignore_patterns("__pycache__", "vendor", "*.pyc"))
    (carpeta / "bootstrap" / "vendor").mkdir(exist_ok=True)
    (carpeta / "runtime").mkdir(exist_ok=True)

    print("== Python embebido y get-pip (vendor)")
    pins = json.loads(PINS.read_text(encoding="utf-8")) if PINS.exists() else {}
    for url, nombre_arch, destino in ((PYTHON_EMBED_URL, "python-embed.zip", "python-embed.zip"),
                                      (GET_PIP_URL, "get-pip.py", "get-pip.py")):
        ruta = descargar_cacheado(url, nombre_arch, a.offline)
        if ruta is None:
            print(f"   [!!] {nombre_arch} no incluido: SARA.bat lo descargara en el equipo del usuario.")
            continue
        if not verificar_pin(nombre_arch, ruta, pins):
            return 5
        shutil.copy2(ruta, carpeta / "bootstrap" / "vendor" / destino)
    PINS.write_text(json.dumps(pins, indent=2), encoding="utf-8")

    manifest = {"version": a.version, "python_embed_url": PYTHON_EMBED_URL, "hashes": pins,
                "archivos_app": sorted(incl)}
    (carpeta / "bootstrap" / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("== Comprobacion final: nada personal dentro del paquete")
    prohibidos = [p for p in carpeta.rglob("*") if p.is_file() and (
        p.name in NUNCA or p.suffix in {".db", ".log"} or "auth" in p.parts or "audio_tmp" in p.parts
        or p.name in {"contactos.json", "self_jids.json"} or p.name.endswith((".log.1", ".log.2")))]
    if prohibidos:
        for p in prohibidos:
            print("   [XX]", p.relative_to(carpeta))
        return 6

    zpath = out / f"{nombre}.zip"
    print(f"== Creando {zpath}")
    if zpath.exists():
        zpath.unlink()
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(carpeta.rglob("*")):
            if p.is_file():
                z.write(p, Path(nombre) / p.relative_to(carpeta))
    print(f"\n[OK] {zpath}  ({zpath.stat().st_size / 2**20:.1f} MB)  sha256 {sha256(zpath)[:16]}...")
    if problemas:
        print("[!!] Recuerda revisar los avisos de imports de arriba.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
