"""Per-listener outbound queues so one slow phone never blocks the others."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

log = logging.getLogger(__name__)

MAX_PENDING_AUDIO = 12   # ~0.5 s of 40 ms frames; older audio is useless for live
MAX_PENDING_TEXT = 300


class Client:
    __slots__ = ("ws", "channel", "audio", "_q", "_audio_pending", "_task", "dropped", "ip")

    def __init__(self, ws: web.WebSocketResponse, ip: str = "") -> None:
        self.ws = ws
        self.ip = ip
        self.channel = None          # Channel the client listens to
        self.audio = False           # wants Opus frames over the socket
        self._q: asyncio.Queue = asyncio.Queue()
        self._audio_pending = 0
        self.dropped = 0
        self._task = asyncio.create_task(self._writer(), name="client-writer")

    def send_text(self, s: str) -> None:
        if self._q.qsize() > MAX_PENDING_TEXT:
            self.dropped += 1
            return
        self._q.put_nowait(s)

    def send_audio(self, b: bytes) -> None:
        if self._audio_pending >= MAX_PENDING_AUDIO:
            self.dropped += 1
            return
        self._audio_pending += 1
        self._q.put_nowait(b)

    async def _writer(self) -> None:
        ws = self.ws
        try:
            while True:
                item = await self._q.get()
                if isinstance(item, bytes):
                    self._audio_pending -= 1
                    await ws.send_bytes(item)
                else:
                    await ws.send_str(item)
        except (asyncio.CancelledError, ConnectionResetError, RuntimeError):
            pass
        except Exception as e:
            log.debug("client writer stopped: %s", e)

    def close(self) -> None:
        self._task.cancel()


class StreamSink:
    """Queue feeding one HTTP MP3 stream response."""

    __slots__ = ("q", "dropped")

    def __init__(self) -> None:
        self.q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=40)
        self.dropped = 0

    def push(self, data: bytes) -> None:
        try:
            self.q.put_nowait(data)
        except asyncio.QueueFull:
            self.dropped += 1
