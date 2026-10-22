"""Technical checks: status, robots, indexability, canonicals, sitemap, links."""
from ..crawl.robots import is_blocked


def _content_pages(snapshot):
    """200 pages that were parsed and have server-rendered content."""
    for url, p in snapshot["pages"].items():
        if p.get("status") == 200 and "title" in p and not p.get("empty_shell"):
            yield url, p


def status_change(snapshot, ctx):
    for url in ctx.cfg.critical_urls:
        p = snapshot["pages"].get(url)
        if p is None:
            yield url, {"status": None, "note": "not crawled"}
        elif p.get("redirects"):
            yield url, {"status": p["redirects"][0][0], "redirects": p["redirects"]}
        elif p.get("status") != 200:
            yield url, {"status": p.get("status"), "error": p.get("error")}


def robots_block(snapshot, ctx):
    robots = snapshot["robots"]
    if robots.get("status") != 200:
        yield ctx.cfg.base_url + "/robots.txt", {"status": robots.get("status"), "error": robots.get("error")}
        return
    for url in ctx.cfg.critical_urls:
        if is_blocked(robots["rules"], url):
            yield url, {"disallow": robots["rules"]["disallow"]}


def noindex_added(snapshot, ctx):
    for url, p in snapshot["pages"].items():
        if p.get("noindex") and ctx.in_scope(url):
            yield url, {"robots_meta": p.get("robots_meta"), "x_robots_tag": p.get("x_robots_tag")}


def canonical_changed(snapshot, ctx):
    for url, p in _content_pages(snapshot):
        canon = p.get("canonicals") or []
        if canon and canon != [url] and ctx.in_scope(url):
            yield url, {"canonicals": canon}


def canonical_missing(snapshot, ctx):
    for url, p in _content_pages(snapshot):
        if not p.get("canonicals") and ctx.in_scope(url):
            yield url, {"canonicals": []}


def sitemap_drop(snapshot, ctx):
    sm = snapshot["sitemap"]
    if sm.get("error") or not sm.get("urls"):
        yield sm["url"], {"status": sm.get("status"), "error": sm.get("error") or "no URLs"}


def broken_internal_link(snapshot, ctx):
    pages, inlinks = snapshot["pages"], snapshot["graph"]["inlinks"]
    for url, p in pages.items():
        status = p.get("status")
        if (status is None or status >= 400) and inlinks.get(url):
            yield url, {"status": status, "error": p.get("error"), "linked_from": inlinks[url]}


def orphan_page(snapshot, ctx):
    inlinks = snapshot["graph"]["inlinks"]
    for url in snapshot["sitemap"]["urls"]:
        if url != ctx.cfg.base_url + "/" and not inlinks.get(url):
            yield url, {"inlinks": 0}


def not_in_sitemap(snapshot, ctx):
    sm = set(snapshot["sitemap"]["urls"])
    inlinks = snapshot["graph"]["inlinks"]
    for url, p in snapshot["pages"].items():
        if p.get("status") == 200 and url not in sm and inlinks.get(url) and not p.get("noindex"):
            yield url, {"linked_from_count": len(inlinks[url])}


def empty_shell(snapshot, ctx):
    for url, p in snapshot["pages"].items():
        if p.get("status") == 200 and p.get("empty_shell") and ctx.in_scope(url):
            yield url, {"word_count": p.get("word_count"), "title": p.get("title"),
                        "in_sitemap": p.get("in_sitemap")}


CHECKS = {f.__name__: f for f in (status_change, robots_block, noindex_added, canonical_changed,
                                  canonical_missing, sitemap_drop, broken_internal_link, orphan_page,
                                  not_in_sitemap, empty_shell)}
