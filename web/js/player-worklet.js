// Jitter-buffer playback of decoded translation audio.
// Starts once `target` seconds are buffered, refills after an underrun and
// skips ahead if more than `max` seconds pile up, so delay stays bounded.

class TranslationPlayer extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = (options && options.processorOptions) || {};
    this.size = Math.ceil(sampleRate * 10);
    this.buf = new Float32Array(this.size);
    this.r = 0;
    this.w = 0;
    this.count = 0;
    this.target = Math.round((o.target || 0.16) * sampleRate);
    this.max = Math.round((o.max || 1.2) * sampleRate);
    this.playing = false;
    this.fade = 0;
    this.sumSq = 0;
    this.n = 0;
    this.port.onmessage = (e) => {
      if (e.data === 'flush') { this.r = this.w = this.count = 0; this.playing = false; return; }
      this.push(e.data);
    };
  }

  push(f) {
    const size = this.size;
    for (let i = 0; i < f.length; i++) {
      this.buf[this.w] = f[i];
      this.w = this.w + 1 === size ? 0 : this.w + 1;
    }
    this.count += f.length;
    if (this.count > this.max) {
      const drop = this.count - this.target;
      this.r = (this.r + drop) % size;
      this.count -= drop;
    }
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    const len = out.length;
    if (!this.playing && this.count >= this.target) { this.playing = true; this.fade = 0; }
    if (this.playing) {
      const size = this.size;
      const n = Math.min(len, this.count);
      for (let i = 0; i < n; i++) {
        let v = this.buf[this.r];
        if (this.fade < 64) { v *= this.fade / 64; this.fade++; }   // no click on start
        out[i] = v;
        this.sumSq += v * v;
        this.r = this.r + 1 === size ? 0 : this.r + 1;
      }
      this.count -= n;
      if (n < len) { out.fill(0, n); this.playing = false; }
    } else {
      out.fill(0);
    }
    this.n += len;
    if (this.n >= 4096) {
      this.port.postMessage({ rms: Math.sqrt(this.sumSq / this.n), buffered: this.count / sampleRate });
      this.n = 0;
      this.sumSq = 0;
    }
    return true;
  }
}

registerProcessor('translation-player', TranslationPlayer);
