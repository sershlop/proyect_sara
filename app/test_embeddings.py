import os, sys, json, threading, http.server, socketserver, tempfile, importlib, shutil
import numpy as np, onnx
from onnx import helper, TensorProto, numpy_helper
from tokenizers import Tokenizer, models, pre_tokenizers, processors

DIM, VOCAB = 384, 50
tmp = tempfile.mkdtemp()
rng = np.random.default_rng(7)
W = rng.standard_normal((VOCAB, DIM)).astype(np.float32)

def hacer_modelo(ruta, salida="last_hidden_state"):
    ii = helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["b","s"])
    am = helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["b","s"])
    tt = helper.make_tensor_value_info("token_type_ids", TensorProto.INT64, ["b","s"])
    nodos = [helper.make_node("Gather", ["W","input_ids"], ["h"], axis=0)]
    if salida == "last_hidden_state":
        nodos[0].output[0] = "last_hidden_state"
        out = helper.make_tensor_value_info("last_hidden_state", TensorProto.FLOAT, ["b","s",DIM])
    else:
        nodos.append(helper.make_node("ReduceMean", ["h"], ["sentence_embedding"], axes=[1], keepdims=0)) if False else None
        # opset 13: ReduceMean con atributo axes
        nodos.append(helper.make_node("ReduceMean", ["h"], ["sentence_embedding"], axes=[1], keepdims=0))
        out = helper.make_tensor_value_info("sentence_embedding", TensorProto.FLOAT, ["b",DIM])
    g = helper.make_graph(nodos, "toy", [ii,am,tt], [out], [numpy_helper.from_array(W, "W")])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)]); m.ir_version = 8
    onnx.save(m, ruta)

palabras = ["<unk>","<s>","</s>","<pad>"] + [f"p{i}" for i in range(VOCAB-4)]
vocab = {w:i for i,w in enumerate(palabras)}
def hacer_tok(ruta):
    t = Tokenizer(models.WordLevel(vocab, unk_token="<unk>"))
    t.pre_tokenizer = pre_tokenizers.Whitespace()
    t.post_processor = processors.TemplateProcessing(single="<s> $A </s>", special_tokens=[("<s>",1),("</s>",2)])
    t.save(ruta)

d = os.path.join(tmp, "emb"); os.makedirs(d)
hacer_modelo(os.path.join(d,"model.onnx")); hacer_tok(os.path.join(d,"tokenizer.json"))
os.environ["SARA_EMB_DIR"] = d
import embeddings as E
E._VARIANTES["fp32"] = ("onnx/model.onnx", 1000); E._TOKENIZADOR = ("tokenizer.json", 100)

def ref(texto):
    ids = [1] + [vocab.get(p, 0) for p in texto.split()][:E._MAX_TOKENS-2] + [2]
    return W[ids].mean(axis=0)

ok = True
def chk(nombre, cond):
    global ok; ok &= bool(cond); print(("OK  " if cond else "FALLA"), nombre)

# 1 vector correcto
v = E.vector_desde_texto("p1 p2 p3 p9")
chk("backend onnx", E.backend()=="onnx")
chk("384 valores y tipo list de float", isinstance(v,list) and len(v)==384 and isinstance(v[0],float))
chk("igual al mean pooling de referencia", np.allclose(v, ref("p1 p2 p3 p9"), atol=1e-6))
# 2 truncado a 128 tokens
largo = " ".join(f"p{i%40}" for i in range(400))
vl = E.vector_desde_texto(largo)
chk("truncado a 128 tokens", np.allclose(vl, ref(largo), atol=1e-6))
# 3 vacío / desconocidas
chk("texto vacío → None", E.vector_desde_texto("") is None)
chk("palabras desconocidas no rompen", E.vector_desde_texto("zzz yyy") is not None)
# 4 liberar y recargar
E.liberar(); chk("recarga tras liberar", E.vector_desde_texto("p5") is not None)
# 5 salida con sentence_embedding
d2 = os.path.join(tmp,"emb2"); os.makedirs(d2)
hacer_modelo(os.path.join(d2,"model.onnx"), "sentence_embedding"); hacer_tok(os.path.join(d2,"tokenizer.json"))
os.environ["SARA_EMB_DIR"] = d2; E.liberar()
v2 = E.vector_desde_texto("p1 p2")
chk("salida sentence_embedding usada directamente", v2 is not None and len(v2)==384 and np.allclose(v2, ref("p1 p2"), atol=1e-6))
# 6 sin archivos y sin sentence-transformers
d3 = os.path.join(tmp,"vacia"); os.makedirs(d3); os.environ["SARA_EMB_DIR"] = d3; E.liberar()
chk("sin archivos → no disponible", E.esta_disponible() is False)
chk("motivo menciona --instalar", "--instalar" in E.motivo())
chk("vector_desde_texto → None", E.vector_desde_texto("hola") is None)
# 7 buscar_mas_similar: numpy == bucle puro
cands = [(f"c{i}", rng.standard_normal(DIM).astype(np.float32).tolist()) for i in range(200)]
q = (np.asarray(cands[37][1]) + 0.05*rng.standard_normal(DIM)).tolist()
r = E.buscar_mas_similar(q, cands, umbral=0.8)
best = max(cands, key=lambda c: E._coseno(q, c[1]))
chk("mejor candidato correcto", r and r["identificador"]==best[0] and abs(r["score"]-E._coseno(q,best[1]))<1e-5)
chk("bajo el umbral → None", E.buscar_mas_similar(q, cands, umbral=0.9999999) is None)
rag = cands[:5] + [("raro", [1.0,2.0])]
r2 = E.buscar_mas_similar(cands[2][1], rag, umbral=0.5)
chk("vectores de distinto largo → cae al bucle de siempre", r2 and r2["identificador"]=="c2")
chk("vector cero no explota", E.buscar_mas_similar([0.0]*DIM, cands, umbral=0.1) is None)
chk("listas vacías → None", E.buscar_mas_similar([], cands) is None and E.buscar_mas_similar(q, []) is None)
# 8 descarga con reanudación contra servidor local con Range
srv_dir = os.path.join(tmp, "srv"); os.makedirs(os.path.join(srv_dir,"onnx"))
shutil.copy(os.path.join(d,"model.onnx"), os.path.join(srv_dir,"onnx","model.onnx")); shutil.copy(os.path.join(d,"tokenizer.json"), srv_dir)
class H(http.server.SimpleHTTPRequestHandler):
    def __init__(s,*a,**k): super().__init__(*a, directory=srv_dir, **k)
    def log_message(s,*a): pass
    def do_GET(s):
        p = os.path.join(srv_dir, s.path.split("?")[0].lstrip("/"))
        if not os.path.isfile(p): s.send_error(404); return
        data = open(p,"rb").read(); rng_h = s.headers.get("Range")
        if rng_h:
            ini = int(rng_h.split("=")[1].split("-")[0]); s.send_response(206); data = data[ini:]
        else: s.send_response(200)
        s.send_header("Content-Length", str(len(data))); s.end_headers(); s.wfile.write(data)
httpd = socketserver.TCPServer(("127.0.0.1",0), H); port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
E._BASE_URL = f"http://127.0.0.1:{port}/" + "{ruta}"
d4 = os.path.join(tmp,"descarga"); os.environ["SARA_EMB_DIR"] = d4; E.liberar()
# deja un .part a medias para probar la reanudación
os.makedirs(d4); full = open(os.path.join(d,"model.onnx"),"rb").read()
open(os.path.join(d4,"model.onnx.part"),"wb").write(full[:len(full)//3])
chk("asegurar() descarga y reanuda", E.asegurar() is True)
chk("archivo descargado idéntico", open(os.path.join(d4,"model.onnx"),"rb").read()==full)
chk("tras asegurar() la Capa 2 funciona", E.vector_desde_texto("p1 p2 p3 p9") is not None and E.backend()=="onnx")
# 9 descarga rota (404)
d5 = os.path.join(tmp,"rota"); os.environ["SARA_EMB_DIR"] = d5; E.liberar()
E._BASE_URL = f"http://127.0.0.1:{port}/nada/" + "{ruta}"
chk("asegurar() con 404 → False y motivo", E.asegurar() is False and "pude" in E.motivo())
# 9b inactividad: se prueba en procesos limpios (el entorno se fija antes de importar)
import subprocess
here = os.path.dirname(os.path.abspath(__file__))
for modo, minutos, pct in (("idle", "0.03", "0"), ("sinpresion", "0.03", "101"), ("nunca", "0", "80")):
    e2 = dict(os.environ); e2["SARA_EMB_DIR"] = d; e2["SARA_EMB_LIBERAR_MIN"] = minutos; e2["SARA_EMB_LIBERAR_RAM_PCT"] = pct; e2["PYTHONPATH"] = here
    r = subprocess.run([sys.executable, os.path.join(here, "test_embeddings_idle.py"), modo], capture_output=True, text=True, env=e2, timeout=120)
    print("".join("   " + l + "\n" for l in r.stdout.splitlines()), end="")
    chk(f"inactividad ({modo})", r.returncode == 0)
# opciones de memoria de ORT
os.environ["SARA_EMB_DIR"] = d; os.environ["SARA_EMB_LIBERAR_MIN"] = "10"
for arena, opt in (("0","basic"),("1","extended"),("0","none"),("1","valor_raro")):
    os.environ["SARA_EMB_ARENA"] = arena; os.environ["SARA_EMB_OPT"] = opt; E.liberar()
    vv = E.vector_desde_texto("p1 p2 p3 p9")
    chk(f"ORT arena={arena} opt={opt} da el mismo vector", vv is not None and np.allclose(vv, ref("p1 p2 p3 p9"), atol=1e-6))
os.environ.pop("SARA_EMB_ARENA"); os.environ.pop("SARA_EMB_OPT"); os.environ.pop("SARA_EMB_LIBERAR_MIN"); E.liberar()
os.environ["SARA_EMB_DIR"] = d4

# 9c comparación fp32 ↔ int8 (modelos de juguete: el int8 es el mismo con los pesos perturbados)
import io, contextlib
d6 = os.path.join(tmp, "dosvariantes"); os.makedirs(d6)
shutil.copy(os.path.join(d,"model.onnx"), os.path.join(d6,"model.onnx")); shutil.copy(os.path.join(d,"tokenizer.json"), os.path.join(d6,"tokenizer.json"))
fr = os.path.join(tmp, "frases.txt")
open(fr,"w",encoding="utf-8").write("\n".join(" ".join(f"p{(i*3+j)%40}" for j in range(4)) for i in range(14)))
E._VARIANTES["int8"] = ("onnx/model_quint8_avx2.onnx", 1000)
def comparar(ruido, args):
    global W
    W0 = W; W = (W0 + ruido*np.random.default_rng(3).standard_normal(W0.shape)).astype(np.float32)
    hacer_modelo(os.path.join(d6,"model_quint8_avx2.onnx")); W = W0
    os.environ["SARA_EMB_DIR"] = d6; E.liberar()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = E._main(["--comparar-int8"] + args)
    return rc, buf.getvalue()
rc, out = comparar(0.02, [fr])
chk("comparar-int8 con ruido pequeño → OK (rc 0)", rc == 0 and "[OK]" in out and "14 frases" in out and "ms/frase" in out)
rc, out = comparar(3.0, [fr])
chk("comparar-int8 con ruido enorme → avisa (rc 1)", rc == 1 and "[!!]" in out)
chk("comparar-int8 restaura el entorno", "SARA_EMB_MODELO" not in os.environ and "SARA_EMB_BACKEND" not in os.environ)
os.remove(os.path.join(d6,"model_quint8_avx2.onnx")); E.liberar()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = E._main(["--comparar-int8", fr])
chk("sin modelo int8 → mensaje claro (rc 1)", rc == 1 and "falta el modelo int8" in buf.getvalue())
os.environ["SARA_EMB_DIR"] = d4; E.liberar()

# 10 CLI
E._BASE_URL = f"http://127.0.0.1:{port}/" + "{ruta}"; os.environ["SARA_EMB_DIR"] = d4; E.liberar()
chk("CLI --probar", E._main(["--probar"])==0)
httpd.shutdown(); shutil.rmtree(tmp)
print("\nTODO OK" if ok else "\nHAY FALLOS"); sys.exit(0 if ok else 1)
