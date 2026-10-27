# 🖼️ visor_foto.py — ventana para ver una foto tomada por el celular y decidir qué hacer con ella (v1)
# La lanza celular.py como proceso aparte:  python visor_foto.py <foto.jpg> <carpeta_donde_guardar_en_la_laptop>
# Botones: 💾 Guardar en la laptop · 📱 Guardar en el celular (Galería, carpeta SARA) · ✖ Cerrar sin guardar.
# Habla con celular.py por stdout (una línea por evento):  LAPTOP|<ruta>  ·  CELULAR  ·  ERROR|<motivo>
# Usa pywebview, que ya viene en runtime\python (es la misma librería de la ventana de SARA).
import os
import pathlib
import shutil
import sys


def _emitir(texto: str) -> None:
    try:
        print(texto, flush=True)
    except Exception:
        pass


_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Foto del celular</title>
<style>
 html,body{margin:0;height:100%;background:#050b14;color:#d6f6ff;font-family:Consolas,monospace}
 body{display:flex;flex-direction:column}
 #img{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;padding:12px}
 #img img{max-width:100%;max-height:100%;object-fit:contain;border:1px solid #00e5ff66;border-radius:8px}
 #estado{text-align:center;padding:6px 12px;color:#7fa6b8;font-size:13px;min-height:20px}
 #barra{display:flex;gap:10px;padding:10px 12px 14px;justify-content:center;flex-wrap:wrap}
 button{font:600 14px Consolas,monospace;color:#00e5ff;background:#00e5ff14;border:1px solid #00e5ff99;
        border-radius:8px;padding:10px 16px;cursor:pointer}
 button:hover{background:#00e5ff33}
 button:disabled{opacity:.45;cursor:default}
 #b3{color:#ff9f1c;border-color:#ff9f1c99;background:#ff9f1c14}
</style></head><body>
<div id="img"><img src="__FOTO__" alt="foto"></div>
<div id="estado">Foto tomada con tu celular. No se guardó en ningún lado: tú decides.</div>
<div id="barra">
 <button id="b1" onclick="act('laptop')">💾 Guardar en la laptop</button>
 <button id="b2" onclick="act('celular')">📱 Guardar en el celular</button>
 <button id="b3" onclick="act('cerrar')">✖ Cerrar sin guardar</button>
</div>
<script>
async function act(que){
  const e = document.getElementById('estado');
  try{
    const r = await window.pywebview.api[que]();
    if(r) e.textContent = r;
    if(que==='laptop') document.getElementById('b1').disabled = true;
    if(que==='celular') document.getElementById('b2').disabled = true;
  }catch(x){ e.textContent = 'No pude hacer eso: ' + x; }
}
</script></body></html>"""


class Api:
    """Métodos que llama la página (los nombres con _ no se exponen)."""

    def __init__(self, foto: str, destino: str):
        self._foto = foto
        self._destino = destino
        self._ventana = None

    def laptop(self) -> str:
        try:
            os.makedirs(self._destino, exist_ok=True)
            raiz, ext = os.path.splitext(os.path.basename(self._foto))
            dst, i = os.path.join(self._destino, raiz + ext), 1
            while os.path.exists(dst):
                dst = os.path.join(self._destino, f"{raiz} ({i}){ext}")
                i += 1
            shutil.copy2(self._foto, dst)
            _emitir("LAPTOP|" + dst)
            return "✔ Guardada en la laptop: " + dst
        except Exception as e:
            return "✖ No pude guardarla en la laptop (" + type(e).__name__ + ")."

    def celular(self) -> str:
        _emitir("CELULAR")
        return "✔ Mandé la orden: se guarda en la Galería del celular (carpeta SARA)."

    def cerrar(self) -> None:
        try:
            if self._ventana is not None:
                self._ventana.destroy()
        except Exception:
            pass


def main() -> int:
    if len(sys.argv) < 3 or not os.path.isfile(sys.argv[1]):
        _emitir("ERROR|uso: visor_foto.py <foto> <carpeta>")
        return 2
    foto, destino = os.path.abspath(sys.argv[1]), sys.argv[2]
    try:
        import webview
    except Exception as e:
        _emitir("ERROR|sin pywebview: " + type(e).__name__)
        try:
            os.startfile(foto)               # al menos se ve la foto con el visor de Windows
        except Exception:
            pass
        return 1
    html_path = os.path.join(os.path.dirname(foto), f"visor_{os.getpid()}.html")
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(_HTML.replace("__FOTO__", pathlib.Path(foto).name))
        api = Api(foto, destino)
        w = webview.create_window("S.A.R.A — Foto del celular", url=pathlib.Path(html_path).as_uri(),
                                  js_api=api, width=920, height=760, on_top=True)
        api._ventana = w
        webview.start()
        return 0
    except Exception as e:
        _emitir("ERROR|" + type(e).__name__ + ": " + str(e)[:80])
        try:
            os.startfile(foto)
        except Exception:
            pass
        return 1
    finally:
        try:
            os.remove(html_path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
