// Operator console.

import { toast as showToast, closeSheet, wireSheet, copyText, canCopy } from './ui.js';

const $ = (id) => document.getElementById(id);
const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';

const A = {
  settings: null,
  catalog: {},
  publicUrl: '',
  files: [],
  devices: [],
  status: null,
  ws: null,
  logSeq: 0,
  rows: new Map(),
  rowsKey: '',
  level: -90,
  shownLevel: -90,
  peak: -90,
  peakAt: 0,
};

// ------------------------------------------------------------------ helpers

const csNames = (() => {
  try { return new Intl.DisplayNames(['cs'], { type: 'language' }); } catch { return null; }
})();
function langCs(code) {
  const n = (csNames && csNames.of(code)) || A.catalog[code] || code;
  return n.charAt(0).toLocaleUpperCase('cs') + n.slice(1);
}
function codeLabel(code) { return code.split('-')[0].toUpperCase(); }

function plural(n, one, few, many) {
  return n === 1 ? one : n >= 2 && n <= 4 ? few : many;
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401 && !path.endsWith('/login')) { showLogin(); throw new Error('Nejste přihlášeni.'); }
  if (!res.ok) throw new Error((data && data.error) || `Chyba ${res.status}`);
  return data;
}

function toast(text, error = false) {
  showToast(text, { error, ms: error ? 5000 : 2200 });
}

/** Styled replacement for confirm(): resolves true when the operator confirms. */
function ask({ title, body, ok, cancel = 'Zrušit' }) {
  const dlg = $('confirm');
  $('confirmTitle').textContent = title;
  $('confirmBody').textContent = body;
  $('confirmOk').textContent = ok;
  $('confirmCancel').textContent = cancel;
  dlg.returnValue = '';
  dlg.showModal();
  $('confirmCancel').focus();
  return new Promise((resolve) => {
    dlg.addEventListener('close', () => resolve(dlg.returnValue === 'ok'), { once: true });
  });
}
wireSheet($('confirm'));
$('confirmOk').addEventListener('click', () => closeSheet($('confirm'), 'ok'));

const DB_MIN = -60;
const pct = (db) => Math.max(0, Math.min(100, ((db - DB_MIN) / -DB_MIN) * 100));

// ------------------------------------------------------------------ login

function showLogin() {
  $('console').hidden = true;
  $('login').hidden = false;
  if (A.ws) { A.ws.onclose = null; A.ws.close(); A.ws = null; }
  $('password').focus();
}

$('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const err = $('loginError');
  const btn = $('loginBtn');
  err.hidden = true;
  btn.disabled = true;
  btn.prepend(Object.assign(document.createElement('span'), { className: 'spinner' }));
  try {
    await api('POST', '/api/admin/login', { password: $('password').value });
    $('password').value = '';
    await enterConsole();
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
    $('password').select();
  } finally {
    btn.disabled = false;
    btn.querySelector('.spinner')?.remove();
  }
});

$('reveal').addEventListener('click', () => {
  const pw = $('password');
  const show = pw.type === 'password';
  pw.type = show ? 'text' : 'password';
  $('reveal').setAttribute('aria-pressed', String(show));
  $('reveal').setAttribute('aria-label', show ? 'Skrýt heslo' : 'Zobrazit heslo');
  $('reveal').title = show ? 'Skrýt heslo' : 'Zobrazit heslo';
  pw.focus();
});

$('logout').addEventListener('click', async () => {
  try { await api('POST', '/api/admin/logout'); } catch { /* ignore */ }
  stopIngest();
  showLogin();
});

async function enterConsole() {
  const cfg = await api('GET', '/api/admin/config');
  A.catalog = cfg.catalog;
  A.files = cfg.files || [];
  applySettings(cfg.settings, cfg.publicUrl);
  $('login').hidden = true;
  $('console').hidden = false;
  connectStatus();
  loadDevices(false);
  if (A.settings.source === 'browser') listMics();
}

// ------------------------------------------------------------------ settings form

function fieldValue(input) {
  if (input.type === 'checkbox') return input.checked;
  if (input.dataset.type === 'int') return parseInt(input.value, 10);
  if (input.dataset.type === 'float') return parseFloat(input.value);
  return input.value;
}

function fillFields() {
  const s = A.settings;
  for (const input of document.querySelectorAll('[data-key]')) {
    const key = input.dataset.key;
    if (key === 'api_key' || document.activeElement === input) continue;
    if (key === 'device' || key === 'channel' || key === 'file_path') continue;
    if (input.type === 'checkbox') input.checked = !!s[key];
    else input.value = s[key] ?? '';
  }
  updateOutputs();
}

function updateOutputs() {
  $('gainOut').textContent = `${(+$('gain_db').value > 0 ? '+' : '')}${$('gain_db').value} dB`;
  $('thrOut').textContent = `${$('gate_threshold_db').value} dB`;
  $('holdOut').textContent = `${(+$('gate_hold_ms').value / 1000).toFixed(1)} s`;
  const idle = +$('idle_stop_s').value;
  $('idleOut').textContent = idle === 0 ? 'ihned' : idle < 60 ? `${idle} s` : `${(idle / 60).toFixed(idle % 60 ? 1 : 0)} min`;
  $('meterThr').style.left = pct(+$('gate_threshold_db').value) + '%';
  $('gateFields').setAttribute('aria-disabled', String(!$('gate_enabled').checked));
  for (const r of document.querySelectorAll('input[type="range"]')) {
    const min = +r.min || 0, max = +r.max || 100;
    r.style.setProperty('--fill', `${((+r.value - min) / (max - min)) * 100}%`);
  }
}

function applySettings(settings, publicUrl) {
  A.settings = settings;
  if (publicUrl) A.publicUrl = publicUrl;
  fillFields();

  const ks = $('keyState');
  ks.textContent = settings.api_key_env ? 'nastaven v proměnné prostředí GEMINI_API_KEY'
    : settings.api_key_set ? `uložen ${settings.api_key_hint}` : 'chybí';
  $('api_key').disabled = settings.api_key_env;
  $('api_key').placeholder = settings.api_key_set ? 'Pro změnu vložte nový klíč' : 'Vložte klíč z Google AI Studia';

  for (const p of document.querySelectorAll('.src-panel')) p.hidden = p.dataset.src !== settings.source;
  $('secureHint').hidden = window.isSecureContext;
  if (settings.source !== 'browser') stopIngest();

  fillFiles();
  fillDevices();
  fillAddLang();
  renderInvite();
  for (const [code, row] of A.rows) row.always.checked = settings.always_on.includes(code);
  $('brandTitle').textContent = settings.event_title || 'Řízení překladu';
}

async function save(patch, okText = 'Uloženo') {
  try {
    const res = await api('PUT', '/api/admin/config', patch);
    applySettings(res.settings, res.publicUrl);
    if (okText) toast(okText);
    return true;
  } catch (e) {
    toast(e.message, true);
    fillFields();
    return false;
  }
}

document.addEventListener('input', (e) => {
  if (e.target.type === 'range') updateOutputs();
});

document.addEventListener('change', async (e) => {
  const input = e.target;
  const key = input.dataset && input.dataset.key;
  if (!key || !A.settings) return;
  if (input.id === 'gate_enabled') updateOutputs();
  const value = fieldValue(input);
  if (key === 'api_key') {
    if (!value.trim()) return;
    if (await save({ api_key: value.trim() }, 'API klíč uložen')) input.value = '';
    return;
  }
  if (key === 'device') {
    await save({ device: value, channel: 0 });
    return;
  }
  await save({ [key]: value });
  if (key === 'source') {
    if (value === 'device') loadDevices(false);
    if (value === 'browser') listMics();
  }
});

// ------------------------------------------------------------------ devices & files

async function loadDevices(refresh) {
  try {
    const res = await api('GET', '/api/admin/devices' + (refresh ? '?refresh=1' : ''));
    A.devices = res.devices || [];
    fillDevices();
    if (refresh) toast(A.devices.length ? `Nalezeno ${A.devices.length} vstupů` : 'Server nevidí žádnou zvukovou kartu', !A.devices.length);
  } catch (e) { toast(e.message, true); }
}

function fillDevices() {
  if (!A.settings) return;
  const sel = $('device');
  const current = A.settings.device;
  sel.textContent = '';
  sel.append(new Option('Výchozí vstup systému', ''));
  let found = !current;
  for (const d of A.devices) {
    const label = `${d.name} (${d.channels} ${plural(d.channels, 'kanál', 'kanály', 'kanálů')}${d.api ? ', ' + d.api : ''})`;
    sel.append(new Option(label, d.name));
    if (d.name === current) found = true;
  }
  if (!found) sel.append(new Option(`${current} (nenalezeno)`, current));
  sel.value = current;

  const dev = A.devices.find((d) => d.name === current) || A.devices.find((d) => d.default);
  const n = Math.max(1, dev ? dev.channels : 2, A.settings.channel + 1);
  const ch = $('channel');
  ch.textContent = '';
  for (let i = 0; i < n; i++) ch.append(new Option(String(i + 1), String(i)));
  ch.value = String(A.settings.channel);
}

function fillFiles() {
  const sel = $('file_path');
  const current = A.settings.file_path;
  sel.textContent = '';
  sel.append(new Option(A.files.length ? 'Vyberte soubor' : 'Ve složce data není žádný WAV', ''));
  for (const f of A.files) sel.append(new Option(f, f));
  if (current && !A.files.includes(current)) sel.append(new Option(`${current} (nenalezeno)`, current));
  sel.value = current;
}

$('refreshDevices').addEventListener('click', () => loadDevices(true));

// ------------------------------------------------------------------ languages

function fillAddLang() {
  const sel = $('addLang');
  const used = new Set(A.settings.languages);
  const codes = Object.keys(A.catalog).filter((c) => !used.has(c))
    .sort((a, b) => langCs(a).localeCompare(langCs(b), 'cs'));
  sel.textContent = '';
  for (const c of codes) sel.append(new Option(`${langCs(c)} (${c})`, c));
  $('addLangBtn').disabled = !codes.length;
}

$('addLangBtn').addEventListener('click', () => {
  const code = $('addLang').value;
  if (code) save({ languages: [...A.settings.languages, code] }, `${langCs(code)} přidána`);
});

function buildRows() {
  const list = $('channels');
  const langs = A.settings.languages;
  const key = langs.join(',');
  if (key === A.rowsKey) return;
  A.rowsKey = key;
  list.textContent = '';
  A.rows.clear();
  if (!langs.length) {
    const li = document.createElement('li');
    li.className = 'hint';
    li.textContent = 'Přidejte alespoň jeden jazyk, do kterého se má překládat.';
    list.append(li);
    return;
  }
  for (const code of langs) {
    const li = document.createElement('li');
    li.className = 'ch';
    li.dataset.lang = code;
    li.innerHTML = `
      <span class="ch-code"></span>
      <span class="ch-main"><span class="ch-name"></span><span class="ch-meta"></span></span>
      <span class="ch-count"><b>0</b><span>posluchačů</span></span>
      <span class="ch-actions">
        <label title="Relace běží stále, i bez posluchačů. Rychlejší start, ale platíte i za ticho v sále."><input type="checkbox" class="switch" data-always> Stále zapnuto</label>
        <button type="button" class="icon-btn" data-remove>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>
        </button>
      </span>`;
    li.querySelector('.ch-code').textContent = codeLabel(code);
    li.querySelector('.ch-name').textContent = `${langCs(code)} (${code})`;
    li.querySelector('[data-remove]').setAttribute('aria-label', `Odebrat ${langCs(code)}`);
    const always = li.querySelector('[data-always]');
    always.checked = A.settings.always_on.includes(code);
    always.setAttribute('aria-label', `${langCs(code)}: stále zapnuto`);
    A.rows.set(code, {
      li, meta: li.querySelector('.ch-meta'), count: li.querySelector('.ch-count b'),
      countLabel: li.querySelector('.ch-count span'), always,
    });
    list.append(li);
  }
}

$('channels').addEventListener('change', (e) => {
  if (!e.target.matches('[data-always]')) return;
  e.stopPropagation();
  const code = e.target.closest('.ch').dataset.lang;
  const set = new Set(A.settings.always_on);
  if (e.target.checked) set.add(code); else set.delete(code);
  save({ always_on: [...set] });
});

$('channels').addEventListener('click', async (e) => {
  const b = e.target.closest('[data-remove]');
  if (!b) return;
  const code = b.closest('.ch').dataset.lang;
  const ch = A.status && A.status.channels.find((c) => c.lang === code);
  const people = ch ? ch.listeners + ch.streams : 0;
  if (people && !await ask({
    title: 'Odebrat jazyk?',
    body: `${langCs(code)} právě ${plural(people, 'poslouchá', 'poslouchají', 'poslouchá')} ${people} ${plural(people, 'člověk', 'lidé', 'lidí')}. Překlad jim okamžitě skončí.`,
    ok: 'Odebrat jazyk',
  })) return;
  save({ languages: A.settings.languages.filter((c) => c !== code) }, `${langCs(code)} odebrána`);
});

const CH_STATUS = {
  idle: 'Nečinný',
  connecting: 'Připojuje se ke Gemini',
  live: 'Překládá',
  reconnecting: 'Obnovuje spojení',
  error: 'Chyba',
};

function renderChannels(st) {
  buildRows();
  for (const ch of st.channels) {
    const row = A.rows.get(ch.lang);
    if (!row) continue;
    row.li.dataset.status = ch.status;
    let text = ch.status === 'idle' && st.onAir ? 'Spustí se s prvním posluchačem' : CH_STATUS[ch.status] || ch.status;
    if (ch.status === 'live') {
      const bits = [];
      if (ch.detected) bits.push(`řečník mluví: ${langCs(ch.detected).toLocaleLowerCase("cs")}`);
      if (ch.audioMinutes) bits.push(`${ch.audioMinutes} min překladu`);
      if (ch.stopping) bits.push('bez posluchačů, brzy se ukončí');
      if (bits.length) text += ', ' + bits.join(', ');
    }
    if (ch.error && ch.status !== 'live') text += `: ${ch.error}`;
    row.meta.textContent = text;
    row.meta.classList.toggle('err', ch.status === 'error');
    const people = ch.listeners;
    row.count.textContent = String(people);
    row.countLabel.textContent = plural(people, 'posluchač', 'posluchači', 'posluchačů')
      + (ch.streams ? `, ${ch.streams} na pozadí` : '');
  }
}

// ------------------------------------------------------------------ invite

function renderInvite() {
  const url = A.settings.public_url || A.publicUrl;
  $('publicUrlText').textContent = url;
  const src = '/api/qr.svg?u=' + encodeURIComponent(url);
  if ($('qr').getAttribute('src') !== src) $('qr').src = src;
  $('localhostWarn').hidden = !/\/\/(localhost|127\.|\[::1\])/.test(url);
  $('qrFullTitle').textContent = A.settings.event_title;
  $('qrFullUrl').textContent = url.replace(/^https?:\/\//, '').replace(/\/$/, '');
}

$('qrFullBtn').addEventListener('click', () => {
  $('qrFullImg').src = $('qr').src;
  $('qrFull').hidden = false;
  document.documentElement.requestFullscreen?.().catch(() => {});
});
function closeQrFull() {
  if ($('qrFull').hidden) return;
  $('qrFull').hidden = true;
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}
$('qrFull').addEventListener('click', closeQrFull);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeQrFull(); });
document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement) closeQrFull(); });

$('copyUrlBtn').hidden = !canCopy();
$('copyUrlBtn').addEventListener('click', async () => {
  if (await copyText($('publicUrlText').textContent)) toast('Adresa zkopírována');
  else toast('Adresu se nepodařilo zkopírovat', true);
});

$('clearBtn').addEventListener('click', async () => {
  if (!await ask({
    title: 'Vymazat přepis?',
    body: 'Posluchačům zmizí dosavadní text ve všech jazycích. Probíhající překlad pokračuje dál.',
    ok: 'Vymazat přepis',
  })) return;
  try { await api('POST', '/api/admin/clear'); toast('Přepis vymazán'); } catch (e) { toast(e.message, true); }
});

// ------------------------------------------------------------------ on air

$('onairBtn').addEventListener('click', async () => {
  if (!A.settings.on_air) { save({ on_air: true }, 'Vysílání zahájeno'); return; }
  if (await ask({
    title: 'Ukončit vysílání?',
    body: 'Posluchači přestanou dostávat překlad a všechna spojení s Gemini se zavřou.',
    ok: 'Ukončit vysílání',
    cancel: 'Pokračovat ve vysílání',
  })) await save({ on_air: false }, 'Vysílání ukončeno');
});

function renderHero(st) {
  const on = st.onAir;
  const hero = $('hero');
  hero.dataset.on = String(on);
  const liveLangs = st.channels.filter((c) => c.status === 'live').length;
  const listeners = st.clients;
  $('heroState').textContent = on ? 'Vysílá se' : 'Nevysílá se';
  let sub;
  if (!on) sub = 'Posluchači vidí, že vysílání ještě nezačalo. Gemini se nevolá.';
  else if (st.input.state === 'error' || st.input.kind === 'none') sub = 'Vysílání běží, ale chybí vstupní signál.';
  else if (!liveLangs) sub = 'Vysílání běží. Překlad se spustí, jakmile se připojí první posluchač.';
  else sub = st.input.sending ? 'Signál se právě překládá.' : 'Překlad je připraven a čeká na řeč.';
  $('heroSub').textContent = sub;
  $('statListeners').textContent = String(listeners);
  $('statLangs').textContent = String(liveLangs);
  $('statListenersLabel').textContent = plural(listeners, 'posluchač', 'posluchači', 'posluchačů');
  $('statLangsLabel').textContent = plural(liveLangs, 'jazyk překládá', 'jazyky překládají', 'jazyků překládá');
  const btn = $('onairBtn');
  $('onairText').textContent = on ? 'Ukončit vysílání' : 'Zahájit vysílání';
  btn.classList.toggle('primary', !on);
  btn.classList.toggle('danger', on);
}

// ------------------------------------------------------------------ signal

function renderSignal(st) {
  const inp = st.input;
  A.level = inp.db;
  if (inp.peak >= A.peak || performance.now() - A.peakAt > 1500) { A.peak = inp.peak; A.peakAt = performance.now(); }
  const chip = $('gateChip');
  let text, tone;
  if (inp.kind === 'none') { text = 'Zdroj vypnut'; tone = 'idle'; }
  else if (inp.state === 'error') { text = 'Chyba vstupu'; tone = 'warn'; }
  else if (inp.state === 'waiting') { text = 'Čeká na prohlížeč'; tone = 'idle'; }
  else if (inp.sending) { text = 'Posílá se k překladu'; tone = 'on'; }
  else if (inp.gate && st.onAir) { text = 'Signál je, žádný jazyk neběží'; tone = 'ok'; }
  else if (inp.gate) { text = 'Signál je'; tone = 'ok'; }
  else { text = 'Ticho, nic se neposílá'; tone = 'idle'; }
  chip.textContent = text;
  chip.dataset.tone = tone;
  $('srcDetail').textContent = inp.detail || '';
  $('srcError').hidden = !inp.error;
  $('srcError').textContent = inp.error || '';
}

function animateMeter() {
  const target = A.level;
  A.shownLevel = target > A.shownLevel ? target : Math.max(target, A.shownLevel - 1.2);
  $('meterFill').style.clipPath = `inset(0 ${100 - pct(A.shownLevel)}% 0 0)`;
  $('meterPeak').style.left = `calc(${pct(A.peak)}% - 1px)`;
  requestAnimationFrame(animateMeter);
}
requestAnimationFrame(animateMeter);

// ------------------------------------------------------------------ log

function appendLog(entries) {
  const list = $('log');
  const pinned = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  for (const r of entries) {
    const li = document.createElement('li');
    li.className = r.lvl;
    const time = document.createElement('time');
    time.textContent = new Date(r.t * 1000).toLocaleTimeString('cs-CZ');
    const span = document.createElement('span');
    span.textContent = r.msg;
    li.append(time, span);
    list.append(li);
  }
  while (list.childElementCount > 400) list.firstElementChild.remove();
  if (pinned) list.scrollTop = list.scrollHeight;
}

// ------------------------------------------------------------------ status socket

function connectStatus() {
  if (A.ws) return;
  const ws = new WebSocket(proto + location.host + '/ws/admin');
  A.ws = ws;
  ws.onopen = () => { $('conn').dataset.ok = 'true'; $('connText').textContent = 'Připojeno'; };
  ws.onmessage = (e) => {
    const st = JSON.parse(e.data);
    A.status = st;
    if (A.settings && st.onAir !== A.settings.on_air) A.settings.on_air = st.onAir;
    renderHero(st);
    renderSignal(st);
    renderChannels(st);
    if (st.log) appendLog(st.log);
  };
  ws.onclose = async () => {
    if (A.ws !== ws) return;
    A.ws = null;
    $('conn').dataset.ok = 'false';
    $('connText').textContent = 'Bez spojení se serverem';
    try {
      const s = await api('GET', '/api/admin/session');
      if (!s.authed) { showLogin(); return; }
    } catch { /* server down: keep retrying */ }
    setTimeout(connectStatus, 1500);
  };
}

// ------------------------------------------------------------------ browser ingest

const ING = { active: false, ctx: null, stream: null, node: null, ws: null, wake: null };

async function listMics() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
  const sel = $('micDevice');
  const keep = sel.value || localStorage.getItem('lt.admin.mic') || '';
  let devices = [];
  try { devices = await navigator.mediaDevices.enumerateDevices(); } catch { return; }
  sel.textContent = '';
  sel.append(new Option('Výchozí vstup', ''));
  let i = 0;
  for (const d of devices) {
    if (d.kind !== 'audioinput' || !d.deviceId || d.deviceId === 'default' || d.deviceId === 'communications') continue;
    sel.append(new Option(d.label || `Vstup ${++i}`, d.deviceId));
  }
  sel.value = [...sel.options].some((o) => o.value === keep) ? keep : '';
}

function setIngestUi() {
  const b = $('ingestBtn');
  $('ingestText').textContent = ING.active ? 'Přestat posílat zvuk' : 'Začít posílat zvuk';
  b.classList.toggle('primary', !ING.active);
  b.classList.toggle('danger', ING.active);
}

function openIngestSocket() {
  const track = ING.stream && ING.stream.getAudioTracks()[0];
  const label = track ? track.label : '';
  const ws = new WebSocket(`${proto}${location.host}/ws/ingest?label=${encodeURIComponent(label)}`);
  ws.binaryType = 'arraybuffer';
  ING.ws = ws;
  ws.onclose = (e) => {
    if (ING.ws !== ws) return;
    ING.ws = null;
    if (e.code === 4409) { toast(e.reason || 'Zdroj zvuku není nastaven na prohlížeč.', true); stopIngest(); return; }
    if (ING.active) setTimeout(() => { if (ING.active && !ING.ws) openIngestSocket(); }, 1500);
  };
}

async function startIngest() {
  if (!window.isSecureContext || !navigator.mediaDevices) {
    toast('Prohlížeč pustí zvukový vstup jen přes https nebo na localhost.', true);
    return;
  }
  const deviceId = $('micDevice').value;
  // Mixer line input must stay untouched; a room mic needs the browser's echo
  // canceller, otherwise translated audio from the speakers loops back in.
  const room = $('micRoom').checked;
  try {
    ING.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: deviceId ? { exact: deviceId } : undefined,
        channelCount: room ? { ideal: 1 } : { ideal: 2 },
        echoCancellation: room,
        noiseSuppression: room,
        autoGainControl: room,
      },
    });
    await listMics();
    if (deviceId) $('micDevice').value = deviceId;
    ING.ctx = new AudioContext({ latencyHint: 'interactive' });
    await ING.ctx.audioWorklet.addModule('/js/ingest-worklet.js');
    const src = ING.ctx.createMediaStreamSource(ING.stream);
    ING.node = new AudioWorkletNode(ING.ctx, 'ingest', {
      numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
      channelCount: 2, channelCountMode: 'explicit', channelInterpretation: 'discrete',
      // A processed (room) stream is mono: read channel 1 instead of halving it in a mix.
      processorOptions: { channel: room ? 0 : parseInt($('micChannel').value, 10) },
    });
    const mute = ING.ctx.createGain();
    mute.gain.value = 0;
    src.connect(ING.node);
    ING.node.connect(mute);
    mute.connect(ING.ctx.destination);
    ING.node.port.onmessage = (e) => {
      const ws = ING.ws;
      if (ws && ws.readyState === 1 && ws.bufferedAmount < 256 * 1024) ws.send(e.data);
    };
    if (ING.ctx.state !== 'running') await ING.ctx.resume();
    ING.active = true;
    openIngestSocket();
    try { ING.wake = await navigator.wakeLock?.request('screen'); } catch { /* optional */ }
    toast('Zvuk se posílá na server');
  } catch (e) {
    stopIngest();
    toast(e.name === 'NotAllowedError' ? 'Prohlížeč nepovolil přístup ke zvukovému vstupu.' : `Vstup nelze spustit: ${e.message}`, true);
  }
  setIngestUi();
}

function stopIngest() {
  ING.active = false;
  const ws = ING.ws;
  ING.ws = null;
  try { ws && ws.close(); } catch { /* ignore */ }
  try { ING.node && ING.node.disconnect(); } catch { /* ignore */ }
  try { ING.ctx && ING.ctx.close(); } catch { /* ignore */ }
  if (ING.stream) for (const t of ING.stream.getTracks()) t.stop();
  try { ING.wake && ING.wake.release(); } catch { /* ignore */ }
  ING.ctx = ING.node = ING.stream = ING.wake = null;
  setIngestUi();
}

$('ingestBtn').addEventListener('click', () => (ING.active ? stopIngest() : startIngest()));
$('micDevice').addEventListener('change', () => {
  localStorage.setItem('lt.admin.mic', $('micDevice').value);
  if (ING.active) { stopIngest(); startIngest(); }
});
try { $('micRoom').checked = localStorage.getItem('lt.admin.micRoom') === '1'; } catch { /* private mode */ }
$('micChannel').disabled = $('micRoom').checked;
$('micRoom').addEventListener('change', () => {
  $('micChannel').disabled = $('micRoom').checked;
  try { localStorage.setItem('lt.admin.micRoom', $('micRoom').checked ? '1' : '0'); } catch { /* private mode */ }
  if (ING.active) { stopIngest(); startIngest(); }
});
$('micChannel').addEventListener('change', () => {
  if (ING.node && !$('micRoom').checked) ING.node.port.postMessage({ channel: parseInt($('micChannel').value, 10) });
});
document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState === 'visible' && ING.active && !ING.wake) {
    try { ING.wake = await navigator.wakeLock?.request('screen'); } catch { /* optional */ }
  }
});
window.addEventListener('beforeunload', (e) => {
  if (ING.active) { e.preventDefault(); e.returnValue = ''; }
});

// ------------------------------------------------------------------ boot

(async () => {
  try {
    const s = await api('GET', '/api/admin/session');
    if (s.authed) await enterConsole();
    else showLogin();
  } catch {
    showLogin();
  }
})();
