"""Per-listener outbound buffers so one slow phone never blocks the others."""

from __future__ import annotations

import asyncio
import collections
import logging
import time

from aiohttp import web

from .util import spawn

log = logging.getLogger(__name__)

MAX_PENDING_AUDIO = 12            # ~0.5 s of 40 ms frames; older audio is useless live
MAX_PENDING_TEXT = 400            # messages
MAX_PENDING_TEXT_CHARS = 1 << 20  # a client this far behind is dropped and reconnects

# Incoming messages: the app sends a ping every 15 s and a subscribe on user action.
MSG_RATE = 2.0                    # tokens per second
MSG_BURST = 20.0
MSG_ABUSE_LIMIT = 100             # messages over the limit before the socket is closed


class Client:
    """One listener WebSocket.

    Text (transcript, status) is never dropped: losing a delta would corrupt the
    transcript, so a client that falls hopelessly behind is disconnected instead
    and gets a fresh history when the app reconnects. Audio frames are dropped
    oldest-first so a stalled phone resumes at the live edge.
    """

    __slots__ = ("ws", "ip", "channel", "audio", "dropped", "closed", "kicked", "_text",
                 "_text_chars", "_audio_q", "_wake", "_task", "_tokens", "_tokens_at", "_over")

    def __init__(self, ws: web.WebSocketResponse, ip: str = "") -> None:
        self.ws = ws
        self.ip = ip
        self.channel = None          # Channel the client listens to
        self.audio = False           # wants Opus frames over the socket
        self.dropped = 0
        self.closed = False
        self.kicked: tuple[int, str] | None = None   # close code and reason when kicked
        self._text: collections.deque[str] = collections.deque()
        self._text_chars = 0
        self._audio_q: collections.deque[bytes] = collections.deque(maxlen=MAX_PENDING_AUDIO)
        self._wake = asyncio.Event()
        self._tokens = MSG_BURST
        self._tokens_at = time.monotonic()
        self._over = 0
        self._task = asyncio.create_task(self._writer(), name="client-writer")

    # ------------------------------------------------------------- outbound

    def send_text(self, s: str) -> None:
        if self.closed:
            return
        if len(self._text) >= MAX_PENDING_TEXT or self._text_chars > MAX_PENDING_TEXT_CHARS:
            self.kick(1013, "too slow")
            return
        self._text.append(s)
        self._text_chars += len(s)
        self._wake.set()

    def send_audio(self, b: bytes) -> None:
        if self.closed:
            return
        q = self._audio_q
        if len(q) == MAX_PENDING_AUDIO:
            self.dropped += 1        # deque(maxlen) drops the oldest frame
        q.append(b)
        self._wake.set()

    async def _writer(self) -> None:
        ws, text, audio, wake = self.ws, self._text, self._audio_q, self._wake
        try:
            while True:
                await wake.wait()
                wake.clear()
                # Drain everything queued; send_* only suspends when the socket
                # buffer is full, so this is one wake-up per burst, not per item.
                while text or audio:
                    if text:
                        s = text.popleft()
                        self._text_chars -= len(s)
                        await ws.send_str(s)
                    else:
                        await ws.send_bytes(audio.popleft())
        except asyncio.CancelledError:
            pass
        except (ConnectionResetError, RuntimeError):
            self.closed = True       # socket gone; the handler will clean up
        except Exception as e:
            self.closed = True
            log.debug("client writer stopped: %s", e)

    # ------------------------------------------------------------- inbound

    def allow_message(self) -> bool:
        """Token bucket for incoming messages. Closes the socket on sustained abuse."""
        now = time.monotonic()
        self._tokens = min(MSG_BURST, self._tokens + (now - self._tokens_at) * MSG_RATE)
        self._tokens_at = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        self._over += 1
        if self._over == MSG_ABUSE_LIMIT:
            log.warning("Closing listener %s: message flood", self.ip or "?")
            self.kick(1008, "too many messages")
        return False

    # ------------------------------------------------------------- lifecycle

    def kick(self, code: int, reason: str) -> None:
        """Close the socket from outside the handler (never mutates channel sets)."""
        if self.closed:
            return
        self.closed = True
        self.kicked = (code, reason)
        self._task.cancel()
        self._text.clear()
        self._audio_q.clear()
        # Needed when the handler is idle in receive(); if it is busy instead, it
        # sees `closed` first and calls close_kicked() itself. The first close wins.
        spawn(self.close_kicked(), "client-kick")

    async def close_kicked(self) -> None:
        if self.kicked is None:
            return
        code, reason = self.kicked
        try:
            await asyncio.wait_for(self.ws.close(code=code, message=reason.encode()), 5)
        except Exception:
            pass

    def close(self) -> None:
        self.closed = True
        self._task.cancel()


class StreamSink:
    """Buffer feeding one HTTP MP3 stream response.

    Holds whole MP3 frames (CBR, no bit reservoir), so dropping from the front
    keeps the stream decodable and the listener near the live edge.
    """

    MAX_BYTES = 16 * 1024            # ~3 s at 40 kbps

    __slots__ = ("_chunks", "_bytes", "_wake", "dropped", "closed")

    def __init__(self) -> None:
        self._chunks: collections.deque[bytes] = collections.deque()
        self._bytes = 0
        self._wake = asyncio.Event()
        self.dropped = 0
        self.closed = False

    def push(self, data: bytes) -> None:
        if self.closed:
            return
        self._chunks.append(data)
        self._bytes += len(data)
        while self._bytes > self.MAX_BYTES and len(self._chunks) > 1:
            self._bytes -= len(self._chunks.popleft())
            self.dropped += 1
        self._wake.set()

    def close(self) -> None:
        self.closed = True
        self._wake.set()

    async def get(self, timeout: float) -> bytes | None:
        """Everything buffered (one write), b"" after `timeout` idle s, None when closed."""
        if not self._chunks and not self.closed:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout)
            except asyncio.TimeoutError:
                return b""
        self._wake.clear()
        if self.closed:
            return None
        data = b"".join(self._chunks)
        self._chunks.clear()
        self._bytes = 0
        return data
