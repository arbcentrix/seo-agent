"""Polite fetcher: one request per interval, fixed user agent, redirects recorded."""
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin

import requests

from ..http import DEFAULT_UA

MAX_REDIRECTS = 5


@dataclass
class Response:
    url: str
    status: int | None
    headers: dict = field(default_factory=dict)
    text: str = ""
    redirects: list = field(default_factory=list)  # [(status, location), ...]
    final_url: str | None = None
    error: str | None = None


class Fetcher:
    def __init__(self, user_agent: str = DEFAULT_UA, interval: float = 1.0, timeout: float = 30,
                 session: requests.Session | None = None, sleep=time.sleep, clock=time.monotonic):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.interval = interval
        self.timeout = timeout
        self._sleep, self._clock = sleep, clock
        self._last = None

    def _wait(self):
        if self._last is not None:
            gap = self.interval - (self._clock() - self._last)
            if gap > 0:
                self._sleep(gap)
        self._last = self._clock()

    def get(self, url: str, headers: dict | None = None) -> Response:
        """GET without auto-redirects; each hop counts as one polite request.

        headers (e.g. If-None-Match) go on the first hop only; a 304 comes back as status 304.
        """
        redirects, current = [], url
        try:
            for hop in range(MAX_REDIRECTS + 1):
                self._wait()
                r = self.session.get(current, timeout=self.timeout, allow_redirects=False,
                                     headers=headers if hop == 0 and headers else None)
                if r.is_redirect and "location" in r.headers:
                    nxt = urljoin(current, r.headers["location"])
                    redirects.append([r.status_code, nxt])
                    current = nxt
                    continue
                return Response(url, r.status_code, {k.lower(): v for k, v in r.headers.items()},
                                r.text, redirects, current)
            return Response(url, None, redirects=redirects, final_url=current, error="too many redirects")
        except requests.RequestException as e:
            return Response(url, None, redirects=redirects, error=f"{type(e).__name__}: {e}"[:300])
