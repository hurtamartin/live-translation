"""Server-side jitter buffer for translated audio.

Gemini delivers 24 kHz PCM in uneven bursts. The channel's pacer pulls one
40 ms frame per tick from this buffer and encodes it once for all listeners.

What makes the result sound smooth:

- Prebuffer before an utterance starts, so normal delivery jitter never runs
  the buffer dry. The prebuffer grows after every underrun in the middle of
  speech and slowly shrinks again while utterances play cleanly: as little
  delay as the current connection to Google allows.
- After an underrun the buffer refills before it plays again, instead of
  stuttering frame by frame.
- No hard edges: a cut in the middle of a waveform is a click. The signal is
  faded to zero when the buffer runs dry and faded in when audio resumes.
- Bounded delay: while far behind, quiet frames (pauses between words) are
  skipped, which catches up without audible artefacts. Only an absurd backlog
  is trimmed outright.
"""

from __future__ import annotations

import numpy as np

PREBUFFER_MIN = 0.2      # s buffered before an utterance starts playing
PREBUFFER_MAX = 0.6
PREBUFFER_STEP = 0.1     # added after each underrun in the middle of speech
PREBUFFER_DECAY = 0.02   # removed after each utterance that played without one
END_GAP = 0.3            # s without new audio: the utterance has ended
CATCHUP_S = 1.0          # backlog above which quiet frames are skipped
CATCHUP_PER_TICK = 2     # skipped frames per tick (catch up at most 3x real time)
MAX_BACKLOG_S = 8.0      # older audio is dropped: nobody wants to hear it this late
FADE_S = 0.004           # fade in/out length

LOUD_RMS = 32768 * 10 ** (-40 / 20)    # last frame this loud: the cut is mid-speech
QUIET_RMS = 32768 * 10 ** (-50 / 20)   # frame this quiet can be skipped to catch up


def _rms(x: np.ndarray) -> float:
    if not x.size:
        return 0.0
    f = x.astype(np.float32)
    return float(np.sqrt(np.dot(f, f) / f.size))


class JitterBuffer:
    """Bursty 16-bit mono PCM in, steady frames out. Not thread-safe (event loop only)."""

    def __init__(self, rate: int, frame_samples: int) -> None:
        self.rate = rate
        self.frame_samples = frame_samples
        self.frame_bytes = frame_samples * 2
        self.fifo = bytearray()
        self.prebuffer = PREBUFFER_MIN
        self.playing = False
        self._waiting_since = 0.0   # when the data now in the buffer started waiting
        self._last_in = 0.0
        self._last_sample = 0       # last sample sent, for a click-free fade to silence
        self._last_rms = 0.0
        self._fade_in = False
        self._clean = True          # current utterance had no underrun
        n = max(2, int(rate * FADE_S))
        self._ramp_up = np.linspace(0.0, 1.0, n, endpoint=False, dtype=np.float32)
        self._ramp_down = self._ramp_up[::-1].copy()
        self._max_bytes = int(MAX_BACKLOG_S * rate) * 2
        self._catchup_bytes = int(CATCHUP_S * rate) * 2
        # Statistics for the admin page.
        self.underruns = 0
        self.skipped = 0
        self.trimmed = 0

    # ------------------------------------------------------------- input

    def push(self, pcm: bytes, now: float) -> None:
        if not self.fifo:
            self._waiting_since = now
        self._last_in = now
        self.fifo += pcm
        excess = len(self.fifo) - self._max_bytes
        if excess > 0:
            del self.fifo[: excess + (excess & 1)]   # stays 16-bit aligned
            self.trimmed += 1
            self._fade_in = True

    def clear(self) -> None:
        self.fifo.clear()
        self.playing = False
        self._last_sample = 0
        self._last_rms = 0.0

    @property
    def backlog(self) -> float:
        """Seconds of audio waiting to be played."""
        return len(self.fifo) / (2 * self.rate)

    # ------------------------------------------------------------- output

    def next_frame(self, now: float) -> bytes | None:
        """The next frame to play, or None for silence."""
        fifo, fb = self.fifo, self.frame_bytes
        if not self.playing:
            if not fifo:
                return self._tail()
            if (len(fifo) < int(self.prebuffer * self.rate) * 2
                    and now - self._waiting_since < self.prebuffer):
                return self._tail()
            self.playing = True
            self._fade_in = True

        if len(fifo) >= fb:
            skips = 0
            while (skips < CATCHUP_PER_TICK and len(fifo) > self._catchup_bytes
                   and _rms(np.frombuffer(bytes(fifo[:fb]), dtype="<i2")) < QUIET_RMS):
                del fifo[:fb]
                skips += 1
            self.skipped += skips
            frame = np.frombuffer(bytes(fifo[:fb]), dtype="<i2")
            del fifo[:fb]
            return self._shape(frame)

        if fifo and now - self._last_in > END_GAP:
            # The utterance ended with a partial frame: fade it out and pad.
            part = np.frombuffer(bytes(fifo), dtype="<i2").astype(np.float32)
            fifo.clear()
            n = min(part.size, self._ramp_down.size)
            part[part.size - n:] *= self._ramp_down[self._ramp_down.size - n:]
            frame = np.zeros(self.frame_samples, dtype=np.float32)
            frame[: part.size] = part
            self._utterance_end(loud=False)
            out = self._shape(frame)
            self._last_sample = 0
            return out

        # Ran dry while more audio may follow.
        self._utterance_end(loud=self._last_rms >= LOUD_RMS and now - self._last_in <= END_GAP)
        self._waiting_since = now
        return self._tail()

    def _utterance_end(self, loud: bool) -> None:
        self.playing = False
        if loud:
            # Cut in the middle of speech: Google is late. Buffer more from now on.
            self.underruns += 1
            self._clean = False
            self.prebuffer = min(PREBUFFER_MAX, self.prebuffer + PREBUFFER_STEP)
            return
        if self._clean:
            self.prebuffer = max(PREBUFFER_MIN, self.prebuffer - PREBUFFER_DECAY)
        self._clean = True

    def _shape(self, frame: np.ndarray) -> bytes:
        if self._fade_in:
            self._fade_in = False
            f = frame.astype(np.float32)
            f[: self._ramp_up.size] *= self._ramp_up
            frame = f
        if frame.dtype != np.dtype("<i2"):
            frame = np.clip(np.rint(frame), -32768, 32767).astype("<i2")
        self._last_sample = int(frame[-1])
        self._last_rms = _rms(frame)
        return frame.tobytes()

    def _tail(self) -> bytes | None:
        """Silence, starting with a short ramp from the last sample if it was not zero."""
        if not self._last_sample:
            return None
        frame = np.zeros(self.frame_samples, dtype="<i2")
        n = self._ramp_down.size
        frame[:n] = np.rint(self._last_sample * self._ramp_down).astype("<i2")
        self._last_sample = 0
        self._last_rms = 0.0
        return frame.tobytes()

    def stats(self) -> dict:
        return {
            "underruns": self.underruns,
            "skippedMs": round(self.skipped * self.frame_samples * 1000 / self.rate),
            "trimmed": self.trimmed,
            "prebufferMs": round(self.prebuffer * 1000),
            "backlogMs": round(self.backlog * 1000),
        }
