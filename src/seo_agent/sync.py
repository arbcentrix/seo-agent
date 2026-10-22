"""Import data sources into the state branch.

GSC  gsc/page_query/<YYYY-MM>.json   date x page x query  (final data)
     gsc/page/<YYYY-MM>.json         date x page totals (includes anonymised queries)
GA4  ga4/landing/<YYYY-MM>.json      date x landingPage: sessions, engagedSessions, totalUsers, keyEvents
     ga4/key_events/<YYYY-MM>.json   date x landingPage x eventName: keyEvents
PSI  psi/<YYYY-MM-DD>.json           critical pages x {mobile, desktop}: lab + field metrics

GSC and GA4 sync every missing day in their window, so the first run is the 16-month
backfill and later runs fetch only new days plus the last few (still being finalised).
"""
import datetime as dt
from datetime import datetime, timezone

from . import security
from .sources import ga4, google_auth, gsc, psi
from .sources.daystore import DayStore, contiguous_ranges, days_to_sync

DEFAULTS = {"backfill_months": 16, "gsc_lag_days": 3, "gsc_refresh_days": 4,
            "ga4_refresh_days": 3, "psi_every_days": 7}


def months_back(day: dt.date, months: int) -> dt.date:
    y, m = divmod(day.year * 12 + day.month - 1 - months, 12)
    first = dt.date(y, m + 1, 1)
    nxt = (first + dt.timedelta(days=32)).replace(day=1)
    return first.replace(day=min(day.day, (nxt - first).days))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sync_gsc(store, token, site, today, opts, log):
    start = months_back(today, opts["backfill_months"])
    end = today - dt.timedelta(days=opts["gsc_lag_days"])
    totals = DayStore(store, "gsc/page")
    days = days_to_sync(totals.synced_days(), start, end, opts["gsc_refresh_days"])
    queries, fetched = [], {"page_query": 0, "page": 0}
    for s, e in contiguous_ranges(days):
        span = [d for d in days if s <= d <= e]
        stamp = _now()
        for name, dims in gsc.DATASETS.items():
            rows = gsc.fetch(token, site, s, e, dims)
            DayStore(store, f"gsc/{name}").put(span, rows, stamp)
            fetched[name] += len(rows)
            if name == "page_query":
                queries += [r["query"] for r in rows]
    log(f"gsc: {len(days)} days synced, {fetched['page_query']} page×query rows, {fetched['page']} page rows")
    return {"days": len(days), "rows": fetched}, security.flag(queries, "gsc_query")


def sync_ga4(store, token, property_id, today, opts, log):
    start = months_back(today, opts["backfill_months"])
    end = today - dt.timedelta(days=1)
    landing = DayStore(store, "ga4/landing")
    days = days_to_sync(landing.synced_days(), start, end, opts["ga4_refresh_days"])
    fetched = {"landing": 0, "key_events": 0}
    for s, e in contiguous_ranges(days):
        span = [d for d in days if s <= d <= e]
        stamp = _now()
        rows = ga4.fetch_landing_pages(token, property_id, s, e)
        landing.put(span, rows, stamp)
        events = ga4.fetch_key_events(token, property_id, s, e)
        DayStore(store, "ga4/key_events").put(span, events, stamp)
        fetched["landing"] += len(rows)
        fetched["key_events"] += len(events)
    log(f"ga4: {len(days)} days synced, {fetched['landing']} landing rows, {fetched['key_events']} key-event rows")
    return {"days": len(days), "rows": fetched}


def last_psi_day(store) -> str | None:
    days = [n[:10] for n in store.listdir("psi") if n.endswith(".json")]
    return max(days) if days else None


def sync_psi(store, urls, today, opts, log, force=False, runner=psi.run):
    last = last_psi_day(store)
    if not force and last and (today - dt.date.fromisoformat(last)).days < opts["psi_every_days"]:
        log(f"psi: skipped, last run {last}")
        return {"skipped": True, "last": last}
    results = []
    for url in urls:
        for strategy in ("mobile", "desktop"):
            entry = {"url": url, "strategy": strategy, "fetched_at": _now()}
            try:
                entry.update(psi.summarize(runner(url, strategy)))
            except Exception as e:
                entry["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            results.append(entry)
    store.write_json(f"psi/{today.isoformat()}.json", results)
    errors = sum(1 for r in results if "error" in r)
    log(f"psi: {len(results)} runs, {errors} errors")
    return {"skipped": False, "runs": len(results), "errors": errors}


def run_sync(cfg, store, only=("gsc", "ga4", "psi"), force_psi=False, today=None, log=print) -> dict:
    """Sync each source; a failing source is reported, not fatal for the others."""
    today = today or dt.date.today()
    opts = {**DEFAULTS, **(cfg.site.get("sources") or {})}
    env = cfg.env
    summary, suspicious = {}, []
    token = None

    def get_token():
        nonlocal token
        token = token or google_auth.get_token(env.google_sa_json_b64)
        return token

    for name in only:
        try:
            if name == "gsc":
                summary["gsc"], flags = sync_gsc(store, get_token(), env.gsc_site, today, opts, log)
                suspicious += flags
            elif name == "ga4":
                summary["ga4"] = sync_ga4(store, get_token(), env.ga4_property_id, today, opts, log)
            elif name == "psi":
                summary["psi"] = sync_psi(store, cfg.critical_urls, today, opts, log, force_psi)
        except Exception as e:
            summary[name] = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
            log(f"{name}: ERROR {summary[name]['error']}")
    return {"sources": summary, "suspicious": suspicious}
