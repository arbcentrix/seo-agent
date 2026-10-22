"""The checks must reproduce the discovery findings from the saved fixtures."""
from seo_agent.checks import run_checks
from seo_agent.checks.gold import findings_to_gold


def test_crawl_covers_sitemap_and_discovered_links(snapshot):
    assert len(snapshot["sitemap"]["urls"]) == 59
    assert len(snapshot["pages"]) == 61  # + /privacy-policy and /terms found via links
    assert not snapshot["truncated"]


def test_reproduces_gold(snapshot, cfg, gold):
    assert findings_to_gold(run_checks(snapshot, cfg)) == gold


def test_every_finding_has_evidence(snapshot, cfg):
    for f in run_checks(snapshot, cfg):
        assert f["url"] and f["evidence"], f


class _ShellOnce:
    """Wraps a source so the first fetch of one URL returns an empty Framer shell."""

    def __init__(self, inner, url, persistent=False):
        self.inner, self.url, self.persistent, self.hits = inner, url, persistent, 0

    def get(self, url):
        r = self.inner.get(url)
        if url == self.url:
            self.hits += 1
            if self.hits == 1 or self.persistent:
                r.text = '<html><head><title>Arbcentrix</title></head><body><div id="main"></div></body></html>'
        return r


def test_cold_render_shell_is_refetched_not_reported(cfg):
    from seo_agent.crawl.crawl import crawl
    from seo_agent.crawl.sources import FixtureSource
    from conftest import FIXTURES

    url = "https://arbcentrix.com/tax"
    src = _ShellOnce(FixtureSource(FIXTURES), url)
    snap = crawl(src, cfg)
    assert src.hits == 2 and snap["pages"][url]["cold_render"] is True
    assert not [f for f in run_checks(snap, cfg) if f["rule"] == "empty_shell"]

    src = _ShellOnce(FixtureSource(FIXTURES), url, persistent=True)
    snap = crawl(src, cfg)
    assert [f["url"] for f in run_checks(snap, cfg) if f["rule"] == "empty_shell"] == [url]


def test_no_page_has_jsonld(snapshot, cfg):
    assert all(p["jsonld_types"] == [] for p in snapshot["pages"].values())
    assert len([f for f in run_checks(snapshot, cfg) if f["rule"] == "jsonld_missing"]) == 61


def test_jsonld_types_are_extracted():
    from seo_agent.crawl.parser import parse_page
    html = ('<html><head><title>t</title><script type="application/ld+json">'
            '{"@graph":[{"@type":"Organization"},{"@type":["Service","Thing"]}]}</script>'
            '<script type="application/ld+json">{bad json</script></head><body>x</body></html>')
    assert parse_page(html, "https://arbcentrix.com/")["jsonld_types"] == ["Organization", "Service", "Thing"]


def test_overlapping_accounting_pages(snapshot, cfg):
    hits = {f["url"].rsplit("/", 1)[1]: f for f in run_checks(snapshot, cfg) if f["rule"] == "overlapping_content"}
    assert sorted(hits) == ["accounting-services", "audit", "tax"]
    assert sorted(hits["accounting-services"]["evidence"]["overlaps_with"]) == [
        "https://arbcentrix.com/audit", "https://arbcentrix.com/tax"]
