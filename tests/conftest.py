import subprocess

import pytest


@pytest.fixture
def fake_repo(tmp_path):
    """A tiny bare-ish repo with two merge commits, for cursor tests."""
    repo = tmp_path / "r.git"
    repo.mkdir()
    run = lambda *a: subprocess.run(a, cwd=repo, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (repo / "f").write_text("0")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    for n in (412, 413):
        (repo / "f").write_text(str(n))
        run("git", "add", "-A")
        run("git", "commit", "-qm", f"Merge PR #{n}")
    return repo
