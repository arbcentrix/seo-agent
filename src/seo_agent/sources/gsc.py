from urllib.parse import quote

import requests


def ping(token: str, site: str) -> str:
    """One call: sites.get, which confirms the property exists and the SA can read it."""
    url = f"https://searchconsole.googleapis.com/webmasters/v3/sites/{quote(site, safe='')}"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return f"permission={r.json().get('permissionLevel')}"
