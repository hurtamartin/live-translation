"""Entry point: python -m app"""

from __future__ import annotations

import asyncio
import gc
import logging

from aiohttp import web

from . import __version__, logbuf
from .config import Env, SettingsStore
from .engine import Engine
from .web import create_app

log = logging.getLogger("app")


def run() -> None:
    env = Env.load()
    logbuf.setup(env.log_level)
    store = SettingsStore(env)
    if not (env.web_dir / "index.html").exists():
        log.error("Web files not found in %s", env.web_dir)
    engine = Engine(store, env)
    app = create_app(engine)

    async def freeze_heap(_app) -> None:
        # Everything allocated so far lives for the whole run. Moving it out of
        # the collector's reach keeps full GC passes short: a long pass pauses
        # the event loop, and with it every listener's audio clock.
        gc.collect()
        gc.freeze()

    app.on_startup.append(freeze_heap)

    try:
        import uvloop  # type: ignore

        loop = uvloop.new_event_loop()
    except ImportError:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    log.info("Live translation %s listening on http://%s:%d (admin: /admin)",
             __version__, env.host, env.port)
    # backlog: when the QR code goes up, hundreds of phones connect within seconds;
    # the default of 128 pending connections would make some of them retry (1 s+).
    web.run_app(app, host=env.host, port=env.port, access_log=None, print=None,
                loop=loop, shutdown_timeout=5, backlog=1024)
