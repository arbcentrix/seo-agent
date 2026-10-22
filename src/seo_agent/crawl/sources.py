"""Page sources for the crawler: live (polite Fetcher) or saved fixtures."""
import gzip
import hashlib
import json
from pathlib import Path

from .fetcher import Fetcher, Response


class LiveSource:
    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher

    def get(self, url: str, headers: dict | None = None) -> Response:
        return self.fetcher.get(url, headers)


class FixtureSource:
    """Replays a directory written by a capture: responses.json + files. Unknown URLs are 404."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.meta = json.loads((self.dir / "responses.json").read_text())

    def get(self, url: str, headers: dict | None = None) -> Response:
        """Like the live site: an ETag from the content, and 304 for a matching If-None-Match."""
        m = self.meta.get(url)
        if m is None:
            return Response(url, 404, final_url=url)
        path = self.dir / m["file"]
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as f:
            text = f.read()
        etag = '"%s"' % hashlib.md5(text.encode()).hexdigest()
        if (headers or {}).get("If-None-Match") == etag and m["status"] == 200:
            return Response(url, 304, {"etag": etag}, "", [], url)
        return Response(url, m["status"], {**m.get("headers", {}), "etag": etag}, text,
                        m.get("redirects", []), m.get("final_url", url), m.get("error"))
