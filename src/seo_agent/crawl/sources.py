"""Page sources for the crawler: live (polite Fetcher) or saved fixtures."""
import gzip
import json
from pathlib import Path

from .fetcher import Fetcher, Response


class LiveSource:
    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher

    def get(self, url: str) -> Response:
        return self.fetcher.get(url)


class FixtureSource:
    """Replays a directory written by a capture: responses.json + files. Unknown URLs are 404."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.meta = json.loads((self.dir / "responses.json").read_text())

    def get(self, url: str) -> Response:
        m = self.meta.get(url)
        if m is None:
            return Response(url, 404, final_url=url)
        path = self.dir / m["file"]
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as f:
            text = f.read()
        return Response(url, m["status"], m.get("headers", {}), text, m.get("redirects", []),
                        m.get("final_url", url), m.get("error"))
