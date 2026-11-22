"""One WebSocket session with the Gemini Live API (live translate model)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Protocol
from urllib.parse import quote

import aiohttp

from .util import spawn

log = logging.getLogger(__name__)

SETUP_TIMEOUT = 15.0


class Events(Protocol):
    def on_input_text(self, text: str, lang: str | None) -> None: ...
    def on_output_text(self, text: str) -> None: ...
    def on_audio(self, pcm: bytes) -> None: ...
    def on_turn_complete(self) -> None: ...
    def on_usage(self, tokens: int) -> None: ...


class SetupError(Exception):
    """Setup failed. `fatal` means retrying soon will not help (bad key, bad model)."""

    def __init__(self, message: str, fatal: bool = False, adapted: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal
        self.adapted = adapted


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
        self._send_q: asyncio.Queue[str] = asyncio.Queue(maxsize=50)
        self._tasks: list[asyncio.Task] = []
        self.ended: asyncio.Future[str] | None = None
        self.dropped = 0
        self._rate_warned = False
        self._closing = False
        self.close_reason = ""

    async def open(self) -> None:
        loop = asyncio.get_running_loop()
        self.ended = loop.create_future()
        sep = "&" if "?" in self.url else "?"
        try:
            self.ws = await self.http.ws_connect(
                f"{self.url}{sep}key={quote(self.api_key)}",
                heartbeat=25, max_msg_size=0, compress=0,
            )
        except aiohttp.WSServerHandshakeError as e:
            fatal = e.status in (400, 401, 403, 404)
            raise SetupError(f"Google odmítl spojení (HTTP {e.status})", fatal=fatal) from e
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            raise SetupError(f"Nelze se spojit s Google: {e}") from e

        await self.ws.send_str(json.dumps(self.setup))
        try:
            msg = await self.ws.receive(timeout=SETUP_TIMEOUT)
        except asyncio.TimeoutError:
            await self.ws.close()
            raise SetupError("Google neodpověděl na nastavení relace.")
        if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
            reason = (msg.extra or "") if msg.type == aiohttp.WSMsgType.CLOSE else ""
            code = msg.data if msg.type == aiohttp.WSMsgType.CLOSE else self.ws.close_code
            raise self._setup_failure(code, str(reason))
        self._handle_raw(msg)
        self._tasks = [
            asyncio.create_task(self._reader(), name=f"gemini-read-{self.label}"),
            asyncio.create_task(self._writer(), name=f"gemini-write-{self.label}"),
        ]

    def _setup_failure(self, code, reason: str) -> SetupError:
        text, fatal = _classify_close(code, reason)
        err = SetupError(text, fatal=fatal)
        err.reason = reason  # type: ignore[attr-defined]
        return err

    def send_audio(self, message: str) -> None:
        if self._send_q.full():
            try:
                self._send_q.get_nowait()
                self.dropped += 1
            except asyncio.QueueEmpty:
                pass
        self._send_q.put_nowait(message)

    async def _writer(self) -> None:
        assert self.ws is not None
        try:
            while True:
                msg = await self._send_q.get()
                await self.ws.send_str(msg)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        except Exception as e:
            log.debug("%s writer stopped: %s", self.label, e)

    async def _reader(self) -> None:
        assert self.ws is not None
        outcome, reason = "closed", ""
        try:
            while True:
                msg = await self.ws.receive()
                if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                    if self._handle_raw(msg) == "goaway":
                        self._end("goaway")
                    continue
                if msg.type == aiohttp.WSMsgType.CLOSE:
                    reason = msg.extra or ""
                break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            reason = str(e)
        finally:
            code = self.ws.close_code if self.ws else None
            if not self._closing:
                self.close_reason = reason
                log.info("%s: session closed by Google (code %s) %s", self.label, code, reason)
            self._end(outcome)

    def _end(self, outcome: str) -> None:
        if self.ended and not self.ended.done():
            self.ended.set_result(outcome)

    def _handle_raw(self, msg: aiohttp.WSMessage) -> str | None:
        data = msg.data if msg.type == aiohttp.WSMsgType.TEXT else msg.data.decode("utf-8", "replace")
        try:
            j = json.loads(data)
        except ValueError:
            return None
        sc = j.get("serverContent")
        ev = self.events
        if sc:
            it = sc.get("inputTranscription")
            if it and it.get("text"):
                ev.on_input_text(it["text"], it.get("languageCode"))
            ot = sc.get("outputTranscription")
            if ot and ot.get("text"):
                ev.on_output_text(ot["text"])
            turn = sc.get("modelTurn")
            if turn:
                for part in turn.get("parts") or ():
                    inline = part.get("inlineData")
                    if inline and inline.get("data"):
                        mime = inline.get("mimeType", "")
                        if "rate=" in mime and "rate=24000" not in mime and not self._rate_warned:
                            self._rate_warned = True
                            log.warning("%s: unexpected audio format %s", self.label, mime)
                        ev.on_audio(base64.b64decode(inline["data"]))
            if sc.get("turnComplete"):
                ev.on_turn_complete()
        usage = j.get("usageMetadata")
        if usage and usage.get("totalTokenCount"):
            ev.on_usage(int(usage["totalTokenCount"]))
        if "goAway" in j:
            log.info("%s: goAway received (%s)", self.label, j["goAway"].get("timeLeft", "?"))
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
