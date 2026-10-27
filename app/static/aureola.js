/**
 * aureola.js — Aureola de SARA (v2: múltiples formas)
 *
 * Formas disponibles (se cambian con SaraAureola.setForma(id), lo usa apariencia.js):
 *   'gota'      (la original: círculo que, tras un retraso, se deforma en gota y gira al procesar)
 *   'engranaje' (12 dientes; gira al procesar, estático en listo / éxito / error)
 *   'hexagono'  (hexágono redondeado; gira al procesar)
 *   'jarvis'    (anillos segmentados contra-rotatorios + núcleo; deriva lenta en reposo, rápida al procesar)
 *   'radar'     (anillos de alcance + barrido con estela y ecos)
 *   'reactor'   (núcleo de energía inestable: anillos concéntricos rápidos, núcleo que parpadea y partículas en órbita)
 *   'ondas'     (visualizador radial de frecuencia: reacciona al micrófono REAL cuando el modo voz está activo, y a la voz de
 *                SARA / lector con una envolvente simulada — la voz se reproduce en el servidor y el navegador no ve su audio)
 *
 * TODAS las formas dibujan con currentColor, así que los colores por estado siguen siendo los de style.css:
 *   azul (listo) · naranja (procesando) · verde (éxito) · rojo (error) · violeta (hablando) · violeta intenso (lector).
 *
 * API:  SaraAureola.setEstado('listo' | 'procesando' | 'exito' | 'error')
 *       SaraAureola.setHablando(bool)  — voz normal de SARA (violeta suave, ondulación rápida y sutil)
 *       SaraAureola.setLector(bool)    — lector de texto (violeta intenso, ondas LARGAS y lentas + ondas expansivas)
 *       SaraAureola.setForma(id) / getForma() / formas() / previa(id)   — NUEVO (personalización)
 * Prueba en consola:  SaraAureola.setEstado('procesando');  SaraAureola.setForma('jarvis')
 *
 * AHORRO DE RECURSOS (v3):
 *   · En reposo (listo, sin voz, sin lector) dibuja a ~20 cuadros/s en vez de 60.
 *   · Si además no hay nada que mover (engranaje / hexágono, animaciones apagadas con /apagar animacion,
 *     o «reducir movimiento» del sistema) el bucle se DETIENE por completo: 0 % de CPU hasta que algo lo despierte
 *     (cambio de estado, voz, lector, micrófono, cambio de forma o encender las animaciones).
 *   · Se pausa con la ventana oculta o minimizada y no reescribe atributos SVG que no cambiaron.
 *   · SaraAureola.bucle() → 'activo' | 'detenido' (para comprobarlo en consola).
 *   · window.SaraAnimacion = interruptor global de animaciones ambientales (lo usan aureola_show.js, apariencia.js y app.js).
 */

/**
 * SaraAnimacion — interruptor global de las animaciones AMBIENTALES (las de reposo).
 *   /apagar animacion  → sin ester egg; aureola, lluvia «Matriz» y fondo «Aurora» quedan quietos mientras SARA no hace nada.
 *   /encender animacion → todo como antes.      /animacion → muestra el estado.
 * NO toca los movimientos que informan del estado: procesando, éxito, error, voz, lector y el micrófono (ondas).
 * Se guarda en localStorage ('sara_animacion'); marca <html class="sin-animacion"> para el CSS.
 * Se define aquí (primer script que carga) para que lo vean aureola_show.js, apariencia.js y app.js.
 */
window.SaraAnimacion = (() => {
    'use strict';
    const CLAVE = 'sara_animacion';
    const oyentes = [];
    const raiz = document.documentElement;
    let activa = true;
    try { activa = localStorage.getItem(CLAVE) !== 'off'; }
    catch (e) { console.warn('[animacion] no pude leer localStorage:', e); }
    raiz.classList.toggle('sin-animacion', !activa);

    // Guarda en apariencia_gui.json (vía server.py). Sirve si localStorage no persiste en la ventana de la app.
    function enviar() {
        fetch('/api/apariencia', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                   body: JSON.stringify({ animacion: activa ? 'on' : 'off' }) })
            .then((r) => { if (!r.ok) console.warn('[animacion] el servidor no guardó la preferencia (HTTP ' + r.status + ')'); })
            .catch((e) => console.warn('[animacion] sin conexión con /api/apariencia:', e));
    }

    function poner(valor, remoto = true) {
        valor = !!valor;
        if (valor === activa) return false;
        activa = valor;
        raiz.classList.toggle('sin-animacion', !activa);
        try { localStorage.setItem(CLAVE, activa ? 'on' : 'off'); }
        catch (e) { console.warn('[animacion] no pude guardar la preferencia:', e); }
        if (remoto) enviar();
        oyentes.forEach((f) => { try { f(activa); } catch (e) { console.error('[animacion] un oyente falló:', e); } });
        return true;
    }

    const norm = (txt) => String(txt || '').toLowerCase().normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '').replace(/[_\s]+/g, ' ').trim();
    const OFF = ['apagar', 'desactivar', 'quitar', 'detener', 'off'];
    const ON = ['encender', 'prender', 'activar', 'poner', 'on'];
    const esAnim = (p) => p === 'animacion' || p === 'animaciones';

    // Devuelve el texto de respuesta si «texto» era uno de estos comandos; null si no (entonces va al servidor).
    function comando(texto) {
        const t = norm(texto);
        if (t[0] !== '/') return null;
        const p = t.slice(1).split(' ');
        let accion = null;
        if (p.length === 2 && esAnim(p[1]) && (OFF.includes(p[0]) || ON.includes(p[0]))) {
            accion = OFF.includes(p[0]) ? 'off' : 'on';
        } else if (esAnim(p[0]) && p.length <= 2) {
            if (p.length === 1 || p[1] === 'estado') accion = 'estado';
            else if (OFF.includes(p[1])) accion = 'off';
            else if (ON.includes(p[1])) accion = 'on';
        }
        if (!accion) return null;
        if (accion === 'off') {
            return poner(false)
                ? 'Animaciones en reposo apagadas: sin ester egg y con la aureola y los fondos quietos mientras no hago nada. ' +
                  'Los colores y movimientos de estado (procesando, voz, lector) siguen igual. Para volver: /encender animacion'
                : 'Las animaciones ya estaban apagadas. Para volver: /encender animacion';
        }
        if (accion === 'on') {
            return poner(true) ? 'Animaciones encendidas.' : 'Las animaciones ya estaban encendidas.';
        }
        return activa
            ? 'Animaciones: encendidas. Para apagarlas: /apagar animacion'
            : 'Animaciones: apagadas. Para encenderlas: /encender animacion';
    }

    // Al arrancar manda lo guardado en el servidor (igual que la forma y el fondo); si el servidor aún no tiene el dato
    // y localmente está apagado, se lo envía una vez.
    fetch('/api/apariencia')
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
            if (d && (d.animacion === 'on' || d.animacion === 'off')) poner(d.animacion === 'on', false);
            else if (d && !activa) enviar();
        })
        .catch((e) => console.warn('[animacion] no pude leer /api/apariencia (se usa localStorage):', e));

    return { activa: () => activa, poner, comando, alCambiar: (f) => { if (typeof f === 'function') oyentes.push(f); } };
})();

window.SaraAureola = (() => {
    const RETRASO_MS = 2000;     // espera antes de deformarse (evita parpadeos en tareas rápidas)
    const R = 70;                // radio base (viewBox 200x200)
    const N = 72;                // puntos por contorno
    const ESCALA_ERROR = 1.065;  // ≈ +1 mm de radio mientras dura el error
    const reducir = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const NS = 'http://www.w3.org/2000/svg';

    const FORMAS = [
        { id: 'gota', nombre: 'Gota' },
        { id: 'engranaje', nombre: 'Engranaje' },
        { id: 'hexagono', nombre: 'Hexágono' },
        { id: 'jarvis', nombre: 'Jarvis' },
        { id: 'radar', nombre: 'Radar' },
        { id: 'reactor', nombre: 'Reactor' },
        { id: 'ondas', nombre: 'Ondas' },
    ];
    const ANCHO_BORDE = { gota: 3, engranaje: 2.6, hexagono: 3, jarvis: 1.2, radar: 1.6, reactor: 1.2, ondas: 1.5 };
    const USA_ROT_GRUPO = { gota: true, engranaje: true, hexagono: true, jarvis: false, radar: false, reactor: false, ondas: false };

    const cont = document.getElementById('aureola');
    const grupo = document.getElementById('aureola-rot');
    const borde = document.getElementById('aureola-borde');
    const fondo = document.getElementById('aureola-fondo');
    if (!cont || !grupo || !borde || !fondo) {
        return { setEstado() {}, setHablando() {}, setLector() {}, setForma() {}, getForma: () => 'gota',
                 formas: () => [], previa: () => '', geometria: () => null, contorno: () => '', reloj: () => 0,
                 despertar() {}, bucle: () => 'detenido' };
    }
    const ondas = ['aureola-onda-a', 'aureola-onda-b'].map((id) => document.getElementById(id)).filter(Boolean);

    // Capa extra (anillos de Jarvis, barrido del radar): vive dentro del grupo para heredar escala / latido
    const extra = document.createElementNS(NS, 'g');
    extra.setAttribute('id', 'aureola-extra');
    grupo.appendChild(extra);

    let estado = '';
    let timer = null;
    let objetivo = 0;   // 0 = círculo, 1 = charco
    let amp = 0;        // deformación actual (suavizada)
    let vel = 0;        // velocidad de giro actual (°/s, suavizada)
    let rot = 0, t = 0, pulso = 0;
    let escala = 1, escalaObj = 1;
    let hablando = false, habl = 0;   // habl = ondulación extra mientras SARA habla
    let lector = false, lec = 0;      // lec = intensidad (0..1) del modo lector
    let tL = 0, tO = 0;               // relojes propios del lector (solo avanzan mientras lee)
    let giro = 0;                     // reloj de giro de las formas Jarvis / Radar / Reactor
    let tr = 0;                       // reloj real (s) para parpadeos rápidos del reactor y las ondas
    let mic = 0;                      // nivel del micrófono (0..1, suavizado) leído del medidor del modo voz
    const medidorStt = document.getElementById('stt-nivel');
    let forma = 'gota';
    let ref = {};                     // referencias a los elementos de la capa extra
    let previo = performance.now();

    // ── Ahorro de recursos ─────────────────────────────────────────────────────
    const FPS_REPOSO = 20;                                    // en reposo el movimiento es lento: 20 cuadros/s se ven igual
    const ESTATICAS = { engranaje: true, hexagono: true };    // formas que en reposo no tienen NINGÚN movimiento propio
    const animacionOn = () => window.SaraAnimacion.activa();
    let raf = 0;                                              // 0 = bucle detenido
    let ultimoCuadro = 0;

    // setAttribute solo si el valor cambió (evita invalidar el SVG en cada cuadro)
    function sa(el, nombre, valor) {
        const k = '_' + nombre;
        if (el[k] === valor) return;
        el[k] = valor;
        el.setAttribute(nombre, valor);
    }
    // montar() escribe algunos atributos directamente: se olvida la caché para no saltarse una escritura necesaria
    function olvidar() {
        [borde, fondo, grupo, ...ondas].forEach((el) => {
            ['d', 'fill-opacity', 'transform', 'r', 'stroke-opacity'].forEach((a) => { el['_' + a] = undefined; });
        });
    }
    const nivelMic = () => (medidorStt ? Math.min(1, (parseFloat(medidorStt.style.width) || 0) / 100) : 0);

    // Reposo = SARA lista, sin voz, sin lector y con todas las transiciones ya asentadas
    function enReposo() {
        return estado === 'listo' && !hablando && !lector
            && habl < 0.004 && lec < 0.004 && amp < 0.004 && pulso < 0.004 && Math.abs(vel) < 0.5
            && Math.abs(escala - escalaObj) < 0.0005
            && (forma !== 'ondas' || (mic < 0.01 && nivelMic() < 0.01));
    }

    function despertar() {
        if (raf || document.hidden) return;
        previo = performance.now();
        ultimoCuadro = 0;
        raf = requestAnimationFrame(cuadro);
    }

    // ── Geometría ──────────────────────────────────────────────────────────────
    function contorno(radio, k, fase, sentido, l = 0) {
        const pts = [];
        for (let i = 0; i < N; i++) {
            const a = (i / N) * Math.PI * 2;
            const def = k * (
                0.075 * Math.sin(2 * a + sentido * t * 1.1 + fase) +
                0.060 * Math.sin(3 * a - sentido * t * 1.5 + fase * 1.7) +
                0.035 * Math.sin(5 * a + sentido * t * 2.1 - fase)
            );
            // Lector: 2 y 3 lóbulos grandes que se estiran y relajan despacio (agua sobre una bocina)
            const largas = l * (
                0.15 * Math.sin(2 * a + sentido * tL * 0.9 + fase) * (0.65 + 0.35 * Math.sin(tL * 1.4)) +
                0.09 * Math.sin(3 * a - sentido * tL * 0.7 - fase) * (0.65 + 0.35 * Math.sin(tL * 1.1 + 1.3))
            );
            const r = radio * (1 + def + largas);
            pts.push([r * Math.cos(a), r * Math.sin(a)]);
        }
        // curvas cuadráticas entre puntos medios → borde líquido y suave
        const m = (p, q) => [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
        const m0 = m(pts[N - 1], pts[0]);
        let d = `M${m0[0].toFixed(2)} ${m0[1].toFixed(2)}`;
        for (let i = 0; i < N; i++) {
            const p = pts[i], mm = m(p, pts[(i + 1) % N]);
            d += `Q${p[0].toFixed(2)} ${p[1].toFixed(2)} ${mm[0].toFixed(2)} ${mm[1].toFixed(2)}`;
        }
        return d + 'Z';
    }

    function circ(r) {   // círculo exacto como trazo (dos arcos)
        return `M${-r} 0A${r} ${r} 0 1 0 ${r} 0A${r} ${r} 0 1 0 ${-r} 0Z`;
    }

    function poligono(n, radio, fase) {   // polígono con esquinas redondeadas (quad por puntos medios)
        const v = [];
        for (let i = 0; i < n; i++) {
            const a = fase + (i * Math.PI * 2) / n;
            v.push([radio * Math.cos(a), radio * Math.sin(a)]);
        }
        const m = (p, q) => [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
        const m0 = m(v[n - 1], v[0]);
        let d = `M${m0[0].toFixed(2)} ${m0[1].toFixed(2)}`;
        for (let i = 0; i < n; i++) {
            const p = v[i], mm = m(p, v[(i + 1) % n]);
            d += `Q${p[0].toFixed(2)} ${p[1].toFixed(2)} ${mm[0].toFixed(2)} ${mm[1].toFixed(2)}`;
        }
        return d + 'Z';
    }

    function rutaEngranaje(rr, rt, dientes = 12, pasos = 288, hueco = 0) {
        const s = (x) => x * x * (3 - 2 * x);
        const perfil = (p) => {
            if (p < 0.10) return rr + (rt - rr) * s(p / 0.10);
            if (p < 0.40) return rt;
            if (p < 0.50) return rt - (rt - rr) * s((p - 0.40) / 0.10);
            return rr;
        };
        let d = '';
        for (let i = 0; i < pasos; i++) {
            const a = (i / pasos) * Math.PI * 2;
            const r = perfil(((i / pasos) * dientes) % 1);
            d += `${i ? 'L' : 'M'}${(r * Math.cos(a)).toFixed(2)} ${(r * Math.sin(a)).toFixed(2)}`;
        }
        return d + 'Z' + (hueco ? circ(hueco) : '');
    }

    function sector(r, a0, a1) {   // cuña de círculo (grados; el ángulo crece en sentido horario)
        const p = (a) => [(r * Math.cos((a * Math.PI) / 180)).toFixed(2), (r * Math.sin((a * Math.PI) / 180)).toFixed(2)];
        const [x0, y0] = p(a0), [x1, y1] = p(a1);
        return `M0 0L${x0} ${y0}A${r} ${r} 0 0 1 ${x1} ${y1}Z`;
    }

    function posParticula(i, g, tt) {   // partícula i del reactor: órbita irregular que respira
        const a = ((g * (8 + (i % 3) * 5) * (i % 2 ? 1 : -1)) + i * 36) * Math.PI / 180;
        const r = 30 + ((i * 7) % 34) + 5 * Math.sin(tt * 3 + i * 1.7);
        return [r * Math.cos(a), r * Math.sin(a)];
    }

    const NB = 56;   // barras del visualizador de ondas
    function rutaBarras(L, tt) {   // L = intensidad 0..1; patrón simétrico izquierda/derecha (como un espectro)
        let d = '';
        for (let i = 0; i < NB; i++) {
            const a = (i / NB) * Math.PI * 2 - Math.PI / 2;
            const j = Math.min(i, NB - i) / (NB / 2);
            const esp = 0.3 + 0.7 * Math.abs(Math.sin(j * 9 + tt * 6.1) * Math.cos(j * 5.3 - tt * 4.3));
            const r2 = 36 + 3 + 33 * L * esp;
            const c = Math.cos(a), s = Math.sin(a);
            d += `M${(36 * c).toFixed(1)} ${(36 * s).toFixed(1)}L${(r2 * c).toFixed(1)} ${(r2 * s).toFixed(1)}`;
        }
        return d;
    }

    // Marcado de la capa extra (compartido por la aureola real y por las miniaturas del menú)
    function marcado(f, estatico) {
        const C = 'currentColor';
        if (f === 'jarvis') {
            return `<circle data-k="ticks" r="80" fill="none" stroke="${C}" stroke-width="5" stroke-opacity=".5" stroke-dasharray="1.4 5.58"/>` +
                `<circle data-k="seg" r="60" fill="none" stroke="${C}" stroke-width="4" stroke-opacity=".9" stroke-dasharray="37.1 10"/>` +
                `<circle data-k="arcs" r="50" fill="none" stroke="${C}" stroke-width="2.5" stroke-opacity=".8" stroke-dasharray="70 34.7"/>` +
                `<circle r="38" fill="none" stroke="${C}" stroke-width="1" stroke-opacity=".45"/>` +
                `<circle data-k="nucleo" r="9" fill="${C}" fill-opacity=".9"/>`;
        }
        if (f === 'radar') {
            let cunas = '';
            for (let i = 0; i < 6; i++) cunas += `<path d="${sector(70, -(i + 1) * 9, -i * 9)}" fill="${C}" fill-opacity="${(0.26 * (1 - i / 6)).toFixed(3)}"/>`;
            const ecos = [[42, 25], [58, 150], [26, 255]].map(([r, a], i) =>
                `<circle data-k="eco${i}" cx="${(r * Math.cos((a * Math.PI) / 180)).toFixed(1)}" cy="${(r * Math.sin((a * Math.PI) / 180)).toFixed(1)}" r="3.2" fill="${C}" fill-opacity="${estatico ? 0.8 : 0.1}"/>`).join('');
            return `<circle r="52" fill="none" stroke="${C}" stroke-width="1" stroke-opacity=".35"/>` +
                `<circle r="34" fill="none" stroke="${C}" stroke-width="1" stroke-opacity=".3"/>` +
                `<circle r="16" fill="none" stroke="${C}" stroke-width="1" stroke-opacity=".3"/>` +
                `<path d="M-70 0H70M0-70V70" stroke="${C}" stroke-width="1" stroke-opacity=".25" fill="none"/>` +
                `<g data-k="barrido"${estatico ? ' transform="rotate(40)"' : ''}>${cunas}<line x1="0" y1="0" x2="70" y2="0" stroke="${C}" stroke-width="2" stroke-opacity=".95"/></g>` + ecos;
        }
        if (f === 'reactor') {
            let part = '';
            for (let i = 0; i < 10; i++) {
                const [x, y] = posParticula(i, 0, 0);
                part += `<circle data-k="p${i}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${(1.6 + (i % 3) * 0.5).toFixed(1)}" fill="${C}" fill-opacity="${estatico ? 0.85 : 0.6}"/>`;
            }
            return `<circle data-k="r1" r="76" fill="none" stroke="${C}" stroke-width="2.2" stroke-opacity=".75" stroke-dasharray="22.4 5.7"/>` +
                `<circle data-k="r2" r="62" fill="none" stroke="${C}" stroke-width="3" stroke-opacity=".85" stroke-dasharray="24 8.47"/>` +
                `<circle data-k="r3" r="48" fill="none" stroke="${C}" stroke-width="2" stroke-opacity=".9" stroke-dasharray="28 9.7"/>` +
                `<circle data-k="halo" r="26" fill="none" stroke="${C}" stroke-width="1.5" stroke-opacity=".6"/>` +
                `<circle data-k="nucleo" r="16" fill="${C}" fill-opacity=".95"/>` + part;
        }
        if (f === 'ondas') {
            return `<path data-k="barras" d="${estatico ? rutaBarras(0.7, 1.3) : ''}" fill="none" stroke="${C}" stroke-width="3.2" stroke-linecap="round" stroke-opacity=".9"/>`;
        }
        return '';
    }

    // Dibujo estático de cada forma (miniaturas del menú). Devuelve el interior de un <svg viewBox="-100 -100 200 200">.
    function previa(f) {
        const C = 'currentColor';
        if (f === 'engranaje') {
            return `<path d="${rutaEngranaje(0.80 * R, R, 12, 288, 0.30 * R)}" fill="${C}" fill-opacity=".14" fill-rule="evenodd" stroke="${C}" stroke-width="2.6" stroke-linejoin="round"/>` +
                   `<path d="${circ(0.62 * R)}${circ(0.54 * R)}" fill="${C}" fill-opacity=".30" fill-rule="evenodd"/>`;
        }
        if (f === 'hexagono') {
            return `<path d="${poligono(6, R * 1.05, -Math.PI / 2)}" fill="${C}" fill-opacity=".10" stroke="${C}" stroke-width="3" stroke-linejoin="round"/>` +
                   `<path d="${poligono(6, R * 0.62 * 1.05, -Math.PI / 2 + Math.PI / 6)}" fill="${C}" fill-opacity=".22"/>`;
        }
        if (f === 'jarvis') {
            return `<path d="${circ(R)}" fill="${C}" fill-opacity=".03" stroke="${C}" stroke-width="1.2"/><path d="${circ(26)}" fill="${C}" fill-opacity=".14"/>` + marcado('jarvis', true);
        }
        if (f === 'radar') {
            return `<path d="${circ(R)}" fill="${C}" fill-opacity=".05" stroke="${C}" stroke-width="1.6"/><path d="${circ(3.5)}" fill="${C}"/>` + marcado('radar', true);
        }
        if (f === 'reactor') {
            return `<path d="${circ(R)}" fill="${C}" fill-opacity=".04" stroke="${C}" stroke-width="1.2"/><path d="${circ(40)}" fill="${C}" fill-opacity=".14"/>` + marcado('reactor', true);
        }
        if (f === 'ondas') {
            return `<path d="${circ(30)}" fill="${C}" fill-opacity=".06" stroke="${C}" stroke-width="1.5"/><path d="${circ(14)}" fill="${C}" fill-opacity=".25"/>` + marcado('ondas', true);
        }
        return `<path d="${contorno(R, 0.55, 0, 1)}" fill="${C}" fill-opacity=".14" stroke="${C}" stroke-width="3" stroke-linejoin="round"/>` +
               `<path d="${contorno(R * 0.80, 0.69, 2.1, -1)}" fill="${C}" fill-opacity=".22"/>`;
    }

    // Monta una forma: ajusta trazos, contornos fijos y la capa extra
    function montar(f) {
        extra.textContent = '';
        extra.innerHTML = marcado(f, false);
        ref = {};
        olvidar();
        extra.querySelectorAll('[data-k]').forEach((e) => { ref[e.getAttribute('data-k')] = e; });
        borde.setAttribute('stroke-width', ANCHO_BORDE[f]);
        if (f === 'engranaje') { borde.setAttribute('fill-rule', 'evenodd'); fondo.setAttribute('fill-rule', 'evenodd'); }
        else { borde.removeAttribute('fill-rule'); fondo.removeAttribute('fill-rule'); }
        // contornos que no cambian en cada cuadro
        if (f === 'engranaje') {
            borde.setAttribute('d', rutaEngranaje(0.80 * R, R, 12, 288, 0.30 * R));
            fondo.setAttribute('d', circ(0.62 * R) + circ(0.54 * R));
        } else if (f === 'hexagono') {
            borde.setAttribute('d', poligono(6, R * 1.05, -Math.PI / 2));
            fondo.setAttribute('d', poligono(6, R * 0.62 * 1.05, -Math.PI / 2 + Math.PI / 6));
        } else if (f === 'jarvis') {
            borde.setAttribute('d', circ(R));
            fondo.setAttribute('d', circ(26));
        } else if (f === 'radar') {
            borde.setAttribute('d', circ(R));
            fondo.setAttribute('d', circ(3.5));
            fondo.setAttribute('fill-opacity', '0.9');
        } else if (f === 'reactor') {
            borde.setAttribute('d', circ(R));
            fondo.setAttribute('d', circ(40));
        } else if (f === 'ondas') {
            borde.setAttribute('d', circ(30));
            fondo.setAttribute('d', circ(14));
        }
    }

    function cuadro(ahora) {
        raf = 0;
        const reposo = enReposo();
        // Reposo: ~20 cuadros/s. Si además no hay nada que mover (forma estática, animaciones apagadas o «reducir
        // movimiento»), este cuadro es el último: el bucle se detiene hasta que despertar() lo reactive.
        const quieta = reposo && (ESTATICAS[forma] || reducir || !animacionOn());
        if (reposo && !quieta && ahora - ultimoCuadro < 1000 / FPS_REPOSO - 2) {
            raf = requestAnimationFrame(cuadro);
            return;
        }
        ultimoCuadro = ahora;
        const dt = Math.max(0, Math.min((ahora - previo) / 1000, 0.1));
        previo = ahora;

        amp += (objetivo - amp) * (1 - Math.exp(-dt / 0.55));         // morph suave
        vel += (objetivo * 140 - vel) * (1 - Math.exp(-dt / 0.8));    // acelera / frena
        pulso *= Math.exp(-dt / 0.35);
        escala += (escalaObj - escala) * (1 - Math.exp(-dt / 0.22));   // crece / vuelve suave
        // Voz: modulación equilibrada y profesional (la aureola "late" al ritmo del habla, sin girar)
        const hObj = hablando && !reducir
            ? 0.35 + 0.15 * Math.sin(ahora / 1000 * 6.5) + 0.08 * Math.sin(ahora / 1000 * 10.2) : 0;
        habl += (hObj - habl) * (1 - Math.exp(-dt / (hablando ? 0.09 : 0.35)));
        // Lector: entra/sale con suavidad; el reloj de las ondas largas solo corre mientras lee (acelerado para seguir el ritmo de la voz)
        lec += ((lector && !reducir ? 1 : 0) - lec) * (1 - Math.exp(-dt / 0.7));
        tL += dt * lec * 1.8;
        tO += dt * lec * 1.8;
        const A = amp + habl;
        t += dt * (0.7 + A * 1.6);
        rot += vel * dt;
        // Jarvis / Radar: deriva lenta en reposo; con la tarea en curso (vel sube tras el retraso) giran rápido
        tr += dt;
        giro += reducir ? 0 : dt * (1.5 + 10 * Math.min(vel / 140, 1));

        const respiro = 0.014 * Math.sin(t * 1.3);                    // respiración en reposo

        if (forma === 'gota') {
            sa(borde, 'd', contorno(R * (1 + respiro), A, 0, 1, lec));
            sa(fondo, 'd', contorno(R * 0.80 * (1 + respiro), A * 1.25, 2.1, -1, lec * 1.25));
            sa(borde, 'fill-opacity', (0.06 + A * 0.14 + lec * 0.10).toFixed(3));
            sa(fondo, 'fill-opacity', (0.05 + A * 0.20 + lec * 0.14).toFixed(3));
        } else if (forma === 'engranaje') {
            sa(borde, 'fill-opacity', (0.10 + habl * 0.12 + lec * 0.10).toFixed(3));
            sa(fondo, 'fill-opacity', (0.16 + habl * 0.22 + lec * 0.14).toFixed(3));
        } else if (forma === 'hexagono') {
            sa(borde, 'fill-opacity', (0.07 + habl * 0.12 + lec * 0.10).toFixed(3));
            sa(fondo, 'fill-opacity', (0.12 + habl * 0.22 + lec * 0.14).toFixed(3));
        } else if (forma === 'jarvis') {
            sa(borde, 'fill-opacity', (0.03 + habl * 0.05 + lec * 0.05).toFixed(3));
            sa(fondo, 'fill-opacity', (0.10 + habl * 0.28 + lec * 0.16).toFixed(3));
            if (ref.ticks) sa(ref.ticks, 'transform', `rotate(${(giro * -2).toFixed(1)})`);
            if (ref.seg) sa(ref.seg, 'transform', `rotate(${(giro * 6).toFixed(1)})`);
            if (ref.arcs) sa(ref.arcs, 'transform', `rotate(${(giro * -10).toFixed(1)})`);
            if (ref.nucleo) sa(ref.nucleo, 'r', (9 * (1 + 0.12 * Math.sin(t * 2.4) + habl * 0.35 + lec * 0.25 * Math.sin(tL * 1.6))).toFixed(2));
        } else if (forma === 'radar') {
            sa(borde, 'fill-opacity', (0.05 + habl * 0.08 + lec * 0.08).toFixed(3));
            const ang = giro * 30;
            if (ref.barrido) sa(ref.barrido, 'transform', `rotate(${(ang % 360).toFixed(1)})`);
            [25, 150, 255].forEach((a, i) => {
                const e = ref['eco' + i];
                if (!e) return;
                const delta = (((ang - a) % 360) + 360) % 360;             // cuánto hace que el barrido pasó por el eco
                sa(e, 'fill-opacity', (delta < 150 ? Math.max(0.1, 1 - delta / 150) : 0.1).toFixed(3));
            });
        } else if (forma === 'reactor') {
            const flick = 0.5 + 0.3 * Math.sin(tr * 9) + 0.2 * Math.sin(tr * 23.7);     // parpadeo inestable del núcleo
            sa(borde, 'fill-opacity', (0.04 + habl * 0.06 + lec * 0.06).toFixed(3));
            sa(fondo, 'fill-opacity', (0.07 + 0.09 * flick + habl * 0.20 + lec * 0.12).toFixed(3));
            [['r1', 14, 76], ['r2', -22, 62], ['r3', 34, 48]].forEach(([k, m, r], i) => {
                if (!ref[k]) return;
                sa(ref[k], 'transform', `rotate(${(giro * m).toFixed(1)})`);
                sa(ref[k], 'r', (r * (1 + 0.025 * Math.sin(tr * 5 + i * 2) + 0.03 * habl)).toFixed(2));
            });
            if (ref.nucleo) sa(ref.nucleo, 'r', (16 * (0.9 + 0.24 * (flick - 0.5) + 0.12 + habl * 0.30)).toFixed(2));
            if (ref.nucleo) sa(ref.nucleo, 'fill-opacity', (0.7 + 0.3 * flick).toFixed(3));
            if (ref.halo) sa(ref.halo, 'r', (26 + 3 * Math.sin(tr * 6.5) + habl * 8).toFixed(2));
            for (let i = 0; i < 10; i++) {
                const e = ref['p' + i];
                if (!e) continue;
                const [x, y] = posParticula(i, giro, tr);
                sa(e, 'cx', x.toFixed(1)); sa(e, 'cy', y.toFixed(1));
                sa(e, 'fill-opacity', (0.45 + 0.5 * Math.abs(Math.sin(tr * 2.3 + i))).toFixed(2));
            }
        } else if (forma === 'ondas') {
            // Micrófono real (medidor del modo voz, 0..100 %) o envolvente simulada de la voz de SARA / lector
            mic += (nivelMic() - mic) * (1 - Math.exp(-dt / 0.07));
            const L = Math.max(Math.min(1, habl * 1.5), lec * 0.8, mic * 1.1, 0.07 + 0.03 * Math.sin(t * 1.3));
            sa(borde, 'fill-opacity', (0.05 + L * 0.08).toFixed(3));
            sa(fondo, 'fill-opacity', (0.12 + L * 0.35).toFixed(3));
            if (ref.barras) sa(ref.barras, 'd', rutaBarras(L, tr));
        }

        // cono de bocina: latido lento de todo el círculo (+4.5 %)
        const latido = 0.045 * lec * Math.sin(tL * 1.6);
        const vozEsc = (forma === 'gota' || forma === 'ondas') ? 0 : 0.12 * habl;            // en las demás formas la voz hace "latir" la escala
        sa(grupo, 'transform',
            `rotate(${(USA_ROT_GRUPO[forma] ? rot : 0).toFixed(2)}) scale(${(escala + 0.07 * pulso + latido + vozEsc).toFixed(4)})`);
        // ondas expansivas: dos anillos que nacen en el borde, crecen y se desvanecen (desfasados)
        ondas.forEach((el, i) => {
            const p = ((tO / 3.2) + i * 0.5) % 1;
            sa(el, 'r', (R * (1.02 + 0.85 * p)).toFixed(1));
            sa(el, 'stroke-opacity', (lec * 0.55 * Math.pow(1 - p, 1.6)).toFixed(3));
        });

        if (!quieta) raf = requestAnimationFrame(cuadro);
    }

    function setEstado(nuevo) {
        if (nuevo === estado) return;          // idempotente: no reinicia el retraso
        estado = nuevo;
        clearTimeout(timer);
        objetivo = 0;
        escalaObj = nuevo === 'error' ? ESCALA_ERROR : 1;
        if (nuevo === 'procesando' && !reducir) {
            timer = setTimeout(() => { if (estado === 'procesando') objetivo = 1; }, RETRASO_MS);
        } else if (nuevo === 'exito') {
            pulso = 1;
        }
        despertar();
    }

    function setForma(id) {
        const f = FORMAS.some((x) => x.id === id) ? id : 'gota';
        if (f === forma && cont.dataset.forma === f) return;
        forma = f;
        cont.dataset.forma = f;               // data-* sobrevive a los className que pone app.js
        montar(f);
        despertar();
    }

    function setHablando(v) { hablando = !!v; if (hablando) despertar(); }
    function setLector(v) { lector = !!v; if (lector) despertar(); }

    setEstado((cont.className.match(/aureola-(\w+)/) || [, 'listo'])[1]);
    despertar();

    // Qué despierta el bucle cuando está detenido: volver a ver la ventana, encender las animaciones y,
    // en la forma «ondas», cualquier movimiento del medidor del micrófono (el modo voz lo escribe en su style).
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) { cancelAnimationFrame(raf); raf = 0; } else despertar();
    });
    window.SaraAnimacion.alCambiar(despertar);
    if (medidorStt && window.MutationObserver) {
        new MutationObserver(() => { if (forma === 'ondas') despertar(); })
            .observe(medidorStt, { attributes: true, attributeFilter: ['style'] });
    }

    // ── API para el ester egg (aureola_show.js) ──
    // (el show siempre dibuja gotas con contorno(); no depende de la forma elegida)
    function geometria() {
        const r = cont.getBoundingClientRect();
        return { cx: r.left + r.width / 2, cy: r.top + r.height / 2, k: r.width / 200, R };   // k = px por unidad del viewBox
    }
    function reloj() { return t; }

    return { setEstado, setHablando, setLector, setForma, getForma: () => forma, formas: () => FORMAS.slice(),
             previa, geometria, contorno, reloj, despertar, bucle: () => (raf ? 'activo' : 'detenido') };
})();
