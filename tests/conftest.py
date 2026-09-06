import itertools
import os
import subprocess

import pytest


@pytest.fixture
def fake_repo(tmp_path):
    """A tiny bare-ish repo with two merge commits, for cursor tests."""
    repo = tmp_path / "r.git"
    repo.mkdir()
    run = lambda *a: _run(repo, *a)  # noqa: E731
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


def _hermetic_git_env():
    """An environment where ``git`` sees only the repo it is pointed at.

    ``GIT_DIR`` and friends override ``cwd``, and the suite runs from the
    ``pre-push`` hook, which exports them - inherited, every command below would
    be aimed at the outer repo. User config is neutralised for the same reason:
    a global ``core.hooksPath`` would run this repo's hooks inside the fixture.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    return env


@pytest.fixture
def git_env():
    """Environment for any test that shells out to ``git``. See above.

    Every such call takes it. Without it an inherited ``GIT_DIR`` points the
    command at the outer repo, so an assertion about a fixture mirror answers
    about this repository instead - and a guardrail passes vacuously.
    """
    return _hermetic_git_env()


def _run(repo, *args):
    subprocess.run(args, cwd=repo, check=True, capture_output=True, env=_hermetic_git_env())


@pytest.fixture
def add_commit():
    """Add one commit to a local repo. Fixtures stay offline by construction."""
    written = itertools.count()

    def _add(repo, message):
        # The counter keeps the tree changing, so two commits may share a
        # message without git refusing an empty commit.
        (repo / "f").write_text(f"{next(written)} {message}")
        _run(repo, "git", "add", "-A")
        _run(repo, "git", "commit", "-qm", message)
        return repo

    return _add


@pytest.fixture
def make_origin(tmp_path, add_commit):
    """Factory for throwaway local repos to clone from.

    Provisioning tests exercise a real `git clone --mirror`, because the
    properties under test - bareness, the disabled push url - are properties of
    what git actually wrote. Cloning a local path keeps that off the network.
    """

    def _make(name, messages=("base",)):
        repo = tmp_path / "origins" / name
        repo.mkdir(parents=True, exist_ok=True)
        _run(repo, "git", "init", "-q", "-b", "main")
        _run(repo, "git", "config", "user.email", "t@example.com")
        _run(repo, "git", "config", "user.name", "t")
        for message in messages:
            add_commit(repo, message)
        return repo

    return _make
