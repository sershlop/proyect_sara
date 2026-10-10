"""Prueba del segmentador, filtros, anti-eco y flujo de eventos con audio SIMULADO (sin micro ni Whisper)."""
import sys, time, threading, queue, types
import numpy as np
sys.path.insert(0, '.')
import stt

SR = 16000
N = int(SR * stt.BLOQUE_S)
rng = np.random.default_rng(0)

def ruido(rms):   return (rng.standard_normal(N) * rms).astype(np.float32)
def habla(rms):   return (np.sin(np.linspace(0, 40, N)) * rms * 1.41).astype(np.float32)

fallos = []
def chequea(cond, msg):
    print(("OK   " if cond else "FALLA"), msg)
    if not cond: fallos.append(msg)

# 1) escala del umbral: ida y vuelta y monotonía
chequea(abs(stt._rms_umbral(0) - stt.R_MIN) < 1e-9 and abs(stt._rms_umbral(100) - stt.R_MAX) < 1e-9, "escala 0→R_MIN, 100→R_MAX")
chequea(abs(stt._pos_desde_rms(stt._rms_umbral(37)) - 37) < 0.01, "pos↔rms es inversa")
chequea(stt._rms_umbral(60) > stt._rms_umbral(30), "derecha = exige más energía")

def correr(bloques, umbral):
    seg = stt._Segmentador(SR); frases = []
    for b in bloques:
        _, f = seg.alimentar(b, umbral)
        if f is not None: frases.append(f)
    return frases

u = stt._rms_umbral(35)
# 2) solo ruido bajo el umbral → NADA llega a Whisper
chequea(correr([ruido(u * 0.3) for _ in range(300)], u) == [], "ruido ambiente bajo el umbral: 0 frases")
# 3) un clic suelto (1 bloque fuerte) no dispara
chequea(correr([ruido(u*0.2)]*20 + [habla(u*3)] + [ruido(u*0.2)]*60, u) == [], "un clic aislado no dispara")
# 4) una frase de ~1.2 s con silencio alrededor → 1 frase, duración razonable
audio = [ruido(u*0.2)]*20 + [habla(u*3)]*38 + [ruido(u*0.2)]*60
fr = correr(audio, u)
chequea(len(fr) == 1, "1 frase detectada")
if fr: 
    d = len(fr[0]) / SR
    print("     duración de la frase:", round(d, 2), "s")
    chequea(1.0 < d < 2.0, "duración ≈ frase + preroll + cola corta (recortó el silencio final)")
# 5) pausa corta dentro de la frase (0.4 s) NO la parte en dos
audio = [habla(u*3)]*20 + [ruido(u*0.2)]*12 + [habla(u*3)]*20 + [ruido(u*0.2)]*60
chequea(len(correr(audio, u)) == 1, "pausa de 0.4 s no parte la frase")
# 6) dos frases separadas por >0.8 s → 2 frases
audio = [habla(u*3)]*20 + [ruido(u*0.2)]*40 + [habla(u*3)]*20 + [ruido(u*0.2)]*40
chequea(len(correr(audio, u)) == 2, "dos frases separadas → 2")
# 7) slider a la derecha: la misma voz deja de pasar
u_alto = stt._rms_umbral(90)
audio = [habla(u*3)]*38 + [ruido(u*0.2)]*60
chequea(len(correr(audio, u_alto)) == 0, "con el slider alto la voz suave no pasa")
chequea(len(correr(audio, u)) == 1, "con el slider bajo esa misma voz sí pasa")
# 8) frase eterna se corta en MAX_SEGMENTO_S
fr = correr([habla(u*3)] * int(25 / stt.BLOQUE_S), u)
chequea(len(fr) >= 1 and len(fr[0]) / SR <= stt.MAX_SEGMENTO_S + 0.1, "corte forzado de frase eterna")

# 9) filtros de alucinaciones
chequea(stt._filtrar("Subtítulos realizados por la comunidad de Amara.org") == "", "filtra alucinación de subtítulos")
chequea(stt._filtrar("¡Gracias por ver el vídeo!") == "", "filtra 'gracias por ver'")
chequea(stt._filtrar("abre la calculadora.") == "abre la calculadora", "conserva un comando real (sin punto final)")
chequea(stt._filtrar("  ") == "" and stt._filtrar(".") == "", "vacío → vacío")

# 10) anti-eco
stt.set_voz_hablando(True);  chequea(stt._suprimido(), "suprimido mientras SARA habla")
stt.set_voz_hablando(False); chequea(stt._suprimido(), "sigue suprimido durante la cola tras hablar")
time.sleep(stt.TRAS_VOZ_S + 0.1); chequea(not stt._suprimido(), "libre al pasar la cola")

# 11) flujo completo: sounddevice y faster_whisper falsos → eventos en orden
eventos = []
def emit(t, **d): eventos.append((t, d))

cola_hooks = {}
class FlujoFalso:
    def __init__(self, samplerate, channels, dtype, blocksize, callback):
        self.cb = callback; self.run = True; cola_hooks['flujo'] = self
    def __enter__(self):
        def bombear():
            guion = [ruido(u*0.2)]*15 + [habla(u*3)]*40 + [ruido(u*0.2)]*60
            for b in guion:
                if not self.run: return
                self.cb(b.reshape(-1, 1), N, None, None); time.sleep(0.002)
        threading.Thread(target=bombear, daemon=True).start(); return self
    def __exit__(self, *a): self.run = False

fake_sd = types.ModuleType('sounddevice'); fake_sd.InputStream = FlujoFalso
fake_sd.query_devices = lambda kind=None: {"name": "Micro falso", "default_samplerate": 16000}
sys.modules['sounddevice'] = fake_sd

class Seg:  
    text = " abre la calculadora. "; no_speech_prob = 0.01; avg_logprob = -0.2
class ModeloFalso:
    def transcribe(self, a, **kw):
        assert kw['language'] == 'es' and kw['vad_filter'] is False
        return [Seg()], None
stt._modelo = ModeloFalso()          # evita descargar nada

stt.activar(emit, umbral=35)
t0 = time.time()
while time.time() - t0 < 10 and not any(t == 'stt_texto' for t, _ in eventos):
    time.sleep(0.05)
textos = [d for t, d in eventos if t == 'stt_texto']
chequea(len(textos) == 1 and textos[0]['texto'] == 'abre la calculadora', f"flujo completo entrega el texto: {textos}")
estados = [d['estado'] for t, d in eventos if t == 'stt_estado']
print("     estados emitidos:", estados)
chequea('escuchando' in estados and 'grabando' in estados, "pasa por escuchando y grabando")
chequea(any(t == 'stt_nivel' for t, _ in eventos), "emite stt_nivel")
stt.desactivar(); time.sleep(0.3)
chequea(stt.estado_actual()['estado'] == 'apagado' and not stt.esta_activo(), "desactivar apaga todo")
chequea(eventos[-1][0] == 'stt_estado' and eventos[-1][1]['estado'] == 'apagado', "último evento: apagado")

# 12) fallo controlado: sin micrófono → estado error una sola vez, modo voz apagado
eventos.clear()
def abrir_roto(*a, **k): raise OSError("No hay dispositivo de entrada")
fake_sd.InputStream = abrir_roto
stt.activar(emit, umbral=35); time.sleep(0.8)
errs = [d for t, d in eventos if t == 'stt_estado' and d['estado'] == 'error']
chequea(len(errs) == 1 and errs[0]['activo'] is False, "sin micro: un solo evento de error y activo=False")
chequea(stt.estado_actual()['estado'] == 'apagado', "tras el error el snapshot dice apagado (no repite el error al reconectar)")

# 13) umbral: clamp, basura y persistencia
import os, tempfile
stt.RUTA_CFG = stt.Path(tempfile.gettempdir()) / 'stt_cfg_test.json'   # carpeta temporal del SO (no toca tu stt_config.json real)
chequea(stt.set_umbral(150) == 100.0 and stt.set_umbral(-5) == 0.0 and stt.set_umbral("abc") == 0.0, "clamp 0-100 y entrada basura")
stt.set_umbral(62, guardar=True)
chequea(stt._leer_umbral_guardado() == 62.0, "persistencia en JSON")
try: stt.RUTA_CFG.unlink()
except OSError: pass

print("\nRESULTADO:", "TODO OK" if not fallos else f"{len(fallos)} FALLOS: {fallos}")
sys.exit(1 if fallos else 0)
