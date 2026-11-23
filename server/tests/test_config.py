from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

import pytest
from conftest import make_env

from app.config import SettingsStore, parse_networks


def test_missing_admin_password_is_never_generated(tmp_path, caplog):
    # settings.json from an older version, which generated a password and stored it
    (tmp_path / "settings.json").write_text(
        json.dumps({"admin_password": "old-generated-pw", "event_title": "Akce"}), "utf-8")
    with caplog.at_level(logging.DEBUG):
        store = SettingsStore(make_env(tmp_path, admin_password=""))
    assert store.admin_password == "", "no password configured: admin disabled, nothing invented"
    assert store.data.event_title == "Akce"
    saved = json.loads((tmp_path / "settings.json").read_text("utf-8"))
    assert "admin_password" not in saved, "the stale copy is removed from disk"
    assert "old-generated-pw" not in caplog.text
    assert "ADMIN_PASSWORD is not set" in caplog.text


def test_admin_password_comes_from_env_only(tmp_path):
    store = SettingsStore(make_env(tmp_path, admin_password="from-env-password"))
    assert store.admin_password == "from-env-password"
    assert not (tmp_path / "settings.json").exists(), "nothing written for the password"


def test_async_saves_never_go_back_in_time(tmp_path):
    async def main():
        store = SettingsStore(make_env(tmp_path))
        pending = []
        for i in range(20):
            store.patch({"event_title": f"v{i}"}, save=False)
            pending.append(asyncio.ensure_future(store.save_async()))
        assert all(await asyncio.gather(*pending))
        saved = json.loads((tmp_path / "settings.json").read_text("utf-8"))
        assert saved["event_title"] == "v19"
    asyncio.run(main())


def test_patch_validates_and_reports_changes(tmp_path):
    store = SettingsStore(make_env(tmp_path))
    changed = store.patch({"languages": ["de", "xx", "de", 5, "en"], "gain_db": 99,
                           "on_air": "false", "gate_enabled": "1"})
    assert store.data.languages == ["de", "en"]
    assert store.data.gain_db == 24.0
    assert store.data.on_air is False
    assert store.data.gate_enabled is True
    assert changed == {"languages", "gain_db"}


@pytest.mark.parametrize("patch", [
    {"gain_db": None},
    {"gain_db": True},
    {"channel": float("inf")},
    {"channel": "abc"},
    {"languages": "en"},
    {"always_on": "en"},
    {"model": "bad model!"},
    {"public_url": "javascript:alert(1)"},
    {"unknown": 1},
])
def test_patch_rejects_bad_values(tmp_path, patch):
    store = SettingsStore(make_env(tmp_path))
    before = json.dumps(store.public_view(), sort_keys=True)
    with pytest.raises((ValueError, TypeError)):
        store.patch(patch)
    assert json.dumps(store.public_view(), sort_keys=True) == before, "nothing applied"


def test_admin_password_not_patchable_and_key_hidden(tmp_path):
    store = SettingsStore(make_env(tmp_path))
    store.patch({"admin_password": "x", "api_key": "AIzaSyTESTKEY123456"})
    assert store.admin_password != "x"
    view = store.public_view()
    assert "admin_password" not in view
    assert view["api_key"] == "" and view["api_key_set"] and view["api_key_hint"] == "…3456"


def test_damaged_file_is_kept_aside(tmp_path):
    (tmp_path / "settings.json").write_text("{not json", "utf-8")
    store = SettingsStore(make_env(tmp_path))
    assert store.data.languages == ["en", "es", "uk"]
    backups = list(tmp_path.glob("settings.json.bad-*"))
    assert len(backups) == 1 and backups[0].read_text("utf-8") == "{not json"


def test_save_failure_does_not_raise(tmp_path, monkeypatch):
    store = SettingsStore(make_env(tmp_path))

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "open", boom)
    assert store.patch({"event_title": "Nový název"}) == {"event_title"}
    assert store.data.event_title == "Nový název"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_settings_file_is_private(tmp_path):
    store = SettingsStore(make_env(tmp_path))
    store.patch({"api_key": "AIzaSyTESTKEY123456"})
    assert (tmp_path / "settings.json").stat().st_mode & 0o777 == 0o600


def test_parse_networks_skips_garbage():
    nets = parse_networks("10.0.0.0/8, nonsense ,::1;192.168.1.7")
    assert [str(n) for n in nets] == ["10.0.0.0/8", "::1/128", "192.168.1.7/32"]
