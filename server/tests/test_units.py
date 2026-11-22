"""Small async units: listener buffers, MP3 sink, Gemini message handling, WAV source."""

from __future__ import annotations

import asyncio
import json
import wave

import numpy as np

from app import clients
from app.audio.sources import FileSource
from app.clients import Client, StreamSink
from app.gemini import GeminiConnection, redact


class StuckWs:
    """A phone that stopped reading: every send blocks forever."""

    def __init__(self) -> None:
        self.closed_with = None

    async def send_str(self, s):
        await asyncio.Event().wait()

    async def send_bytes(self, b):
        await asyncio.Event().wait()

    async def close(self, code=1000, message=b""):
        self.closed_with = (code, message)


class RecordingWs:
    def __init__(self) -> None:
        self.sent: list = []

    async def send_str(self, s):
        self.sent.append(s)

    async def send_bytes(self, b):
        self.sent.append(b)


def test_slow_client_is_disconnected_not_corrupted():
    async def main():
        ws = StuckWs()
        c = Client(ws)
        for i in range(clients.MAX_PENDING_TEXT + 5):
            c.send_text(f"msg {i}")
        await asyncio.sleep(0.05)
        assert c.closed
        assert ws.closed_with and ws.closed_with[0] == 1013
    asyncio.run(main())


def test_audio_drops_oldest_and_text_keeps_order():
    async def main():
        ws = StuckWs()
        c = Client(ws)
        await asyncio.sleep(0)          # writer blocks on the first item
        for i in range(clients.MAX_PENDING_AUDIO + 3):
            c.send_audio(bytes([i]))
        assert c.dropped >= 3
        assert c._audio_q[-1] == bytes([clients.MAX_PENDING_AUDIO + 2])
        c.close()

        ws2 = RecordingWs()
        c2 = Client(ws2)
        for i in range(5):
            c2.send_text(str(i))
        c2.send_audio(b"a")
        await asyncio.sleep(0.01)
        assert ws2.sent == ["0", "1", "2", "3", "4", b"a"]
        c2.close()
    asyncio.run(main())


def test_message_rate_limit():
    async def main():
        ws = StuckWs()
        c = Client(ws)
        allowed = sum(c.allow_message() for _ in range(int(clients.MSG_BURST) + 10))
        assert allowed == int(clients.MSG_BURST)
        for _ in range(clients.MSG_ABUSE_LIMIT):
            c.allow_message()
        await asyncio.sleep(0.01)
        assert c.closed and ws.closed_with[0] == 1008
    asyncio.run(main())


def test_stream_sink_keeps_live_edge():
    async def main():
        s = StreamSink()
        chunk = b"x" * 1000
        for _ in range(40):
            s.push(chunk)
        data = await s.get(0.1)
        assert len(data) <= StreamSink.MAX_BYTES and s.dropped > 0
        assert await s.get(0.01) == b""        # idle -> empty, not None
        s.close()
        assert await s.get(0.01) is None
    asyncio.run(main())


class Events:
    def __init__(self) -> None:
        self.calls: list = []

    def on_input_text(self, text, lang):
        self.calls.append(("in", text, lang))

    def on_output_text(self, text):
        self.calls.append(("out", text))

    def on_audio(self, pcm):
        self.calls.append(("audio", len(pcm)))

    def on_turn_complete(self):
        self.calls.append(("turn",))

    def on_usage(self, tokens):
        self.calls.append(("usage", tokens))


class Msg:
    def __init__(self, data) -> None:
        import aiohttp
        self.type = aiohttp.WSMsgType.TEXT
        self.data = data if isinstance(data, str) else json.dumps(data)


def test_malformed_gemini_messages_are_ignored():
    ev = Events()
    conn = GeminiConnection(None, "ws://x", "KEY", {}, ev, "[t]")
    for bad in ["[]", "null", "{bad", {"serverContent": []}, {"serverContent": {"modelTurn": {"parts": [1]}}},
                {"serverContent": {"modelTurn": {"parts": [{"inlineData": {"data": "!!notb64"}}]}}},
                {"usageMetadata": {"totalTokenCount": "x"}}, {"goAway": None}]:
        conn._dispatch(Msg(bad))
    assert conn._dispatch(Msg({"goAway": {"timeLeft": "5s"}})) == "goaway"
    conn._dispatch(Msg({"serverContent": {"inputTranscription": {"text": "ahoj", "languageCode": "cs"},
                                          "outputTranscription": {"text": "hello"},
                                          "modelTurn": {"parts": [{"inlineData": {"data": "AAAAAA=="}}]},
                                          "turnComplete": True}}))
    assert ("in", "ahoj", "cs") in ev.calls and ("out", "hello") in ev.calls
    assert ("audio", 4) in ev.calls and ("turn",) in ev.calls


def test_api_key_is_redacted():
    text = "400, message='Bad', url='wss://g.googleapis.com/ws?key=AIzaSyABC123&alt=x'"
    assert "AIzaSyABC123" not in redact(text)
    assert "AIzaSyABC123" not in redact("failed for AIzaSyABC123", "AIzaSyABC123")


def test_empty_wav_does_not_freeze_the_server(tmp_path):
    path = tmp_path / "empty.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)

    async def main():
        got = []
        src = FileSource(got.append, path)
        await src.start()
        await asyncio.sleep(0.1)       # would never return if the loop spun
        assert src.state == "error" and "prázdný" in src.error
        await src.stop()
    asyncio.run(main())


def test_wav_with_truncated_frame_loops(tmp_path):
    path = tmp_path / "short.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes((np.ones(801, dtype="<i2") * 1000).tobytes())  # odd sample count

    async def main():
        got = []
        src = FileSource(got.append, path)
        await src.start()
        await asyncio.sleep(0.3)
        assert src.state == "running"
        await src.stop()
        assert got
    asyncio.run(main())
