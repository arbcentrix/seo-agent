import copy
import json
from pathlib import Path

import pytest

from seo_agent.config import load_config
from seo_agent.crawl.crawl import crawl
from seo_agent.crawl.sources import FixtureSource

FIXTURES = Path(__file__).parent / "fixtures" / "2026-10-22"
GOLD = Path(__file__).parent / "gold" / "discovery_2026-10-22.json"


@pytest.fixture(scope="session")
def cfg():
    return load_config(environ={})


@pytest.fixture(scope="session")
def _snapshot(cfg):
    return crawl(FixtureSource(FIXTURES), cfg, now="2026-10-22T00:00:00+00:00")


@pytest.fixture
def snapshot(_snapshot):
    return copy.deepcopy(_snapshot)


@pytest.fixture(scope="session")
def gold():
    return json.loads(GOLD.read_text())["findings"]
