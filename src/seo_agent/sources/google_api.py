"""Small helpers for Google APIs: retries on 429/5xx with backoff."""
import time

import requests

RETRY_STATUS = {429, 500, 502, 503, 504}


def request(method: str, url: str, *, token: str | None = None, retries: int = 4,
            sleep=time.sleep, timeout: float = 60, **kw) -> dict:
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(retries + 1):
        try:
            r = requests.request(method, url, headers=headers, timeout=timeout, **kw)
        except requests.RequestException:
            if attempt == retries:
                raise
        else:
            if r.status_code not in RETRY_STATUS or attempt == retries:
                if not r.ok:
                    # Keep Google's error message; it never contains our credentials.
                    raise requests.HTTPError(f"{r.status_code} {url.split('?')[0]}: {r.text[:300]}",
                                             response=r)
                return r.json()
        sleep(2 ** (attempt + 1))
    raise AssertionError("unreachable")
