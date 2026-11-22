"""One WebSocket session with the Gemini Live API (live translate model)."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
import time
from typing import Protocol
from urllib.parse import quote

import aiohttp

from .util import spawn

log = logging.getLogger(__name__)

SETUP_TIMEOUT = 15.0
MAX_MESSAGE = 32 * 1024 * 1024   # Gemini audio chunks are ~100 kB; refuse anything absurd
SEND_QUEUE = 50                  # 5 s of 100 ms input blocks


class Events(Protocol):
    def on_input_text(self, text: str, lang: str | None) -> None: ...
    def on_output_text(self, text: str) -> None: ...
    def on_audio(self, pcm: bytes) -> None: ...
    def on_turn_complete(self) -> None: ...
    def on_usage(self, tokens: int) -> None: ...


class SetupError(Exception):
    """Setup failed. `fatal` means retrying soon will not help (bad key, bad model)."""

    def __init__(self, message: str, fatal: bool = False, reason: str = "") -> None:
        super().__init__(message)
        self.fatal = fatal
        self.reason = reason


class Placement:
    """Where optional setup fields go. Shared by all sessions.

    The documented layout is used first; if Google rejects a field's position
    (`Unknown name "x" at 'setup...'`), the field is moved and the session retried.
    """

    def __init__(self) -> None:
        self.where = {"inputAudioTranscription": "top", "outputAudioTranscription": "top",
                      "translationConfig": "gen"}
        self.failed: dict[str, set[str]] = {k: set() for k in self.where}

    def adapt(self, reason: str) -> bool:
        changed = False
        for m in re.finditer(r"Unknown name \"(\w+)\" at 'setup(\.generation_?config)?'", reason, re.I):
            field, where = m.group(1), ("gen" if m.group(2) else "top")
            if field not in self.where or self.where[field] != where:
                continue
            self.failed[field].add(where)
            nxt = next((p for p in ("top", "gen") if p not in self.failed[field]), None)
            if nxt:
                self.where[field] = nxt
                changed = True
            elif field != "translationConfig":
                self.where[field] = "omit"
                changed = True
        return changed


def build_setup(model: str, lang: str, echo: bool, instruction: str, placement: Placement) -> dict:
    gen: dict = {"responseModalities": ["AUDIO"]}
    setup: dict = {"model": f"models/{model}", "generationConfig": gen}
    values = {
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
        "translationConfig": {"targetLanguageCode": lang, "echoTargetLanguage": echo},
    }
    for name, value in values.items():
        where = placement.where[name]
        if where == "top":
            setup[name] = value
        elif where == "gen":
            gen[name] = value
    if instruction.strip():
        setup["systemInstruction"] = {"parts": [{"text": instruction.strip()}]}
    return {"setup": setup}


def _classify_close(code: int | None, reason: str) -> tuple[str, bool]:
    r = reason.lower()
    if any(s in r for s in ("api key", "api_key", "permission", "unauth", "denied")):
        return f"Google nepřijal API klíč ({reason})", True
    if "not found" in r or "is not supported" in r:
        return f"Model není dostupný ({reason})", True
    if any(s in r for s in ("quota", "exhaust", "rate limit", "resource has been")):
        return f"Vyčerpaná kvóta API ({reason})", False
    if reason:
        return f"Google ukončil spojení: {reason}", False
    return f"Spojení s Google se uzavřelo (kód {code})", False


_KEY_PARAM = re.compile(r"([?&]key=)[^&\s'\"]+", re.I)


def redact(text: str, secret: str = "") -> str:
    """Strip the API key from text that ends up in logs or the admin page.

    aiohttp puts the request URL (with ?key=...) into some exception messages.
    """
    text = _KEY_PARAM.sub(r"\1…", text)
    if secret and len(secret) >= 6:
        text = text.replace(secret, "…").replace(quote(secret), "…")
    return text


class GeminiConnection:
    def __init__(self, http: aiohttp.ClientSession, url: str, api_key: str, setup: dict,
                 events: Events, label: str) -> None:
        self.http = http
        self.url = url
        self.api_key = api_key
        self.setup = setup
        self.events = events
        self.label = label
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self._send_q: asyncio.Queue[str] = asyncio.Queue(maxsize=SEND_QUEUE)
        self._tasks: list[asyncio.Task] = []
        self.ended: asyncio.Future[str] | None = None
        self.dropped = 0
        self._rate_warned = False
        self._bad_warned = False
        self._closing = False
        self.close_reason = ""
        self.opened_at = 0.0
        self.last_rx = 0.0

    @property
    def alive(self) -> bool:
        """Open and still reading: input sent now will be translated."""
        return (not self._closing and self.ws is not None and not self.ws.closed
                and bool(self._tasks) and not self._tasks[0].done())

    async def open(self) -> None:
        loop = asyncio.get_running_loop()
        self.ended = loop.create_future()
        sep = "&" if "?" in self.url else "?"
        try:
            self.ws = await self.http.ws_connect(
                f"{self.url}{sep}key={quote(self.api_key, safe='')}",
                heartbeat=25, max_msg_size=MAX_MESSAGE, compress=0,
            )
        except aiohttp.WSServerHandshakeError as e:
            fatal = e.status in (400, 401, 403, 404)
            raise SetupError(f"Google odmítl spojení (HTTP {e.status})", fatal=fatal) from None
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as e:
            raise SetupError(f"Nelze se spojit s Google: {redact(str(e), self.api_key)}") from None

        try:
            await self.ws.send_str(json.dumps(self.setup))
            msg = await self.ws.receive(timeout=SETUP_TIMEOUT)
        except asyncio.TimeoutError:
            raise SetupError("Google neodpověděl na nastavení relace.") from None
        except (aiohttp.ClientError, OSError, RuntimeError) as e:
            raise SetupError(f"Spojení s Google se přerušilo: {redact(str(e), self.api_key)}") from None
        if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
            reason = str(msg.extra or "") if msg.type == aiohttp.WSMsgType.CLOSE else ""
            code = msg.data if msg.type == aiohttp.WSMsgType.CLOSE else self.ws.close_code
            text, fatal = _classify_close(code, redact(reason, self.api_key))
            raise SetupError(text, fatal=fatal, reason=reason)
        self.opened_at = self.last_rx = time.monotonic()
        self._dispatch(msg)
        self._tasks = [
            asyncio.create_task(self._reader(), name=f"gemini-read-{self.label}"),
            asyncio.create_task(self._writer(), name=f"gemini-write-{self.label}"),
        ]

    def send_audio(self, message: str) -> None:
        q = self._send_q
        if q.full():
            try:
                q.get_nowait()
                self.dropped += 1
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(message)

    async def _writer(self) -> None:
        assert self.ws is not None
        ws, q = self.ws, self._send_q
        try:
            while True:
                msg = await q.get()
                await ws.send_str(msg)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        except Exception as e:
            log.debug("%s writer stopped: %s", self.label, redact(str(e), self.api_key))

    async def _reader(self) -> None:
        assert self.ws is not None
        outcome, reason = "closed", ""
        try:
            while True:
                msg = await self.ws.receive()
                if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                    self.last_rx = time.monotonic()
                    if self._dispatch(msg) == "goaway":
                        self._end("goaway")
                    continue
                if msg.type == aiohttp.WSMsgType.CLOSE:
                    reason = str(msg.extra or "")
                break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            reason = str(e)
        finally:
            reason = redact(reason, self.api_key)
            code = self.ws.close_code if self.ws else None
            if not self._closing:
                now = time.monotonic()
                if reason:
                    self.close_reason = reason
                    log.info("%s: Google closed the session after %.0f s (code %s): %s",
                             self.label, now - self.opened_at, code, reason)
                else:
                    # No close frame: the TCP connection was dropped (preview backend).
                    exc = self.ws.exception() if self.ws else None
                    self.close_reason = "Google přerušil spojení bez udání důvodu."
                    log.info("%s: connection dropped after %.0f s (code %s, last message %.1f s "
                             "before, %d audio blocks dropped)%s", self.label, now - self.opened_at,
                             code, now - self.last_rx, self.dropped,
                             f": {redact(str(exc), self.api_key)}" if exc else "")
            self._end(outcome)

    def _end(self, outcome: str) -> None:
        if self.ended and not self.ended.done():
            self.ended.set_result(outcome)

    def _dispatch(self, msg: aiohttp.WSMessage) -> str | None:
        """Handle one server message. A malformed message (or a bug in a handler)
        is logged once and skipped: it must not end a working session."""
        try:
            return self._handle_raw(msg)
        except Exception:
            if not self._bad_warned:
                self._bad_warned = True
                log.exception("%s: could not handle a message from Google", self.label)
            return None

    def _handle_raw(self, msg: aiohttp.WSMessage) -> str | None:
        data = msg.data if msg.type == aiohttp.WSMsgType.TEXT else msg.data.decode("utf-8", "replace")
        try:
            j = json.loads(data)
        except ValueError:
            return None
        if not isinstance(j, dict):
            return None
        sc = j.get("serverContent")
        ev = self.events
        if isinstance(sc, dict):
            it = sc.get("inputTranscription")
            if isinstance(it, dict) and it.get("text"):
                ev.on_input_text(str(it["text"]), it.get("languageCode"))
            ot = sc.get("outputTranscription")
            if isinstance(ot, dict) and ot.get("text"):
                ev.on_output_text(str(ot["text"]))
            turn = sc.get("modelTurn")
            if isinstance(turn, dict):
                for part in turn.get("parts") or ():
                    inline = part.get("inlineData") if isinstance(part, dict) else None
                    if isinstance(inline, dict) and inline.get("data"):
                        mime = str(inline.get("mimeType", ""))
                        if "rate=" in mime and "rate=24000" not in mime and not self._rate_warned:
                            self._rate_warned = True
                            log.warning("%s: unexpected audio format %s", self.label, mime)
                        try:
                            pcm = base64.b64decode(inline["data"])
                        except (binascii.Error, ValueError, TypeError):
                            continue
                        if len(pcm) & 1:
                            pcm = pcm[:-1]
                        if pcm:
                            ev.on_audio(pcm)
            if sc.get("turnComplete"):
                ev.on_turn_complete()
        usage = j.get("usageMetadata")
        if isinstance(usage, dict) and usage.get("totalTokenCount"):
            ev.on_usage(int(usage["totalTokenCount"]))
        if "goAway" in j:
            ga = j["goAway"]
            log.info("%s: goAway received (%s)", self.label,
                     ga.get("timeLeft", "?") if isinstance(ga, dict) else "?")
            return "goaway"
        return None

    async def wait(self) -> str:
        assert self.ended is not None
        return await asyncio.shield(self.ended)

    def close_later(self, delay: float) -> None:
        async def _later():
            await asyncio.sleep(delay)
            await self.close()
        spawn(_later(), f"gemini-drain-{self.label}")

    async def close(self) -> None:
        self._closing = True
        for t in self._tasks:
            t.cancel()
        if self.ws is not None and not self.ws.closed:
            try:
                await asyncio.wait_for(self.ws.close(), timeout=3)
            except Exception:
                pass
        self._end("closed")
