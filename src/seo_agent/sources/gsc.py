"""Google Search Console: search analytics by date x page x query (and date x page totals).

The date x page totals are kept separately because GSC drops anonymised queries from
query-level rows, so summing those undercounts a page's clicks and impressions.
"""
from urllib.parse import quote

from .google_api import request

API = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}"
PAGE_SIZE = 25000
DATASETS = {"page_query": ["date", "page", "query"], "page": ["date", "page"]}


def ping(token: str, site: str) -> str:
    """One call: sites.get, which confirms the property exists and the SA can read it."""
    j = request("GET", API.format(site=quote(site, safe="")), token=token)
    return f"permission={j.get('permissionLevel')}"


def fetch(token: str, site: str, start: str, end: str, dimensions: list[str]) -> list[dict]:
    url = API.format(site=quote(site, safe="")) + "/searchAnalytics/query"
    rows, start_row = [], 0
    while True:
        j = request("POST", url, token=token, json={
            "startDate": start, "endDate": end, "dimensions": dimensions, "type": "web",
            "dataState": "final", "rowLimit": PAGE_SIZE, "startRow": start_row})
        batch = j.get("rows", [])
        for r in batch:
            row = dict(zip(dimensions, r["keys"]))
            row.update(clicks=int(r["clicks"]), impressions=int(r["impressions"]),
                       position=round(r["position"], 2))
            rows.append(row)
        if len(batch) < PAGE_SIZE:
            return rows
        start_row += PAGE_SIZE
