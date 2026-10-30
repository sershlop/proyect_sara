import base64
import math
import sys
import threading

_lock = threading.Lock()
_actual = {"seq": 0, "jpeg": "", "control_activo": False, "control_estado": ""}
_ventana = None


def _control_activo():
    with _lock:
        return _actual["control_activo"]


def _leer_fotogramas():
    global _ventana
    for linea in sys.stdin.buffer:
        if linea.strip() == b"CERRAR":
            ventana = _ventana
            if ventana is not None:
                try:
                    ventana.destroy()
                except Exception:
                    pass
            return
        if linea.startswith(b"CONTROL_ESTADO|"):
            partes = linea.strip().split(b"|", 2)
            if len(partes) != 3 or partes[1] not in (b"0", b"1"):
                continue
            try:
                mensaje = base64.urlsafe_b64decode(partes[2]).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                continue
            with _lock:
                _actual["control_activo"] = partes[1] == b"1"
                _actual["control_estado"] = mensaje[:300]
            continue
        if len(linea) > 1_400_000:
            continue
        try:
            imagen = base64.b64decode(linea.strip(), validate=True)
            if not imagen or len(imagen) > 1_000_000:
                continue
        except Exception:
            continue
        with _lock:
            _actual["seq"] += 1
            _actual["jpeg"] = base64.b64encode(imagen).decode("ascii")


class Api:
    def __init__(self, solicitado_desde):
        self.solicitado_desde = solicitado_desde
        self.posicion = (0.5, 0.5)

    def fotograma(self, ultimo):
        with _lock:
            if ultimo == _actual["seq"]:
                return {
                    "seq": _actual["seq"],
                    "jpeg": "",
                    "control_activo": _actual["control_activo"],
                    "control_estado": _actual["control_estado"],
                }
            return dict(_actual)

    def _emitir(self, mensaje):
        print(mensaje, flush=True)

    def recordar_posicion(self, x, y):
        if (isinstance(x, bool) or isinstance(y, bool)
                or not isinstance(x, (int, float)) or not isinstance(y, (int, float))
                or not math.isfinite(x) or not math.isfinite(y)
                or not 0 <= x <= 1 or not 0 <= y <= 1):
            return False
        self.posicion = (x, y)
        return True

    def cambiar_control(self, activo):
        if type(activo) is not bool:
            return False
        with _lock:
            _actual["control_activo"] = activo
            _actual["control_estado"] = (
                "Solicitando control remoto al celular…" if activo else "Control remoto desactivado."
            )
        self._emitir("CONTROL|" + ("1" if activo else "0"))
        return True

    def cambiar_perfil(self, fps):
        if type(fps) is not int or fps not in (2, 5, 8, 12):
            return False
        self._emitir(f"PERFIL|{fps}")
        return True

    def tocar(self, x, y):
        if (not _control_activo() or isinstance(x, bool) or isinstance(y, bool)
                or not isinstance(x, (int, float)) or not isinstance(y, (int, float))
                or not math.isfinite(x) or not math.isfinite(y)
                or not 0 <= x <= 1 or not 0 <= y <= 1):
            return False
        self._emitir(f"TOQUE|{x:.6f}|{y:.6f}")
        return True

    def deslizar(self, direccion):
        movimientos = {
            "arriba": (0.0, -0.55),
            "abajo": (0.0, 0.55),
            "izquierda": (-0.75, 0.0),
            "derecha": (0.95, 0.0),
        }
        if not _control_activo() or direccion not in movimientos:
            return False
        dx, dy = movimientos[direccion]
        self._emitir(f"DESLIZAR|0.500000|0.500000|{dx:.4f}|{dy:.4f}")
        return True

    def deslizar_delta(self, x, y, dx, dy):
        valores = (x, y, dx, dy)
        if (not _control_activo()
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) for v in valores)
                or not 0 <= x <= 1 or not 0 <= y <= 1
                or not -0.55 <= dx <= 0.55 or not -0.55 <= dy <= 0.55
                or (dx == 0 and dy == 0)):
            return False
        self.recordar_posicion(x, y)
        self._emitir(f"DESLIZAR|{x:.6f}|{y:.6f}|{dx:.4f}|{dy:.4f}")
        return True

    def arrastrar(self, x, y, destino_x, destino_y):
        valores = (x, y, destino_x, destino_y)
        if (not _control_activo()
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) for v in valores)
                or any(not 0 <= v <= 1 for v in valores)
                or (x == destino_x and y == destino_y)):
            return False
        self._emitir(
            f"ARRASTRAR|{x:.6f}|{y:.6f}|{destino_x:.6f}|{destino_y:.6f}"
        )
        return True

    def ir_inicio(self):
        if not _control_activo():
            return False
        self._emitir("INICIO")
        return True

    def cerrar(self):
        if _ventana is not None:
            _ventana.destroy()
        return True


def main():
    global _ventana
    solicitado_desde = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("laptop", "celular") else "laptop"
    etiqueta = "la laptop" if solicitado_desde == "laptop" else "el celular"
    try:
        import webview
    except Exception as exc:
        print("ERROR|pywebview no disponible: " + type(exc).__name__, flush=True)
        return 2
    threading.Thread(target=_leer_fotogramas, name="sara-visor-entrada", daemon=True).start()
    html = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>SARA · Pantalla del celular</title><style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;background:#050b14;color:#d6f6ff;font:14px Consolas,monospace}
body{display:flex;flex-direction:column}header,footer{padding:12px 16px;text-align:center;color:#00e5ff}
main{flex:1;min-height:0;display:grid;place-items:center;padding:8px;position:relative}
img{max-width:100%;max-height:100%;object-fit:contain;touch-action:manipulation}
#estado{color:#7fa6b8}button,select{background:#102332;color:#00e5ff;border:1px solid #00e5ff99;padding:8px 14px;border-radius:6px}
#estado_control{color:#ffd166;min-height:1.2em}
button:disabled{opacity:.45;cursor:not-allowed}
#marca{position:absolute;right:18px;top:18px;padding:7px 10px;background:#04101dcc;color:#00e5ff;border-radius:5px;font-weight:bold}
label{color:#d6f6ff} .opciones{display:flex;align-items:center;justify-content:center;gap:14px;flex-wrap:wrap;padding:6px}
main{display:flex;align-items:center;gap:10px}#vista{flex:1;min-width:0;min-height:0;height:100%;display:grid;place-items:center}
img{max-width:100%;max-height:100%;object-fit:contain;touch-action:none;user-select:none;-webkit-user-drag:none}
#direcciones{display:flex;flex-direction:column;align-items:center;gap:8px}
#direcciones div{display:flex;gap:8px}#direcciones button{min-width:44px;min-height:40px;font-size:18px;padding:4px}
</style></head><body><header> S.A.R.A // PANTALLA DEL CELULAR EN VIVO </header>
<main><div id="vista"><img id="imagen" alt="Esperando permiso y fotogramas del celular"></div>
<aside id="direcciones" aria-label="Controles remotos del celular">
<button id="inicio" disabled onclick="window.pywebview.api.ir_inicio()" title="Ir a la pantalla principal" aria-label="Pantalla principal">⌂</button>
<button disabled onclick="window.pywebview.api.deslizar('arriba')" title="Deslizar hacia arriba">▲</button>
<div><button disabled onclick="window.pywebview.api.deslizar('izquierda')" title="Deslizar hacia la izquierda">◀</button>
<button disabled onclick="window.pywebview.api.deslizar('derecha')" title="Deslizar hacia la derecha">▶</button></div>
<button disabled onclick="window.pywebview.api.deslizar('abajo')" title="Deslizar hacia abajo">▼</button>
</aside><div id="marca">S.A.R.A · SOLICITADO DESDE __ORIGEN__</div></main>
<footer><div id="estado">En espera del permiso de captura en el celular…</div>
<div id="estado_control" role="status" aria-live="polite"></div>
<div class="opciones">
<label><input id="control" type="checkbox"> Control táctil remoto</label>
<label>Perfil
<select id="perfil"><option value="2">Hasta 2 FPS · detalle</option><option value="5">Hasta 5 FPS · equilibrado</option><option value="8">Hasta 8 FPS · fluido</option><option value="12">Hasta 12 FPS · máxima fluidez</option></select></label>
<button onclick="window.pywebview.api.cerrar()">Detener y cerrar</button></div></footer>
<script>
let seq=0;async function tick(){try{const f=await window.pywebview.api.fotograma(seq);if(f.control_estado!==undefined){const c=document.getElementById('control');c.checked=f.control_activo;document.querySelectorAll('#direcciones button').forEach(b=>b.disabled=!f.control_activo);document.getElementById('estado_control').textContent=f.control_estado;}if(f.seq!==seq&&f.jpeg){seq=f.seq;document.getElementById('imagen').src='data:image/jpeg;base64,'+f.jpeg;document.getElementById('estado').textContent='En vivo · '+seq+' fotogramas recibidos';}}catch(e){}setTimeout(tick,120)}
window.addEventListener('pywebviewready',tick);
document.getElementById('control').onchange=e=>{window.pywebview.api.cambiar_control(e.target.checked);document.querySelectorAll('#direcciones button').forEach(b=>b.disabled=!e.target.checked)};
document.getElementById('perfil').onchange=e=>window.pywebview.api.cambiar_perfil(Number(e.target.value));
const vista=document.getElementById('vista'),imagen=document.getElementById('imagen');
function posicionImagen(clientX,clientY,limitar=false){if(!imagen.naturalWidth||!imagen.naturalHeight)return null;
const r=imagen.getBoundingClientRect(),k=Math.min(r.width/imagen.naturalWidth,r.height/imagen.naturalHeight),w=imagen.naturalWidth*k,h=imagen.naturalHeight*k,ox=(r.width-w)/2,oy=(r.height-h)/2;
const x=(clientX-r.left-ox)/w,y=(clientY-r.top-oy)/h;
if(limitar)return{x:Math.max(0,Math.min(1,x)),y:Math.max(0,Math.min(1,y))};
return x>=0&&x<=1&&y>=0&&y<=1?{x,y}:null}
let puntero=null;
imagen.addEventListener('pointerdown',e=>{if(!document.getElementById('control').checked||e.button!==0)return;
const p=posicionImagen(e.clientX,e.clientY);if(!p)return;e.preventDefault();imagen.setPointerCapture(e.pointerId);
puntero={id:e.pointerId,x:p.x,y:p.y,clientX:e.clientX,clientY:e.clientY,movido:false}});
imagen.addEventListener('pointermove',e=>{if(!puntero||puntero.id!==e.pointerId)return;
if(Math.hypot(e.clientX-puntero.clientX,e.clientY-puntero.clientY)>7)puntero.movido=true});
imagen.addEventListener('pointerup',e=>{if(!puntero||puntero.id!==e.pointerId)return;
const inicio=puntero;puntero=null;const fin=posicionImagen(e.clientX,e.clientY,inicio.movido);
if(!fin)return;
if(inicio.movido)window.pywebview.api.arrastrar(inicio.x,inicio.y,fin.x,fin.y);
else{window.pywebview.api.recordar_posicion(inicio.x,inicio.y);window.pywebview.api.tocar(inicio.x,inicio.y)}});
imagen.addEventListener('pointercancel',()=>{puntero=null});
let ruedaX=0,ruedaY=0,ruedaPos=null,temporizadorRueda=null;
vista.addEventListener('wheel',e=>{if(!document.getElementById('control').checked)return;
const p=posicionImagen(e.clientX,e.clientY);if(!p)return;e.preventDefault();ruedaPos=p;
const factor=e.deltaMode===1?16:e.deltaMode===2?Math.max(vista.clientHeight,1):1;ruedaX+=e.deltaX*factor;ruedaY+=e.deltaY*factor;
clearTimeout(temporizadorRueda);temporizadorRueda=setTimeout(()=>{if(!ruedaPos)return;
const dx=Math.max(-.55,Math.min(.55,-ruedaX/120*.55)),dy=Math.max(-.55,Math.min(.55,-ruedaY/120*.55));
if(Math.abs(dx)+Math.abs(dy)>=.04)window.pywebview.api.deslizar_delta(ruedaPos.x,ruedaPos.y,dx,dy);
ruedaX=0;ruedaY=0;ruedaPos=null},400)}, {passive:false});
</script>
</body></html>"""
    html = html.replace("__ORIGEN__", etiqueta)
    _ventana = webview.create_window(
        "S.A.R.A — Pantalla del celular · " + etiqueta,
        html=html,
        js_api=Api(solicitado_desde),
        width=1100,
        height=800,
        on_top=True,
    )
    _ventana.events.closed += lambda: print("CERRADO", flush=True)
    print("LISTO", flush=True)
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
