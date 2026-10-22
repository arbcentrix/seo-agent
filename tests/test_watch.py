"""Publish watch: fingerprints, build signature, and re-fetching only what changed."""
import copy
import hashlib
import re

import pytest

from seo_agent.checks.regression import carry_forward, evaluate
from seo_agent.crawl.crawl import crawl, crawl_watch
from seo_agent.crawl.fetcher import Response
from seo_agent.crawl.fingerprint import build_signature, changed_fields, fingerprint
from seo_agent.crawl.sources import FixtureSource

from conftest import FIXTURES

B = "https://arbcentrix.com"
HOME, TAX, CAREERS = f"{B}/", f"{B}/tax", f"{B}/careers"


class SiteSource:
    """The fixture site, editable, with ETags/304 like Framer, counting requests."""

    def __init__(self):
        fx = FixtureSource(FIXTURES)
        self.meta = fx.meta
        self.texts = {u: fx.get(u).text for u in fx.meta}
        self.requests, self.full_bodies = [], 0

    def edit(self, url, old, new, count=1):
        assert old in self.texts[url], old
        self.texts[url] = self.texts[url].replace(old, new, count)

    def get(self, url, headers=None):
        self.requests.append(url)
        m = self.meta.get(url)
        if m is None:
            return Response(url, 404, final_url=url)
        text = self.texts[url]
        etag = '"%s"' % hashlib.md5(text.encode()).hexdigest()
        if (headers or {}).get("If-None-Match") == etag:
            return Response(url, 304, {"etag": etag}, "", [], url)
        self.full_bodies += 1
        return Response(url, m["status"], {**m["headers"], "etag": etag}, text, [], url)


@pytest.fixture
def site():
    return SiteSource()


@pytest.fixture
def baseline(site, cfg):
    snap = crawl(site, cfg)
    site.requests.clear()
    site.full_bodies = 0
    return snap


def publish(site):
    """What a Framer publish does to the homepage: new hashed bundle names."""
    site.edit(HOME, "script_main.", "script_main.NEWBUILD", count=1)


def test_fingerprints_and_build_signature(baseline):
    home = baseline["pages"][HOME]
    assert home["build_assets"] and all("framerusercontent.com/sites/" in u for u in home["build_assets"])
    assert not any("calendly" in u or "events.framer.com" in u for u in home["build_assets"])
    assert baseline["build_signature"] == build_signature(home)
    page = copy.deepcopy(baseline["pages"][TAX])
    before = fingerprint(page)
    page["title"], page["h1"] = "New", ["New H1"]
    assert changed_fields(before, fingerprint(page)) == ["h1", "title"]
    assert changed_fields(before, before) == []


def test_quiet_watch_only_checks_critical_pages(site, baseline, cfg):
    snap = crawl_watch(site, cfg, baseline)
    w = snap["watch"]
    assert w["mode"] == "quiet" and w["reasons"] == []
    assert len(site.requests) == 2 + 15            # robots.txt, sitemap.xml, 15 critical pages
    assert site.full_bodies == 2                   # only robots and sitemap: every page answered 304
    assert w["fetched"] == 15 and w["not_modified"] == 15 and w["carried"] == 46
    assert w["changed"] == {} and len(snap["pages"]) == 61
    assert snap["pages"][CAREERS]["carried"] is True
    assert evaluate(snap, cfg, baseline, {}, "t")["alerts"] == []
    # Markers do not leak into the next run.
    again = crawl_watch(site, cfg, snap)
    assert again["watch"]["mode"] == "quiet" and not again["pages"][TAX].get("carried")


def test_critical_page_change_is_seen_without_a_publish(site, baseline, cfg):
    site.edit(TAX, "<title>Arbcentrix</title>", "<title>Tax | Arbcentrix</title>")
    w = crawl_watch(site, cfg, baseline)["watch"]
    assert w["mode"] == "quiet" and w["changed"] == {TAX: ["title"]}


def test_publish_refetches_everything_and_reports_changed_fields(site, cfg):
    site.edit(CAREERS, "<title>Arbcentrix</title>", "<title>Careers | Arbcentrix</title>")
    baseline = crawl(site, cfg)
    site.requests.clear()
    site.full_bodies = 0
    publish(site)
    site.edit(CAREERS, "<title>Careers | Arbcentrix</title>", "<title>Arbcentrix</title>")
    snap = crawl_watch(site, cfg, baseline)
    w = snap["watch"]
    assert w["mode"] == "publish" and w["reasons"] == ["build signature changed"]
    assert w["fetched"] == 61 and w["carried"] == 0
    assert site.full_bodies == 2 + 2               # robots, sitemap + the two edited pages; rest 304
    assert w["changed"] == {CAREERS: ["title"]}
    assert snap["build_signature"] != baseline["build_signature"]
    alerts = evaluate(carry_forward(snap, baseline), cfg, baseline, {}, "t")["alerts"]
    assert [(a["rule"], a["url"]) for a in alerts] == [("title_meta_regression", CAREERS)]


def test_non_critical_change_without_publish_waits_for_daily(site, baseline, cfg):
    site.edit(CAREERS, "<title>Arbcentrix</title>", "<title></title>")   # no new build
    snap = crawl_watch(site, cfg, baseline)
    assert snap["watch"]["mode"] == "quiet" and snap["pages"][CAREERS]["title"] == "Arbcentrix"
    full = crawl_watch(site, cfg, baseline, full=True)                     # API-triggered run
    assert full["watch"]["reasons"] == ["full run requested"]
    assert full["watch"]["changed"] == {CAREERS: ["title"]}


def test_sitemap_change_triggers_full_check_and_lists_removed_pages(site, baseline, cfg):
    gone = f"{B}/silent-attrition-old"
    baseline = copy.deepcopy(baseline)
    baseline["pages"][gone] = {**baseline["pages"][CAREERS], "url": gone}
    site.texts[f"{B}/sitemap.xml"] = re.sub(r"<url><loc>https://arbcentrix.com/careers</loc></url>", "",
                                            site.texts[f"{B}/sitemap.xml"])
    w = crawl_watch(site, cfg, baseline)["watch"]
    assert "sitemap changed" in w["reasons"]
    assert gone not in w["removed_pages"]   # it was 200 before, so it is re-checked...
    snap = crawl_watch(site, cfg, baseline)
    assert snap["pages"][gone]["status"] == 404   # ...and found gone


def test_robots_change_triggers_full_check(site, baseline, cfg):
    site.texts[f"{B}/robots.txt"] = "User-agent: *\nDisallow: /tax\n"
    snap = crawl_watch(site, cfg, baseline)
    assert "robots.txt changed" in snap["watch"]["reasons"]
    alerts = evaluate(snap, cfg, baseline, {}, "t")["alerts"]
    assert ("robots_block", TAX) in {(a["rule"], a["url"]) for a in alerts}


def test_first_watch_without_previous_is_a_full_crawl(site, cfg):
    snap = crawl_watch(site, cfg, None)
    assert snap["watch"] == {"mode": "full", "reason": "no previous snapshot"} and len(snap["pages"]) == 61
