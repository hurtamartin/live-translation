"""Audio path units: jitter buffer, session hand-over, hung session watchdog."""

from __future__ import annotations

import asyncio

import numpy as np

from app.audio import jitter as jb_mod
from app.audio.jitter import JitterBuffer
from app.channel import Channel
from app.gemini import STALL_BLOCKS, GeminiConnection, Placement

RATE = 24000
FRAME = 960
TICK = FRAME / RATE   # 40 ms


def tone(seconds: float, amp: float = 0.3) -> bytes:
    n = int(seconds * RATE)
    return (amp * 32767 * np.sin(2 * np.pi * 220 * np.arange(n) / RATE)).astype("<i2").tobytes()


def dc(seconds: float, value: int = 12000) -> bytes:
    return np.full(int(seconds * RATE), value, dtype="<i2").tobytes()


def silence(seconds: float) -> bytes:
    return bytes(int(seconds * RATE) * 2)


def samples(frame: bytes | None) -> np.ndarray:
    return np.zeros(FRAME, dtype="<i2") if frame is None else np.frombuffer(frame, dtype="<i2")


def max_step(frames: list[bytes | None]) -> int:
    """Largest jump between consecutive samples across the whole output."""
    x = np.concatenate([samples(f) for f in frames]).astype(np.int32)
    return int(np.max(np.abs(np.diff(x))))


# ---------------------------------------------------------------- jitter buffer

def test_prebuffers_then_plays_continuously():
    jb = JitterBuffer(RATE, FRAME)
    jb.push(tone(0.1), 0.0)
    assert jb.next_frame(0.04) is None, "waits for the prebuffer"
    jb.push(tone(1.0), 0.05)
    frames = [jb.next_frame(0.08 + i * TICK) for i in range(25)]
    assert all(f is not None for f in frames)
    assert jb.underruns == 0


def test_starts_and_stops_without_clicks():
    jb = JitterBuffer(RATE, FRAME)
    jb.push(dc(0.4), 0.0)                 # worst case: a hard DC step
    frames = [jb.next_frame(i * TICK) for i in range(1, 20)]
    first = samples(next(f for f in frames if f is not None))
    assert abs(int(first[0])) < 500, "faded in"
    # 12000 / 96 samples of ramp ~ 125 per sample; a raw cut would jump 12000
    assert max_step(frames) < 400


def test_underrun_mid_speech_grows_prebuffer_and_rebuffers():
    jb = JitterBuffer(RATE, FRAME)
    t = 0.0
    signal, pos = tone(3.0), int(0.2 * RATE) * 2
    jb.push(signal[:pos], t)
    out = []
    chunk = int(0.03 * RATE) * 2
    for _ in range(40):
        # Google delivers a bit slower than real time: the buffer runs dry mid-speech.
        jb.push(signal[pos:pos + chunk], t)
        pos += chunk
        t += TICK
        out.append(jb.next_frame(t))
        if jb.underruns:
            break
    assert jb.underruns == 1
    assert jb.prebuffer > jb_mod.PREBUFFER_MIN
    assert max_step(out) < 3000, "cut is faded, not a click"
    # Late audio arrives: it waits for the (bigger) prebuffer instead of stuttering.
    jb.push(tone(0.1), t)
    assert jb.next_frame(t + TICK) is None


def test_clean_utterances_shrink_prebuffer_again():
    jb = JitterBuffer(RATE, FRAME)
    jb.prebuffer = jb_mod.PREBUFFER_MAX
    t = 0.0
    for _ in range(5):
        jb.push(tone(0.5) + silence(0.1), t)   # speech ending in silence
        for _ in range(30):
            t += TICK
            jb.next_frame(t)
        t += 1.0
    assert jb.underruns == 0
    assert jb.prebuffer < jb_mod.PREBUFFER_MAX


def test_partial_last_frame_is_padded_and_faded():
    jb = JitterBuffer(RATE, FRAME)
    jb.push(dc(0.21), 0.0)                # not a multiple of 40 ms, nothing follows
    frames = []
    t = 0.5
    for _ in range(12):
        t += TICK
        frames.append(jb.next_frame(t))
    played = [f for f in frames if f is not None]
    assert samples(played[-1])[-1] == 0
    assert max_step(frames) < 400
    assert jb.underruns == 0


def test_catch_up_skips_only_quiet_frames():
    jb = JitterBuffer(RATE, FRAME)
    jb.push(tone(1.2) + silence(1.0) + tone(1.2), 0.0)
    t = 0.0
    loud = 0
    for _ in range(200):
        t += TICK
        f = jb.next_frame(t)
        if f is not None and np.max(np.abs(samples(f))) > 1000:
            loud += 1
    assert jb.skipped > 0
    assert loud >= int(2.4 / TICK) - 1, "no speech was dropped"


def test_backlog_is_bounded():
    jb = JitterBuffer(RATE, FRAME)
    for i in range(12):
        jb.push(tone(1.0), i * 0.01)
    assert jb.backlog <= jb_mod.MAX_BACKLOG_S
    assert jb.trimmed > 0


# ---------------------------------------------------------------- session hand-over

class FakeConn:
    def __init__(self) -> None:
        self.alive = True
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        self.alive = False


def test_goaway_handover_does_not_interleave_sessions():
    async def main():
        from app.channel import _Session

        ch = Channel(None, "en")
        ch._pacer = object()               # pretend listeners: audio goes to the jitter buffer
        old_src, new_src = _Session(ch), _Session(ch)
        ch._active_src = old_src
        old_src.on_output_text("old one")
        ch._active_src = new_src
        old = FakeConn()
        ch._hand_over(old, old_src)

        new_src.on_output_text("new one")  # held back
        new_src.on_audio(b"\x02\x00" * 4)
        old_src.on_output_text(" old two")
        old_src.on_audio(b"\x01\x00" * 4)
        assert [s["tr"] for s in ch.segments] == ["old one old two"]

        old_src.on_turn_complete()         # old session finished its sentence
        await asyncio.sleep(0.15)
        assert old.closed and ch._draining is None
        assert [s["tr"] for s in ch.segments] == ["old one old two", "new one"]
        assert bytes(ch.jitter.fifo) == b"\x01\x00" * 4 + b"\x02\x00" * 4
        ch._pacer = None
    asyncio.run(main())


def test_handover_waits_at_most_until_old_session_is_quiet(monkeypatch):
    monkeypatch.setattr("app.channel.DRAIN_QUIET", 0.1)

    async def main():
        from app.channel import _Session

        ch = Channel(None, "en")
        old_src, new_src = _Session(ch), _Session(ch)
        ch._active_src = new_src
        old = FakeConn()
        ch._hand_over(old, old_src)
        new_src.on_output_text("hello")
        assert not ch.segments
        await asyncio.sleep(0.3)
        assert old.closed and [s["tr"] for s in ch.segments] == ["hello"]
    asyncio.run(main())


# ---------------------------------------------------------------- Gemini session

def test_hung_session_is_detected():
    async def main():
        conn = GeminiConnection(None, "ws://x", "KEY", {}, None, "[t]")
        conn.ended = asyncio.get_running_loop().create_future()
        for _ in range(STALL_BLOCKS - 1):
            conn.send_audio("{}")
        assert not conn.ended.done()
        conn.send_audio("{}")
        assert await conn.wait() == "stalled"
        assert conn.close_reason
    asyncio.run(main())


def test_rejected_stream_end_is_switched_off():
    p = Placement()
    assert p.stream_end
    assert p.adapt("Invalid JSON payload received. Unknown name \"audioStreamEnd\" at 'realtime_input'")
    assert not p.stream_end
    assert not p.adapt("Unknown name \"audioStreamEnd\" at 'realtime_input'"), "only once"
