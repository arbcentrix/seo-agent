"""Aggregations over stored GSC/GA4 rows. Every number in weekly.json comes from here."""
import datetime as dt
from collections import defaultdict

from ..crawl.parser import normalize_url


def window(end: dt.date, days: int, offset: int = 0) -> list[str]:
    """[start, end] of `days` days ending `offset` days before `end`."""
    last = end - dt.timedelta(days=offset)
    return [(last - dt.timedelta(days=days - 1)).isoformat(), last.isoformat()]


def ratio(a, b, nd=4):
    return round(a / b, nd) if b else None


def gsc_metrics(rows) -> dict:
    clicks = sum(r["clicks"] for r in rows)
    impr = sum(r["impressions"] for r in rows)
    pos = ratio(sum(r["position"] * r["impressions"] for r in rows), impr, 2)
    return {"clicks": clicks, "impressions": impr, "ctr": ratio(clicks, impr), "position": pos}


def group(rows, *keys) -> dict:
    out = defaultdict(list)
    for r in rows:
        k = tuple(normalize_url(r[x]) if x == "page" else r[x] for x in keys)
        out[k if len(keys) > 1 else k[0]].append(r)
    return out


def delta(now, before) -> dict:
    """{metric: {"now", "before", "change", "change_pct"}} for numeric metrics."""
    out = {}
    for k, v in now.items():
        b = before.get(k)
        if isinstance(v, (int, float)) or isinstance(b, (int, float)):
            change = round(v - b, 4) if v is not None and b is not None else None
            out[k] = {"now": v, "before": b, "change": change,
                      "change_pct": ratio(change, b, 4) if change is not None and b else None}
    return out


def ga4_dates(cfg) -> dict:
    """The two GA4 start dates from site.yaml (None when not set); shown on every report."""
    g = cfg.site.get("ga4") or {}
    return {k: str(g[k]) if g.get(k) else None for k in ("data_from", "key_events_from")}


def _from(rows, start):
    return [r for r in rows if not start or r["date"] >= str(start)]


def ga4_metrics(landing_rows, event_rows, channel: str | None, dates: dict | None = None) -> dict:
    """GA4 counts. Sessions count from dates["data_from"] (new property go-live); key events
    count from dates["key_events_from"]. Rows before those dates are ignored."""
    dates = dates or {}
    lr = [r for r in landing_rows if channel is None or r.get("sessionDefaultChannelGroup") == channel]
    er = [r for r in event_rows if channel is None or r.get("sessionDefaultChannelGroup") == channel]
    ke_from = dates.get("key_events_from") or dates.get("data_from")
    sess_rows, ke_landing, er = _from(lr, dates.get("data_from")), _from(lr, ke_from), _from(er, ke_from)
    sessions = sum(r["sessions"] for r in sess_rows)
    engaged = sum(r["engagedSessions"] for r in sess_rows)
    events = defaultdict(float)
    for r in er:
        events[r["eventName"]] += r["keyEvents"]
    return {"sessions": sessions, "engaged_sessions": engaged, "engagement_rate": ratio(engaged, sessions),
            "key_events": round(sum(r["keyEvents"] for r in ke_landing), 2),
            "key_events_by_name": {k: round(v, 2) for k, v in sorted(events.items())}}
