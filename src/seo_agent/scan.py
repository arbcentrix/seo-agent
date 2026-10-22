"""daily and watch: crawl, run checks, diff with the previous snapshot, write alerts.

State branch layout:
  snapshots/latest.json      most recent crawl (daily or watch) - the diff baseline
  snapshots/<date>.json      one per daily run
  alerts/open.json           alerts still firing, with the evidence they opened with
  alerts/resolved.jsonl      one line per alert that cleared
  runs.jsonl                 one line per run
Outputs (out/):
  snapshot.json, findings.json (daily only: every rules.yaml check),
  alerts.json (new + ongoing alerts; [] when healthy)
"""
import json
from collections import Counter
from pathlib import Path

from .checks import run_checks
from .checks.regression import carry_forward, evaluate
from .crawl.crawl import crawl
from .crawl.fetcher import Fetcher
from .crawl.sources import FixtureSource, LiveSource

LATEST = "snapshots/latest.json"
OPEN = "alerts/open.json"
RESOLVED = "alerts/resolved.jsonl"


def _dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def run_scan(kind: str, cfg, out_dir: Path, state=None, fixtures: Path | None = None,
             previous: dict | None = None, log=print, sync_result: dict | None = None) -> dict:
    assert kind in ("daily", "watch")
    source = FixtureSource(fixtures) if fixtures else LiveSource(
        Fetcher(cfg.user_agent, interval=cfg.site.get("request_interval_seconds", 1)))

    open_alerts = {}
    if state is not None:
        previous = state.read_json(LATEST)
        open_alerts = state.read_json(OPEN) or {}

    snapshot = carry_forward(crawl(source, cfg), previous)
    now = snapshot["fetched_at"]
    result = evaluate(snapshot, cfg, previous, open_alerts, now)
    alerts = result["alerts"]

    out_dir.mkdir(parents=True, exist_ok=True)
    _dump(out_dir / "snapshot.json", snapshot)
    _dump(out_dir / "alerts.json", alerts)
    findings = None
    if kind == "daily":
        findings = run_checks(snapshot, cfg, previous)
        _dump(out_dir / "findings.json", findings)

    status = Counter(a["status"] for a in alerts)
    summary = {"pages": len(snapshot["pages"]), "baseline": previous is None,
               "alerts_new": status["new"], "alerts_ongoing": status["ongoing"],
               "resolved": len(result["resolved"]),
               "alerts_by_severity": dict(Counter(a["severity"] for a in alerts)),
               "truncated": snapshot["truncated"]}
    if findings is not None:
        summary["findings"] = len(findings)
    log(json.dumps(summary))

    if state is not None:
        state.write_json(LATEST, snapshot)
        if kind == "daily":
            state.write_json(f"snapshots/{now[:10]}.json", snapshot)
        state.write_json(OPEN, result["open"])
        if result["resolved"]:
            old = state.read_text(RESOLVED) or ""
            state.write_text(RESOLVED, old + "".join(json.dumps(r, sort_keys=True) + "\n"
                                                     for r in result["resolved"]))
        extra = {k: v for k, v in summary.items() if k != "findings"}
        if sync_result:
            extra["sources"] = sync_result["sources"]
        state.append_run(kind, findings=len(findings) if findings is not None else len(alerts),
                         suspicious=(sync_result or {}).get("suspicious"), **extra)
        state.commit_and_push(f"{kind} {now}: {status['new']} new, {status['ongoing']} ongoing alerts")
    return {"summary": summary, "alerts": alerts, "resolved": result["resolved"]}
