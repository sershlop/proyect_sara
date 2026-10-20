'use strict';
// SARA · puente WhatsApp (dispositivo vinculado vía Baileys). Solo escucha en 127.0.0.1 y exige token.
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const pino = require('pino');
const {
  default: makeWASocket, useMultiFileAuthState, DisconnectReason,
  fetchLatestBaileysVersion, Browsers, downloadMediaMessage,
} = require('@whiskeysockets/baileys');

const PORT = parseInt(process.env.WA_PORT || '8765', 10);
const TOKEN = process.env.WA_TOKEN || '';
const AUTH_DIR = process.env.WA_AUTH_DIR || path.join(__dirname, 'auth');
const MAX_BUF = 300;
const AUDIO_DIR = path.join(__dirname, 'audio_tmp');   // notas de voz descargadas (SARA-Net); se borran solas
const AUDIO_MAX_BYTES = 2 * 1024 * 1024;
let audioMax = 0;                      // segundos máximos de nota de voz a descargar; 0 = no descargar nada
const BOOT = crypto.randomBytes(4).toString('hex');
const logger = pino({ level: 'silent' });
const log = (...a) => console.log('[sara-wa]', ...a);

let sock = null, estado = 'desconectado', yo = null, seq = 0;
const inbox = [];
const enviadosMsg = new Map();         // id -> contenido enviado: WhatsApp lo pide de nuevo si tu teléfono no pudo descifrarlo
const PREFERIR_LID = process.env.WA_YO_LID === '1';   // plan B: responder a tu chat por su LID en vez de por tu número
const enviados = new Set();            // ids de mensajes que ENVIÓ este puente (anti-eco de SARA-Net)
const contactos = new Map();           // jid -> {jid, nombre (tu libreta), notify (como ellos se llaman)}
const SELF_FILE = path.join(__dirname, 'self_jids.json');   // chats "Tú" aprendidos (sobreviven reinicios)
let selfJids = new Set();
try { selfJids = new Set(JSON.parse(fs.readFileSync(SELF_FILE, 'utf8'))); } catch (_) {}
let aprenderHasta = 0;                                       // ms epoch; > ahora = modo aprender activo
const guardarSelf = () => { try { fs.writeFileSync(SELF_FILE, JSON.stringify([...selfJids])); } catch (_) {} };
const mask = (j) => { const [u, d] = String(j || '').split('@'); return u ? '…' + u.slice(-4) + '@' + (d || '?') : 'ninguno'; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const limpiarJid = (j) => String(j || '').replace(/:\d+@/, '@');
// Solo cuentan las PERSONAS (conocidas o desconocidas): número normal (@s.whatsapp.net) o LID (@lid).
// Canales (@newsletter), listas de difusión (@broadcast), estados y cualquier otro tipo NO entran al buzón de SARA.
const esPersona = (j) => /@(s\.whatsapp\.net|lid)$/.test(String(j || ''));
const ignoradosVistos = new Set();   // para registrar UNA vez por tipo (sin contenido) qué se está ignorando
const plano = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();

const CONT_FILE = path.join(__dirname, 'contactos.json');
try { for (const c of JSON.parse(fs.readFileSync(CONT_FILE, 'utf8'))) contactos.set(c.jid, c); } catch (_) {}
let contTimer = null;
function guardarContactosDiferido() {
  if (contTimer) return;
  contTimer = setTimeout(() => {
    contTimer = null;
    try { fs.writeFileSync(CONT_FILE, JSON.stringify([...contactos.values()])); } catch (_) {}
  }, 5000);
}
const nombreDe = (c) => (c.nombre || c.notify || '');
// esLibreta = nombre con el que TÚ lo guardaste; si no, es el nombre que ELLOS se pusieron (pushName).
// Antes un pushName pisaba al nombre de tu libreta: por eso no reconocía "mamá".
function guardarContacto(jid, nombre, esLibreta) {
  jid = limpiarJid(jid);
  if (!jid || !nombre || !esPersona(jid)) return;      // grupos, canales, difusión y estados no son contactos
  const c = contactos.get(jid) || { jid, nombre: '', notify: '' };
  if (esLibreta) c.nombre = String(nombre); else c.notify = String(nombre);
  contactos.set(jid, c);
  guardarContactosDiferido();
}
function registrarContacto(c) {
  for (const id of [c.id, c.lid, c.phoneNumber].filter(Boolean)) {
    guardarContacto(id, c.name || c.verifiedName, true);
    guardarContacto(id, c.notify, false);
  }
}

function textoDe(m) {
  let x = m.message || {};
  x = x.ephemeralMessage?.message || x.viewOnceMessage?.message || x;
  return x.conversation || x.extendedTextMessage?.text || x.imageMessage?.caption ||
    x.videoMessage?.caption || (x.audioMessage ? '[nota de voz]' : '') ||
    (x.imageMessage ? '[imagen]' : '') || (x.videoMessage ? '[video]' : '') ||
    (x.documentMessage ? '[documento]' : '') || (x.stickerMessage ? '[sticker]' : '');
}

function audioDe(m) {
  let x = m.message || {};
  x = x.ephemeralMessage?.message || x.viewOnceMessage?.message || x;
  return x.audioMessage || null;
}

function limpiarAudios(todos) {          // borra audios viejos (o todos): nada de voz se queda en disco
  try {
    for (const f of fs.readdirSync(AUDIO_DIR)) {
      const ruta = path.join(AUDIO_DIR, f);
      try { if (todos || Date.now() - fs.statSync(ruta).mtimeMs > 10 * 60 * 1000) fs.unlinkSync(ruta); } catch (_) {}
    }
  } catch (_) {}
}
limpiarAudios(true);

function yaRegistrado() {
  try { return !!JSON.parse(fs.readFileSync(path.join(AUTH_DIR, 'creds.json'), 'utf8')).registered; } catch (_) { return false; }
}

function descartarSocket() {
  if (!sock) return;
  try { sock.ev.removeAllListeners(); } catch (_) {}
  try { sock.end(undefined); } catch (_) {}
  sock = null;
}

async function iniciar(phone) {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();
  log('versión de WhatsApp Web:', version.join('.'), '· ya registrado:', !!state.creds.registered);
  descartarSocket();
  const s = makeWASocket({
    version, auth: state, logger, printQRInTerminal: false,
    browser: Browsers.ubuntu('Chrome'),            // OBLIGATORIO un navegador conocido para vincular con código
    markOnlineOnConnect: false, syncFullHistory: false,
    getMessage: async (key) => enviadosMsg.get(key.id) || undefined,   // sin esto el teléfono muestra "Esperando este mensaje"

  });
  sock = s;

  let pedido = false, ok, ko;
  const promesaCodigo = new Promise((a, b) => { ok = a; ko = b; });

  s.ev.on('creds.update', saveCreds);
  s.ev.on('contacts.upsert', (cs) => cs.forEach(registrarContacto));
  s.ev.on('contacts.update', (cs) => cs.forEach(registrarContacto));
  s.ev.on('messaging-history.set', ({ contacts }) => (contacts || []).forEach(registrarContacto));
  s.ev.on('messages.upsert', async ({ messages, type }) => {
    for (const m of messages) {
      try {
      const jid = limpiarJid(m.key.remoteJid);
      if (!jid || jid === 'status@broadcast' || !m.message) continue;
      if (m.key.id && enviados.has(m.key.id)) continue;   // eco de lo que envió SARA: no es una orden
      const grupo = jid.endsWith('@g.us');
      if (!grupo && !esPersona(jid)) {                  // canal de WhatsApp, lista de difusión, etc.: ni se guarda ni se avisa
        const dom = jid.split('@')[1] || '?';
        if (!ignoradosVistos.has(dom)) { ignoradosVistos.add(dom); log('ignorando chats @' + dom + ' (no son personas)'); }
        continue;
      }
      // ¿Es el chat contigo mismo? (puede llegar como número normal o como LID)
      const me = s.authState?.creds?.me || {};
      const mios = [limpiarJid(s.user?.id), limpiarJid(s.user?.lid), limpiarJid(me.id), limpiarJid(me.lid)].filter(Boolean);
      const alt = limpiarJid(m.key.remoteJidAlt);
      let propio = !grupo && !!m.key.fromMe &&
        (mios.includes(jid) || selfJids.has(jid) || (!!alt && (mios.includes(alt) || selfJids.has(alt))));
      // Modo aprender (/whatsapp net aprender): el próximo mensaje tuyo en un chat 1 a 1 marca ese chat como "Tú".
      if (!propio && !grupo && m.key.fromMe && type === 'notify' && Date.now() < aprenderHasta) {
        selfJids.add(jid); if (alt) selfJids.add(alt);
        guardarSelf(); aprenderHasta = 0; propio = true;
        log('chat propio APRENDIDO:', mask(jid), alt ? mask(alt) : '');
      }
      if (m.key.fromMe && !grupo)                       // diagnóstico SIN texto: qué tipo de jid llega
        log('msg mío', JSON.stringify({ type, jid: mask(jid), alt: alt ? mask(alt) : null, propio,
                                         conocidos: mios.length + selfJids.size }));
      // 'notify' = mensaje nuevo (comportamiento de siempre). 'append' solo si es tu chat propio.
      if (type !== 'notify' && !(type === 'append' && propio)) continue;
      const txt = textoDe(m);
      if (!txt) continue;

      // Nota de voz: SOLO en tu chat propio y solo si SARA-Net-voz está encendido (audioMax > 0).
      let audio = null, audio_error = null;
      const au = audioDe(m);
      if (au && propio && audioMax > 0) {
        limpiarAudios(false);
        if ((Number(au.seconds) || 0) > audioMax) audio_error = 'larga';
        else {
          try {
            const buf = await downloadMediaMessage(m, 'buffer', {}, { logger, reuploadRequest: s.updateMediaMessage });
            if (!buf || !buf.length) audio_error = 'descarga';
            else if (buf.length > AUDIO_MAX_BYTES) audio_error = 'larga';
            else {
              fs.mkdirSync(AUDIO_DIR, { recursive: true });
              audio = path.join(AUDIO_DIR, String(m.key.id || Date.now()).replace(/[^A-Za-z0-9]/g, '') + '.ogg');
              fs.writeFileSync(audio, buf);
            }
          } catch (e) { audio_error = 'descarga'; log('nota de voz no descargada:', String((e && e.message) || e)); }
        }
      }

      if (!grupo && m.pushName && !m.key.fromMe) guardarContacto(jid, m.pushName, false);
      const nombre = (grupo ? m.pushName : (nombreDe(contactos.get(jid) || {}) || m.pushName)) || jid.split('@')[0];
      inbox.push({ id: ++seq, wid: m.key.id || null, jid, nombre, texto: txt,
                   ts: Number(m.messageTimestamp) || Math.floor(Date.now() / 1000),
                   grupo, mia: !!m.key.fromMe, propio, audio, audio_error });
      if (inbox.length > MAX_BUF) inbox.shift();
      } catch (e) { log('error procesando un mensaje entrante:', String((e && e.message) || e)); }
    }
  });

  s.ev.on('connection.update', async (u) => {
    if (s !== sock) return;                              // evento de un socket descartado
    const code = u.lastDisconnect?.error?.output?.statusCode;
    log('connection.update', JSON.stringify({ connection: u.connection || null, qr: !!u.qr,
        code: code || null, msg: u.lastDisconnect?.error?.message || null }));

    // El socket avisa con "qr" que ya puede vincularse: es el momento correcto de pedir el código.
    if (u.qr && phone && !state.creds.registered && !pedido) {
      pedido = true;
      try {
        const c = await s.requestPairingCode(phone);
        estado = 'esperando_codigo';
        log('código de enlace generado para', phone);
        ok(c);
      } catch (e) { ko(e); }
    }
    if (u.connection === 'open') {
      estado = 'abierto';
      yo = limpiarJid(s.user?.id);
      setTimeout(async () => {                           // pide a WhatsApp los nombres de tu libreta (una vez por conexión)
        try { await s.resyncAppState(['critical_unblock_low'], true); log('resync de contactos pedido'); }
        catch (e) { log('resync de contactos no disponible:', String((e && e.message) || e)); }
      }, 8000);
      log('✅ enlazado como', yo, '· id:', mask(s.user?.id), '· lid:', mask(s.user?.lid), '· aprendidos:', selfJids.size);
    } else if (u.connection === 'close') {
      if (code === DisconnectReason.loggedOut) {
        estado = 'desconectado'; yo = null;
        selfJids.clear(); try { fs.unlinkSync(SELF_FILE); } catch (_) {}
        try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch (_) {}
      } else if (!state.creds.registered && code !== DisconnectReason.restartRequired) {
        estado = 'desconectado';                         // vinculación fallida: no se reintenta en bucle
        ko(new Error('WhatsApp cerró la conexión (código ' + code + ')'));
      } else {                                           // incluye 515 (reinicio normal tras vincular)
        estado = 'reconectando';
        await sleep(2000);
        if (s === sock) iniciar().catch((e) => { estado = 'error'; log('error al reconectar', e.message); });
      }
    }
  });

  if (state.creds.registered || !phone) return null;
  return Promise.race([promesaCodigo,
    new Promise((_, rej) => setTimeout(() => rej(new Error('WhatsApp no respondió para generar el código')), 30000))]);
}

async function aJid(to) {
  if (to === 'yo' && PREFERIR_LID) { const l = [...selfJids].find((j) => j.endsWith('@lid')); if (l) return l; }
  if (to === 'yo') { if (!yo) throw new Error('aún no sé cuál es tu número'); return yo; }
  if (String(to).includes('@')) return limpiarJid(to);
  const dig = String(to).replace(/\D/g, '');
  const r = await sock.onWhatsApp(dig);
  if (!r || !r[0] || !r[0].exists) throw new Error('ese número no tiene WhatsApp');
  return r[0].jid;
}

function leer(req) {
  return new Promise((ok) => {
    let b = ''; req.on('data', (d) => (b += d));
    req.on('end', () => { try { ok(JSON.parse(b || '{}')); } catch { ok({}); } });
  });
}

http.createServer(async (req, res) => {
  const out = (c, o) => { res.writeHead(c, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(o)); };
  if (TOKEN && req.headers['x-sara-token'] !== TOKEN) return out(401, { error: 'token' });
  const url = new URL(req.url, 'http://x');
  const body = req.method === 'POST' ? await leer(req) : {};
  try {
    switch (url.pathname) {
      case '/status': return out(200, { estado, yo, lid: limpiarJid(sock?.user?.lid) || null, boot: BOOT, contactos: contactos.size,
                                        libreta: [...contactos.values()].filter((c) => c.nombre).length,
                                        aprendidos: selfJids.size, aprendiendo: Date.now() < aprenderHasta });
      case '/aprender': {
        const seg = Math.min(300, Math.max(10, parseInt(body.segundos || '90', 10) || 90));
        aprenderHasta = Date.now() + seg * 1000;
        log('modo aprender activo', seg, 's');
        return out(200, { ok: true, segundos: seg });
      }
      case '/connect': {
        if (sock && (estado === 'abierto' || estado === 'reconectando'))
          return out(200, { estado, yo, code: null });
        if (sock) {                                      // intento anterior sin completar: se descarta por completo
          descartarSocket();
          if (!yaRegistrado()) { try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch (_) {} }
        }
        const phone = String(body.phone || '').replace(/\D/g, '') || undefined;
        const code = await iniciar(phone);
        return out(200, { estado, code });
      }
      case '/send': {
        if (estado !== 'abierto') return out(200, { ok: false, error: 'WhatsApp no está conectado' });
        const jid = await aJid(body.to);
        const env = await sock.sendMessage(jid, { text: String(body.text || '').slice(0, 3000) });
        if (env?.key?.id) {
          enviados.add(env.key.id);
          if (env.message) { enviadosMsg.set(env.key.id, env.message); if (enviadosMsg.size > 300) enviadosMsg.delete(enviadosMsg.keys().next().value); }
          if (enviados.size > 300) enviados.delete(enviados.values().next().value);
        }
        return out(200, { ok: true, jid });
      }
      case '/messages': {
        if (url.searchParams.has('audio')) audioMax = Math.max(0, parseInt(url.searchParams.get('audio') || '0', 10) || 0);
        const since = parseInt(url.searchParams.get('since') || '0', 10);
        const lim = parseInt(url.searchParams.get('limit') || '50', 10);
        return out(200, { boot: BOOT, mensajes: inbox.filter((m) => m.id > since).slice(-lim) });
      }
      case '/contacts': {
        const toks = plano(url.searchParams.get('q')).split(/\s+/).filter(Boolean);
        const lista = [...contactos.values()]
          .filter((c) => { const n = plano(c.nombre + ' ' + c.notify); return toks.length && toks.every((t) => n.includes(t)); })
          .sort((a, b) => (b.nombre ? 1 : 0) - (a.nombre ? 1 : 0))      // primero los de TU libreta
          .slice(0, Math.min(50, Math.max(1, parseInt(url.searchParams.get('limit') || '8', 10) || 8)))   // ?limit= (SARA pide 40: cada persona sale como número y como @lid)
          .map((c) => ({ jid: c.jid, nombre: nombreDe(c), libreta: !!c.nombre }));
        return out(200, { contactos: lista });
      }
      case '/logout': {
        try { if (sock) await sock.logout(); } catch (_) {}
        descartarSocket();
        try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch (_) {}
        estado = 'desconectado'; yo = null;
        selfJids.clear(); aprenderHasta = 0;
        try { fs.unlinkSync(SELF_FILE); } catch (_) {}
        return out(200, { ok: true });
      }
      default: return out(404, { error: 'no existe' });
    }
  } catch (e) { return out(500, { error: String((e && e.message) || e) }); }
}).listen(PORT, '127.0.0.1', () => log(`escuchando en 127.0.0.1:${PORT} boot=${BOOT}`));

process.on('unhandledRejection', (e) => console.error('unhandledRejection', e));
