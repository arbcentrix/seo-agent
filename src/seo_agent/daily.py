"""daily: polite crawl of the live site, run rules.yaml checks, write out/ and the state branch."""
import json
from collections import Counter
from pathlib import Path

from .checks import run_checks
from .crawl.crawl import crawl
from .crawl.fetcher import Fetcher
from .crawl.sources import FixtureSource, LiveSource


def run_daily(cfg, out_dir: Path, state=None, fixtures: Path | None = None, log=print) -> dict:
    source = FixtureSource(fixtures) if fixtures else LiveSource(
        Fetcher(cfg.user_agent, interval=cfg.site.get("request_interval_seconds", 1)))
    snapshot = crawl(source, cfg)
    findings = run_checks(snapshot, cfg)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "snapshot.json").write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    (out_dir / "findings.json").write_text(json.dumps(findings, indent=2) + "\n")

    by_severity = Counter(f["severity"] for f in findings)
    summary = {"pages": len(snapshot["pages"]), "findings": len(findings),
               "by_severity": dict(by_severity), "truncated": snapshot["truncated"]}
    log(json.dumps(summary))

    if state is not None:
        day = snapshot["fetched_at"][:10]
        state.write_json(f"snapshots/{day}.json", snapshot)
        state.append_run("daily", findings=len(findings), pages=len(snapshot["pages"]),
                         by_severity=dict(by_severity))
        state.commit_and_push(f"daily {day}: {len(findings)} findings")
    return summary
