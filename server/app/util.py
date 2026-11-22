"""Small asyncio helpers."""

from __future__ import annotations

import asyncio
import logging
from typing import Coroutine

log = logging.getLogger(__name__)
_background: set[asyncio.Task] = set()


def spawn(coro: Coroutine, name: str | None = None) -> asyncio.Task:
    """create_task that keeps a strong reference until the task finishes.

    The event loop only holds weak references, so fire-and-forget tasks
    could otherwise be garbage-collected mid-flight.
    """
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _background.add(task)

    def _done(t: asyncio.Task) -> None:
        _background.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.error("Background task %s failed: %r", t.get_name(), t.exception())

    task.add_done_callback(_done)
    return task
