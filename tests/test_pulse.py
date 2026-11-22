"""Engineering pulse: git mirrors for history, API for state (SPEC 3.7).

The rules here are as load-bearing as the features - rule 1 ("state changes,
not activity") is what keeps this from reading as a productivity metric on a
named engineer.

The last section is what `board.py` left behind when it folded in here: the
`jira` block of the hand-edited watchlist, one definition of a ticket key, and
the read-only guardrails that parse this module's source.
"""

import ast
import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

from daydag import pulse
from daydag.pulse import (
    Item,
    Mirror,
    MirrorStore,
    Pulse,
    PulseError,
    WatchedRepo,
    WatchlistError,
    _parse_repo_line,
    _run_git,
    keys_in,
    read_watchlist,
)


def test_merges_since_last_run_uses_the_stored_cursor(fake_repo):
    m = Mirror.attach(fake_repo, cursor="HEAD~2")
    merged = m.merges_since_cursor()
    assert [c.title for c in merged] == ["Merge PR #412", "Merge PR #413"]


def test_a_mirror_reads_its_own_path_not_whatever_GIT_DIR_names(fake_repo, tmp_path, monkeypatch):
    """`cwd` does not win against `GIT_DIR`, and git hands every hook a `GIT_DIR`.

    Without this the pulse reports another repository's history as the mirror's,
    with links that resolve to the wrong project.
    """
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "somewhere-else"))
    m = Mirror.attach(fake_repo, cursor="HEAD~2")
    assert [c.title for c in m.merges_since_cursor()] == ["Merge PR #412", "Merge PR #413"]


def test_cursor_advances_only_after_a_successful_read(fake_repo):
    m = Mirror.attach(fake_repo, cursor="HEAD~2")
    before = m.cursor
    with pytest.raises(PulseError):
        m.merges_since_cursor(_fail=True)
    assert m.cursor == before, "cursor advanced past commits that were never reported"


@pytest.mark.guardrail
def test_stale_mirror_reports_as_of_rather_than_asserting(fake_repo):
    """Guardrail 6. Reporting a stale mirror as current is how this lies."""
    m = Mirror.attach(fake_repo, cursor="HEAD~2")
    m.mark_fetch_failed()
    report = Pulse(mirrors=[m]).render()
    assert "as of" in report
    assert "no updates" not in report


@pytest.mark.guardrail
@pytest.mark.parametrize("forbidden", ["commits", "lines changed", "+/-", "contributions"])
def test_never_emits_activity_metrics(fake_repo, forbidden):
    """SPEC 3.7 rule 1: the unit is 'did the thing he's tracking move'."""
    report = Pulse(mirrors=[Mirror.attach(fake_repo, cursor="HEAD~2")]).render()
    assert forbidden not in report.lower()


def test_silence_produces_no_shipping_block(fake_repo):
    """SPEC 3.7 rule 3: a quiet day produces nothing, not 'no updates'."""
    m = Mirror.attach(fake_repo, cursor="HEAD")
    assert Pulse(mirrors=[m]).render() == ""


def test_joins_slack_pr_and_note_on_the_ticket_key():
    """The valuable part: resolve an ask to a ticket, a PR and a status."""
    p = Pulse(mirrors=[])
    p.observe_slack("jasmeet said he'd do DATA-812")
    p.observe_pr(title="DATA-812 consolidate compute engines", number=412, state="merged")
    joined = p.by_ticket("DATA-812")
    assert joined.pr_number == 412 and joined.mentioned_in_slack


@pytest.mark.guardrail
def test_evidence_marks_movement_but_never_closes_a_loop():
    """SPEC 3.7: merged is not the same as what was asked for."""
    p = Pulse(mirrors=[])
    p.observe_pr(title="DATA-812 consolidate", number=412, state="merged")
    loop = p.apply_evidence({"key": "DATA-812", "status": "open"})
    assert loop["status"] == "open"
    assert loop["evidence_of_movement"] is True


def test_every_reported_item_carries_a_permalink(fake_repo):
    p = Pulse(mirrors=[Mirror.attach(fake_repo, cursor="HEAD~2")])
    assert all(item.permalink for item in p.items())


@pytest.mark.guardrail
def test_a_mirror_reads_its_own_repo_whatever_the_environment_says(fake_repo, monkeypatch):
    """Guardrail 6 again: a mirror that reads the wrong repo reports confidently.

    git exports GIT_DIR into hooks, and a scheduled loop can inherit it from
    anywhere. Inherited, it overrides cwd and the mirror silently answers about
    a different repository.
    """
    monkeypatch.setenv("GIT_DIR", str(fake_repo.parent / "not-the-mirror"))
    monkeypatch.setenv("GIT_WORK_TREE", str(fake_repo.parent))
    m = Mirror.attach(fake_repo, cursor="HEAD~2")
    assert [c.title for c in m.merges_since_cursor()] == ["Merge PR #412", "Merge PR #413"]


# -- invariant 3: an item without a permalink is not a claim (#59) --------


@pytest.mark.guardrail
def test_an_item_without_a_permalink_is_refused_at_construction():
    """Invariant 3, "evidence or silence", enforced where the claim enters.

    `voice.render` already refuses to render a nudge with no permalink, and the
    evidence bus refuses an observation with none. An `Item` is the thing both
    of those are built from, so it takes the same side rather than splitting the
    difference: raise, don't render an apology. A filtered or apologetic line
    still occupies the reader's attention with a claim nothing can back.
    """
    with pytest.raises(ValueError, match="permalink"):
        Item(title="Merge PR #412", permalink="")


@pytest.mark.guardrail
@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
def test_whitespace_is_not_a_permalink(blank: str):
    """A space passes `if not permalink` and renders as `- title ( )`."""
    with pytest.raises(ValueError, match="permalink"):
        Item(title="Merge PR #412", permalink=blank)


def test_the_refusal_names_the_item_so_it_can_be_acted_on():
    """A raise nobody can trace back to a source is barely better than a drop."""
    with pytest.raises(ValueError, match="Merge PR #412"):
        Item(title="Merge PR #412", permalink="")


def test_a_permalink_is_stored_stripped():
    """Trailing whitespace out of a git subject would render inside the brackets."""
    assert Item(title="t", permalink="  /r.git#abc1234  ").permalink == "/r.git#abc1234"


# -- #60: a stale mirror is dated, not just admitted ----------------------


FETCHED = datetime(2026, 9, 5, 6, 40, tzinfo=UTC)


@pytest.mark.guardrail
def test_a_stale_mirror_line_carries_the_time_of_its_last_good_fetch(fake_repo):
    """Issue #32 asked for "repo state as of <ts>". Undated staleness is honest
    but unusable: twenty minutes and four days mean different things about
    whether to trust the rest of the block."""
    m = Mirror.attach(fake_repo, cursor="HEAD~2", last_fetched_at=FETCHED)
    m.mark_fetch_failed()

    line = Pulse(mirrors=[m]).render()

    assert "2026-09-05 06:40 UTC" in line
    assert "as of last run" not in line, "the undated wording survived"


@pytest.mark.guardrail
def test_a_mirror_that_never_fetched_says_so_rather_than_inventing_a_time(fake_repo):
    """The failure direction matters: a fabricated or omitted date reads as
    fresh. No record on file is itself the honest answer."""
    m = Mirror.attach(fake_repo, cursor="HEAD~2")
    m.mark_fetch_failed()

    line = Pulse(mirrors=[m]).render()

    assert "as of" in line
    assert "no successful fetch on record" in line


def test_a_successful_fetch_records_its_time_and_clears_the_staleness(fake_repo):
    m = Mirror.attach(fake_repo, cursor="HEAD~2")
    m.mark_fetch_failed()

    m.mark_fetched(FETCHED)

    assert m.stale is False
    assert m.last_fetched_at == FETCHED


# -- issue #128: a repo that lands somewhere other than its default branch ----


def test_a_repo_landing_on_dev_reports_its_landings(repo_landing_on_dev):
    """The bug in #128: silence from the wrong branch reads as a healthy repo.

    `main` holds only the base commit, so following the default branch reports
    nothing at all - not stale, not unavailable, just empty. That is how two
    months of real work on `createos-ingestion` were reported as a stall.
    """
    m = Mirror.attach(repo_landing_on_dev, cursor="dev~2", ref="dev")
    assert [c.title for c in m.merges_since_cursor()] == ["Merge PR #501", "Merge PR #502"]


def test_the_default_branch_stays_the_default(fake_repo):
    """Omitting `ref` must behave exactly as before, or every existing row moves."""
    m = Mirror.attach(fake_repo, cursor="HEAD~2")
    assert m.ref == "HEAD"
    assert [c.title for c in m.merges_since_cursor()] == ["Merge PR #412", "Merge PR #413"]


def test_the_cursor_advances_to_the_named_ref_not_to_HEAD(repo_landing_on_dev):
    """Advancing to HEAD would park the cursor on a branch nothing lands on.

    The next run would then read `main..dev` and re-report every landing it had
    already reported, forever.
    """
    m = Mirror.attach(repo_landing_on_dev, cursor="dev~2", ref="dev")
    m.merges_since_cursor()
    head = _run_git(("rev-parse", "HEAD"), cwd=repo_landing_on_dev).stdout.strip()
    dev = _run_git(("rev-parse", "dev"), cwd=repo_landing_on_dev).stdout.strip()
    assert m.cursor == dev
    assert m.cursor != head, "cursor parked on the default branch, not on the watched one"


def test_a_ref_that_does_not_resolve_names_the_ref(repo_landing_on_dev):
    """A typo in the watchlist must not read as a broken mirror."""
    m = Mirror.attach(repo_landing_on_dev, cursor="HEAD", ref="devv")
    with pytest.raises(PulseError) as caught:
        m.merges_since_cursor()
    assert "devv" in str(caught.value)


def test_a_branch_field_is_parsed_off_the_watchlist_bullet():
    """`branch:` is a field of its own, exactly as `wiki` is - never a substring."""
    assert _parse_repo_line("ExampleOrg/svc · branch:dev · statement ingestion").branch == "dev"
    assert _parse_repo_line("ExampleOrg/svc · wiki").branch == ""
    # Prose that merely mentions a branch is not a marker.
    assert _parse_repo_line("ExampleOrg/svc · see the dev branch for details").branch == ""


def test_a_watched_repos_branch_reaches_its_mirror(tmp_path, repo_landing_on_dev, git_env):
    """The seam the bug actually lived in: parsed, then dropped before the read."""
    store = MirrorStore(tmp_path / "mirrors", url_for=lambda _r: str(repo_landing_on_dev))
    repo = WatchedRepo("ExampleOrg", "svc", branch="dev")
    mirror = store.ensure(repo, cursor="dev~2")
    assert mirror.ref == "dev"
    assert [c.title for c in mirror.merges_since_cursor()] == ["Merge PR #501", "Merge PR #502"]


# -- what the review of #128 found -------------------------------------------


def test_first_sight_on_a_branch_repo_reports_no_backlog(tmp_path, repo_landing_on_dev):
    """A fresh clone opens at the WATCHED tip, not at the default branch's.

    `HEAD..dev` is the branch's whole divergence, so first sight of a repo that
    has been on `dev` for two months would open with two months of merges -
    the backlog FIRST_SIGHT exists to suppress, on the repos `branch:` is for.
    """
    store = MirrorStore(tmp_path / "m", url_for=lambda _r: str(repo_landing_on_dev))
    report = store.sync([WatchedRepo("ExampleOrg", "svc", branch="dev")])
    assert [m.merges_since_cursor() for m in report.mirrors] == [[]]


def test_the_second_read_reports_only_what_landed_after_first_sight(
    tmp_path, repo_landing_on_dev, add_commit
):
    """First sight suppresses the backlog without suppressing the next landing."""
    store = MirrorStore(tmp_path / "m", url_for=lambda _r: str(repo_landing_on_dev))
    repo = WatchedRepo("ExampleOrg", "svc", branch="dev")
    mirror = store.sync([repo]).mirrors[0]
    mirror.merges_since_cursor()
    _run_git(("checkout", "-q", "dev"), cwd=repo_landing_on_dev)
    add_commit(repo_landing_on_dev, "Merge PR #503")
    store.refresh(mirror)
    assert [c.title for c in mirror.merges_since_cursor()] == ["Merge PR #503"]


def test_a_watchlist_typo_reaches_the_reader(tmp_path, repo_landing_on_dev):
    """The branch name has to survive to the brief, or the fix is undiscoverable.

    `items()` is the only path to a report, and it used to swallow every
    message - leaving a repo silently unread with no way to find out why.
    """
    store = MirrorStore(tmp_path / "m", url_for=lambda _r: str(repo_landing_on_dev))
    mirror = store.sync([WatchedRepo("ExampleOrg", "svc", branch="devv")]).mirrors[0]
    pulse = Pulse(mirrors=[mirror])
    assert pulse.items() == []
    rendered = pulse.render()
    assert "devv" in rendered, rendered


def test_a_broken_mirror_is_not_blamed_on_the_watchlist(tmp_path):
    """Sending the reader to edit a file that is already correct is the inversion."""
    empty = tmp_path / "not-a-mirror"
    empty.mkdir()
    m = Mirror.attach(empty, cursor="HEAD", ref="dev")
    with pytest.raises(PulseError) as caught:
        m.merges_since_cursor()
    assert not isinstance(caught.value, WatchlistError)
    assert "watchlist" not in str(caught.value).casefold()


def test_the_cursor_lands_on_the_sha_that_bounded_the_read(repo_landing_on_dev):
    """Resolved once. Two resolutions let a push between them skip landings."""
    m = Mirror.attach(repo_landing_on_dev, cursor="dev~2", ref="dev")
    items = m.merges_since_cursor()
    tip = _run_git(("rev-parse", "dev"), cwd=repo_landing_on_dev).stdout.strip()
    assert len(items) == 2
    assert m.cursor == tip


def test_one_repo_listed_twice_on_different_branches_is_reported(tmp_path, repo_landing_on_dev):
    """One mirror carries one cursor, so the second row can never be read.

    Deduping it away silently is the same "silence reads as health" failure
    this module keeps relearning.
    """
    store = MirrorStore(tmp_path / "m", url_for=lambda _r: str(repo_landing_on_dev))
    report = store.sync(
        [
            WatchedRepo("ExampleOrg", "svc", branch="dev"),
            WatchedRepo("ExampleOrg", "svc", branch="release"),
        ]
    )
    assert len(report.mirrors) == 1
    assert any("listed twice" in line for line in report.unreadable), report.unreadable


def test_the_same_repo_listed_twice_identically_stays_quiet(tmp_path, repo_landing_on_dev):
    """A duplicate row that agrees with itself is not a problem worth a line."""
    store = MirrorStore(tmp_path / "m", url_for=lambda _r: str(repo_landing_on_dev))
    repo = WatchedRepo("ExampleOrg", "svc", branch="dev")
    report = store.sync([repo, repo])
    assert len(report.mirrors) == 1
    assert report.unreadable == []


def test_an_empty_repos_unborn_HEAD_is_not_blamed_on_the_watchlist(tmp_path, make_origin):
    """`HEAD` is the default, not a line anyone typed.

    Caught by `test_a_mirror_that_cannot_be_read_costs_one_line_not_the_block`:
    the first cut of the watchlist-typo message called an empty repo a config
    error and told the reader to edit a line that does not exist.
    """
    empty = make_origin("empty", messages=())
    store = MirrorStore(tmp_path / "m", url_for=lambda _r: str(empty))
    mirror = store.sync([WatchedRepo("ExampleOrg", "empty")]).mirrors[0]
    with pytest.raises(PulseError) as caught:
        mirror.merges_since_cursor()
    assert not isinstance(caught.value, WatchlistError)
    assert "watchlist" not in str(caught.value).casefold()


def test_the_typo_line_names_the_repo_once(tmp_path, repo_landing_on_dev):
    """`render` already prefixes the slug; the message must not repeat it."""
    store = MirrorStore(tmp_path / "m", url_for=lambda _r: str(repo_landing_on_dev))
    mirror = store.sync([WatchedRepo("ExampleOrg", "svc", branch="devv")]).mirrors[0]
    rendered = Pulse(mirrors=[mirror]).render()
    assert rendered.count("ExampleOrg/svc") == 1, rendered


def test_a_merge_button_landing_is_titled_by_its_pr_not_its_branch():
    from daydag.pulse import _landing_title

    assert (
        _landing_title(
            "Merge pull request #964 from ExampleOrg/feat/TDE-1007-all-labels",
            "Label engine: every label, not the first page\n\nlonger body",
        )
        == "Label engine: every label, not the first page (#964)"
    )


def test_a_squash_landing_and_a_bodiless_merge_keep_their_subject():
    from daydag.pulse import _landing_title

    assert _landing_title("fix(auth): constrain intent IDs (#422)", "") == (
        "fix(auth): constrain intent IDs (#422)"
    )
    assert _landing_title("Merge pull request #7 from ExampleOrg/x", "  \n") == (
        "Merge pull request #7 from ExampleOrg/x"
    )


def test_a_landing_links_to_github_not_to_a_path_on_this_machine():
    from daydag.pulse import WatchedRepo, github_link

    assert github_link(WatchedRepo("ExampleOrg", "svc")).format(sha="abc123") == (
        "https://github.com/ExampleOrg/svc/commit/abc123"
    )
    assert github_link(WatchedRepo("ExampleOrg", "svc").wiki_repo()) == (
        "https://github.com/ExampleOrg/svc/wiki/_history"
    )


# -- the watchlist's jira block and the ticket key (folded in from board.py) --

WATCHLIST = """\
# Watchlist

## repos
- CreateMusicGroup/some-service · wiki

**jira**
- CDI · the data team's board · jql: labels = ingest · workstream: Data Platform
- DED
- a line that names no project

## projects
- 7 · the pod that never moved to jira
- not-a-number

---
- CDI-not-in-a-block
"""


def test_the_key_regex_is_pulses_and_is_not_reimplemented_here(monkeypatch):
    """One definition of what a ticket key looks like, or they drift apart.

    Driven rather than grepped: swap pulse's extractor and every reader in
    this module has to change with it, which is only true if there is one.
    """
    monkeypatch.setattr(Pulse, "_keys", staticmethod(lambda text: {"XX-1"} if text else set()))

    assert keys_in("nothing key-shaped in here") == {"XX-1"}


def test_the_jira_block_parses_and_the_repos_and_projects_blocks_are_left_alone(tmp_path: Path):
    path = tmp_path / "Watchlist.md"
    path.write_text(WATCHLIST, encoding="utf-8")

    watched = read_watchlist(path)

    assert [p.key for p in watched.projects] == ["CDI", "DED"]
    assert [r.slug for r in watched.repos] == ["CreateMusicGroup/some-service"]
    # `unparsed` is repo lines only, because the pulse renders each one into
    # the brief. A jira or projects bullet that names nothing never was one.
    assert watched.unparsed == []


def test_one_pass_reads_both_blocks_exactly_as_the_two_parsers_did(tmp_path: Path):
    """The fold's own check, with the values the two parsers gave before it.

    A bold label opens a block and a rule closes it; a nested bullet is read
    like any other; backticks and brackets come off a key; a repeated key
    repeats, because each entry is one Jira read; a lowercase or hyphenated
    head is not a key; and a `projects` block is not read, so `ABC` under it
    is nobody's project.
    """
    path = tmp_path / "Watchlist.md"
    path.write_text(
        "**repos**\n"
        "- ExampleOrg/service-a · wiki\n"
        "  - `ExampleOrg/service-b` · branch:dev\n"
        "- ExampleOrg / typo\n"
        "---\n"
        "- ExampleOrg/after-the-rule\n"
        "## jira\n"
        "- CDI · live board · jql: labels = ingest\n"
        "\t- `TDE` | data platform\n"
        "- (DED)\n"
        "- CDI\n"
        "- cdi\n"
        "- CDI-596\n"
        "**projects**\n"
        "- 7 · a pod board\n"
        "- ABC\n"
        "---\n"
        "- XYZ\n"
        "**Jira**:\n"
        "- ZED\n",
        encoding="utf-8",
    )

    watched = read_watchlist(path)

    assert [(r.slug, r.wiki, r.branch) for r in watched.repos] == [
        ("ExampleOrg/service-a", True, ""),
        ("ExampleOrg/service-b", False, "dev"),
    ]
    assert watched.unparsed == ["ExampleOrg / typo"]
    assert [p.key for p in watched.projects] == ["CDI", "TDE", "DED", "CDI", "ZED"]


WRITE_SHAPED = (
    "transition",
    "comment",
    "create",
    "update_issue",
    "editissue",
    "assign",
    "delete",
    "post",
    "put",
    "patch",
    "write",
)

#: Names `pulse.py` binds that carry a write verb as a substring and are not a
#: write. Exact names, each with its reason: the scan below still fires on
#: anything else, so a new one has to be argued in here in review.
NOT_A_BOARD_WRITE = {
    "__post_init__": "the dataclass hook that refuses an `Item` with no permalink",
    "FORBIDDEN_IN_OUTPUT": "`put` inside `output`: the activity words a render must not carry",
}


def _defined_names(path: Path) -> list[str]:
    """Every name this module defines or binds - not its prose.

    Parsed rather than grepped, because the docstrings deliberately say the
    word "transition" while explaining that there is no such call, and a scan
    that trips on its own explanation is one people learn to route around.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.alias):
            names.append(node.name.split(".")[-1])
    return names


@pytest.mark.guardrail
def test_the_module_exposes_no_way_to_drive_the_board():
    """BEHAVIOURAL. Guardrail 4 is "read the board, never drive it".

    The package holds no Jira client: the eod plan hands the agent one bounded
    read per project (`recipes.jira_moved_on`), and this module is where those
    project keys are read off the watchlist and where a ticket key is defined.
    The token behind the real connector carries `read:jira-work` plus
    Confluence read and no write scope at all, so a bug that tried to
    transition a ticket would be refused one layer below this code. What this
    test can prove is the half that lives here: no name the module defines or
    binds, public or private, is shaped like a write.

    It scanned `board.py` until that module folded in here, and scans all of
    `pulse.py` now. `NOT_A_BOARD_WRITE` is the two names the wider file brings
    that hold a verb by accident; each has to still exist to stay exempt.
    """
    source = Path(inspect.getfile(pulse))
    defined = _defined_names(source)

    gone = sorted(set(NOT_A_BOARD_WRITE) - set(defined))
    assert not gone, f"an exemption for a name pulse.py no longer has: {gone}"
    offenders = sorted(
        {name for name in defined for verb in WRITE_SHAPED if verb in name.casefold()}
        - set(NOT_A_BOARD_WRITE)
    )

    assert not offenders, (
        f"\nGUARDRAIL 4: a board write surface appeared in {source.name}: {offenders}\n"
        "The board belongs to the data team. A transition, a note on a ticket or\n"
        "a new ticket is drafted for a per-action yes, exactly like a Slack\n"
        "message - it is never sent from here. Do not weaken this test."
    )


@pytest.mark.guardrail
def test_the_public_surface_is_readers_and_nothing_else():
    """Belt and braces on the export list: `from daydag.pulse import *` must
    not be able to reach a driver even if one is added privately."""
    exported = [name for name in dir(pulse) if not name.startswith("_")]
    offenders = [
        name
        for name in exported
        for verb in WRITE_SHAPED
        if verb in name.casefold() and name not in NOT_A_BOARD_WRITE
    ]
    assert not offenders, f"a write-shaped name is importable from daydag.pulse: {offenders}"


@pytest.mark.guardrail
def test_the_module_holds_no_connector_client():
    """This module's half of "no connector client besides the git mirror"
    (tests/test_guardrails.py carries the package-wide version). It is the git
    mirror, reached through `subprocess`; every other read here is a path to a
    hand-edited file or a string, and nothing takes a query to go run.

    Checked against imported module names, not the whole source text - the
    module docstring says "Atlassian" in prose, which a plain text search
    would flag as if it were a client import.
    """
    tree = ast.parse(Path(inspect.getfile(pulse)).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    banned = {"requests", "httpx", "urllib", "urllib3", "aiohttp", "atlassian", "jira"}
    assert not imported & banned, (
        f"a connector client import appeared in pulse.py: {imported & banned}"
    )
