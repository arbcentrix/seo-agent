"""Weekly report assembly, Priority, labels and the qa gate (synthetic store, no network)."""
import copy
import datetime as dt
import json

import pytest

from seo_agent.checks import run_checks
from seo_agent.qa import validate_email, validate_labels, validate_recommendation
from seo_agent.score.priority import ScoreError, priority
from seo_agent.score.weekly import apply_labels, build_weekly, known_urls
from seo_agent.sources.daystore import DayStore
from seo_agent.config import Env
from seo_agent.state import DirStore

END = dt.date(2026, 10, 19)
B = "https://arbcentrix.com"


def day(n):
    return (END - dt.timedelta(days=n)).isoformat()


@pytest.fixture
def store(tmp_path):
    st = DirStore(tmp_path / "state")
    pq, page = [], []

    def add(n, url, query, clicks, impr, pos):
        pq.append({"date": day(n), "page": url, "query": query, "clicks": clicks, "impressions": impr, "position": pos})

    for n in range(56):
        recent = n < 28
        add(n, f"{B}/", "arbcentrix", 3, 6, 1.0)                                  # branded
        add(n, f"{B}/about", "arbcentrix", 0, 4, 1.2)                             # branded, low CTR: excluded
        add(n, f"{B}/tax", "offshore tax preparation", 0, 2, 9.0)                 # striking distance
        add(n, f"{B}/accounting", "outsourced accounting", 0 if recent else 1, 3, 3.0)  # low CTR at pos 3
        add(n, f"{B}/audit", "audit support", 0, 1, 8.0)                          # split across 2 pages
        add(n, f"{B}/services", "audit support", 0, 1, 9.0)
        if not recent:
            add(n, f"{B}/insights", "cpa staffing", 1, 5, 4.0)                    # page decline: lost all
    totals = {}
    for r in pq:
        k = (r["date"], r["page"])
        t = totals.setdefault(k, {"date": r["date"], "page": r["page"], "clicks": 0, "impressions": 0, "position": 0})
        t["clicks"] += r["clicks"]
        t["impressions"] += r["impressions"]
        t["position"] = r["position"]
    days = [day(n) for n in range(56)]
    DayStore(st, "gsc/page_query").put(days, pq, "t")
    DayStore(st, "gsc/page").put(days, list(totals.values()), "t")
    DayStore(st, "ga4/landing").put(days, [
        {"date": day(1), "landingPage": "/tax", "sessionDefaultChannelGroup": "Organic Search",
         "sessions": 4, "engagedSessions": 3, "totalUsers": 4, "keyEvents": 1},
        {"date": day(1), "landingPage": "/", "sessionDefaultChannelGroup": "Direct",
         "sessions": 10, "engagedSessions": 5, "totalUsers": 9, "keyEvents": 0}], "t")
    DayStore(st, "ga4/key_events").put(days, [
        {"date": day(1), "landingPage": "/tax", "sessionDefaultChannelGroup": "Organic Search",
         "eventName": "generate_lead", "keyEvents": 1}], "t")
    st.write_json("psi/2026-10-20.json", [
        {"url": f"{B}/tax", "strategy": "mobile", "lab": {"performance": 61, "lcp_ms": 12941, "cls": 0.01}, "field": None},
        {"url": f"{B}/tax", "strategy": "desktop", "lab": {"performance": 86, "lcp_ms": 1851, "cls": 0.0}, "field": None},
        {"url": f"{B}/", "strategy": "mobile", "error": "HTTPError: 500"}])
    return st


@pytest.fixture
def weekly(cfg, store, snapshot):
    return build_weekly(cfg, store, snapshot, run_checks(snapshot, cfg), {}, "now")


def cands(w, type_):
    return {c["id"]: c for c in w["candidates"] if c["type"] == type_}


def test_windows_and_kpis(weekly):
    assert weekly["windows"]["7d"] == ["2026-10-13", "2026-10-19"]
    assert weekly["windows"]["28d"] == ["2026-09-22", "2026-10-19"]
    g = weekly["kpis"]["28d_vs_prev"]["gsc"]
    assert g["clicks"]["now"] == 28 * 3 and g["clicks"]["before"] == 28 * (3 + 1 + 1)
    assert g["impressions"]["now"] == 28 * 17 and g["impressions"]["before"] == 28 * 22
    assert g["clicks"]["change_pct"] == round((84 - 140) / 140, 4)
    org = weekly["kpis"]["7d_vs_prev"]["ga4"]["organic"]
    assert org["metrics"]["sessions"]["now"] == 4 and org["key_events_by_name"] == {"generate_lead": 1.0}
    assert weekly["kpis"]["7d_vs_prev"]["ga4"]["all_channels"]["metrics"]["sessions"]["now"] == 14


def test_query_page_rows(weekly):
    rows = {(r["query"], r["page"]): r for r in weekly["query_page"]}
    assert rows[("arbcentrix", f"{B}/")]["branded"] is True
    tax = rows[("offshore tax preparation", f"{B}/tax")]
    assert tax == {"query": "offshore tax preparation", "page": f"{B}/tax", "clicks": 0, "impressions": 56,
                   "ctr": 0.0, "position": 9.0, "branded": False, "intent": None}


def test_data_candidates(weekly):
    assert set(cands(weekly, "striking_distance")) == {f"striking_distance:offshore tax preparation|{B}/tax",
                                                       f"striking_distance:audit support|{B}/audit",
                                                       f"striking_distance:audit support|{B}/services"}
    assert set(cands(weekly, "low_ctr")) == {f"low_ctr:outsourced accounting|{B}/accounting"}  # branded /about excluded
    assert cands(weekly, "low_ctr")[f"low_ctr:outsourced accounting|{B}/accounting"]["evidence"]["expected_ctr"] == 0.10
    decline = cands(weekly, "page_decline")[f"page_decline:{B}/insights"]["evidence"]
    assert decline["clicks_before"] == 28 and decline["clicks_now"] == 0 and decline["fraction_lost"] == 1.0
    split = cands(weekly, "cannibalisation")["cannibalisation:audit support"]
    assert split["urls"] == [f"{B}/audit", f"{B}/services"]
    psi = cands(weekly, "psi_poor")
    assert set(psi) == {"psi_poor:mobile"} and psi["psi_poor:mobile"]["evidence"]["by_page"][f"{B}/tax"]["lab_lcp_ms"] == 12941
    no_impr = {c["urls"][0] for c in cands(weekly, "critical_no_impressions").values()}
    assert f"{B}/contact" in no_impr and f"{B}/tax" not in no_impr


def test_low_ctr_and_striking_distance_are_disjoint(cfg, store, snapshot):
    pq = DayStore(store, "gsc/page_query")
    pq.put([day(0)], [{"date": day(0), "page": f"{B}/tax", "query": "q edge", "clicks": 0,
                       "impressions": 40, "position": 3.49}], "t")
    w = build_weekly(cfg, store, snapshot, [], {}, "now")
    ids = {c["id"] for c in w["candidates"] if "q edge" in c["queries"]}
    assert ids == {f"low_ctr:q edge|{B}/tax"}
    pq.put([day(0)], [{"date": day(0), "page": f"{B}/tax", "query": "q edge", "clicks": 0,
                       "impressions": 40, "position": 3.5}], "t")
    w = build_weekly(cfg, store, snapshot, [], {}, "now")
    assert {c["id"] for c in w["candidates"] if "q edge" in c["queries"]} == {f"striking_distance:q edge|{B}/tax"}


def test_crawl_candidates_mirror_findings(weekly):
    crawl = cands(weekly, "crawl_title_meta_regression")["crawl_title_meta_regression:title_meta_regression"]
    assert crawl["evidence"]["pages"] == 61 and crawl["severity"] == "high"
    assert weekly["site_health"]["findings_by_rule"]["orphan_page"] == 30


def test_priority_formula():
    assert priority({"impact": 5, "relevance": 4, "confidence": 3, "effort": 2, "risk": 1}) == 30.0
    assert priority({"impact": 1, "relevance": 1, "confidence": 1, "effort": 5, "risk": 5}) == 0.04
    for bad in ({"impact": 6, "relevance": 1, "confidence": 1, "effort": 1, "risk": 1},
                {"impact": 2.5, "relevance": 1, "confidence": 1, "effort": 1, "risk": 1},
                {"impact": True, "relevance": 1, "confidence": 1, "effort": 1, "risk": 1}, {}):
        with pytest.raises(ScoreError):
            priority(bad)


def _labels(w, **override):
    scores = {c["id"]: {"impact": 3, "relevance": 3, "confidence": 3, "effort": 3, "risk": 1,
                        "rationale": "r"} for c in w["candidates"]}
    scores.update(override)
    return {"intents": {r["query"]: "commercial" for r in w["query_page"]}, "scores": scores}


def test_labels_validate_and_apply(cfg, weekly):
    intents = cfg.rules["scoring"]["intents"]
    top = f"striking_distance:offshore tax preparation|{B}/tax"
    labels = _labels(weekly, **{top: {"impact": 5, "relevance": 5, "confidence": 4, "effort": 2, "risk": 1}})
    assert validate_labels(labels, weekly, intents) == []
    w = apply_labels(copy.deepcopy(weekly), labels)
    assert w["candidates"][0]["id"] == top and w["candidates"][0]["priority"] == 50.0
    assert all(r["intent"] == "commercial" for r in w["query_page"])

    bad = _labels(weekly)
    bad["intents"]["arbcentrix"] = "curious"
    bad["intents"]["made up query"] = "commercial"
    bad["scores"][top] = {"impact": 9, "relevance": 3, "confidence": 3, "effort": 3, "risk": 1, "priority": 99}
    del bad["scores"][next(iter(bad["scores"]))]
    errs = " | ".join(validate_labels(bad, weekly, intents))
    for needle in ("'curious'", "unknown query 'made up query'", "scores.impact", "do not supply priority",
                   "have no scores"):
        assert needle in errs, needle


# --- recommendations ------------------------------------------------------------

def _rec(w, **kw):
    cid = f"striking_distance:offshore tax preparation|{B}/tax"
    scores = {"impact": 4, "relevance": 5, "confidence": 3, "effort": 2, "risk": 1}
    rec = {"id": "r1", "candidate_id": cid, "title": "Lift /tax onto page one",
           "finding": "/tax ranks 9 for 'offshore tax preparation' with no clicks.",
           "evidence": [{"source": "gsc", "metric": "impressions", "value": 56},
                        {"source": "gsc", "metric": "position", "value": 9.0},
                        {"source": "crawl", "detail": "Title is the brand-only 'Arbcentrix'."}],
           "urls": [f"{B}/tax"], "queries": ["offshore tax preparation"],
           "impact": "More clicks from a commercial query.", "action": "Rewrite the title and H1.",
           "steps": ["Draft a title with the query", "Update the H1"], "effort": 2, "confidence": 3,
           "scores": scores, "priority": priority(scores),
           "validation": "Position under 5 and CTR over 5% within 28 days.", "approval_level": "editor"}
    rec.update(kw)
    return rec


def check(cfg, w, snapshot, rec):
    return validate_recommendation(rec, w, known_urls(w, snapshot), "arbcentrix.com",
                                   list(cfg.rules["scoring"]["approval_levels"]))


def test_good_recommendation_passes(cfg, weekly, snapshot):
    assert check(cfg, weekly, snapshot, _rec(weekly)) == []


@pytest.mark.parametrize("change, needle", [
    ({"evidence": []}, "missing evidence"),
    ({"evidence": [{"source": "gut", "detail": "x"}]}, "source must be one of"),
    ({"evidence": [{"source": "gsc", "metric": "impressions", "value": 60}]}, "impressions is 56 in weekly.json, not 60"),
    ({"evidence": [{"source": "gsc", "metric": "sessions", "value": 3}]}, "not traceable"),
    ({"urls": ["https://arbcentrix.com/does-not-exist"]}, "not a page the crawl or GSC data knows"),
    ({"urls": ["http://arbcentrix.com/tax"]}, "not an absolute https URL"),
    ({"urls": ["https://evil.example/tax"]}, "not on arbcentrix.com"),
    ({"urls": ["arbcentrix.com/tax"]}, "not an absolute https URL"),
    ({"queries": ["invented query"]}, "not in the GSC data"),
    ({"urls": [], "queries": []}, "at least one URL or query"),
    ({"finding": ""}, "missing finding"),
    ({"validation": " "}, "missing validation"),
    ({"steps": []}, "steps must be"),
    ({"approval_level": "anyone"}, "approval_level must be one of"),
    ({"priority": 25}, "priority must be 30.0"),
    ({"effort": 3}, "effort must equal scores.effort"),
    ({"scores": {"impact": 4}}, "scores.relevance"),
    ({"candidate_id": "nope"}, "unknown candidate_id"),
])
def test_bad_recommendations_rejected(cfg, weekly, snapshot, change, needle):
    errs = check(cfg, weekly, snapshot, _rec(weekly, **change))
    assert any(needle in e for e in errs), errs


def test_scores_must_match_labelled_candidate(cfg, weekly, snapshot):
    w = apply_labels(copy.deepcopy(weekly), _labels(weekly))
    errs = check(cfg, w, snapshot, _rec(w))
    assert "scores differ from the candidate's scores in weekly.json" in errs


# --- email ----------------------------------------------------------------------

ENV = {"REPORTS_TO": "team@arbcentrix.com", "ALERTS_TO": "ops@arbcentrix.com", "SHADOW_EMAIL": "shadow@arbcentrix.com"}


def _email(**kw):
    e = {"kind": "report", "to": ["shadow@arbcentrix.com"], "subject": "[SHADOW] Weekly SEO report",
         "body": f"See {B}/tax.", "recommendation_ids": ["r1"]}
    e.update(kw)
    return e


def test_email_rules(weekly, snapshot):
    known = known_urls(weekly, snapshot)
    shadow = Env.from_environ({**ENV, "DELIVERY_MODE": "shadow"})
    live = Env.from_environ({**ENV, "DELIVERY_MODE": "live"})
    ok = lambda env, e, ids={"r1"}: validate_email(e, env, known, "arbcentrix.com", ids, "SECRETSECRETSECRETSECRETSECRET")  # noqa: E731
    assert ok(shadow, _email()) == []
    assert ok(live, _email(to=["team@arbcentrix.com"], subject="Weekly SEO report")) == []
    assert ok(live, _email(kind="alert", to=["ops@arbcentrix.com"], subject="Alert")) == []
    cases = [
        (shadow, _email(to=["team@arbcentrix.com"]), "not allowed in shadow mode"),
        (live, _email(to=["ops@arbcentrix.com"], subject="x"), "not allowed in live mode"),
        (live, _email(kind="alert", to=["team@arbcentrix.com"], subject="x"), "not allowed in live mode"),
        (shadow, _email(subject="Weekly"), "must start with [SHADOW]"),
        (shadow, _email(cc=["x@y.com"]), "cc is not allowed"),
        (shadow, _email(body="key: SECRETSECRETSECRETSECRETSECRET"), "credential material"),
        (shadow, _email(body='{"private_key": "x"}'), "credential material"),
        (shadow, _email(body=f"{B}/nope"), "not a page the crawl"),
        (shadow, _email(body="http://arbcentrix.com/tax"), "is not https"),
        (shadow, _email(recommendation_ids=["r2"]), "has not passed qa"),
        (shadow, _email(to=[]), "non-empty list"),
    ]
    for env, e, needle in cases:
        assert any(needle in x for x in ok(env, e)), (needle, ok(env, e))


# --- CLI ----------------------------------------------------------------------

def test_qa_cli_exit_codes(cfg, weekly, snapshot, tmp_path, capsys):
    from seo_agent.reporting import run_qa
    out = tmp_path / "out"
    out.mkdir()
    (out / "weekly.json").write_text(json.dumps(weekly))
    (out / "snapshot.json").write_text(json.dumps(snapshot))
    good, bad = out / "good.json", out / "bad.json"
    good.write_text(json.dumps([_rec(weekly)]))
    bad.write_text(json.dumps({"recommendations": [_rec(weekly, id="r9", urls=["https://arbcentrix.com/x"])]}))
    assert run_qa(cfg, None, out, good, None, None, log=lambda *_: None) == 0
    assert run_qa(cfg, None, out, bad, None, None, log=lambda *_: None) == 1
    assert json.loads((out / "qa.json").read_text())["recommendations"]["r9"]
    assert run_qa(cfg, None, out, None, None, None, log=lambda *_: None) == 2
