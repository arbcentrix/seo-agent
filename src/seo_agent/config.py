"""Config loading: config/*.yaml plus environment settings."""
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DELIVERY_MODES = ("live", "shadow")


class ConfigError(Exception):
    pass


def _load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError:
        raise ConfigError(f"missing config file: {path}")
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}")
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping")
    return data


def _split(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").replace(";", ",").split(",") if x.strip()]


@dataclass(frozen=True)
class Env:
    delivery_mode: str
    reports_to: list[str]
    alerts_to: list[str]
    shadow_email: str | None
    monday_board_id: str | None
    monday_workspace_id: str | None
    gsc_site: str | None
    ga4_property_id: str | None
    state_branch: str
    # Never printed: repr is suppressed so the secret cannot leak via logs.
    google_sa_json_b64: str | None = field(default=None, repr=False)

    @classmethod
    def from_environ(cls, environ=os.environ) -> "Env":
        mode = environ.get("DELIVERY_MODE", "shadow")
        if mode not in DELIVERY_MODES:
            raise ConfigError(f"DELIVERY_MODE must be one of {DELIVERY_MODES}, got {mode!r}")
        return cls(
            delivery_mode=mode,
            reports_to=_split(environ.get("REPORTS_TO")),
            alerts_to=_split(environ.get("ALERTS_TO")),
            shadow_email=environ.get("SHADOW_EMAIL") or None,
            monday_board_id=environ.get("MONDAY_BOARD_ID") or None,
            monday_workspace_id=environ.get("MONDAY_WORKSPACE_ID") or None,
            gsc_site=environ.get("GSC_SITE") or None,
            ga4_property_id=environ.get("GA4_PROPERTY_ID") or None,
            state_branch=environ.get("STATE_BRANCH", "state"),
            google_sa_json_b64=environ.get("GOOGLE_SA_JSON_B64") or None,
        )

    def allowed_recipients(self, kind: str) -> list[str]:
        """The only addresses email may go to (CLAUDE.md rule)."""
        if self.delivery_mode == "shadow":
            return [self.shadow_email] if self.shadow_email else []
        return self.alerts_to if kind == "alert" else self.reports_to


@dataclass(frozen=True)
class Config:
    site: dict
    rules: dict
    env: Env

    @property
    def base_url(self) -> str:
        return self.site["base_url"].rstrip("/")

    @property
    def user_agent(self) -> str:
        return self.site.get("user_agent", "ArbcentrixSEOAgent/1.0")

    @property
    def critical_urls(self) -> list[str]:
        return [self.base_url + p for p in self.site.get("critical_pages") or []]


def load_config(config_dir: Path = CONFIG_DIR, environ=os.environ) -> Config:
    site = _load_yaml(config_dir / "site.yaml")
    rules = _load_yaml(config_dir / "rules.yaml")
    for key in ("domain", "base_url"):
        if not site.get(key):
            raise ConfigError(f"site.yaml: missing {key}")
    return Config(site=site, rules=rules, env=Env.from_environ(environ))
