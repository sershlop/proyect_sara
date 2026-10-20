/**
 * apariencia.js — Menú de personalización visual de SARA (NUEVO)
 *
 * Línea blanca en el borde izquierdo (centro vertical). Al pulsarla se despliega un panel con dos secciones:
 *   1) Aureola / logo central → miniaturas de las formas (SaraAureola.formas / previa)
 *   2) Fondo / interfaz       → miniaturas de los temas (clase tema-<id> en <body>, definidos en style.css;
 *                               «Matriz» añade además una lluvia de código en un <canvas> propio)
 * Los colores de estado de la aureola (azul, naranja, verde, rojo, violeta de voz y lector) NO cambian con ningún tema.
 *
 * Persistencia: localStorage + archivo apariencia_gui.json vía GET/POST /api/apariencia (server.py).
 * Si el servidor no tiene esas rutas, sigue funcionando solo con localStorage (y deja un aviso en consola).
 * 3) Voz de SARA        → lista de voces (GET/POST /api/voz); al elegir una suena una muestra. Se oculta si el servidor no la ofrece.
 *                           (v2) Agrupa por motor (neuronales con internet / voces de Windows instaladas), marca las que no
 *                           responden, dice con qué voz suena SARA mientras Edge-TTS está caído y ofrece «Reintentar».
 * Depende de: aureola.js (SaraAureola.setForma). Se carga DESPUÉS de aureola.js y ANTES de app.js.
 */
(() => {
    'use strict';
    const A = window.SaraAureola;
    if (!A || !A.setForma) return;

    const CLAVE = 'sara_apariencia';
    const FONDOS = [
        { id: 'clasico', nombre: 'Clásico' },
        { id: 'jarvis', nombre: 'Jarvis' },
        { id: 'cuadricula', nombre: 'Cuadrícula' },
        { id: 'carbono', nombre: 'Carbono' },
        { id: 'aurora', nombre: 'Aurora' },
        { id: 'circuito', nombre: 'Circuito' },
        { id: 'ambar', nombre: 'Ámbar HUD' },
        { id: 'matrix', nombre: 'Matriz' },
        { id: 'oled', nombre: 'Negro OLED' },
    ];
    const POR_DEFECTO = { forma: 'gota', fondo: 'clasico' };
    let sel = { ...POR_DEFECTO };

    // ── Lluvia de código (fondo «Matriz»): canvas propio, solo existe mientras ese fondo está activo ──
    const lluvia = (() => {
        const CARS = 'ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789{}[]<>/\\=+*;:$#@%&|~';
        const TAM = 16;                        // px por carácter
        const FPS = 30;                        // tope de cuadros: lluvia lenta y barata
        const reducir = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        let cv = null, cx = null, cols = [], filas = 0, raf = 0, previo = 0, activo = false;
        const car = () => CARS[Math.floor(Math.random() * CARS.length)];

        function nuevaColumna(inicial) {
            return { y: inicial ? Math.random() * filas : -Math.random() * 20, v: 5 + Math.random() * 8,
                     len: 12 + Math.floor(Math.random() * 18), tono: Math.random(),
                     ch: Array.from({ length: filas + 2 }, car) };
        }
        function medir() {
            cv.width = window.innerWidth; cv.height = window.innerHeight;
            filas = Math.ceil(cv.height / TAM) + 1;
            cols = Array.from({ length: Math.ceil(cv.width / TAM) }, () => nuevaColumna(true));
        }
        function dibujar(dt) {
            cx.clearRect(0, 0, cv.width, cv.height);
            cx.font = TAM + 'px "MS Gothic","Yu Gothic","Consolas",monospace';
            cols.forEach((c, i) => {
                c.y += c.v * dt;
                if (c.y - c.len > filas) Object.assign(c, nuevaColumna(false));
                const cabeza = Math.floor(c.y);
                for (let k = 0; k < c.len; k++) {
                    const fila = cabeza - k;
                    if (fila < 0 || fila >= filas) continue;
                    if (Math.random() < 0.01) c.ch[fila] = car();           // los caracteres «mutan» al caer
                    const a = Math.pow(1 - k / c.len, 2) * 0.85;
                    cx.fillStyle = k === 0 ? 'rgba(200,255,225,0.95)'
                        : `rgba(0,255,${Math.round(110 + c.tono * 110)},${a.toFixed(3)})`;   // verde → cian según la columna
                    cx.fillText(c.ch[fila], i * TAM, (fila + 1) * TAM);
                }
            });
        }
        function paso(ahora) {
            raf = requestAnimationFrame(paso);
            if (ahora - previo < 1000 / FPS) return;
            const dt = Math.min((ahora - previo) / 1000, 0.1);
            previo = ahora;
            dibujar(dt);
        }
        function iniciar() {
            if (activo) return;
            cv = document.createElement('canvas');
            cv.id = 'matrix-canvas'; cv.className = 'matrix-canvas'; cv.setAttribute('aria-hidden', 'true');
            cx = cv.getContext && cv.getContext('2d');
            if (!cx) { console.warn('[apariencia] este navegador no ofrece canvas 2D: sin lluvia de código'); cv = null; return; }
            document.body.appendChild(cv);
            activo = true;
            medir();
            window.addEventListener('resize', alCambiarTamano);
            if (reducir) dibujar(0); else { previo = performance.now(); raf = requestAnimationFrame(paso); }
        }
        function alCambiarTamano() { if (activo) { medir(); if (reducir) dibujar(0); } }
        function detener() {
            if (!activo) return;
            activo = false;
            cancelAnimationFrame(raf);
            window.removeEventListener('resize', alCambiarTamano);
            if (cv) cv.remove();
            cv = cx = null; cols = [];
        }
        return { iniciar, detener };
    })();

    const idsFormas = () => A.formas().map((f) => f.id);
    const validar = (d) => ({
        forma: d && idsFormas().includes(d.forma) ? d.forma : POR_DEFECTO.forma,
        fondo: d && FONDOS.some((f) => f.id === d.fondo) ? d.fondo : POR_DEFECTO.fondo,
    });

    function leerLocal() {
        try { return JSON.parse(localStorage.getItem(CLAVE) || 'null'); }
        catch (e) { console.warn('[apariencia] no pude leer localStorage:', e); return null; }
    }

    function guardar() {
        try { localStorage.setItem(CLAVE, JSON.stringify(sel)); }
        catch (e) { console.warn('[apariencia] no pude escribir localStorage:', e); }
        fetch('/api/apariencia', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(sel),
        }).then((r) => { if (!r.ok) console.warn('[apariencia] el servidor no guardó la preferencia (HTTP ' + r.status + ')'); })
          .catch((e) => console.warn('[apariencia] sin conexión con /api/apariencia:', e));
    }

    // ── Aplicar ────────────────────────────────────────────────────────────────
    function aplicar() {
        A.setForma(sel.forma);
        FONDOS.forEach((f) => document.body.classList.remove('tema-' + f.id));
        document.body.classList.add('tema-' + sel.fondo);
        if (sel.fondo === 'matrix') lluvia.iniciar(); else lluvia.detener();
        marcar();
    }

    // ── Interfaz del menú ──────────────────────────────────────────────────────
    const tab = document.createElement('button');
    tab.id = 'apar-tab'; tab.className = 'apar-tab'; tab.type = 'button';
    tab.title = 'Personalizar apariencia'; tab.setAttribute('aria-label', 'Personalizar apariencia');
    tab.setAttribute('aria-expanded', 'false');

    const panel = document.createElement('div');
    panel.id = 'apar-panel'; panel.className = 'apar-panel hidden';
    panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', 'Apariencia de SARA');
    panel.innerHTML =
        '<div class="apar-header"><span>🎨 Apariencia</span>' +
        '<button type="button" class="apar-reset" id="apar-reset" title="Volver al estilo original">Restablecer</button></div>' +
        '<div class="apar-seccion">Aureola / logo central</div><div class="apar-grid" id="apar-formas"></div>' +
        '<div class="apar-seccion">Fondo / interfaz</div><div class="apar-grid" id="apar-fondos"></div>' +
        '<div class="apar-seccion apar-voz-seccion hidden">Voz de SARA</div>' +
        '<div class="apar-aviso hidden" id="apar-voz-aviso" role="status"></div>' +
        '<div class="apar-voces hidden" id="apar-voces"></div>';

    function mini(tipo, id, nombre, vistaHTML, claseVista) {
        const b = document.createElement('button');
        b.type = 'button'; b.className = 'apar-mini'; b.dataset.tipo = tipo; b.dataset.valor = id;
        b.setAttribute('aria-pressed', 'false'); b.title = nombre;
        b.innerHTML = `<span class="apar-vista ${claseVista}">${vistaHTML}</span><span class="apar-nombre">${nombre}</span>`;
        return b;
    }

    function construir() {
        const gF = panel.querySelector('#apar-formas');
        A.formas().forEach((f) => {
            gF.appendChild(mini('forma', f.id, f.nombre,
                `<svg viewBox="-100 -100 200 200" aria-hidden="true">${A.previa(f.id)}</svg>`, 'apar-vista-forma'));
        });
        const gB = panel.querySelector('#apar-fondos');
        FONDOS.forEach((f) => {
            gB.appendChild(mini('fondo', f.id, f.nombre, '', 'mini-fondo tema-' + f.id));
        });
    }

    // ── Voz de SARA ───────────────────────────────────────────────────────────
    let vozActual = '';
    const ICONO_VOZ = { M: '♂', F: '♀', S: '🖥' };
    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

    function marcarVoz() {
        panel.querySelectorAll('.apar-voz').forEach((b) =>
            b.setAttribute('aria-pressed', b.dataset.valor === vozActual ? 'true' : 'false'));
    }

    function botonVoz(v) {
        const b = document.createElement('button');
        b.type = 'button'; b.className = 'apar-voz'; b.dataset.valor = v.id; b.setAttribute('aria-pressed', 'false');
        b.dataset.disp = v.disponible === false ? '0' : '1';
        b.dataset.inst = v.instalada === false ? '0' : '1';
        const nota = v.disponible === false ? (v.motor === 'piper' ? ' · falta instalar piper-tts'
            : (v.motor === 'kokoro' ? ' · falta instalar kokoro-onnx' : ' · no responde ahora'))
            : (v.instalada === false ? ` · se descarga al elegirla (${v.peso || '~60 MB'})` : '');
        b.innerHTML = `<span class="apar-voz-ico">${ICONO_VOZ[v.genero] || '🔊'}</span>` +
            `<span class="apar-voz-txt"><b>${esc(v.nombre)}</b><small>${esc(v.region)} · ${esc(v.estilo)}${nota}</small></span>`;
        return b;
    }

    function pintarAviso(d) {
        const aviso = panel.querySelector('#apar-voz-aviso');
        const caido = d.edge && d.edge.estado === 'caido';
        aviso.classList.toggle('hidden', !caido);
        if (!caido) { aviso.textContent = ''; return; }
        const suena = d.suena_con ? `SARA suena con «${esc(d.suena_con)}».` : 'SARA suena con la voz de Windows.';
        aviso.innerHTML = `<span>Las voces neuronales no responden ahora (Microsoft rechaza la conexión). ${suena} ` +
            'Tu elección se conserva y vuelve sola cuando el servicio responda.</span>' +
            '<button type="button" class="apar-reintentar" id="apar-voz-reintentar">Reintentar</button>';
    }

    function cargarVoces() {
        fetch('/api/voz')
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => {
                if (!d || !Array.isArray(d.voces) || !d.voces.length) return;     // sin voz.py/servidor nuevo: sección oculta
                const lista = panel.querySelector('#apar-voces');
                lista.textContent = '';
                // Con motor (voz.py v3.3) se agrupa; sin él (versión anterior) se muestra la lista plana de siempre.
                const grupos = [
                    ['kokoro', 'Neuronales · sin internet · Kokoro (la mejor calidad)'],
                    ['piper', 'Neuronales · sin internet · Piper (calidad alta)'],
                    ['edge', 'Neuronales · necesitan internet'],
                    ['windows', 'Windows · sin internet'],
                ];
                const hayMotor = d.voces.some((v) => v.motor);
                if (!hayMotor) d.voces.forEach((v) => lista.appendChild(botonVoz(v)));
                else {
                    grupos.sort((a, b) => {                                     // lo que funciona, primero
                        const ok = (m) => d.voces.some((v) => v.motor === m && v.disponible !== false);
                        return Number(ok(b[0])) - Number(ok(a[0]));
                    }).forEach(([motor, titulo]) => {
                        const items = d.voces.filter((v) => v.motor === motor);
                        if (!items.length) return;
                        const h = document.createElement('div');
                        h.className = 'apar-subseccion'; h.textContent = titulo;
                        lista.appendChild(h);
                        items.forEach((v) => lista.appendChild(botonVoz(v)));
                    });
                }
                vozActual = d.actual || '';
                panel.querySelector('.apar-voz-seccion').classList.remove('hidden');
                lista.classList.remove('hidden');
                pintarAviso(d);
                marcarVoz();
            })
            .catch((e) => console.warn('[apariencia] no pude leer /api/voz:', e));
    }

    function reintentarNeuronales(btn) {
        btn.disabled = true; btn.textContent = 'Probando…';
        fetch('/api/voz', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ accion: 'reintentar' }) })
            .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(() => cargarVoces())                                          // repinta el estado real, ya sea que volvió o no
            .catch((e) => { console.warn('[apariencia] no pude reintentar las voces neuronales:', e);
                            btn.disabled = false; btn.textContent = 'Reintentar'; });
    }

    function elegirVoz(id) {
        const previa = vozActual;
        const btn = panel.querySelector(`.apar-voz[data-valor="${CSS.escape(id)}"]`);
        const aviso = panel.querySelector('#apar-voz-aviso');
        const bajando = btn && btn.dataset.inst === '0';           // voz offline aún sin descargar: tarda unos segundos
        vozActual = id; marcarVoz();
        if (bajando && aviso) { aviso.textContent = 'Descargando la voz (solo esta vez; puede tardar unos minutos)… suena sola al terminar.'; aviso.classList.remove('hidden'); }
        fetch('/api/voz', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ voz: id, probar: true }) })
            .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json().catch(() => ({})); })
            .then((d) => { if (d && d.ok === false) throw new Error('el servidor rechazó la voz'); if (bajando) cargarVoces(); })
            .catch((e) => {
                console.warn('[apariencia] no pude cambiar la voz:', e); vozActual = previa; marcarVoz();
                if (bajando && aviso) { aviso.textContent = 'No pude descargar esa voz (¿sin internet?). Sigue la anterior.'; aviso.classList.remove('hidden'); }
            });
    }

    function marcar() {
        panel.querySelectorAll('.apar-mini').forEach((b) => {
            const activo = (b.dataset.tipo === 'forma' ? sel.forma : sel.fondo) === b.dataset.valor;
            b.setAttribute('aria-pressed', activo ? 'true' : 'false');
        });
    }

    function alternar(abrir) {
        const mostrar = abrir === undefined ? panel.classList.contains('hidden') : abrir;
        panel.classList.toggle('hidden', !mostrar);
        tab.setAttribute('aria-expanded', mostrar ? 'true' : 'false');
    }

    function iniciar() {
        document.body.appendChild(tab);
        document.body.appendChild(panel);
        construir();

        tab.addEventListener('click', (e) => { e.stopPropagation(); alternar(); });
        panel.addEventListener('click', (e) => {
            e.stopPropagation();
            const rt = e.target.closest('#apar-voz-reintentar');
            if (rt) { reintentarNeuronales(rt); return; }
            const bv = e.target.closest('.apar-voz');
            if (bv) { elegirVoz(bv.dataset.valor); return; }
            const b = e.target.closest('.apar-mini');
            if (b) {
                if (b.dataset.tipo === 'forma') sel.forma = b.dataset.valor; else sel.fondo = b.dataset.valor;
                aplicar(); guardar();
            } else if (e.target.id === 'apar-reset') {
                sel = { ...POR_DEFECTO }; aplicar(); guardar();
            }
        });
        document.addEventListener('click', (e) => {            // clic fuera → cierra
            if (!panel.classList.contains('hidden') && !panel.contains(e.target) && e.target !== tab) alternar(false);
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !panel.classList.contains('hidden')) alternar(false);
        });

        // 1) lo guardado localmente se aplica de inmediato (sin parpadeo); 2) el servidor, si responde, manda
        sel = validar(leerLocal());
        aplicar();
        cargarVoces();
        fetch('/api/apariencia')
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => {
                if (d && (d.forma || d.fondo)) {
                    sel = validar(d); aplicar();
                    try { localStorage.setItem(CLAVE, JSON.stringify(sel)); } catch (e) { /* ya avisado en guardar() */ }
                }
            })
            .catch((e) => console.warn('[apariencia] no pude leer /api/apariencia (se usa localStorage):', e));
    }

    if (document.body) iniciar(); else document.addEventListener('DOMContentLoaded', iniciar);

    window.SaraApariencia = { get: () => ({ ...sel }), fondos: () => FONDOS.slice(),
        poner(forma, fondo) { sel = validar({ forma: forma ?? sel.forma, fondo: fondo ?? sel.fondo }); aplicar(); guardar(); } };
})();
