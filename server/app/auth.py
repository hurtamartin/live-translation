"""Admin sessions: password login -> HttpOnly, SameSite=Strict cookie."""

from __future__ import annotations

import hmac
import secrets
import time

COOKIE = "lt_admin"
TTL = 12 * 3600


class Auth:
    def __init__(self) -> None:
        self._tokens: dict[str, float] = {}
        self._fails: dict[str, list[float]] = {}

    def check_password(self, given: str, expected: str) -> bool:
        return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())

    def blocked(self, ip: str) -> bool:
        now = time.time()
        recent = [t for t in self._fails.get(ip, []) if now - t < 300]
        self._fails[ip] = recent
        return len(recent) >= 8

    def fail(self, ip: str) -> None:
        self._fails.setdefault(ip, []).append(time.time())

    def issue(self) -> str:
        now = time.time()
        self._tokens = {t: exp for t, exp in self._tokens.items() if exp > now}
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
