"""Crawl orchestration: robots.txt, sitemap, sitemap pages, then discovered internal links."""
from datetime import datetime, timezone

from .linkgraph import build_graph
from .parser import normalize_url, parse_page
from .robots import parse_robots
from .sitemap import SitemapError, parse_sitemap

DEFAULT_MAX_PAGES = 300


def _sitemap(source, url):
    r = source.get(url)
    if r.status != 200:
        return {"url": url, "status": r.status, "error": r.error or f"HTTP {r.status}", "urls": []}
    try:
        kind, locs = parse_sitemap(r.text)
        if kind == "sitemapindex":
            urls = []
            for child in locs:
                urls += _sitemap(source, child)["urls"]
            locs = urls
    except SitemapError as e:
        return {"url": url, "status": r.status, "error": str(e), "urls": []}
    return {"url": url, "status": r.status, "error": None, "urls": [normalize_url(u) for u in locs]}


def crawl(source, cfg, max_pages: int = DEFAULT_MAX_PAGES, now: str | None = None) -> dict:
    site = cfg.site
    base = cfg.base_url
    booking = (site.get("cta") or {}).get("booking_link")

    r = source.get(base + "/robots.txt")
    robots = {"status": r.status, "error": r.error,
              "text": r.text if r.status == 200 else None,
              "rules": parse_robots(r.text, cfg.user_agent) if r.status == 200 else None}
    sitemap = _sitemap(source, site.get("sitemap_url") or base + "/sitemap.xml")

    queue = list(dict.fromkeys(sitemap["urls"] + cfg.critical_urls))
    seen, pages = set(queue), {}
    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        resp = source.get(url)
        page = {"status": resp.status, "redirects": resp.redirects, "final_url": resp.final_url,
                "error": resp.error, "in_sitemap": url in sitemap["urls"]}
        if resp.status == 200 and "html" in resp.headers.get("content-type", "text/html"):
            parsed = parse_page(resp.text, url, resp.headers, domain=site["domain"],
                                booking_pattern=booking)
            if parsed["empty_shell"]:
                # Framer renders on demand: the first hit after a publish can be an empty
                # shell. Re-fetch once; only a shell that persists is reported.
                retry = source.get(url)
                if retry.status == 200:
                    reparsed = parse_page(retry.text, url, retry.headers, domain=site["domain"],
                                          booking_pattern=booking)
                    page["cold_render"] = not reparsed["empty_shell"]
                    parsed = reparsed
            page.update(parsed)
            for link in page["internal_links"]:
                if link not in seen:
                    seen.add(link)
                    queue.append(link)
        page["url"] = url
        pages[url] = page

    return {
        "fetched_at": now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_url": base,
        "robots": robots,
        "sitemap": sitemap,
        "pages": pages,
        "graph": build_graph({u: p for u, p in pages.items() if "internal_links" in p}),
        "truncated": bool(queue),
    }
