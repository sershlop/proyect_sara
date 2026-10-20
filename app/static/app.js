/**
 * app.js — Cliente WebSocket y lógica de UI para SARA v2.0 (v2, fusionado con el backend)
 *
 * Eventos servidor → GUI:  servidor_listo, estado_procesando, resultado_clasificacion,
 *   resultado_accion, solicitud_confirmacion, confirmacion_ya_resuelta,
 *   sugerencia_proactiva, notificacion, estado_sistema, pong, voz_estado (NUEVO: SARA hablando)
 * Eventos GUI → servidor:  mensaje_usuario, respuesta_confirmacion, respuesta_sugerencia, ping,
 *                          lector_leer, lector_detener, lector_reanudar,
 *                          stt_activar, stt_desactivar, stt_umbral
 * Evento servidor → GUI del lector de texto a voz:  lector_estado
 * Eventos servidor → GUI de la voz de entrada (STT):  stt_estado, stt_nivel, stt_texto
 */

const CONFIG = {
    WS_URL: `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`,
    RECONNECT_DELAY: 3000,
    RECONNECT_MAX_ATTEMPTS: 20,
    WATCHDOG_MS: 100000,     // si el servidor nunca responde, se libera el input
};

const STATE = {
    ws: null,
    conectado: false,
    procesando: false,
    core: 'cargando',
    confirmacion_pendiente: null,
    timer_modal: null,
    watchdog: null,
    intentos_reconexion: 0,
    avisado_desconexion: false,
    historial: [],
    idx_historial: -1,
    feedback: null,
    hablando: false,          // SARA está diciendo algo en voz alta (NO bloquea el input)
    timer_voz: null,
    remoto: { pensando: false, timer_pensar: null, timer_habla: null },   // actividad de Telegram / WhatsApp
    estado_aureola: 'procesando',
    lector: { leyendo: false, reanudable: false },   // lector de texto a voz (canal aparte de la voz normal)
    stt: { activo: false, estado: 'apagado' },       // voz de entrada (micrófono → texto)
};

const $ = (id) => document.getElementById(id);
const DOM = {
    aureola: $('aureola'), etapaLabel: $('etapa-label'),
    chatPanel: $('chat-panel'), toggleChat: $('toggle-chat'),
    inputUsuario: $('input-usuario'), btnEnviar: $('btn-enviar'),
    mensajes: $('mensajes'), acciones: $('acciones-rapidas'),
    modal: $('modal-confirmacion'), pregunta: $('pregunta-confirmacion'),
    opciones: $('opciones-container'), botones: $('botones-container'),
    btnSi: $('btn-si'), btnNo: $('btn-no'), timeoutFill: $('timeout-fill'),
    sugPanel: $('sugerencias-panel'), sugContainer: $('sugerencias-container'),
    cpu: $('cpu-valor'), ram: $('ram-valor'), disco: $('disco-valor'),
    red: $('red-valor'), ollama: $('ollama-valor'), core: $('core-valor'), hora: $('hora-valor'),
    lectorTab: $('lector-tab'), lectorPanel: $('lector-panel'), lectorTexto: $('lector-texto'),
    lectorBtn: $('lector-btn'), lectorReanudar: $('lector-reanudar'), lectorLimpiar: $('lector-limpiar'),
    lectorProgreso: $('lector-progreso'), lectorAviso: $('lector-aviso'),
    btnVoz: $('btn-voz'), sttPanel: $('stt-panel'), sttEstado: $('stt-estado'),
    sttNivel: $('stt-nivel'), sttSlider: $('stt-slider'),
};

// ── INICIO ────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    inicializar_handlers();
    actualizar_hora();
    setInterval(actualizar_hora, 1000);
    setInterval(() => { if (STATE.conectado) enviar_evento('ping'); }, 30000);
    conectar_websocket();
});

// ── WEBSOCKET ─────────────────────────────────────────────────────────────

function conectar_websocket() {
    try {
        STATE.ws = new WebSocket(CONFIG.WS_URL);
    } catch (e) {
        agregar_mensaje('error', `No se pudo crear la conexión: ${e.message}`);
        return;
    }

    STATE.ws.onopen = () => {
        STATE.conectado = true;
        STATE.intentos_reconexion = 0;
        if (STATE.avisado_desconexion) agregar_mensaje('sistema meta', 'Reconectado');
        STATE.avisado_desconexion = false;
        if (!STATE.procesando) bloquear_input(false);
    };

    STATE.ws.onmessage = (ev) => {
        try { despachar_evento(JSON.parse(ev.data)); }
        catch (e) { console.error('Evento inválido:', e, ev.data); }
    };

    STATE.ws.onerror = () => { /* onclose se encarga */ };

    STATE.ws.onclose = () => {
        STATE.conectado = false;
        STATE.procesando = false;
        STATE.hablando = false;
        actualizar_aureola('error');
        actualizar_etapa_label('Sin conexión');
        DOM.core.textContent = '✗';
        if (!STATE.avisado_desconexion) {
            agregar_mensaje('error', 'Conexión perdida. Reintentando…');
            STATE.avisado_desconexion = true;
        }
        cerrar_modal();
        if (STATE.intentos_reconexion < CONFIG.RECONNECT_MAX_ATTEMPTS) {
            STATE.intentos_reconexion++;
            setTimeout(conectar_websocket, CONFIG.RECONNECT_DELAY);
        } else {
            agregar_mensaje('error', 'No se pudo reconectar. Reinicia SARA.');
        }
    };
}

function enviar_evento(tipo, datos = {}) {
    if (!STATE.conectado || STATE.ws.readyState !== WebSocket.OPEN) return false;
    STATE.ws.send(JSON.stringify({ tipo, timestamp: new Date().toISOString(), ...datos }));
    return true;
}

// ── DESPACHO ──────────────────────────────────────────────────────────────

const DESPACHO = {
    servidor_listo: evento_servidor_listo,
    estado_procesando: evento_estado_procesando,
    resultado_clasificacion: evento_resultado_clasificacion,
    resultado_accion: evento_resultado_accion,
    solicitud_confirmacion: evento_solicitud_confirmacion,
    confirmacion_ya_resuelta: evento_confirmacion_ya_resuelta,
    sugerencia_proactiva: evento_sugerencia_proactiva,
    notificacion: evento_notificacion,
    voz_estado: evento_voz_estado,
    actividad_remota: evento_actividad_remota,
    lector_estado: evento_lector_estado,
    stt_estado: evento_stt_estado,
    stt_nivel: evento_stt_nivel,
    stt_texto: evento_stt_texto,
    estado_sistema: evento_estado_sistema,
    pong: () => {},
};

// Eventos que el servidor emite por su cuenta (sin que el usuario haya hecho nada): NO cuentan como actividad
// ni interrumpen el ester egg. Se siguen mostrando normalmente en el chat / panel de sugerencias.
const EVENTOS_PASIVOS = new Set(['estado_sistema', 'pong', 'notificacion', 'sugerencia_proactiva', 'stt_nivel']);

function despachar_evento(evento) {
    if (window.SaraShow && !EVENTOS_PASIVOS.has(evento.tipo)) SaraShow.actividad();
    const h = DESPACHO[evento.tipo];
    if (h) h(evento); else console.warn('Evento desconocido:', evento.tipo);
}

// ── HANDLERS DE EVENTOS ───────────────────────────────────────────────────

function evento_servidor_listo(ev) {
    const anterior = STATE.core;
    STATE.core = ev.core || 'cargando';
    if (STATE.core === 'listo') {
        DOM.core.textContent = '✓';
        if (!STATE.procesando) { actualizar_aureola('listo'); actualizar_etapa_label('Listo'); }
        if (anterior !== 'listo') agregar_mensaje('sistema meta', 'Núcleo de SARA listo');
    } else if (STATE.core === 'error') {
        DOM.core.textContent = '✗';
        actualizar_aureola('error');
        actualizar_etapa_label('Error de núcleo');
        agregar_mensaje('error', `El núcleo no arrancó: ${ev.error || 'revisa sara_server_debug.log'}`);
    } else {
        DOM.core.textContent = '…';
        actualizar_aureola('procesando');
        actualizar_etapa_label('Cargando núcleo…');
    }
}

function evento_estado_procesando(ev) {
    // Compatibilidad: un voz.py antiguo emitía etapa "hablando" DESPUÉS de la respuesta y dejaba la GUI bloqueada.
    if (ev.etapa === 'hablando') { evento_voz_estado({ hablando: true }); return; }
    // Solo tiene sentido mientras esperamos respuesta a un mensaje NUESTRO. Si el servidor ya dio la
    // operación por terminada (p. ej. "tardó más de 90 s"), los avisos tardíos del hilo que sigue
    // trabajando NO deben volver a bloquear la interfaz.
    if (!STATE.procesando) return;
    STATE.procesando = true;
    actualizar_aureola('procesando');
    actualizar_etapa_label(ev.etapa);
    bloquear_input(true);
    armar_watchdog();
}

// Telegram / WhatsApp: la aureola se comporta igual que con una orden escrita en la ventana,
// pero SIN bloquear el input ni tocar STATE.procesando.  pensando = amarillo · hablando = violeta.
function evento_actividad_remota(ev) {
    const R = STATE.remoto;
    const canal = ev.canal === 'whatsapp' ? '📱 WhatsApp' : '✈️ Telegram';
    if (ev.fase === 'pensando') {
        R.pensando = true;
        clearTimeout(R.timer_pensar);
        R.timer_pensar = setTimeout(() => { R.pensando = false; if (!STATE.procesando) actualizar_aureola('listo'); }, 120000);
        if (!STATE.procesando) {
            actualizar_aureola('procesando');
            actualizar_etapa_label(`${canal}: pensando…`);
        }
    } else if (ev.fase === 'hablando') {
        R.pensando = false;
        clearTimeout(R.timer_pensar);
        if (STATE.procesando) return;
        actualizar_aureola('listo');
        evento_voz_estado({ hablando: true });
        clearTimeout(R.timer_habla);
        R.timer_habla = setTimeout(() => {
            evento_voz_estado({ hablando: false });
            if (!R.pensando && !STATE.procesando) { actualizar_aureola('listo'); actualizar_etapa_label('Listo'); }
        }, Math.max(1500, (Number(ev.duracion) || 3) * 1000));
        actualizar_etapa_label(`${canal}: respondiendo`);
    } else if (ev.fase === 'fin') {
        R.pensando = false;
        clearTimeout(R.timer_pensar);
        if (STATE.procesando || STATE.hablando) return;      // si va a sonar el violeta, que termine solo
        actualizar_aureola(ev.ok ? 'exito' : 'error');
        setTimeout(() => {
            if (!STATE.procesando && !R.pensando) { actualizar_aureola('listo'); actualizar_etapa_label(STATE.hablando ? '🔊 Hablando' : 'Listo'); }
        }, ev.ok ? 800 : 1000);
    }
}

function evento_voz_estado(ev) {
    STATE.hablando = !!ev.hablando;
    clearTimeout(STATE.timer_voz);
    if (STATE.hablando) {      // red de seguridad: si nunca llega el 'false', se apaga sola
        STATE.timer_voz = setTimeout(() => { STATE.hablando = false; actualizar_aureola(STATE.estado_aureola); }, 45000);
    }
    actualizar_aureola(STATE.estado_aureola);
    if (!STATE.procesando && STATE.core === 'listo') actualizar_etapa_label(STATE.hablando ? '🔊 Hablando' : 'Listo');
}

function evento_resultado_clasificacion(ev) {
    const pct = Math.round((Number(ev.confianza) || 0) * 100);
    const via = ev.metodo ? ` · vía ${ev.metodo}` : '';
    const div = agregar_mensaje('sistema meta', `${ev.categoria} · ${pct}%${via}`);
    quitar_feedback();
    if (!div) return;
    const fb = document.createElement('span');
    fb.className = 'feedback-clasificacion';
    fb.innerHTML = '<button class="fb-btn" data-cmd="/confirmar" title="Clasificación correcta">👍</button>' +
                   '<button class="fb-btn" data-cmd="/validar" title="Guardar como regla permanente">📌</button>' +
                   '<button class="fb-btn" data-cmd="/corregir" title="Corregir la categoría">✏️</button>';
    fb.addEventListener('click', (e) => {
        const b = e.target.closest('.fb-btn');
        if (!b) return;
        if (b.dataset.cmd === '/corregir') {
            DOM.inputUsuario.value = '/corregir ';
            DOM.inputUsuario.focus();
            return;
        }
        if (STATE.procesando) return;
        quitar_feedback();
        enviar_texto(b.dataset.cmd);
    });
    div.appendChild(fb);
    STATE.feedback = fb;
}

function quitar_feedback() {
    if (STATE.feedback) { STATE.feedback.remove(); STATE.feedback = null; }
}

function evento_resultado_accion(ev) {
    agregar_mensaje(ev.exito ? 'exito' : 'error', ev.texto || '(sin respuesta)');
    actualizar_aureola(ev.exito ? 'exito' : 'error');
    actualizar_etapa_label(ev.exito ? 'Hecho' : 'Error');
    clearTimeout(STATE.watchdog);
    STATE.procesando = false;
    cerrar_modal();
    setTimeout(() => {
        if (STATE.procesando) return;
        bloquear_input(false);
        DOM.inputUsuario.focus();
        actualizar_aureola('listo');
        actualizar_etapa_label('Listo');
    }, ev.exito ? 800 : 1000);   // error: 1 s en rojo antes de volver a azul
}

function evento_solicitud_confirmacion(ev) {
    const { id_confirmacion, pregunta, opciones, timeout_segundos } = ev;
    STATE.confirmacion_pendiente = id_confirmacion;
    DOM.pregunta.textContent = pregunta;
    DOM.pregunta.style.whiteSpace = 'pre-wrap';
    DOM.opciones.innerHTML = '';
    (opciones || []).forEach((op, idx) => {
        const b = document.createElement('button');
        b.className = 'opcion-btn';
        b.textContent = op;
        b.onclick = () => responder_confirmacion(id_confirmacion, 'si', idx);
        DOM.opciones.appendChild(b);
    });
    DOM.botones.style.display = (opciones && opciones.length) ? 'none' : 'flex';

    DOM.timeoutFill.style.animation = 'none';
    void DOM.timeoutFill.offsetWidth;   // reinicia la animación
    DOM.timeoutFill.style.animation = `timeout-shrink ${timeout_segundos}s linear forwards`;

    clearTimeout(STATE.timer_modal);
    STATE.timer_modal = setTimeout(() => {
        if (STATE.confirmacion_pendiente === id_confirmacion) cerrar_modal();
    }, timeout_segundos * 1000);

    DOM.modal.classList.remove('hidden');
    actualizar_etapa_label('Esperando confirmación');
    DOM.btnSi.focus();
}

function evento_confirmacion_ya_resuelta(ev) {
    if (ev.resultado === 'timeout') agregar_mensaje('sistema meta', 'Confirmación expirada');
    cerrar_modal();
}

function evento_sugerencia_proactiva(ev) {
    const { id_sugerencia, titulo, descripcion } = ev;
    const t = document.createElement('div');
    t.className = 'tarjeta-sugerencia';
    t.innerHTML = `<p><strong>${escaper_html(titulo || 'Sugerencia')}</strong></p>
        <p>${escaper_html(descripcion || '')}</p>
        <div class="botones-sugerencia">
            <button class="btn-sugerencia btn-activar">Activar</button>
            <button class="btn-sugerencia btn-ignorar">Ignorar</button>
        </div>`;
    const responder = (respuesta) => {
        enviar_evento('respuesta_sugerencia', { id_sugerencia, respuesta });
        t.remove();
        actualizar_panel_sugerencias();
    };
    t.querySelector('.btn-activar').onclick = () => responder('activar');
    t.querySelector('.btn-ignorar').onclick = () => responder('ignorar');
    DOM.sugContainer.appendChild(t);
    actualizar_panel_sugerencias();
    agregar_mensaje('sistema meta', `💡 Sugerencia: ${titulo || ''}`);
}

function evento_notificacion(ev) {
    const clase = ev.nivel === 'error' ? 'error' : ev.nivel === 'exito' ? 'exito' : 'notificacion';
    agregar_mensaje(clase, ev.texto || '');
}

function evento_estado_sistema(ev) {
    const { cpu, ram, disco, red, ollama } = ev.data || {};
    const nivel = (el, v) => { el.className = 'valor' + (v >= 90 ? ' error' : v >= 80 ? ' advertencia' : ''); };
    if (cpu !== undefined) { DOM.cpu.textContent = `${cpu}%`; nivel(DOM.cpu, cpu); }
    if (ram !== undefined) { DOM.ram.textContent = `${ram}%`; nivel(DOM.ram, ram); }
    if (disco !== undefined) { DOM.disco.textContent = `${disco}%`; nivel(DOM.disco, disco); }
    if (red !== undefined) DOM.red.textContent = red ? '✓' : '✗';
    if (ollama !== undefined) DOM.ollama.textContent = ollama ? '✓' : '✗';
}

// ── ACCIONES DE UI ────────────────────────────────────────────────────────

function inicializar_handlers() {
    DOM.inputUsuario.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); enviar_mensaje(); }
        else if (e.key === 'ArrowUp') { navegar_historial(1); e.preventDefault(); }
        else if (e.key === 'ArrowDown') { navegar_historial(-1); e.preventDefault(); }
    });
    DOM.btnEnviar.addEventListener('click', enviar_mensaje);

    DOM.toggleChat.addEventListener('click', () => {
        DOM.chatPanel.classList.toggle('collapsed');
        DOM.toggleChat.textContent = DOM.chatPanel.classList.contains('collapsed') ? '+' : '−';
    });

    DOM.acciones.addEventListener('click', (e) => {
        const chip = e.target.closest('.chip');
        if (chip && !STATE.procesando) enviar_texto(chip.dataset.cmd);
    });

    DOM.btnSi.addEventListener('click', () => STATE.confirmacion_pendiente &&
        responder_confirmacion(STATE.confirmacion_pendiente, 'si'));
    DOM.btnNo.addEventListener('click', () => STATE.confirmacion_pendiente &&
        responder_confirmacion(STATE.confirmacion_pendiente, 'no'));
    document.addEventListener('keydown', (e) => {
        if (DOM.modal.classList.contains('hidden') || !STATE.confirmacion_pendiente) return;
        if (e.key === 'Escape') responder_confirmacion(STATE.confirmacion_pendiente, 'no');
    });

    inicializar_lector();
    inicializar_stt();
}

function enviar_mensaje() {
    const texto = DOM.inputUsuario.value.trim();
    if (!texto || STATE.procesando) return;
    if (enviar_texto(texto)) {
        STATE.historial.unshift(texto);
        STATE.historial = STATE.historial.slice(0, 50);
        STATE.idx_historial = -1;
        DOM.inputUsuario.value = '';
    }
}

function enviar_texto(texto, etiqueta = '', origen = 'texto') {
    // origen: 'texto' (teclado, chips y botones) o 'voz' (micrófono). El servidor solo acepta esos dos.
    if (!STATE.conectado) { agregar_mensaje('error', 'No conectado al servidor'); return false; }
    agregar_mensaje('usuario', etiqueta ? `${etiqueta} ${texto}` : texto);
    if (!enviar_evento('mensaje_usuario', { texto, origen })) return false;
    STATE.procesando = true;          // se libera con resultado_accion (o el watchdog)
    bloquear_input(true);
    armar_watchdog();
    return true;
}

function navegar_historial(dir) {
    const n = STATE.idx_historial + dir;
    if (n < -1 || n >= STATE.historial.length) return;
    STATE.idx_historial = n;
    DOM.inputUsuario.value = n === -1 ? '' : STATE.historial[n];
}

function responder_confirmacion(id, respuesta, opcion = null) {
    enviar_evento('respuesta_confirmacion', { id_confirmacion: id, respuesta, opcion });
    cerrar_modal();
}

function cerrar_modal() {
    clearTimeout(STATE.timer_modal);
    STATE.confirmacion_pendiente = null;
    DOM.modal.classList.add('hidden');
    DOM.opciones.innerHTML = '';
    if (STATE.procesando) actualizar_etapa_label('ejecución');
}

function bloquear_input(bloq) {
    DOM.inputUsuario.disabled = bloq;
    DOM.btnEnviar.disabled = bloq;
}

function armar_watchdog() {
    clearTimeout(STATE.watchdog);
    STATE.watchdog = setTimeout(() => {
        if (!STATE.procesando) return;
        STATE.procesando = false;
        bloquear_input(false);
        actualizar_aureola('error');
        actualizar_etapa_label('Sin respuesta');
        agregar_mensaje('error', 'El servidor no respondió a tiempo.');
    }, CONFIG.WATCHDOG_MS);
}

// ── UTILIDADES DE UI ──────────────────────────────────────────────────────

function agregar_mensaje(tipo, texto) {
    const div = document.createElement('div');
    div.className = `mensaje ${tipo}`;
    const ts = new Date().toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' });
    div.innerHTML = `<span class="timestamp">${ts}</span><p>${escaper_html(texto)}</p>`;
    DOM.mensajes.appendChild(div);
    while (DOM.mensajes.children.length > 200) DOM.mensajes.removeChild(DOM.mensajes.firstChild);
    DOM.mensajes.scrollTop = DOM.mensajes.scrollHeight;
    return div;
}

function escaper_html(texto) {
    const d = document.createElement('div');
    d.textContent = texto == null ? '' : String(texto);
    return d.innerHTML;
}

function actualizar_panel_sugerencias() {
    DOM.sugPanel.classList.toggle('vacio', DOM.sugContainer.children.length === 0);
}

function actualizar_aureola(estado) {
    STATE.estado_aureola = estado;
    // La voz solo colorea/ondula la aureola en reposo; procesando / éxito / error tienen prioridad.
    // El lector tiene su propia aureola (violeta intenso, ondas largas) y desplaza a la de voz normal.
    const lector = STATE.lector.leyendo && estado === 'listo';
    const voz = STATE.hablando && estado === 'listo' && !lector;
    DOM.aureola.className = `aureola aureola-${estado}${voz ? ' hablando' : ''}${lector ? ' lector' : ''}`;
    if (window.SaraAureola) { SaraAureola.setEstado(estado); SaraAureola.setHablando(voz); SaraAureola.setLector(lector); }
    if (window.SaraShow) SaraShow.contexto(estado, voz, lector);   // ester egg: reinicia el reloj de inactividad
}

function actualizar_etapa_label(etapa) {
    const mapeo = {
        'normalización': '📝 Normalizando', 'clasificación': '🏷️ Clasificando',
        'ejecución': '⚙️ Ejecutando', 'listo': 'Listo', 'error': 'Error',
    };
    DOM.etapaLabel.textContent = mapeo[etapa] || etapa;
}

function actualizar_hora() {
    const a = new Date();
    DOM.hora.textContent = `${String(a.getHours()).padStart(2, '0')}:${String(a.getMinutes()).padStart(2, '0')}`;
}

// ── LECTOR DE TEXTO A VOZ ─────────────────────────────────────────────────
// Canal independiente de la voz normal. La bandeja principal tiene PRIORIDAD: al enviar un mensaje
// el servidor pausa el lector (evento lector_estado 'pausado') y aquí aparece el botón «Reanudar».

function inicializar_lector() {
    if (!DOM.lectorTab || !DOM.lectorPanel) return;       // GUI sin el lector: no hacer nada
    DOM.lectorTab.addEventListener('click', () => alternar_panel_lector());
    DOM.lectorBtn.addEventListener('click', () => (STATE.lector.leyendo ? detener_lector() : iniciar_lector()));
    DOM.lectorReanudar.addEventListener('click', reanudar_lector);
    DOM.lectorLimpiar.addEventListener('click', () => { DOM.lectorTexto.value = ''; DOM.lectorTexto.focus(); });
    DOM.lectorTexto.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (!STATE.lector.leyendo) iniciar_lector();      // Enter solo inicia; detener es el botón rojo
        } else if (e.key === 'Escape') {
            alternar_panel_lector(false);
        }
    });
}

function alternar_panel_lector(abrir) {
    const mostrar = abrir === undefined ? DOM.lectorPanel.classList.contains('hidden') : abrir;
    DOM.lectorPanel.classList.toggle('hidden', !mostrar);
    DOM.lectorTab.setAttribute('aria-expanded', String(mostrar));
    if (mostrar) {
        DOM.lectorTab.classList.remove('atencion');
        DOM.lectorTexto.focus();
    }
}

function mostrar_aviso_lector(texto) {
    DOM.lectorAviso.textContent = texto || '';
    DOM.lectorAviso.classList.toggle('hidden', !texto);
}

function poner_lector({ leyendo, reanudable, progreso }) {
    STATE.lector.leyendo = !!leyendo;
    STATE.lector.reanudable = !!reanudable;
    DOM.lectorBtn.classList.toggle('detener', STATE.lector.leyendo);
    DOM.lectorBtn.textContent = STATE.lector.leyendo ? '■ Detener' : '▶ Leer';
    DOM.lectorReanudar.classList.toggle('hidden', !STATE.lector.reanudable);
    DOM.lectorTab.classList.toggle('leyendo', STATE.lector.leyendo);
    DOM.lectorProgreso.textContent = progreso || '';
    actualizar_aureola(STATE.estado_aureola);
}

function iniciar_lector() {
    const texto = DOM.lectorTexto.value.trim();
    if (!texto) { mostrar_aviso_lector('Escribe o pega el texto que quieres que lea.'); return; }
    if (!enviar_evento('lector_leer', { texto })) { mostrar_aviso_lector('Sin conexión con el servidor.'); return; }
    mostrar_aviso_lector('');
    poner_lector({ leyendo: true, reanudable: false, progreso: 'Preparando…' });   // botón rojo al instante
}

function detener_lector() {
    if (!enviar_evento('lector_detener')) mostrar_aviso_lector('Sin conexión con el servidor.');
}

function reanudar_lector() {
    if (!enviar_evento('lector_reanudar')) { mostrar_aviso_lector('Sin conexión con el servidor.'); return; }
    mostrar_aviso_lector('');
    poner_lector({ leyendo: true, reanudable: false, progreso: 'Reanudando…' });
}

function evento_lector_estado(ev) {
    const progreso = ev.total ? `${Math.min((ev.actual || 0) + (ev.estado === 'leyendo' ? 1 : 0), ev.total)}/${ev.total}` : '';
    switch (ev.estado) {
        case 'leyendo':
            poner_lector({ leyendo: true, reanudable: false, progreso });
            break;
        case 'terminado':
            poner_lector({ leyendo: false, reanudable: false, progreso: '' });
            mostrar_aviso_lector('');
            break;
        case 'detenido':
            poner_lector({ leyendo: false, reanudable: !!ev.puede_reanudar, progreso });
            break;
        case 'pausado':      // la bandeja principal reclamó el canal
            poner_lector({ leyendo: false, reanudable: !!ev.puede_reanudar, progreso });
            mostrar_aviso_lector('Lectura en pausa: atendí un comando tuyo. Puedes reanudarla desde aquí.');
            if (DOM.lectorPanel.classList.contains('hidden')) DOM.lectorTab.classList.add('atencion');
            break;
        case 'error':
            poner_lector({ leyendo: false, reanudable: false, progreso: '' });
            mostrar_aviso_lector(ev.mensaje || 'El lector no pudo continuar.');
            break;
    }
}

// ── VOZ DE ENTRADA (STT) ──────────────────────────────────────────────────
// El micrófono se captura en Python (stt.py). Aquí solo: botón 🎙️ (activar/desactivar, rojo si está activo),
// slider del noise gate + medidor de nivel, y el envío del texto reconocido por el MISMO camino que el chat
// escrito (enviar_texto → mensaje_usuario). El modo texto sigue funcionando igual.

const STT_ETIQUETAS = {
    cargando: 'Cargando el modelo de voz…',
    escuchando: '🎙️ Escuchando',
    grabando: '🔴 Te escucho…',
    transcribiendo: '✍️ Transcribiendo…',
    pausado: '⏸️ Pausado: SARA está hablando',
};

function inicializar_stt() {
    if (!DOM.btnVoz || !DOM.sttSlider) return;       // GUI sin voz de entrada: no hacer nada
    DOM.btnVoz.addEventListener('click', alternar_voz);
    // Mientras se arrastra: el umbral cambia en vivo. Al soltar: se guarda en el servidor.
    DOM.sttSlider.addEventListener('input', () =>
        enviar_evento('stt_umbral', { valor: Number(DOM.sttSlider.value), guardar: false }));
    DOM.sttSlider.addEventListener('change', () =>
        enviar_evento('stt_umbral', { valor: Number(DOM.sttSlider.value), guardar: true }));
}

function alternar_voz() {
    if (STATE.stt.activo) {
        if (!enviar_evento('stt_desactivar')) { agregar_mensaje('error', 'Sin conexión con el servidor.'); return; }
        poner_stt({ activo: false, estado: 'apagado' });
    } else {
        if (!enviar_evento('stt_activar', { umbral: Number(DOM.sttSlider.value) })) {
            agregar_mensaje('error', 'Sin conexión con el servidor.');
            return;
        }
        poner_stt({ activo: true, estado: 'cargando', mensaje: 'Iniciando…' });     // botón rojo al instante
    }
}

function poner_stt({ activo, estado, mensaje, umbral }) {
    STATE.stt.activo = !!activo;
    STATE.stt.estado = estado || 'apagado';
    DOM.btnVoz.classList.toggle('activo', STATE.stt.activo);
    DOM.btnVoz.setAttribute('aria-pressed', String(STATE.stt.activo));
    DOM.btnVoz.title = STATE.stt.activo ? 'Desactivar modo voz' : 'Activar modo voz';
    DOM.sttPanel.classList.toggle('hidden', !STATE.stt.activo);
    DOM.sttEstado.textContent = mensaje || STT_ETIQUETAS[STATE.stt.estado] || '';
    if (umbral !== undefined && umbral !== null && document.activeElement !== DOM.sttSlider) {
        DOM.sttSlider.value = umbral;                 // no pisar el slider mientras el usuario lo arrastra
    }
    if (!STATE.stt.activo) { DOM.sttNivel.style.width = '0%'; DOM.sttNivel.classList.remove('sobre'); }
}

function evento_stt_estado(ev) {
    const previo = STATE.stt.estado;
    poner_stt(ev);
    if (ev.estado === 'error' && previo !== 'error') agregar_mensaje('error', `Voz de entrada: ${ev.mensaje || 'falló'}`);
}

function evento_stt_nivel(ev) {
    if (!STATE.stt.activo) return;
    DOM.sttNivel.style.width = `${Math.max(0, Math.min(100, Number(ev.nivel) || 0))}%`;
    DOM.sttNivel.classList.toggle('sobre', Number(ev.nivel) >= Number(ev.umbral));
}

function evento_stt_texto(ev) {
    const texto = (ev.texto || '').trim();
    if (!texto || !STATE.stt.activo) return;
    // Mismo criterio que el teclado: si SARA está ocupada o hay una confirmación abierta, no se envía.
    // (Confirmar por voz es una capacidad futura; hoy el modal se responde con los botones.)
    if (STATE.procesando || STATE.confirmacion_pendiente || !STATE.conectado) {
        agregar_mensaje('sistema meta', `🎙️ Ignorado (SARA está ocupada), repítelo después: «${texto}»`);
        return;
    }
    enviar_texto(texto, '🎙️', 'voz');
}
