"""One target language: a Gemini session, its transcript and its audio fan-out."""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
from typing import TYPE_CHECKING, Callable

from .audio.codecs import Mp3Encoder, OpusEncoder
from .audio.jitter import JitterBuffer
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
HANGOVER_FRAMES = 12                    # keep clients' clocks running across short gaps
SENTENCE_END = tuple(".!?…。！？")
HISTORY = 80
HISTORY_SENT = 40                       # segments a newly joined listener receives
GAP_BLOCKS = 50                         # audio kept while reconnecting (5 s of 100 ms blocks)
GAP_MAX_AGE = 8.0                       # older buffered audio is stale, not worth translating
DRAIN_S = 6.0                           # an old session may finish its sentence after goAway...
DRAIN_QUIET = 1.0                       # ...until it has been silent this long
HELD_MAX = 2000                         # events of the new session held back meanwhile
MAX_BACKOFF = 20.0


class _Session:
    """Routes the events of one Gemini session to its channel.

    After goAway the old session keeps translating what it has already heard
    while the new one starts. Their output must not interleave (alternating
    chunks of two voices, words of two sentences mixed in one block), so the
    channel holds the new session's output back until the old one is done.
    """

    __slots__ = ("ch", "last_event", "finished")

    def __init__(self, ch: "Channel") -> None:
        self.ch = ch
        self.last_event = 0.0
        self.finished = False

    def on_input_text(self, text: str, lang: str | None) -> None:
        self.ch._route(self, self.ch.on_input_text, text, lang)

    def on_output_text(self, text: str) -> None:
        self.ch._route(self, self.ch.on_output_text, text)

    def on_audio(self, pcm: bytes) -> None:
        self.ch._route(self, self.ch.on_audio, pcm)

    def on_turn_complete(self) -> None:
        self.finished = True
        self.ch._route(self, self.ch.on_turn_complete)

    def on_usage(self, tokens: int) -> None:
        self.ch.on_usage(tokens)   # billed either way


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
        # Input that arrives while a dropped session is being replaced; sent to the
        # new session so no words are lost to the gap.
        self._gap: collections.deque[tuple[float, str]] = collections.deque(maxlen=GAP_BLOCKS)
        # Session hand-over (goAway): see _Session.
        self._active_src: _Session | None = None
        self._draining: _Session | None = None
        self._drain_task: asyncio.Task | None = None
        self._held: collections.deque[tuple[Callable, tuple]] = collections.deque(maxlen=HELD_MAX)

        self.segments: collections.deque[dict] = collections.deque(maxlen=HISTORY)
        self._seg: dict | None = None
        self._seg_id = 0
        self._brk = True
        self._last_text = 0.0
        self._hist_cache: str | None = None

        self.jitter = JitterBuffer(OUT_RATE, FRAME_SAMPLES)
        self._pacer: asyncio.Task | None = None
        self.seq = 0
        self.stalls = 0

        self.detected = ""
        self.last_output = 0.0
        self.tokens = 0
        self.connects = 0
        self.audio_seconds = 0.0

    # ------------------------------------------------------------- demand

    def _enabled(self) -> bool:
        s = self.engine.settings
        return s.on_air and self.lang in s.languages

    def _wanted(self) -> bool:
        s = self.engine.settings
        return self._enabled() and bool(self.clients or self.streams or self.lang in s.always_on)

    def update_demand(self) -> None:
        if self._wanted():
            self._cancel_stop()
            if self._task is None:
                self._task = asyncio.create_task(self._supervise(), name=f"channel-{self.lang}")
                self._update_pacer()
            return
        if self._task is None:
            self._cancel_stop()
            return
        delay = self.engine.settings.idle_stop_s
        if not self._enabled() or delay <= 0:
            # Off air: close right away, even if an idle timer is already pending.
            self._halt_soon()
        elif self._stop_handle is None:
            self._stop_handle = asyncio.get_running_loop().call_later(delay, self._idle_expired)

    def _idle_expired(self) -> None:
        self._stop_handle = None
        if self._task is not None and not self._wanted():
            log.info("[%s] no listeners for %d s, closing session",
                     self.lang, self.engine.settings.idle_stop_s)
            self._halt_soon()

    def _cancel_stop(self) -> None:
        if self._stop_handle is not None:
            self._stop_handle.cancel()
            self._stop_handle = None

    def _halt(self) -> asyncio.Task | None:
        """Stop the supervisor synchronously; returns the task still winding down.

        Everything a new supervisor relies on is reset here, before any await,
        so update_demand() may start a successor immediately.
        """
        self._cancel_stop()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
        self.active = None
        self._active_src = None
        self._draining = None
        self._held.clear()
        if self._drain_task is not None:
            self._drain_task.cancel()   # closes the old session
            self._drain_task = None
        self._gap.clear()
        self._update_pacer()
        return task

    def _halt_soon(self) -> None:
        task = self._halt()
        if task is not None:
            spawn(asyncio.wait([task]), f"stop-{self.lang}")

    async def stop(self) -> None:
        task = self._halt()
        if task is not None:
            await asyncio.wait([task])   # does not swallow our own cancellation

    def restart(self) -> None:
        """Reconnect with the current settings (key, model, instructions)."""
        if self._task is not None:
            self._halt_soon()
        self.update_demand()

    # ------------------------------------------------------------- session

    def _set_status(self, status: str, error: str = "") -> None:
        if status == self.status and error == self.error:
            return
        changed = status != self.status
        self.status, self.error = status, error
        if changed:
            self._broadcast(json.dumps({"t": "ch", "lang": self.lang, "status": status}))

    async def _supervise(self) -> None:
        """Keep one translation session open while the channel is wanted.

        Must never die on its own: any unexpected error is logged and retried,
        otherwise the channel would be stuck "connecting" until a restart.
        """
        engine = self.engine
        me = asyncio.current_task()
        backoff = 1.0
        first = True
        current: GeminiConnection | None = None   # the session this supervisor owns
        current_src: _Session | None = None
        try:
            while True:
                try:
                    if current is not None and not current.alive:
                        if self.active is current:
                            self.active = None
                        await current.close()
                        current = current_src = None
                    if self.active is None:
                        self._set_status("connecting" if first else "reconnecting", self.error)
                    first = False

                    key = engine.store.api_key
                    if not key:
                        self._set_status("error", "Chybí API klíč Gemini. Zadejte ho v administraci.")
                        await asyncio.sleep(3600)   # restart() wakes us when a key is set
                        continue

                    s = engine.settings
                    setup = build_setup(s.model, self.lang, s.echo_target, s.system_instruction,
                                        engine.placement)
                    src = _Session(self)
                    conn = GeminiConnection(engine.http, engine.env.gemini_ws_url, key, setup, src,
                                            f"[{self.lang}]")
                    try:
                        await conn.open()
                    except SetupError as e:
                        await conn.close()
                        if e.reason and engine.placement.adapt(e.reason):
                            log.info("[%s] adjusted setup layout, retrying", self.lang)
                            continue
                        log.warning("[%s] %s", self.lang, e)
                        if self.active is None:
                            self._set_status("error" if e.fatal else "reconnecting", str(e))
                        await asyncio.sleep(30 if e.fatal else backoff)
                        backoff = min(backoff * 2, MAX_BACKOFF)
                        continue
                    except BaseException:
                        await conn.close()
                        raise

                    old, old_src = current, current_src
                    current, current_src = conn, src
                    self.active = conn
                    self._active_src = src
                    if old is not None and old_src is not None:
                        self._hand_over(old, old_src)   # old one finishes its sentence first
                    self._flush_gap(conn)
                    self.connects += 1
                    backoff = 1.0
                    self._set_status("live")
                    log.info("[%s] translation session live", self.lang)

                    if await conn.wait() == "goaway":
                        continue  # open the next session while this one keeps running
                    if self.active is conn:
                        self.active = None
                        self._active_src = None
                    current = current_src = None
                    await conn.close()
                    if conn.close_reason and engine.placement.adapt(conn.close_reason):
                        log.info("[%s] Google rejected an optional field, it is no longer sent",
                                 self.lang)
                    self._set_status("reconnecting", conn.close_reason or "Spojení s Google se přerušilo.")
                    await asyncio.sleep(0.1 if backoff <= 1 else backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("[%s] translation supervisor error, retrying", self.lang)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)
        except asyncio.CancelledError:
            pass
        finally:
            if self.active is current:
                self.active = None
            if current is not None:
                await current.close()
            if self._task is None or self._task is me:   # no successor has taken over
                self._gap.clear()
                self._set_status("idle")

    # ------------------------------------------------------------- hand-over

    def _route(self, src: _Session, fn: Callable, *args) -> None:
        src.last_event = time.monotonic()
        if src is self._draining:
            fn(*args)
        elif src is self._active_src:
            if self._draining is not None:
                self._held.append((fn, args))
            else:
                fn(*args)
        # else: a session that is being closed; its output is no longer wanted

    def _hand_over(self, old: GeminiConnection, src: _Session) -> None:
        if self._draining is not None:
            # Two hand-overs within DRAIN_S: stop waiting for the oldest session.
            self._release_held()
        if self._drain_task is not None:
            self._drain_task.cancel()
        src.finished = False
        self._draining = src
        self._drain_task = spawn(self._drain(old, src), f"gemini-drain-{self.lang}")

    async def _drain(self, conn: GeminiConnection, src: _Session) -> None:
        start = time.monotonic()
        try:
            while conn.alive:
                now = time.monotonic()
                if (src.finished or now - start >= DRAIN_S
                        or now - max(src.last_event, start) >= DRAIN_QUIET):
                    break
                await asyncio.sleep(0.05)
        finally:
            if self._draining is src:
                self._release_held()
            if self._drain_task is asyncio.current_task():
                self._drain_task = None
            await conn.close()

    def _release_held(self) -> None:
        self._draining = None
        held = list(self._held)
        self._held.clear()
        self._brk = True   # the new session's words start a new block
        for fn, args in held:
            fn(*args)

    # ------------------------------------------------------------- input

    @property
    def wants_audio(self) -> bool:
        """A session is live or being (re)opened, so input should not be dropped."""
        return self._task is not None

    def send_audio(self, message: str) -> None:
        conn = self.active
        if conn is not None and conn.alive:
            conn.send_audio(message)
        elif self._task is not None:
            self._gap.append((time.monotonic(), message))

    def end_audio(self) -> None:
        """The noise gate closed: let Google flush what it has buffered right away."""
        conn = self.active
        if conn is not None and conn.alive and self.engine.placement.stream_end:
            conn.send_stream_end()

    def _flush_gap(self, conn: GeminiConnection) -> None:
        cutoff = time.monotonic() - GAP_MAX_AGE
        fresh = [m for t, m in self._gap if t >= cutoff]
        self._gap.clear()
        if fresh:
            log.info("[%s] sending %.1f s of audio buffered during reconnect", self.lang, len(fresh) / 10)
            for m in fresh:
                conn.send_audio(m)

    # ------------------------------------------------------------- Gemini events

    def on_input_text(self, text: str, lang: str | None) -> None:
        if lang:
            self.detected = str(lang)[:16]
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
        self.jitter.push(pcm, time.monotonic())

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
        self._hist_cache = None
        self._broadcast(json.dumps({"t": "tx", "s": seg["s"], "k": kind, "x": text},
                                   ensure_ascii=False))

    def history_message(self) -> str:
        # Cached: when a QR code goes up, hundreds of phones join within seconds.
        if self._hist_cache is None:
            segs = list(self.segments)[-HISTORY_SENT:]
            self._hist_cache = json.dumps({"t": "hist", "lang": self.lang, "segs": segs},
                                          ensure_ascii=False)
        return self._hist_cache

    def clear_history(self) -> None:
        self.segments.clear()
        self._seg = None
        self._hist_cache = None
        self._broadcast(self.history_message())

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
            self.jitter.clear()

    def _pacer_died(self, task: asyncio.Task) -> None:
        """The pacer ended on its own (should not happen): start a fresh one."""
        if self._pacer is task and not task.cancelled():
            self._pacer = None
            self.jitter.clear()
            asyncio.get_running_loop().call_later(1.0, self._update_pacer)

    async def _pace(self) -> None:
        """Real-time clock for the outgoing audio.

        The jitter buffer turns Gemini's uneven bursts into a steady 40 ms frame
        stream: Opus frames for app listeners (only while there is speech) and a
        continuous MP3 stream for background listeners.
        """
        asyncio.current_task().add_done_callback(self._pacer_died)
        loop = asyncio.get_running_loop()
        opus: OpusEncoder | None = None
        mp3: Mp3Encoder | None = None
        if OpusEncoder.available():
            try:
                opus = OpusEncoder(OUT_RATE, FRAME_SAMPLES)
            except Exception:
                log.exception("[%s] Opus encoder unavailable, app audio disabled", self.lang)
        silence = bytes(FRAME_BYTES)
        jitter = self.jitter
        hang = 0
        errors = 0
        t = loop.time()
        try:
            while True:
                t += FRAME_S
                delay = t - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                elif delay < -1.0:
                    # Event loop stalled: resync instead of bursting a second of frames.
                    self.stalls += 1
                    t = loop.time()

                frame = jitter.next_frame(time.monotonic())
                if frame is not None:
                    hang = HANGOVER_FRAMES
                elif hang:
                    hang -= 1
                pcm = silence if frame is None else frame

                try:
                    if (frame is not None or hang) and opus is not None and self.audio_clients:
                        self.seq = (self.seq + 1) & 0xFFFFFFFF
                        packet = b"\x01" + self.seq.to_bytes(4, "big") + opus.encode(pcm)
                        for c in self.audio_clients:
                            c.send_audio(packet)

                    if self.streams:
                        if mp3 is None and Mp3Encoder.available():
                            mp3 = Mp3Encoder(OUT_RATE)
                        if mp3 is not None:
                            data = mp3.encode(pcm)
                            if data:
                                for sink in self.streams:
                                    sink.push(data)
                    elif mp3 is not None:
                        mp3.close()
                        mp3 = None
                except Exception:
                    # One bad frame must not silence the channel for everyone.
                    errors += 1
                    if errors == 1 or errors % 1000 == 0:
                        log.exception("[%s] audio frame failed (%d so far)", self.lang, errors)
        except asyncio.CancelledError:
            raise
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
            "dropped": self.active.dropped if self.active is not None else 0,
            "stalls": self.stalls,
            **self.jitter.stats(),
        }

    async def shutdown(self) -> None:
        """Language removed from the event: release listeners and streams."""
        await self.stop()
        removed = json.dumps({"t": "ch", "lang": self.lang, "status": "removed"})
        for c in list(self.clients):
            c.send_text(removed)
            c.channel = None
        for sink in list(self.streams):
            sink.close()
        self.clients.clear()
        self.audio_clients.clear()
        self.streams.clear()
