"""Internal link graph built from parsed pages."""
from collections import defaultdict


def build_graph(pages: dict) -> dict:
    """pages: {url: parsed_page}. Returns {"outlinks": {...}, "inlinks": {...}} (self-links excluded)."""
    outlinks, inlinks = {}, defaultdict(list)
    for url, page in pages.items():
        targets = [t for t in page.get("internal_links", []) if t != url]
        outlinks[url] = targets
        for t in targets:
            inlinks[t].append(url)
    return {"outlinks": outlinks, "inlinks": {k: sorted(v) for k, v in inlinks.items()}}
