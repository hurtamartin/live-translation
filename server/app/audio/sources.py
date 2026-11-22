"""Audio sources. Each one delivers float32 mono samples at 16 kHz to a sink.

- DeviceSource: sound card visible to the container (ALSA via PortAudio),
  e.g. the USB interface of the mixing desk. One channel of a multichannel
  interface can be picked.
- BrowserSource: a browser on the operator's computer streams the mixer feed
  over a WebSocket (useful with Docker Desktop on Windows/macOS where USB audio
  cannot be passed into containers).
- FileSource: loops a 16-bit WAV file from DATA_DIR. Handy for rehearsals.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np
import soxr

log = logging.getLogger(__name__)

TARGET_RATE = 16000
Sink = Callable[[np.ndarray], None]

# PortAudio is not thread-safe for (re)initialisation: Pa_Terminate() while another
# thread opens or queries a stream can crash the whole process. Every call that
# touches the library state goes through this lock (they all run in worker threads).
_pa_lock = threading.Lock()
_open_streams = 0  # guarded by _pa_lock; no re-scan while a stream is open


def _sd():
    import sounddevice  # imported lazily: needs libportaudio2

    return sounddevice


def _rescan(sd) -> bool:
    """Re-enumerate devices so a re-plugged USB interface shows up. Lock held."""
    if _open_streams:
        return False
    sd._terminate()
    sd._initialize()
    return True


def _default_input(sd) -> int:
    try:
        idx = sd.default.device[0]  # _InputOutputPair or (in, out)
    except (TypeError, IndexError):
        idx = sd.default.device
    try:
        return int(idx)
    except (TypeError, ValueError):
        return -1


def list_devices(refresh: bool = False) -> list[dict]:
    try:
        sd = _sd()
        with _pa_lock:
            if refresh:
                _rescan(sd)
            default_in = _default_input(sd)
            apis = sd.query_hostapis()
            devices = sd.query_devices()
        out = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                out.append({
                    "index": i,
                    "name": d["name"],
                    "channels": int(d["max_input_channels"]),
                    "rate": int(d["default_samplerate"]),
                    "api": apis[d["hostapi"]]["name"] if d["hostapi"] < len(apis) else "",
                    "default": i == default_in,
                })
        return out
    except Exception as e:
        log.warning("Cannot list audio devices: %s", e)
        return []


class Source:
    kind = "none"

    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self.state = "stopped"  # stopped | starting | running | waiting | error
        self.detail = ""
        self.error = ""

    async def start(self) -> None:
        self.state = "stopped"

    async def stop(self) -> None:
        self.state = "stopped"

    def info(self) -> dict:
        return {"kind": self.kind, "state": self.state, "detail": self.detail, "error": self.error}


class DeviceSource(Source):
    kind = "device"

    def __init__(self, sink: Sink, device: str, channel: int) -> None:
        super().__init__(sink)
        self.device = device
        self.channel = channel
        self._task: asyncio.Task | None = None
        self.overflows = 0
        self.drops = 0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._supervise(), name="device-source")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self.state = "stopped"

    def _resolve(self, sd) -> tuple[int, dict]:
        devices = sd.query_devices()
        if self.device:
            for i, d in enumerate(devices):
                if d["name"] == self.device and d["max_input_channels"] > 0:
                    return i, d
            for i, d in enumerate(devices):
                if self.device.lower() in d["name"].lower() and d["max_input_channels"] > 0:
                    return i, d
            raise RuntimeError(f"Zařízení „{self.device}“ nebylo nalezeno.")
        default_in = _default_input(sd)
        if default_in >= 0:
            return default_in, devices[default_in]
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                return i, d
        raise RuntimeError("Server nevidí žádnou zvukovou kartu se vstupem.")

    def _prepare(self, rescan: bool) -> tuple[int, dict]:
        sd = _sd()
        with _pa_lock:
            if rescan:
                _rescan(sd)
            return self._resolve(sd)

    async def _supervise(self) -> None:
        loop = asyncio.get_running_loop()
        attempt = 0
        last_error = ""
        while True:
            stream = None
            try:
                self.state = "starting"
                sd = _sd()
                idx, info = await asyncio.to_thread(self._prepare, attempt > 0)
                native = int(info["default_samplerate"])
                max_ch = int(info["max_input_channels"])
                ch = self.channel if self.channel < max_ch else 0
                if ch != self.channel:
                    log.warning("Channel %d not available on %s, using 1", self.channel + 1, info["name"])
                resampler = soxr.ResampleStream(native, TARGET_RATE, 1, dtype="float32", quality="HQ")
                q: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=64)

                def put(block: np.ndarray) -> None:
                    try:
                        q.put_nowait(block)
                    except asyncio.QueueFull:
                        self.drops += 1

                def callback(indata, frames, time_info, status):  # PortAudio thread
                    if status and status.input_overflow:
                        self.overflows += 1
                    loop.call_soon_threadsafe(put, indata[:, ch].copy())

                def open_stream():
                    global _open_streams
                    with _pa_lock:
                        s = sd.InputStream(device=idx, channels=ch + 1, samplerate=native,
                                           dtype="float32", blocksize=max(256, native // 20),
                                           latency="high", callback=callback)
                        _open_streams += 1
                        try:
                            s.start()
                        except BaseException:
                            _open_streams -= 1
                            s.close()
                            raise
                        return s

                stream = await asyncio.to_thread(open_stream)
                self.state = "running"
                self.error = ""
                self.detail = f"{info['name']}, kanál {ch + 1}, {native} Hz"
                log.info("Audio input started: %s", self.detail)
                attempt = 0
                last_error = ""
                while True:
                    try:
                        block = await asyncio.wait_for(q.get(), timeout=3.0)
                    except asyncio.TimeoutError:
                        raise RuntimeError("Zvuková karta přestala posílat data.")
                    out = resampler.resample_chunk(block)
                    if out.size:
                        self.sink(out)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.state = "error"
                self.error = str(e)
                attempt += 1
                # A missing interface retries every 3 s: log each new error once, not forever.
                if self.error != last_error:
                    last_error = self.error
                    log.error("Audio input error: %s (retrying every 3 s)", e)
            finally:
                if stream is not None:
                    try:
                        await asyncio.to_thread(_close_stream, stream)
                    except Exception:
                        pass
            await asyncio.sleep(3)


def _close_stream(stream) -> None:
    global _open_streams
    with _pa_lock:
        try:
            stream.close()
        finally:
            _open_streams -= 1


class BrowserSource(Source):
    """Fed by /ws/ingest: Int16 little-endian mono PCM at 16 kHz."""

    kind = "browser"

    def __init__(self, sink: Sink) -> None:
        super().__init__(sink)
        self.ws = None
        self.state = "waiting"
        self.last_data = 0.0

    async def start(self) -> None:
        self.state = "waiting"
        self.detail = "Čeká na prohlížeč operátora"

    async def stop(self) -> None:
        if self.ws is not None:
            await self.ws.close(message=b"source changed")
        self.ws = None
        self.state = "stopped"

    async def attach(self, ws, label: str) -> None:
        if self.ws is not None:
            log.info("New browser source replaces the previous one")
            await self.ws.close(message=b"replaced")
        self.ws = ws
        self.state = "running"
        self.error = ""
        self.detail = label or "Prohlížeč operátora"

    def detach(self, ws) -> None:
        if self.ws is ws:
            self.ws = None
            self.state = "waiting"
            self.detail = "Čeká na prohlížeč operátora"

    def feed(self, data: bytes) -> None:
        if len(data) < 2:
            return
        if len(data) & 1:
            data = data[:-1]
        self.last_data = time.monotonic()
        self.sink(np.frombuffer(data, dtype="<i2").astype(np.float32) * (1.0 / 32768.0))


class FileSource(Source):
    kind = "file"

    def __init__(self, sink: Sink, path: Path) -> None:
        super().__init__(sink)
        self.path = path
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="file-source")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self.state = "stopped"

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                with wave.open(str(self.path), "rb") as wf:
                    if wf.getsampwidth() != 2:
                        raise RuntimeError("Podporován je jen 16bitový PCM WAV.")
                    rate, nch = wf.getframerate(), wf.getnchannels()
                    # An empty file would otherwise spin in the rewind loop and
                    # freeze the whole server.
                    if wf.getnframes() <= 0:
                        raise RuntimeError("WAV soubor je prázdný.")
                    if not 4000 <= rate <= 384000 or not 1 <= nch <= 64:
                        raise RuntimeError(f"Nepodporovaný WAV: {rate} Hz, {nch} kanálů.")
                    resampler = soxr.ResampleStream(rate, TARGET_RATE, 1, dtype="float32")
                    self.state, self.error = "running", ""
                    self.detail = f"{self.path.name}, {rate} Hz, smyčka"
                    step = rate // 10
                    t = loop.time()
                    while True:
                        raw = wf.readframes(step)
                        # Truncated last frame: keep whole frames only.
                        raw = raw[: len(raw) - len(raw) % (2 * nch)]
                        if not raw:
                            wf.rewind()
                            await asyncio.sleep(0.01)
                            continue
                        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                        if nch > 1:
                            x = x.reshape(-1, nch).mean(axis=1)
                        out = resampler.resample_chunk(x)
                        if out.size:
                            self.sink(out)
                        t += len(x) / rate
                        await asyncio.sleep(max(0.0, t - loop.time()))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.state, self.error = "error", str(e)
                log.error("File source error: %s", e)
                await asyncio.sleep(5)
