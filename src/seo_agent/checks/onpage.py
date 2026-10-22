"""On-page checks: titles, meta, headings, content, tracking, CTAs."""
from collections import Counter

from .technical import _content_pages


def title_meta_regression(snapshot, ctx):
    brand = {b.strip().lower() for b in ctx.threshold.get("brand_only", [])}
    for url, p in snapshot["pages"].items():
        if p.get("status") != 200 or "title" not in p or not ctx.in_scope(url):
            continue
        bad = {}
        for field in ("title", "meta_description"):
            value = (p.get(field) or "").strip()
            if not value:
                bad[field] = "empty"
            elif value.lower() in brand:
                bad[field] = f"brand-only: {value!r}"
        if bad:
            yield url, bad


def tracking_missing(snapshot, ctx):
    tracking = ctx.cfg.site.get("tracking") or {}
    gtm = tracking.get("gtm_container_id")
    forbidden = set(tracking.get("forbidden_ids") or [])
    for url, p in snapshot["pages"].items():
        if p.get("status") != 200 or "tracking_ids" not in p:
            continue
        ids = set(p["tracking_ids"])
        if forbidden & ids:
            yield url, {"forbidden_ids_found": sorted(forbidden & ids)}
        elif gtm and url in ctx.cfg.critical_urls and gtm not in ids:
            yield url, {"expected": gtm, "found": sorted(ids)}


def cta_broken(snapshot, ctx):
    cta = ctx.cfg.site.get("cta") or {}
    base = ctx.cfg.base_url
    for kind, key, field in (("booking link", "booking_pages", "has_booking"),
                             ("form", "form_pages", "has_form")):
        for path in cta.get(key) or []:
            url = base + path
            p = snapshot["pages"].get(url)
            # A page that is down or redirecting is status_change's alert, not a second one here.
            if p and p.get("status") == 200 and not p.get("redirects") and not p.get(field):
                yield url, {"missing": kind}


def h1_missing(snapshot, ctx):
    for url, p in _content_pages(snapshot):
        if not p.get("h1") and ctx.in_scope(url):
            yield url, {"h1": []}


def h1_multiple(snapshot, ctx):
    limit = ctx.threshold.get("max_distinct", 1)
    for url, p in _content_pages(snapshot):
        if len(p.get("h1") or []) > limit and ctx.in_scope(url):
            yield url, {"h1_count": len(p["h1"]), "h1": p["h1"][:5]}


def thin_content(snapshot, ctx):
    minimum = ctx.threshold.get("min_words", 300)
    for url, p in _content_pages(snapshot):
        if p.get("word_count", 0) < minimum and ctx.in_scope(url):
            yield url, {"word_count": p["word_count"], "min_words": minimum}


def jsonld_missing(snapshot, ctx):
    for url, p in _content_pages(snapshot):
        if "jsonld_types" in p and not p["jsonld_types"] and ctx.in_scope(url):
            yield url, {"jsonld_types": []}


def overlapping_content(snapshot, ctx):
    """Pages whose own text (site-wide boilerplate removed) largely repeats another page's."""
    t = ctx.threshold
    max_pages, min_shared, min_unique = t.get("boilerplate_max_pages", 3), t.get("min_overlap", 0.5), t.get("min_unique", 20)
    pages = {u: set(p["text_sample"]) for u, p in _content_pages(snapshot) if p.get("text_sample")}
    df = Counter(h for s in pages.values() for h in s)
    own = {u: {h for h in s if df[h] <= max_pages} for u, s in pages.items()}
    own = {u: s for u, s in own.items() if len(s) >= min_unique and ctx.in_scope(u)}
    urls = sorted(own)
    partners = {}
    for i, a in enumerate(urls):
        for b in urls[i + 1:]:
            share = len(own[a] & own[b]) / min(len(own[a]), len(own[b]))
            if share >= min_shared:
                partners.setdefault(a, {})[b] = round(share, 2)
                partners.setdefault(b, {})[a] = round(share, 2)
    for url, others in partners.items():
        yield url, {"overlaps_with": dict(sorted(others.items())), "min_overlap": min_shared}


def content_shrink(snapshot, ctx):
    """Comparative: words now vs. before an open alert, else the previous snapshot."""
    frac = ctx.threshold.get("min_fraction_lost", 0.4)
    for url in ctx.cfg.critical_urls:
        old = (ctx.baseline or {}).get(url, {}).get("words_before")
        if old is None and ctx.previous:
            old = (ctx.previous["pages"].get(url) or {}).get("word_count")
        new = (snapshot["pages"].get(url) or {}).get("word_count") or 0
        if old and (old - new) / old >= frac:
            yield url, {"words_before": old, "words_now": new,
                        "fraction_lost": round((old - new) / old, 3)}


CHECKS = {f.__name__: f for f in (title_meta_regression, tracking_missing, cta_broken, h1_missing,
                                  h1_multiple, thin_content, jsonld_missing,
                                  overlapping_content, content_shrink)}
