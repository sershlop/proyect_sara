# Hijo de test_embeddings.py: prueba la descarga por inactividad en un proceso limpio (el entorno se fija ANTES de importar).
import os, sys, time
import embeddings as E
E._VARIANTES["fp32"] = ("onnx/model.onnx", 1000); E._TOKENIZADOR = ("tokenizer.json", 100)
modo = sys.argv[1]
ok = True
def chk(n, c):
    global ok; ok &= bool(c); print(("OK  " if c else "FALLA"), n)
frase = "p1 p2 p3 p9"
if modo == "idle":                      # SARA_EMB_LIBERAR_MIN=0.03 (≈1.8 s)
    chk("antes de usarse no hay modelo", E._modelo is None)
    r1 = E.vector_desde_texto(frase)
    chk("tras usarlo está cargado y hay vigilante", E._modelo is not None and E._vigilando is True)
    time.sleep(3.2)
    chk("tras ~3 s sin uso se soltó solo", E._modelo is None)
    r2 = E.vector_desde_texto(frase)
    chk("la siguiente consulta lo recarga y da lo mismo", r2 == r1 and E._modelo is not None)
    fin = time.time() + 3.0
    while time.time() < fin:
        E.vector_desde_texto("p4 p5"); time.sleep(0.2)
    chk("usándolo seguido NO se suelta", E._modelo is not None)
    time.sleep(3.2)
    chk("se soltó de nuevo al dejar de usarlo", E._modelo is None)
    E.vector_desde_texto(frase)
    time.sleep(0.3)
    chk("el vigilante vuelve a arrancar tras recargar", E._vigilando is True and E._modelo is not None)
    E.liberar(); time.sleep(1.0)
    chk("liberar() a mano: el vigilante termina solo", E._vigilando is False)
elif modo == "sinpresion":              # RAM holgada (RAM_PCT=101): aunque esté ocioso, NO se suelta
    E.vector_desde_texto(frase)
    time.sleep(3.2)
    chk("con la RAM holgada no se suelta aunque esté ocioso", E._modelo is not None and E._vigilando is True)
    t = time.monotonic(); r = E.vector_desde_texto(frase)
    chk("y sigue respondiendo al instante (sin recarga)", r is not None and time.monotonic() - t < 0.5)
elif modo == "nunca":                   # SARA_EMB_LIBERAR_MIN=0
    E.vector_desde_texto(frase)
    time.sleep(1.5)
    chk("con LIBERAR_MIN=0 no hay vigilante y no se suelta", E._modelo is not None and E._vigilando is False)
sys.exit(0 if ok else 1)
