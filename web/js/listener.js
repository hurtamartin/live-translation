import { t, nativeName, localName, codeParts, uiLang } from './i18n.js';
import { LivePlayer, StreamPlayer } from './player.js';

// ------------------------------------------------------------------ storage

const store = {
  get(k, d = null) { try { const v = localStorage.getItem('lt.' + k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem('lt.' + k, String(v)); } catch { /* private mode */ } },
};

const $ = (id) => document.getElementById(id);
const el = {
  title: $('title'), onair: $('onair'), onairText: $('onairText'), banner: $('banner'),
  feed: $('feed'), feedInner: $('feedInner'), empty: $('empty'), emptyTitle: $('emptyTitle'), emptyBody: $('emptyBody'),
  jump: $('jump'), jumpText: $('jumpText'),
  channelBtn: $('channelBtn'), chCode: $('chCode'), chNative: $('chNative'), chSub: $('chSub'),
  play: $('play'), playLabel: $('playLabel'), settingsBtn: $('settingsBtn'), settingsLabel: $('settingsLabel'),
  langSheet: $('langSheet'), langTitle: $('langTitle'), langHint: $('langHint'), langList: $('langList'),
  settingsSheet: $('settingsSheet'), sizes: $('sizes'), showSrc: $('showSrc'), bgMode: $('bgMode'),
  keepAwake: $('keepAwake'), awakeRow: $('awakeRow'), themes: $('themes'),
  qr: $('qr'), shareUrl: $('shareUrl'), shareBtn: $('shareBtn'),
  installGroup: $('installGroup'), installBtn: $('installBtn'), installIos: $('installIos'),
  toast: $('toast'),
};

const SIZES = ['1.05rem', '1.3rem', '1.6rem', '1.95rem', '2.4rem'];

const S = {
  info: null,
  lang: store.get('lang'),
  chStatus: 'idle',
  connected: false,
  everConnected: false,
  ws: null,
  retry: 0,
  lastMsg: 0,
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
  el.shareBtn.textContent = t('shareBtn');
  el.installBtn.textContent = t('install');
  el.installIos.textContent = t('installIos');
  el.jumpText.textContent = t('jump');
  for (const b of el.themes.querySelectorAll('button')) b.textContent = t(b.dataset.theme);
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

  const theme = store.get('theme', 'dark');
  document.documentElement.dataset.theme = theme;
  for (const b of el.themes.children) b.setAttribute('aria-pressed', String(b.dataset.theme === theme));
  const dark = theme === 'dark' || (theme === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.querySelector('meta[name="theme-color"]').content = dark ? '#10162B' : '#F1F3F9';

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
  const ws = new WebSocket(wsUrl());
  ws.binaryType = 'arraybuffer';
  S.ws = ws;
  ws.onopen = () => {
    S.connected = true;
    S.everConnected = true;
    S.retry = 0;
    S.lastMsg = Date.now();
    if (S.lang) subscribe();
    render();
  };
  ws.onmessage = (e) => {
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
    if (S.ws !== ws) return;
    S.ws = null;
    S.connected = false;
    render();
    const delay = Math.min(500 * 2 ** S.retry++, 8000);
    setTimeout(connect, delay);
  };
  ws.onerror = () => { /* onclose follows */ };
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
  if (Date.now() - S.lastMsg > 35000) { S.ws.close(); return; }
  send({ t: 'ping' });
}, 15000);

// ------------------------------------------------------------------ messages

function onMessage(j) {
  switch (j.t) {
    case 'info': onInfo(j); break;
    case 'ch':
      if (j.lang !== S.lang) return;
      if (j.status === 'removed') { toast(t('unavailable')); S.lang = null; stopAudio(); clearFeed(); openLangSheet(); }
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

function makeSeg(id) {
  const art = document.createElement('article');
  art.className = 'seg';
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
  if (!seg) seg = makeSeg(j.s);
  appendText(j.k === 'tr' ? seg.tr : seg.src, j.x);
  if (pinned) scrollToEnd(); else el.jump.hidden = false;
  if (!el.empty.hidden) render();
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
  el.onair.dataset.state = onAir && S.connected ? 'live' : 'off';
  el.onairText.textContent = onAir ? t('live') : t('offAir');

  el.banner.hidden = S.connected || !S.everConnected;
  el.banner.textContent = t('reconnecting');

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
  if (!S.connected) setSub(S.everConnected ? t('reconnecting') : t('connecting'));
  else if (!S.lang) setSub(t('changeLang'));
  else if (!onAir) setSub(t('offAir'));
  else if (S.chStatus === 'error') setSub(t('errTitle'), 'warn');
  else if (S.chStatus === 'connecting' || S.chStatus === 'reconnecting') setSub(t('startTitle'));
  else if (S.audioState === 'buffering') setSub(t('loading'), 'live');
  else if (S.audioState === 'playing') setSub(t('playing'), 'live');
  else if (S.audioState === 'paused') setSub(t('resume'));
  else if (!audioAvailable()) setSub(t('noAudio'));
  else setSub(localName(S.lang) !== nativeName(S.lang) ? localName(S.lang) : t('tapListen'));

  // Empty state
  const hasText = S.segs.size > 0;
  el.empty.hidden = hasText;
  if (!hasText) {
    let title, body;
    if (!S.connected) { title = t('connecting'); body = ''; }
    else if (!S.lang) { title = t('chooseLang'); body = t('chooseHint'); }
    else if (!onAir) { title = t('offTitle'); body = t('offBody'); }
    else if (S.chStatus === 'error') { title = t('errTitle'); body = t('errBody'); }
    else if (S.chStatus === 'connecting' || S.chStatus === 'reconnecting') { title = t('startTitle'); body = t('startBody'); }
    else { title = t('waitTitle'); body = t('waitBody'); }
    el.emptyTitle.textContent = title;
    el.emptyBody.textContent = body;
  }

  // Play button
  el.play.dataset.state = S.audioState === 'paused' ? 'idle' : S.audioState;
  el.play.disabled = !S.lang || !audioAvailable();
  el.playLabel.textContent = S.audioState === 'idle' || S.audioState === 'paused' ? t('listen') : t('stop');
  updateWakeLock();
}

function renderLangList() {
  const langs = (S.info && S.info.langs) || [];
  el.langList.textContent = '';
  if (!langs.length) {
    const li = document.createElement('li');
    li.className = 'lang-empty';
    li.textContent = t('noLangs');
    el.langList.append(li);
    return;
  }
  for (const code of langs) {
    const li = document.createElement('li');
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

function openLangSheet(focusCode) {
  renderLangList();
  if (!el.langSheet.open) el.langSheet.showModal();
  const target = el.langList.querySelector(`[data-lang="${CSS.escape(focusCode || S.lang || '')}"]`)
    || el.langList.querySelector('button');
  if (target) target.focus();
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
  if (close && el.langSheet.open) el.langSheet.close();
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
          if (S.player === p) el.play.style.setProperty('--lvl', (1 + Math.min(0.22, rms * 2.2)).toFixed(3));
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
    toast(t('noAudio'), true);
  }
}

function stopAudio(silent = false) {
  if (S.player) { S.player.stop(); S.player = null; }
  const wasLive = S.mode === 'live';
  S.mode = null;
  el.play.style.removeProperty('--lvl');
  if (wasLive) send({ t: 'audio', on: false });
  if (!silent) setAudioState('idle');
  else S.audioState = 'idle';
}

async function togglePlay() {
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
  const want = el.keepAwake.checked && document.visibilityState === 'visible'
    && !!(S.info && S.info.onAir) && !(S.mode === 'stream' && S.audioState !== 'idle');
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

let toastTimer = 0;
function toast(text, error = false) {
  el.toast.textContent = text;
  el.toast.classList.toggle('error', error);
  el.toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.toast.classList.remove('show'), 3200);
}

function openSettings() {
  el.shareUrl.textContent = location.origin + '/';
  if (!el.qr.src) {
    el.qr.src = '/api/qr.svg';
  }
  el.shareBtn.hidden = !navigator.share;
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const standalone = matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  el.installBtn.hidden = !S.installEvent;
  el.installIos.hidden = !(ios && !standalone);
  el.installGroup.hidden = standalone || (!S.installEvent && !ios);
  el.settingsSheet.showModal();
}

// ------------------------------------------------------------------ events

el.channelBtn.addEventListener('click', () => openLangSheet());
el.play.addEventListener('click', togglePlay);
el.settingsBtn.addEventListener('click', openSettings);
el.jump.addEventListener('click', scrollToEnd);
el.feed.addEventListener('scroll', () => { if (nearBottom()) el.jump.hidden = true; }, { passive: true });

el.langList.addEventListener('click', (e) => {
  const b = e.target.closest('button[data-lang]');
  if (b) pickLang(b.dataset.lang);
});

for (const dlg of [el.langSheet, el.settingsSheet]) {
  dlg.addEventListener('click', (e) => {
    if (e.target === dlg || e.target.closest('[data-close]')) dlg.close();
  });
}

el.sizes.addEventListener('click', (e) => {
  const b = e.target.closest('button[data-size]');
  if (!b) return;
  const pinned = nearBottom();
  store.set('size', b.dataset.size);
  applyPrefs();
  if (pinned) scrollToEnd();
});

el.themes.addEventListener('click', (e) => {
  const b = e.target.closest('button[data-theme]');
  if (!b) return;
  store.set('theme', b.dataset.theme);
  applyPrefs();
});

el.showSrc.addEventListener('change', () => { store.set('src', el.showSrc.checked ? '1' : '0'); applyPrefs(); });
el.keepAwake.addEventListener('change', () => { store.set('awake', el.keepAwake.checked ? '1' : '0'); updateWakeLock(); });
el.bgMode.addEventListener('change', () => {
  store.set('bg', el.bgMode.checked ? '1' : '0');
  if (S.audioState !== 'idle') startAudio();   // switch transport right away (inside this tap)
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
    if (S.ws === null && !S.connected) { /* reconnect loop is already scheduled */ }
  }
  updateWakeLock();
});

matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', applyPrefs);

// ------------------------------------------------------------------ boot

applyStaticText();
applyPrefs();
render();
LivePlayer.supported().then((ok) => { S.liveOk = ok; render(); });
connect();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}
