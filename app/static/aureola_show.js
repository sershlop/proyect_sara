/**
 * aureola_show.js — ESTER EGG de SARA: «la supernova de la aureola».
 *
 * Tras 60 s de inactividad con SARA en reposo (azul), las aureolas de todos los estados
 * aparecen por la pantalla, giran, se vuelven líneas de energía, convergen en la aureola
 * azul del centro, ésta vibra y estalla como un fuego artificial, y las chispas vuelven a
 * fundirse hasta dejar la aureola azul sólida. Cualquier interacción lo cancela con un
 * cierre rápido.
 *
 * API:  SaraShow.contexto(estado, voz, lector) — lo llama app.js desde actualizar_aureola
 *       SaraShow.actividad()                   — lo llama app.js con cada evento real del servidor
 *       SaraShow.iniciar() / SaraShow.detener() — prueba manual desde la consola
 * Depende de: SaraAureola.geometria / contorno / reloj (aureola.js) y #show-canvas (index.html).
 */
window.SaraShow = (() => {
    'use strict';

    // ── AJUSTES ────────────────────────────────────────────────────────────────
    const CFG = {
        ESPERA_MS: 60000,      // inactividad necesaria para disparar el show
        MODO: 'global',        // 'global' = cualquier inactividad | 'sobre_aureola' = solo cursor quieto SOBRE la aureola
        VELOCIDAD: 1,          // 1 = normal (2 = el doble de rápido; útil para probar)
        CUERPOS: 12,           // aureolas completas que aparecen por la pantalla
        HILOS: 56,             // líneas de energía extra (círculos concéntricos)
        CHISPAS: 110,          // rayos del fuego artificial
    };
    // Duración de cada fase, en segundos (el show completo dura ≈ 32 s)
    const FASES = { aparicion: 4.5, giro: 4.5, lineas: 5.5, convergencia: 5.5, vibracion: 2.5, explosion: 3.5, reagrupar: 4.5, cierre: 1.4 };
    const T0 = {};
    let TOTAL = 0;
    for (const k of Object.keys(FASES)) { T0[k] = TOTAL; TOTAL += FASES[k]; }

    // ── ELEMENTOS ──────────────────────────────────────────────────────────────
    const A = window.SaraAureola;
    const canvas = document.getElementById('show-canvas');
    const cont = document.querySelector('.aureola-container');
    const modal = document.getElementById('modal-confirmacion');
    if (!A || !A.geometria || !canvas || !cont || !canvas.getContext) {
        return { contexto() {}, actividad() {}, iniciar() {}, detener() {} };   // sin lienzo: no hace nada
    }
    const ctx = canvas.getContext('2d');
    const mqReducir = window.matchMedia('(prefers-reduced-motion: reduce)');

    // ── UTILIDADES ─────────────────────────────────────────────────────────────
    const TAU = Math.PI * 2;
    const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
    const lerp = (a, b, u) => a + (b - a) * u;
    const suave = (v) => { v = clamp(v); return v * v * (3 - 2 * v); };
    const eio = (u) => (u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2);
    const rnd = (a, b) => a + Math.random() * (b - a);
    const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${clamp(a).toFixed(3)})`;
    const mezcla = (c1, c2, u) => [lerp(c1[0], c2[0], u), lerp(c1[1], c2[1], u), lerp(c1[2], c2[2], u)];
    const BLANCO = [255, 255, 255];

    const _norm = document.createElement('canvas').getContext('2d');
    function leerColor(variable, respaldo) {
        const v = getComputedStyle(document.documentElement).getPropertyValue(variable).trim() || respaldo;
        _norm.fillStyle = '#000';
        _norm.fillStyle = v;                       // el navegador normaliza cualquier formato CSS a #rrggbb
        const s = String(_norm.fillStyle);
        const n = s[0] === '#' ? parseInt(s.slice(1), 16) : parseInt(respaldo.slice(1), 16);
        return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }

    // ── ESTADO ─────────────────────────────────────────────────────────────────
    let ctxEstado = { estado: 'procesando', voz: false, lector: false };
    let ultima = performance.now();          // última actividad
    let mx = -9999, my = -9999, dentro = false;
    let activo = false, cerrando = false, cierreOK = false;
    let raf = 0, previo = 0, tOculta = 0, tAbort = 0;
    let T = 0, Th = 0;                       // T = reloj del show (s); Th = ángulo de giro acumulado
    let W = 0, H = 0, dpr = 1, g = null, cx = 0, cy = 0, Rpx = 0, ay0 = 1;
    let AZUL, PAL, hilos = [], chispas = [], explotado = false, regrupado = false;

    // ── DETECTOR DE INACTIVIDAD ────────────────────────────────────────────────
    function puedeArmarse() {
        return ctxEstado.estado === 'listo' && !ctxEstado.voz && !ctxEstado.lector
            && !document.hidden && !mqReducir.matches;
    }

    function enArea(x, y) {
        const q = A.geometria();
        return !!q && Math.hypot(x - q.cx, y - q.cy) <= q.k * 100;   // caja completa de la aureola
    }

    function actividad() {
        ultima = performance.now();
        if (activo) abortar();
    }

    function alMover(e) {
        if (Math.hypot(e.clientX - mx, e.clientY - my) < 4) return;   // ruido del sensor / mousemove sintético
        mx = e.clientX; my = e.clientY;
        dentro = enArea(mx, my);
        actividad();
    }

    function contexto(estado, voz, lector) {
        ctxEstado = { estado, voz: !!voz, lector: !!lector };
        actividad();
    }

    function revisar() {
        if (activo) return;
        if (modal && !modal.classList.contains('hidden')) { ultima = performance.now(); return; }   // confirmación abierta
        if (CFG.MODO === 'sobre_aureola' && !dentro) return;
        if (performance.now() - ultima >= CFG.ESPERA_MS && puedeArmarse()) iniciar();
    }

    // ── CICLO DE VIDA ──────────────────────────────────────────────────────────
    function iniciar() {
        if (activo || document.hidden || mqReducir.matches) return;
        g = A.geometria();
        if (!g) return;
        activo = true; cerrando = false; cierreOK = false;
        T = 0; Th = 0; explotado = false; regrupado = false;
        construir();
        canvas.classList.add('activo');                                        // el lienzo aparece (0.4 s)
        tOculta = setTimeout(() => cont.classList.add('show-oculta'), 450);    // luego se oculta la aureola real
        previo = performance.now();
        raf = requestAnimationFrame(cuadro);
    }

    function abortar() {             // cierre rápido: el lienzo se desvanece y la aureola real vuelve
        if (!activo || cerrando) return;
        cerrando = true;
        clearTimeout(tOculta);
        cont.classList.remove('show-oculta');
        canvas.classList.remove('activo');
        tAbort = setTimeout(parar, 500);
    }

    function parar() {
        cancelAnimationFrame(raf);
        clearTimeout(tOculta); clearTimeout(tAbort);
        activo = false; cerrando = false;
        canvas.classList.remove('activo');
        cont.classList.remove('show-oculta');
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        hilos = []; chispas = [];
        ultima = performance.now();
    }

    // ── CONSTRUCCIÓN DE LA ESCENA ──────────────────────────────────────────────
    function construir() {
        W = window.innerWidth; H = window.innerHeight;
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
        cx = g.cx; cy = g.cy; Rpx = g.k * g.R; ay0 = H / W;

        // Colores desde las variables CSS: siguen cualquier cambio de tema
        AZUL = leerColor('--color-info', '#3b82f6');
        PAL = {
            rojo: leerColor('--color-error', '#ef4444'),
            verde: leerColor('--color-exito', '#10b981'),
            violeta: leerColor('--color-voz', '#a78bfa'),
            intenso: leerColor('--color-lector', '#8b5cf6'),
            naranja: leerColor('--color-advertencia', '#f59e0b'),
        };
        // Cada tipo imita el carácter de su aureola real: rojo/verde = círculo, violeta = ondula, intenso = ondas largas
        const tipos = [
            { col: PAL.rojo, k: 0, l: 0 },
            { col: PAL.verde, k: 0, l: 0 },
            { col: PAL.violeta, k: 0.32, l: 0 },
            { col: PAL.intenso, k: 0.18, l: 1 },
            { col: PAL.naranja, k: 0.7, l: 0 },      // procesando: la «gota» deformada
        ];
        const paleta = [PAL.rojo, PAL.verde, PAL.violeta, PAL.intenso, PAL.naranja, AZUL];
        const base = Math.min(W, H), dMax = 0.47 * W;
        hilos = [];

        // 1) Cuerpos: aureolas completas repartidas sin solaparse (órbita elíptica que cabe en pantalla)
        for (let i = 0; i < CFG.CUERPOS; i++) {
            const tp = tipos[i % tipos.length];
            for (let n = 0; n < 300; n++) {
                const rho = base * rnd(0.055, 0.11);
                const x = rnd(0.05 * W, 0.95 * W), y = rnd(0.07 * H, 0.93 * H);
                const dx = x - cx, dy = (y - cy) / ay0, d = Math.hypot(dx, dy);
                if (d > dMax || d < Rpx * 2.2 + rho) continue;
                if (hilos.some((o) => o.cuerpo && Math.hypot(o.x - x, o.y - y) < o.rho + rho + 10)) continue;
                hilos.push({
                    cuerpo: true, col: tp.col, k: tp.k, l: tp.l, rho, x, y,
                    th0: Math.atan2(dy, dx), d0: d, dfin: Rpx * rnd(0.9, 1.12),
                    f: rnd(0.92, 1.08), L: rnd(1.2, 2.4), w: 2, delay: rnd(0.2, 2.8), ph: rnd(0, TAU),
                });
                break;
            }
        }
        // 2) Hilos puros: líneas de energía que aparecen como círculos concéntricos
        for (let i = 0; i < CFG.HILOS; i++) {
            hilos.push({
                cuerpo: false, col: paleta[i % paleta.length],
                th0: rnd(0, TAU), d0: dMax * rnd(0.1, 1), dfin: Rpx * rnd(0.9, 1.12),
                f: rnd(0.7, 1.35), L: rnd(0.7, 2.6), w: rnd(1, 2.4), delay: rnd(0, 0.55), ph: rnd(0, TAU),
            });
        }
    }

    function crearChispas() {
        const base = Math.min(W, H), paleta = [AZUL, PAL.rojo, PAL.verde, PAL.violeta, PAL.intenso, PAL.naranja];

        chispas = Array.from({ length: CFG.CHISPAS }, (_, j) => {
            const a = rnd(0, TAU), v = base * (0.18 + 0.67 * Math.pow(Math.random(), 0.8));
            return {
                x: 0, y: 0, vx: Math.cos(a) * v, vy: Math.sin(a) * v, col: paleta[j % paleta.length],
                w: rnd(1.2, 2.6), tw: rnd(0, TAU), ao: (j / CFG.CHISPAS) * TAU, sw: rnd(-0.45, 0.45), sx: 0, sy: 0,
            };
        });
    }

    // ── DIBUJADO ───────────────────────────────────────────────────────────────
    // Réplica de la aureola real: mismas curvas (SaraAureola.contorno), mismas opacidades, mismo halo.
    // s = píxeles por unidad del viewBox; k = deformación; fs = fase de animación; l = intensidad de lector.
    function aureola(x, y, s, col, alfa, k, fs, l, glow, resp) {
        if (alfa < 0.004) return;
        ctx.save();
        ctx.globalCompositeOperation = 'source-over';
        ctx.translate(x, y); ctx.scale(s, s);
        const HR = 193;   // halo ambiental (equivale al ::after del CSS)
        const gh = ctx.createRadialGradient(0, 0, 0, 0, 0, HR);
        gh.addColorStop(0, rgba(col, Math.min(0.5, 0.16 * glow / 14) * alfa));
        gh.addColorStop(1, rgba(col, 0));
        ctx.fillStyle = gh; ctx.beginPath(); ctx.arc(0, 0, HR, 0, TAU); ctx.fill();
        const R = g.R;
        ctx.fillStyle = rgba(col, (0.05 + k * 0.20 + l * 0.14) * alfa);            // fondo: más chico, gira al revés
        ctx.fill(new Path2D(A.contorno(R * 0.80 * (1 + resp), k * 1.25, 2.1 + fs, -1, l * 1.25)));
        ctx.shadowColor = rgba(col, alfa); ctx.shadowBlur = glow;                  // borde con resplandor
        ctx.fillStyle = rgba(col, (0.06 + k * 0.14 + l * 0.10) * alfa);
        ctx.strokeStyle = rgba(col, alfa); ctx.lineWidth = 3; ctx.lineJoin = 'round';
        const pb = new Path2D(A.contorno(R * (1 + resp), k, fs, 1, l));
        ctx.fill(pb); ctx.stroke(pb);
        ctx.restore();
    }

    function dibujarHilo(h, ay, e, fL, alfaG) {
        const ang = h.th0 + h.f * Th;
        const d = lerp(h.d0, h.dfin, e);
        const a = (h.cuerpo ? suave((T - h.delay) / 1.6) : suave((fL - h.delay * 0.5) / 0.45)) * alfaG;
        if (a < 0.004) return;
        const hx = cx + d * Math.cos(ang), hy = cy + d * Math.sin(ang) * ay;

        if (h.cuerpo) {   // la aureola completa se «derrite» hacia línea durante la fase de líneas
            const flot = Math.sin(T * 0.8 + h.ph) * 5 * (1 - e);
            const ca = a * (1 - suave(fL * 1.25));
            aureola(hx, hy + flot, (h.rho / g.R) * lerp(1, 0.35, suave(fL)), h.col, ca,
                h.k, T * 0.9 + h.ph, h.l, 14, 0.014 * Math.sin(T * 1.3 + h.ph));
        }
        const L = lerp(h.L * suave(fL), 5.8, e * e);   // la estela crece; al converger se cierra en anillo
        if (L < 0.03) return;
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        ctx.translate(cx, cy); ctx.scale(1, ay);
        const gr = ctx.createConicGradient(ang - L, 0, 0);
        gr.addColorStop(0, rgba(h.col, 0));
        gr.addColorStop(clamp(L / TAU, 0.001, 0.999), rgba(h.col, a * 0.9));
        ctx.strokeStyle = gr; ctx.lineWidth = h.w * (h.cuerpo ? 1.4 : 1); ctx.lineCap = 'round';
        ctx.beginPath(); ctx.arc(0, 0, d, ang - L, ang); ctx.stroke();
        ctx.restore();
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        ctx.fillStyle = rgba(h.col, a);
        ctx.beginPath(); ctx.arc(hx, hy, h.w * 1.3, 0, TAU); ctx.fill();     // cabeza brillante
        ctx.restore();
    }

    function trazoChispa(x0, y0, x1, y1, col, a, w) {
        ctx.strokeStyle = rgba(col, a); ctx.lineWidth = w;
        ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
        ctx.fillStyle = rgba(col, a * 0.35);
        ctx.beginPath(); ctx.arc(x1, y1, w * 1.8, 0, TAU); ctx.fill();
    }

    function posChispa(c, u) {   // trayectoria curva de vuelta al anillo de la aureola
        const tx = Rpx * Math.cos(c.ao), ty = Rpx * Math.sin(c.ao);
        const dx = tx - c.sx, dy = ty - c.sy, len = Math.hypot(dx, dy) || 1;
        const off = Math.sin(Math.PI * u) * c.sw * len * 0.5;
        return [c.sx + dx * u - (dy / len) * off, c.sy + dy * u + (dx / len) * off];
    }

    // ── BUCLE PRINCIPAL ────────────────────────────────────────────────────────
    const fase = (k) => clamp((T - T0[k]) / FASES[k]);

    function cuadro(ahora) {
        if (!activo) return;
        raf = requestAnimationFrame(cuadro);
        const dt = Math.min((ahora - previo) / 1000, 0.05) * CFG.VELOCIDAD;
        previo = ahora;
        T += dt;

        const fG = fase('giro'), fL = fase('lineas'), fC = fase('convergencia'), fV = fase('vibracion');
        const e = eio(fC), uR = eio(fase('reagrupar'));

        // Velocidad angular: despacio → acelera → gira frenético al converger → frena al vibrar
        let om = 0;
        if (T >= T0.giro) om = 0.22 * suave(fG);
        if (T >= T0.lineas) om = 0.22 + 2.0 * fL * fL;
        if (T >= T0.convergencia) om = 2.22 + 3.0 * Math.pow(fC, 1.5);
        if (T >= T0.vibracion) om = 5.22 * (1 - 0.8 * suave(fV));
        Th += om * dt;
        const ay = lerp(ay0, 1, e);   // órbitas elípticas (llenan la pantalla) → círculos perfectos al converger

        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, W, H);

        // 1) cuerpos y líneas de energía (se apagan al vibrar)
        const alfaG = 1 - suave((T - T0.vibracion) / (FASES.vibracion * 0.5));
        if (T < T0.explosion && alfaG > 0.003) for (const h of hilos) dibujarHilo(h, ay, e, fL, alfaG);

        // 2) aureola azul central
        const resp = 0.014 * Math.sin(A.reloj() * 1.3);   // respiración idéntica a la real
        let aC = 1, kC = 0, fsC = 0, sC = g.k, glow = 14, mixB = 0, jx = 0, jy = 0;
        if (T < T0.vibracion) {                            // absorbe la energía que converge
            glow = 14 + 22 * e * e; mixB = 0.3 * e * e;
        } else if (T < T0.explosion) {                     // vibra cada vez más fuerte
            const v = suave(fV), jit = 0.4 + 5 * v;
            kC = 0.55 * v; fsC = T * 34; glow = 36 + 24 * v; mixB = 0.3 + 0.5 * v;
            jx = rnd(-jit, jit); jy = rnd(-jit, jit); sC = g.k * (1 - 0.08 * v);
        } else if (T < T0.reagrupar) {                     // estalla: desaparece en un destello
            aC = 1 - suave((T - T0.explosion) / 0.22); kC = 0.55; fsC = T * 34; glow = 60; mixB = 0.8;
        } else {                                           // renace desvaneciéndose hasta quedar sólida
            aC = suave((uR - 0.7) / 0.3); kC = 0.3 * (1 - aC); fsC = T * 6;
            glow = 14 + 24 * Math.sin(Math.PI * aC); mixB = 0.5 * (1 - aC);
        }
        aureola(cx + jx, cy + jy, sC, mezcla(AZUL, BLANCO, mixB), aC, kC, fsC, 0, glow, resp);

        // 3) destello + ondas expansivas del estallido
        if (T >= T0.explosion) {
            if (!explotado) { explotado = true; crearChispas(); }
            const tx = T - T0.explosion;
            ctx.save();
            ctx.globalCompositeOperation = 'lighter';
            const fl = 1 - clamp(tx / 0.5);
            if (fl > 0) {
                const gd = ctx.createRadialGradient(cx, cy, 0, cx, cy, Rpx * 5);
                gd.addColorStop(0, rgba(BLANCO, 0.9 * fl * fl));
                gd.addColorStop(0.35, rgba(AZUL, 0.35 * fl));
                gd.addColorStop(1, rgba(AZUL, 0));
                ctx.fillStyle = gd; ctx.beginPath(); ctx.arc(cx, cy, Rpx * 5, 0, TAU); ctx.fill();
            }
            for (let i = 0; i < 2; i++) {
                const p = clamp((tx - i * 0.25) / 1.4);
                if (p <= 0 || p >= 1) continue;
                ctx.strokeStyle = rgba(mezcla(AZUL, BLANCO, 0.5), 0.55 * (1 - p) * (1 - p));
                ctx.lineWidth = 1 + 4 * (1 - p);
                ctx.beginPath(); ctx.arc(cx, cy, lerp(Rpx, Math.hypot(W, H) * 0.42, 1 - (1 - p) * (1 - p)), 0, TAU); ctx.stroke();
            }
            ctx.restore();

            // 4) chispas: vuelan, frenan y flotan; luego vuelven curvando al anillo de la aureola
            ctx.save();
            ctx.globalCompositeOperation = 'lighter';
            ctx.lineCap = 'round';
            if (T >= T0.reagrupar && !regrupado) {
                regrupado = true;
                for (const c of chispas) { c.sx = c.x; c.sy = c.y; }
            }
            for (const c of chispas) {
                if (T < T0.reagrupar) {
                    const dr = Math.exp(-1.7 * dt);
                    c.vx *= dr; c.vy = c.vy * dr + 40 * dt;
                    c.x += c.vx * dt; c.y += c.vy * dt;
                    const v = Math.hypot(c.vx, c.vy), len = clamp(v * 0.05, 2, 70);
                    const ux = v > 1 ? c.vx / v : 0, uy = v > 1 ? c.vy / v : 0;
                    const al = Math.max(0.55 + 0.4 * Math.sin(T * 6 + c.tw), 1 - tx / 0.8);
                    trazoChispa(cx + c.x - ux * len, cy + c.y - uy * len, cx + c.x, cy + c.y, c.col, al, c.w);
                } else {
                    const [x1, y1] = posChispa(c, uR), [x0, y0] = posChispa(c, Math.max(0, uR - 0.06));
                    const al = (1 - suave((uR - 0.88) / 0.12)) * (0.75 + 0.25 * Math.sin(T * 9 + c.tw));
                    trazoChispa(cx + x0, cy + y0, cx + x1, cy + y1, mezcla(c.col, AZUL, uR * uR), al, c.w * (1 - 0.4 * uR));
                }
            }
            ctx.restore();
        }

        // 5) cierre: la aureola real reaparece bajo la réplica y después el lienzo se desvanece
        if (T >= T0.cierre && !cierreOK) { cierreOK = true; cont.classList.remove('show-oculta'); }
        if (T >= T0.cierre + 0.7 && !cerrando) { cerrando = true; canvas.classList.remove('activo'); }
        if (T >= TOTAL) parar();
    }

    // ── ARRANQUE ───────────────────────────────────────────────────────────────
    ['keydown', 'pointerdown', 'wheel', 'touchstart'].forEach((ev) =>
        window.addEventListener(ev, actividad, { passive: true, capture: true }));
    window.addEventListener('mousemove', alMover, { passive: true });
    window.addEventListener('resize', actividad);
    document.addEventListener('visibilitychange', actividad);
    document.documentElement.addEventListener('mouseleave', () => { dentro = false; });
    setInterval(revisar, 1000);

    return { contexto, actividad, iniciar, detener: abortar };
})();
