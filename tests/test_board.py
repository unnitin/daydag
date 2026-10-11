"""The board module after the retirement of its join (#12 -> PR #199).

What stays is what the loops reach: the `jira` and `projects` blocks of a
hand-edited watchlist, one definition of a ticket key, and the read-only
guardrails that parse this module's source. Nothing here touches the network.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from daydag import board
from daydag.board import READ_ONLY_SCOPES, BoardError, keys_in, read_board_watchlist
from daydag.pulse import Pulse

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


def test_the_jira_and_projects_blocks_parse_and_the_repos_block_is_left_alone(tmp_path: Path):
    path = tmp_path / "Watchlist.md"
    path.write_text(WATCHLIST, encoding="utf-8")

    watched = read_board_watchlist(path)

    assert [p.key for p in watched.projects] == ["CDI", "DED"]
    assert watched.projects[0].jql == "labels = ingest"
    assert watched.projects[0].workstream == "Data Platform"
    assert watched.org_projects == [7]


def test_a_line_that_tried_to_name_a_project_is_carried_not_swallowed(tmp_path: Path):
    """A typo silently stops a project being watched, and nothing else notices."""
    path = tmp_path / "Watchlist.md"
    path.write_text(WATCHLIST, encoding="utf-8")

    assert read_board_watchlist(path).unparsed == ["not-a-number"]


def test_a_missing_watchlist_degrades_into_the_pulses_own_failure_family(tmp_path: Path):
    with pytest.raises(BoardError):
        read_board_watchlist(tmp_path / "gone.md")


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

    Two things make that assertable rather than aspirational: this module is
    the package's first Jira reader, and the token behind it carries
    `read:jira-work` plus Confluence read and no write scope at all - a bug
    that tried to transition a ticket would be refused one layer below this
    code. What this test can prove is the half that lives here: no callable in
    the module, public or private, is shaped like a write.
    """
    source = Path(inspect.getfile(board))
    defined = _defined_names(source)

    offenders = sorted(
        {name for name in defined for verb in WRITE_SHAPED if verb in name.casefold()}
    )

    assert not offenders, (
        f"\nGUARDRAIL 4: a board write surface appeared in {source.name}: {offenders}\n"
        "The board belongs to the data team. A transition, a note on a ticket or\n"
        "a new ticket is drafted for a per-action yes, exactly like a Slack\n"
        "message - it is never sent from here. Do not weaken this test."
    )


@pytest.mark.guardrail
def test_the_module_asks_for_read_scopes_only():
    """The scopes DayDAG declares it needs. A write scope here is a request to
    be granted one, and the request is the thing to catch in review."""
    assert READ_ONLY_SCOPES, "an empty scope list asserts nothing"
    for scope in READ_ONLY_SCOPES:
        assert scope.startswith("read:"), f"{scope!r} is not a read scope"
        assert "write" not in scope
        assert "manage" not in scope


@pytest.mark.guardrail
def test_the_public_surface_is_readers_and_nothing_else():
    """Belt and braces on the export list: `from daydag.board import *` must
    not be able to reach a driver even if one is added privately."""
    exported = [name for name in dir(board) if not name.startswith("_")]
    offenders = [name for name in exported for verb in WRITE_SHAPED if verb in name.casefold()]
    assert not offenders, f"a write-shaped name is importable from daydag.board: {offenders}"


@pytest.mark.guardrail
def test_the_module_holds_no_connector_client():
    """This module's half of "no connector client besides the git mirror"
    (tests/test_guardrails.py carries the package-wide version). Every read
    here is a path to a hand-edited file or a string; nothing takes a query
    to go run.

    Checked against imported module names, not the whole source text - the
    module docstring and `board_site`'s own docstring both say "atlassian" in
    prose, which a plain text search would flag as if it were a client import.
    """
    tree = ast.parse(Path(inspect.getfile(board)).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    banned = {"requests", "httpx", "urllib", "urllib3", "aiohttp", "atlassian", "jira"}
    assert not imported & banned, (
        f"a connector client import appeared in board.py: {imported & banned}"
    )
