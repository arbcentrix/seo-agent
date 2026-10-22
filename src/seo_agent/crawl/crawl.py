"""Crawl orchestration: robots.txt, sitemap, sitemap pages, then discovered internal links."""
from datetime import datetime, timezone

from .fingerprint import build_signature, changed_fields, fingerprint
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


def _opts(cfg) -> dict:
    site = cfg.site
    return {"domain": site["domain"],
            "booking_pattern": (site.get("cta") or {}).get("booking_link"),
            "build_asset_pattern": (site.get("watch") or {}).get("build_asset_pattern")}


def _clean(page: dict) -> dict:
    """A previous page without this-run markers (and without its carried-forward last_ok)."""
    return {k: v for k, v in page.items() if k not in ("carried", "not_modified", "last_ok")}


def fetch_page(source, url: str, opts: dict, in_sitemap: bool, previous: dict | None = None) -> dict:
    """Fetch and parse one page. With `previous` (its last parse), ask conditionally and
    reuse that parse on a 304."""
    headers = {"If-None-Match": previous["etag"]} if previous and previous.get("etag") else None
    resp = source.get(url, headers) if headers else source.get(url)
    if resp.status == 304 and previous:
        return {**_clean(previous), "in_sitemap": in_sitemap, "not_modified": True}
    page = {"status": resp.status, "redirects": resp.redirects, "final_url": resp.final_url,
            "error": resp.error, "in_sitemap": in_sitemap,
            "etag": resp.headers.get("etag"), "last_modified": resp.headers.get("last-modified")}
    if resp.status == 200 and "html" in resp.headers.get("content-type", "text/html"):
        parsed = parse_page(resp.text, url, resp.headers, **opts)
        if parsed["empty_shell"]:
            # Framer renders on demand: the first hit after a publish can be an empty
            # shell. Re-fetch once; only a shell that persists is reported.
            retry = source.get(url)
            if retry.status == 200:
                reparsed = parse_page(retry.text, url, retry.headers, **opts)
                page["cold_render"] = not reparsed["empty_shell"]
                page["etag"] = retry.headers.get("etag")
                parsed = reparsed
        page.update(parsed)
    page["url"] = url
    page["fingerprint"] = fingerprint(page)
    return page


def _robots(source, cfg):
    r = source.get(cfg.base_url + "/robots.txt")
    return {"status": r.status, "error": r.error,
            "text": r.text if r.status == 200 else None,
            "rules": parse_robots(r.text, cfg.user_agent) if r.status == 200 else None}


def _snapshot(cfg, robots, sitemap, pages, truncated, now, extra=None):
    home = pages.get(cfg.base_url + "/")
    return {
        "fetched_at": now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_url": cfg.base_url,
        "robots": robots,
        "sitemap": sitemap,
        "pages": pages,
        "graph": build_graph({u: p for u, p in pages.items() if "internal_links" in p}),
        "build_signature": build_signature(home),
        "truncated": truncated,
        **(extra or {}),
    }


def _crawl_queue(source, opts, queue, sitemap_urls, pages, max_pages, previous_pages=None):
    """Breadth-first over queue, following internal links. Fills `pages`; returns leftovers."""
    seen = set(queue) | set(pages)
    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        prev = (previous_pages or {}).get(url)
        page = fetch_page(source, url, opts, url in sitemap_urls, prev)
        pages[url] = page
        for link in page.get("internal_links") or []:
            if link not in seen:
                seen.add(link)
                queue.append(link)
    return queue


def crawl(source, cfg, max_pages: int = DEFAULT_MAX_PAGES, now: str | None = None) -> dict:
    """Full crawl: robots.txt, sitemap, every sitemap URL and critical page, then internal links."""
    opts = _opts(cfg)
    robots = _robots(source, cfg)
    sitemap = _sitemap(source, cfg.site.get("sitemap_url") or cfg.base_url + "/sitemap.xml")
    pages = {}
    left = _crawl_queue(source, opts, list(dict.fromkeys(sitemap["urls"] + cfg.critical_urls)),
                        set(sitemap["urls"]), pages, max_pages)
    return _snapshot(cfg, robots, sitemap, pages, bool(left), now)


def crawl_watch(source, cfg, previous: dict | None, full: bool = False,
                max_pages: int = DEFAULT_MAX_PAGES, now: str | None = None) -> dict:
    """Incremental crawl for the publish watch.

    Always: robots.txt, the sitemap, and the critical pages (homepage included), asked
    conditionally so unchanged pages cost a 304. Everything else is re-fetched only when
    something was published: the homepage build signature, the sitemap's URL set or
    robots.txt changed (or full=True). Otherwise the other pages are carried over from
    the previous snapshot unchanged. snapshot["watch"] says what was fetched and changed.
    """
    if not previous:
        snap = crawl(source, cfg, max_pages, now)
        snap["watch"] = {"mode": "full", "reason": "no previous snapshot"}
        return snap
    opts = _opts(cfg)
    prev_pages = previous["pages"]
    robots = _robots(source, cfg)
    sitemap = _sitemap(source, cfg.site.get("sitemap_url") or cfg.base_url + "/sitemap.xml")
    sm_urls = set(sitemap["urls"])

    pages = {}
    home = cfg.base_url + "/"
    for url in dict.fromkeys([home] + cfg.critical_urls):
        pages[url] = fetch_page(source, url, opts, url in sm_urls, prev_pages.get(url))

    build_now = build_signature(pages.get(home))
    reasons = []
    if full:
        reasons.append("full run requested")
    if build_now != previous.get("build_signature"):
        reasons.append("build signature changed")
    if sm_urls != set(previous["sitemap"].get("urls") or []):
        reasons.append("sitemap changed")
    if robots.get("text") != previous["robots"].get("text") or robots.get("status") != previous["robots"].get("status"):
        reasons.append("robots.txt changed")

    left = []
    if reasons:
        queue = [u for u in dict.fromkeys(sitemap["urls"] + [u for u, p in prev_pages.items()
                                                              if p.get("status") == 200])
                 if u not in pages]
        # Pages already fetched above still contribute their links.
        for p in list(pages.values()):
            for link in p.get("internal_links") or []:
                if link not in pages and link not in queue:
                    queue.append(link)
        left = _crawl_queue(source, opts, queue, sm_urls, pages, max_pages, prev_pages)
        carried = []
    else:
        carried = [u for u in prev_pages if u not in pages]
        for url in carried:
            pages[url] = {**_clean(prev_pages[url]), "carried": True}

    changed, new = {}, []
    for url, page in pages.items():
        if page.get("carried"):
            continue
        old = prev_pages.get(url)
        if old is None:
            new.append(url)
        elif not page.get("not_modified"):
            fields = changed_fields(old.get("fingerprint"), page.get("fingerprint"))
            if fields:
                changed[url] = fields
    fetched = [u for u, p in pages.items() if not p.get("carried")]
    watch = {"mode": "publish" if reasons else "quiet", "reasons": reasons,
             "build_signature_before": previous.get("build_signature"), "build_signature": build_now,
             "fetched": len(fetched), "not_modified": sum(1 for u in fetched if pages[u].get("not_modified")),
             "carried": len(carried), "changed": dict(sorted(changed.items())), "new_pages": sorted(new),
             "removed_pages": sorted(u for u in prev_pages if u not in pages)}
    return _snapshot(cfg, robots, sitemap, pages, bool(left), now, {"watch": watch})
