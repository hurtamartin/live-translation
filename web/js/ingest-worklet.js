// Operator-side capture: picks one input channel (or mixes L+R), decimates to
// 16 kHz and emits 100 ms blocks of Int16 PCM for /ws/ingest.

class Ingest extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = (options && options.processorOptions) || {};
    this.channel = Number.isInteger(o.channel) ? o.channel : -1;
    this.ratio = sampleRate / 16000;
    this.pos = 0;
    this.acc = 0;
    this.cnt = 0;
    this.out = new Int16Array(1600);
    this.n = 0;
    this.port.onmessage = (e) => {
      if (e.data && Number.isInteger(e.data.channel)) this.channel = e.data.channel;
    };
  }

  process(inputs) {
    const inp = inputs[0];
    if (!inp || !inp.length || !inp[0]) return true;
    const chans = inp.length;
    const pick = this.channel >= 0 && this.channel < chans ? inp[this.channel] : null;
    const len = inp[0].length;
    for (let i = 0; i < len; i++) {
      let v;
      if (pick) v = pick[i];
      else { v = 0; for (let c = 0; c < chans; c++) v += inp[c][i]; v /= chans; }
      this.acc += v;
      this.cnt++;
      this.pos++;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        let s = this.acc / this.cnt;
        this.acc = 0;
        this.cnt = 0;
        if (s > 1) s = 1; else if (s < -1) s = -1;
        this.out[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
        if (this.n === 1600) {
          const buf = this.out.buffer;
          this.port.postMessage(buf, [buf]);
          this.out = new Int16Array(1600);
          this.n = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor('ingest', Ingest);
