"""Planted breakages: each one, applied to a copy of the fixtures, raises exactly its alert.

The baseline is the untouched fixture crawl; each case edits a copy on disk (the files the
crawler reads, not the parsed snapshot) and diffs the new crawl against the baseline.
"""
import gzip
import json
import shutil

import pytest

from seo_agent.checks.regression import evaluate
from seo_agent.crawl.crawl import crawl
from seo_agent.crawl.sources import FixtureSource

from conftest import FIXTURES

B = "https://arbcentrix.com"
DROPPED = ["kevin-mairs", "maan-sacdalan", "ystevie-ty", "locations", "careers"]


def edit_page(root, name, fn):
    path = root / "pages" / f"{name}.html.gz"
    with gzip.open(path, "rt", encoding="utf-8") as f:
        old = f.read()
    new = fn(old)
    assert new != old, f"edit changed nothing in {name}"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(new)


def add_noindex(root):
    edit_page(root, "services", lambda t: t.replace("</head>", '<meta name="robots" content="noindex"></head>', 1))


def contact_404(root):
    meta = json.loads((root / "responses.json").read_text())
    meta[f"{B}/contact"].update(status=404)
    (root / "responses.json").write_text(json.dumps(meta))


def disallow_all(root):
    (root / "robots.txt").write_text("User-agent: *\nDisallow: /\n")


def remove_gtm(root):
    for path in (root / "pages").glob("*.html.gz"):
        edit_page(root, path.name.removesuffix(".html.gz"), lambda t: t.replace("GTM-N8DDS834", "GTM-REMOVED"))


def drop_sitemap_urls(root):
    text = (root / "sitemap.xml").read_text()
    for slug in DROPPED:
        entry = f"<url><loc>{B}/{slug}</loc></url>\n"
        assert entry in text
        text = text.replace(entry, "")
    (root / "sitemap.xml").write_text(text)


# plant, rule that must fire, URLs it must report
CASES = {
    "noindex": (add_noindex, "noindex_added", {f"{B}/services"}),
    "contact_404": (contact_404, "status_change", {f"{B}/contact"}),
    "robots_disallow_all": (disallow_all, "robots_block", "critical"),
    "gtm_removed": (remove_gtm, "tracking_missing", "critical"),
    "sitemap_drop": (drop_sitemap_urls, "sitemap_drop", {f"{B}/sitemap.xml"}),
}


@pytest.fixture(scope="module")
def baseline(cfg):
    return crawl(FixtureSource(FIXTURES), cfg, now="2026-10-22T00:00:00+00:00")


def alerts_after(tmp_path, cfg, baseline, plants):
    root = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, root)
    for plant in plants:
        plant(root)
    snap = crawl(FixtureSource(root), cfg, now="2026-10-23T00:00:00+00:00")
    return evaluate(snap, cfg, baseline, {}, "2026-10-23T00:00:00+00:00")["alerts"]


def expected_urls(cfg, urls):
    return set(cfg.critical_urls) if urls == "critical" else urls


def test_healthy_copy_raises_nothing(tmp_path, cfg, baseline):
    assert alerts_after(tmp_path, cfg, baseline, []) == []


@pytest.mark.parametrize("case", CASES)
def test_each_breakage_raises_only_its_alert(case, tmp_path, cfg, baseline):
    plant, rule, urls = CASES[case]
    alerts = alerts_after(tmp_path, cfg, baseline, [plant])
    assert {a["rule"] for a in alerts} == {rule}
    assert {a["url"] for a in alerts} == expected_urls(cfg, urls)
    assert len(alerts) == len({a["url"] for a in alerts})   # one alert per URL, no duplicates
    assert {a["status"] for a in alerts} == {"new"}


def test_all_breakages_together_raise_one_rule_each(tmp_path, cfg, baseline):
    alerts = alerts_after(tmp_path, cfg, baseline, [p for p, _, _ in CASES.values()])
    by_rule = {}
    for a in alerts:
        by_rule.setdefault(a["rule"], set()).add(a["url"])
    expected = {rule: expected_urls(cfg, urls) for _, rule, urls in CASES.values()}
    # A page that 404s has no HTML to check: /contact is status_change's alert alone.
    expected["tracking_missing"] -= {f"{B}/contact"}
    assert by_rule == expected


def test_gtm_removed_from_one_page_is_one_alert(tmp_path, cfg, baseline):
    alerts = alerts_after(tmp_path, cfg, baseline, [
        lambda root: edit_page(root, "tax", lambda t: t.replace("GTM-N8DDS834", "GTM-REMOVED"))])
    assert [(a["rule"], a["url"]) for a in alerts] == [("tracking_missing", f"{B}/tax")]
