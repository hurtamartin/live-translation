import { t, nativeName, localName, codeParts, uiLang } from './i18n.js';
import { LivePlayer, StreamPlayer } from './player.js';
import { toast as showToast, closeSheet, wireSheet, copyText, canCopy, haptic } from './ui.js';

// ------------------------------------------------------------------ storage

const store = {
  get(k, d = null) { try { const v = localStorage.getItem('lt.' + k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem('lt.' + k, String(v)); } catch { /* private mode */ } },
};

const $ = (id) => document.getElementById(id);
const el = {
  title: $('title'), onair: $('onair'), onairText: $('onairText'), banner: $('banner'), bannerText: $('bannerText'),
  feed: $('feed'), feedInner: $('feedInner'), empty: $('empty'), emptyTitle: $('emptyTitle'), emptyBody: $('emptyBody'),
  retryBtn: $('retryBtn'),
  jump: $('jump'), jumpText: $('jumpText'), dock: $('dock'),
  channelBtn: $('channelBtn'), chCode: $('chCode'), chNative: $('chNative'), chSub: $('chSub'),
  play: $('play'), playLabel: $('playLabel'), settingsBtn: $('settingsBtn'), settingsLabel: $('settingsLabel'),
  langSheet: $('langSheet'), langTitle: $('langTitle'), langHint: $('langHint'), langList: $('langList'),
  settingsSheet: $('settingsSheet'), sizes: $('sizes'), sizePreview: $('sizePreview'), showSrc: $('showSrc'), bgMode: $('bgMode'),
  keepAwake: $('keepAwake'), awakeRow: $('awakeRow'), themes: $('themes'),
  qr: $('qr'), shareUrl: $('shareUrl'), shareBtn: $('shareBtn'), copyBtn: $('copyBtn'),
  installGroup: $('installGroup'), installBtn: $('installBtn'), installIos: $('installIos'),
};

const SIZES = ['1.05rem', '1.3rem', '1.6rem', '1.95rem', '2.4rem'];
const CONNECT_TIMEOUT = 6000;   // a server that is switched off often never refuses: do not hang

function readCachedInfo() {
  try { const v = JSON.parse(store.get('info', 'null')); return v && typeof v === 'object' ? v : null; } catch { return null; }
}

const S = {
  info: null,
  cached: readCachedInfo(),   // title and languages from the last visit, for offline starts
  lang: store.get('lang'),
  chStatus: 'idle',
  connected: false,
  everConnected: false,
  ws: null,
  fails: 0,                   // failed attempts since the last successful connection
  retryTimer: 0,
  probing: false,             // a retry the listener asked for is in flight
  lastMsg: 0,
  emptyState: null,
  voice: -1,
  keyboard: false,
  segs: new Map(),
  nowSeg: null,
  player: null,
  mode: null,            // 'live' | 'stream'
  audioState: 'idle',    // idle | buffering | playing | paused
  liveOk: false,
  autoPicked: false,
  wakeLock: null,
  installEvent: null,
};

// ------------------------------------------------------------------ static text

function applyStaticText() {
  el.langTitle.textContent = t('chooseLang');
  el.langHint.textContent = t('chooseHint');
  el.settingsLabel.textContent = t('settings');
  $('settingsTitle').textContent = t('settings');
  $('sizeLabel').textContent = t('textSize');
  $('srcLabel').textContent = t('showSrc');
  $('bgLabel').textContent = t('bg');
  $('bgHint').textContent = t('bgHint');
  $('awakeLabel').textContent = t('awake');
  $('themeLabel').textContent = t('theme');
  $('shareLabel').textContent = t('share');
  $('shareHint').textContent = t('shareHint');
  $('shareText').textContent = t('shareBtn');
  $('copyText').textContent = t('copyLink');
  $('installText').textContent = t('install');
  el.installIos.textContent = t('installIos');
  el.jumpText.textContent = t('jump');
  el.bannerText.textContent = t('reconnecting');
  $('retryText').textContent = t('retry');
  if (S.cached && S.cached.title) { el.title.textContent = S.cached.title; document.title = S.cached.title; }
  for (const b of el.themes.querySelectorAll('button')) b.querySelector('span').textContent = t(b.dataset.themeSet);
  for (const b of document.querySelectorAll('[data-close]')) b.setAttribute('aria-label', t('close'));
  SIZES.forEach((_, i) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = 'A';
    b.dataset.size = String(i);
    b.setAttribute('aria-label', `${t('textSize')} ${i + 1}`);
    el.sizes.append(b);
  });
}

// ------------------------------------------------------------------ preferences

function applyPrefs() {
  const size = Math.min(SIZES.length - 1, Math.max(0, +store.get('size', 1)));
  document.documentElement.style.setProperty('--fs', SIZES[size]);
  for (const b of el.sizes.children) b.setAttribute('aria-pressed', String(+b.dataset.size === size));
  // The colour theme is applied and stored (key lt.theme) by theme.js before first paint.

  el.showSrc.checked = store.get('src', '0') === '1';
  document.body.classList.toggle('show-src', el.showSrc.checked);
  el.bgMode.checked = store.get('bg', '0') === '1';
  el.keepAwake.checked = store.get('awake', '1') === '1';
  el.awakeRow.hidden = !('wakeLock' in navigator);
}

// ------------------------------------------------------------------ connection

function wsUrl() {
  return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws';
}

function connect() {
  clearTimeout(S.retryTimer);
  S.retryTimer = 0;
  let ws;
  try { ws = new WebSocket(wsUrl()); } catch {
    S.fails++;
    S.probing = false;
    render();
    scheduleRetry();
    return;
  }
  ws.binaryType = 'arraybuffer';
  S.ws = ws;
  const deadline = setTimeout(() => { if (ws.readyState === WebSocket.CONNECTING) ws.close(); }, CONNECT_TIMEOUT);
  ws.onopen = () => {
    clearTimeout(deadline);
    if (S.ws !== ws) { ws.close(); return; }
    S.connected = true;
    S.everConnected = true;
    S.fails = 0;
    S.probing = false;
    S.lastMsg = Date.now();
    if (S.lang) subscribe();
    render();
  };
  ws.onmessage = (e) => {
    if (S.ws !== ws) return;
    S.lastMsg = Date.now();
    if (typeof e.data === 'string') {
      let j;
      try { j = JSON.parse(e.data); } catch { return; }
      onMessage(j);
    } else if (S.player && S.mode === 'live') {
      S.player.push(e.data);
    }
  };
  ws.onclose = () => {
    clearTimeout(deadline);
    if (S.ws !== ws) return;
    S.ws = null;
    S.connected = false;
    S.probing = false;
    S.fails++;
    render();
    scheduleRetry();
  };
  ws.onerror = () => { /* onclose follows */ };
}

// Exponential backoff with jitter, so hundreds of phones do not all hit a
// restarted server in the same instant.
function scheduleRetry() {
  clearTimeout(S.retryTimer);
  const base = Math.min(500 * 2 ** Math.min(Math.max(S.fails - 1, 0), 4), 8000);
  S.retryTimer = setTimeout(connect, base * (0.75 + Math.random() * 0.5));
}

/** Try right away: network is back, the page is visible again, or "Try again" was tapped. */
function reconnectNow() {
  if (S.connected || (S.ws && S.ws.readyState === WebSocket.CONNECTING)) return;
  connect();
}

/** Abandon a socket that went silent (phone slept, Wi-Fi changed) and dial again. */
function restartSocket() {
  const ws = S.ws;
  S.ws = null;
  S.connected = false;
  if (ws) { ws.onclose = null; try { ws.close(); } catch { /* already closed */ } }
  render();
  connect();
}

/** Ask the server for a sign of life; restart if none comes back quickly. */
function probe() {
  if (!S.ws || S.ws.readyState !== WebSocket.OPEN) return;
  const sent = Date.now();
  send({ t: 'ping' });
  setTimeout(() => { if (S.connected && S.lastMsg < sent) restartSocket(); }, 4000);
}

function send(obj) {
  if (S.ws && S.ws.readyState === 1) S.ws.send(JSON.stringify(obj));
}

function subscribe() {
  send({ t: 'sub', lang: S.lang, audio: S.mode === 'live' && S.audioState !== 'idle' });
}

// Mobile networks drop sockets silently; ping and reconnect when answers stop.
setInterval(() => {
  if (!S.ws || S.ws.readyState !== 1) return;
  if (Date.now() - S.lastMsg > 35000) { restartSocket(); return; }
  send({ t: 'ping' });
}, 15000);

/**
 * Connection as the listener should see it:
 *  online       - connected to the server
 *  connecting   - first attempt (or a retry the listener asked for) is running
 *  reconnecting - just lost the connection, trying again
 *  offline      - the server cannot be reached: nothing is being broadcast
 *  nonet        - the phone itself has no network
 */
function netState() {
  if (S.connected) return 'online';
  if (navigator.onLine === false) return 'nonet';
  if (S.probing || (!S.everConnected && S.fails === 0)) return 'connecting';
  if (S.everConnected && S.fails < 2) return 'reconnecting';
  return 'offline';
}

// ------------------------------------------------------------------ messages

function onMessage(j) {
  switch (j.t) {
    case 'info': onInfo(j); break;
    case 'ch':
      if (j.lang !== S.lang) return;
      if (j.status === 'removed') { toast(t('unavailable'), 'info'); S.lang = null; stopAudio(); clearFeed(); openLangSheet(); }
      else S.chStatus = j.status;
      render();
      break;
    case 'hist': if (j.lang === S.lang) loadHistory(j.segs || []); break;
    case 'tx': onText(j); break;
    default: break;
  }
}

function onInfo(info) {
  S.info = info;
  document.title = info.title || document.title;
  el.title.textContent = info.title || '';
  const langs = info.langs || [];
  S.cached = { title: info.title || '', langs };
  store.set('info', JSON.stringify(S.cached));
  // A language-specific QR code carries ?lang=xx.
  const urlLang = new URLSearchParams(location.search).get('lang');
  if (urlLang && !S.autoPicked) {
    const hit = langs.find((c) => c.toLowerCase() === urlLang.toLowerCase());
    if (hit) { S.autoPicked = true; if (hit !== S.lang) pickLang(hit, false); }
    history.replaceState(null, '', location.pathname);
  }
  if (S.lang && !langs.includes(S.lang)) S.lang = null;
  if (!S.lang && !S.autoPicked) {
    S.autoPicked = true;
    const prefs = (navigator.languages || []).map((l) => l.toLowerCase());
    const match = langs.find((c) => prefs.includes(c.toLowerCase()))
      || langs.find((c) => prefs.some((p) => p.split('-')[0] === c.toLowerCase().split('-')[0]));
    if (match && langs.length === 1) pickLang(match, false);
    else openLangSheet(match);
  }
  renderLangList();
  render();
}

// ------------------------------------------------------------------ transcript

function nearBottom() {
  const f = el.feed;
  return f.scrollHeight - f.scrollTop - f.clientHeight < 120;
}

function scrollToEnd() {
  el.feed.scrollTop = el.feed.scrollHeight;
  el.jump.hidden = true;
}

function makeSeg(id, fresh = false) {
  const art = document.createElement('article');
  art.className = fresh ? 'seg fresh' : 'seg';
  const tr = document.createElement('p');
  tr.className = 'tr';
  const src = document.createElement('p');
  src.className = 'src';
  src.lang = '';
  art.append(tr, src);
  el.feedInner.append(art);
  const seg = { el: art, tr, src };
  S.segs.set(id, seg);
  if (S.nowSeg) S.nowSeg.el.classList.remove('now');
  art.classList.add('now');
  S.nowSeg = seg;
  while (S.segs.size > 150) {
    const [oldId, old] = S.segs.entries().next().value;
    old.el.remove();
    S.segs.delete(oldId);
  }
  return seg;
}

function appendText(node, text) {
  if (node.firstChild) node.firstChild.appendData(text);
  else node.append(text);
}

function onText(j) {
  if (!S.lang) return;
  const pinned = nearBottom();
  let seg = S.segs.get(j.s);
  if (!seg) seg = makeSeg(j.s, true);
  appendText(j.k === 'tr' ? seg.tr : seg.src, j.x);
  if (j.k === 'tr') showTyping();
  if (pinned) scrollToEnd(); else el.jump.hidden = false;
  if (!el.empty.hidden) render();
}

// The current sentence shows a pulsing dot while words keep arriving.
let typingTimer = 0;
function showTyping() {
  el.feed.classList.add('typing');
  clearTimeout(typingTimer);
  typingTimer = setTimeout(() => el.feed.classList.remove('typing'), 1800);
}

function loadHistory(segs) {
  clearFeed();
  for (const s of segs) {
    const seg = makeSeg(s.s);
    if (s.tr) seg.tr.append(s.tr);
    if (s.src) seg.src.append(s.src);
  }
  scrollToEnd();
  render();
}

function clearFeed() {
  el.feedInner.textContent = '';
  S.segs.clear();
  S.nowSeg = null;
  el.jump.hidden = true;
}

// ------------------------------------------------------------------ rendering

function setSub(text, tone = '') {
  el.chSub.textContent = text;
  el.chSub.dataset.tone = tone;
}

function render() {
  const info = S.info;
  const onAir = !!(info && info.onAir);
  const net = netState();
  const hasText = S.segs.size > 0;

  // On-air pill: hidden until the server answers, kept as it was during a short reconnect.
  if (net === 'online' && info) {
    el.onair.dataset.state = onAir ? 'live' : 'off';
    el.onairText.textContent = onAir ? t('live') : t('offAir');
  } else if (net === 'offline' || net === 'nonet') {
    el.onair.dataset.state = 'off';
    el.onairText.textContent = net === 'nonet' ? t('noNet') : t('offAir');
  } else if (el.onair.dataset.state === 'live') {
    el.onair.dataset.state = 'off';
  }

  // Lost connection with a transcript on screen: a quiet banner, the text stays readable.
  el.banner.hidden = !(hasText && !S.connected);
  el.bannerText.textContent = net === 'nonet' ? t('noNetTitle') : net === 'offline' ? t('serverDown') : t('reconnecting');

  // Channel readout
  if (S.lang) {
    const [main, sub] = codeParts(S.lang);
    el.chCode.textContent = main;
    if (sub) { const s = document.createElement('small'); s.textContent = sub; el.chCode.append(s); }
    el.chNative.textContent = nativeName(S.lang);
    el.chNative.lang = S.lang;
  } else {
    el.chCode.textContent = '··';
    el.chNative.textContent = t('chooseLang');
    el.chNative.lang = uiLang;
  }

  // Status line under the language name
  if (net === 'nonet') setSub(t('noNet'));
  else if (net === 'offline') setSub(t('offAir'));
  else if (!S.connected) setSub(t('connecting'));
  else if (!S.lang) setSub('');
  else if (!onAir) setSub(t('offAir'));
  else if (S.chStatus === 'error') setSub(t('errTitle'), 'warn');
  else if (S.chStatus === 'connecting' || S.chStatus === 'reconnecting') setSub(t('startTitle'));
  else if (S.audioState === 'buffering') setSub(t('loading'), 'live');
  else if (S.audioState === 'playing') setSub(t('playing'), 'live');
  else if (S.audioState === 'paused') setSub(t('resume'));
  else if (!audioAvailable()) setSub(t('noAudio'));
  else setSub(localName(S.lang) !== nativeName(S.lang) ? localName(S.lang) : t('tapListen'));

  // Empty state
  el.empty.hidden = hasText;
  if (!hasText) {
    let title, body, state;
    if (net === 'nonet') { title = t('noNetTitle'); body = t('noNetBody'); state = 'nonet'; }
    else if (net === 'offline') { title = t('offlineTitle'); body = t('offlineBody'); state = 'offline'; }
    else if (!S.connected) { title = S.everConnected ? t('reconnecting') : t('connecting'); body = ''; state = 'connecting'; }
    else if (!S.lang) { title = t('chooseLang'); body = t('chooseHint'); state = 'choose'; }
    else if (!onAir) { title = t('offTitle'); body = t('offBody'); state = 'off'; }
    else if (S.chStatus === 'error') { title = t('errTitle'); body = t('errBody'); state = 'error'; }
    else if (S.chStatus === 'connecting' || S.chStatus === 'reconnecting') { title = t('startTitle'); body = t('startBody'); state = 'starting'; }
    else { title = t('waitTitle'); body = t('waitBody'); state = 'waiting'; }
    el.emptyTitle.textContent = title;
    el.emptyBody.textContent = body;
    el.emptyBody.hidden = !body;
    el.retryBtn.hidden = state !== 'offline' && state !== 'nonet';
    if (state !== S.emptyState) {
      // Fade the new message in (a quick "connecting" never shows: see listener.css).
      S.emptyState = state;
      el.empty.dataset.state = state;
      el.empty.classList.remove('in');
      void el.empty.offsetWidth;
      el.empty.classList.add('in');
    }
  } else {
    S.emptyState = null;
  }

  // Play button
  el.dock.dataset.audio = S.audioState;
  el.play.dataset.state = S.audioState === 'paused' ? 'idle' : S.audioState;
  el.play.disabled = !S.lang || !audioAvailable() || (!S.connected && S.audioState === 'idle');
  el.playLabel.textContent = S.audioState === 'idle' || S.audioState === 'paused' ? t('listen') : t('stop');
  updateWakeLock();
}

function renderLangList() {
  const langs = (S.info && S.info.langs) || (S.cached && S.cached.langs) || [];
  el.langList.textContent = '';
  if (!langs.length) {
    const li = document.createElement('li');
    li.className = 'lang-empty';
    li.textContent = t('noLangs');
    el.langList.append(li);
    return;
  }
  for (const [i, code] of langs.entries()) {
    const li = document.createElement('li');
    li.style.setProperty('--i', String(i));
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.lang = code;
    b.setAttribute('aria-current', String(code === S.lang));
    const c = document.createElement('span');
    c.className = 'code';
    const [main, sub] = codeParts(code);
    c.textContent = main;
    if (sub) { const s = document.createElement('small'); s.textContent = sub; c.append(s); }
    const names = document.createElement('span');
    names.className = 'names';
    const n1 = document.createElement('span');
    n1.className = 'native';
    n1.lang = code;
    n1.textContent = nativeName(code);
    const n2 = document.createElement('span');
    n2.className = 'sub';
    n2.textContent = localName(code);
    names.append(n1);
    if (n2.textContent !== n1.textContent) names.append(n2);
    b.append(c, names);
    b.insertAdjacentHTML('beforeend', '<svg class="check" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>');
    li.append(b);
    el.langList.append(li);
  }
}

// ------------------------------------------------------------------ language

/** showModal() focuses the first button and browsers then draw a focus ring on it
 *  even after a tap. On touch the sheet itself takes focus instead. */
function openSheet(dlg) {
  if (dlg.open) return;
  dlg.showModal();
  if (!S.keyboard) dlg.focus({ preventScroll: true });
}

function openLangSheet(focusCode) {
  renderLangList();
  openSheet(el.langSheet);
  const target = el.langList.querySelector(`[data-lang="${CSS.escape(focusCode || S.lang || '')}"]`)
    || el.langList.querySelector('button');
  if (!target) return;
  // Keyboard users land on the list; on touch the sheet itself keeps focus (no stray ring).
  if (S.keyboard) target.focus();
  else target.scrollIntoView({ block: 'nearest' });
}

function pickLang(code, close = true) {
  if (code !== S.lang) {
    S.lang = code;
    store.set('lang', code);
    S.chStatus = 'idle';
    clearFeed();
    if (S.player) {
      if (S.mode === 'live') S.player.flush();
      else S.player.setUrl(streamUrl());
    }
    subscribe();
  }
  if (close && el.langSheet.open) closeSheet(el.langSheet);
  updateMediaSession();
  render();
}

// ------------------------------------------------------------------ audio

function audioAvailable() {
  const c = (S.info && S.info.codecs) || {};
  return (S.liveOk && c.opus) || c.mp3;
}

function streamUrl() { return `/stream/${encodeURIComponent(S.lang)}.mp3`; }

function chooseMode() {
  const c = (S.info && S.info.codecs) || {};
  if (el.bgMode.checked && c.mp3) return 'stream';
  if (S.liveOk && c.opus) return 'live';
  return c.mp3 ? 'stream' : null;
}

function setAudioState(state) {
  S.audioState = state;
  if ('mediaSession' in navigator) {
    navigator.mediaSession.playbackState = state === 'playing' || state === 'buffering' ? 'playing' : 'paused';
  }
  render();
}

async function startAudio() {
  if (!S.lang) { openLangSheet(); return; }
  const mode = chooseMode();
  if (!mode) return;
  stopAudio(true);
  S.mode = mode;
  setAudioState('buffering');
  try {
    if (mode === 'live') {
      const p = new LivePlayer({
        onLevel: (rms) => {
          if (S.player !== p) return;
          const lvl = Math.min(0.22, rms * 2.2);
          el.dock.style.setProperty('--amp', (0.25 + lvl * 3.4).toFixed(3));
          // The ripples around play fade down in pauses and swell back with the voice.
          const voice = Math.min(1, 0.15 + rms * 10);
          if (Math.abs(voice - S.voice) > 0.05) {
            S.voice = voice;
            el.dock.style.setProperty('--voice', voice.toFixed(2));
          }
        },
        onState: (st) => { if (S.player === p && st !== 'running' && S.audioState !== 'idle') setAudioState('paused'); },
      });
      S.player = p;
      await p.start();
      subscribe();
      if (S.audioState === 'buffering') setAudioState('playing');
    } else {
      const p = new StreamPlayer(streamUrl(), {
        onState: (st) => { if (S.player === p) setAudioState(st); },
      });
      S.player = p;
      await p.start();
    }
    updateMediaSession();
  } catch (err) {
    console.warn('Audio start failed', err);
    if (mode === 'live' && ((S.info && S.info.codecs) || {}).mp3) {
      S.liveOk = false;          // fall back to the stream from now on
      stopAudio(true);
      return startAudio();
    }
    stopAudio();
    toast(t('noAudio'), 'error');
  }
}

function stopAudio(silent = false) {
  if (S.player) { S.player.stop(); S.player = null; }
  const wasLive = S.mode === 'live';
  S.mode = null;
  S.voice = -1;
  el.dock.style.removeProperty('--amp');
  el.dock.style.removeProperty('--voice');
  if (wasLive) send({ t: 'audio', on: false });
  if (!silent) setAudioState('idle');
  else S.audioState = 'idle';
}

async function togglePlay() {
  haptic();
  if (S.audioState === 'paused' && S.player) {
    try { await S.player.resume(); setAudioState('playing'); return; } catch { /* restart below */ }
  }
  if (S.audioState === 'idle' || S.audioState === 'paused') await startAudio();
  else stopAudio();
}

function updateMediaSession() {
  if (!('mediaSession' in navigator) || !S.lang) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: (S.info && S.info.title) || document.title,
      artist: nativeName(S.lang),
      artwork: [
        { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
        { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
      ],
    });
    navigator.mediaSession.setActionHandler('play', () => { if (S.audioState !== 'playing') togglePlay(); });
    navigator.mediaSession.setActionHandler('pause', () => stopAudio());
    navigator.mediaSession.setActionHandler('stop', () => stopAudio());
  } catch { /* unsupported action */ }
}

// ------------------------------------------------------------------ wake lock

async function updateWakeLock() {
  if (!('wakeLock' in navigator)) return;
  // No point keeping the screen lit while the server is unreachable.
  const want = el.keepAwake.checked && document.visibilityState === 'visible'
    && !!(S.info && S.info.onAir) && netState() !== 'offline' && netState() !== 'nonet'
    && !(S.mode === 'stream' && S.audioState !== 'idle');
  if (want && !S.wakeLock) {
    S.wakeLock = 'pending';
    try {
      const lock = await navigator.wakeLock.request('screen');
      S.wakeLock = lock;
      lock.addEventListener('release', () => { if (S.wakeLock === lock) S.wakeLock = null; });
    } catch { S.wakeLock = null; }
  } else if (!want && S.wakeLock && S.wakeLock !== 'pending') {
    const lock = S.wakeLock;
    S.wakeLock = null;
    try { await lock.release(); } catch { /* already released */ }
  }
}

// ------------------------------------------------------------------ misc UI

/** tone: '' (done), 'info' or 'error'. */
function toast(text, tone = '') {
  const error = tone === 'error';
  showToast(text, { error, info: tone === 'info', ms: error ? 4500 : 3200 });
}

function sizePreviewText() {
  const last = S.nowSeg && (S.nowSeg.tr.textContent || S.nowSeg.src.textContent);
  return last || t('waitBody');
}

function openSettings() {
  el.shareUrl.textContent = location.origin + '/';
  // The QR code comes from the server: without it (offline start) the card shows just the link.
  if (!el.qr.getAttribute('src') && S.connected) el.qr.src = '/api/qr.svg';
  el.qr.hidden = !el.qr.getAttribute('src');
  el.shareBtn.hidden = !navigator.share;
  el.copyBtn.hidden = !canCopy();
  el.sizePreview.textContent = sizePreviewText();
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const standalone = matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  el.installBtn.hidden = !S.installEvent;
  el.installIos.hidden = !(ios && !standalone);
  el.installGroup.hidden = standalone || (!S.installEvent && !ios);
  openSheet(el.settingsSheet);
}

// ------------------------------------------------------------------ events

el.channelBtn.addEventListener('click', () => openLangSheet());
el.play.addEventListener('click', togglePlay);
el.settingsBtn.addEventListener('click', openSettings);
el.jump.addEventListener('click', scrollToEnd);
el.qr.addEventListener('error', () => { el.qr.removeAttribute('src'); el.qr.hidden = true; });

el.retryBtn.addEventListener('click', () => {
  haptic();
  S.probing = true;
  render();
  reconnectNow();
});
window.addEventListener('online', () => { render(); reconnectNow(); });
window.addEventListener('offline', render);

// Focus rings and list focus only for keyboard users.
document.addEventListener('keydown', (e) => { if (e.key === 'Tab' || e.key === 'Enter' || e.key === ' ') S.keyboard = true; }, true);
document.addEventListener('pointerdown', () => { S.keyboard = false; }, true);
el.feed.addEventListener('scroll', () => { if (nearBottom()) el.jump.hidden = true; }, { passive: true });

el.langList.addEventListener('click', (e) => {
  const b = e.target.closest('button[data-lang]');
  if (!b) return;
  haptic();
  pickLang(b.dataset.lang);
});

for (const dlg of [el.langSheet, el.settingsSheet]) wireSheet(dlg);

el.sizes.addEventListener('click', (e) => {
  const b = e.target.closest('button[data-size]');
  if (!b) return;
  const pinned = nearBottom();
  store.set('size', b.dataset.size);
  applyPrefs();
  if (pinned) scrollToEnd();
});

el.showSrc.addEventListener('change', () => { store.set('src', el.showSrc.checked ? '1' : '0'); applyPrefs(); });
el.keepAwake.addEventListener('change', () => { store.set('awake', el.keepAwake.checked ? '1' : '0'); updateWakeLock(); });
el.bgMode.addEventListener('change', () => {
  store.set('bg', el.bgMode.checked ? '1' : '0');
  if (S.audioState !== 'idle') startAudio();   // switch transport right away (inside this tap)
});

el.copyBtn.addEventListener('click', async () => {
  if (await copyText(location.origin + '/')) { haptic(); toast(t('copied')); }
});

el.shareBtn.addEventListener('click', async () => {
  try { await navigator.share({ title: (S.info && S.info.title) || document.title, url: location.origin + '/' }); }
  catch { /* cancelled */ }
});

window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); S.installEvent = e; });
el.installBtn.addEventListener('click', async () => {
  if (!S.installEvent) return;
  S.installEvent.prompt();
  try { await S.installEvent.userChoice; } catch { /* ignore */ }
  S.installEvent = null;
  el.installGroup.hidden = true;
});

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    if (S.player && S.audioState !== 'idle' && S.player.state !== 'running') {
      S.player.resume().then(() => setAudioState('playing')).catch(() => setAudioState('paused'));
    }
    // Back from the pocket: do not wait for the backoff timer, and check that an
    // open-looking socket really still works.
    if (S.connected) probe(); else reconnectNow();
  }
  updateWakeLock();
});

// ------------------------------------------------------------------ boot

applyStaticText();
applyPrefs();
render();
LivePlayer.supported().then((ok) => { S.liveOk = ok; render(); });
connect();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}
