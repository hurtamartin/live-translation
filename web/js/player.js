// Two ways to hear the translation:
//  LivePlayer   - Opus frames over the WebSocket, decoded with WebCodecs and
//                 played through an AudioWorklet jitter buffer. Lowest delay.
//                 Needs https (secure context) and a WebCodecs-capable browser.
//  StreamPlayer - plain <audio> element on /stream/<lang>.mp3. Works everywhere,
//                 keeps playing with the screen locked, a bit more delay.

const OPUS_CONFIG = { codec: 'opus', sampleRate: 48000, numberOfChannels: 1 };

// Jitter buffer (seconds). Starts small and adapts in the worklet: grows after a
// dropout caused by the network, shrinks again while playback stays clean.
const BUFFER = { target: 0.15, minTarget: 0.1, maxTarget: 0.8, max: 2.5 };

function audioDataToFloat(data) {
  const n = data.numberOfFrames;
  const out = new Float32Array(n);
  try {
    data.copyTo(out, { planeIndex: 0, format: 'f32-planar' });
    return out;
  } catch { /* format conversion not supported: copy natively */ }
  const fmt = data.format || '';
  if (fmt.startsWith('f32')) {
    if (fmt === 'f32' && data.numberOfChannels > 1) {
      const tmp = new Float32Array(n * data.numberOfChannels);
      data.copyTo(tmp, { planeIndex: 0 });
      for (let i = 0; i < n; i++) out[i] = tmp[i * data.numberOfChannels];
    } else {
      data.copyTo(out, { planeIndex: 0 });
    }
  } else if (fmt.startsWith('s16')) {
    const tmp = new Int16Array(n * (fmt === 's16' ? data.numberOfChannels : 1));
    data.copyTo(tmp, { planeIndex: 0 });
    const step = fmt === 's16' ? data.numberOfChannels : 1;
    for (let i = 0; i < n; i++) out[i] = tmp[i * step] / 32768;
  }
  return out;
}

function preferPlayback() {
  // iOS: play even when the ring/silent switch is on.
  try { if (navigator.audioSession) navigator.audioSession.type = 'playback'; } catch { /* ignore */ }
}

export class LivePlayer {
  static async supported() {
    if (!window.isSecureContext || typeof AudioDecoder === 'undefined' || typeof AudioWorkletNode === 'undefined') return false;
    try { return !!(await AudioDecoder.isConfigSupported(OPUS_CONFIG)).supported; } catch { return false; }
  }

  constructor({ onLevel, onState } = {}) {
    this.onLevel = onLevel || (() => {});
    this.onState = onState || (() => {});
    this.ctx = null;
    this.node = null;
    this.decoder = null;
    this.ts = 0;
    this.inRate = OPUS_CONFIG.sampleRate;
  }

  /** Must be called from a user gesture. */
  async start() {
    preferPlayback();
    const Ctx = window.AudioContext || window.webkitAudioContext;
    // Native device rate (the worklet resamples) and 'playback' latency: larger
    // hardware buffers, so a busy phone does not crackle. Costs a few tens of ms,
    // small next to the translation delay itself.
    try { this.ctx = new Ctx({ latencyHint: 'playback' }); }
    catch { this.ctx = new Ctx(); }
    const resumed = this.ctx.resume();
    await this.ctx.audioWorklet.addModule('/js/player-worklet.js');
    this.node = new AudioWorkletNode(this.ctx, 'translation-player', {
      numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1],
      processorOptions: { inRate: this.inRate, ...BUFFER },
    });
    this.node.port.onmessage = (e) => this.onLevel(e.data.rms, e.data.buffered);
    this.node.connect(this.ctx.destination);
    await resumed;
    this.ctx.onstatechange = () => this.onState(this.ctx ? this.ctx.state : 'closed');
    this._newDecoder();
  }

  _newDecoder() {
    if (this.decoder && this.decoder.state !== 'closed') { try { this.decoder.close(); } catch { /* ignore */ } }
    this.decoder = new AudioDecoder({
      output: (data) => this._output(data),
      error: (err) => { console.warn('Opus decoder error', err); setTimeout(() => this.ctx && this._newDecoder(), 200); },
    });
    this.decoder.configure(OPUS_CONFIG);
  }

  push(buffer) {
    const u8 = new Uint8Array(buffer);
    if (u8.length < 6 || u8[0] !== 1 || !this.decoder || this.decoder.state !== 'configured') return;
    if (this.decoder.decodeQueueSize > 25) return;   // device too slow: drop rather than lag
    this.decoder.decode(new EncodedAudioChunk({ type: 'key', timestamp: this.ts, duration: 40000, data: u8.subarray(5) }));
    this.ts += 40000;
  }

  _output(data) {
    if (!this.ctx || !this.node) { data.close(); return; }
    const rate = data.sampleRate;
    const f = audioDataToFloat(data);
    data.close();
    if (rate !== this.inRate) {
      this.inRate = rate;
      this.node.port.postMessage({ rate });
    }
    this.node.port.postMessage(f, [f.buffer]);
  }

  flush() { if (this.node) this.node.port.postMessage('flush'); }

  get state() { return this.ctx ? this.ctx.state : 'closed'; }

  async resume() { if (this.ctx && this.ctx.state !== 'running') await this.ctx.resume(); }

  stop() {
    try { if (this.decoder && this.decoder.state !== 'closed') this.decoder.close(); } catch { /* ignore */ }
    try { if (this.node) this.node.disconnect(); } catch { /* ignore */ }
    try { if (this.ctx) this.ctx.close(); } catch { /* ignore */ }
    this.decoder = this.node = this.ctx = null;
  }
}

export class StreamPlayer {
  constructor(url, { onState } = {}) {
    this.url = url;
    this.onState = onState || (() => {});
    this.audio = new Audio();
    this.audio.preload = 'none';
    this.audio.setAttribute('playsinline', '');
    this.active = false;
    this.retry = 0;
    this.timer = 0;
    this.restartTimer = 0;
    this.waitingSince = 0;
    const a = this.audio;
    a.addEventListener('playing', () => { this.retry = 0; this.waitingSince = 0; this.onState('playing'); });
    a.addEventListener('waiting', () => { this.waitingSince ||= performance.now(); this.onState('buffering'); });
    a.addEventListener('error', () => this._recover());
    a.addEventListener('ended', () => this._recover());
    a.addEventListener('pause', () => { if (this.active && !a.ended && a.error == null) this.onState('paused'); });
  }

  async start() {
    preferPlayback();
    this.active = true;
    this.onState('buffering');
    this.audio.src = this.url + '?t=' + Date.now();
    clearInterval(this.timer);
    this.timer = setInterval(() => this._watch(), 1000);
    await this.audio.play();
  }

  // Keeps latency in check without seeking. Seeking to the live edge empties the
  // buffer and stalls playback, and phones then rebuffer and lag again, so the
  // stream ended up playing in fragments. Instead play slightly faster while
  // behind (pitch is preserved), and only reload the stream after a long lag.
  _watch() {
    const a = this.audio;
    if (!this.active) return;
    if (this.waitingSince && performance.now() - this.waitingSince > 12000) {
      this.waitingSince = 0;
      this._recover();
      return;
    }
    if (!a.buffered.length || a.paused) return;
    const lag = a.buffered.end(a.buffered.length - 1) - a.currentTime;
    if (lag > 15) { this._reload(); return; }
    // Hysteresis: speed up above 3 s, back to normal once under 2 s (Wi-Fi safety margin).
    const rate = lag > 5 ? 1.1 : lag > 3 ? 1.05 : lag < 2 ? 1 : a.playbackRate;
    if (a.playbackRate !== rate) a.playbackRate = rate;
  }

  _reload() {
    this.audio.playbackRate = 1;
    this.audio.src = this.url + '?t=' + Date.now();
    this.audio.play().catch(() => this.onState('paused'));
  }

  _recover() {
    if (!this.active) return;
    this.onState('buffering');
    clearTimeout(this.restartTimer);
    const delay = Math.min(1000 * 2 ** this.retry++, 10000);
    this.restartTimer = setTimeout(() => { if (this.active) this._reload(); }, delay);
  }

  setUrl(url) {
    this.url = url;
    if (this.active) this._reload();
  }

  get state() { return this.active && !this.audio.paused ? 'running' : 'suspended'; }

  async resume() { if (this.active && this.audio.paused) await this.audio.play(); }

  stop() {
    this.active = false;
    clearInterval(this.timer);
    clearTimeout(this.restartTimer);
    this.audio.pause();
    this.audio.removeAttribute('src');
    this.audio.load();
  }
}
