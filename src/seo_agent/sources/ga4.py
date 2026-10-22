import requests


def ping(token: str, property_id: str) -> str:
    """One call: a one-row runReport."""
    pid = property_id.removeprefix("properties/")
    r = requests.post(
        f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport",
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
        json={"dateRanges": [{"startDate": "7daysAgo", "endDate": "yesterday"}],
              "metrics": [{"name": "sessions"}], "limit": 1})
    r.raise_for_status()
    return f"rows={len(r.json().get('rows', []))}"
