"""Shared test helpers. Run from the server directory: python -m pytest"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
ROOT = SERVER.parent
sys.path.insert(0, str(SERVER))
sys.path.insert(0, str(ROOT / "tools"))

from app.config import DEFAULT_TRUSTED_PROXIES, Env, parse_networks  # noqa: E402

ADMIN_PASSWORD = "test-admin-password"


def make_env(data_dir: Path, **overrides) -> Env:
    values = dict(
        host="127.0.0.1",
        port=0,
        data_dir=data_dir,
        web_dir=ROOT / "web",
        admin_password=ADMIN_PASSWORD,
        api_key="",
        gemini_ws_url="ws://127.0.0.1:9/ws",
        log_level="INFO",
        trusted_proxies=parse_networks(DEFAULT_TRUSTED_PROXIES),
        max_clients=1000,
    )
    values.update(overrides)
    return Env(**values)


async def wait_for(predicate, timeout: float = 5.0, interval: float = 0.02) -> None:
    """Poll until predicate() is true; fail the test after `timeout` seconds."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(interval)


async def recv_until(ws, predicate, timeout: float = 5.0) -> dict:
    """Read JSON text messages from a client WebSocket until one matches."""
    async def _loop():
        while True:
            msg = await ws.receive()
            if msg.type.name != "TEXT":
                if msg.type.name in ("CLOSE", "CLOSED", "CLOSING", "ERROR"):
                    raise AssertionError(f"socket closed: {msg}")
                continue
            j = json.loads(msg.data)
            if predicate(j):
                return j
    return await asyncio.wait_for(_loop(), timeout)
