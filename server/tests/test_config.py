from __future__ import annotations

import json
import os
import sys

import pytest
from conftest import make_env

from app.config import SettingsStore, parse_networks


def test_defaults_and_generated_password(tmp_path):
    store = SettingsStore(make_env(tmp_path, admin_password=""))
    assert store.admin_password, "a password is generated when none is configured"
    saved = json.loads((tmp_path / "settings.json").read_text("utf-8"))
    assert saved["admin_password"] == store.admin_password


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
