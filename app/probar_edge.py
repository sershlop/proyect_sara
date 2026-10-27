# probar_edge.py — ¿funcionan las voces de Edge-TTS en ESTE equipo?  (no toca SARA ni sus archivos)
# Uso:  python probar_edge.py
# Prueba 3 cosas y dice cuál funciona: la librería tal cual, con el servidor nuevo, y la versión instalada.
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import edge_tts
    import edge_tts.communicate as com
    import edge_tts.constants as const
    from importlib.metadata import version
except Exception as e:
    print(f"[XX] edge-tts no está instalado: {e}\n     Instálalo con:  pip install -U edge-tts")
    sys.exit(2)

VOCES = ["es-MX-DaliaNeural", "es-MX-JorgeNeural"]
NUEVA = ("wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
         f"?Ocp-Apim-Subscription-Key={const.TRUSTED_CLIENT_TOKEN}")
ORIGINAL = const.WSS_URL


async def _una(voz: str) -> str:
    destino = Path(tempfile.gettempdir()) / f"probar_edge_{os.getpid()}.mp3"
    try:
        t0 = time.perf_counter()
        await asyncio.wait_for(edge_tts.Communicate("Hola, soy Sara.", voz).save(str(destino)), timeout=20)
        ok = destino.exists() and destino.stat().st_size > 1000
        return f"OK  ({int((time.perf_counter() - t0) * 1000)} ms, {destino.stat().st_size // 1024} KB)" if ok else "vacío"
    except Exception as e:
        return f"FALLA  {type(e).__name__}: {str(e)[:110]}"
    finally:
        try:
            destino.unlink()
        except OSError:
            pass


def _con(url: str) -> dict:
    const.WSS_URL = url
    com.WSS_URL = url
    return {v: asyncio.run(_una(v)) for v in VOCES}


print(f"edge-tts instalado: {version('edge-tts')}   (se necesita 7.3.1 o más)")
resultados = {}
for nombre, url in (("A) servidor de siempre", ORIGINAL), ("B) servidor nuevo (parche)", NUEVA)):
    print(f"\n{nombre}")
    resultados[nombre] = _con(url)
    for v, r in resultados[nombre].items():
        print(f"   {v}: {r}")

a = all(r.startswith("OK") for r in resultados["A) servidor de siempre"].values())
b = all(r.startswith("OK") for r in resultados["B) servidor nuevo (parche)"].values())
print("\nVEREDICTO:")
if a:
    print("  [OK] Edge funciona sin parche. SARA no necesita nada más (pon SARA_EDGE_PARCHE=0 si quieres).")
elif b:
    print("  [OK] Funciona con el servidor nuevo. El parche de voz.py v3.7 lo aplica solo.")
else:
    print("  [XX] Ninguna de las dos funcionó. Copia lo de arriba y mándamelo: sin Edge hay que usar otras voces.")
