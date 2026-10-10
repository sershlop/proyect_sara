/**
 * apariencia.js — Menú de personalización visual de SARA (NUEVO)
 *
 * Línea blanca en el borde izquierdo (centro vertical). Al pulsarla se despliega un panel con dos secciones:
 *   1) Aureola / logo central → miniaturas de las formas (SaraAureola.formas / previa)
 *   2) Fondo / interfaz       → miniaturas de los temas (clase tema-<id> en <body>, definidos en style.css)
 * Los colores de estado de la aureola (azul, naranja, verde, rojo, violeta de voz y lector) NO cambian con ningún tema.
 *
 * Persistencia: localStorage + archivo apariencia_gui.json vía GET/POST /api/apariencia (server.py).
 * Si el servidor no tiene esas rutas, sigue funcionando solo con localStorage (y deja un aviso en consola).
 * 3) Voz de SARA        → lista de voces (GET/POST /api/voz); al elegir una suena una muestra. Se oculta si el servidor no la ofrece.
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
    ];
    const POR_DEFECTO = { forma: 'gota', fondo: 'clasico' };
    let sel = { ...POR_DEFECTO };

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
        '<div class="apar-seccion apar-voz-seccion hidden">Voz de SARA</div><div class="apar-voces hidden" id="apar-voces"></div>';

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

    function cargarVoces() {
        fetch('/api/voz')
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => {
                if (!d || !Array.isArray(d.voces) || !d.voces.length) return;     // sin voz.py/servidor nuevo: sección oculta
                const lista = panel.querySelector('#apar-voces');
                lista.textContent = '';
                d.voces.forEach((v) => {
                    const b = document.createElement('button');
                    b.type = 'button'; b.className = 'apar-voz'; b.dataset.valor = v.id; b.setAttribute('aria-pressed', 'false');
                    b.innerHTML = `<span class="apar-voz-ico">${ICONO_VOZ[v.genero] || '🔊'}</span>` +
                        `<span class="apar-voz-txt"><b>${esc(v.nombre)}</b><small>${esc(v.region)} · ${esc(v.estilo)}</small></span>`;
                    lista.appendChild(b);
                });
                vozActual = d.actual || '';
                panel.querySelector('.apar-voz-seccion').classList.remove('hidden');
                lista.classList.remove('hidden');
                marcarVoz();
            })
            .catch((e) => console.warn('[apariencia] no pude leer /api/voz:', e));
    }

    function elegirVoz(id) {
        const previa = vozActual;
        vozActual = id; marcarVoz();
        fetch('/api/voz', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ voz: id, probar: true }) })
            .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); })
            .catch((e) => { console.warn('[apariencia] no pude cambiar la voz:', e); vozActual = previa; marcarVoz(); });
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
