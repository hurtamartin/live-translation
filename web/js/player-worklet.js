// Jitter-buffer playback of decoded translation audio (runs on the audio thread).
//
// - Resamples from the decoder rate to the device rate itself (cubic, state kept
//   across chunks): no clicks at chunk edges, and the AudioContext can run at the
//   phone's native rate. Forcing 48 kHz makes some phones resample badly.
// - Starts once `target` seconds are buffered. The target adapts: it grows after an
//   underrun in the middle of speech (Wi-Fi hiccup) and shrinks slowly while playback
//   is clean, so the delay stays as low as the network allows.
// - Never cuts hard: fades to silence when the buffer runs dry, fades in on restart.
// - Behind after a network burst: plays up to 4 % faster (inaudible for speech) until
//   back at the target, instead of jumping. Only a huge backlog is skipped.

class TranslationPlayer extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = (options && options.processorOptions) || {};
    this.setRate(o.inRate || 48000);
    let size = 1;
    while (size < 48000 * 16) size <<= 1;       // ring buffer, power of two
    this.size = size;
    this.mask = size - 1;
    this.buf = new Float32Array(size);
    this.w = 0;           // write index
    this.r = 0;           // read index (integer part)
    this.frac = 0;        // read index (fractional part)
    this.count = 0;       // input samples from r on
    this.target = (o.target || 0.15) * this.inRate;
    this.minTarget = (o.minTarget || 0.1) * this.inRate;
    this.maxTarget = (o.maxTarget || 0.8) * this.inRate;
    this.max = (o.max || 2.5) * this.inRate;
    this.playing = false;
    this.fadeLen = Math.round(sampleRate * 0.005);
    this.fade = 0;
    this.last = 0;                                              // last output sample
    this.decay = Math.exp(Math.log(0.01) / (0.005 * sampleRate)); // -40 dB in 5 ms
    this.envK = 1 - Math.exp(-1 / (0.02 * sampleRate));        // 20 ms envelope
    this.env = 0;
    this.clean = 0;       // output samples since the last underrun / target change
    this.underruns = 0;
    this.sumSq = 0;
    this.n = 0;
    this.port.onmessage = (e) => {
      const d = e.data;
      if (d === 'flush') { this.r = this.w; this.count = 0; this.frac = 0; this.playing = false; }
      else if (d instanceof Float32Array) this.push(d);
      else if (d && d.rate) this.setRate(d.rate);
    };
  }

  setRate(rate) {
    this.inRate = rate;
    this.step = rate / sampleRate;   // input samples per output sample
  }

  push(f) {
    const buf = this.buf;
    const mask = this.mask;
    let w = this.w;
    for (let i = 0; i < f.length; i++) {
      buf[w] = f[i];
      w = (w + 1) & mask;
    }
    this.w = w;
    this.count += f.length;
    const limit = this.size - 8;   // never let the writer lap the reader
    if (this.count > limit) {
      this.r = (this.r + this.count - limit) & mask;
      this.count = limit;
    }
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    const len = out.length;
    let i = 0;

    if (!this.playing && this.count >= this.target) {
      this.playing = true;
      this.fade = 0;
    }
    if (this.playing) {
      if (this.count > this.max) {
        // Hopelessly behind: skip to the target (rare; the speed-up handles the rest).
        const skip = Math.floor(this.count - this.target);
        this.r = (this.r + skip) & this.mask;
        this.count -= skip;
        this.fade = 0;
      }
      const behind = (this.count - this.target) / this.inRate;
      const speed = behind > 0.1 ? 1 + Math.min(0.04, behind * 0.04) : 1;
      const inc = this.step * speed;
      const buf = this.buf;
      const mask = this.mask;
      for (; i < len; i++) {
        if (this.count < 3) break;
        const r = this.r;
        const p0 = buf[(r - 1) & mask];
        const p1 = buf[r];
        const p2 = buf[(r + 1) & mask];
        const p3 = buf[(r + 2) & mask];
        const t = this.frac;
        // Catmull-Rom: exact at t = 0, so no filtering when the rates match.
        let v = p1 + 0.5 * t * (p2 - p0 + t * (2 * p0 - 5 * p1 + 4 * p2 - p3 + t * (3 * (p1 - p2) + p3 - p0)));
        if (this.fade < this.fadeLen) { v *= this.fade / this.fadeLen; this.fade++; }
        out[i] = v;
        this.env += ((v < 0 ? -v : v) - this.env) * this.envK;
        this.frac += inc;
        if (this.frac >= 1) {
          const adv = Math.floor(this.frac);
          this.frac -= adv;
          this.r = (r + adv) & mask;
          this.count -= adv;
        }
      }
      if (i < len) {
        // Ran dry. Loud right before: a cut in the middle of speech, buffer more.
        this.playing = false;
        if (this.env > 0.01) {
          this.underruns++;
          this.target = Math.min(this.maxTarget, this.target + 0.05 * this.inRate);
          this.clean = 0;
        }
      } else {
        this.clean += len;
        if (this.clean > 20 * sampleRate && this.target > this.minTarget) {
          this.target = Math.max(this.minTarget, this.target - 0.01 * this.inRate);
          this.clean = 0;
        }
      }
    }

    // Rest of the quantum: glide from the last sample to silence (no click).
    let last = i > 0 ? out[i - 1] : this.last;
    for (; i < len; i++) {
      last *= this.decay;
      out[i] = last;
      this.env += ((last < 0 ? -last : last) - this.env) * this.envK;
    }
    this.last = Math.abs(last) < 1e-6 ? 0 : last;

    for (let k = 0; k < len; k++) this.sumSq += out[k] * out[k];
    this.n += len;
    if (this.n >= 4096) {
      this.port.postMessage({
        rms: Math.sqrt(this.sumSq / this.n),
        buffered: this.count / this.inRate,
        target: this.target / this.inRate,
        underruns: this.underruns,
      });
      this.n = 0;
      this.sumSq = 0;
    }
    return true;
  }
}

registerProcessor('translation-player', TranslationPlayer);
