"""Page fingerprints and the site build signature, so watch can tell what changed.

A page fingerprint hashes the SEO-relevant fields; the per-field hashes say which ones
changed. The build signature hashes the homepage's own Framer bundle URLs, whose names
carry a content hash, so it changes on every publish.
"""
import hashlib
import json

FIELDS = ("status", "redirects", "title", "meta_description", "canonicals", "robots_meta",
          "x_robots_tag", "h1", "h2", "internal_links", "has_booking", "has_form", "tracking_ids",
          "word_count")


def _h(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def fingerprint(page: dict) -> dict:
    fields = {f: _h(page.get(f)) for f in FIELDS}
    return {"hash": _h(fields), "fields": fields}


def changed_fields(old: dict | None, new: dict | None) -> list[str]:
    if not old or not new:
        return []
    return sorted(f for f in FIELDS if old["fields"].get(f) != new["fields"].get(f))


def build_signature(homepage: dict | None) -> str | None:
    assets = (homepage or {}).get("build_assets")
    return _h(assets) if assets else None
