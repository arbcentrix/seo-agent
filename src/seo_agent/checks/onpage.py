"""On-page checks: titles, meta, headings, content, tracking, CTAs."""
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
            if p is None or p.get("status") != 200 or not p.get(field):
                yield url, {"missing": kind, "status": p.get("status") if p else None}


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


def content_shrink(snapshot, ctx):
    """Diff-only: needs the previous snapshot (wired in stage 3)."""
    if not ctx.previous:
        return
    frac = ctx.threshold.get("min_fraction_lost", 0.4)
    for url in ctx.cfg.critical_urls:
        old = (ctx.previous["pages"].get(url) or {}).get("word_count") or 0
        new = (snapshot["pages"].get(url) or {}).get("word_count") or 0
        if old and (old - new) / old >= frac:
            yield url, {"words_before": old, "words_now": new}


CHECKS = {f.__name__: f for f in (title_meta_regression, tracking_missing, cta_broken, h1_missing,
                                  h1_multiple, thin_content, content_shrink)}
