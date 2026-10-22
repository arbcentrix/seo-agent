"""Regression diff: turn checks into alerts by comparing with the previous snapshot.

- mode: state  -> alert whenever the rule fires.
- mode: diff   -> alert only when the rule fires now and did not fire on the previous
                  snapshot (a regression). Comparative rules (sitemap URL loss,
                  content_shrink) compare against ctx.previous directly.
- An alert stays open, reported as "ongoing", until its rule stops firing for that URL.
  Open alerts carry the evidence they opened with, so comparative rules keep measuring
  against the pre-regression value.
- With no previous snapshot (first run) diff rules only establish the baseline.
- A page that fails to load keeps its last good parse under "last_ok", and diffs compare
  against that, so a page coming back after an outage is not mistaken for a regression.
"""
import copy

from .engine import active_rules, run_rule, sort_findings


def _parsed(page: dict | None) -> bool:
    return bool(page) and page.get("status") == 200 and "title" in page


def carry_forward(snapshot: dict, previous: dict | None) -> dict:
    """Attach last_ok (the last good parse) to pages that did not parse this time."""
    if not previous:
        return snapshot
    for url, page in snapshot["pages"].items():
        if _parsed(page):
            continue
        old = previous["pages"].get(url)
        last_ok = {k: v for k, v in old.items() if k != "last_ok"} if _parsed(old) else (old or {}).get("last_ok")
        if last_ok:
            page["last_ok"] = last_ok
    return snapshot


def _effective(previous: dict) -> dict:
    """previous with unparsed pages replaced by their last good parse."""
    eff = copy.copy(previous)
    eff["pages"] = {url: (p["last_ok"] if not _parsed(p) and p.get("last_ok") else p)
                    for url, p in previous["pages"].items()}
    return eff


def _key(rule_id, url):
    return f"{rule_id} {url}"


def evaluate(snapshot: dict, cfg, previous: dict | None, open_alerts: dict | None,
             now: str, only_watch: bool = True) -> dict:
    """Return {"alerts": [...], "open": {...}, "resolved": [...]}.

    open_alerts / returned "open": {key: alert} as stored on the state branch.
    """
    open_alerts = open_alerts or {}
    previous = _effective(previous) if previous else None
    alerts, still_open, resolved = [], {}, []
    rules = active_rules(cfg, only_watch)
    for rule in rules:
        rid = rule["id"]
        baseline = {a["url"]: a["evidence"] for a in open_alerts.values() if a["rule"] == rid}
        now_hits = run_rule(rule, snapshot, cfg, previous, baseline)
        if rule.get("mode") == "diff":
            if previous is None:
                prev_hits = now_hits  # first run: everything is baseline
            else:
                # Condition parts only: comparative logic needs a snapshot before `previous`.
                prev_hits = run_rule(rule, previous, cfg, None, None)
        else:
            prev_hits = {}

        for url, evidence in now_hits.items():
            key = _key(rid, url)
            if key in open_alerts:
                opened = open_alerts[key]
                alert = {**opened, "status": "ongoing", "last_seen": now, "evidence_now": evidence}
            elif url not in prev_hits:
                alert = {"rule": rid, "severity": rule["severity"], "url": url, "evidence": evidence,
                         "status": "new", "first_seen": now, "last_seen": now}
            else:
                continue  # already failing before: a baseline finding, not a regression
            alerts.append(alert)
            still_open[key] = {k: v for k, v in alert.items() if k not in ("status", "evidence_now")}

    active_ids = {r["id"] for r in rules}
    for key, alert in open_alerts.items():
        if key not in still_open and alert["rule"] in active_ids:
            resolved.append({**alert, "resolved_at": now})
        elif key not in still_open:
            still_open[key] = alert  # rule not evaluated in this run (e.g. watch subset)
    return {"alerts": sort_findings(alerts), "open": still_open, "resolved": resolved}
