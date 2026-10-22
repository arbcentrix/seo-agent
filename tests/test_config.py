import pytest

from seo_agent.config import ConfigError, Env, load_config


def test_loads_repo_config():
    cfg = load_config(environ={})
    assert cfg.base_url == "https://arbcentrix.com"
    assert cfg.user_agent == "ArbcentrixSEOAgent/1.0"
    assert cfg.env.delivery_mode == "shadow"


def test_bad_delivery_mode():
    with pytest.raises(ConfigError):
        Env.from_environ({"DELIVERY_MODE": "yolo"})


def test_recipients_follow_mode():
    base = {"REPORTS_TO": "a@x.com, b@x.com", "ALERTS_TO": "c@x.com", "SHADOW_EMAIL": "s@x.com"}
    shadow = Env.from_environ({**base, "DELIVERY_MODE": "shadow"})
    live = Env.from_environ({**base, "DELIVERY_MODE": "live"})
    assert shadow.allowed_recipients("alert") == shadow.allowed_recipients("report") == ["s@x.com"]
    assert live.allowed_recipients("report") == ["a@x.com", "b@x.com"]
    assert live.allowed_recipients("alert") == ["c@x.com"]


def test_secret_not_in_repr():
    env = Env.from_environ({"GOOGLE_SA_JSON_B64": "SECRETVALUE"})
    assert "SECRETVALUE" not in repr(env)
