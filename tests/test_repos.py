"""`daydag.repos`: the refresh every recap runs first.

The working-copy half touches a tree a human works in, so every way it could
lose work is pinned: it fast-forwards a clean branch and leaves everything else
exactly where it was.
"""

from __future__ import annotations

import subprocess

import pytest

from daydag import repos
from daydag.repos import refresh_copy, working_copies


def _git(repo, git_env, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, env=git_env, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def clone(tmp_path, git_env, make_origin):
    """An origin with one commit and a working copy of it, upstream set."""
    origin = make_origin("origin")
    copy = tmp_path / "copy"
    subprocess.run(["git", "clone", "-q", str(origin), str(copy)], env=git_env, check=True)
    _git(copy, git_env, "config", "user.email", "t@example.com")
    _git(copy, git_env, "config", "user.name", "t")
    return origin, copy


def test_fast_forwards_a_clean_branch_behind_its_upstream(clone, git_env, add_commit):
    origin, copy = clone
    add_commit(origin, "one")
    add_commit(origin, "two")

    result = refresh_copy(copy)

    assert result.outcome == "fast-forwarded 2 commits"
    assert result.pulled == 2
    assert _git(copy, git_env, "rev-parse", "HEAD") == _git(origin, git_env, "rev-parse", "HEAD")


def test_up_to_date_is_said_plainly(clone):
    _, copy = clone
    assert refresh_copy(copy).outcome == "up to date"


def test_uncommitted_changes_are_never_pulled_over(clone, git_env, add_commit):
    origin, copy = clone
    before = _git(copy, git_env, "rev-parse", "HEAD")
    add_commit(origin, "upstream")
    (copy / "f").write_text("his edit")

    result = refresh_copy(copy)

    assert "uncommitted changes, 1 commits not pulled" in result.outcome
    assert _git(copy, git_env, "rev-parse", "HEAD") == before
    assert (copy / "f").read_text() == "his edit"


def test_a_diverged_branch_is_left_alone(clone, git_env, add_commit):
    origin, copy = clone
    add_commit(origin, "upstream")
    add_commit(copy, "local")
    before = _git(copy, git_env, "rev-parse", "HEAD")

    result = refresh_copy(copy)

    assert result.outcome == "fetched; diverged (1 ahead, 1 behind), not pulled"
    assert _git(copy, git_env, "rev-parse", "HEAD") == before


def test_a_detached_head_is_left_alone(clone, git_env, add_commit):
    origin, copy = clone
    _git(copy, git_env, "checkout", "-q", "--detach")
    add_commit(origin, "upstream")

    assert refresh_copy(copy).outcome == "fetched; detached HEAD, left as is"


def test_a_branch_without_upstream_is_left_alone(clone, git_env):
    _, copy = clone
    _git(copy, git_env, "checkout", "-q", "-b", "scratch")

    result = refresh_copy(copy)

    assert result.branch == "scratch"
    assert result.outcome == "fetched; no upstream branch, left as is"


def test_an_unreachable_origin_is_one_line(clone):
    origin, copy = clone
    origin.rename(origin.with_name("gone"))

    assert refresh_copy(copy).outcome.startswith("couldn't fetch - ")


def test_only_clones_of_a_watched_org_are_picked_up(tmp_path, git_env):
    root = tmp_path / "Documents"
    for name, url in (
        ("ours-ssh", "git@github.com:CreateCo/a.git"),
        ("ours-https", "https://github.com/createco/b"),
        ("theirs", "https://github.com/Someone/c"),
    ):
        repo = root / name
        repo.mkdir(parents=True)
        _git(repo, git_env, "init", "-q")
        _git(repo, git_env, "remote", "add", "origin", url)
    (root / "not-a-repo").mkdir()

    found = working_copies([root, tmp_path / "missing"], {"CreateCo"})

    assert [path.name for path in found] == ["ours-https", "ours-ssh"]


def test_no_subcommand_is_a_usage_error(capsys):
    assert repos.main([]) == 2
    assert "usage" in capsys.readouterr().err


def test_no_vault_is_a_config_error_not_a_crash(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text(
        "SLACK_USER_PRINCIPAL=UPRINCIPAL1\nEMAIL_PRINCIPAL=principal@x.com\n"
        f"MIRROR_DIR={tmp_path / 'mirrors'}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert repos.main(["sync"]) == 2
    assert "couldn't refresh repos" in capsys.readouterr().err
