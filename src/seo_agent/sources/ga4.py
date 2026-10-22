"""GA4 Data API: landing pages by day and channel, and key events by landing page and event name.

Channel is sessionDefaultChannelGroup, so reports can isolate "Organic Search".
"""
from .google_api import request

API = "https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport"
PAGE_SIZE = 100000
LANDING_METRICS = ["sessions", "engagedSessions", "totalUsers", "keyEvents"]


def _pid(property_id: str) -> str:
    return property_id.removeprefix("properties/")


def ping(token: str, property_id: str) -> str:
    """One call: a one-row runReport."""
    j = request("POST", API.format(pid=_pid(property_id)), token=token, json={
        "dateRanges": [{"startDate": "7daysAgo", "endDate": "yesterday"}],
        "metrics": [{"name": "sessions"}], "limit": 1})
    return f"rows={len(j.get('rows', []))}"


def _report(token, property_id, start, end, dimensions, metrics, metric_filter=None):
    rows, offset = [], 0
    while True:
        body = {"dateRanges": [{"startDate": start, "endDate": end}],
                "dimensions": [{"name": d} for d in dimensions],
                "metrics": [{"name": m} for m in metrics],
                "limit": PAGE_SIZE, "offset": offset, "keepEmptyRows": False}
        if metric_filter:
            body["metricFilter"] = metric_filter
        j = request("POST", API.format(pid=_pid(property_id)), token=token, json=body)
        for r in j.get("rows", []):
            row = {d: v["value"] for d, v in zip(dimensions, r["dimensionValues"])}
            d = row.pop("date")
            row = {"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", **row}
            for m, v in zip(metrics, r["metricValues"]):
                row[m] = float(v["value"]) if "." in v["value"] else int(v["value"])
            rows.append(row)
        offset += PAGE_SIZE
        if offset >= j.get("rowCount", 0):
            return rows


def fetch_landing_pages(token, property_id, start, end) -> list[dict]:
    return _report(token, property_id, start, end, ["date", "landingPage", "sessionDefaultChannelGroup"],
                   LANDING_METRICS)


def fetch_key_events(token, property_id, start, end) -> list[dict]:
    return _report(token, property_id, start, end,
                   ["date", "landingPage", "sessionDefaultChannelGroup", "eventName"], ["keyEvents"],
                   {"filter": {"fieldName": "keyEvents",
                               "numericFilter": {"operation": "GREATER_THAN",
                                                 "value": {"int64Value": "0"}}}})
