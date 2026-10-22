from seo_agent.crawl.fetcher import Fetcher
from seo_agent.crawl.parser import normalize_url, parse_page
from seo_agent.crawl.robots import is_blocked, parse_robots
from seo_agent.crawl.sitemap import SitemapError, parse_sitemap

import pytest


def test_robots():
    rules = parse_robots("User-agent: *\nDisallow: /private\nAllow: /private/ok\nDisallow: /*.pdf$\n")
    assert is_blocked(rules, "https://x.com/private/a")
    assert not is_blocked(rules, "https://x.com/private/ok")
    assert is_blocked(rules, "https://x.com/f.pdf") and not is_blocked(rules, "https://x.com/f.pdfx")
    assert not is_blocked(parse_robots("User-agent: *\nAllow: /\n"), "https://x.com/tax")
    assert not is_blocked(parse_robots("User-agent: *\nDisallow:\n"), "https://x.com/tax")


def test_sitemap():
    kind, locs = parse_sitemap('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                               '<url><loc> https://a.com/x </loc></url></urlset>')
    assert kind == "urlset" and locs == ["https://a.com/x"]
    with pytest.raises(SitemapError):
        parse_sitemap("<html>")


def test_parser_basics():
    html = """<html><head><title> Tax | Arbcentrix </title>
      <meta name="description" content="d"><meta name="robots" content="noindex">
      <link rel="canonical" href="/tax"></head>
      <body><div id="main"><h1>A</h1><h1>A</h1><h1>B</h1>
      <a href="/about/#x">a</a><a href="https://calendly.com/arbcentrix/schedule-a-consultation">b</a>
      <a href="mailto:x@y">m</a><form></form><script>var id='GTM-ABC123'</script> one two</div></body></html>"""
    p = parse_page(html, "https://arbcentrix.com/tax", {"X-Robots-Tag": "none"},
                   booking_pattern="calendly.com/arbcentrix/schedule-a-consultation")
    assert p["title"] == "Tax | Arbcentrix" and p["meta_description"] == "d"
    assert p["canonicals"] == ["https://arbcentrix.com/tax"]
    assert p["noindex"] and p["h1"] == ["A", "B"]
    assert p["internal_links"] == ["https://arbcentrix.com/about"]
    assert p["has_booking"] and p["has_form"] and p["tracking_ids"] == ["GTM-ABC123"]
    assert not p["empty_shell"]
    assert parse_page('<body><div id="main"></div></body>', "https://arbcentrix.com/x")["empty_shell"]


def test_normalize():
    assert normalize_url("HTTPS://Arbcentrix.com/a/#f") == "https://arbcentrix.com/a"
    assert normalize_url("https://arbcentrix.com") == "https://arbcentrix.com/"


class FakeResp:
    def __init__(self, status, location=None):
        self.status_code, self.text = status, ""
        self.headers = {"location": location} if location else {}
        self.is_redirect = location is not None


class FakeSession:
    def __init__(self, responses):
        self.headers, self.responses, self.calls = {}, list(responses), []

    def get(self, url, **kw):
        assert kw["allow_redirects"] is False
        self.calls.append(url)
        return self.responses.pop(0)


def test_fetcher_polite_and_records_redirects():
    t = [0.0]
    sleeps = []
    sess = FakeSession([FakeResp(301, "/b"), FakeResp(200), FakeResp(200)])
    f = Fetcher(session=sess, interval=1.0, sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)),
                clock=lambda: t[0])
    r = f.get("https://arbcentrix.com/a")
    assert r.status == 200 and r.redirects == [[301, "https://arbcentrix.com/b"]]
    assert r.final_url == "https://arbcentrix.com/b"
    f.get("https://arbcentrix.com/c")
    assert sess.headers["User-Agent"] == "ArbcentrixSEOAgent/1.0"
    assert sleeps == [1.0, 1.0]  # every request after the first waits the full interval
