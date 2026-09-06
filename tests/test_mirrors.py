"""Mirror provisioning: clone, disable push, refresh (ARCHITECTURE, "Shape of the clones").

The read half of the pulse assumed a mirror already existed. This is the half
that makes one, and the properties that matter are properties of what git
actually wrote on disk - so these tests run real `git clone --mirror` against
local throwaway repos rather than mocking subprocess. Nothing here touches the
network.
"""

import shutil
import subprocess

import pytest

from daydag.config import ConfigError, Identities
from daydag.pulse import (
    GIT_TIMED_OUT,
    NO_PUSH,
    MirrorStore,
    MirrorUnavailable,
    Pulse,
    PulseError,
    WatchedRepo,
    _run_git,
    github_url,
    mirror_root,
    read_watchlist,
)
from daydag.state import StateFolder


@pytest.fixture
def git_out(git_env):
    """Read something back out of a repo on disk, hermetically.

    Every call carries `git_env`: the suite runs from the pre-push hook, which
    exports GIT_DIR, and GIT_DIR overrides cwd - so without it these assertions
    would answer about the outer repo and the guardrails would pass vacuously.
    """

    def _out(cwd, *args):
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, env=git_env
        )
        return result.returncode, result.stdout.strip()

    return _out


class Origins(dict):
    """Local stand-ins for the github repos a watchlist names, keyed by repo name."""

    def __init__(self, make_origin):
        super().__init__()
        self._make = make_origin

    def add(self, name, messages=("base",)):
        self[name] = self._make(name, messages)
        return self[name]


@pytest.fixture
def origins(make_origin):
    return Origins(make_origin)


@pytest.fixture
def store(tmp_path, origins):
    """A store whose clone urls resolve to local origins.

    `url_for` is the seam that keeps these tests offline: in production it is
    `github_url`; here it hands back a path on tmpfs. A repo with no origin
    resolves to a path that does not exist, which is what an absent wiki or a
    repo the token cannot see looks like to git.
    """

    def url_for(repo):
        absent = tmp_path / "origins" / f"{repo.name}-absent"
        return str(origins.get(repo.slug) or origins.get(repo.name, absent))

    return MirrorStore(tmp_path / "mirrors", url_for=url_for)


# -- watchlist parsing ----------------------------------------------------


def test_reads_the_repo_list_from_the_watchlist_markdown(tmp_path):
    path = tmp_path / "Watchlist.md"
    path.write_text(
        "# Watchlist\n\n"
        "## repos\n"
        "- ExampleOrg/service-a\n"
        "- `ExampleOrg/service-b`\n"
        "- https://github.com/ExampleOrg/service-c.git\n"
        "- [service-d](https://github.com/ExampleOrg/service-d) · pod discovery\n"
        "\n## jira\n"
        "- ABC · board 4\n"
        "\n## channels\n"
        "- ExampleOrg/not-a-repo-either\n",
        encoding="utf-8",
    )
    assert [r.slug for r in read_watchlist(path).repos] == [
        "ExampleOrg/service-a",
        "ExampleOrg/service-b",
        "ExampleOrg/service-c",
        "ExampleOrg/service-d",
    ]


def test_a_wiki_marker_on_a_line_adds_the_wiki_repo(tmp_path):
    path = tmp_path / "Watchlist.md"
    path.write_text(
        "## repos\n- ExampleOrg/platform · wiki\n- ExampleOrg/plain\n", encoding="utf-8"
    )
    repos = {r.name: r for r in read_watchlist(path).repos}
    assert repos["platform"].wiki is True
    assert repos["plain"].wiki is False


def test_the_word_wiki_in_a_trailing_note_is_not_a_marker(tmp_path):
    """A marker is a field of its own, not a word anywhere on the line."""
    path = tmp_path / "Watchlist.md"
    path.write_text(
        "## repos\n- ExampleOrg/service-a · see the wiki for the runbook\n", encoding="utf-8"
    )
    assert read_watchlist(path).repos[0].wiki is False


def test_a_pasted_browser_url_with_a_subpath_still_names_the_repo(tmp_path):
    path = tmp_path / "Watchlist.md"
    path.write_text(
        "## repos\n- https://github.com/ExampleOrg/service-a/tree/main · locked\n",
        encoding="utf-8",
    )
    assert [r.slug for r in read_watchlist(path).repos] == ["ExampleOrg/service-a"]


def test_a_line_that_meant_to_name_a_repo_but_does_not_parse_is_surfaced(tmp_path):
    """Otherwise a typo'd slug stops being watched and nothing ever says so."""
    path = tmp_path / "Watchlist.md"
    path.write_text("## repos\n- ExampleOrg / service-a\n", encoding="utf-8")

    watchlist = read_watchlist(path)

    assert watchlist.repos == []
    assert watchlist.unparsed == ["ExampleOrg / service-a"]
    assert "not watched" in Pulse(unreadable=watchlist.unparsed).render()


def test_only_the_repos_heading_opens_the_repos_block(tmp_path):
    """`startswith("repo")` would put `## Reporting cadence` through this parser."""
    path = tmp_path / "Watchlist.md"
    path.write_text(
        "## repos\n- ExampleOrg/service-a\n\n## Reporting cadence\n- weekly / friday\n",
        encoding="utf-8",
    )
    watchlist = read_watchlist(path)
    assert [r.slug for r in watchlist.repos] == ["ExampleOrg/service-a"]
    assert watchlist.unparsed == []


def test_a_github_url_that_is_not_a_repo_is_not_mirrored(tmp_path):
    """`github.com/orgs/<org>/repositories` is a page, not a repo to clone."""
    path = tmp_path / "Watchlist.md"
    path.write_text(
        "## repos\n- https://github.com/orgs/ExampleOrg/repositories\n", encoding="utf-8"
    )
    watchlist = read_watchlist(path)
    assert watchlist.repos == []
    assert watchlist.unparsed == ["https://github.com/orgs/ExampleOrg/repositories"]


def test_a_rule_ends_the_repos_block(tmp_path):
    """A hand-edited file separates sections with `---` and bold labels too."""
    path = tmp_path / "Watchlist.md"
    path.write_text(
        "## repos\n- ExampleOrg/service-a\n\n---\n\n**jira**\n- ABC/board-4\n",
        encoding="utf-8",
    )
    watchlist = read_watchlist(path)
    assert [r.slug for r in watchlist.repos] == ["ExampleOrg/service-a"]
    assert watchlist.unparsed == []


def test_a_missing_watchlist_is_a_pulse_error_not_a_traceback(tmp_path):
    """The vault is iCloud-synced; an evicted placeholder must not kill the run."""
    with pytest.raises(PulseError):
        read_watchlist(tmp_path / "gone" / "Watchlist.md")


def test_prose_and_empty_bullets_in_the_repos_block_are_skipped(tmp_path):
    """Watchlist.md is Nitin's to edit by hand, so it will contain prose."""
    path = tmp_path / "Watchlist.md"
    path.write_text(
        "## repos\n"
        "these are the ones worth a fetch:\n"
        "- (none for the analytics pod yet)\n"
        "- ExampleOrg/service-a\n",
        encoding="utf-8",
    )
    assert [r.slug for r in read_watchlist(path).repos] == ["ExampleOrg/service-a"]


def test_the_folder_statefolder_lays_out_parses_as_an_empty_list(tmp_path):
    """The parser reads the real file StateFolder.create writes, not a mock of it."""
    folder = StateFolder.create(tmp_path / "DayDAG")
    assert read_watchlist(folder.watchlist_path).repos == []


# -- config ---------------------------------------------------------------


def test_the_mirror_directory_comes_from_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    env = tmp_path / ".env"
    env.write_text("MIRROR_DIR=$HOME/.local/share/daydag/repos\n", encoding="utf-8")
    root = mirror_root(Identities.from_file(env))
    assert root == tmp_path / "home" / ".local/share/daydag/repos"


def test_a_missing_mirror_directory_is_a_config_error_not_a_default(tmp_path):
    """A silent default would put a few hundred MB of git objects somewhere unowned."""
    env = tmp_path / ".env"
    env.write_text("VAULT_ROOT=/somewhere\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        mirror_root(Identities.from_file(env))


@pytest.mark.parametrize("value", ["", "$NOT_SET_ANYWHERE/repos"])
def test_an_empty_or_unresolved_mirror_directory_is_refused(tmp_path, value):
    """Either one resolves to somewhere unowned - the process cwd, or a `$VAR` folder."""
    env = tmp_path / ".env"
    env.write_text(f"MIRROR_DIR={value}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        mirror_root(Identities.from_file(env))


def test_a_relative_mirror_directory_clones_where_it_says(tmp_path, monkeypatch, make_origin):
    """`MIRROR_DIR=repos` is legal config, and must not land the clone elsewhere."""
    origin = make_origin("service-a")
    monkeypatch.chdir(tmp_path)
    store = MirrorStore("mirrors", url_for=lambda repo: str(origin))

    report = store.sync([WatchedRepo("ExampleOrg", "service-a")])

    assert report.unavailable == []
    assert (tmp_path / "mirrors" / "ExampleOrg" / "service-a.git" / "HEAD").is_file()


def test_a_hung_git_call_comes_back_as_a_failure_not_an_exception():
    """A credential prompt with output captured is an invisible, unbounded wait."""
    result = _run_git(["version"], timeout=0.000001)
    assert result.returncode == GIT_TIMED_OUT


def test_only_gits_own_words_class_a_repo_as_absent():
    """ "Auth failed" is not "no such repo", and treating it as one hides an outage."""
    detail = "fatal: repository 'https://github.com/ExampleOrg/platform.wiki.git/' not found"
    absent = MirrorUnavailable("ExampleOrg/platform.wiki", "clone", "reason", detail)
    refused = MirrorUnavailable(
        "ExampleOrg/platform.wiki", "clone", "reason", "fatal: Authentication failed"
    )
    assert absent.repo_is_absent is True
    assert refused.repo_is_absent is False


def test_clone_urls_are_github_https_and_the_wiki_is_its_own_repo():
    repo = WatchedRepo("ExampleOrg", "platform", wiki=True)
    assert github_url(repo) == "https://github.com/ExampleOrg/platform.git"
    assert github_url(repo.wiki_repo()) == "https://github.com/ExampleOrg/platform.wiki.git"


# -- provisioning ---------------------------------------------------------


def test_a_new_repo_on_the_list_is_cloned_bare_on_first_sight(store, origins, git_out):
    origins.add("service-a")
    report = store.sync([WatchedRepo("ExampleOrg", "service-a")])

    path = store.path_for(WatchedRepo("ExampleOrg", "service-a"))
    assert path.name == "service-a.git" and path.is_dir()
    assert report.cloned == ["ExampleOrg/service-a"]
    assert git_out(path, "rev-parse", "--is-bare-repository") == (0, "true")
    assert git_out(path, "config", "--get", "remote.origin.mirror") == (0, "true")
    assert not (path / "f").exists(), "a working tree invites the agent to think it can edit"


def test_an_existing_mirror_is_refreshed_rather_than_recloned(store, add_commit, origins, git_out):
    repo = WatchedRepo("ExampleOrg", "service-a")
    origin = origins.add("service-a")
    first = store.sync([repo])
    head = git_out(store.path_for(repo), "rev-parse", "HEAD")[1]

    add_commit(origin, "DATA-812 consolidate compute engines")
    second = store.sync([repo], cursors={repo.slug: head})

    assert second.cloned == [], "re-cloning on every run would be a full re-download"
    assert [i.title for i in second.mirrors[0].merges_since_cursor()] == [
        "DATA-812 consolidate compute engines"
    ]
    assert first.mirrors[0].path == second.mirrors[0].path


def test_fetch_prunes_refs_deleted_upstream(store, add_commit, origins, git_out, git_env):
    repo = WatchedRepo("ExampleOrg", "service-a")
    origin = origins.add("service-a")
    subprocess.run(
        ["git", "branch", "gone"], cwd=origin, check=True, capture_output=True, env=git_env
    )
    store.sync([repo])
    assert git_out(store.path_for(repo), "rev-parse", "--verify", "refs/heads/gone")[0] == 0

    subprocess.run(
        ["git", "branch", "-D", "gone"], cwd=origin, check=True, capture_output=True, env=git_env
    )
    store.sync([repo])
    assert git_out(store.path_for(repo), "rev-parse", "--verify", "refs/heads/gone")[0] != 0


def test_a_freshly_cloned_repo_reports_nothing_rather_than_all_of_history(store, origins):
    """First sight is not news. The delta starts at the next run."""
    origins.add("service-a", messages=("base", "Merge PR #412", "Merge PR #413"))
    report = store.sync([WatchedRepo("ExampleOrg", "service-a")])
    assert Pulse(mirrors=report.mirrors).render() == ""


def test_a_repo_dropped_from_the_list_is_left_on_disk(store, origins):
    a, b = WatchedRepo("ExampleOrg", "service-a"), WatchedRepo("ExampleOrg", "service-b")
    origins.add("service-a")
    origins.add("service-b")
    store.sync([a, b])

    report = store.sync([a])

    assert store.path_for(b).is_dir(), "removing a line from config must not delete objects"
    assert [m.path for m in report.mirrors] == [store.path_for(a)]


def test_same_named_repos_in_two_orgs_get_their_own_mirrors(store, origins, make_origin, git_out):
    """Repo names are unique per org. A flat directory would merge two histories."""
    origins.add("service-a")
    origins["OtherOrg/service-a"] = make_origin("other-org-copy", messages=("other org's base",))

    report = store.sync(
        [WatchedRepo("ExampleOrg", "service-a"), WatchedRepo("OtherOrg", "service-a")]
    )

    assert sorted(report.cloned) == ["ExampleOrg/service-a", "OtherOrg/service-a"]
    assert (store.root / "ExampleOrg" / "service-a.git").is_dir()
    assert (store.root / "OtherOrg" / "service-a.git").is_dir()
    subjects = {git_out(m.path, "log", "-1", "--format=%s")[1] for m in report.mirrors}
    assert subjects == {"base", "other org's base"}, "one org's landings reported under another"


# -- the wiki -------------------------------------------------------------


def test_a_wiki_marked_repo_mirrors_the_wiki_repo_too(store, origins):
    origins.add("platform")
    origins.add("platform.wiki", messages=("Home.md",))

    report = store.sync([WatchedRepo("ExampleOrg", "platform", wiki=True)])

    paths = {m.path.name for m in report.mirrors}
    assert paths == {"platform.git", "platform.wiki.git"}
    assert (store.root / "ExampleOrg" / "platform.wiki.git").is_dir()


def test_a_repo_with_no_wiki_still_mirrors_the_repo(store, origins):
    """Wikis are created lazily on GitHub, so an absent one is normal, not an outage.

    Reported as an outage it would be a line in every brief, three times a day,
    about a repo that is fine - which is how a reader learns to skim the block.
    """
    origins.add("platform")

    report = store.sync([WatchedRepo("ExampleOrg", "platform", wiki=True)])

    assert [m.path.name for m in report.mirrors] == ["platform.git"]
    assert report.unavailable == []
    assert report.wikis_absent == ["ExampleOrg/platform.wiki"]
    assert Pulse.from_sync(report).render() == ""


# -- guardrails -----------------------------------------------------------


@pytest.mark.guardrail
def test_every_mirror_has_its_push_url_disabled(store, origins, git_out):
    """Invariant 4 at the transport: no credential that can write to a pod's repo."""
    origins.add("platform")
    origins.add("platform.wiki", messages=("Home.md",))

    report = store.sync([WatchedRepo("ExampleOrg", "platform", wiki=True)])

    assert report.mirrors
    for mirror in report.mirrors:
        assert git_out(mirror.path, "remote", "get-url", "--push", "origin")[1] == NO_PUSH
        assert git_out(mirror.path, "remote", "get-url", "origin")[1] != NO_PUSH


@pytest.mark.guardrail
def test_a_push_from_a_mirror_cannot_reach_the_origin(store, origins, git_out, git_env):
    """Asserting on config alone would pass even with a pushable url still set.

    A mirror clone pushes every ref with no refspec, so an unguarded one really
    can create a branch on a pod's repo - which it does if `_disable_push` is
    removed. This is the write attempt itself, not the setting behind it.
    """
    origin = origins.add("service-a")
    mirror = store.sync([WatchedRepo("ExampleOrg", "service-a")]).mirrors[0]
    head = git_out(mirror.path, "rev-parse", "HEAD")[1]
    subprocess.run(
        ["git", "update-ref", "refs/heads/agent-was-here", head],
        cwd=mirror.path,
        check=True,
        capture_output=True,
        env=git_env,
    )

    code, _ = git_out(mirror.path, "push", "origin")

    assert code != 0
    assert git_out(origin, "rev-parse", "--verify", "refs/heads/agent-was-here")[0] != 0


@pytest.mark.guardrail
def test_provisioning_ignores_an_inherited_git_dir(store, origins, make_origin, monkeypatch):
    """GIT_DIR overrides cwd, so an inherited one aims every call at another repo.

    The pulse runs from a scheduler and the suite runs from a git hook; both can
    hand these down. A mirror answering about someone else's repo is guardrail
    6's "repo state as of" turned into a confident lie.
    """
    elsewhere = make_origin("elsewhere", messages=("someone else's history",))
    origins.add("service-a", messages=("base", "Merge PR #412"))
    repo = WatchedRepo("ExampleOrg", "service-a")
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))
    store.sync([repo])

    report = store.sync([repo], cursors={repo.slug: "HEAD~1"})

    assert [i.title for i in Pulse.from_sync(report).items()] == ["Merge PR #412"]


@pytest.mark.guardrail
def test_a_tampered_push_url_is_disabled_again_on_the_next_sync(store, origins, git_out, git_env):
    """Enforcement is re-asserted every run, not once at clone time."""
    repo = WatchedRepo("ExampleOrg", "service-a")
    origin = origins.add("service-a")
    store.sync([repo])
    subprocess.run(
        ["git", "remote", "set-url", "--push", "origin", str(origin)],
        cwd=store.path_for(repo),
        check=True,
        capture_output=True,
        env=git_env,
    )

    store.sync([repo])

    assert git_out(store.path_for(repo), "remote", "get-url", "--push", "origin")[1] == NO_PUSH


@pytest.mark.guardrail
def test_a_mirror_that_cannot_be_read_costs_one_line_not_the_block(store, origins, make_origin):
    """Guardrail 6 on the read side too.

    A repo with nothing in it yet clones fine and then has an unborn HEAD, so
    the cursor read fails. Letting that raise takes down the whole block,
    including every repo that was perfectly readable.
    """
    empty = make_origin("empty", messages=())
    origins["ExampleOrg/empty"] = empty
    origins.add("service-a", messages=("base", "Merge PR #412"))
    repos = [WatchedRepo("ExampleOrg", "empty"), WatchedRepo("ExampleOrg", "service-a")]
    store.sync(repos)

    report = store.sync(repos, cursors={"ExampleOrg/service-a": "HEAD~1"})
    rendered = Pulse.from_sync(report).render()

    assert "Merge PR #412" in rendered
    assert "ExampleOrg/empty: could not read the mirror" in rendered


@pytest.mark.guardrail
def test_a_failed_fetch_marks_the_mirror_stale_instead_of_raising(store, origins):
    """Guardrail 6: one 'couldn't check' line, never a stalled brief."""
    repo = WatchedRepo("ExampleOrg", "service-a")
    origins.add("service-a")
    store.sync([repo])
    shutil.rmtree(origins["service-a"])

    report = store.sync([repo])

    assert report.mirrors[0].stale is True
    assert "as of" in Pulse(mirrors=report.mirrors).render()


@pytest.mark.guardrail
def test_a_repo_that_cannot_be_cloned_is_named_rather_than_dropped(store, origins):
    """A watched repo silently missing from the report is the worst outcome."""
    origins.add("service-a")
    repos = [WatchedRepo("ExampleOrg", "service-a"), WatchedRepo("ExampleOrg", "gone")]

    report = store.sync(repos)

    assert [u.slug for u in report.unavailable] == ["ExampleOrg/gone"]
    assert "ExampleOrg/gone" in Pulse.from_sync(report).render()


@pytest.mark.guardrail
def test_a_mirror_whose_push_guard_fails_is_not_read_and_says_why(store, origins, monkeypatch):
    """Two failures, two fixes. Reporting one as the other sends you to the wrong place.

    A mirror the guard could not be re-applied to is dropped from the report
    rather than read: an unguarded mirror is the thing invariant 4 forbids.
    """
    origins.add("service-a")

    def refuse(self, slug, path):
        raise MirrorUnavailable(slug, "push-guard", "left unread, could not disable its url")

    monkeypatch.setattr(MirrorStore, "_disable_push", refuse)
    repo = WatchedRepo("ExampleOrg", "service-a")
    report = store.sync([repo])

    assert report.mirrors == []
    assert [(u.slug, u.reason) for u in report.unavailable] == [
        ("ExampleOrg/service-a", "left unread, could not disable its url")
    ]
    assert "could not clone" not in Pulse.from_sync(report).render()
    assert not store.path_for(repo).exists(), "an unguarded mirror was left where it is read from"


@pytest.mark.guardrail
def test_a_non_mirror_directory_in_the_way_is_reported_never_deleted(store, origins, tmp_path):
    """Cleaning up "a failed clone" with rmtree deletes whatever is actually there."""
    repo = WatchedRepo("ExampleOrg", "service-a")
    origins.add("service-a")
    occupied = store.path_for(repo)
    occupied.mkdir(parents=True)
    (occupied / "RESTORED-FROM-BACKUP.txt").write_text("not a mirror", encoding="utf-8")

    report = store.sync([repo])

    assert (occupied / "RESTORED-FROM-BACKUP.txt").exists()
    assert [u.slug for u in report.unavailable] == ["ExampleOrg/service-a"]
    assert "in the way" in Pulse.from_sync(report).render()
