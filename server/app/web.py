"""HTTP + WebSocket routes."""

from __future__ import annotations

import asyncio
import functools
import io
import json
import logging

import segno
from aiohttp import WSMsgType, web

from .audio.codecs import Mp3Encoder
from .audio.sources import BrowserSource, list_devices
from .auth import COOKIE, TTL, Auth
from .clients import Client, StreamSink
from .engine import Engine
from .languages import LANGUAGES
from .logbuf import ring

log = logging.getLogger(__name__)

ENGINE = web.AppKey("engine", Engine)
AUTH = web.AppKey("auth", Auth)
QR_CACHE = web.AppKey("qr", dict)

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
       "media-src 'self' blob:; connect-src 'self' ws: wss:; font-src 'self'; worker-src 'self'; "
       "manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


# ---------------------------------------------------------------- helpers

def _engine(request: web.Request) -> Engine:
    return request.app[ENGINE]


def _client_ip(request: web.Request) -> str:
    if _engine(request).env.trust_proxy:
        fwd = request.headers.get("X-Forwarded-For")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.remote or ""


def _is_https(request: web.Request) -> bool:
    if _engine(request).env.trust_proxy and request.headers.get("X-Forwarded-Proto") == "https":
        return True
    return request.secure


def public_url(request: web.Request) -> str:
    engine = _engine(request)
    if engine.settings.public_url:
        return engine.settings.public_url
    proto = "https" if _is_https(request) else "http"
    host = request.headers.get("X-Forwarded-Host") if engine.env.trust_proxy else None
    return f"{proto}://{host or request.host}/"


def _is_admin(request: web.Request) -> bool:
    return request.app[AUTH].valid(request.cookies.get(COOKIE))


def admin_only(handler):
    @functools.wraps(handler)
    async def wrapper(request: web.Request):
        if not _is_admin(request):
            return web.json_response({"error": "Nejste přihlášeni."}, status=401)
        return await handler(request)
    return wrapper


async def _json_body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text="Invalid JSON")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="Expected object")
    return data


async def on_prepare(request: web.Request, response: web.StreamResponse) -> None:
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "no-referrer")
    path = request.path
    if response.content_type == "text/html":
        h.setdefault("Content-Security-Policy", CSP)
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
    if path.startswith(("/fonts/", "/icons/")):
        h.setdefault("Cache-Control", "public, max-age=604800")
    elif "Cache-Control" not in h:
        h["Cache-Control"] = "no-cache"


# ---------------------------------------------------------------- public

async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_engine(request).env.web_dir / "index.html")


async def admin_page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_engine(request).env.web_dir / "admin.html")


async def healthz(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def api_info(request: web.Request) -> web.Response:
    e = _engine(request)
    s = e.settings
    return web.json_response({"title": s.event_title, "onAir": s.on_air, "langs": s.languages,
                              "publicUrl": public_url(request)})


async def manifest(request: web.Request) -> web.Response:
    title = _engine(request).settings.event_title or "Živý překlad"
    body = {
        "id": "/",
        "name": title,
        "short_name": title if len(title) <= 14 else title[:13].rstrip() + "…",
        "description": "Živý překlad do vašeho telefonu: text i zvuk.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#10162B",
        "theme_color": "#10162B",
        "icons": [
            {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/icons/maskable-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
    }
    return web.Response(text=json.dumps(body, ensure_ascii=False),
                        content_type="application/manifest+json")


async def qr_svg(request: web.Request) -> web.Response:
    url = public_url(request)
    cache = request.app[QR_CACHE]
    svg = cache.get(url)
    if svg is None:
        buf = io.BytesIO()
        segno.make(url, error="m").save(buf, kind="svg", border=2, dark="#10162B",
                                        light="#FFFFFF", xmldecl=False, omitsize=True)
        svg = buf.getvalue()
        cache.clear()
        cache[url] = svg
    return web.Response(body=svg, content_type="image/svg+xml",
                        headers={"X-Public-Url": url})


async def ws_listener(request: web.Request) -> web.WebSocketResponse:
    engine = _engine(request)
    if len(engine.clients) >= engine.env.max_clients:
        raise web.HTTPServiceUnavailable(text="Too many listeners")
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4096, compress=False)
    await ws.prepare(request)
    client = Client(ws, _client_ip(request))
    engine.add_client(client)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                j = json.loads(msg.data)
            except ValueError:
                continue
            if not isinstance(j, dict):
                continue
            t = j.get("t")
            if t == "sub":
                engine.subscribe(client, str(j.get("lang", ""))[:16], bool(j.get("audio")))
            elif t == "audio" and client.channel is not None:
                client.channel.set_audio(client, bool(j.get("on")))
            elif t == "ping":
                client.send_text('{"t":"pong"}')
    finally:
        engine.remove_client(client)
    return ws


async def stream_mp3(request: web.Request) -> web.StreamResponse:
    engine = _engine(request)
    ch = engine.channels.get(request.match_info["lang"])
    if ch is None:
        raise web.HTTPNotFound()
    if not Mp3Encoder.available():
        raise web.HTTPServiceUnavailable(text="MP3 encoder not available")
    resp = web.StreamResponse(headers={
        "Content-Type": "audio/mpeg",
        "Cache-Control": "no-cache, no-store",
        "X-Accel-Buffering": "no",
    })
    await resp.prepare(request)
    sink = StreamSink()
    ch.add_stream(sink)
    try:
        while True:
            try:
                data = await asyncio.wait_for(sink.q.get(), timeout=10)
            except asyncio.TimeoutError:
                if request.transport is None or request.transport.is_closing():
                    break
                continue
            await resp.write(data)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        ch.remove_stream(sink)
    return resp


# ---------------------------------------------------------------- admin

async def admin_login(request: web.Request) -> web.Response:
    auth = request.app[AUTH]
    ip = _client_ip(request)
    if auth.blocked(ip):
        return web.json_response({"error": "Příliš mnoho pokusů. Zkuste to za 5 minut."}, status=429)
    data = await _json_body(request)
    if not auth.check_password(str(data.get("password", "")), _engine(request).store.admin_password):
        auth.fail(ip)
        await asyncio.sleep(0.8)
        log.warning("Failed admin login from %s", ip)
        return web.json_response({"error": "Nesprávné heslo."}, status=401)
    resp = web.json_response({"ok": True})
    resp.set_cookie(COOKIE, auth.issue(), httponly=True, samesite="Strict",
                    secure=_is_https(request), max_age=TTL, path="/")
    return resp


async def admin_logout(request: web.Request) -> web.Response:
    request.app[AUTH].revoke(request.cookies.get(COOKIE))
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE, path="/")
    return resp


async def admin_session(request: web.Request) -> web.Response:
    return web.json_response({"authed": _is_admin(request)})


def _data_files(engine: Engine) -> list[str]:
    try:
        return sorted(p.name for p in engine.env.data_dir.glob("*.wav"))[:100]
    except OSError:
        return []


@admin_only
async def admin_config(request: web.Request) -> web.Response:
    engine = _engine(request)
    return web.json_response({
        "settings": engine.store.public_view(),
        "catalog": LANGUAGES,
        "publicUrl": public_url(request),
        "files": _data_files(engine),
    })


@admin_only
async def admin_config_put(request: web.Request) -> web.Response:
    engine = _engine(request)
    patch = await _json_body(request)
    try:
        changed = engine.store.patch(patch)
    except (ValueError, TypeError) as e:
        return web.json_response({"error": str(e)}, status=400)
    if changed:
        log.info("Settings changed: %s", ", ".join(sorted(changed - {"api_key"}))
                 + (" (API key)" if "api_key" in changed else ""))
        await engine.apply(changed)
    return web.json_response({"settings": engine.store.public_view(), "publicUrl": public_url(request)})


@admin_only
async def admin_devices(request: web.Request) -> web.Response:
    engine = _engine(request)
    refresh = request.query.get("refresh") == "1" and not (
        engine.source.kind == "device" and engine.source.state == "running")
    devices = await asyncio.to_thread(list_devices, refresh)
    return web.json_response({"devices": devices})


@admin_only
async def admin_clear(request: web.Request) -> web.Response:
    _engine(request).clear_history()
    return web.json_response({"ok": True})


async def ws_admin(request: web.Request) -> web.StreamResponse:
    if not _is_admin(request):
        return web.json_response({"error": "Nejste přihlášeni."}, status=401)
    engine = _engine(request)
    ws = web.WebSocketResponse(heartbeat=20, compress=False)
    await ws.prepare(request)
    seq = max(0, ring.seq - 150)

    async def pump() -> None:
        nonlocal seq
        while not ws.closed:
            st = engine.status()
            logs = ring.since(seq)
            if logs:
                seq = logs[-1]["i"]
                st["log"] = logs
            await ws.send_str(json.dumps(st, ensure_ascii=False))
            await asyncio.sleep(0.1)

    task = asyncio.create_task(pump())
    try:
        async for _ in ws:
            pass
    finally:
        task.cancel()
    return ws


async def ws_ingest(request: web.Request) -> web.StreamResponse:
    if not _is_admin(request):
        return web.json_response({"error": "Nejste přihlášeni."}, status=401)
    engine = _engine(request)
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=256 * 1024, compress=False)
    await ws.prepare(request)
    src = engine.source
    if not isinstance(src, BrowserSource):
        await ws.close(code=4409, message="Zdroj zvuku není nastaven na prohlížeč.".encode())
        return ws
    await src.attach(ws, request.query.get("label", "")[:80])
    log.info("Browser audio source connected (%s)", src.detail)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY and engine.source is src:
                src.feed(msg.data)
    finally:
        src.detach(ws)
        log.info("Browser audio source disconnected")
    return ws


# ---------------------------------------------------------------- app

def create_app(engine: Engine) -> web.Application:
    app = web.Application(client_max_size=64 * 1024)
    app[ENGINE] = engine
    app[AUTH] = Auth()
    app[QR_CACHE] = {}
    r = app.router
    r.add_get("/healthz", healthz)
    r.add_get("/api/info", api_info)
    r.add_get("/manifest.webmanifest", manifest)
    r.add_get("/api/qr.svg", qr_svg)
    r.add_get("/ws", ws_listener)
    r.add_get("/stream/{lang}.mp3", stream_mp3, allow_head=False)
    r.add_post("/api/admin/login", admin_login)
    r.add_post("/api/admin/logout", admin_logout)
    r.add_get("/api/admin/session", admin_session)
    r.add_get("/api/admin/config", admin_config)
    r.add_put("/api/admin/config", admin_config_put)
    r.add_get("/api/admin/devices", admin_devices)
    r.add_post("/api/admin/clear", admin_clear)
    r.add_get("/ws/admin", ws_admin)
    r.add_get("/ws/ingest", ws_ingest)
    r.add_get("/", index)
    r.add_get("/admin", admin_page)
    r.add_static("/", engine.env.web_dir, show_index=False, follow_symlinks=False)
    app.on_response_prepare.append(on_prepare)

    async def startup(_app):
        await engine.start()

    async def shutdown(_app):
        await engine.stop()

    app.on_startup.append(startup)
    app.on_shutdown.append(shutdown)
    return app
