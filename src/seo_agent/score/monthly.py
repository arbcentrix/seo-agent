"""Assemble out/monthly.json: 3- and 12-month trend data for the strategy write-up and briefs.

Months are calendar months. Only complete months (every day synced) are used, so the
report ends on the last complete month of final GSC data. GSC keeps 16 months, so a
12-month vs previous-12-month comparison is not possible; the 12-month view is a month
series with a least-squares trend instead.
"""
import calendar
import datetime as dt
import json
from collections import Counter, defaultdict

from ..sources.daystore import DayStore
from . import aggregate as agg
from .weekly import ORGANIC, _candidate, is_branded, runs_summary

TOP_PAGES = 20
TOP_QUERIES = 50


def month_range(month: str) -> list[str]:
    y, m = map(int, month.split("-"))
    return [f"{month}-01", f"{month}-{calendar.monthrange(y, m)[1]:02d}"]


def shift(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    y, m = divmod(y * 12 + m - 1 + n, 12)
    return f"{y:04d}-{m + 1:02d}"


def complete_months(synced: set[str]) -> list[str]:
    per_month = Counter(d[:7] for d in synced)
    return sorted(m for m, n in per_month.items() if n == calendar.monthrange(*map(int, m.split("-")))[1])


def span(months: list[str]) -> list[str]:
    return [month_range(months[0])[0], month_range(months[-1])[1]]


def slope(values: list[float]) -> float | None:
    """Least-squares change per month."""
    n = len(values)
    if n < 2:
        return None
    mx, my = (n - 1) / 2, sum(values) / n
    den = sum((i - mx) ** 2 for i in range(n))
    return round(sum((i - mx) * (v - my) for i, v in enumerate(values)) / den, 3)


def _site_month(page, landing, events, month):
    r = month_range(month)
    g = agg.gsc_metrics(page.rows(*r))
    o = agg.ga4_metrics(landing.rows(*r), events.rows(*r), ORGANIC)
    return {"month": month, **g, "organic_sessions": o["sessions"], "organic_key_events": o["key_events"]}


def _compare(page, landing, events, now_months, before_months):
    if not before_months:
        return None
    now_r, before_r = span(now_months), span(before_months)
    g = agg.delta(agg.gsc_metrics(page.rows(*now_r)), agg.gsc_metrics(page.rows(*before_r)))
    o_now = agg.ga4_metrics(landing.rows(*now_r), events.rows(*now_r), ORGANIC)
    o_before = agg.ga4_metrics(landing.rows(*before_r), events.rows(*before_r), ORGANIC)
    return {"now": now_r, "before": before_r, "gsc": g,
            "ga4_organic": agg.delta({k: o_now[k] for k in ("sessions", "engaged_sessions", "key_events")},
                                     {k: o_before[k] for k in ("sessions", "engaged_sessions", "key_events")})}


def _psi_trend(store, since: str) -> dict:
    files = sorted(n for n in store.listdir("psi") if n.endswith(".json") and n[:10] >= since)
    if not files:
        return {"runs": 0}
    first, last = store.read_json(f"psi/{files[0]}") or [], store.read_json(f"psi/{files[-1]}") or []
    key = lambda r: (r["url"], r["strategy"])  # noqa: E731
    before = {key(r): r for r in first if "error" not in r}
    out = {}
    for r in last:
        if "error" in r:
            continue
        b = before.get(key(r))
        out[f"{r['url']}|{r['strategy']}"] = {
            "performance": {"first": b["lab"]["performance"] if b else None, "latest": r["lab"]["performance"]},
            "lcp_ms": {"first": b["lab"]["lcp_ms"] if b else None, "latest": r["lab"]["lcp_ms"]},
            "field": r.get("field")}
    return {"runs": len(files), "first": files[0][:10], "latest": files[-1][:10], "pages": out}


def _history(store, since: str) -> dict:
    runs = [r for r in (store.read_runs() if hasattr(store, "read_runs") else []) if r.get("ts", "")[:10] >= since]
    per_month = defaultdict(lambda: {"runs": 0, "errors": 0, "alerts_new": 0})
    for r in runs:
        m = per_month[r["ts"][:7]]
        m["runs"] += 1
        m["errors"] += r.get("status") == "error"
        m["alerts_new"] += r.get("alerts_new") or 0
    resolved = Counter()
    for line in (store.read_text("alerts/resolved.jsonl") or "").splitlines():
        if line.strip():
            a = json.loads(line)
            if a.get("resolved_at", "")[:10] >= since:
                resolved[a["resolved_at"][:7]] += 1
    for month, n in resolved.items():
        per_month[month]["alerts_resolved"] = n
    return {"by_month": dict(sorted(per_month.items())),
            "open_alerts": len(store.read_json("alerts/open.json") or {})}


def _candidates(cfg, qp_now, qp_before, pages_now, pages_before):
    rules = {c["id"]: c.get("threshold") or {} for c in cfg.rules.get("monthly_candidates") or []}
    out = []
    by_query = defaultdict(list)
    for r in qp_now:
        by_query[r["query"]].append(r)
    before_impr = Counter()
    for r in qp_before:
        before_impr[r["query"]] += r["impressions"]
    if "content_gap" in rules:
        t = rules["content_gap"]
        for q, rs in sorted(by_query.items()):
            impr = sum(r["impressions"] for r in rs)
            best = min(rs, key=lambda r: r["position"])
            if not rs[0]["branded"] and impr >= t["min_impressions"] and best["position"] >= t["min_best_position"]:
                out.append(_candidate("content_gap", q, f"'{q}' ranks {best['position']} at best", [r["page"] for r in rs],
                                      [q], {"impressions": impr, "best_position": best["position"],
                                            "best_page": best["page"]}))
    if "rising_query" in rules:
        t = rules["rising_query"]
        for q, rs in sorted(by_query.items()):
            impr, before = sum(r["impressions"] for r in rs), before_impr[q]
            grew = before == 0 or impr / before >= t["min_growth"]
            if not rs[0]["branded"] and impr >= t["min_impressions"] and grew:
                out.append(_candidate("rising_query", q, f"'{q}' impressions {before} -> {impr}", [r["page"] for r in rs],
                                      [q], {"impressions": impr, "impressions_before": before,
                                            "growth": agg.ratio(impr, before, 2) if before else None}))
    if "declining_page" in rules:
        t = rules["declining_page"]
        for url, b in sorted(pages_before.items()):
            n = pages_now.get(url, {"clicks": 0, "impressions": 0})
            if b["clicks"] >= t["min_clicks_before"] and (b["clicks"] - n["clicks"]) / b["clicks"] >= t["min_fraction_lost"]:
                out.append(_candidate("declining_page", url, f"{url} lost clicks over 3 months", [url], [],
                                      {"clicks_before": b["clicks"], "clicks_now": n["clicks"],
                                       "fraction_lost": agg.ratio(b["clicks"] - n["clicks"], b["clicks"])}))
    if "critical_page_flat" in rules:
        for url in cfg.critical_urls:
            n = pages_now.get(url, {"clicks": 0, "impressions": 0})
            if not n["clicks"]:
                out.append(_candidate("critical_page_flat", url, f"{url} had no clicks in 3 months", [url], [],
                                      {"clicks": 0, "impressions": n["impressions"]}))
    return out


def build_monthly(cfg, store, generated_at: str) -> dict:
    page, pq = DayStore(store, "gsc/page"), DayStore(store, "gsc/page_query")
    landing, events = DayStore(store, "ga4/landing"), DayStore(store, "ga4/key_events")
    months = complete_months(page.synced_days())
    if len(months) < 3:
        raise RuntimeError(f"need 3 complete months of GSC data, have {len(months)}: run sync first")
    last = months[-1]
    last3 = [shift(last, -i) for i in (2, 1, 0)]
    prev3 = [m for m in (shift(last, -i) for i in (5, 4, 3)) if m in months]
    ly3 = [m for m in (shift(m, -12) for m in last3) if m in months]
    series = [m for m in (shift(last, -i) for i in range(12, -1, -1)) if m in months]  # up to 13 months

    site = [_site_month(page, landing, events, m) for m in series]
    twelve = site[-12:]
    brand = cfg.site.get("brand_terms") or []

    def per_page(months_):
        if not months_:
            return {}
        return {url: agg.gsc_metrics(rs) for url, rs in agg.group(page.rows(*span(months_)), "page").items()}

    def query_page(months_):
        if not months_:
            return []
        rows = [{"query": q, "page": p, **agg.gsc_metrics(rs), "branded": is_branded(q, brand), "intent": None}
                for (q, p), rs in agg.group(pq.rows(*span(months_)), "query", "page").items()]
        return sorted(rows, key=lambda r: (-r["impressions"], r["query"], r["page"]))

    pages_now, pages_before = per_page(last3), per_page(prev3)
    qp_now, qp_before = query_page(last3), query_page(prev3)
    top_pages = sorted(per_page(series[-12:]).items(), key=lambda kv: (-kv[1]["impressions"], kv[0]))[:TOP_PAGES]
    page_series = {url: [{"month": m, **agg.gsc_metrics([r for r in page.rows(*month_range(m))
                                                         if agg.normalize_url(r["page"]) == url])} for m in series[-12:]]
                   for url, _ in top_pages}
    q_now, q_before = defaultdict(list), defaultdict(list)
    for r in qp_now:
        q_now[r["query"]].append(r)
    for r in qp_before:
        q_before[r["query"]].append(r)
    queries = []
    for q in set(q_now) | set(q_before):
        n, b = agg.gsc_metrics(q_now.get(q, [])), agg.gsc_metrics(q_before.get(q, []))
        queries.append({"query": q, "branded": is_branded(q, brand), "now": n, "before": b,
                        "impressions_change": n["impressions"] - b["impressions"]})
    queries.sort(key=lambda r: (-r["now"]["impressions"], r["query"]))

    return {
        "generated_at": generated_at,
        "months": {"last_complete": last, "last_3": last3, "previous_3": prev3, "last_3_last_year": ly3,
                   "series": series, "available": months},
        "trend_3m": {"vs_previous_3": _compare(page, landing, events, last3, prev3),
                     "vs_last_year": _compare(page, landing, events, last3, ly3) if len(ly3) == 3 else None},
        "trend_12m": {"series": site, "months": len(twelve),
                      "slope_per_month": {k: slope([s[k] or 0 for s in twelve])
                                          for k in ("clicks", "impressions", "position", "organic_sessions")},
                      "totals": agg.gsc_metrics(page.rows(*span([s["month"] for s in twelve]))),
                      "previous_12": "not available: GSC keeps 16 months"},
        "pages": {"three_month": [{"page": u, "now": pages_now.get(u, agg.gsc_metrics([])),
                                   "before": pages_before.get(u, agg.gsc_metrics([]))}
                                  for u in sorted(set(pages_now) | set(pages_before))],
                  "top_12m_series": page_series},
        "queries": {"three_month": queries[:TOP_QUERIES],
                    "non_branded_three_month": [q for q in queries if not q["branded"]][:TOP_QUERIES]},
        "query_page": qp_now,
        "psi": _psi_trend(store, month_range(last3[0])[0]),
        "history": _history(store, month_range(last3[0])[0]),
        "runs": runs_summary(cfg, store, generated_at, days=30),
        "candidates": _candidates(cfg, qp_now, qp_before, pages_now, pages_before),
    }
