"""A plain directory with the StateBranch read/write API (for --no-state runs and tests)."""
import json
from pathlib import Path

from .runs import RunLog


class DirStore(RunLog):
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def _file(self, rel: str) -> Path:
        p = (self.path / rel).resolve()
        if self.path.resolve() not in p.parents:
            raise ValueError(f"path escapes store: {rel}")
        return p

    def read_text(self, rel):
        p = self._file(rel)
        return p.read_text() if p.exists() else None

    def read_json(self, rel):
        t = self.read_text(rel)
        return None if t is None else json.loads(t)

    def write_text(self, rel, text):
        p = self._file(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def write_json(self, rel, data):
        self.write_text(rel, json.dumps(data, indent=2, sort_keys=True) + "\n")

    def listdir(self, rel_dir: str) -> list[str]:
        d = self._file(rel_dir) if rel_dir else self.path
        return sorted(p.name for p in d.iterdir()) if d.exists() else []
