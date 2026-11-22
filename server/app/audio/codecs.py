"""Tiny ctypes bindings for libopus and libmp3lame.

Both libraries are a few hundred kB as Debian packages, so the image stays
small and there is no ffmpeg/PyAV dependency. Each channel encodes once and
the same bytes are sent to every listener.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

log = logging.getLogger(__name__)


def _load(name: str, sonames: tuple[str, ...]) -> ctypes.CDLL | None:
    candidates: list[str] = []
    found = ctypes.util.find_library(name)
    if found:
        candidates.append(found)
    candidates.extend(sonames)
    for c in candidates:
        try:
            return ctypes.CDLL(c)
        except OSError:
            continue
    return None


# ----------------------------------------------------------------------- Opus

_opus = _load("opus", ("libopus.so.0", "libopus.so", "libopus-0.dll", "opus.dll", "libopus.dylib"))

OPUS_APPLICATION_VOIP = 2048
OPUS_SET_BITRATE_REQUEST = 4002
OPUS_SET_COMPLEXITY_REQUEST = 4010
OPUS_SET_SIGNAL_REQUEST = 4024
OPUS_SIGNAL_VOICE = 3001

if _opus is not None:
    _opus.opus_encoder_create.restype = ctypes.c_void_p
    _opus.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int,
                                          ctypes.POINTER(ctypes.c_int)]
    _opus.opus_encode.restype = ctypes.c_int32
    _opus.opus_encode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int,
                                  ctypes.c_char_p, ctypes.c_int32]
    _opus.opus_encoder_destroy.restype = None
    _opus.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
    _opus.opus_encoder_ctl.restype = ctypes.c_int  # variadic: no argtypes


class OpusEncoder:
    """Mono Opus encoder for raw packets (one packet per frame)."""

    MAX_PACKET = 4000

    @staticmethod
    def available() -> bool:
        return _opus is not None

    def __init__(self, rate: int = 24000, frame_samples: int = 960, bitrate: int = 24000) -> None:
        if _opus is None:
            raise RuntimeError("libopus not available")
        err = ctypes.c_int(0)
        st = _opus.opus_encoder_create(rate, 1, OPUS_APPLICATION_VOIP, ctypes.byref(err))
        if err.value != 0 or not st:
            raise RuntimeError(f"opus_encoder_create failed ({err.value})")
        self._st = ctypes.c_void_p(st)
        self.frame_samples = frame_samples
        for req, val in ((OPUS_SET_BITRATE_REQUEST, bitrate),
                         (OPUS_SET_COMPLEXITY_REQUEST, 5),
                         (OPUS_SET_SIGNAL_REQUEST, OPUS_SIGNAL_VOICE)):
            _opus.opus_encoder_ctl(self._st, ctypes.c_int(req), ctypes.c_int32(val))
        self._out = ctypes.create_string_buffer(self.MAX_PACKET)

    def encode(self, pcm16: bytes) -> bytes:
        n = _opus.opus_encode(self._st, pcm16, self.frame_samples, self._out, self.MAX_PACKET)
        if n < 0:
            raise RuntimeError(f"opus_encode failed ({n})")
        return ctypes.string_at(self._out, n)

    def close(self) -> None:
        if self._st:
            _opus.opus_encoder_destroy(self._st)
            self._st = None

    def __del__(self) -> None:  # pragma: no cover
        try:
            self.close()
        except Exception:
            pass


# ----------------------------------------------------------------------- MP3

_lame = _load("mp3lame", ("libmp3lame.so.0", "libmp3lame.so", "libmp3lame.dll", "libmp3lame.dylib"))

if _lame is not None:
    _lame.lame_init.restype = ctypes.c_void_p
    _lame.lame_init.argtypes = []
    for _fn in ("lame_set_in_samplerate", "lame_set_out_samplerate", "lame_set_num_channels",
                "lame_set_mode", "lame_set_brate", "lame_set_quality",
                "lame_set_disable_reservoir"):
        getattr(_lame, _fn).restype = ctypes.c_int
        getattr(_lame, _fn).argtypes = [ctypes.c_void_p, ctypes.c_int]
    _lame.lame_init_params.restype = ctypes.c_int
    _lame.lame_init_params.argtypes = [ctypes.c_void_p]
    _lame.lame_encode_buffer.restype = ctypes.c_int
    _lame.lame_encode_buffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p,
                                         ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    _lame.lame_close.restype = ctypes.c_int
    _lame.lame_close.argtypes = [ctypes.c_void_p]

LAME_MONO = 3


class Mp3Encoder:
    """Mono CBR MP3 encoder without bit reservoir, so listeners can join at any frame."""

    @staticmethod
    def available() -> bool:
        return _lame is not None

    def __init__(self, rate: int = 24000, kbps: int = 40) -> None:
        if _lame is None:
            raise RuntimeError("libmp3lame not available")
        g = _lame.lame_init()
        if not g:
            raise RuntimeError("lame_init failed")
        self._g = ctypes.c_void_p(g)
        _lame.lame_set_in_samplerate(self._g, rate)
        _lame.lame_set_out_samplerate(self._g, rate)
        _lame.lame_set_num_channels(self._g, 1)
        _lame.lame_set_mode(self._g, LAME_MONO)
        _lame.lame_set_brate(self._g, kbps)
        _lame.lame_set_quality(self._g, 7)
        _lame.lame_set_disable_reservoir(self._g, 1)
        if _lame.lame_init_params(self._g) < 0:
            _lame.lame_close(self._g)
            raise RuntimeError("lame_init_params failed")
        self._cap = 16384
        self._out = ctypes.create_string_buffer(self._cap)

    def encode(self, pcm16: bytes) -> bytes:
        n_samples = len(pcm16) // 2
        need = int(1.25 * n_samples) + 7200
        if need > self._cap:
            self._cap = need
            self._out = ctypes.create_string_buffer(need)
        n = _lame.lame_encode_buffer(self._g, pcm16, pcm16, n_samples, self._out, self._cap)
        if n < 0:
            raise RuntimeError(f"lame_encode_buffer failed ({n})")
        return ctypes.string_at(self._out, n) if n else b""

    def close(self) -> None:
        if self._g:
            _lame.lame_close(self._g)
            self._g = None

    def __del__(self) -> None:  # pragma: no cover
        try:
            self.close()
        except Exception:
            pass


def describe() -> dict:
    return {"opus": OpusEncoder.available(), "mp3": Mp3Encoder.available()}
