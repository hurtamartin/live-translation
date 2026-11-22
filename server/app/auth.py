"""Admin sessions (password login -> HttpOnly, SameSite=Strict cookie) and login throttling."""

from __future__ import annotations

import collections
import hmac
import logging
import secrets
import time

log = logging.getLogger(__name__)

COOKIE = "lt_admin"
TTL = 12 * 3600
MAX_SESSIONS = 32           # oldest admin session is dropped beyond this

FAIL_WINDOW = 300.0         # seconds a failed attempt counts against its source
MAX_FAILS_PER_IP = 8
# Backstop across all addresses: per-IP counting alone is useless when the
# address can be spoofed or rotated, so the total number of guesses is capped too.
MAX_FAILS_GLOBAL = 100
MAX_TRACKED_IPS = 2048      # bounded memory, whatever the attacker sends


class Auth:
    def __init__(self) -> None:
        self._tokens: dict[str, float] = {}
        self._fails: dict[str, collections.deque[float]] = {}
        self._global: collections.deque[float] = collections.deque()
        self._global_warned = False

    @staticmethod
    def check_password(given: str, expected: str) -> bool:
        return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())

    # ------------------------------------------------------------- throttling

    def _prune(self, now: float) -> None:
        cutoff = now - FAIL_WINDOW
        g = self._global
        while g and g[0] < cutoff:
            g.popleft()
        if not g:
            self._fails.clear()
            self._global_warned = False

    def blocked(self, ip: str) -> bool:
        now = time.monotonic()
        self._prune(now)
        if len(self._global) >= MAX_FAILS_GLOBAL:
            if not self._global_warned:
                self._global_warned = True
                log.warning("Admin login locked for everyone: %d failed attempts in %d s",
                            len(self._global), FAIL_WINDOW)
            return True
        recent = self._fails.get(ip)
        if not recent:
            return False
        cutoff = now - FAIL_WINDOW
        while recent and recent[0] < cutoff:
            recent.popleft()
        if not recent:
            del self._fails[ip]
            return False
        return len(recent) >= MAX_FAILS_PER_IP

    def fail(self, ip: str) -> None:
        now = time.monotonic()
        self._global.append(now)
        recent = self._fails.get(ip)
        if recent is None:
            if len(self._fails) >= MAX_TRACKED_IPS:
                # dicts keep insertion order: forget the address seen longest ago
                del self._fails[next(iter(self._fails))]
            recent = self._fails[ip] = collections.deque(maxlen=MAX_FAILS_PER_IP)
        recent.append(now)

    # ------------------------------------------------------------- sessions

    def issue(self) -> str:
        now = time.time()
        self._tokens = {t: exp for t, exp in self._tokens.items() if exp > now}
        while len(self._tokens) >= MAX_SESSIONS:
            del self._tokens[next(iter(self._tokens))]
        token = secrets.token_urlsafe(32)
        self._tokens[token] = now + TTL
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        exp = self._tokens.get(token)
        return bool(exp and exp > time.time())

    def revoke(self, token: str | None) -> None:
        if token:
            self._tokens.pop(token, None)
