"""One target language: a Gemini session, its transcript and its audio fan-out."""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
from typing import TYPE_CHECKING

from .audio.codecs import Mp3Encoder, OpusEncoder
from .clients import Client, StreamSink
from .gemini import GeminiConnection, SetupError, build_setup
from .util import spawn

if TYPE_CHECKING:
    from .engine import Engine

log = logging.getLogger(__name__)

OUT_RATE = 24000
FRAME_S = 0.04                          # 40 ms Opus frames
FRAME_SAMPLES = int(OUT_RATE * FRAME_S)  # 960
FRAME_BYTES = FRAME_SAMPLES * 2
PREBUFFER_S = 0.2                       # absorbs uneven arrival of Gemini audio
PREBUFFER_BYTES = int(OUT_RATE * PREBUFFER_S) * 2
HANGOVER_FRAMES = 12                    # keep clients' clocks running across short gaps
MAX_FIFO_BYTES = OUT_RATE * 2 * 20      # never let latency grow past 20 s
SENTENCE_END = tuple(".!?…。！？")
HISTORY = 80


class Channel:
    def __init__(self, engine: "Engine", lang: str) -> None:
        self.engine = engine
        self.lang = lang
        self.clients: set[Client] = set()
        self.audio_clients: set[Client] = set()
        self.streams: set[StreamSink] = set()

        self.status = "idle"   # idle | connecting | live | reconnecting | error
        self.error = ""
        self.active: GeminiConnection | None = None
        self._task: asyncio.Task | None = None
        self._stop_handle: asyncio.TimerHandle | None = None
        self._wake = asyncio.Event()

        self.segments: collections.deque[dict] = collections.deque(maxlen=HISTORY)
        self._seg: dict | None = None
        self._seg_id = 0
        self._brk = True
        self._last_text = 0.0

        self.fifo = bytearray()
        self._pacer: asyncio.Task | None = None
        self._first_audio = 0.0
        self._last_audio = 0.0
        self.seq = 0

        self.detected = ""
        self.last_output = 0.0
        self.tokens = 0
        self.connects = 0
        self.audio_seconds = 0.0

    # ------------------------------------------------------------- demand

    def update_demand(self) -> None:
        s = self.engine.settings
        enabled = s.on_air and self.lang in s.languages
        wanted = enabled and bool(self.clients or self.streams or self.lang in s.always_on)
        if wanted:
            self._cancel_stop()
            if self._task is None:
                self._task = asyncio.create_task(self._supervise(), name=f"channel-{self.lang}")
                self._update_pacer()
        elif self._task is not None and self._stop_handle is None:
            delay = s.idle_stop_s if enabled else 0
            if delay <= 0:
                spawn(self.stop(), f"stop-{self.lang}")
            else:
                loop = asyncio.get_running_loop()
                self._stop_handle = loop.call_later(delay, lambda: spawn(self._idle_stop(), f"idle-{self.lang}"))

    async def _idle_stop(self) -> None:
        self._stop_handle = None
        s = self.engine.settings
        if not (self.clients or self.streams or self.lang in s.always_on):
            log.info("[%s] no listeners for %d s, closing session", self.lang, s.idle_stop_s)
            await self.stop()

    def _cancel_stop(self) -> None:
        if self._stop_handle is not None:
            self._stop_handle.cancel()
            self._stop_handle = None

    async def stop(self) -> None:
        self._cancel_stop()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._update_pacer()

    async def restart(self) -> None:
        if self._task is not None:
            await self.stop()
        self.update_demand()

    def poke(self) -> None:
        """Settings changed: wake a supervisor that is waiting to retry."""
        self._wake.set()

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    # ------------------------------------------------------------- session

    def _set_status(self, status: str, error: str = "") -> None:
        if status == self.status and error == self.error:
            return
        self.status, self.error = status, error
        self._broadcast(json.dumps({"t": "ch", "lang": self.lang, "status": status}))

    async def _supervise(self) -> None:
        engine = self.engine
        backoff = 1.0
        first = True
        try:
            while True:
                if self.active is not None and self.active.ws is not None and self.active.ws.closed:
                    self.active = None
                if self.active is None:
                    self._set_status("connecting" if first else "reconnecting", self.error)
                first = False

                key = engine.store.api_key
                if not key:
                    self._set_status("error", "Chybí API klíč Gemini. Zadejte ho v administraci.")
                    await self._sleep(3600)
                    continue

                s = engine.settings
                setup = build_setup(s.model, self.lang, s.echo_target, s.system_instruction,
                                    engine.placement)
                conn = GeminiConnection(engine.http, engine.env.gemini_ws_url, key, setup, self,
                                        f"[{self.lang}]")
                try:
                    await conn.open()
                except SetupError as e:
                    await conn.close()
                    reason = getattr(e, "reason", "")
                    if reason and engine.placement.adapt(reason):
                        log.info("[%s] adjusted setup layout, retrying", self.lang)
                        continue
                    log.warning("[%s] %s", self.lang, e)
                    if self.active is None:
                        self._set_status("error" if e.fatal else "reconnecting", str(e))
                    await self._sleep(30 if e.fatal else backoff)
                    backoff = min(backoff * 2, 20)
                    continue

                old, self.active = self.active, conn
                if old is not None:
                    old.close_later(6.0)   # let the old session finish its sentence
                self.connects += 1
                backoff = 1.0
                self._set_status("live")
                log.info("[%s] translation session live", self.lang)

                if await conn.wait() == "goaway":
                    continue  # open the next session while this one keeps running
                if self.active is conn:
                    self.active = None
                await conn.close()
                self._set_status("reconnecting", conn.close_reason or "Spojení s Google se přerušilo.")
                await self._sleep(0.3 if backoff <= 1 else backoff)
                backoff = min(backoff * 2, 20)
        except asyncio.CancelledError:
            pass
        finally:
            conn, self.active = self.active, None
            if conn is not None:
                await conn.close()
            self._set_status("idle")

    def send_audio(self, message: str) -> None:
        conn = self.active
        if conn is not None:
            conn.send_audio(message)

    # ------------------------------------------------------------- Gemini events

    def on_input_text(self, text: str, lang: str | None) -> None:
        if lang:
            self.detected = lang
        self._append("src", text)

    def on_output_text(self, text: str) -> None:
        self._append("tr", text)

    def on_turn_complete(self) -> None:
        self._brk = True

    def on_usage(self, tokens: int) -> None:
        self.tokens += tokens

    def on_audio(self, pcm: bytes) -> None:
        self.last_output = time.time()
        self.audio_seconds += len(pcm) / (OUT_RATE * 2)
        if self._pacer is None:
            return
        now = time.monotonic()
        if not self.fifo:
            self._first_audio = now
        self._last_audio = now
        self.fifo += pcm
        if len(self.fifo) > MAX_FIFO_BYTES:
            del self.fifo[: len(self.fifo) - MAX_FIFO_BYTES]

    def _append(self, kind: str, text: str) -> None:
        now = time.monotonic()
        seg = self._seg
        if (seg is None or self._brk or now - self._last_text > 4.0
                or (len(seg["tr"]) > 320 and seg["tr"].rstrip().endswith(SENTENCE_END))
                or len(seg["src"]) > 600):
            self._seg_id += 1
            seg = {"s": self._seg_id, "src": "", "tr": "", "t": int(time.time())}
            self.segments.append(seg)
            self._seg = seg
            self._brk = False
        if not seg[kind]:
            text = text.lstrip()
            if not text:
                return
        seg[kind] += text
        self._last_text = now
        self._broadcast(json.dumps({"t": "tx", "s": seg["s"], "k": kind, "x": text},
                                   ensure_ascii=False))

    def history_message(self) -> str:
        segs = list(self.segments)[-40:]
        return json.dumps({"t": "hist", "lang": self.lang, "segs": segs}, ensure_ascii=False)

    def clear_history(self) -> None:
        self.segments.clear()
        self._seg = None
        self._broadcast(json.dumps({"t": "hist", "lang": self.lang, "segs": []}))

    # ------------------------------------------------------------- listeners

    def _broadcast(self, s: str) -> None:
        for c in self.clients:
            c.send_text(s)

    def add_client(self, client: Client, audio: bool) -> None:
        self.clients.add(client)
        client.channel = self
        self.set_audio(client, audio)
        client.send_text(json.dumps({"t": "ch", "lang": self.lang, "status": self.status}))
        client.send_text(self.history_message())
        self.update_demand()

    def remove_client(self, client: Client) -> None:
        self.clients.discard(client)
        self.audio_clients.discard(client)
        if client.channel is self:
            client.channel = None
        self._update_pacer()
        self.update_demand()

    def set_audio(self, client: Client, on: bool) -> None:
        client.audio = on and OpusEncoder.available()
        if client.audio:
            self.audio_clients.add(client)
        else:
            self.audio_clients.discard(client)
        self._update_pacer()

    def add_stream(self, sink: StreamSink) -> None:
        self.streams.add(sink)
        self.update_demand()
        self._update_pacer()

    def remove_stream(self, sink: StreamSink) -> None:
        self.streams.discard(sink)
        self._update_pacer()
        self.update_demand()

    # ------------------------------------------------------------- audio pacing

    def _update_pacer(self) -> None:
        need = self._task is not None and bool(self.audio_clients or self.streams)
        if need and self._pacer is None:
            self._pacer = asyncio.create_task(self._pace(), name=f"pacer-{self.lang}")
        elif not need and self._pacer is not None:
            self._pacer.cancel()
            self._pacer = None
            self.fifo.clear()

    async def _pace(self) -> None:
        """Real-time clock for the outgoing audio.

        Gemini delivers audio in uneven bursts. The pacer turns it into a steady
        40 ms frame stream: Opus frames for app listeners (only while there is
        speech) and a continuous MP3 stream for background listeners.
        """
        loop = asyncio.get_running_loop()
        opus = OpusEncoder(OUT_RATE, FRAME_SAMPLES) if OpusEncoder.available() else None
        mp3: Mp3Encoder | None = None
        silence = bytes(FRAME_BYTES)
        fifo = self.fifo
        playing = False
        hang = 0
        t = loop.time()
        try:
            while True:
                t += FRAME_S
                delay = t - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                elif delay < -1.0:
                    t = loop.time()   # event loop stalled: resync instead of bursting

                now = time.monotonic()
                if not playing and fifo and (len(fifo) >= PREBUFFER_BYTES
                                             or now - self._first_audio >= PREBUFFER_S):
                    playing = True
                frame = silence
                if playing:
                    if len(fifo) >= FRAME_BYTES:
                        frame = bytes(fifo[:FRAME_BYTES])
                        del fifo[:FRAME_BYTES]
                    elif fifo and now - self._last_audio > 0.3:
                        frame = bytes(fifo).ljust(FRAME_BYTES, b"\0")
                        fifo.clear()
                    else:
                        playing = False
                if frame is not silence:
                    hang = HANGOVER_FRAMES
                elif hang:
                    hang -= 1

                if (frame is not silence or hang) and opus is not None and self.audio_clients:
                    self.seq = (self.seq + 1) & 0xFFFFFFFF
                    packet = b"\x01" + self.seq.to_bytes(4, "big") + opus.encode(frame)
                    for c in self.audio_clients:
                        c.send_audio(packet)

                if self.streams:
                    if mp3 is None and Mp3Encoder.available():
                        mp3 = Mp3Encoder(OUT_RATE)
                    if mp3 is not None:
                        data = mp3.encode(frame)
                        if data:
                            for sink in self.streams:
                                sink.push(data)
                elif mp3 is not None:
                    mp3.close()
                    mp3 = None
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("[%s] audio pacer failed", self.lang)
        finally:
            if opus is not None:
                opus.close()
            if mp3 is not None:
                mp3.close()

    # ------------------------------------------------------------- admin

    def info(self) -> dict:
        return {
            "lang": self.lang,
            "status": self.status,
            "error": self.error,
            "listeners": len(self.clients),
            "audio": len(self.audio_clients),
            "streams": len(self.streams),
            "detected": self.detected,
            "lastOutput": round(time.time() - self.last_output, 1) if self.last_output else None,
            "tokens": self.tokens,
            "connects": self.connects,
            "audioMinutes": round(self.audio_seconds / 60, 1),
            "stopping": self._stop_handle is not None,
        }

    async def shutdown(self) -> None:
        await self.stop()
        for c in list(self.clients):
            c.send_text(json.dumps({"t": "ch", "lang": self.lang, "status": "removed"}))
            c.channel = None
        self.clients.clear()
        self.audio_clients.clear()
