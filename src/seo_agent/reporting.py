"""weekly and qa commands."""
import json
from datetime import datetime, timezone
from pathlib import Path

from .checks import run_checks
from .qa import validate_alert, validate_email, validate_labels, validate_recommendation
from .score.weekly import apply_labels, build_weekly, known_urls

LATEST = "snapshots/latest.json"


def _read(path: Path):
    return json.loads(Path(path).read_text())


def _dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _snapshot(store, out_dir: Path):
    snap = store.read_json(LATEST) if store is not None else None
    if snap is None and (out_dir / "snapshot.json").exists():
        snap = _read(out_dir / "snapshot.json")
    return snap


def run_weekly(cfg, store, out_dir: Path, labels_path: Path | None = None, commit=None,
               sync_result=None, log=print) -> int:
    weekly_path = out_dir / "weekly.json"
    if labels_path:
        weekly, labels = _read(weekly_path), _read(labels_path)
        errors = validate_labels(labels, weekly, cfg.rules["scoring"]["intents"])
        if errors:
            for e in errors:
                log(f"labels REJECTED: {e}")
            return 1
        weekly = apply_labels(weekly, labels)
        _dump(weekly_path, weekly)
        top = [(c["id"], c["priority"]) for c in weekly["candidates"][:5]]
        log(json.dumps({"labels": "applied", "top_candidates": top}))
        if store is not None:
            store.write_json(f"reports/weekly/{weekly['windows']['7d'][1]}.json", weekly)
        if commit:
            commit("weekly-labels", {"candidates": len(weekly["candidates"])}, None)
        return 0

    snapshot = _snapshot(store, out_dir)
    if snapshot is None:
        log("no crawl snapshot: run `python -m seo_agent daily` first")
        return 1
    findings = run_checks(snapshot, cfg)
    open_alerts = (store.read_json("alerts/open.json") if store is not None else None) or {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    weekly = build_weekly(cfg, store, snapshot, findings, open_alerts, now)
    _dump(weekly_path, weekly)
    summary = {"window": weekly["windows"]["7d"], "candidates": len(weekly["candidates"]),
               "query_page_rows": len(weekly["query_page"])}
    log(json.dumps(summary))
    if store is not None:
        store.write_json(f"reports/weekly/{weekly['windows']['7d'][1]}.json", weekly)
    if commit:
        commit("weekly", summary, sync_result)
    return 0


def with_alert_candidates(weekly: dict, alerts: list) -> dict:
    """Add today's alerts as candidates (id alert_<rule>:<url>) so alert recommendations can pass qa."""
    have = {c["id"] for c in weekly["candidates"]}
    extra = [{"id": f"alert_{a['rule']}:{a['url']}", "type": f"alert_{a['rule']}", "severity": a["severity"],
              "title": f"{a['rule']} on {a['url']}", "urls": [a["url"]], "queries": [],
              "evidence": a.get("evidence_now") or a["evidence"], "scores": None, "priority": None}
             for a in alerts if f"alert_{a['rule']}:{a['url']}" not in have]
    return {**weekly, "candidates": weekly["candidates"] + extra}


def _items(data, key):
    return data.get(key, []) if isinstance(data, dict) else data


def run_qa(cfg, store, out_dir: Path, recommendations: Path | None, email: Path | None,
           labels: Path | None, weekly_path: Path | None = None, log=print) -> int:
    weekly_path = weekly_path or out_dir / "weekly.json"
    alerts_path = out_dir / "alerts.json"
    if not weekly_path.exists() and not alerts_path.exists():
        log(f"qa: neither {weekly_path} nor {alerts_path} exists; run weekly, daily or watch first")
        return 2
    weekly = _read(weekly_path) if weekly_path.exists() else {"candidates": [], "query_page": []}
    weekly = with_alert_candidates(weekly, _read(alerts_path) if alerts_path.exists() else [])
    known = known_urls(weekly, _snapshot(store, out_dir))
    domain = cfg.site["domain"]
    result, failed, checked = {"labels": None, "recommendations": {}, "email": None}, False, 0

    if labels:
        errs = validate_labels(_read(labels), weekly, cfg.rules["scoring"]["intents"])
        result["labels"] = errs
        failed |= bool(errs)
        checked += 1
    passed = set()
    if recommendations:
        levels = list(cfg.rules["scoring"]["approval_levels"])
        for i, rec in enumerate(_items(_read(recommendations), "recommendations")):
            rid = rec.get("id") or f"#{i}"
            errs = validate_recommendation(rec, weekly, known, domain, levels)
            result["recommendations"][rid] = errs
            if not errs:
                passed.add(rid)
            failed |= bool(errs)
            checked += 1
    if email:
        errs = validate_email(_read(email), cfg.env, known, domain, passed, cfg.env.google_sa_json_b64)
        result["email"] = errs
        failed |= bool(errs)
        checked += 1
    if not checked:
        log("qa: nothing to check (give --recommendations, --email and/or --labels)")
        return 2

    _dump(out_dir / "qa.json", {"passed": not failed, **result})
    if result["labels"] is not None:
        log(f"labels: {'OK' if not result['labels'] else 'REJECTED'}")
        for e in result["labels"]:
            log(f"  - {e}")
    for rid, errs in result["recommendations"].items():
        log(f"recommendation {rid}: {'OK' if not errs else 'REJECTED'}")
        for e in errs:
            log(f"  - {e}")
    if result["email"] is not None:
        log(f"email: {'OK' if not result['email'] else 'REJECTED'}")
        for e in result["email"]:
            log(f"  - {e}")
    return 1 if failed else 0


def run_verify(cfg, store, out_dir: Path, items_path: Path, weekly_path: Path | None = None,
               log=print) -> int:
    from .verify import verify_items
    weekly_path = weekly_path or out_dir / "weekly.json"
    weekly = _read(weekly_path) if weekly_path.exists() else None
    if weekly is None and store is not None:
        days = [n for n in store.listdir("reports/weekly") if n.endswith(".json")]
        weekly = store.read_json(f"reports/weekly/{max(days)}") if days else None
    open_alerts = (store.read_json("alerts/open.json") if store is not None else None) or {}
    result = verify_items(_items(_read(items_path), "items"), cfg, _snapshot(store, out_dir), open_alerts, weekly)
    _dump(out_dir / "verify.json", result)
    for item_id, r in result.items():
        log(f"{item_id}: {r['status']}" + (f" {json.dumps(r['detail'])}" if r["detail"] else ""))
    return 0


def detect_kind(path: Path) -> str | None:
    data = _read(path)
    if isinstance(data, dict):
        if "intents" in data or "scores" in data:
            return "labels"
        if "to" in data and "subject" in data:
            return "email"
        if "recommendations" in data:
            return "recommendations"
        return None
    if isinstance(data, list):
        if not data or all(isinstance(x, dict) and "rule" in x and "evidence" in x for x in data):
            return "alerts"
        if all(isinstance(x, dict) and "finding" in x for x in data):
            return "recommendations"
    return None


def run_qa_alerts(cfg, alerts_path: Path, out_dir: Path, log=print) -> int:
    raw_path = alerts_path.with_name("alerts_raw.json")
    if not raw_path.exists():
        log(f"qa: {raw_path} not found; run daily or watch first")
        return 2
    raw = {(a["rule"], a["url"]): a for a in _read(raw_path)}
    alerts = _read(alerts_path)
    levels = list(cfg.rules["scoring"]["approval_levels"])
    result = {}
    for a in alerts:
        key = (a.get("rule"), a.get("url"))
        rule = next((r for r in cfg.rules.get("checks", []) if r["id"] == key[0]), None)
        result[f"{key[0]} {key[1]}"] = validate_alert(a, raw.get(key), levels, rule)
    for key in set(raw) - {(a.get("rule"), a.get("url")) for a in alerts}:
        result[f"{key[0]} {key[1]}"] = ["alert from the script is missing from the file"]
    failed = any(result.values())
    _dump(out_dir / "qa.json", {"passed": not failed, "alerts": result})
    if not result:
        log("alerts: none")
    for k, errs in sorted(result.items()):
        log(f"alert {k}: {'OK' if not errs else 'REJECTED'}")
        for e in errs:
            log(f"  - {e}")
    return 1 if failed else 0
