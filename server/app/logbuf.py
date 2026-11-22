"""Logging setup with an in-memory ring buffer that the admin page can tail."""

from __future__ import annotations

import collections
import logging


class RingHandler(logging.Handler):
    def __init__(self, capacity: int = 300) -> None:
        super().__init__(level=logging.INFO)
        self.records: collections.deque[dict] = collections.deque(maxlen=capacity)
        self.seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.seq += 1
            self.records.append({
                "i": self.seq,
                "t": record.created,
                "lvl": record.levelname.lower(),
                "src": record.name.rsplit(".", 1)[-1],
                "msg": record.getMessage(),
            })
        except Exception:  # pragma: no cover - logging must never raise
            self.handleError(record)

    def since(self, seq: int) -> list[dict]:
        if seq >= self.seq:
            return []
        # emit() runs under this lock and may be called from worker threads
        # (device listing, PortAudio): iterating unlocked could raise
        # "deque mutated during iteration" and kill the admin status feed.
        self.acquire()
        try:
            return [r for r in self.records if r["i"] > seq]
        finally:
            self.release()


ring = RingHandler()


def setup(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger().addHandler(ring)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
