"""Environment + persisted settings (DATA_DIR/settings.json)."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .languages import LANGUAGES

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.5-live-translate-preview"
DEFAULT_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
SOURCES = ("device", "browser", "file", "none")

# Reverse proxies whose X-Forwarded-* headers are believed: loopback and the
# address range Docker gives its bridge networks (Caddy from docker-compose).
# Headers from anyone else are ignored, otherwise a client could fake its
# address and dodge the login throttling, or fake https.
DEFAULT_TRUSTED_PROXIES = "127.0.0.0/8,::1/128,172.16.0.0/12"

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

MIN_PASSWORD_LEN = 12


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        log.warning("%s=%r is not a number, using %d", name, raw, default)
        return default


def parse_networks(spec: str) -> tuple[IPNetwork, ...]:
    nets: list[IPNetwork] = []
    for part in spec.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            log.warning("Ignoring invalid network in TRUSTED_PROXIES: %r", part)
    return tuple(nets)


@dataclass(frozen=True)
class Env:
    host: str
    port: int
    data_dir: Path
    web_dir: Path
    admin_password: str
    api_key: str
    gemini_ws_url: str
    log_level: str
    trusted_proxies: tuple[IPNetwork, ...]
    max_clients: int

    @classmethod
    def load(cls) -> "Env":
        here = Path(__file__).resolve().parent
        default_web = here.parent.parent / "web"
        trust = _env_bool("TRUST_PROXY", True)
        return cls(
            host=os.environ.get("HOST", "0.0.0.0"),
            port=_env_int("PORT", 8080, 1, 65535),
            data_dir=Path(os.environ.get("DATA_DIR", here.parent.parent / "data")),
            web_dir=Path(os.environ.get("WEB_DIR", default_web)),
            admin_password=os.environ.get("ADMIN_PASSWORD", ""),
            api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
            gemini_ws_url=os.environ.get("GEMINI_WS_URL", DEFAULT_WS_URL),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            trusted_proxies=parse_networks(
                os.environ.get("TRUSTED_PROXIES", DEFAULT_TRUSTED_PROXIES)) if trust else (),
            max_clients=_env_int("MAX_CLIENTS", 1000, 1, 100_000),
        )


@dataclass
class Settings:
    api_key: str = ""
    model: str = DEFAULT_MODEL
    event_title: str = "Živý překlad"
    languages: list[str] = field(default_factory=lambda: ["en", "es", "uk"])
    always_on: list[str] = field(default_factory=list)
    on_air: bool = False
    source: str = "device"
    device: str = ""
    channel: int = 0
    file_path: str = ""
    gain_db: float = 0.0
    gate_enabled: bool = True
    gate_threshold_db: float = -50.0
    gate_hold_ms: int = 1500
    idle_stop_s: int = 90
    system_instruction: str = ""
    echo_target: bool = False
    public_url: str = ""


_MODEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_URL_RE = re.compile(r"^https?://[^\s]{1,300}$")


def _clamp(v, lo, hi, cast):
    if v is None or isinstance(v, bool):
        raise ValueError("Očekáváno číslo.")
    return max(lo, min(hi, cast(v)))


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _codes(value) -> list[str]:
    """Known language codes from a list, in order, without duplicates."""
    if not isinstance(value, list):
        raise ValueError("Jazyky musí být seznam.")
    return [c for c in dict.fromkeys(v for v in value if isinstance(v, str)) if c in LANGUAGES]


def _coerce(name: str, value, current: Settings):
    """Validate one incoming field. Raises ValueError with a user-facing message."""
    if name in ("on_air", "gate_enabled", "echo_target"):
        return _as_bool(value)
    if name == "languages":
        out = _codes(value)
        if len(out) > 24:
            raise ValueError("Nejvýše 24 jazyků najednou.")
        return out
    if name == "always_on":
        return _codes(value)
    if name == "source":
        if value not in SOURCES:
            raise ValueError("Neznámý zdroj zvuku.")
        return value
    if name == "channel":
        return _clamp(value, 0, 63, int)
    if name == "gain_db":
        return round(_clamp(value, -24.0, 24.0, float), 1)
    if name == "gate_threshold_db":
        return round(_clamp(value, -90.0, 0.0, float), 1)
    if name == "gate_hold_ms":
        return _clamp(value, 200, 10000, int)
    if name == "idle_stop_s":
        return _clamp(value, 0, 3600, int)
    if name == "model":
        value = str(value).strip()
        if not _MODEL_RE.match(value):
            raise ValueError("Neplatný název modelu.")
        return value
    if name == "public_url":
        value = str(value).strip()
        if value and not _URL_RE.match(value):
            raise ValueError("Adresa musí začínat http:// nebo https://")
        return value
    limits = {"api_key": 200, "event_title": 80, "device": 200, "file_path": 300,
              "system_instruction": 4000}
    if name in limits:
        value = str(value)
        if name != "system_instruction":
            value = value.strip()
        if len(value) > limits[name]:
            raise ValueError("Text je příliš dlouhý.")
        return value
    raise ValueError(f"Neznámé nastavení: {name}")


class SettingsStore:
    def __init__(self, env: Env) -> None:
        self.env = env
        self.path = env.data_dir / "settings.json"
        self.data = Settings()
        # Saves run in a worker thread (fsync must not stall the audio clock).
        # Each snapshot gets a version so an older one never overwrites a newer one.
        self._lock = threading.Lock()
        self._version = 0
        self._written = 0
        self._load()

    def _read(self) -> dict:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not a JSON object")
            return raw
        except FileNotFoundError:
            return {}
        except ValueError as e:
            # Keep the damaged file for inspection instead of silently overwriting
            # it with defaults: it may hold the API key.
            bad = self.path.with_name(f"settings.json.bad-{int(time.time())}")
            log.error("Settings file %s is damaged (%s), moved to %s, using defaults",
                      self.path, e, bad.name)
            try:
                os.replace(self.path, bad)
            except OSError:
                pass
        except OSError as e:
            log.error("Could not read %s: %s", self.path, e)
        return {}

    def _load(self) -> None:
        self.env.data_dir.mkdir(parents=True, exist_ok=True)
        known = {f.name for f in fields(Settings)}
        raw = self._read()
        for k, v in raw.items():
            if k in known:
                try:
                    setattr(self.data, k, _coerce(k, v, self.data))
                except (ValueError, TypeError, OverflowError):
                    log.warning("Ignoring invalid setting %s", k)
        self.data.always_on = [c for c in self.data.always_on if c in self.data.languages]
        if "admin_password" in raw:
            # Older versions generated a password and stored it here. The password
            # now only comes from the environment: remove the stale copy from disk.
            self.save()
        pw = self.env.admin_password
        if not pw:
            log.error("ADMIN_PASSWORD is not set: the admin console is disabled. "
                      "Set it in .env and restart.")
        elif len(pw) < MIN_PASSWORD_LEN:
            log.warning("ADMIN_PASSWORD is shorter than %d characters: use a longer one.",
                        MIN_PASSWORD_LEN)

    @property
    def admin_password(self) -> str:
        """Empty when not configured: then nobody can log in."""
        return self.env.admin_password

    @property
    def api_key(self) -> str:
        return self.env.api_key or self.data.api_key

    def _snapshot(self) -> tuple[int, bytes]:
        self._version += 1
        return self._version, json.dumps(asdict(self.data), ensure_ascii=False,
                                         indent=2).encode("utf-8")

    def _write(self, version: int, data: bytes) -> bool:
        """Atomically persist one snapshot. Thread-safe.

        Never raises: a full or read-only disk must not break the running
        broadcast, the change still applies in memory and the error is logged.
        """
        with self._lock:
            if version <= self._written:
                return True   # a newer snapshot is already on disk
            tmp = self.path.with_suffix(".tmp")
            try:
                # Created 0600 from the start: the file holds the API key.
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except OSError as e:
                log.error("Could not save settings to %s: %s", self.path, e)
                return False
            self._written = version
            try:
                os.chmod(self.path, 0o600)  # files written by older versions
            except OSError:
                pass
            return True

    def save(self) -> bool:
        return self._write(*self._snapshot())

    async def save_async(self) -> bool:
        """save() without blocking the event loop (fsync can take long on slow disks)."""
        version, data = self._snapshot()
        return await asyncio.to_thread(self._write, version, data)

    def patch(self, patch: dict, save: bool = True) -> set[str]:
        """Validate and apply a partial update. Returns the names that changed.

        With save=False the caller persists the change (save_async) itself.
        """
        if not isinstance(patch, dict):
            raise ValueError("Očekáván JSON objekt.")
        staged = {}
        for k, v in patch.items():
            if k == "admin_password":
                continue  # comes from the environment only
            if k == "api_key" and self.env.api_key:
                continue  # env wins
            try:
                staged[k] = _coerce(k, v, self.data)
            except OverflowError as e:
                raise ValueError("Číslo je mimo rozsah.") from e
        changed = set()
        for k, v in staged.items():
            if getattr(self.data, k) != v:
                setattr(self.data, k, v)
                changed.add(k)
        if "languages" in changed:
            self.data.always_on = [c for c in self.data.always_on if c in self.data.languages]
        if changed and save:
            self.save()
        return changed

    def public_view(self) -> dict:
        d = asdict(self.data)
        key = self.api_key
        d["api_key"] = ""
        d["api_key_set"] = bool(key)
        d["api_key_hint"] = ("…" + key[-4:]) if len(key) > 8 else ""
        d["api_key_env"] = bool(self.env.api_key)
        return d
