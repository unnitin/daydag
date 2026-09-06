import os
import subprocess

import pytest


def _hermetic_git_env() -> dict[str, str]:
    """An environment where ``git`` sees only the repo it is pointed at.

    The suite runs from the ``pre-push`` hook, and git exports ``GIT_DIR`` and
    friends into its hooks. Inherited, they redirect every command below at the
    outer repo, and the fixture's first commit fails - so a perfectly green
    branch is unpushable, and the failure names a test that has nothing to do
    with the change. User config is neutralised for the same reason: a global
    ``core.hooksPath`` would run this repo's hooks inside the fixture.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    return env


@pytest.fixture
def git_env():
    """Environment for any test that shells out to ``git``. See above."""
    return _hermetic_git_env()


@pytest.fixture
def fake_repo(tmp_path):
    """A tiny bare-ish repo with two merge commits, for cursor tests."""
    repo = tmp_path / "r.git"
    repo.mkdir()
    env = _hermetic_git_env()

    def run(*args):
        return subprocess.run(args, cwd=repo, check=True, capture_output=True, text=True, env=env)

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
