# -*- coding: utf-8 -*-
"""
bootstrap.py v1.0 - Instalador y arrancador de SARA.  Solo libreria estandar.

Lo ejecuta SARA.bat con el Python propio de runtime/python. Cada vez que se abre SARA pasa por
aqui: si todo esta al dia tarda un instante y lanza app/lanzador.py; si falta algo, lo instala.

Principios (los mismos que SARA):
  * Degradacion elegante: si un componente OPCIONAL falla, se avisa y SARA arranca sin el.
  * Idempotente y retomable: runtime/estado.json recuerda que se instalo (y con que version).
  * Nada se instala fuera de la carpeta de SARA (salvo VC++ Runtime / WebView2 si faltan).

Opciones:  --auto  --perfil ligero|estandar|completo  --componentes  --reparar  --configurar
           --diagnostico  --navegador  --no-lanzar  --sin-descargas-pesadas
"""
import argparse
import ctypes
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

BOOT_VERSION = "1.0"

# --------------------------------------------------------------------------- rutas
AQUI = Path(__file__).resolve().parent
ROOT = AQUI.parent
APP = ROOT / "app"
RUNTIME = ROOT / "runtime"
PYDIR = RUNTIME / "python"
PY = PYDIR / "python.exe"
SITE = PYDIR / "Lib" / "site-packages"
HF_HOME = RUNTIME / "hf_cache"
PIP_CACHE = RUNTIME / "pip_cache"
OLLAMA_DIR = RUNTIME / "ollama"
OLLAMA_MODELS = RUNTIME / "ollama_models"
ESTADO = RUNTIME / "estado.json"
LOG = RUNTIME / "instalacion.log"
REQ_DIR = AQUI / "requisitos"
VENDOR = AQUI / "vendor"
WHEELS = AQUI / "wheels"          # wheels propios (proxy_tools: solo existe como fuente y el Python embebido no puede compilar)
NODE_DIR = SITE / "nodejs_wheel"

# --------------------------------------------------------------------------- versiones fijadas
OLLAMA_VERSION = "v0.40.1"
OLLAMA_URL = f"https://github.com/ollama/ollama/releases/download/{OLLAMA_VERSION}/ollama-windows-amd64.zip"
OLLAMA_MODELO = "llava-phi3"
OLLAMA_PUERTO = 11434
NODE_PIN = "nodejs-wheel-binaries==24.15.0"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
VCREDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
MODELO_EMBEDDINGS = "paraphrase-multilingual-MiniLM-L12-v2"   # el mismo de config.MODELO_EMBEDDINGS
MODELO_WHISPER = "base"                                        # el mismo de config.STT_MODELO

# mb = descarga aproximada. Verificado: torch 114.6 MB, wheel de node 41.5 MB, Ollama 1468 MB,
# llama3.2:3b 2.0 GB. El resto son ESTIMACIONES.
COMPONENTES = {
    "core":        dict(titulo="Nucleo + ventana de SARA",          req="core.txt",        mb=100,  obligatorio=True),
    "voz_salida":  dict(titulo="Voz de SARA (que hable)",           req="voz_salida.txt",  mb=150),
    "semantica":   dict(titulo="Comprension semantica (Capa 2)",    req="semantica.txt",   mb=550),
    "voz_entrada": dict(titulo="Escucha por microfono (Whisper)",   req="voz_entrada.txt", mb=250),
    "whatsapp":    dict(titulo="WhatsApp / SARA-Net (opcional)",    pins=[NODE_PIN],       mb=200),
    "ollama":      dict(titulo="IA local (Ollama + LLaVA-Phi3, con vision)",  mb=4400),
}
ORDEN = ["core", "voz_salida", "semantica", "voz_entrada", "whatsapp", "ollama"]
PERFILES = {
    "ligero":   ["core", "voz_salida"],
    "estandar": ["core", "voz_salida", "semantica", "voz_entrada"],
    "completo": ["core", "voz_salida", "semantica", "voz_entrada", "ollama"],
}
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_logf = None
_ULTIMAS = []                    # ultimas lineas del ultimo comando (para mostrar el motivo real de un fallo)


# --------------------------------------------------------------------------- salida / log
def _abrir_log():
    global _logf
    RUNTIME.mkdir(parents=True, exist_ok=True)
    try:
        if LOG.exists() and LOG.stat().st_size > 1_000_000:
            LOG.replace(LOG.with_suffix(".log.1"))
        _logf = open(LOG, "a", encoding="utf-8", errors="replace")
    except OSError:
        _logf = None


def _log(texto: str):
    if _logf:
        try:
            _logf.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {texto}\n")
            _logf.flush()
        except OSError:
            pass


def say(texto: str = "", marca: str = ""):
    linea = f" {marca} {texto}" if marca else f" {texto}"
    try:
        print(linea, flush=True)
    except UnicodeEncodeError:
        print(linea.encode("ascii", "replace").decode("ascii"), flush=True)
    _log(linea.strip())


def ok(t): say(t, "[OK]")
def info(t): say(t, "[..]")
def warn(t): say(t, "[!!]")
def fail(t): say(t, "[XX]")


def fatal(texto: str, codigo: int = 1):
    fail(texto)
    sys.exit(codigo)


def _vaciar_teclado():
    """Descarta teclas pulsadas ANTES de preguntar (p. ej. un Enter impaciente durante una instalacion lenta).
    Sin esto, esas teclas contestaban solas las preguntas y el instalador 'pasaba de largo'."""
    if os.name != "nt":
        return
    try:
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getwch()
    except Exception:                                             # noqa: BLE001 - mejor esfuerzo
        pass


def preguntar(texto: str, defecto: str = "") -> str:
    try:
        if not sys.stdin or not sys.stdin.isatty():
            return defecto
        _vaciar_teclado()
        r = input(f" {texto} ").strip()
        return r if r else defecto
    except (EOFError, KeyboardInterrupt):
        return defecto


# --------------------------------------------------------------------------- estado
def cargar_estado() -> dict:
    try:
        d = json.loads(ESTADO.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("componentes", {})
            return d
    except (OSError, ValueError):
        pass
    return {"version": 1, "seleccion": [], "componentes": {}}


def guardar_estado(est: dict):
    try:
        ESTADO.parent.mkdir(parents=True, exist_ok=True)
        tmp = ESTADO.with_suffix(".tmp")
        tmp.write_text(json.dumps(est, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(ESTADO)
    except OSError as e:
        _log(f"No pude guardar el estado: {e}")


def sha256_de(ruta: Path) -> str:
    h = hashlib.sha256()
    h.update(ruta.read_bytes())
    return h.hexdigest()


# --------------------------------------------------------------------------- hardware
def hardware() -> dict:
    hw = {"ram_gb": 0.0, "disco_libre_gb": 0.0, "avx2": None, "windows": "", "build": 0,
          "x64": struct.calcsize("P") == 8}
    try:
        hw["disco_libre_gb"] = shutil.disk_usage(ROOT).free / 2**30
    except OSError:
        pass
    if os.name == "nt":
        try:
            class _MEM(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = _MEM()
            m.dwLength = ctypes.sizeof(_MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            hw["ram_gb"] = m.ullTotalPhys / 2**30
            hw["avx2"] = bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))   # PF_AVX2_INSTRUCTIONS_AVAILABLE
            v = sys.getwindowsversion()
            hw["build"] = v.build
            hw["windows"] = f"{'11' if v.build >= 22000 else '10' if v.major == 10 else str(v.major)} (build {v.build})"
        except Exception as e:                                   # noqa: BLE001 - informativo, nunca debe tumbar el arranque
            _log(f"hardware(): {e}")
    else:                                                        # solo para probar la logica fuera de Windows
        try:
            with open("/proc/meminfo") as f:
                kb = int(f.readline().split()[1])
            hw["ram_gb"] = kb / 2**20
        except Exception:                                        # noqa: BLE001
            pass
    return hw


def perfil_recomendado(hw: dict) -> str:
    ram = hw.get("ram_gb") or 0
    if hw.get("avx2") is False or ram < 5.5:
        return "ligero"
    if ram < 10:
        return "estandar"
    return "completo"


def descarga_total_mb(sel) -> int:
    return sum(COMPONENTES[c]["mb"] for c in sel)


def revisar_entorno(hw: dict, simular: bool = False) -> list:
    """Devuelve avisos. Aborta solo si el equipo no puede ejecutar SARA."""
    avisos = []
    if os.name != "nt" and not simular:
        fatal("SARA solo funciona en Windows 10/11 de 64 bits.")
    if not hw["x64"] and not simular:
        fatal("Este Python no es de 64 bits; SARA necesita Windows de 64 bits.")
    ruta = str(ROOT)
    bajo = ruta.lower()
    if any(x in bajo for x in ("onedrive", "dropbox", "google drive", "googledrive")):
        avisos.append("La carpeta esta dentro de una nube (OneDrive/Dropbox/Drive): la base de datos puede bloquearse. "
                      "Mueve SARA a algo como C:\\SARA.")
    if any(ord(c) > 127 for c in ruta):
        avisos.append("La ruta tiene acentos o caracteres especiales; si algo falla, mueve SARA a C:\\SARA.")
    if "program files" in bajo or bajo.startswith(("c:\\windows", "c:\\programdata")):
        avisos.append("Esta en una carpeta protegida de Windows; SARA necesita poder escribir aqui. Usa C:\\SARA.")
    try:
        prueba = RUNTIME / ".escritura"
        prueba.write_text("ok", encoding="utf-8")
        prueba.unlink()
    except OSError:
        fatal(f"No puedo escribir en {RUNTIME}. Mueve la carpeta de SARA a un lugar normal (p. ej. C:\\SARA).")
    if hw.get("avx2") is False:
        avisos.append("Tu procesador no reporta AVX2: la IA local (Ollama) y algunas librerias pueden no funcionar. "
                      "Se recomienda el perfil ligero.")
    return avisos


# --------------------------------------------------------------------------- procesos y descargas
def ejecutar(cmd, cwd=None, env=None, mostrar=None) -> int:
    """Corre un comando, guarda TODO en el log y muestra solo las lineas que 'mostrar' acepta."""
    _log("$ " + " ".join(str(c) for c in cmd))
    try:
        p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                             creationflags=_SIN_VENTANA)
    except OSError as e:
        _log(f"No pude ejecutar: {e}")
        return 127
    _ULTIMAS.clear()
    for linea in p.stdout:
        _log("  " + linea.rstrip())
        _ULTIMAS.append(linea.rstrip())
        if len(_ULTIMAS) > 25:
            del _ULTIMAS[0]
        if mostrar and mostrar(linea):
            try:
                print("     " + linea.strip()[:100], flush=True)
            except UnicodeEncodeError:
                pass
    return p.wait()


def _mostrar_pip(linea: str) -> bool:
    return linea.startswith(("Collecting", "Installing collected", "Successfully", "ERROR"))


def descargar(url: str, destino: Path, desc: str, reintentos: int = 5) -> bool:
    """Descarga con reanudacion (Range) para conexiones malas. Devuelve True si el archivo quedo completo."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".part")
    for intento in range(1, reintentos + 1):
        try:
            pos = parcial.stat().st_size if parcial.exists() else 0
            cab = {"User-Agent": f"SARA-bootstrap/{BOOT_VERSION}"}
            if pos:
                cab["Range"] = f"bytes={pos}-"
            with urllib.request.urlopen(urllib.request.Request(url, headers=cab), timeout=30) as r:
                if pos and getattr(r, "status", 200) != 206:
                    pos = 0                                       # el servidor ignoro Range: empezar de cero
                restante = int(r.headers.get("Content-Length") or 0)
                total = restante + pos if restante else 0
                t0 = ultimo = time.time()
                hecho = pos
                with open(parcial, "ab" if pos else "wb") as f:
                    while True:
                        bloque = r.read(1024 * 1024)
                        if not bloque:
                            break
                        f.write(bloque)
                        hecho += len(bloque)
                        if time.time() - ultimo > 0.5:
                            ultimo = time.time()
                            pct = f"{hecho * 100 // total}%" if total else ""
                            print(f"\r     {desc}: {pct} {hecho // 2**20} MB".ljust(70), end="", flush=True)
            if total and parcial.stat().st_size < total:
                raise OSError("descarga incompleta")
            print(f"\r     {desc}: listo ({hecho // 2**20} MB, {time.time() - t0:.0f} s)".ljust(70), flush=True)
            parcial.replace(destino)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 416 and parcial.exists():                # ya estaba completo
                parcial.replace(destino)
                return True
            _log(f"descargar {url}: HTTP {e.code}")
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            _log(f"descargar {url}: {e}")
        print()
        if intento < reintentos:
            warn(f"Descarga interrumpida; reintento {intento + 1}/{reintentos} en {2 * intento} s...")
            time.sleep(2 * intento)
    return False


# --------------------------------------------------------------------------- Python propio
_SITECUSTOMIZE = '''# Generado por bootstrap.py de SARA. Sustituye lo que un Python normal hace solo.
import os, sys
try:
    for _s in (sys.stdout, sys.stderr):
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
try:
    import site
    _sp = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "Lib", "site-packages")
    if os.path.isdir(_sp):
        site.addsitedir(_sp)   # procesa los .pth (pywin32 los necesita)
except Exception:
    pass
'''


def preparar_python():
    if not PY.exists():
        fatal(f"Falta {PY}. Vuelve a abrir SARA.bat.")
    pth = next(PYDIR.glob("python*._pth"), None)
    if pth is None:
        warn("Este Python no es la version 'embeddable' (no hay ._pth); se usa tal cual.")
    else:
        base = pth.name[:-len("._pth")]
        deseado = f"{base}.zip\n.\nLib\\site-packages\n..\\..\\app\nimport site\n"
        if not pth.exists() or pth.read_text(encoding="utf-8", errors="replace") != deseado:
            pth.write_text(deseado, encoding="utf-8")
            _log(f"Escrito {pth.name}")
    sc = PYDIR / "sitecustomize.py"
    if not sc.exists() or sc.read_text(encoding="utf-8", errors="replace") != _SITECUSTOMIZE:
        sc.write_text(_SITECUSTOMIZE, encoding="utf-8")
    if (SITE / "pip").is_dir():
        return
    info("Instalando pip (solo una vez)...")
    getpip = VENDOR / "get-pip.py"
    if not getpip.exists() and not descargar(GET_PIP_URL, getpip, "get-pip.py"):
        fatal("No pude conseguir pip. Revisa tu conexion a internet.")
    rc = ejecutar([PY, getpip, "--no-warn-script-location", "--disable-pip-version-check"],
                  mostrar=lambda l: l.startswith(("Successfully", "ERROR")))
    if rc != 0 or not (SITE / "pip").is_dir():
        fatal("Fallo la instalacion de pip (mira runtime/instalacion.log).")
    ok("pip instalado")


def _env_pip() -> dict:
    env = dict(os.environ)
    env["PIP_CACHE_DIR"] = str(PIP_CACHE)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    return env


def pip_instalar(args: list, reintentos: int = 3) -> bool:
    base = [PY, "-m", "pip", "install", "--no-warn-script-location", "--progress-bar", "off",
            "--retries", "5", "--timeout", "30",
            "--only-binary=:all:"]                                 # sin compilador en el equipo del usuario
    if WHEELS.is_dir():
        base += ["--find-links", str(WHEELS)]
    for i in range(1, reintentos + 1):
        if ejecutar(base + args, env=_env_pip(), mostrar=_mostrar_pip) == 0:
            return True
        fail("pip fallo. Ultimas lineas:")
        for l in _ULTIMAS[-12:]:
            say("    " + l[:110])
        if i < reintentos:
            warn(f"Reintento {i + 1}/{reintentos}...")
            time.sleep(3 * i)
    return False


# --------------------------------------------------------------------------- piezas del sistema
def _dll_en_system32(nombre: str) -> bool:
    return (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / nombre).exists()


def asegurar_vcredist(est: dict):
    if os.name != "nt" or est.get("vcredist"):
        return
    if _dll_en_system32("vcruntime140_1.dll") and _dll_en_system32("msvcp140.dll"):
        est["vcredist"] = "presente"
        return
    info("Falta el 'Visual C++ Runtime' de Microsoft (torch, Whisper y onnx lo necesitan).")
    exe = RUNTIME / "vc_redist.x64.exe"
    if descargar(VCREDIST_URL, exe, "VC++ Runtime") :
        info("Windows puede pedirte permiso de administrador para instalarlo...")
        rc = ejecutar([exe, "/install", "/quiet", "/norestart"])
        if rc in (0, 1638, 3010):
            ok("VC++ Runtime instalado" + (" (puede pedir reiniciar)" if rc == 3010 else ""))
            est["vcredist"] = "instalado"
        else:
            warn(f"No se pudo instalar el VC++ Runtime (codigo {rc}). Si torch/Whisper fallan, instalalo desde microsoft.com.")
        try:
            exe.unlink()
        except OSError:
            pass
    else:
        warn("No pude descargar el VC++ Runtime; si torch/Whisper fallan, instalalo desde microsoft.com.")


def webview2_instalado() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
    except ImportError:
        return False
    rutas = [(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
             (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
             (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}")]
    for hive, ruta in rutas:
        try:
            with winreg.OpenKey(hive, ruta) as k:
                pv, _ = winreg.QueryValueEx(k, "pv")
                if pv and pv != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def asegurar_webview2(est: dict):
    if os.name != "nt" or est.get("webview2"):
        return
    if webview2_instalado():
        est["webview2"] = "presente"
        return
    info("Falta WebView2 (la ventana de SARA). Descargando el instalador de Microsoft (~2 MB)...")
    exe = RUNTIME / "webview2_setup.exe"
    if descargar(WEBVIEW2_URL, exe, "WebView2"):
        rc = ejecutar([exe, "/silent", "/install"])
        if webview2_instalado():
            ok("WebView2 instalado")
            est["webview2"] = "instalado"
        else:
            warn(f"No se pudo instalar WebView2 (codigo {rc}). SARA abrira su interfaz en tu navegador.")
            est["webview2"] = "fallo"
        try:
            exe.unlink()
        except OSError:
            pass
    else:
        warn("No pude descargar WebView2. SARA abrira su interfaz en tu navegador.")
        est["webview2"] = "fallo"


# --------------------------------------------------------------------------- componentes
def _hash_componente(nombre: str) -> str:
    spec = COMPONENTES[nombre]
    h = hashlib.sha256()
    if "req" in spec:
        h.update((REQ_DIR / spec["req"]).read_bytes())
    for p in spec.get("pins", []):
        h.update(p.encode())
    if nombre == "whatsapp":
        lock = APP / "wa_sidecar" / "package-lock.json"
        if lock.exists():
            h.update(lock.read_bytes())
    if nombre == "ollama":
        h.update((OLLAMA_VERSION + OLLAMA_MODELO).encode())
    return h.hexdigest()


def _entorno_sara(propio_ollama: bool = False) -> dict:
    env = dict(os.environ)
    rutas = [str(PYDIR), str(PYDIR / "Scripts")]
    if (NODE_DIR / "node.exe").exists():
        rutas.append(str(NODE_DIR))                 # whatsapp.py hace shutil.which("node")
    if (OLLAMA_DIR / "ollama.exe").exists():
        rutas.append(str(OLLAMA_DIR))
    env["PATH"] = os.pathsep.join(rutas + [env.get("PATH", "")])
    env["HF_HOME"] = str(HF_HOME)
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    if propio_ollama or (OLLAMA_DIR / "ollama.exe").exists():
        env["OLLAMA_MODELS"] = str(OLLAMA_MODELS)
    env["SARA_RELEASE"] = "1"
    return env


def instalar_pip(nombre: str) -> bool:
    spec = COMPONENTES[nombre]
    args = ["-r", str(REQ_DIR / spec["req"])] if "req" in spec else list(spec["pins"])
    return pip_instalar(args)


def precargar_modelos(nombre: str):
    """Baja los modelos ahora para que despues SARA funcione sin internet. Un fallo no es grave."""
    env = _entorno_sara()
    if nombre == "semantica":
        # v2 (15 oct 2026): Capa 2 sobre ONNX (sin torch). embeddings.py baja model.onnx + tokenizer.json a runtime\\embeddings_onnx.
        info(f"Descargando el modelo semantico ONNX ({MODELO_EMBEDDINGS}, ~0.5 GB)...")
        rc = ejecutar([PY, str(APP / "embeddings.py"), "--instalar"], cwd=APP, env=env,
                      mostrar=lambda l: "[OK]" in l or "[XX]" in l or "Error" in l)
        if rc == 0:
            ok("Modelo listo (despues funciona sin internet)")
        else:
            warn("No pude precargar el modelo; se descargara con 'python app\\embeddings.py --instalar' (necesita internet).")
        return
    elif nombre == "voz_salida":
        # Voz neuronal OFFLINE única: Piper «claude» de calidad alta (~63 MB) en runtime\\piper_voces (v3.6: sin Kokoro).
        info("Descargando la voz offline de SARA (Piper claude, ~65 MB)...")
        buenas = 0
        for modulo in ("voz_piper.py",):
            rc = ejecutar([PY, str(APP / modulo), "--instalar"], cwd=APP, env=env,
                          mostrar=lambda l: "[OK]" in l or "[XX]" in l or "Error" in l)
            buenas += 1 if rc == 0 else 0
            if modulo == "voz_piper.py":                      # retira del disco las voces antiguas «medium»
                ejecutar([PY, str(APP / modulo), "--limpiar"], cwd=APP, env=env, mostrar=lambda l: "[OK]" in l)
        if buenas:
            ok("Voces offline listas (SARA habla aunque Edge-TTS o internet fallen)")
        else:
            warn("No pude bajar las voces offline; se bajan al elegirlas en el menu de voces (necesita internet).")
        return
    elif nombre == "voz_entrada":
        info(f"Descargando el modelo de voz Whisper '{MODELO_WHISPER}' (~150 MB)...")
        codigo = f"from faster_whisper.utils import download_model as d; d('{MODELO_WHISPER}')"
    else:
        return
    rc = ejecutar([PY, "-c", codigo], cwd=APP, env=env, mostrar=lambda l: "Error" in l or "Traceback" in l)
    if rc == 0:
        ok("Modelo listo (despues funciona sin internet)")
    else:
        warn("No pude precargar el modelo; se descargara la primera vez que se use (necesita internet).")


def instalar_whatsapp() -> bool:
    if not instalar_pip("whatsapp"):
        return False
    node, npm = NODE_DIR / "node.exe", NODE_DIR / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    d = APP / "wa_sidecar"
    if not (node.exists() and npm.exists()):
        warn("No encuentro node/npm tras instalar el paquete.")
        return False
    if not (d / "index.js").exists():
        warn("Falta app/wa_sidecar/index.js.")
        return False
    info("Instalando el puente de WhatsApp (npm)...")
    env = _entorno_sara()
    lock = (d / "package-lock.json").exists()
    for modo in (["ci"] if lock else []) + ["install"]:
        rc = ejecutar([node, npm, modo, "--omit=dev", "--no-audit", "--no-fund"], cwd=d, env=env,
                      mostrar=lambda l: l.startswith(("added", "npm ERR", "npm error")))
        if rc == 0 and (d / "node_modules").is_dir():
            return True
    return False


# ---- Ollama
def ollama_responde() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{OLLAMA_PUERTO}/", timeout=1.5) as r:
            return r.status == 200
    except Exception:                                                       # noqa: BLE001
        return False


def _exe_ollama():
    propio = OLLAMA_DIR / "ollama.exe"
    if propio.exists():
        return propio
    hallado = shutil.which("ollama")
    if hallado:
        return Path(hallado)
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return local if local.exists() else None


def ollama_tiene_modelo() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{OLLAMA_PUERTO}/api/tags", timeout=3) as r:
            datos = json.loads(r.read().decode("utf-8", "replace"))
            nombres = {m.get("name") for m in datos.get("models", [])}
        return OLLAMA_MODELO in nombres or f"{OLLAMA_MODELO}:latest" in nombres
    except Exception:                                                       # noqa: BLE001
        return False


def instalar_ollama(forzar: bool = False) -> bool:
    if _exe_ollama() is None:
        info(f"Descargando Ollama {OLLAMA_VERSION} (~1.5 GB, la parte mas pesada)...")
        zpath = RUNTIME / "ollama.zip"
        if not descargar(OLLAMA_URL, zpath, "Ollama"):
            return False
        info("Descomprimiendo Ollama...")
        try:
            with zipfile.ZipFile(zpath) as z:
                if z.testzip() is not None:
                    raise zipfile.BadZipFile("zip danado")
                z.extractall(OLLAMA_DIR)
            zpath.unlink()
        except (zipfile.BadZipFile, OSError) as e:
            fail(f"Ollama: {e}")
            try:
                zpath.unlink()
            except OSError:
                pass
            return False
        if not (OLLAMA_DIR / "ollama.exe").exists():
            hallado = next(OLLAMA_DIR.rglob("ollama.exe"), None)
            if hallado is None:
                return False
            for item in hallado.parent.iterdir():                          # sube el contenido a OLLAMA_DIR
                destino = OLLAMA_DIR / item.name
                if not destino.exists():
                    shutil.move(str(item), str(destino))
        ok("Ollama listo")
    return True


def asegurar_ollama() -> "subprocess.Popen | None":
    """Deja Ollama respondiendo con el modelo descargado. Devuelve el proceso SOLO si lo arranco SARA."""
    exe = _exe_ollama()
    if exe is None:
        warn("Ollama no esta instalado: la IA local no estara disponible (SARA usa el respaldo externo).")
        return None
    proc = None
    env = _entorno_sara()
    if not ollama_responde():
        info("Encendiendo la IA local...")
        proc = subprocess.Popen([str(exe), "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, creationflags=_SIN_VENTANA)
        for _ in range(60):
            time.sleep(0.5)
            if ollama_responde():
                break
        else:
            warn("Ollama no respondio a tiempo; SARA arranca sin IA local.")
            proc.terminate()
            return None
    if not ollama_tiene_modelo():
        info(f"Descargando el modelo {OLLAMA_MODELO} (~2 GB, solo una vez)...")
        rc = subprocess.call([str(exe), "pull", OLLAMA_MODELO], env=env)
        if rc != 0:
            warn("No pude descargar el modelo; SARA usara el respaldo externo. Se reintenta al abrir SARA otra vez.")
        else:
            ok("Modelo local listo")
    return proc


# --------------------------------------------------------------------------- seleccion
def elegir_componentes(hw: dict, args, est: dict) -> list:
    rec = perfil_recomendado(hw)
    if args.perfil:
        sel = list(PERFILES[args.perfil])
    elif args.auto or not (sys.stdin and sys.stdin.isatty()):
        sel = list(PERFILES[rec])
    else:
        say()
        say(f"Tu equipo: {hw['ram_gb']:.1f} GB de RAM, {hw['disco_libre_gb']:.0f} GB libres, "
            f"AVX2: {'si' if hw['avx2'] else 'no' if hw['avx2'] is False else '?'}, Windows {hw['windows'] or '?'}")
        say()
        nombres = {"ligero": "Ligero   - motor + ventana + voz", "estandar": "Estandar - + comprension semantica y escucha",
                   "completo": "Completo - + IA local (Ollama, necesita ~3 GB de RAM al usarla)"}
        for i, p in enumerate(("ligero", "estandar", "completo"), 1):
            say(f"[{i}] {nombres[p]}  (descarga ~{descarga_total_mb(PERFILES[p]) / 1024:.1f} GB)"
                + ("   <- recomendado" if p == rec else ""))
        say("[4] Elegir pieza por pieza")
        r = preguntar(f"Elige 1-4 (Enter = {rec}):", "")
        if r in ("1", "2", "3"):
            sel = list(PERFILES[("ligero", "estandar", "completo")[int(r) - 1]])
        elif r == "4":
            sel = ["core"]
            for c in ORDEN[1:]:
                s = COMPONENTES[c]
                if preguntar(f"Instalar '{s['titulo']}' (~{s['mb']} MB)? [s/N]:", "n").lower().startswith("s"):
                    sel.append(c)
        else:
            sel = list(PERFILES[rec])
        if "whatsapp" not in sel and preguntar("Activar WhatsApp / SARA-Net? (usa una libreria no oficial) [s/N]:", "n").lower().startswith("s"):
            sel.append("whatsapp")
    sel = [c for c in ORDEN if c in sel or c == "core"]
    est["seleccion"] = sel
    est["perfil_recomendado"] = rec
    guardar_estado(est)
    libre, necesita = hw["disco_libre_gb"], descarga_total_mb(sel) * 2 / 1024 + 1
    if libre and libre < necesita:
        warn(f"Poco espacio: hay {libre:.1f} GB libres y se necesitan unos {necesita:.1f} GB. Puede fallar a medias.")
    return sel


# --------------------------------------------------------------------------- .env
# Nombres EXACTOS que lee config.py (verificado): GROQ_API_KEY, GEMINI_API_KEY, DEEPSEEK_API_KEY, TELEGRAM_BOT_TOKEN.
CLAVES_EXTERNAS = (("GROQ_API_KEY", "Groq", "console.groq.com"),
                   ("GEMINI_API_KEY", "Gemini", "aistudio.google.com"),
                   ("DEEPSEEK_API_KEY", "DeepSeek", "platform.deepseek.com"),
                   ("TELEGRAM_BOT_TOKEN", "Telegram (bot de control remoto)", "@BotFather en Telegram"))


def _mascara(valor: str) -> str:
    return "termina en ..." + valor[-4:] if len(valor) > 8 else "ya definida"


def _leer_env(env_path: Path) -> dict:
    existentes = {}
    if env_path.exists():
        for linea in env_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            if "=" in linea and not linea.lstrip().startswith("#"):
                k, v = linea.split("=", 1)
                existentes[k.strip()] = v.strip()
    return existentes


def asistente_env(est: dict, args, forzar: bool = False):
    """Primera vez: pregunta SOLO lo que falta y avisa de lo que ya estaba. Con forzar (--configurar) pregunta todo,
    muestra lo actual y deja conservarlo con Enter. Nunca pisa un valor si el usuario no escribe otro."""
    if est.get("env_hecho") and not forzar:
        return
    env_path = APP / ".env"
    existentes = _leer_env(env_path)
    nuevos = {}
    interactivo = (forzar or not getattr(args, "auto", False)) and sys.stdin and sys.stdin.isatty()
    if interactivo:
        say()
        say("Configuracion (Enter = omitir o conservar). Se guarda en app\\.env, solo en tu equipo.")
        actual = existentes.get("SARA_USUARIO")
        if actual and not forzar:
            say(f"Nombre: ya configurado ({actual}).")
        else:
            n = preguntar(f"Como te llamo? [{actual}]:" if actual else "Como te llamo?:", "")
            if n:
                nuevos["SARA_USUARIO"] = n
        say("Claves de IAs externas (gratuitas y opcionales; SARA funciona sin ellas):")
        for clave, nombre, donde in CLAVES_EXTERNAS:
            actual = existentes.get(clave)
            if actual and not forzar:
                say(f"{nombre}: ya configurada ({_mascara(actual)}).")
                continue
            msg = (f"{nombre}: {_mascara(actual)}. Pega una nueva o Enter para conservar:" if actual
                   else f"{nombre} ({donde}) [Enter = omitir]:")
            v = preguntar(msg, "")
            if v:
                nuevos[clave] = v
        if not forzar:
            say("Para cambiar esto despues: abre SARA.bat --configurar (o CONFIGURAR.bat).")
    ram = (est.get("hw") or {}).get("ram_gb", 99)
    if "ollama" in est.get("seleccion", []) and ram < 8 and "SARA_OLLAMA_KEEP_ALIVE" not in existentes:
        nuevos["SARA_OLLAMA_KEEP_ALIVE"] = "5m"                 # RAM justa: soltar la IA local pronto
    if not env_path.exists():
        plantilla = (AQUI / "env.example")
        base = plantilla.read_text(encoding="utf-8") if plantilla.exists() else ""
        env_path.write_text(base, encoding="utf-8")
    if nuevos:
        lineas = env_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        for k, v in nuevos.items():
            for i, l in enumerate(lineas):
                if l.strip().startswith(("#" + k + "=", "# " + k + "=", k + "=")):
                    lineas[i] = f"{k}={v}"
                    break
            else:
                lineas.append(f"{k}={v}")
        env_path.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    est["env_hecho"] = True


# --------------------------------------------------------------------------- pruebas de humo
_PRUEBAS = {
    "core": "import fastapi, uvicorn, websockets, requests, psutil, dotenv, dateparser, win32api, webview, cryptography",
    "voz_salida": "import edge_tts, comtypes, pycaw, piper, av",
    "semantica": "import numpy, onnxruntime, tokenizers",
    "voz_entrada": "import numpy, av, sounddevice, faster_whisper",
}


def prueba_humo(nombre: str) -> bool:
    cod = _PRUEBAS.get(nombre)
    if not cod:
        return True
    rc = ejecutar([PY, "-c", cod], cwd=APP, env=_entorno_sara(),
                  mostrar=lambda l: "Error" in l or "error" in l)
    if rc == 0 and nombre == "core":
        rc = ejecutar([PY, "-c", "import database; database.crear_tablas(); print('db ok')"],
                      cwd=APP, env=_entorno_sara(), mostrar=lambda l: "rror" in l)
    return rc == 0


# --------------------------------------------------------------------------- celular (app Android)
CEL_DIR = ROOT / "SARA_Celular"        # codigo de la app + el .apk ya compilado (el compañero NO necesita Android Studio)
CEL_PUERTO = 8770                      # el mismo de config.CEL_PUERTO
CEL_REGLAS = (("SARA celular (red local)", "LocalSubnet", "private"),     # (nombre, quien puede entrar, perfil)
              ("SARA celular (Tailscale)", "100.64.0.0/10", "any"))


def _apk_celular():
    """El .apk mas reciente dentro de SARA_Celular\\ (o None)."""
    try:
        apks = sorted(CEL_DIR.glob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True)
        return apks[0] if apks else None
    except OSError:
        return None


def _regla_firewall_existe(nombre: str) -> bool:
    """netsh devuelve 0 si la regla existe y 1 si no (no depende del idioma de Windows)."""
    try:
        r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={nombre}"],
                           capture_output=True, creationflags=_SIN_VENTANA, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def _crear_reglas_firewall() -> bool:
    """Abre SOLO el puerto del celular, SOLO para el python.exe propio de SARA y SOLO desde la red local o Tailscale.
    Pide permiso de administrador (UAC). Un .bat temporal evita problemas de comillas anidadas."""
    lineas = ["@echo off"]
    for nombre, remoto, perfil in CEL_REGLAS:
        lineas.append(f'netsh advfirewall firewall delete rule name="{nombre}" >nul 2>&1')
        lineas.append(f'netsh advfirewall firewall add rule name="{nombre}" dir=in action=allow protocol=TCP '
                      f'localport={CEL_PUERTO} program="{PY}" remoteip={remoto} profile={perfil} >nul')
    bat = RUNTIME / "firewall_celular.bat"
    try:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        bat.write_bytes(("\r\n".join(lineas) + "\r\n").encode("ascii"))
        ruta_ps = str(bat).replace("'", "''")
        ejecutar(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                  f"Start-Process -FilePath '{ruta_ps}' -Verb RunAs -Wait -WindowStyle Hidden"])
    except Exception as e:
        _log(f"firewall celular: {e}")
        return False
    finally:
        try:
            bat.unlink()
        except OSError:
            pass
    return all(_regla_firewall_existe(n) for n, _r, _p in CEL_REGLAS)


def _escritorio() -> Path:
    """Escritorio real del usuario (respeta OneDrive/redirecciones). Si falla, Descargas."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                           capture_output=True, text=True, creationflags=_SIN_VENTANA, timeout=30)
        d = Path((r.stdout or "").strip())
        if str(d) and d.is_dir():
            return d
    except Exception:
        pass
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads"


def entregar_apk(apk: Path) -> bool:
    """Comprueba el SHA-256 (si junto al APK viene SARA-Celular.apk.sha256), lo copia al Escritorio y abre el
    Explorador con el archivo seleccionado. El APK no es secreto: la huella solo detecta un archivo danado o cambiado."""
    try:
        f = apk.with_name(apk.name + ".sha256")
        if f.exists():
            esperado = f.read_text(encoding="utf-8-sig").split()[0].strip().lower()
            if sha256_de(apk).lower() != esperado:
                warn("El APK no coincide con su huella SHA-256 (archivo danado o modificado). No lo copio; "
                     "vuelve a extraer el .zip de SARA.")
                return False
        destino = _escritorio() / apk.name
        shutil.copy2(apk, destino)
        ok(f"APK copiado a: {destino}")
        subprocess.Popen(["explorer.exe", f"/select,{destino}"])
        return True
    except Exception as e:
        _log(f"entregar_apk: {e!r}")
        warn(f"No pude copiar el APK ({type(e).__name__}). Esta en {apk}: copialo a mano.")
        return False


def preparar_celular(est: dict, args, forzar: bool = False):
    """Rama movil. Primera vez (o con --celular): dice donde esta el APK, ofrece abrir el puerto en el Firewall y
    explica como vincular. Si no existe SARA_Celular\\ no hace nada. Nunca impide arrancar SARA."""
    if not CEL_DIR.is_dir():
        if forzar:
            warn(f"No encuentro la carpeta {CEL_DIR}. Este paquete no trae la app del celular.")
        return
    if est.get("celular_hecho") and not forzar:
        return
    try:
        interactivo = bool(sys.stdin and sys.stdin.isatty()) and not getattr(args, "auto", False)
        say()
        say("Celular (SARA movil)")
        apk = _apk_celular()
        if apk:
            ok(f"App del celular incluida: {apk.name}")
            if interactivo:
                r = preguntar("Copiar la app al Escritorio para pasarla a tu celular? [S/n]:", "s")
                if str(r).strip().lower() in ("", "s", "si", "y", "yes"):
                    if entregar_apk(apk):
                        say("  Pasala al celular (cable, Drive, WhatsApp...) y permite 'instalar apps desconocidas'.")
            else:
                say(f"  Para sacarla: SARA.bat --celular   (o copia {apk})")
        else:
            warn(f"No hay ningun .apk en {CEL_DIR}: este paquete no trae la app del celular.")
        faltan = [n for n, _r, _p in CEL_REGLAS if not _regla_firewall_existe(n)]
        if faltan or forzar:
            if interactivo:
                r = preguntar("Permitir que el celular se conecte a SARA? Abre el puerto "
                              f"{CEL_PUERTO} solo para SARA, en tu red y en Tailscale (pide permiso de administrador) [S/n]:", "s")
                if str(r).strip().lower() in ("", "s", "si", "y", "yes"):
                    if _crear_reglas_firewall():
                        ok("Firewall listo para el celular.")
                    else:
                        warn("No se pudo crear la regla (permiso denegado?). Reintenta con SARA.bat --celular.")
                else:
                    say("Sin cambios en el Firewall. Mas adelante: SARA.bat --celular")
            else:
                say("Para que el celular conecte, permite el puerto en el Firewall: SARA.bat --celular")
        else:
            ok("Firewall: el celular ya tiene permiso.")
        say("Para vincular: abre SARA y escribe  /celular vincular  (te da la direccion y un codigo de 6 digitos;")
        say("escribelos en la app. La app registra la clave de tu huella; por eso el celular necesita huella.)")
        say("Fuera de casa: instala Tailscale en la laptop y en el celular, con la misma cuenta.")
        est["celular_hecho"] = True
    except Exception as e:                                       # nunca bloquear el arranque por esto
        _log(f"preparar_celular: {e!r}")
        warn("No pude preparar la parte del celular (no impide usar SARA). Mira runtime/instalacion.log.")


# --------------------------------------------------------------------------- flujo principal
def instalar_seleccion(sel: list, est: dict, forzar: bool) -> None:
    comp = est["componentes"]
    for nombre in sel:
        spec = COMPONENTES[nombre]
        try:
            h = _hash_componente(nombre)
        except OSError as e:
            warn(f"{spec['titulo']}: no encuentro sus archivos ({e}).")
            continue
        previo = comp.get(nombre, {})
        if not forzar and previo.get("ok") and previo.get("hash") == h:
            continue
        say()
        info(f"Instalando: {spec['titulo']}  (~{spec['mb']} MB)")
        if nombre in ("semantica", "voz_entrada"):
            asegurar_vcredist(est)
        if nombre == "ollama":
            exito = instalar_ollama()
        elif nombre == "whatsapp":
            exito = instalar_whatsapp()
        else:
            exito = instalar_pip(nombre)
        if exito and nombre in _PRUEBAS:
            exito = prueba_humo(nombre)
            if not exito:
                warn("Se instalo, pero la prueba de importacion fallo (revisa runtime/instalacion.log).")
        if exito and nombre in ("semantica", "voz_entrada", "voz_salida"):
            precargar_modelos(nombre)
        comp[nombre] = {"ok": bool(exito), "hash": h, "ts": int(time.time())}
        guardar_estado(est)
        if exito:
            ok(f"{spec['titulo']}: listo")
        elif spec.get("obligatorio"):
            fatal(f"No pude instalar '{spec['titulo']}', sin el SARA no arranca. Mira runtime/instalacion.log.")
        else:
            warn(f"{spec['titulo']}: no se pudo instalar. SARA arranca sin esta parte "
                 f"(se reintenta la proxima vez; o usa SARA.bat --componentes).")


def lanzar(args, est: dict) -> int:
    env = _entorno_sara()
    if args.navegador or (est.get("webview2") == "fallo"):
        env["SARA_NAVEGADOR"] = "1"
    propio = None
    if "ollama" in est.get("seleccion", []) and est["componentes"].get("ollama", {}).get("ok"):
        propio = asegurar_ollama()
    say()
    ok("Abriendo SARA...")
    say("La primera vez, en equipos lentos o con disco duro mecanico, el nucleo puede tardar varios minutos en")
    say("cargar. La ventana avisara 'Nucleo de SARA listo'. Aqui tambien veras el progreso.")
    try:
        return subprocess.call([str(PY), "lanzador.py"], cwd=APP, env=env)
    finally:
        if propio is not None and propio.poll() is None:
            propio.terminate()                                  # solo cerramos el Ollama que abrio SARA
            try:
                propio.wait(timeout=5)
            except subprocess.TimeoutExpired:
                propio.kill()


def diagnostico(hw: dict, est: dict):
    say("== Diagnostico ==")
    say(f"Bootstrap {BOOT_VERSION} | carpeta: {ROOT}")
    say(f"RAM {hw['ram_gb']:.1f} GB | disco libre {hw['disco_libre_gb']:.0f} GB | AVX2 {hw['avx2']} | Windows {hw['windows']}")
    say(f"Perfil recomendado: {perfil_recomendado(hw)} | seleccion: {est.get('seleccion')}")
    for c in ORDEN:
        e = est["componentes"].get(c)
        say(f"  {c:12s} {'OK' if e and e.get('ok') else 'no instalado' if not e else 'FALLO'}")
    say(f"WebView2: {'si' if webview2_instalado() else 'no'} | Ollama responde: {'si' if ollama_responde() else 'no'}")
    for c in _PRUEBAS:
        if est["componentes"].get(c, {}).get("ok"):
            say(f"  importaciones de {c}: {'OK' if prueba_humo(c) else 'FALLAN (ver log)'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Instalador/arrancador de SARA")
    ap.add_argument("--auto", action="store_true", help="sin preguntas: usa el perfil recomendado")
    ap.add_argument("--perfil", choices=list(PERFILES))
    ap.add_argument("--componentes", action="store_true", help="volver a elegir que se instala")
    ap.add_argument("--reparar", action="store_true", help="reinstala todo lo seleccionado")
    ap.add_argument("--configurar", action="store_true", help="cambiar nombre y claves de IAs externas (app\\.env)")
    ap.add_argument("--diagnostico", action="store_true")
    ap.add_argument("--navegador", action="store_true", help="abre la interfaz en el navegador, no en ventana")
    ap.add_argument("--celular", action="store_true", help="prepara la app del celular: APK, Firewall y como vincular")
    ap.add_argument("--no-lanzar", action="store_true", help="instala y sale (para pruebas)")
    ap.add_argument("--simular", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    _abrir_log()
    _log(f"=== bootstrap {BOOT_VERSION} args={sys.argv[1:]} ===")
    say("=" * 52)
    say("  SARA - Sistema Autonomo de Razonamiento Artificial")
    say("=" * 52)
    if not APP.is_dir() or not (APP / "lanzador.py").exists():
        fatal(f"No encuentro la carpeta 'app' con SARA en {ROOT}. Extrae el .zip completo.")

    hw = hardware()
    for a in revisar_entorno(hw, args.simular):
        warn(a)
    preparar_python()
    est = cargar_estado()
    est["hw"] = {"ram_gb": round(hw["ram_gb"], 1)}
    if args.diagnostico:
        diagnostico(hw, est)
        return 0
    if args.celular:
        preparar_celular(est, args, forzar=True)
        guardar_estado(est)
        return 0
    if args.configurar:
        asistente_env(est, args, forzar=True)
        guardar_estado(est)
        ok("Configuracion guardada en app\\.env. Abre SARA.bat para usarla.")
        return 0
    if not est.get("seleccion") or args.componentes:
        sel = elegir_componentes(hw, args, est)
    else:
        sel = est["seleccion"]

    asegurar_webview2(est)
    instalar_seleccion(sel, est, args.reparar)
    if args.reparar or not est.get("compilado"):
        info("Preparando el codigo de SARA (acelera el primer arranque)...")
        ejecutar([PY, "-m", "compileall", "-q", str(APP)])         # un fallo aqui no es grave: Python compila al importar
        est["compilado"] = True
    asistente_env(est, args)
    preparar_celular(est, args)
    guardar_estado(est)
    if PIP_CACHE.exists():
        shutil.rmtree(PIP_CACHE, ignore_errors=True)            # libera espacio en equipos justos
    if args.no_lanzar:
        ok("Instalacion terminada.")
        return 0
    return lanzar(args, est)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
