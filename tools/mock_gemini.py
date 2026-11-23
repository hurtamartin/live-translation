"""Fake Gemini Live endpoint for local testing without an API key.

Run:   python tools/mock_gemini.py 9100
Then:  GEMINI_WS_URL=ws://127.0.0.1:9100/ws GEMINI_API_KEY=test python -m app

For every second of received audio it answers with a transcript fragment,
a "translation" fragment and one second of a 24 kHz test tone.
API key "bad" is rejected like Google does. MOCK_GOAWAY_S=N sends goAway after N s.
MOCK_DROP_S=N drops the TCP connection after N s without a close frame, as the
preview backend sometimes does.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys

import numpy as np
from aiohttp import WSMsgType, web

SRC = "dobrý den vítám vás na dnešním setkání a děkuji že jste přišli".split()
TONE = (0.15 * np.sin(2 * np.pi * 330 * np.arange(24000) / 24000) * 32767).astype("<i2").tobytes()
STREAM_ENDS: list[str] = []   # languages that got audioStreamEnd (inspected by tests)


async def handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(max_msg_size=0)
    await ws.prepare(request)
    if request.query.get("key") == "bad":
        await ws.close(code=1008, message=b"API key not valid. Please pass a valid API key.")
        return ws
    first = await ws.receive()
    setup = json.loads(first.data)["setup"]
    lang = setup.get("generationConfig", {}).get("translationConfig", {}).get("targetLanguageCode", "?")
    print(f"mock: session for {lang}", flush=True)
    await ws.send_str(json.dumps({"setupComplete": {}}))
    loop = asyncio.get_running_loop()
    start = loop.time()
    goaway = float(os.environ.get("MOCK_GOAWAY_S", "0"))
    sent_goaway = False
    drop = float(os.environ.get("MOCK_DROP_S", "0"))
    received = 0
    total = 0
    i = 0
    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue
        rt = json.loads(msg.data).get("realtimeInput", {})
        if rt.get("audioStreamEnd"):
            STREAM_ENDS.append(lang)
            continue
        audio = rt.get("audio")
        if not audio:
            continue
        n = len(base64.b64decode(audio["data"])) // 2
        received += n
        total += n
        if drop and loop.time() - start > drop:
            print(f"mock: dropping {lang} after {total / 16000:.1f} s of audio", flush=True)
            request.transport.abort()
            return ws
        if received >= 16000:
            received -= 16000
            word = SRC[i % len(SRC)]
            i += 1
            await ws.send_str(json.dumps({"serverContent": {"inputTranscription": {
                "text": " " + word, "languageCode": "cs"}}}))
            await ws.send_str(json.dumps({"serverContent": {"outputTranscription": {
                "text": f" {lang}:{word}" + ("." if i % 4 == 0 else "")}}}))
            for k in range(4):
                chunk = TONE[k * 12000:(k + 1) * 12000]
                await ws.send_str(json.dumps({"serverContent": {"modelTurn": {"parts": [{"inlineData": {
                    "mimeType": "audio/pcm;rate=24000", "data": base64.b64encode(chunk).decode()}}]}}}))
            if i % 4 == 0:
                await ws.send_str(json.dumps({"serverContent": {"turnComplete": True},
                                              "usageMetadata": {"totalTokenCount": 120}}))
        if goaway and not sent_goaway and loop.time() - start > goaway:
            sent_goaway = True
            await ws.send_str(json.dumps({"goAway": {"timeLeft": "3s"}}))
            loop.call_later(3, lambda: asyncio.ensure_future(ws.close()))
    print(f"mock: session for {lang} closed after {total / 16000:.1f} s of audio", flush=True)
    return ws


def main() -> None:
    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    web.run_app(app, host="0.0.0.0", port=int(sys.argv[1]) if len(sys.argv) > 1 else 9100, print=None)


if __name__ == "__main__":
    main()
