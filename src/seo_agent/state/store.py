"""Read/write the state branch through a dedicated git worktree.

The state branch is an orphan branch holding snapshots, runs.jsonl and out/.
This is the only place the agent commits to (CLAUDE.md).
"""
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

RUNS_FILE = "runs.jsonl"
BACKOFF = (2, 4, 8, 16)


class StateError(Exception):
    pass


def _git(cwd, *args, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise StateError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r


class StateBranch:
    def __init__(self, repo: Path, branch: str = "state", worktree: Path | None = None,
                 remote: str = "origin"):
        self.repo = Path(repo)
        self.branch = branch
        self.remote = remote
        self.path = Path(worktree) if worktree else self.repo / ".state-worktree"

    # -- setup ---------------------------------------------------------
    def open(self) -> "StateBranch":
        if (self.path / ".git").exists():
            return self
        have_remote = _git(self.repo, "fetch", self.remote, self.branch, check=False).returncode == 0
        if have_remote:
            _git(self.repo, "worktree", "add", "-B", self.branch, str(self.path),
                 f"{self.remote}/{self.branch}")
        else:
            _git(self.repo, "worktree", "add", "--detach", str(self.path))
            _git(self.path, "checkout", "--orphan", self.branch)
            _git(self.path, "rm", "-rf", "--quiet", ".", check=False)
            _git(self.path, "commit", "--allow-empty", "-m", "Initialise state branch")
        return self

    # -- read/write ----------------------------------------------------
    def _file(self, rel: str) -> Path:
        p = (self.path / rel).resolve()
        if self.path.resolve() not in p.parents:
            raise StateError(f"path escapes state branch: {rel}")
        return p

    def read_text(self, rel: str) -> str | None:
        p = self._file(rel)
        return p.read_text() if p.exists() else None

    def read_json(self, rel: str):
        text = self.read_text(rel)
        return None if text is None else json.loads(text)

    def write_text(self, rel: str, text: str) -> None:
        p = self._file(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def write_json(self, rel: str, data) -> None:
        self.write_text(rel, json.dumps(data, indent=2, sort_keys=True) + "\n")

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

    def read_runs(self) -> list[dict]:
        text = self.read_text(RUNS_FILE) or ""
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    # -- publish -------------------------------------------------------
    def commit(self, message: str) -> bool:
        _git(self.path, "add", "-A")
        if not _git(self.path, "status", "--porcelain").stdout.strip():
            return False
        _git(self.path, "commit", "-m", message)
        return True

    def push(self) -> None:
        err = ""
        for delay in (0, *BACKOFF):
            time.sleep(delay)
            r = _git(self.path, "push", "-u", self.remote, self.branch, check=False)
            if r.returncode == 0:
                return
            err = r.stderr.strip()
        raise StateError(f"push failed: {err}")

    def commit_and_push(self, message: str) -> bool:
        changed = self.commit(message)
        if changed:
            self.push()
        return changed
