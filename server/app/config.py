"""Environment + persisted settings (DATA_DIR/settings.json)."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
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


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


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
    trust_proxy: bool
    max_clients: int

    @classmethod
    def load(cls) -> "Env":
        here = Path(__file__).resolve().parent
        default_web = here.parent.parent / "web"
        return cls(
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8080")),
            data_dir=Path(os.environ.get("DATA_DIR", here.parent.parent / "data")),
            web_dir=Path(os.environ.get("WEB_DIR", default_web)),
            admin_password=os.environ.get("ADMIN_PASSWORD", ""),
            api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
            gemini_ws_url=os.environ.get("GEMINI_WS_URL", DEFAULT_WS_URL),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            trust_proxy=_env_bool("TRUST_PROXY", True),
            max_clients=int(os.environ.get("MAX_CLIENTS", "1000")),
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
    admin_password: str = ""  # only used when ADMIN_PASSWORD env is not set


_MODEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def _clamp(v, lo, hi, cast):
    return max(lo, min(hi, cast(v)))


def _coerce(name: str, value, current: Settings):
    """Validate one incoming field. Raises ValueError with a user-facing message."""
    if name in ("on_air", "gate_enabled", "echo_target"):
        return bool(value)
    if name == "languages":
        if not isinstance(value, list):
            raise ValueError("Jazyky musí být seznam.")
        out: list[str] = []
        for code in value:
            if code in LANGUAGES and code not in out:
                out.append(code)
        if len(out) > 24:
            raise ValueError("Nejvýše 24 jazyků najednou.")
        return out
    if name == "always_on":
        if not isinstance(value, list):
            raise ValueError("Seznam musí být pole.")
        return [c for c in dict.fromkeys(value) if c in LANGUAGES]
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
        if value and not re.match(r"^https?://[^\s]{1,300}$", value):
            raise ValueError("Adresa musí začínat http:// nebo https://")
        return value
    limits = {"api_key": 200, "event_title": 80, "device": 200, "file_path": 300,
              "system_instruction": 4000, "admin_password": 200}
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
        self._load()

    def _load(self) -> None:
        self.env.data_dir.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text("utf-8"))
                known = {f.name for f in fields(Settings)}
                for k, v in raw.items():
                    if k in known:
                        try:
                            setattr(self.data, k, _coerce(k, v, self.data))
                        except (ValueError, TypeError):
                            log.warning("Ignoring invalid setting %s", k)
            except Exception as e:
                log.error("Could not read %s: %s", self.path, e)
        self.data.always_on = [c for c in self.data.always_on if c in self.data.languages]
        if not self.env.admin_password and not self.data.admin_password:
            self.data.admin_password = secrets.token_urlsafe(9)
            self.save()
            log.warning("ADMIN_PASSWORD is not set. Generated admin password: %s",
                        self.data.admin_password)

    @property
    def admin_password(self) -> str:
        return self.env.admin_password or self.data.admin_password

    @property
    def api_key(self) -> str:
        return self.env.api_key or self.data.api_key

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self.data), ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def patch(self, patch: dict) -> set[str]:
        """Validate and apply a partial update. Returns the names that changed."""
        if not isinstance(patch, dict):
            raise ValueError("Očekáván JSON objekt.")
        staged = {}
        for k, v in patch.items():
            if k == "admin_password":
                continue  # not editable through the API
            if k == "api_key" and self.env.api_key:
                continue  # env wins
            staged[k] = _coerce(k, v, self.data)
        changed = set()
        for k, v in staged.items():
            if getattr(self.data, k) != v:
                setattr(self.data, k, v)
                changed.add(k)
        if "languages" in changed:
            self.data.always_on = [c for c in self.data.always_on if c in self.data.languages]
        if changed:
            self.save()
        return changed

    def public_view(self) -> dict:
        d = asdict(self.data)
        d.pop("admin_password", None)
        key = self.api_key
        d["api_key"] = ""
        d["api_key_set"] = bool(key)
        d["api_key_hint"] = ("…" + key[-4:]) if len(key) > 8 else ""
        d["api_key_env"] = bool(self.env.api_key)
        return d
