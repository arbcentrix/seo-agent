"""runs.jsonl: one line per run, shared by StateBranch and DirStore."""
import json
from datetime import datetime, timezone

RUNS_FILE = "runs.jsonl"


class RunLog:
    """Mixin; the class provides _file(rel) -> Path."""

    def append_run(self, command: str, status: str = "ok", findings: int = 0,
                   suspicious: list | None = None, **extra) -> dict:
        """Append one line to runs.jsonl. Call on every run, even empty ones."""
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "command": command, "status": status, "findings": findings,
                 "suspicious": suspicious or [], **extra}
        p = self._file(RUNS_FILE)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def close_run(self, command: str, **fields) -> dict:
        """Merge Claude's outcome into this run's line (the newest open line for `command`).

        Scripts append the line; the prompt's last step closes it, so each run is one line.
        If the script died before writing, a new line is appended instead.
        """
        p = self._file(RUNS_FILE)
        lines = p.read_text().splitlines() if p.exists() else []
        for i in range(len(lines) - 1, -1, -1):
            entry = json.loads(lines[i])
            if entry.get("command") == command and not entry.get("closed"):
                suspicious = entry.get("suspicious", []) + (fields.pop("suspicious", None) or [])
                entry.update(fields, suspicious=suspicious, closed=True)
                lines[i] = json.dumps(entry, sort_keys=True)
                p.write_text("\n".join(lines) + "\n")
                return entry
        return self.append_run(command, closed=True, **fields)

    def listdir(self, rel_dir: str) -> list[str]:
        d = self._file(rel_dir)
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    def read_runs(self) -> list[dict]:
        text = self.read_text(RUNS_FILE) or ""
        return [json.loads(line) for line in text.splitlines() if line.strip()]
