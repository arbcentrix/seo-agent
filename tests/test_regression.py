"""Regression diff: alerts fire on transitions, persist while failing, resolve when fixed."""
import copy
import json
import subprocess

from seo_agent.checks.regression import carry_forward, evaluate
from seo_agent.scan import run_scan
from seo_agent.state import StateBranch

from conftest import FIXTURES

TAX = "https://arbcentrix.com/tax"
HOME = "https://arbcentrix.com/"
SITEMAP = "https://arbcentrix.com/sitemap.xml"


def run(cfg, now, prev, open_alerts=None, t="t"):
    return evaluate(now, cfg, prev, open_alerts, t)


def keys(result):
    return {(a["rule"], a["url"], a["status"]) for a in result["alerts"]}


def test_first_run_is_baseline_and_healthy(snapshot, cfg):
    assert run(cfg, snapshot, None)["alerts"] == []


def test_unchanged_site_is_healthy(snapshot, cfg):
    # Brand-only titles exist on every page today: discovery findings, not regressions.
    assert run(cfg, snapshot, copy.deepcopy(snapshot))["alerts"] == []


def test_alert_lifecycle_new_ongoing_resolved(snapshot, cfg):
    prev = copy.deepcopy(snapshot)
    snapshot["pages"][TAX]["status"] = 500
    r1 = run(cfg, snapshot, prev, t="t1")
    assert keys(r1) == {("status_change", TAX, "new")}

    carry_forward(snapshot, prev)
    assert snapshot["pages"][TAX]["last_ok"]["title"] == "Arbcentrix"
    r2 = run(cfg, carry_forward(copy.deepcopy(snapshot), snapshot), snapshot, r1["open"], t="t2")
    assert keys(r2) == {("status_change", TAX, "ongoing")}
    assert r2["alerts"][0]["first_seen"] == "t1" and r2["alerts"][0]["last_seen"] == "t2"

    fixed = copy.deepcopy(prev)  # back up, with today's long-standing brand-only title
    r3 = run(cfg, fixed, snapshot, r2["open"], t="t3")
    assert r3["alerts"] == [] and r3["open"] == {}
    assert [(a["rule"], a["url"], a["resolved_at"]) for a in r3["resolved"]] == [("status_change", TAX, "t3")]


def test_starts_redirecting(snapshot, cfg):
    prev = copy.deepcopy(snapshot)
    snapshot["pages"][TAX]["redirects"] = [[301, HOME]]
    assert keys(run(cfg, snapshot, prev)) == {("status_change", TAX, "new")}


def test_robots_new_disallow_and_disappears(snapshot, cfg):
    prev = copy.deepcopy(snapshot)
    snapshot["robots"]["rules"] = {"allow": [], "disallow": ["/tax"]}
    assert keys(run(cfg, snapshot, prev)) == {("robots_block", TAX, "new")}
    snapshot["robots"]["status"] = 404
    assert keys(run(cfg, snapshot, prev)) == {("robots_block", "https://arbcentrix.com/robots.txt", "new")}


def test_noindex_gained_but_not_on_excluded_pages(snapshot, cfg):
    prev = copy.deepcopy(snapshot)
    snapshot["pages"][TAX]["noindex"] = True
    snapshot["pages"]["https://arbcentrix.com/terms"]["noindex"] = True
    assert keys(run(cfg, snapshot, prev)) == {("noindex_added", TAX, "new")}


def test_sitemap_thresholds(snapshot, cfg):
    prev = copy.deepcopy(snapshot)
    urls = snapshot["sitemap"]["urls"]
    snapshot["sitemap"]["urls"] = urls[:-2]  # 2 of 59 = 3.4%: below both thresholds
    assert run(cfg, snapshot, prev)["alerts"] == []
    snapshot["sitemap"]["urls"] = urls[:-3]  # 3 URLs lost
    r = run(cfg, snapshot, prev)
    assert keys(r) == {("sitemap_drop", SITEMAP, "new")}
    assert r["alerts"][0]["evidence"]["lost"] == sorted(urls[-3:])
    # Next run compares with the pre-drop URL set, so the alert stays open.
    r2 = run(cfg, copy.deepcopy(snapshot), snapshot, r["open"])
    assert keys(r2) == {("sitemap_drop", SITEMAP, "ongoing")}
    snapshot["sitemap"].update(status=500, error="HTTP 500", urls=[])
    assert ("sitemap_drop", SITEMAP, "new") in keys(run(cfg, snapshot, prev))


def test_sitemap_fraction_threshold(snapshot, cfg):
    prev = copy.deepcopy(snapshot)
    prev["sitemap"]["urls"] = prev["sitemap"]["urls"][:20]
    snapshot["sitemap"]["urls"] = prev["sitemap"]["urls"][:18]  # 2 of 20 = 10%
    assert keys(run(cfg, snapshot, prev)) == {("sitemap_drop", SITEMAP, "new")}


def test_title_meta_regression_only_from_a_good_value(snapshot, cfg):
    page = snapshot["pages"][TAX]
    page.update(title="Offshore tax support | Arbcentrix", meta_description="Tax prep support.")
    prev = copy.deepcopy(snapshot)
    assert run(cfg, copy.deepcopy(snapshot), prev)["alerts"] == []
    page["title"] = "Arbcentrix"
    assert keys(run(cfg, snapshot, prev)) == {("title_meta_regression", TAX, "new")}
    page["title"] = ""
    assert keys(run(cfg, snapshot, prev)) == {("title_meta_regression", TAX, "new")}


def test_content_shrink_persists_against_pre_shrink_baseline(snapshot, cfg):
    prev = copy.deepcopy(snapshot)
    before = prev["pages"][TAX]["word_count"]
    snapshot["pages"][TAX]["word_count"] = int(before * 0.55)
    r1 = run(cfg, snapshot, prev)
    assert keys(r1) == {("content_shrink", TAX, "new")}
    assert r1["alerts"][0]["evidence"]["words_before"] == before
    r2 = run(cfg, copy.deepcopy(snapshot), snapshot, r1["open"])
    assert keys(r2) == {("content_shrink", TAX, "ongoing")}
    restored = copy.deepcopy(prev)
    assert run(cfg, restored, snapshot, r2["open"])["resolved"]


def test_state_rules_alert_without_a_transition(snapshot, cfg):
    snapshot["pages"]["https://arbcentrix.com/contact"]["has_form"] = False
    snapshot["pages"][TAX]["tracking_ids"] = ["GTM-N8DDS834", "GTM-PG3F2KGJ"]
    got = keys(run(cfg, snapshot, None))  # even on the very first run
    assert got == {("cta_broken", "https://arbcentrix.com/contact", "new"),
                   ("tracking_missing", TAX, "new")}


def test_new_page_published_with_brand_only_title(snapshot, cfg):
    prev = copy.deepcopy(snapshot)
    new = "https://arbcentrix.com/new-service"
    snapshot["pages"][new] = {**copy.deepcopy(snapshot["pages"][TAX]), "url": new}
    assert ("title_meta_regression", new, "new") in keys(run(cfg, snapshot, prev))


# --- end to end through the state branch ------------------------------------

def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def test_scan_end_to_end_with_state(cfg, tmp_path):
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "clone", str(remote), str(work))
    for k, v in (("user.email", "t@x.com"), ("user.name", "t")):
        _git(work, "config", k, v)
    _git(work, "commit", "--allow-empty", "-m", "init")
    _git(work, "push", "origin", "HEAD")
    state = StateBranch(work, "state", tmp_path / "wt").open()
    out = tmp_path / "out"

    first = run_scan("daily", cfg, out, state, FIXTURES, log=lambda *_: None)
    assert first["summary"]["baseline"] is True
    assert json.loads((out / "alerts.json").read_text()) == []
    assert (out / "findings.json").exists()

    second = run_scan("watch", cfg, out, state, FIXTURES, log=lambda *_: None)
    assert second["summary"]["baseline"] is False and second["alerts"] == []
    assert json.loads((out / "alerts.json").read_text()) == []

    runs = state.read_runs()
    assert [r["command"] for r in runs] == ["daily", "watch"]
    assert state.read_json("alerts/open.json") == {}
    assert state.read_json("snapshots/latest.json")["pages"]
    dated = sorted(p.name for p in (state.path / "snapshots").iterdir())
    assert len(dated) == 2 and "latest.json" in dated  # daily writes a dated copy, watch does not


def test_regression_during_outage_is_still_caught(snapshot, cfg):
    prev = copy.deepcopy(snapshot)
    prev["pages"][TAX].update(title="Offshore tax support | Arbcentrix", meta_description="Tax help.")
    down = copy.deepcopy(prev)
    down["pages"][TAX] = {"url": TAX, "status": 503, "redirects": [], "error": None}
    carry_forward(down, prev)
    back = copy.deepcopy(prev)
    back["pages"][TAX]["title"] = ""  # came back broken
    assert ("title_meta_regression", TAX, "new") in keys(run(cfg, back, down))
