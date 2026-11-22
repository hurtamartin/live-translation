from __future__ import annotations

from app import auth
from app.auth import Auth


def test_per_ip_limit():
    a = Auth()
    for _ in range(auth.MAX_FAILS_PER_IP):
        assert not a.blocked("1.1.1.1")
        a.fail("1.1.1.1")
    assert a.blocked("1.1.1.1")
    assert not a.blocked("2.2.2.2")


def test_global_limit_stops_rotating_addresses():
    a = Auth()
    for i in range(auth.MAX_FAILS_GLOBAL):
        ip = f"10.0.{i // 250}.{i % 250}"
        assert not a.blocked(ip)
        a.fail(ip)
    assert a.blocked("10.99.99.99")


def test_memory_is_bounded(monkeypatch):
    monkeypatch.setattr(auth, "MAX_FAILS_GLOBAL", 10**9)
    a = Auth()
    for i in range(auth.MAX_TRACKED_IPS + 500):
        a.fail(f"ip-{i}")
    assert len(a._fails) <= auth.MAX_TRACKED_IPS
    # Checking an unknown address must not create an entry.
    a.blocked("never-seen")
    assert "never-seen" not in a._fails


def test_sessions():
    a = Auth()
    t = a.issue()
    assert a.valid(t) and not a.valid("other") and not a.valid(None)
    a.revoke(t)
    assert not a.valid(t)
    tokens = [a.issue() for _ in range(auth.MAX_SESSIONS + 5)]
    assert len(a._tokens) == auth.MAX_SESSIONS
    assert a.valid(tokens[-1]) and not a.valid(tokens[0])


def test_password_check():
    assert Auth.check_password("abc", "abc")
    assert not Auth.check_password("abd", "abc")
    assert not Auth.check_password("", "")
