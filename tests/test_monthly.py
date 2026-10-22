"""Monthly trend assembly on a synthetic 15-month store (no network)."""
import datetime as dt
import json

import pytest

from seo_agent.score.monthly import build_monthly, complete_months, month_range, shift, slope, span
from seo_agent.sources.daystore import DayStore
from seo_agent.state import DirStore

B = "https://arbcentrix.com"
START, END = dt.date(2025, 6, 22), dt.date(2026, 10, 19)   # same window as the real backfill


def days():
    d = START
    while d <= END:
        yield d
        d += dt.timedelta(days=1)


@pytest.fixture
def store(tmp_path):
    st = DirStore(tmp_path / "state")
    pq = []
    for d in days():
        iso, month = d.isoformat(), d.strftime("%Y-%m")
        # Branded: 2 clicks/day until 2026-06, then 1/day.
        pq.append({"date": iso, "page": f"{B}/", "query": "arbcentrix", "clicks": 2 if month < "2026-07" else 1,
                   "impressions": 10, "position": 1.0})
        if month >= "2026-07":   # new non-branded query, page 3
            pq.append({"date": iso, "page": f"{B}/audit", "query": "audit support philippines", "clicks": 0,
                       "impressions": 1, "position": 35.0})
        if "2026-04" <= month <= "2026-06":   # /insights earned clicks, then lost them
            pq.append({"date": iso, "page": f"{B}/insights", "query": "cpa staffing", "clicks": 1,
                       "impressions": 3, "position": 5.0})
    totals = {}
    for r in pq:
        t = totals.setdefault((r["date"], r["page"]), {"date": r["date"], "page": r["page"], "clicks": 0,
                                                       "impressions": 0, "position": r["position"]})
        t["clicks"] += r["clicks"]
        t["impressions"] += r["impressions"]
    all_days = [d.isoformat() for d in days()]
    DayStore(st, "gsc/page_query").put(all_days, pq, "t")
    DayStore(st, "gsc/page").put(all_days, list(totals.values()), "t")
    DayStore(st, "ga4/landing").put(all_days, [{"date": "2026-09-10", "landingPage": "/audit",
                                                "sessionDefaultChannelGroup": "Organic Search", "sessions": 3,
                                                "engagedSessions": 2, "totalUsers": 3, "keyEvents": 1}], "t")
    DayStore(st, "ga4/key_events").put(all_days, [], "t")
    st.write_json("psi/2026-08-01.json", [{"url": f"{B}/tax", "strategy": "mobile",
                                            "lab": {"performance": 50, "lcp_ms": 9000}, "field": None}])
    st.write_json("psi/2026-10-20.json", [{"url": f"{B}/tax", "strategy": "mobile",
                                            "lab": {"performance": 61, "lcp_ms": 6000}, "field": None}])
    st.append_run("daily", alerts_new=2)
    return st


def test_month_helpers():
    assert month_range("2026-02") == ["2026-02-01", "2026-02-28"]
    assert shift("2026-01", -1) == "2025-12" and shift("2025-12", 13) == "2027-01"
    assert span(["2026-07", "2026-09"]) == ["2026-07-01", "2026-09-30"]
    synced = {d.isoformat() for d in days()}
    months = complete_months(synced)
    assert months[0] == "2025-07" and months[-1] == "2026-09" and len(months) == 15
    assert slope([1, 2, 3]) == 1.0 and slope([5]) is None


def test_monthly_trends(cfg, store):
    m = build_monthly(cfg, store, "2026-11-03T07:43:00+00:00")
    assert m["months"]["last_3"] == ["2026-07", "2026-08", "2026-09"]
    assert m["months"]["previous_3"] == ["2026-04", "2026-05", "2026-06"]
    assert m["months"]["last_3_last_year"] == ["2025-07", "2025-08", "2025-09"]
    assert len(m["months"]["series"]) == 13

    q3 = m["trend_3m"]["vs_previous_3"]["gsc"]
    assert q3["clicks"]["now"] == 92 and q3["clicks"]["before"] == 91 * 2 + 91   # Apr-Jun incl. /insights
    ly = m["trend_3m"]["vs_last_year"]["gsc"]
    assert ly["clicks"]["before"] == 92 * 2 and ly["impressions"]["now"] == 92 * 11
    assert m["trend_3m"]["vs_previous_3"]["ga4_organic"]["sessions"]["now"] == 3

    t12 = m["trend_12m"]
    assert t12["months"] == 12 and t12["previous_12"].startswith("not available")
    assert t12["slope_per_month"]["clicks"] < 0
    assert t12["series"][-1] == {"month": "2026-09", "clicks": 30, "impressions": 330, "ctr": 0.0909,
                                 "position": round((300 * 1 + 30 * 35) / 330, 2),
                                 "organic_sessions": 3, "organic_key_events": 1}


def test_monthly_candidates_and_sections(cfg, store):
    m = build_monthly(cfg, store, "2026-11-03T07:43:00+00:00")
    ids = {c["id"] for c in m["candidates"]}
    assert "content_gap:audit support philippines" in ids          # 92 impressions at position 35
    assert "rising_query:audit support philippines" in ids         # 0 -> 92
    assert f"declining_page:{B}/insights" in ids                   # 91 clicks -> 0
    assert f"critical_page_flat:{B}/contact" in ids
    assert not any("arbcentrix" in i for i in ids if i.startswith(("content_gap", "rising_query")))
    gap = next(c for c in m["candidates"] if c["id"] == "content_gap:audit support philippines")
    assert gap["evidence"] == {"impressions": 92, "best_position": 35.0, "best_page": f"{B}/audit"}
    assert m["psi"]["pages"][f"{B}/tax|mobile"]["lcp_ms"] == {"first": 9000, "latest": 6000}
    assert m["history"]["by_month"] and m["queries"]["non_branded_three_month"][0]["query"] == "audit support philippines"


def test_monthly_needs_three_complete_months(cfg, tmp_path):
    st = DirStore(tmp_path)
    DayStore(st, "gsc/page").put([f"2026-09-{d:02d}" for d in range(1, 31)], [], "t")
    with pytest.raises(RuntimeError, match="need 3 complete months"):
        build_monthly(cfg, st, "2026-11-03T07:43:00+00:00")


def test_qa_accepts_briefs_citing_monthly_candidates(cfg, store, snapshot, tmp_path):
    from seo_agent.reporting import run_qa
    from seo_agent.score.priority import priority
    out = tmp_path / "out"
    out.mkdir()
    (out / "monthly.json").write_text(json.dumps(build_monthly(cfg, store, "2026-11-03T07:43:00+00:00")))
    (out / "snapshot.json").write_text(json.dumps(snapshot))
    scores = {"impact": 4, "relevance": 5, "confidence": 3, "effort": 3, "risk": 1}
    brief = {"id": "b1", "candidate_id": "content_gap:audit support philippines", "title": "Audit support page",
             "finding": "'audit support philippines' ranks 35 at best.",
             "evidence": [{"source": "gsc", "metric": "impressions", "value": 92},
                          {"source": "gsc", "metric": "best_position", "value": 35.0}],
             "urls": [f"{B}/audit"], "queries": ["audit support philippines"], "impact": "Buyer query.",
             "action": "Expand /audit for this query.", "steps": ["Outline", "Write", "Link from /services"],
             "effort": 3, "confidence": 3, "scores": scores, "priority": priority(scores),
             "validation": "Position under 10 within 3 months.", "approval_level": "owner"}
    (out / "recs.json").write_text(json.dumps([brief]))
    assert run_qa(cfg, None, out, out / "recs.json", None, None, log=lambda *_: None) == 0
