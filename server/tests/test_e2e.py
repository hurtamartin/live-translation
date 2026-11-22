"""End to end: real server + tools/mock_gemini.py, driven like the phone and admin pages."""

from __future__ import annotations

import asyncio
import contextlib

import aiohttp
import mock_gemini
import numpy as np
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from conftest import ADMIN_PASSWORD, make_env, recv_until, wait_for

from app import web as appweb
from app.config import SettingsStore
from app.engine import Engine
from app.web import create_app

NOISE = (np.random.default_rng(1).normal(0, 3000, 1600)).astype("<i2").tobytes()  # 100 ms


@pytest.fixture(autouse=True)
def fast_login(monkeypatch):
    monkeypatch.setattr(appweb, "LOGIN_FAIL_DELAY", 0)
    for var in ("MOCK_DROP_S", "MOCK_GOAWAY_S"):
        monkeypatch.delenv(var, raising=False)


@contextlib.asynccontextmanager
async def running(tmp_path, settings: dict | None = None, **env):
    """Mock Gemini + translation server; yields (client, engine)."""
    mock_app = web.Application()
    mock_app.router.add_get("/{tail:.*}", mock_gemini.handler)
    async with TestServer(mock_app) as mock:
        ws_url = str(mock.make_url("/ws")).replace("http://", "ws://")
        e = make_env(tmp_path, api_key="test", gemini_ws_url=ws_url, **env)
        store = SettingsStore(e)
        store.patch({"source": "browser", "languages": ["en", "de"], "on_air": True,
                     "gate_enabled": False, "idle_stop_s": 0, **(settings or {})})
        engine = Engine(store, e)
        async with TestClient(TestServer(create_app(engine))) as client:
            yield client, engine


async def login(client) -> None:
    r = await client.post("/api/admin/login", json={"password": ADMIN_PASSWORD})
    assert r.status == 200


async def listener(client, lang: str = "en"):
    ws = await client.ws_connect("/ws")
    await recv_until(ws, lambda j: j["t"] == "info")
    await ws.send_json({"t": "sub", "lang": lang, "audio": False})
    await recv_until(ws, lambda j: j["t"] == "hist" and j["lang"] == lang)
    return ws


async def speak(ingest, seconds: float, realtime: bool = False) -> None:
    for _ in range(int(seconds * 10)):
        await ingest.send_bytes(NOISE)
        await asyncio.sleep(0.1 if realtime else 0.005)


def test_translation_reaches_listener(tmp_path):
    async def main():
        async with running(tmp_path) as (client, engine):
            ws = await listener(client, "en")
            await wait_for(lambda: engine.channels["en"].status == "live")
            assert engine.channels["de"].status == "idle", "no listener, no paid session"

            await login(client)
            ingest = await client.ws_connect("/ws/ingest?label=test")
            await speak(ingest, 2.2)
            tx = await recv_until(ws, lambda j: j["t"] == "tx" and j["k"] == "tr")
            assert tx["x"].strip().startswith("en:")

            # A late joiner gets the transcript as history.
            late = await client.ws_connect("/ws")
            await late.send_json({"t": "sub", "lang": "en"})
            hist = await recv_until(late, lambda j: j["t"] == "hist")
            assert hist["segs"] and "en:" in hist["segs"][-1]["tr"]

            # Last listener leaves with idle_stop_s=0: the session closes.
            await ws.close()
            await late.close()
            await wait_for(lambda: engine.channels["en"].status == "idle")
            assert engine.channels["en"]._task is None
    asyncio.run(main())


def test_restart_storm_settles_live(tmp_path):
    async def main():
        async with running(tmp_path) as (client, engine):
            ws = await listener(client, "en")
            ch = engine.channels["en"]
            await wait_for(lambda: ch.status == "live")
            await login(client)
            for i in range(8):
                r = await client.put("/api/admin/config", json={"system_instruction": f"v{i}"})
                assert r.status == 200
            await wait_for(lambda: ch.status == "live" and ch.active is not None and ch.active.alive)
            await asyncio.sleep(0.5)   # superseded supervisors must not overwrite the state
            assert ch.status == "live" and ch.active.alive
            await ws.close()
    asyncio.run(main())


def test_dropped_session_reconnects(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_DROP_S", "0.5")

    async def main():
        async with running(tmp_path) as (client, engine):
            ws = await listener(client, "en")
            ch = engine.channels["en"]
            await wait_for(lambda: ch.status == "live")
            await login(client)
            ingest = await client.ws_connect("/ws/ingest")
            await speak(ingest, 1.5, realtime=True)
            await wait_for(lambda: ch.connects >= 2 and ch.status == "live")
            await ws.close()
    asyncio.run(main())


def test_goaway_hands_over_without_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_GOAWAY_S", "0.3")

    async def main():
        async with running(tmp_path) as (client, engine):
            ws = await listener(client, "en")
            ch = engine.channels["en"]
            await wait_for(lambda: ch.status == "live")
            await login(client)
            ingest = await client.ws_connect("/ws/ingest")
            await speak(ingest, 1.0, realtime=True)
            await wait_for(lambda: ch.connects >= 2)
            assert ch.status == "live"
            await ws.close()
    asyncio.run(main())


def test_off_air_stops_session_even_with_idle_timer(tmp_path):
    async def main():
        async with running(tmp_path, {"idle_stop_s": 600}) as (client, engine):
            ws = await listener(client, "en")
            ch = engine.channels["en"]
            await wait_for(lambda: ch.status == "live")
            await ws.close()
            await wait_for(lambda: ch._stop_handle is not None)   # idle timer pending
            await login(client)
            r = await client.put("/api/admin/config", json={"on_air": False})
            assert r.status == 200
            await wait_for(lambda: ch._task is None and ch.status == "idle")
    asyncio.run(main())


def test_removed_language_releases_listeners(tmp_path):
    async def main():
        async with running(tmp_path) as (client, engine):
            ws = await listener(client, "de")
            await login(client)
            await client.put("/api/admin/config", json={"languages": ["en"]})
            msg = await recv_until(ws, lambda j: j["t"] == "ch" and j["status"] == "removed")
            assert msg == {"t": "ch", "lang": "de", "status": "removed"}
            assert "de" not in engine.channels
    asyncio.run(main())


def test_listener_limit_and_flood(tmp_path):
    async def main():
        async with running(tmp_path, max_clients=1) as (client, engine):
            ws = await client.ws_connect("/ws")
            with pytest.raises(aiohttp.WSServerHandshakeError) as e:
                await client.ws_connect("/ws")
            assert e.value.status == 503
            for _ in range(200):
                await ws.send_json({"t": "ping"})
            msg = await asyncio.wait_for(_until_closed(ws), 5)
            assert ws.close_code == 1008, msg
    asyncio.run(main())


async def _until_closed(ws):
    async for _ in ws:
        pass
    return ws.close_code


def test_admin_requires_login_and_same_origin(tmp_path):
    async def main():
        async with running(tmp_path) as (client, engine):
            r = await client.get("/api/admin/config")
            assert r.status == 401
            with pytest.raises(aiohttp.WSServerHandshakeError):
                await client.ws_connect("/ws/admin")

            evil = {"Origin": "https://evil.example"}
            r = await client.post("/api/admin/login", json={"password": ADMIN_PASSWORD}, headers=evil)
            assert r.status == 403
            await login(client)
            r = await client.put("/api/admin/config", json={"on_air": False}, headers=evil)
            assert r.status == 403 and engine.settings.on_air
            with pytest.raises(aiohttp.WSServerHandshakeError):
                await client.ws_connect("/ws/admin", origin="https://evil.example")

            same = {"Origin": f"http://{client.host}:{client.port}"}
            r = await client.put("/api/admin/config", json={"event_title": "Akce"}, headers=same)
            assert r.status == 200
            status = await client.ws_connect("/ws/admin", origin=same["Origin"])
            st = await recv_until(status, lambda j: "onAir" in j)
            assert st["channels"][0]["lang"] == "en"
            await status.close()
    asyncio.run(main())


def test_login_throttling_ignores_spoofed_forwarded_for(tmp_path):
    async def main():
        # Peer is not a trusted proxy: X-Forwarded-For must not create new "clients".
        async with running(tmp_path, trusted_proxies=()) as (client, engine):
            for i in range(8):
                r = await client.post("/api/admin/login", json={"password": "wrong"},
                                      headers={"X-Forwarded-For": f"203.0.113.{i}"})
                assert r.status == 401
            r = await client.post("/api/admin/login", json={"password": ADMIN_PASSWORD},
                                  headers={"X-Forwarded-For": "203.0.113.200"})
            assert r.status == 429
    asyncio.run(main())


def test_forwarded_headers_from_trusted_proxy(tmp_path):
    async def main():
        async with running(tmp_path) as (client, engine):   # 127.0.0.1 is trusted
            r = await client.get("/api/info", headers={
                "X-Forwarded-Proto": "https", "X-Forwarded-Host": "preklad.example.cz",
                "X-Forwarded-For": "6.6.6.6, 198.51.100.7"})
            assert (await r.json())["publicUrl"] == "https://preklad.example.cz/"

            # The rightmost untrusted hop is the client; a prepended value is ignored.
            for _ in range(8):
                await client.post("/api/admin/login", json={"password": "x"},
                                  headers={"X-Forwarded-For": "6.6.6.6, 198.51.100.7"})
            r = await client.post("/api/admin/login", json={"password": ADMIN_PASSWORD},
                                  headers={"X-Forwarded-For": "1.2.3.4, 198.51.100.7"})
            assert r.status == 429
            r = await client.post("/api/admin/login", json={"password": ADMIN_PASSWORD},
                                  headers={"X-Forwarded-For": "198.51.100.8"})
            assert r.status == 200
    asyncio.run(main())


def test_qr_and_static(tmp_path):
    async def main():
        async with running(tmp_path) as (client, engine):
            r = await client.get("/api/qr.svg")
            assert r.status == 200 and r.content_type == "image/svg+xml"
            r = await client.get("/")
            assert r.status == 200 and "Content-Security-Policy" in r.headers
            r = await client.get("/../server/app/config.py")
            assert r.status in (403, 404)
    asyncio.run(main())
