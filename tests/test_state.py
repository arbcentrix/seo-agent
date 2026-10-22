import subprocess

import pytest

from seo_agent.state import StateBranch, StateError


def git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repos(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    work = tmp_path / "work"
    git(tmp_path, "clone", str(remote), str(work))
    git(work, "config", "user.email", "t@x.com")
    git(work, "config", "user.name", "t")
    git(work, "commit", "--allow-empty", "-m", "init")
    git(work, "push", "origin", "HEAD")
    return remote, work


def test_roundtrip_and_runs(repos, tmp_path):
    remote, work = repos
    s = StateBranch(work, "state", tmp_path / "wt").open()
    s.write_json("snap/a.json", {"x": 1})
    s.append_run("daily")
    s.append_run("watch", findings=2, suspicious=["prompt injection on /x"])
    assert s.commit_and_push("run")
    assert not s.commit_and_push("noop")

    clone = tmp_path / "clone"
    git(tmp_path, "clone", str(remote), str(clone))
    git(clone, "config", "user.email", "t@x.com")
    git(clone, "config", "user.name", "t")
    s2 = StateBranch(clone, "state", tmp_path / "wt2").open()
    assert s2.read_json("snap/a.json") == {"x": 1}
    assert [r["command"] for r in s2.read_runs()] == ["daily", "watch"]
    assert s2.read_json("missing.json") is None


def test_path_escape_rejected(repos, tmp_path):
    s = StateBranch(repos[1], "state", tmp_path / "wt").open()
    with pytest.raises(StateError):
        s.write_text("../evil", "x")
