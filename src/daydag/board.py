"""The board's watchlist blocks, and ticket keys in a line - the two things the loops read.

USING IT
    watched = read_board_watchlist(folder.watchlist_path)   # -> JiraWatchlist
    [p.key for p in watched.projects]              # the eod plan's one Jira read per project
    keys_in("VP-Data said he'd do CDI-596")        # -> {"CDI-596"}, pulse's definition of a key

CONTRACTS - break one and the guarantee is gone
    1. Read the board, never drive it. No name this module defines - public,
       private, callable or imported - is shaped like a write to someone else's
       ticket; `tests/test_board.py` parses the source and asserts that rather
       than trusting this sentence. The real connector's token carries
       `read:jira-work` plus Confluence read and no write scope at all.
    2. No connector client. Nothing here can reach a network; the reader takes
       a path to a hand-edited file and the key extractor takes a string.
    3. One definition of a ticket key. `keys_in` delegates to `pulse`; a second
       regex is how a Slack line and a board row stop being the same fact.

WHY IT EXISTS
    The eod plan asks Jira for what moved in each watched project
    (`recipes.jira_moved_on`), and `movement` joins that with the repos and
    his own side of the day. Both need the project list from `Watchlist.md`,
    and `movement` needs to know a key when it sees one. That is this module.

    The ticket join, the snapshot diff, the two discrepancy flags and the
    board report that this module carried from #12 were retired on
    2026-10-11 (tag `pre-retirement`, PR #199): no loop called them, the eod
    read that replaced them is bounded to one project and one day, and the
    `sprint` command SPEC names is run by the agent with its own JQL. Restore
    from the tag when a loop needs the join rather than rebuilding it.

KNOWN LIMIT
    GitHub Projects v2 is flagged, not read - the v2 API is GraphQL-only and a
    REST call against it returns nothing useful rather than an error, which is
    why a watched org project renders as one named line instead of silence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from daydag.pulse import _BOLD_HEADING, _BULLET, _HEADING, _RULE, Pulse, PulseError
from daydag.recipes import PROJECT_KEY

__all__ = [
    "PROJECTS_V2_NOTE",
    "READ_ONLY_SCOPES",
    "BoardError",
    "JiraWatchlist",
    "WatchedProject",
    "keys_in",
    "read_board_watchlist",
]


class BoardError(PulseError):
    """The watchlist's board blocks could not be read.

    Deliberately inside the pulse's own failure family: the board is one
    source among several a run reads, so an unreadable watchlist degrades the
    same way an unfetchable mirror does - one line in the brief, never a dead
    6:40am run.
    """


#: The scopes DayDAG asks Atlassian for. Declarative rather than enforced by
#: this module - the grant behind the real connector is what actually stops a
#: write, not a tuple in this file - but a write scope appearing here is the
#: thing to catch in review, before it is ever requested.
READ_ONLY_SCOPES = ("read:jira-work", "read:confluence-content.all")


def keys_in(text: str) -> set[str]:
    """Ticket keys in a line, using `pulse`'s definition of one.

    A one-line delegation rather than a second regex - deliberately. A key is
    the spine of the whole pulse: it is what makes a Slack line, a branch, a
    PR title and a board row the same fact, and two definitions of what one
    looks like drift apart the first time a project puts a digit somewhere
    unusual in its key.
    """
    return Pulse._keys(text)


# ---------------------------------------------------------------------------
# the watchlist blocks
# ---------------------------------------------------------------------------


#: Field separators in a hand-edited watchlist bullet, matching `pulse`'s.
_FIELDS = re.compile(r"[·|]")


@dataclass(frozen=True)
class WatchedProject:
    """One `jira` bullet: which board, and what it is being watched for."""

    key: str
    note: str = ""
    jql: str = ""
    workstream: str = ""


@dataclass(frozen=True)
class JiraWatchlist:
    """The `jira` and `projects` blocks, plus the lines that tried to be one."""

    projects: list[WatchedProject] = field(default_factory=list)
    org_projects: list[int] = field(default_factory=list)
    #: Carried rather than dropped: a typo silently stops a board being
    #: watched, and nothing else in the system would ever notice it had gone.
    unparsed: list[str] = field(default_factory=list)


def _tried_to_name_one(head: str) -> bool:
    """Whether a bullet was aiming at a project and missed, or is just prose.

    A single token that failed to parse is a typo worth reporting; a sentence
    is a note somebody left themselves, and reporting those turns the
    "not watched" line into the noise it exists to cut through.
    """
    return bool(head) and not any(character.isspace() for character in head)


def _project_from_fields(fields: list[str]) -> WatchedProject:
    note, jql, workstream = "", "", ""
    for entry in fields[1:]:
        lowered = entry.casefold()
        if lowered.startswith("jql:"):
            jql = entry[len("jql:") :].strip()
        elif lowered.startswith("workstream:"):
            workstream = entry[len("workstream:") :].strip()
        elif not note:
            note = entry
    return WatchedProject(key=fields[0], note=note, jql=jql, workstream=workstream)


def read_board_watchlist(path: str | Path) -> JiraWatchlist:
    """Parse the `jira` and `projects` blocks of `Watchlist.md`.

    The block machinery (`_HEADING`, `_BOLD_HEADING`, `_RULE`, `_BULLET`) is
    imported from `pulse` rather than rewritten, because both parsers read the
    *same file*: if the two ever disagreed about where a `---` ends a block,
    board rows would leak into the mirror's clone queue and `CDI/whatever`
    would become a repo the pulse tries to fetch.

    A missing file raises `BoardError` rather than an `OSError` from three
    frames down. It is a real failure - nothing is being watched - so it is
    not swallowed, but it stays in the family the pulse degrades on: the vault
    is iCloud-synced, and an evicted placeholder is a documented hazard, not a
    reason for the morning run to die on a traceback.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as unreadable:
        raise BoardError(f"could not read the watchlist at {path}: {unreadable}") from unreadable

    watchlist = JiraWatchlist()
    block = ""
    for line in text.splitlines():
        stripped = line.strip()
        heading = _HEADING.match(stripped) or _BOLD_HEADING.match(stripped)
        if heading:
            name = heading["name"].strip().casefold().rstrip(":")
            block = name if name in {"jira", "projects"} else ""
            continue
        if _RULE.fullmatch(stripped):
            # A horizontal rule ends whatever block was open, exactly as it
            # does for the repos block - otherwise a trailing `- CDI-596` in
            # someone's scratch notes becomes a watched project.
            block = ""
            continue
        bullet = _BULLET.match(stripped) if block else None
        if bullet is None:
            continue
        body = bullet["body"].replace("`", "")
        fields = [entry.strip().strip("()") for entry in _FIELDS.split(body)]
        head = fields[0]
        if block == "jira":
            if PROJECT_KEY.fullmatch(head):
                watchlist.projects.append(_project_from_fields(fields))
            elif _tried_to_name_one(head):
                watchlist.unparsed.append(body)
        elif head.isdigit():
            watchlist.org_projects.append(int(head))
        elif _tried_to_name_one(head):
            watchlist.unparsed.append(body)
    return watchlist


#: Said out loud when a GitHub Projects board is watched. Projects v2 is
#: GraphQL-only: the REST endpoints return nothing useful rather than an
#: error, so an unread board would otherwise render as a quiet one - which is
#: the exact confusion guardrail 6 exists to prevent.
PROJECTS_V2_NOTE = "github projects board not read, projects v2 needs graphql"
