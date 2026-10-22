"""Compare a Screaming Frog crawl with the agent's snapshot.

Input is SF's Internal > HTML export (internal_all.csv). Per field (status code, title,
canonical, inlinks) the agreement is matching URLs / all URLs either crawler saw; a URL
only one side found counts as a mismatch. Passes when every field is at or above the
threshold (default 95%).
"""
import csv
import re
from pathlib import Path

from .crawl.parser import normalize_url

FIELDS = ("status", "title", "canonical", "inlinks")
THRESHOLD = 0.95
_SPACE = re.compile(r"\s+")


def _clean(s) -> str:
    return _SPACE.sub(" ", s or "").strip()


def _norm(u) -> str:
    u = _clean(u)
    return normalize_url(u) if u else ""


def _int(s):
    try:
        return int(_clean(s))
    except ValueError:
        return None


def read_sf(path: Path) -> dict:
    """{normalised url: {status, title, canonical, inlinks}} from an SF internal export."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if rows and not {"Address", "Status Code"} <= set(rows[0]):
        raise ValueError("not a Screaming Frog internal export: needs Address and Status Code columns")
    out = {}
    for r in rows:
        if "Content Type" in r and "html" not in (r["Content Type"] or "").lower() and _int(r["Status Code"]) == 200:
            continue  # images, CSS, JS
        out[_norm(r["Address"])] = {
            "status": _int(r["Status Code"]),
            "title": _clean(r.get("Title 1")),
            "canonical": _norm(r.get("Canonical Link Element 1")),
            "inlinks": _int(r.get("Unique Inlinks") or r.get("Inlinks")),
        }
    return out


def ours(snapshot: dict) -> dict:
    """The same fields from our snapshot. SF reports a redirect's own status, not the final
    one, and counts a self-link as an inlink; we match both."""
    inlinks = snapshot["graph"]["inlinks"]
    out = {}
    for url, p in snapshot["pages"].items():
        redirects = p.get("redirects") or []
        out[url] = {
            "status": redirects[0][0] if redirects else p.get("status"),
            "title": _clean(p.get("title")),
            "canonical": _norm((p.get("canonicals") or [""])[0]),
            "inlinks": len(inlinks.get(url, [])) + (url in (p.get("internal_links") or [])),
        }
    return out


def compare(sf: dict, mine: dict, threshold: float = THRESHOLD) -> dict:
    urls = sorted(set(sf) | set(mine))
    fields, mismatches = {}, []
    for f in FIELDS:
        bad = []
        for u in urls:
            if u in sf and u in mine:
                if sf[u][f] != mine[u][f]:
                    bad.append({"url": u, "field": f, "agent": mine[u][f], "screaming_frog": sf[u][f]})
            else:
                bad.append({"url": u, "field": f, "agent": mine[u][f] if u in mine else "(not crawled)",
                            "screaming_frog": sf[u][f] if u in sf else "(not crawled)"})
        fields[f] = {"agree": len(urls) - len(bad), "total": len(urls),
                     "rate": (len(urls) - len(bad)) / len(urls) if urls else 0.0}
        mismatches += bad
    return {"urls": len(urls), "threshold": threshold, "fields": fields,
            "pass": bool(urls) and all(v["rate"] >= threshold for v in fields.values()),
            "mismatches": mismatches}


def report(result: dict) -> str:
    lines = [f"{result['urls']} URLs compared, threshold {result['threshold']:.0%}"]
    for f, v in result["fields"].items():
        lines.append(f"  {f:<10} {v['agree']}/{v['total']}  {v['rate']:.1%}"
                     f"{'' if v['rate'] >= result['threshold'] else '  FAIL'}")
    lines += [f"  {m['field']} {m['url']}: agent={m['agent']!r} sf={m['screaming_frog']!r}"
              for m in result["mismatches"]]
    lines.append("PASS" if result["pass"] else "FAIL")
    return "\n".join(lines)
