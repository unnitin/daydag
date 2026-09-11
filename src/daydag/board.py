"""The board half of the engineering pulse: Jira read, GitHub Projects flagged.

USING IT
    from daydag import recipes
    from daydag.board import (
        BoardJoin, BoardReport, BoardSnapshot, apply_board_evidence,
        board_deltas, board_search, board_site, closed_unannounced,
        tickets_from_search, tickets_never_made,
    )

    params = board_search(["CDI"])            # -> a recipes.jira_search() dict
    payload = call_jira(params)                # the caller's connector, injected
    site = board_site(identities)              # "example.atlassian.net", from .env
    after = BoardSnapshot.of(tickets_from_search(payload, site=site), taken_at=now)

    deltas = board_deltas(before, after)       # [] on the first run - nothing to diff yet

    join = BoardJoin()
    join.observe_board(after.tickets.values())
    join.observe_slack("VP-Data said he'd do CDI-596", permalink=slack_url, at=ts)
    join.resolve("CDI-596")                    # -> Resolved: ticket + PR + slack mention

    never_made = tickets_never_made(join, projects=["CDI"])
    unannounced = closed_unannounced(deltas, join)
    loop = apply_board_evidence(loop, deltas)  # {**loop, "evidence_of_movement": bool}

    BoardReport(deltas=deltas, discrepancies=[*never_made, *unannounced]).render()

CONTRACTS - break one and the guarantee is gone
    1. Read the board, never drive it. No name this module defines - public,
       private, callable or imported - is shaped like a write to someone else's
       ticket; `tests/test_board.py` parses the source and asserts that rather
       than trusting this sentence. The real connector's token carries
       `read:jira-work` plus Confluence read and no write scope at all, so a
       bug that reached for a transition would be refused a layer below this
       code regardless - this module is the half of guardrail 4 that lives here.
    2. No connector client. Every source is a callable the caller injects, the
       same shape `smoke.py` and `recipes.py` already use. `tickets_from_search`
       takes an already-fetched payload, never a query to go run - there is
       nothing in this module capable of reaching a network.
    3. Every query is bounded by `recipes.jira_search`, via `board_search`'s
       pass-through of `recipes.JIRA_FIELDS`. Neither is restated here - a
       second field list or a second cap is exactly how the two would drift,
       which is what turned one unbounded, 4-project JQL into 125,231
       characters in the first place (#2 audit).
    4. Board evidence marks a loop MOVED and never CLOSED, for every kind of
       delta and every status a loop can be in. `apply_board_evidence` adds
       exactly one key, `evidence_of_movement`, and returns every other field
       of the loop unchanged - a ticket reaching Done is not proof the ask
       behind it was satisfied.
    5. Nothing to compare against is not news. The first run against an empty
       snapshot reports no deltas, and a ticket that drops out of the bounded
       `updated` window is absence, not a close - the same rule
       `pulse.MirrorStore` gives a freshly cloned mirror.
    6. Evidence or silence. Every `Ticket` carries a permalink built from a
       configured site (`board_site`), never a literal, and `BoardReport`
       renders as the empty string on a quiet board rather than "no updates".

WHY IT EXISTS
    `pulse` answers "did code land". This answers "did the thing he is
    tracking move on the board" - and the two failures Nitin already works
    around by hand: he believes a ticket exists and it was never made, and a
    ticket closes and nobody says so. The ticket key (`ABC-123`) is what makes
    both answerable without anyone holding the Slack-thread-to-PR-to-ticket
    mapping in their head; `BoardJoin` is that mapping, computed once instead
    of reconstructed from memory every time someone asks "is that on the board".

KNOWN LIMIT
    `tickets_never_made` makes the weaker of the two possible claims. A real
    backlog ticket nobody has touched lately falls out of the query's `updated`
    window the same way a truly nonexistent one does, so asserting "never
    made" would sometimes be a confident line that is wrong; the rendered
    phrase says "talked about, but did not come back from the board" rather
    than asserting non-existence. Sprint burn (SPEC 3.7 item 4's other clause)
    is not built here: the live board's sprint field is not one
    `recipes.JIRA_FIELDS` carries, and guessing its shape from an unverified
    fixture is exactly the kind of assumption the #2 connector audit exists to
    catch rather than repeat. GitHub Projects v2 is flagged, not read - the v2
    API is GraphQL-only and a REST call against it returns nothing useful
    rather than an error, which is why a watched org project renders as one
    named line instead of silence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from daydag.config import ConfigError
from daydag.payloads import has, records
from daydag.pulse import _BOLD_HEADING, _BULLET, _HEADING, _RULE, Joined, Pulse, PulseError
from daydag.recipes import JIRA_DEFAULT_MAX_RESULTS, JIRA_DEFAULT_WINDOW_DAYS, JIRA_FIELDS
from daydag.recipes import jira_search as _jira_search
from daydag.voice import clipped

__all__ = [
    "PROJECTS_V2_NOTE",
    "READ_ONLY_SCOPES",
    "BoardDelta",
    "BoardError",
    "BoardJoin",
    "BoardReport",
    "BoardSnapshot",
    "Discrepancy",
    "JiraWatchlist",
    "Mention",
    "Resolved",
    "Ticket",
    "WatchedProject",
    "apply_board_evidence",
    "board_deltas",
    "board_search",
    "board_site",
    "closed_unannounced",
    "keys_in",
    "read_board_watchlist",
    "ticket_from_issue",
    "tickets_from_search",
    "tickets_never_made",
]


class BoardError(PulseError):
    """A board could not be read or a stored snapshot could not be restored.

    Deliberately inside the pulse's own failure family: the board is one
    source among several a run reads, so an unreadable watchlist or a
    malformed cached ticket degrades the same way an unfetchable mirror does -
    one line in the brief, never a dead 6:40am run.
    """


#: The scopes DayDAG asks Atlassian for. Declarative rather than enforced by
#: this module - the grant behind the real connector is what actually stops a
#: write, not a tuple in this file - but a write scope appearing here is the
#: thing to catch in review, before it is ever requested.
READ_ONLY_SCOPES = ("read:jira-work", "read:confluence-content.all")


# ---------------------------------------------------------------------------
# text and time - the guarded edge every field from the board crosses
# ---------------------------------------------------------------------------

#: Field limits, kept local per CONTRIBUTING - `voice.clipped` owns the
#: collapse-then-clip rule, the budget per field is this module's to set.
_KEY_LIMIT = 40
_STATUS_LIMIT = 60
_NAME_LIMIT = 60
_SUMMARY_LIMIT = 200
_TIMESTAMP_LIMIT = 40
_PERMALINK_LIMIT = 300


def _text(value: object, limit: int) -> str:
    """A board field as one clipped line - tolerant of Jira's explicit nulls.

    `voice.one_line` stringifies deliberately (`one_line(404) == "404"` is its
    own tested contract, for a payload that is not always a string) and that
    same contract turns ``None`` into the four-letter word ``"None"``. Every
    field read below can carry one: Jira sends an explicit null for a field a
    ticket has not filled in rather than omitting the key, and an unresolved
    ticket stringified that way carries the resolution date ``'None'``, an
    unassigned one an owner literally named ``None``. So the null check
    happens once, here, rather than at every call site that reads an optional
    field and needs to remember it.
    """
    return "" if value is None else clipped(value, limit, ellipsis="...")


#: A Slack `ts` in its native form: an epoch, as a string. Nine digits puts the
#: floor in 2001, so no plausible ticket number, page size or year reaches it.
_EPOCH_TEXT = re.compile(r"\d{9,12}(?:\.\d+)?")


def _moment(value: object) -> datetime | None:
    """A timestamp from a `datetime`, an epoch, an epoch string, or an ISO one.

    `datetime` is checked FIRST, ahead of every other branch - not because a
    caller is expected to pass one today, but because the alternative failure
    is quiet rather than loud: falling through to the string branch below,
    `str(a_datetime)` renders as ISO-ish text that `datetime.fromisoformat`
    then happens to re-parse, so an already-correct value would have kept
    working by accident rather than by a case that says so. `recipes._as_day`
    hit the same shape of bug from the other direction - a `datetime` silently
    satisfying an `isinstance(x, date)` check meant for a bare date - and the
    fix there is the same one applied here: name the more specific type and
    handle it before the general one gets a chance to be almost right.

    Slack's `ts` is the other awkward case: it is an epoch, and it arrives as
    a *string* (`"1757000000.123456"`). Read as ISO it parses as nothing, and
    a mention with no time cannot be shown to postdate a close - so every
    close would report as unannounced. Hence the digits-first branch.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    if _EPOCH_TEXT.fullmatch(text):
        return datetime.fromtimestamp(float(text), tz=UTC)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _epoch(value: object) -> float | None:
    """The same moment, as a POSIX timestamp - never a naive/aware comparison.

    Reduced to one number on the way in so a naive value and an offset-aware
    one never meet in a `>=`: Slack hands back a naive-reading epoch and Jira
    hands back an offset, and comparing the two `datetime` objects directly is
    a `TypeError` at 6:40am rather than at review time.
    """
    moment = _moment(value)
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

#: A hostname and only a hostname. A scheme or a path here is a permalink that
#: goes somewhere other than where the line it is printed on says it does.
_HOSTNAME = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
)


def board_site(identities: Mapping[str, str]) -> str:
    """The Atlassian site, from ``ATLASSIAN_SITE``.

    Configured rather than written down: this repo is public, and the site
    name is an identifier like any other. Every permalink `ticket_from_issue`
    builds comes from the value this returns, which is why it is checked to be
    a bare hostname before it is ever interpolated into a URL.
    """
    raw = str(identities.get("ATLASSIAN_SITE", "")).strip()
    if not raw:
        raise ConfigError("ATLASSIAN_SITE is not set. Add it to .env; see .env.example.")
    if "$" in raw:
        raise ConfigError("ATLASSIAN_SITE names an unset variable. Fix it in .env.")
    if not _HOSTNAME.fullmatch(raw):
        raise ConfigError("ATLASSIAN_SITE must be a bare hostname, with no scheme and no path.")
    return raw


def board_search(
    projects: Sequence[str],
    *,
    updated_within_days: int = JIRA_DEFAULT_WINDOW_DAYS,
    statuses: Sequence[str] | None = None,
    max_results: int = JIRA_DEFAULT_MAX_RESULTS,
    page: int = 0,
) -> dict[str, object]:
    """Search parameters for one page of board state.

    A thin pass-through to `recipes.jira_search`, pinned to `JIRA_FIELDS` -
    the exact fields `ticket_from_issue` below reads and no others. Kept here
    rather than left for every caller to remember, because a caller forgetting
    the field list is what turned a bounded-looking query into 125,231
    characters the one time it actually happened (#2 audit). Every bound -
    the field list, the page cap, the project-key validation - is enforced by
    `recipes.jira_search` itself; nothing here restates or loosens any of them.
    """
    return _jira_search(
        projects,
        updated_within_days=updated_within_days,
        statuses=statuses,
        fields=JIRA_FIELDS,
        max_results=max_results,
        page=page,
    )


# ---------------------------------------------------------------------------
# one ticket
# ---------------------------------------------------------------------------

#: Labels that mean a ticket is stuck, when the search asked for `labels`.
_BLOCKED_LABELS = frozenset({"blocked", "impediment", "blocker"})
#: Fallback read off the status name itself, for a board that flags a ticket by
#: putting it in a column rather than by labelling it - `labels` may not even
#: be a field the caller's query asked for.
_BLOCKED_STATUS_WORDS = ("blocked", "impediment", "on hold")


def _blocked(fields: Mapping[str, Any], status_name: str) -> bool:
    labels = {str(label).casefold() for label in (fields.get("labels") or ())}
    if labels & _BLOCKED_LABELS:
        return True
    lowered = status_name.casefold()
    return any(word in lowered for word in _BLOCKED_STATUS_WORDS)


@dataclass(frozen=True)
class Ticket:
    """One board row, reduced to what a brief line can be built from."""

    key: str
    summary: str
    status: str
    owner: str | None
    permalink: str
    project: str
    done: bool = False
    blocked: bool = False
    updated_at: str = ""
    resolved_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object) -> Ticket:
        """Restore one ticket from a stored snapshot - or hand one back as-is.

        Checked in this order deliberately. `isinstance(raw, Mapping)` is
        false for a `Ticket` - it is a plain frozen dataclass with no
        `Mapping` base - so a coercion that checks the mapping case first and
        falls through to a permissive `else` on anything else is exactly the
        shape that, in a sibling module today, stringified an already-built
        value's `repr()` into a field and quietly reset a flag the object
        actually carried. The fix generalises: name the already-coerced case
        and check it BEFORE the mapping case, and never let the final branch
        be a bare-metal `cls(str(raw))` that accepts literally anything.
        Refusing an unrecognised shape here raises `BoardError`, which stays
        inside the family this package degrades on, rather than a `TypeError`
        three frames from the cache that wrote the file.
        """
        if isinstance(raw, Ticket):
            return raw
        if not isinstance(raw, Mapping):
            raise BoardError(f"not a stored ticket or a mapping of one: {type(raw).__name__}")
        known = {entry.name for entry in dataclass_fields(cls)}
        try:
            return cls(**{name: value for name, value in raw.items() if name in known})
        except TypeError as malformed:
            raise BoardError(
                f"a stored board snapshot could not be read: {malformed}"
            ) from malformed


def ticket_from_issue(raw: Mapping[str, Any], *, site: str) -> Ticket:
    """One `searchJiraIssuesUsingJql` result to a `Ticket`.

    Tolerant on purpose: an unassigned ticket arrives as an explicit `null`
    rather than a missing key, a status may or may not carry a category, and a
    `KeyError` three frames down here takes the whole morning brief with it.

    `Won't Do` and `Done` are both terminal and both live on the data team's
    board, so the status *category* decides rather than the status name - a
    board that renames a column should not silently stop reporting closes.
    """
    key = _text(raw.get("key", ""), _KEY_LIMIT)
    fields = raw.get("fields") or {}
    status = fields.get("status") or {}
    category = (status.get("statusCategory") or {}).get("key", "")
    status_name = _text(status.get("name", ""), _STATUS_LIMIT)
    owner_field = fields.get("assignee") or {}
    return Ticket(
        key=key,
        summary=_text(fields.get("summary", ""), _SUMMARY_LIMIT),
        status=status_name,
        # Empty is nobody, not somebody called "". Jira sends a null assignee
        # for an unassigned ticket, and one with a null display name for an
        # account that no longer exists - both mean the same thing.
        owner=_text(owner_field.get("displayName"), _NAME_LIMIT) or None,
        permalink=f"https://{site}/browse/{key}",
        project=key.split("-", 1)[0] if key else "",
        done=category == "done",
        blocked=_blocked(fields, status_name),
        updated_at=_text(fields.get("updated"), _TIMESTAMP_LIMIT),
        resolved_at=_text(fields.get("resolutiondate"), _TIMESTAMP_LIMIT) or None,
    )


def tickets_from_search(payload: Any, *, site: str) -> list[Ticket]:
    """Every ticket in one bounded search response.

    `payloads.records` is what tells a genuinely empty board (a dormant
    project, or a live one with nothing in the window - both honest) apart
    from a payload that never had an issue list in the first place - an error
    object, a completely different shape. Only the second raises; the first
    is exactly what a quiet board looks like and is not a failure.
    """
    issues = records(payload)
    if issues is None:
        raise BoardError(
            "no issue list came back; the payload is not shaped like a jira search result"
        )
    return [ticket_from_issue(issue, site=site) for issue in issues if has(issue, "key")]


# ---------------------------------------------------------------------------
# a snapshot, and the deltas between two of them
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoardSnapshot:
    """Every watched ticket as of one run, plus when the run was."""

    tickets: dict[str, Ticket] = field(default_factory=dict)
    taken_at: str = ""

    @classmethod
    def empty(cls) -> BoardSnapshot:
        return cls()

    @classmethod
    def of(cls, tickets: Iterable[Ticket], *, taken_at: str = "") -> BoardSnapshot:
        return cls({ticket.key: ticket for ticket in tickets}, taken_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "taken_at": self.taken_at,
            "tickets": {key: ticket.to_dict() for key, ticket in self.tickets.items()},
        }

    @classmethod
    def from_dict(cls, raw: object) -> BoardSnapshot:
        """The inverse of `to_dict` - or the identity, for an already-live snapshot.

        Same ordering as `Ticket.from_dict` and for the same reason: check
        "is this already my own type" before treating it as the mapping this
        method exists to parse.
        """
        if isinstance(raw, BoardSnapshot):
            return raw
        if not isinstance(raw, Mapping):
            raise BoardError(
                f"not a stored board snapshot or a mapping of one: {type(raw).__name__}"
            )
        rows = raw.get("tickets") or {}
        if not isinstance(rows, Mapping):
            raise BoardError("a stored board snapshot's 'tickets' is not a mapping")
        return cls(
            {key: Ticket.from_dict(row) for key, row in rows.items()},
            str(raw.get("taken_at", "")),
        )


@dataclass(frozen=True)
class BoardDelta:
    """One state change on the board, with the link that makes it checkable."""

    kind: str
    key: str
    summary: str
    permalink: str
    detail: str = ""


def _delta(kind: str, ticket: Ticket, detail: str) -> BoardDelta:
    return BoardDelta(
        kind=kind, key=ticket.key, summary=ticket.summary, permalink=ticket.permalink, detail=detail
    )


def board_deltas(before: BoardSnapshot, after: BoardSnapshot) -> list[BoardDelta]:
    """What changed between two snapshots.

    Two absences are deliberately not news.

    The first is the first run: with nothing to compare against, every ticket
    on the board would report as newly opened and bury the day's actual
    movement under a backlog nobody asked about. Same rule a freshly cloned
    mirror gets from `pulse.MirrorStore`.

    The second is a ticket that fell out of the window. The query is bounded
    on `updated`, so a ticket vanishing from the result means "not touched
    lately", not "closed" - reading it as a close would announce every ticket
    the moment it went quiet, and announce it again when it came back.
    """
    if not before.tickets:
        return []
    found: list[BoardDelta] = []
    for key in sorted(after.tickets):
        now = after.tickets[key]
        prior = before.tickets.get(key)
        if prior is None:
            # A ticket filed and finished between two runs is a close, not an
            # open. Reported as "opened in Done" it reads as work starting,
            # and - worse - it never reaches `closed_unannounced`, which is
            # the whole reason the close case is tracked at all.
            kind = "closed" if now.done else "opened"
            found.append(_delta(kind, now, f"new -> {now.status}" if now.done else now.status))
            continue
        if now.done and not prior.done:
            # A close reported as another column move reads as still in
            # flight, which is the opposite of what it is.
            found.append(_delta("closed", now, f"{prior.status} -> {now.status}"))
        elif now.status != prior.status:
            found.append(_delta("moved", now, f"{prior.status} -> {now.status}"))
        if now.owner != prior.owner:
            found.append(
                _delta("reassigned", now, f"{prior.owner or 'nobody'} -> {now.owner or 'nobody'}")
            )
        if now.blocked and not prior.blocked:
            # Newly blocked only. A flag that stays up is re-reported every
            # run otherwise, and a line that appears every day stops being read.
            found.append(_delta("blocked", now, now.status))
    return found


# ---------------------------------------------------------------------------
# the ticket-key join
# ---------------------------------------------------------------------------


def keys_in(text: str) -> set[str]:
    """Ticket keys in a line, using `pulse`'s definition of one.

    A one-line delegation rather than a second regex - deliberately. A key is
    the spine of the whole pulse: it is what makes a Slack line, a branch, a
    PR title and a board row the same fact, and two definitions of what one
    looks like drift apart the first time a project puts a digit somewhere
    unusual in its key.
    """
    return Pulse._keys(text)


@dataclass(frozen=True)
class Mention:
    """That a line named a key, and where and when - never what it said."""

    permalink: str = ""
    at: float | None = None


@dataclass(frozen=True)
class Resolved:
    """One ticket key, and everything found under it across every source.

    `joined` composes rather than repeats `pulse.Joined` - the PR half of this
    is a fact `Pulse` already tracks, so it is read through, not copied.
    """

    key: str
    joined: Joined
    ticket: Ticket | None = None
    slack_permalink: str = ""
    in_branch: bool = False
    in_notes: bool = False

    @property
    def pr_number(self) -> int | None:
        return self.joined.pr_number

    @property
    def pr_state(self) -> str | None:
        return self.joined.pr_state

    @property
    def mentioned_in_slack(self) -> bool:
        return self.joined.mentioned_in_slack


def _first_permalink(mentions: Iterable[Mention]) -> str:
    return next((mention.permalink for mention in mentions if mention.permalink), "")


class BoardJoin:
    """Slack, branches, notes and the board, joined on the ticket key.

    The valuable half of SPEC 3.7: "he said he'd do the compute-engine
    consolidation" becomes a ticket, a PR and a status without anyone holding
    the mapping in their head.

    Text handed to an `observe_*` method is read for keys and then dropped -
    nothing a board row or a Slack message says can reach a report through
    this class, which is what makes hostile text in a hand-edited file
    uninteresting rather than dangerous.
    """

    def __init__(self, pulse: Pulse | None = None) -> None:
        self._pulse = pulse if pulse is not None else Pulse()
        self._tickets: dict[str, Ticket] = {}
        self._slack: dict[str, list[Mention]] = {}
        self._notes: dict[str, list[Mention]] = {}
        self._branches: set[str] = set()

    # -- observing ---------------------------------------------------------

    def observe_board(self, tickets: Iterable[Ticket]) -> None:
        for ticket in tickets:
            self._tickets[ticket.key] = ticket

    def observe_slack(self, text: str, *, permalink: str = "", at: object = None) -> None:
        """A Slack line.

        `at` matters more than it looks - it is the only thing that can later
        separate a mention that announced a close from one that merely
        predates it.
        """
        self._pulse.observe_slack(text)
        mention = Mention(permalink=_text(permalink, _PERMALINK_LIMIT), at=_epoch(at))
        for key in keys_in(text):
            self._slack.setdefault(key, []).append(mention)

    def observe_note(self, text: str, *, permalink: str = "") -> None:
        mention = Mention(permalink=_text(permalink, _PERMALINK_LIMIT))
        for key in keys_in(text):
            self._notes.setdefault(key, []).append(mention)

    def observe_branch(self, name: str) -> None:
        self._branches.update(keys_in(name))

    # -- reading back --------------------------------------------------------

    def resolve(self, key: str) -> Resolved:
        mentions = self._slack.get(key, ())
        return Resolved(
            key=key,
            joined=self._pulse.by_ticket(key),
            ticket=self._tickets.get(key),
            slack_permalink=_first_permalink(mentions),
            in_branch=key in self._branches,
            in_notes=key in self._notes,
        )

    def mentions_in_slack(self, key: str) -> tuple[Mention, ...]:
        return tuple(self._slack.get(key, ()))

    def talked_about(self) -> dict[str, tuple[Mention, ...]]:
        """Every key a human named, in Slack or in a meeting note.

        Branches are excluded on purpose: a branch named for a key is somebody
        already working, which is not the same claim as somebody saying a
        ticket exists - and it is that claim which is worth checking.
        """
        keys = set(self._slack) | set(self._notes)
        return {
            key: tuple(self._slack.get(key, ())) + tuple(self._notes.get(key, ()))
            for key in sorted(keys)
        }


# ---------------------------------------------------------------------------
# the two named failures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Discrepancy:
    """Something that does not line up. Surfaced, never resolved here."""

    kind: str
    key: str
    permalink: str = ""
    detail: str = ""


def tickets_never_made(join: BoardJoin, projects: Sequence[str]) -> list[Discrepancy]:
    """Keys people talk about that no watched board has.

    SPEC 3.7's first named case - *he believes there is a ticket, there is
    not*. Restricted to watched projects because every repo on earth has an
    `ABC-1` from somebody else's tracker in a commit message somewhere, and
    flagging those trains the reader to skip the whole block.

    See the module's KNOWN LIMIT: this is deliberately the weaker claim. A
    real backlog ticket outside the query's `updated` window looks identical
    to one that was never made, so the rendered phrase says what is actually
    known rather than asserting non-existence.
    """
    watched = {str(project).strip().casefold() for project in projects}
    return [
        Discrepancy(kind="ticket-never-made", key=key, permalink=_first_permalink(mentions))
        for key, mentions in join.talked_about().items()
        if key.split("-", 1)[0].casefold() in watched and join.resolve(key).ticket is None
    ]


def closed_unannounced(deltas: Iterable[BoardDelta], join: BoardJoin) -> list[Discrepancy]:
    """Closes nobody said anything about afterwards.

    SPEC 3.7's second named case - closed friday, still being asked about
    monday. The subtlety is the direction of time: "still working CDI-596" the
    day *before* the close announces nothing, so only a mention at or after
    the resolution timestamp counts, and a mention with no timestamp at all
    cannot be shown to be after it.

    A close whose resolution time is unknown is left alone rather than
    flagged: the claim being made is "nobody said so", and without a clock
    that claim cannot be made honestly.
    """
    found: list[Discrepancy] = []
    for delta in deltas:
        if delta.kind != "closed":
            continue
        ticket = join.resolve(delta.key).ticket
        closed_at = _epoch(ticket.resolved_at) if ticket is not None else None
        if closed_at is None:
            continue
        said_after = [
            mention
            for mention in join.mentions_in_slack(delta.key)
            if mention.at is not None and mention.at >= closed_at
        ]
        if not said_after:
            found.append(
                Discrepancy(
                    kind="closed-unannounced",
                    key=delta.key,
                    permalink=delta.permalink,
                    detail=delta.detail,
                )
            )
    return found


# ---------------------------------------------------------------------------
# evidence for the chaser
# ---------------------------------------------------------------------------


def apply_board_evidence(loop: dict[str, Any], deltas: Iterable[BoardDelta]) -> dict[str, Any]:
    """Mark a loop as moved on the strength of the board. Never close it.

    A ticket reaching Done is not proof the ask was satisfied - it may be a
    fraction of what was asked for, or somebody else's reading of it. Per SPEC
    principle 5 the agent surfaces and Nitin resolves, so the only thing this
    adds is a flag: every other field of the loop it was handed comes back
    unchanged, for every kind of delta and every status a loop can be in.
    """
    key = loop.get("key", "")
    moved = any(delta.key == key for delta in deltas)
    return {**loop, "evidence_of_movement": bool(moved)}


# ---------------------------------------------------------------------------
# the watchlist blocks
# ---------------------------------------------------------------------------

#: An Atlassian project key: 2-10 uppercase alphanumerics starting with a
#: letter. Matches `recipes._project_keys`'s own check exactly - reused rather
#: than restated, since the two describe the same fact: what a JQL will accept.
_PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")

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
            if _PROJECT_KEY.fullmatch(head):
                watchlist.projects.append(_project_from_fields(fields))
            elif _tried_to_name_one(head):
                watchlist.unparsed.append(body)
        elif head.isdigit():
            watchlist.org_projects.append(int(head))
        elif _tried_to_name_one(head):
            watchlist.unparsed.append(body)
    return watchlist


# ---------------------------------------------------------------------------
# what reaches the brief
# ---------------------------------------------------------------------------

#: Said out loud when a GitHub Projects board is watched. Projects v2 is
#: GraphQL-only: the REST endpoints return nothing useful rather than an
#: error, so an unread board would otherwise render as a quiet one - which is
#: the exact confusion guardrail 6 exists to prevent.
PROJECTS_V2_NOTE = "github projects board not read, projects v2 needs graphql"

#: How each kind of movement reads in a line. State changes, never activity:
#: there is no count anywhere in this table and there is not meant to be.
_PHRASING = {
    "opened": "opened in {detail}",
    "moved": "moved {detail}",
    "closed": "closed {detail}",
    "reassigned": "reassigned, {detail}",
    "blocked": "newly blocked in {detail}",
}

_DISCREPANCY_PHRASING = {
    "ticket-never-made": "talked about, but did not come back from the board",
    "closed-unannounced": "closed and nobody said so",
}


def _sourced(body: str, permalink: str) -> str:
    """One bullet, carrying the link that makes it checkable."""
    return f"- {body} ({permalink})" if permalink else f"- {body}"


@dataclass
class BoardReport:
    """The board block of a push, or nothing at all."""

    deltas: list[BoardDelta] = field(default_factory=list)
    discrepancies: list[Discrepancy] = field(default_factory=list)
    org_projects: list[int] = field(default_factory=list)
    #: Boards that could not be read this run. One line each, per guardrail 6 -
    #: expired Atlassian auth never takes a healthy source down with it.
    unavailable: list[str] = field(default_factory=list)

    def render(self) -> str:
        """The block, or an empty string.

        A quiet board produces nothing rather than "no updates". Silence is
        information; a line saying nothing happened is noise that teaches the
        reader to skim past the ones that matter.
        """
        lines: list[str] = []
        for delta in self.deltas:
            phrase = _PHRASING.get(delta.kind, delta.kind).format(detail=delta.detail)
            body = f"{delta.key} {phrase}"
            if delta.summary:
                body = f"{body}: {delta.summary}"
            lines.append(_sourced(body, delta.permalink))
        for found in self.discrepancies:
            phrase = _DISCREPANCY_PHRASING.get(found.kind, found.kind)
            lines.append(_sourced(f"{found.key} {phrase}", found.permalink))
        if self.org_projects:
            watched = ", ".join(str(number) for number in self.org_projects)
            lines.append(f"- {PROJECTS_V2_NOTE}: {watched}")
        lines.extend(f"- {entry}" for entry in self.unavailable)
        return "\n".join(lines)
