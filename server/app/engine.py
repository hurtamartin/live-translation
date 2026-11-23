"""Central state: audio input -> noise gate -> fan-out to language channels."""

from __future__ import annotations

import asyncio
import base64
import collections
import json
import logging
import math
import time
from pathlib import Path

import aiohttp
import numpy as np

from . import __version__
from .audio.codecs import describe as codecs
from .audio.sources import BrowserSource, DeviceSource, FileSource, Source
from .channel import Channel
from .clients import Client
from .config import Env, SettingsStore
from .gemini import Placement
from .util import every, spawn

log = logging.getLogger(__name__)

BLOCK = 1600  # 100 ms at 16 kHz, the chunk size Google recommends
PREROLL_BLOCKS = 3  # 300 ms sent ahead of the gate opening, so first syllables survive
LAG_PROBE_S = 0.25  # event loop responsiveness check
LAG_WARN_S = 0.15   # a stall this long is audible: every listener's audio clock stops


class Engine:
    def __init__(self, store: SettingsStore, env: Env) -> None:
        self.store = store
        self.env = env
        self.placement = Placement()
        self.channels: dict[str, Channel] = {}
        self.clients: set[Client] = set()
        self.http: aiohttp.ClientSession | None = None
        self.source: Source = Source(self.feed)
        self._buf = np.zeros(0, dtype=np.float32)
        self._preroll: collections.deque[np.ndarray] = collections.deque(maxlen=PREROLL_BLOCKS)
        self._hold_until = 0.0
        self.level_db = -90.0
        self.peak_db = -90.0
        self.gate_open = False
        self.sending = False
        self.sent_seconds = 0.0
        self.started = time.time()
        self._info: str | None = None
        self.loop_lag = 0.0       # recent worst event loop stall, seconds (decays)
        self._lag_task: asyncio.Task | None = None

    @property
    def settings(self):
        return self.store.data

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=15))
        c = codecs()
        log.info("Codecs: opus=%s mp3=%s", c["opus"], c["mp3"])
        if not c["opus"]:
            log.warning("libopus missing: phones fall back to the MP3 stream")
        self._sync_channels()
        await self._start_source()
        self._lag_task = asyncio.create_task(self._watch_loop(), name="loop-lag")

    async def _watch_loop(self) -> None:
        """Measure how late the event loop wakes up.

        All audio is paced on this loop, so anything that blocks it (slow disk,
        a heavy request) is heard as a dropout by every listener at once.
        """
        loop = asyncio.get_running_loop()
        t = loop.time()
        while True:
            await asyncio.sleep(LAG_PROBE_S)
            now = loop.time()
            lag = max(0.0, now - t - LAG_PROBE_S)
            t = now
            self.loop_lag = max(lag, self.loop_lag * 0.97)
            if lag >= LAG_WARN_S and every("loop-lag", 60):
                log.warning("Event loop was blocked for %.0f ms: listeners may hear a dropout",
                            lag * 1000)

    async def stop(self) -> None:
        """Graceful shutdown, bounded in time (docker stop allows ~10 s)."""
        if self._lag_task is not None:
            self._lag_task.cancel()
        channels = list(self.channels.values())
        await asyncio.gather(*(ch.stop() for ch in channels), return_exceptions=True)
        await self.source.stop()
        for ch in channels:
            for sink in list(ch.streams):
                sink.close()
        clients = list(self.clients)
        for c in clients:
            c.close()
        if clients:
            # Concurrently: a phone that vanished would otherwise hold each close
            # for the full close timeout, one after another.
            closing = [asyncio.ensure_future(c.ws.close(code=1001, message=b"server shutdown"))
                       for c in clients]
            _, pending = await asyncio.wait(closing, timeout=3)
            for f in pending:
                f.cancel()
        if self.http is not None:
            await self.http.close()

    def _sync_channels(self) -> None:
        wanted = self.settings.languages
        for lang in list(self.channels):
            if lang not in wanted:
                spawn(self.channels.pop(lang).shutdown(), f"remove-{lang}")
        for lang in wanted:
            if lang not in self.channels:
                self.channels[lang] = Channel(self, lang)
        for ch in self.channels.values():
            ch.update_demand()

    def resolve_file(self, name: str) -> Path | None:
        if not name:
            return None
        base = self.env.data_dir.resolve()
        p = (base / name).resolve()
        if base not in p.parents and p != base:
            return None
        return p

    async def _start_source(self) -> None:
        await self.source.stop()
        s = self.settings
        if s.source == "device":
            src: Source = DeviceSource(self.feed, s.device, s.channel)
        elif s.source == "browser":
            src = BrowserSource(self.feed)
        elif s.source == "file":
            path = self.resolve_file(s.file_path)
            if path is None:
                src = Source(self.feed)
                src.state, src.error = "error", "Zadejte název WAV souboru ve složce data."
            else:
                src = FileSource(self.feed, path)
        else:
            src = Source(self.feed)
        self._buf = np.zeros(0, dtype=np.float32)
        self._preroll.clear()
        self.gate_open = False
        self.source = src
        await src.start()

    async def apply(self, changed: set[str]) -> None:
        if changed & {"source", "device", "channel", "file_path"}:
            await self._start_source()
        if "languages" in changed:
            self._sync_channels()
        if changed & {"model", "system_instruction", "echo_target", "api_key"}:
            for ch in self.channels.values():
                ch.restart()   # reconnects in the background with the new setup
        if changed & {"on_air", "always_on", "idle_stop_s"}:
            for ch in self.channels.values():
                ch.update_demand()
        if changed & {"event_title", "on_air", "languages", "public_url"}:
            self.broadcast_info()
        if "on_air" in changed:
            log.info("Broadcast %s", "started" if self.settings.on_air else "stopped")

    # ------------------------------------------------------------- audio path

    def feed(self, x: np.ndarray) -> None:
        """Accept float32 mono 16 kHz samples of any length."""
        buf = np.concatenate((self._buf, x)) if self._buf.size else x
        n = buf.size // BLOCK
        for i in range(n):
            self._process(buf[i * BLOCK:(i + 1) * BLOCK])
        self._buf = buf[n * BLOCK:].copy()

    def _process(self, block: np.ndarray) -> None:
        s = self.settings
        if s.gain_db:
            block = block * (10.0 ** (s.gain_db / 20.0))
        energy = float(np.dot(block, block)) / BLOCK
        peak = float(np.max(np.abs(block)))
        db = 10.0 * math.log10(energy) if energy > 1e-12 else -120.0
        self.level_db = max(db, -90.0)
        self.peak_db = max(20.0 * math.log10(peak) if peak > 1e-6 else -120.0, -90.0)

        now = time.monotonic()
        opening = False
        if not s.gate_enabled:
            opening = not self.gate_open
            self.gate_open = True
        elif db >= s.gate_threshold_db:
            opening = not self.gate_open
            self.gate_open = True
            self._hold_until = now + s.gate_hold_ms / 1000.0
        elif self.gate_open and now >= self._hold_until:
            self.gate_open = False

        live = s.on_air and any(ch.wants_audio for ch in self.channels.values())
        if not (live and self.gate_open):
            self._preroll.append(block)
            if self.sending and live:
                # Speech paused: without this, Google may keep the end of the last
                # sentence until the next one starts.
                for ch in self.channels.values():
                    ch.end_audio()
            self.sending = False
            return
        if opening:
            for b in self._preroll:
                self._send(b)
            self._preroll.clear()
        self._send(block)
        self.sending = True

    def _send(self, block: np.ndarray) -> None:
        pcm = (np.clip(block, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        # Built once and shared by every language session.
        msg = ('{"realtimeInput":{"audio":{"data":"' + base64.b64encode(pcm).decode("ascii")
               + '","mimeType":"audio/pcm;rate=16000"}}}')
        for ch in self.channels.values():
            ch.send_audio(msg)
        self.sent_seconds += BLOCK / 16000

    # ------------------------------------------------------------- listeners

    def info_message(self) -> str:
        if self._info is not None:
            return self._info
        s = self.settings
        self._info = json.dumps({
            "t": "info",
            "title": s.event_title,
            "onAir": s.on_air,
            "langs": s.languages,
            # The operator's public address (empty when not set): phones share this
            # link, the same one the server puts in the QR code.
            "url": s.public_url,
            "codecs": codecs(),
            "v": __version__,
        }, ensure_ascii=False)
        return self._info

    def broadcast_info(self) -> None:
        self._info = None
        msg = self.info_message()
        for c in self.clients:
            c.send_text(msg)

    def listener_count(self) -> int:
        return len(self.clients)

    def stream_count(self) -> int:
        return sum(len(ch.streams) for ch in self.channels.values())

    def add_client(self, client: Client) -> None:
        self.clients.add(client)
        client.send_text(self.info_message())

    def remove_client(self, client: Client) -> None:
        self.clients.discard(client)
        if client.channel is not None:
            client.channel.remove_client(client)
        client.close()

    def subscribe(self, client: Client, lang: str, audio: bool) -> None:
        ch = self.channels.get(lang)
        if client.channel is not None and client.channel is not ch:
            client.channel.remove_client(client)
        if ch is None:
            client.send_text(json.dumps({"t": "ch", "lang": lang, "status": "removed"}))
            return
        if client.channel is ch:
            ch.set_audio(client, audio)
        else:
            ch.add_client(client, audio)

    # ------------------------------------------------------------- admin

    def status(self) -> dict:
        s = self.settings
        return {
            "onAir": s.on_air,
            "uptime": int(time.time() - self.started),
            "input": {
                **self.source.info(),
                "db": round(self.level_db, 1),
                "peak": round(self.peak_db, 1),
                "gate": self.gate_open,
                "sending": self.sending,
                "sentMinutes": round(self.sent_seconds / 60, 1),
            },
            "channels": [self.channels[l].info() for l in s.languages if l in self.channels],
            "clients": len(self.clients),
            "codecs": codecs(),
            "loopLagMs": round(self.loop_lag * 1000),
        }

    def clear_history(self) -> None:
        for ch in self.channels.values():
            ch.clear_history()
