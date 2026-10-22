"""verify: has an Applied recommendation's issue cleared? Script answers only; Claude acts on them.

Input items: [{"item_id", "candidate_id", "urls": [...], "applied_at": "YYYY-MM-DD"}].
Statuses:
  resolved       the issue no longer shows in current script output -> may move Applied -> Verified
  still_present  it still shows
  too_early      data candidates only: the latest 28-day window still includes days before applied_at
  unknown        the candidate type or the data needed is not available
"""
from .checks import run_checks

DATA_TYPES = {"striking_distance", "low_ctr", "page_decline", "cannibalisation",
              "critical_no_impressions", "psi_poor"}


def verify_items(items, cfg, snapshot, open_alerts, weekly) -> dict:
    findings = run_checks(snapshot, cfg) if snapshot else None
    present_findings = {(f["rule"], f["url"]) for f in findings or []}
    weekly_ids = {c["id"] for c in (weekly or {}).get("candidates", [])}
    out = {}
    for item in items:
        cid, urls = item.get("candidate_id") or "", item.get("urls") or []
        type_ = cid.split(":", 1)[0]
        status, detail = "unknown", None
        if type_.startswith("crawl_") and findings is not None:
            rule = type_[len("crawl_"):]
            still = [u for u in urls if (rule, u) in present_findings]
            status = "still_present" if still or not urls else "resolved"
            detail = {"still_failing": still} if still else ({"note": "item has no URLs"} if not urls else None)
        elif type_.startswith("alert_"):
            rule = type_[len("alert_"):]
            still = [u for u in urls if f"{rule} {u}" in (open_alerts or {})]
            status = "still_present" if still or not urls else "resolved"
            detail = {"still_open": still} if still else None
        elif type_ in DATA_TYPES and weekly:
            start = weekly["windows"]["28d"][0]
            applied = item.get("applied_at") or ""
            if not applied or start <= applied:
                status, detail = "too_early", {"window_starts": start, "applied_at": applied or None}
            else:
                status = "still_present" if cid in weekly_ids else "resolved"
                detail = {"window": weekly["windows"]["28d"]}
        out[item.get("item_id") or cid] = {"candidate_id": cid, "status": status, "detail": detail}
    return out
