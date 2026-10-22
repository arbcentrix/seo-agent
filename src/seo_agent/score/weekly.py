"""Assemble out/weekly.json: KPIs, changes, candidate issues and query <-> page data.

Claude fills in `intent` on query_page rows and `scores` on candidates (labels file);
apply_labels then computes Priority and ranks the candidates.
"""
import datetime as dt
from collections import defaultdict

from ..crawl.parser import normalize_url
from ..sources.daystore import DayStore
from . import aggregate as agg
from .priority import priority

ORGANIC = "Organic Search"
TOP_N = 10


def _gsc_end(store) -> dt.date | None:
    days = DayStore(store, "gsc/page").synced_days()
    return dt.date.fromisoformat(max(days)) if days else None


def _windows(end: dt.date) -> dict:
    return {"7d": agg.window(end, 7), "prev_7d": agg.window(end, 7, 7),
            "28d": agg.window(end, 28), "prev_28d": agg.window(end, 28, 28),
            "28d_last_year": agg.window(end, 28, 364)}


def _kpis(store, w) -> dict:
    page, landing, events = DayStore(store, "gsc/page"), DayStore(store, "ga4/landing"), DayStore(store, "ga4/key_events")
    out = {}
    for label, (now_w, before_w) in {"7d_vs_prev": ("7d", "prev_7d"), "28d_vs_prev": ("28d", "prev_28d"),
                                     "28d_vs_last_year": ("28d", "28d_last_year")}.items():
        gsc_now, gsc_before = (agg.gsc_metrics(page.rows(*w[k])) for k in (now_w, before_w))
        ga = {}
        for name, channel in (("organic", ORGANIC), ("all_channels", None)):
            now_m = agg.ga4_metrics(landing.rows(*w[now_w]), events.rows(*w[now_w]), channel)
            before_m = agg.ga4_metrics(landing.rows(*w[before_w]), events.rows(*w[before_w]), channel)
            ga[name] = {"metrics": agg.delta(now_m, before_m),
                        "key_events_by_name": now_m["key_events_by_name"]}
        out[label] = {"gsc": agg.delta(gsc_now, gsc_before), "ga4": ga}
    return out


def _movers(now_groups, before_groups, key_name) -> dict:
    rows = []
    for key in set(now_groups) | set(before_groups):
        now_m, before_m = agg.gsc_metrics(now_groups.get(key, [])), agg.gsc_metrics(before_groups.get(key, []))
        rows.append({key_name: key, "now": now_m, "before": before_m,
                     "clicks_change": now_m["clicks"] - before_m["clicks"],
                     "impressions_change": now_m["impressions"] - before_m["impressions"]})
    by = lambda k: sorted(rows, key=lambda r: (r[k], r[key_name]))  # noqa: E731
    out = {"gainers": [r for r in reversed(by("clicks_change")) if r["clicks_change"] > 0][:TOP_N],
           "losers": [r for r in by("clicks_change") if r["clicks_change"] < 0][:TOP_N]}
    out["new"] = sorted(r[key_name] for r in rows if r["before"]["impressions"] == 0 and r["now"]["impressions"] > 0)
    out["lost"] = sorted(r[key_name] for r in rows if r["now"]["impressions"] == 0 and r["before"]["impressions"] > 0)
    return out


def _changes(store, w) -> dict:
    page, pq = DayStore(store, "gsc/page"), DayStore(store, "gsc/page_query")
    return {"window": {"now": w["28d"], "before": w["prev_28d"]},
            "pages": _movers(agg.group(page.rows(*w["28d"]), "page"), agg.group(page.rows(*w["prev_28d"]), "page"), "page"),
            "queries": _movers(agg.group(pq.rows(*w["28d"]), "query"), agg.group(pq.rows(*w["prev_28d"]), "query"), "query")}


def is_branded(query: str, brand_terms) -> bool:
    q = query.lower()
    return any(t.lower() in q for t in brand_terms)


def _query_page(cfg, store, w) -> list[dict]:
    brand = cfg.site.get("brand_terms") or []
    rows = []
    for (query, page), rs in agg.group(DayStore(store, "gsc/page_query").rows(*w["28d"]), "query", "page").items():
        rows.append({"query": query, "page": page, **agg.gsc_metrics(rs),
                     "branded": is_branded(query, brand), "intent": None})
    return sorted(rows, key=lambda r: (-r["impressions"], r["query"], r["page"]))


def _candidate(type_, key, title, urls, queries, evidence, severity=None):
    return {"id": f"{type_}:{key}", "type": type_, "title": title, "severity": severity,
            "urls": sorted(set(urls)), "queries": sorted(set(queries)), "evidence": evidence,
            "scores": None, "priority": None}


def _data_candidates(cfg, store, w, query_page, psi_rows) -> list[dict]:
    rules = {c["id"]: c.get("threshold") or {} for c in cfg.rules.get("candidates") or []}
    out = []
    if "striking_distance" in rules:
        t = rules["striking_distance"]
        for r in query_page:
            if r["branded"] and not t.get("include_branded", False):
                continue
            if t["min_position"] <= (r["position"] or 0) <= t["max_position"] and r["impressions"] >= t["min_impressions"]:
                out.append(_candidate("striking_distance", f"{r['query']}|{r['page']}",
                                      f"'{r['query']}' ranks {r['position']} for {r['page']}", [r["page"]], [r["query"]],
                                      {k: r[k] for k in ("clicks", "impressions", "ctr", "position")}))
    if "low_ctr" in rules:
        t = rules["low_ctr"]
        expected = {int(k): v for k, v in t["expected_ctr"].items()}
        for r in query_page:
            pos = r["position"] or 99
            if r["branded"] and not t.get("include_branded", False):
                continue
            if pos < t["max_position"] and r["impressions"] >= t["min_impressions"]:
                exp = expected.get(max(1, round(pos)))
                if exp and (r["ctr"] or 0) < exp * t["ratio"]:
                    out.append(_candidate("low_ctr", f"{r['query']}|{r['page']}",
                                          f"Low CTR for '{r['query']}' on {r['page']}", [r["page"]], [r["query"]],
                                          {**{k: r[k] for k in ("clicks", "impressions", "ctr", "position")},
                                           "expected_ctr": exp}))
    page = DayStore(store, "gsc/page")
    now_pages, before_pages = agg.group(page.rows(*w["28d"]), "page"), agg.group(page.rows(*w["prev_28d"]), "page")
    if "page_decline" in rules:
        t = rules["page_decline"]
        for url, rs in before_pages.items():
            b, n = agg.gsc_metrics(rs), agg.gsc_metrics(now_pages.get(url, []))
            if b["clicks"] >= t["min_clicks_before"] and (b["clicks"] - n["clicks"]) / b["clicks"] >= t["min_fraction_lost"]:
                out.append(_candidate("page_decline", url, f"{url} lost clicks", [url], [],
                                      {"clicks_before": b["clicks"], "clicks_now": n["clicks"],
                                       "impressions_before": b["impressions"], "impressions_now": n["impressions"],
                                       "fraction_lost": agg.ratio(b["clicks"] - n["clicks"], b["clicks"])}))
    if "cannibalisation" in rules:
        t = rules["cannibalisation"]
        by_query = defaultdict(list)
        for r in query_page:
            if not r["branded"] or t.get("include_branded", False):
                by_query[r["query"]].append(r)
        for q, rs in by_query.items():
            total = sum(r["impressions"] for r in rs)
            sharing = [r for r in rs if total and r["impressions"] / total >= t["min_share"]]
            if total >= t["min_impressions"] and len(sharing) >= 2:
                out.append(_candidate("cannibalisation", q, f"'{q}' is split across {len(sharing)} pages",
                                      [r["page"] for r in sharing], [q],
                                      {"impressions": total,
                                       "pages": {r["page"]: {"impressions": r["impressions"], "position": r["position"]}
                                                 for r in sharing}}))
    if "critical_no_impressions" in rules:
        for url in cfg.critical_urls:
            if not agg.gsc_metrics(now_pages.get(url, []))["impressions"]:
                out.append(_candidate("critical_no_impressions", url, f"{url} had no search impressions",
                                      [url], [], {"impressions": 0, "window": w["28d"]}))
    if "psi_poor" in rules:
        t = rules["psi_poor"]
        slow = defaultdict(dict)
        for r in psi_rows:
            if "error" in r:
                continue
            field, lab, bad = r.get("field") or {}, r.get("lab") or {}, {}
            if field:
                for key, limit in (("lcp_ms", t["max_lcp_ms"]), ("inp_ms", t["max_inp_ms"]), ("cls", t["max_cls"])):
                    if field.get(key) is not None and field[key] > limit:
                        bad[f"field_{key}"] = field[key]
            else:
                if lab.get("performance") is not None and lab["performance"] < t["min_lab_performance"]:
                    bad["lab_performance"] = lab["performance"]
                if lab.get("lcp_ms") is not None and lab["lcp_ms"] > t["lab_max_lcp_ms"]:
                    bad["lab_lcp_ms"] = lab["lcp_ms"]
                if lab.get("cls") is not None and lab["cls"] > t["lab_max_cls"]:
                    bad["lab_cls"] = lab["cls"]
            if bad:
                slow[r["strategy"]][r["url"]] = {**bad, "lab": lab, "field": field or None}
        for strategy, pages in sorted(slow.items()):
            out.append(_candidate("psi_poor", strategy, f"{len(pages)} critical page(s) slow on {strategy}",
                                  list(pages), [], {"pages": len(pages), "strategy": strategy, "by_page": pages}))
    return out


def _crawl_candidates(findings, open_alerts) -> list[dict]:
    out, by_rule = [], defaultdict(list)
    for f in findings:
        by_rule[f["rule"]].append(f)
    for rule, fs in sorted(by_rule.items()):
        out.append(_candidate(f"crawl_{rule}", rule, f"{rule}: {len(fs)} page(s)", [f["url"] for f in fs], [],
                              {"pages": len(fs), "examples": {f["url"]: f["evidence"] for f in fs[:5]}},
                              fs[0]["severity"]))
    for a in (open_alerts or {}).values():
        out.append(_candidate(f"alert_{a['rule']}", a["url"], f"Open alert {a['rule']} on {a['url']}", [a["url"]], [],
                              {**a["evidence"], "first_seen": a["first_seen"]}, a["severity"]))
    return out


def _psi_latest(store) -> tuple[str | None, list[dict]]:
    days = [n[:10] for n in store.listdir("psi") if n.endswith(".json")]
    if not days:
        return None, []
    day = max(days)
    return day, store.read_json(f"psi/{day}.json") or []


def build_weekly(cfg, store, snapshot, findings, open_alerts, generated_at: str) -> dict:
    end = _gsc_end(store)
    if end is None:
        raise RuntimeError("no GSC data in the store: run `python -m seo_agent sync` first")
    w = _windows(end)
    query_page = _query_page(cfg, store, w)
    psi_day, psi_rows = _psi_latest(store)
    candidates = _crawl_candidates(findings, open_alerts) + _data_candidates(cfg, store, w, query_page, psi_rows)
    return {
        "generated_at": generated_at,
        "windows": w,
        "kpis": _kpis(store, w),
        "site_health": {"pages_crawled": len(snapshot["pages"]) if snapshot else None,
                        "crawled_at": snapshot.get("fetched_at") if snapshot else None,
                        "findings_by_rule": {c["id"].split(":", 1)[1]: c["evidence"]["pages"]
                                             for c in candidates if c["type"].startswith("crawl_")},
                        "open_alerts": len(open_alerts or {})},
        "psi": {"date": psi_day, "results": psi_rows},
        "changes": _changes(store, w),
        "query_page": query_page,
        "candidates": candidates,
        "labels_applied": False,
    }


def apply_labels(weekly: dict, labels: dict) -> dict:
    """Copy Claude's intents and scores in, compute Priority, rank candidates. Labels must pass qa first."""
    intents = labels.get("intents") or {}
    for r in weekly["query_page"]:
        r["intent"] = intents.get(r["query"])
    scores = labels.get("scores") or {}
    for c in weekly["candidates"]:
        s = scores.get(c["id"])
        c["scores"] = {k: s[k] for k in ("impact", "relevance", "confidence", "effort", "risk")} if s else None
        c["rationale"] = (s or {}).get("rationale")
        c["priority"] = priority(c["scores"]) if s else None
    weekly["candidates"].sort(key=lambda c: (c["priority"] is None, -(c["priority"] or 0), c["id"]))
    weekly["labels_applied"] = True
    return weekly


def known_urls(weekly: dict, snapshot: dict | None) -> set[str]:
    urls = set()
    if snapshot:
        urls |= set(snapshot["pages"]) | set(snapshot["sitemap"]["urls"])
    urls |= {r["page"] for r in weekly.get("query_page", [])}
    for c in weekly.get("candidates", []):
        urls |= set(c["urls"])
    return {normalize_url(u) for u in urls}
