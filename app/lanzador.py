"""
🚀 lanzador.py — Arranca server.py y abre la ventana pywebview (v5)

Cambios v5.1:
  * Mensajes honestos: "Servidor listo" solo significa que el servidor web responde; el núcleo
    (importar módulos, BD, índices) puede seguir cargando varios minutos en equipos lentos o con HDD.
    Un hilo vigila /health y avisa por consola con el tiempo transcurrido hasta "Núcleo listo".

Cambios v5 (distribución):
  * Si pywebview no está instalado, WebView2 falta o la ventana no puede abrirse,
    SARA abre su interfaz en el NAVEGADOR predeterminado (el servidor FastAPI ya
    sirve la misma GUI). Antes quedaba un bucle mudo o una excepción.
  * SARA_NAVEGADOR=1 fuerza ese modo (lo usa bootstrap.py si WebView2 no se pudo instalar).
  * En modo navegador el servidor sigue vivo hasta cerrar esta consola o pulsar Ctrl+C,
    y el lanzador termina solo si el servidor muere.

Cambios v4:
  * Sincroniza el puerto con server.py: elige un puerto libre (8000-8009) y se
    lo pasa por la variable de entorno SARA_PORT. Antes el lanzador asumía
    siempre 8000 aunque el servidor saltara a 8001 → ventana en blanco.
  * Reutiliza un servidor SARA ya activo en 8000-8009 (comprueba /health).
  * La salida del subproceso va a sara_server_stdout.log (sin pipes llenándose).
  * SARA_DEBUG=1 abre la ventana con herramientas de desarrollador.
"""

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PUERTO_BASE = 8000
RANGO_PUERTOS = range(PUERTO_BASE, PUERTO_BASE + 10)
TIMEOUT_ARRANQUE = 60
LOG_STDOUT = BASE_DIR / "sara_server_stdout.log"


def _health(puerto: int) -> dict | None:
    try:
        r = requests.get(f"http://{HOST}:{puerto}/health", timeout=1.0)
        info = r.json() if r.status_code == 200 else None
        return info if isinstance(info, dict) and info.get("status") == "ok" and "core" in info else None
    except Exception:
        return None


def _puerto_sara_activo() -> int | None:
    for p in RANGO_PUERTOS:
        if _health(p):
            return p
    return None


def _puerto_libre() -> int:
    for p in RANGO_PUERTOS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"No hay puertos libres entre {RANGO_PUERTOS.start} y {RANGO_PUERTOS.stop - 1}")


def esperar_servidor(proc: subprocess.Popen, puerto: int) -> bool:
    print(f"⏳ Esperando http://{HOST}:{puerto}/health ...")
    t0 = time.time()
    while time.time() - t0 < TIMEOUT_ARRANQUE:
        if proc.poll() is not None:
            print(f"✗ server.py terminó con código {proc.returncode}. Últimas líneas:\n")
            try:
                print("".join(LOG_STDOUT.read_text(encoding="utf-8", errors="replace").splitlines(True)[-25:]))
            except Exception:
                pass
            return False
        info = _health(puerto)
        if info:
            print(f"✓ Servidor web arrancado: {info}")
            if info.get("core") != "listo":
                print("⏳ El núcleo de SARA sigue cargando (la primera vez, en equipos lentos, puede tardar varios minutos).")
                print("   La ventana ya se puede abrir; SARA responderá cuando diga «Núcleo de SARA listo».")
                threading.Thread(target=_vigilar_nucleo, args=(puerto,), name="vigia-nucleo", daemon=True).start()
            nombres = {"index": "index.html", "app_js": "app.js", "style_css": "style.css"}
            faltan = [n for k, n in nombres.items() if not info.get(k)]
            if faltan:
                print(f"⚠️  No encuentro: {', '.join(faltan)} (colócalos en static/ o junto a server.py)")
            return True
        time.sleep(0.5)
    print("✗ Timeout esperando al servidor")
    return False


def _vigilar_nucleo(puerto: int, intervalo: float = 4.0, aviso_cada: float = 20.0) -> None:
    """Informa por consola hasta que el núcleo termine de cargar (o falle). No bloquea nada."""
    t0 = ultimo_aviso = time.time()
    while True:
        time.sleep(intervalo)
        info = _health(puerto)
        if info is None:                      # el servidor ya no responde: el hilo principal se encarga
            return
        estado = info.get("core")
        if estado == "listo":
            print(f"✓ Núcleo de SARA listo ({time.time() - t0:.0f} s tras abrir el servidor).")
            return
        if estado == "error":
            print(f"✗ El núcleo no arrancó: {info.get('error')}. Revisa sara_server_debug.log")
            return
        if time.time() - ultimo_aviso >= aviso_cada:
            ultimo_aviso = time.time()
            print(f"⏳ Núcleo cargando... {time.time() - t0:.0f} s (disco lento: sé paciente, la 2.ª vez es mucho más rápido)")


def _modo_navegador(url: str, proc: subprocess.Popen | None, motivo: str) -> None:
    """Sin ventana propia: abre el navegador predeterminado y mantiene vivo el servidor."""
    print(f"ℹ️  {motivo}")
    print(f"🌐 Abriendo SARA en tu navegador: {url}")
    print("   (deja esta consola abierta; ciérrala o pulsa Ctrl+C para detener SARA)")
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"⚠️  No pude abrir el navegador ({e}). Abre {url} a mano.")
    try:
        while proc is None or proc.poll() is None:
            time.sleep(1)
        print("✗ El servidor de SARA terminó.")
    except KeyboardInterrupt:
        pass


def abrir_interfaz(url: str, proc: subprocess.Popen | None = None) -> None:
    if os.getenv("SARA_NAVEGADOR") == "1":
        return _modo_navegador(url, proc, "Modo navegador solicitado (SARA_NAVEGADOR=1).")
    try:
        import webview
    except ImportError:
        return _modo_navegador(url, proc, "pywebview no está instalado.")
    try:
        webview.create_window("SARA v2.0", url=url, width=1200, height=800,
                              resizable=True, background_color="#0a0e27")
        webview.start(debug=os.getenv("SARA_DEBUG") == "1")
    except Exception as e:
        return _modo_navegador(url, proc, f"No pude abrir la ventana ({type(e).__name__}: {e}). "
                                          f"Suele faltar WebView2.")


def main() -> None:
    print("🤖 SARA v2.0")
    os.chdir(BASE_DIR)

    activo = _puerto_sara_activo()
    if activo:
        print(f"✓ Ya hay un servidor SARA en el puerto {activo} (se reutiliza).")
        abrir_interfaz(f"http://{HOST}:{activo}")
        return

    puerto = _puerto_libre()
    env = dict(os.environ, SARA_PORT=str(puerto))
    salida = open(LOG_STDOUT, "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-u", "server.py"], cwd=BASE_DIR, env=env,
                            stdout=salida, stderr=subprocess.STDOUT)
    print(f"✓ server.py iniciado (PID {proc.pid}, puerto {puerto}); log: {LOG_STDOUT.name}")
    try:
        if not esperar_servidor(proc, puerto):
            sys.exit(1)
        abrir_interfaz(f"http://{HOST}:{puerto}", proc)
    finally:
        print("Deteniendo servidor...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        salida.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
