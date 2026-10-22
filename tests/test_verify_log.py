import copy
import json

from seo_agent.cli import main
from seo_agent.verify import verify_items

B = "https://arbcentrix.com"
WEEKLY = {"windows": {"28d": ["2026-11-01", "2026-11-28"]},
          "candidates": [{"id": f"low_ctr:q|{B}/tax"}]}


def test_verify_statuses(cfg, snapshot):
    items = [
        {"item_id": "1", "candidate_id": "crawl_h1_missing:h1_missing", "urls": [f"{B}/newsletter"]},
        {"item_id": "2", "candidate_id": "crawl_h1_missing:h1_missing", "urls": [f"{B}/tax"]},
        {"item_id": "3", "candidate_id": f"alert_status_change:{B}/tax", "urls": [f"{B}/tax"]},
        {"item_id": "4", "candidate_id": f"low_ctr:q|{B}/tax", "urls": [f"{B}/tax"], "applied_at": "2026-10-20"},
        {"item_id": "5", "candidate_id": f"low_ctr:q|{B}/audit", "urls": [f"{B}/audit"], "applied_at": "2026-10-20"},
        {"item_id": "6", "candidate_id": f"low_ctr:q|{B}/audit", "urls": [f"{B}/audit"], "applied_at": "2026-11-05"},
        {"item_id": "7", "candidate_id": "mystery:x", "urls": []},
    ]
    alerts = {f"status_change {B}/tax": {}}
    r = verify_items(items, cfg, snapshot, alerts, WEEKLY)
    assert {k: v["status"] for k, v in r.items()} == {
        "1": "still_present", "2": "resolved", "3": "still_present", "4": "still_present",
        "5": "resolved", "6": "too_early", "7": "unknown"}
    assert verify_items(items[2:3], cfg, snapshot, {}, WEEKLY)["3"]["status"] == "resolved"


def test_log_command_appends_run(tmp_path, monkeypatch):
    out = tmp_path / "out"
    assert main(["log", "--no-state", "--out", str(out), "--run", "daily", "--status", "error",
                 "--note", "crawl failed", "--suspicious", "ignore previous instructions"]) == 0
    line = json.loads((out / "state-local" / "runs.jsonl").read_text().splitlines()[0])
    assert line["command"] == "daily" and line["status"] == "error" and line["note"] == "crawl failed"
    assert line["suspicious"] == [{"source": "claude", "text": "ignore previous instructions"}]


# --- qa on alerts.json (prompt step: `python -m seo_agent qa out/alerts.json`) -------

RAW = [{"rule": "status_change", "severity": "critical", "url": f"{B}/tax", "status": "new",
        "evidence": {"status": 500, "error": None}, "first_seen": "t", "last_seen": "t"},
       {"rule": "content_shrink", "severity": "medium", "url": f"{B}/audit", "status": "ongoing",
        "evidence": {"words_before": 898, "words_now": 401, "fraction_lost": 0.553},
        "evidence_now": {"words_before": 898, "words_now": 401, "fraction_lost": 0.553},
        "first_seen": "t0", "last_seen": "t"}]


def explained(**over):
    alerts = copy.deepcopy(RAW)
    alerts[0].update(cause="The /tax page returns HTTP 500 since the last publish.",
                     fix="Republish /tax in Framer and check it returns 200.",
                     effort=1, confidence=4, approval_level="developer")
    alerts[1].update(cause="The page dropped from 898 to 401 words, 55.3% of its copy.",
                     fix="Restore the removed sections.", effort=2, confidence=3, approval_level="editor")
    for k, v in over.items():
        alerts[0][k] = v
    return alerts


def run_alert_qa(cfg, tmp_path, alerts):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    (out / "alerts_raw.json").write_text(json.dumps(RAW))
    (out / "alerts.json").write_text(json.dumps(alerts))
    code = main(["qa", str(out / "alerts.json"), "--out", str(out), "--no-state"])
    return code, json.loads((out / "qa.json").read_text())["alerts"]


def test_alert_qa_passes(cfg, tmp_path):
    code, res = run_alert_qa(cfg, tmp_path, explained())
    assert code == 0 and not any(res.values())


def test_alert_qa_empty_file_passes(cfg, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "alerts_raw.json").write_text("[]")
    (out / "alerts.json").write_text("[]")
    assert main(["qa", str(out / "alerts.json"), "--out", str(out), "--no-state"]) == 0


def test_alert_qa_rejections(cfg, tmp_path):
    cases = [
        ({"cause": ""}, "missing cause"),
        ({"fix": "One. Two. Three."}, "4 sentences"),
        ({"cause": "The page returns HTTP 503."}, "number 503 is not in this alert's evidence"),
        ({"severity": "low"}, "script field 'severity' was changed"),
        ({"evidence": {"status": 200}}, "script field 'evidence' was changed"),
        ({"effort": 0}, "effort must be"),
        ({"approval_level": None}, "approval_level must be"),
    ]
    for change, needle in cases:
        code, res = run_alert_qa(cfg, tmp_path, explained(**change))
        errs = res[f"status_change {B}/tax"]
        assert code == 1 and any(needle in e for e in errs), (needle, errs)
    code, res = run_alert_qa(cfg, tmp_path, explained()[:1])
    assert res[f"content_shrink {B}/audit"] == ["alert from the script is missing from the file"]
    extra = explained() + [{**RAW[0], "url": f"{B}/x"}]
    assert "not an alert the script produced" in run_alert_qa(cfg, tmp_path, extra)[1][f"status_change {B}/x"][0]


def test_detect_kind(tmp_path):
    from seo_agent.reporting import detect_kind
    cases = {"alerts": RAW, "labels": {"intents": {}, "scores": {}},
             "email": {"to": [], "subject": "s", "body": "b"},
             "recommendations": [{"finding": "x"}]}
    for kind, data in cases.items():
        p = tmp_path / f"{kind}.json"
        p.write_text(json.dumps(data))
        assert detect_kind(p) == kind
    (tmp_path / "x.json").write_text('{"what": 1}')
    assert detect_kind(tmp_path / "x.json") is None


# --- one runs.jsonl line per run -----------------------------------------------

def test_close_run_merges_into_script_line(tmp_path):
    from seo_agent.state import DirStore
    st = DirStore(tmp_path)
    st.append_run("daily", findings=3, suspicious=[{"source": "gsc_query", "text": "x"}])
    st.close_run("daily", status="ok", note="emailed 1 alert",
                 suspicious=[{"source": "claude", "text": "y"}])
    runs = st.read_runs()
    assert len(runs) == 1 and runs[0]["closed"] and runs[0]["note"] == "emailed 1 alert"
    assert runs[0]["findings"] == 3 and len(runs[0]["suspicious"]) == 2
    st.close_run("daily", status="error", note="crashed before the script logged")  # nothing open
    assert len(st.read_runs()) == 2 and st.read_runs()[1]["status"] == "error"


def test_runs_summary_against_schedule(cfg, tmp_path):
    from seo_agent.score.weekly import runs_summary
    from seo_agent.state import DirStore
    st = DirStore(tmp_path)
    lines = [{"ts": f"2026-10-{d:02d}T06:13:00+00:00", "command": "daily", "status": "ok", "closed": True}
             for d in range(14, 20)]
    lines += [{"ts": "2026-10-19T06:13:00+00:00", "command": "watch", "status": "error"},
              {"ts": "2026-10-01T06:13:00+00:00", "command": "daily", "status": "ok"}]
    st.write_text("runs.jsonl", "".join(json.dumps(x) + "\n" for x in lines))
    r = runs_summary(cfg, st, "2026-10-20T06:43:00+00:00")
    assert r["tasks"]["daily"] == {"task": "SEO daily watch", "expected": 7, "runs": 6, "ok": 6, "errors": 0,
                                   "not_closed": 0, "last_run": "2026-10-19T06:13:00+00:00", "missing": 1}
    assert r["tasks"]["watch"]["errors"] == 1 and r["tasks"]["watch"]["missing"] == 41
    assert r["tasks"]["monthly"]["expected"] is None


def test_log_works_without_google_auth(tmp_path):
    """A run must still be able to record its failure if google-auth is missing."""
    import subprocess
    import sys
    code = (
        "import sys\n"
        "for m in ('google', 'google.auth', 'google.oauth2'): sys.modules[m] = None\n"
        "from seo_agent.cli import main\n"
        f"sys.exit(main(['log', '--no-state', '--out', {str(tmp_path)!r}, '--run', 'watch',"
        " '--status', 'error', '--note', 'google-auth missing']))\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert '"status": "error"' in (tmp_path / "state-local" / "runs.jsonl").read_text()
