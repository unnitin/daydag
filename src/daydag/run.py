"""Running a loop: plan the fetch, hand it to the agent, assemble the push.

USING IT
    python -m daydag.run plan morning              # -> JSON: what to fetch
    python -m daydag.run render morning < payloads.json   # -> the push text

    plan("morning", now=now, identities=ids)       # -> Plan
    render("morning", now=now, identities=ids, payloads=payloads)   # -> str

    A payloads file is `{source: whatever the connector returned}`:
        {"calendar": [...], "slack": [...], "gmail": [...],
         "vault": "..." | null,                 # null: the note does not exist
         "vault_notes": {"<path>": "..." | null}}   # eod's Friday reads, by path

CONTRACTS - break one and the guarantee is gone
    1. This module FETCHES NOTHING. Python cannot call an MCP connector; the
       agent can. So a loop runs in two halves with the fetch in between, and
       the package keeps holding no client - the tripwire in
       `tests/test_guardrails.py` stays true because it stays true by
       construction, not by discipline.
    2. The plan carries the recipe's OWN bounds, not a restatement of them.
       One calendar day, an id-scoped Slack query, a bounded Jira window. An
       agent following the plan cannot widen a window by accident, which is
       how a 5-day calendar pull returned 156,681 chars and never arrived.
    3. A missing payload DEGRADES. A source the agent could not reach is one
       "couldn't check X" line and the push still ships (guardrail 6). So is a
       payload of the wrong shape: the agent hands back whatever the connector
       said, and a string where a list belongs is a bad fetch, not a reason to
       lose the other three sources.
    4. `now` must be timezone-aware, because `brief.assemble` refuses a naive
       one - the overnight cutoff is an hour of his day and guessing which day
       is not a thing to do quietly. The runner does not re-add the guess.
    5. Rendering is not sending. This returns text; `daydag.delivery` is the
       only thing that posts, and it has no destination parameter.

WHY IT EXISTS
    Every module took its world as an argument and nothing supplied one. The
    logic was complete and unrunnable: `Sources` is a Protocol with no
    implementation, so a brief could be assembled in a test and nowhere else.

    This is the missing half, and its shape is forced rather than chosen.
    Python in this runtime cannot reach Slack, Gmail or Calendar - only the
    vault and `gh` are direct. The agent holds the connectors. So the seam
    goes exactly where the capability boundary already is.

KNOWN LIMIT
    All seven loops in `LOOPS` render. Without `--for`, `prep` renders the
    NEXT meeting worth prepping; with it, the one he named (`prep_selector`).
    Either way its points come from the overnight Slack payload rather than
    the row-specific searches `prep.sources` would build, because the two-phase
    plan cannot know the row before the fetch. FIRING a prep ping at a
    meeting's start time is scheduling, and belongs to #25, not here.

    The plan is advisory. Nothing verifies the agent actually ran the query it
    was given rather than one of its own, which is why every check that
    matters lives in `daydag.smoke` and runs on the payloads.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from daydag import brief, eod_wrap, recipes, week_ahead
from daydag.config import ConfigError, resolve_reference, timezone_for
from daydag.ingestion import classify_items, unplaced
from daydag.ledger import Ledger, Match, title_from_gemini_subject
from daydag.people import People
from daydag.prep import Audience, Reason, build, point, prep_worthy
from daydag.prep_selector import HORIZON_DAYS, select
from daydag.runlog import RunLog
from daydag.smoke import REACHED
from daydag.state import (
    EventLog,
    NotesGap,
    StateFolder,
    StateNotWritable,
    classify_sensitivity,
    read_section,
)

__all__ = ["LOOPS", "Plan", "RunError", "Step", "main", "plan", "render"]

#: Every loop `SKILL.md` advertises. Three of these used to be absent, and the
#: skill advertised them anyway: `prep.py`, `ingestion.py` and `pulse.py` were
#: built and tested with no way to reach them, so asking for "chase" got a
#: refusal naming the other three (#95).
#:
#: `ship` is the odd one - it reads local git mirrors, not a connector, so its
#: plan has no fetch steps at all. See `_calendar_windows` for the rest.
LOOPS = ("morning", "eod", "week-ahead", "prep", "ingest", "chase", "ship")

#: Loops whose plan asks for no calendar at all.
_NO_CALENDAR = frozenset({"ingest", "chase", "ship"})

#: Loops that READ the rehydrated ledger - and therefore the only loops whose
#: `--write-state` may project notes gaps. `week-ahead` builds its own ledger;
#: `chase`, `ingest` and `ship` never look at one. The first gate was
#: `_NO_CALENDAR`, which handed chase an EMPTY ledger and then let `_project`
#: write `notes_gaps=[]` over the section the morning run had just recorded.
_NEEDS_LEDGER = frozenset({"morning", "eod", "prep"})


class RunError(RuntimeError):
    """A loop was asked for something it cannot do, in the caller's terms."""


@dataclass(frozen=True)
class Step:
    """One fetch the agent must perform before the loop can be assembled."""

    source: str
    #: Human-readable, for the agent following the plan by hand.
    how: str
    #: Machine-readable: the query, path or window the recipe produced.
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "how": self.how, "detail": dict(self.detail)}


@dataclass(frozen=True)
class Plan:
    """Everything one loop needs fetched, with the recipe's bounds attached."""

    loop: str
    at: str
    steps: tuple[Step, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"loop": self.loop, "at": self.at, "steps": [s.to_dict() for s in self.steps]}


def _known(loop: str) -> str:
    if loop not in LOOPS:
        raise RunError(f"{loop!r} is not a loop; try one of {', '.join(LOOPS)}")
    return loop


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise RunError(
            "now must be timezone-aware - the overnight cutoff is an hour of his day, "
            "and which day that is cannot be guessed from a naive value"
        )
    return now


def _principal(identities: Mapping[str, str]) -> str:
    return resolve_reference(
        "${SLACK_USER_PRINCIPAL}", identities, what="the principal", error=RunError
    )


def plan(loop: str, *, now: datetime, identities: Mapping[str, str], selector: str = "") -> Plan:
    """What the agent must fetch, with every bound the recipe already applies."""
    _known(loop)
    _aware(now)
    principal = _principal(identities)
    # The PRINCIPAL'S day, not the runner's. `now.date()` is the runner's
    # timezone, and `brief` renders the Pacific day - so between 5pm and
    # midnight Pacific the plan fetched one day while the brief reported
    # another, and the two would have disagreed on every evening run. Same
    # convention as `brief._local` and `recipes.timezone_for`.
    tz = timezone_for(identities)
    day = now.astimezone(tz).date()

    if loop == "ship":
        # No connector steps at all. `pulse` reads the git mirrors on disk, so
        # this loop is the one that genuinely needs nothing fetched - the plan
        # says so rather than emitting four steps whose payloads it ignores.
        return Plan(
            loop=loop,
            at=now.isoformat(),
            steps=(
                Step(
                    "git",
                    "no connector fetch - sync the mirrors and pass the Pulse to render",
                    {"watchlist": recipes.vault_relative("DayDAG", "Watchlist.md")},
                ),
            ),
        )

    windows = _calendar_windows(loop, day, tz=tz, selector=selector)
    overnight = recipes.slack_overnight(now, mentioning=principal, identities=identities)
    note = recipes.weekly_note(day)

    steps = [
        Step(
            "calendar",
            "one day, never a range - a five-day pull blew the output limit",
            {"day": str(window.day), "time_min": window.time_min, "time_max": window.time_max},
        )
        for window in windows
    ] + [
        Step(
            "slack",
            "search with this query verbatim (the id is already resolved), newest first;"
            " page until a result's ts falls below min_ts, or the older edge of the"
            " window is silently missing on a busy night",
            {"query": overnight.query, "min_ts": overnight.min_ts},
        ),
        Step(
            "gmail",
            "search, then fetch each thread in PLAIN_TEXT - results alone are metadata",
            # The window the BRIEF will ask for, not today's. At 6:40am the
            # brief reports on yesterday's meetings and asks gmail for
            # `after:<the evening the overnight window opened>`. A closed
            # today-only window fetched the wrong mail, and `_Payloads.gmail`
            # serves whatever was fetched regardless of the query it is handed,
            # so the two disagreed in silence.
            #
            # Open-ended on purpose. `before:` dropped a note that arrived at
            # 17:12 PDT because gmail put it past the day boundary, and its
            # meeting was then reported as having no notes - while the note
            # sat in the mailbox. Notes also genuinely arrive the next day: a
            # Sep 10 meeting's note landed 00:56 PDT on Sep 11.
            {"query": recipes.gmail_gemini_notes(after=_overnight_opened(overnight))},
        ),
        Step(
            "vault",
            "read this note; it may not exist, which is itself a finding",
            # `weekly_note` already returns the full connector-relative path,
            # prefix included. Prepending a folder to it named nothing.
            {"path": note},
        ),
        *_extra_notes(loop, day),
    ]
    if loop == "prep" and selector:
        # `_prep` reads the calendar and the Slack payload, nothing else. No
        # note can attach to a meeting that has not happened, and the weekly
        # note is never read - so gmail and vault were two connector round-trips
        # for nothing, the same waste `_NO_CALENDAR` exists to prevent.
        steps = [step for step in steps if step.source in {"calendar", "slack"}]
    return Plan(loop=loop, at=now.isoformat(), steps=tuple(steps))


def _extra_notes(loop: str, day: date) -> list[Step]:
    """Vault notes a loop reads BEYOND its own weekly note.

    Only the EOD wrap has any: on a Friday it reports whether next week's plan
    and next week's meeting prep actually landed, reading both through
    `sources.vault_note`. The plan never asked for them, so even once that
    method existed there was nothing for it to serve - the fix and the fetch
    have to arrive together or the section still never renders.

    They go under `vault_notes`, keyed by path, because `vault` already means
    one specific note and overloading it would make "which note is missing"
    unanswerable.
    """
    if loop != "eod":
        return []
    next_week = day + timedelta(days=7)
    return [
        Step(
            "vault_notes",
            "read each; absent is the finding - put it under `vault_notes` keyed by path",
            {"paths": [recipes.weekly_note(next_week), recipes.meeting_prep(next_week)]},
        )
    ]


def _calendar_windows(
    loop: str, day: date, *, tz: Any = recipes.PACIFIC, selector: str = ""
) -> list[recipes.DayWindow]:
    """The calendar windows this LOOP will actually ask its sources for.

    Every loop used to get the same single window - the principal's today -
    because the plan never looked at `loop` at all. Each consumer then asked
    for something else and was served today's events anyway, silently:

        morning      today                    matched, by luck
        eod          TOMORROW                 served today
        week-ahead   next mon-sun, 7 windows  served today, seven times

    So the two loops nobody had run were both fetching the wrong days. Kept in
    step with the consumers deliberately - `eod_wrap` and `week_ahead` derive
    their windows from these same `recipes` helpers, so the arithmetic (and
    the Monday-of-next-week rule) lives in one place rather than two.
    """
    if loop in _NO_CALENDAR:
        # `ingest` reads mail, `chase` reads the chase list he maintains by
        # hand. Neither looks at the calendar, and fetching a day they ignore
        # is a connector round-trip for nothing.
        return []
    if loop == "prep" and selector:
        # A NAMED prep searches the week, not today - the meeting he wants
        # prepped is usually not today's, that is why he named it. Seven
        # windows, in HIS zone: an earlier version recomputed the day in
        # hardcoded Pacific and fetched an eighth day the match then discarded,
        # so a London principal got windows a day off and the answer "nothing
        # matches" for a meeting that existed. `_prep` derives its `until` from
        # this same arithmetic, so fetch and match are one set.
        return recipes.calendar_days(day, day + timedelta(days=HORIZON_DAYS - 1), tz=tz)
    if loop == "eod":
        # `eod_wrap` reads exactly one window, and it is tomorrow's: the wrap
        # reports the day that just ended and previews the first meeting of
        # the next one.
        return [recipes.calendar_day(day + timedelta(days=1))]
    if loop == "week-ahead":
        this_monday, _ = recipes.week_range(day)
        next_monday = this_monday + timedelta(days=7)
        # Seven requests, never one wide one - a five-day pull measured 156,681
        # characters and exceeded the connector's output limit (#2 audit).
        return recipes.calendar_days(next_monday, next_monday + timedelta(days=6))
    return [recipes.calendar_day(day)]


def _overnight_opened(window: recipes.OvernightWindow) -> Any:
    """The date the overnight window opened - what `brief` keys its gmail
    query off, so the plan asks for the same thing the consumer will."""
    return datetime.fromtimestamp(window.min_ts, tz=recipes.PACIFIC).date()


class _Payloads:
    """A `brief.Sources` served from what the agent fetched.

    A source the agent could not reach is simply absent, and a source it
    fetched badly is the wrong shape. Both raise here, which is what puts them
    on the brief's own degrade path as one named line instead of taking the
    push down - see contract 3.
    """

    def __init__(self, payloads: Mapping[str, Any]) -> None:
        self._payloads = payloads

    @staticmethod
    def _instant(value: Any) -> Any:
        """A JSON timestamp as a `datetime`, or the value untouched.

        The one place this seam can go wrong quietly. `brief._local` returns
        None unless the value is a `datetime` OBJECT, and the agent fetches
        over MCP, where every instant is a string - so passing payloads
        through untouched rendered every meeting "all day", right title and
        wrong time, on every real run. No test caught it because tests build
        datetimes directly.

        Google nests it as `{"dateTime": ...}`; an all-day event carries
        `{"date": ...}` and has no instant, which stays untouched and renders
        as the all-day it actually is. An unparseable string also stays put:
        `_local` will read it as no instant, which is a meeting without a
        time rather than a meeting at a guessed one.
        """
        if isinstance(value, Mapping):
            value = value.get("dateTime") or value.get("date_time") or value
        if not isinstance(value, str):
            return value
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value

    #: Every field on a calendar record that is an instant. `start` alone was
    #: parsed and `end` was not - one field, not its twin - so the ledger got a
    #: string, raised, and the whole notes-gap mechanism degraded to "couldn't
    #: check the meeting ledger" on every run. That is the differentiator
    #: quietly not working: a meeting with no row today is a gap that can never
    #: be surfaced tomorrow.
    _INSTANTS = ("start", "end")

    @classmethod
    def timed(cls, record: Any) -> Any:
        """A record with every instant field parsed. Shared with the replay
        path, which reads the same JSON back out of the event log."""
        if not isinstance(record, Mapping):
            return record
        parsed = {name: cls._instant(record[name]) for name in cls._INSTANTS if name in record}
        return {**record, **parsed} if parsed else record

    def _records(self, name: str) -> list[Mapping[str, Any]]:
        if name not in self._payloads:
            raise RunError(f"{name} was not fetched")
        value = self._payloads[name]
        if not isinstance(value, list):
            raise RunError(f"{name} came back as {type(value).__name__}, not a list of records")
        return value

    @staticmethod
    def _day_of(record: Mapping[str, Any]) -> date | None:
        """The local date a `timed()` record starts on, or None if unplaceable.

        Reads only what `timed` leaves behind: a `datetime` for anything it
        could parse, google's all-day `{"date": ...}` untouched, or a value
        neither of them understood. It used to re-run the string parse `_instant`
        had just done one line earlier - two parsers of one field in one class,
        which an alias added to one would silently not reach in the other.
        """
        start = record.get("start")
        if isinstance(start, datetime):
            # Naive means already his wall-clock, same contract as `brief._local`.
            return (start.astimezone(recipes.PACIFIC) if start.tzinfo else start).date()
        if isinstance(start, date):
            return start
        if isinstance(start, Mapping):  # all-day: {"date": "YYYY-MM-DD"}
            try:
                return date.fromisoformat(str(start.get("date", "")))
            except ValueError:
                return None
        return None

    def calendar(self, window: recipes.DayWindow) -> Sequence[Mapping[str, Any]]:
        """The fetched events that fall on ``window``'s day.

        Filtered, and that is the whole point. This used to return the entire
        bucket for ANY window, so a consumer asking for a day the plan never
        fetched was served a different day's events and could not tell:

        * `eod_wrap` asks for TOMORROW and was handed today, so the wrap
          printed this morning's 8:15 standup as tomorrow's first meeting.
        * `week_ahead` asks for seven days, one window at a time, and was
          handed the same single day seven times over.

        Neither raised. Wrong data in a push is worse than a missing section,
        because a missing one says so. Now an unfetched window comes back
        empty, which is a section omitted rather than a section that lies.

        A record whose start cannot be placed at all is returned for every
        window rather than dropped: an untimed meeting is still a meeting, and
        losing its ledger row loses a notes gap permanently. `Ledger.seed_day`
        keys on (id, start) and so absorbs the repeat.
        """
        records = [self.timed(record) for record in self._records("calendar")]
        return [r for r in records if self._day_of(r) in (window.day, None)]

    def slack(self, query: str) -> Sequence[Mapping[str, Any]]:
        return self._records("slack")

    def gmail(self, query: str) -> Sequence[Mapping[str, Any]]:
        return self._records("gmail")

    def weekly_note(self, path: str) -> str:
        """The weekly note, in the THREE states `brief.read_vault_note` tells apart.

        It splits on exception type - text, `FileNotFoundError` for a note that
        was never written, anything else for a source it could not reach - and
        this layer could only ever produce two of the three. `null` had no
        meaning, so an agent reporting "the file is not there" had to choose
        between omitting the key, which renders "couldn't check the weekly
        note" and reads as a downed connector, and sending "", which renders
        NOTHING AT ALL because an empty note is a note that was read.

        The third state is the one that is true: the note is hand-written, and
        the series has had a gap for weeks, so every real run meets it. It is
        also the one worth saying out loud, because the brief cannot triage the
        day against a plan of record that does not exist.

        `str(None)` also used to render the note's body as the literal text
        "None".

            key absent  -> could not reach the vault   (RunError -> degrade)
            null        -> the note does not exist     (FileNotFoundError)
            ""          -> it exists and is empty
            text        -> the note
        """
        if "vault" not in self._payloads:
            raise RunError("the weekly note was not read")
        note = self._payloads["vault"]
        if note is None:
            raise FileNotFoundError(path)
        return str(note)

    def vault_note(self, path: str) -> str:
        """Any vault note by path, carried in `vault_notes`.

        `eod_wrap` reads next week's plan and next week's meeting prep through
        this to report whether Friday's planning actually landed. The `Sources`
        protocol never declared it, so this class never implemented it, so both
        reads raised and the whole "friday - weekly-planning outcome" section
        was dropped on every real run. Both test doubles have the method, which
        is exactly why nothing failed.

        Same three states as `weekly_note`, one level down:

            vault_notes absent       -> could not read any of them (degrade)
            path absent from the map -> that note does not exist
            null                     -> that note does not exist
            text                     -> the note
        """
        notes = self._payloads.get("vault_notes")
        if not isinstance(notes, Mapping):
            raise RunError(f"{path} was not read")
        note = notes.get(path)
        if note is None:
            raise FileNotFoundError(path)
        return str(note)


#: The event kind a seeded meeting is recorded under, so the next run can
#: rehydrate it. Meetings are not sensitive as a class - a title can be, which
#: is what `NotesGap.sensitivity` is for on the way back out.
MEETING = "meeting"


def _vault(identities: Mapping[str, str]) -> StateFolder | None:
    """The `DayDAG/` folder, or None when no vault is configured.

    Optional because a test and a first run both have none, and because a
    missing vault must not stop a brief - it is one absent section, not a
    stall (guardrail 6).
    """
    root = identities.get("VAULT_ROOT")
    if not root:
        return None
    return StateFolder.create(Path(root) / "DayDAG")


def _remembered(log: EventLog | None) -> Ledger:
    """A ledger carrying every meeting seeded on a previous run.

    THE reason this module exists rather than `Ledger()` being enough.
    `_seed_and_gaps` seeds today's rows precisely so that tomorrow can report
    the ones that produced nothing - and a ledger rebuilt empty every run has
    no tomorrow. "meetings w/ no notes" could only ever report the empty set,
    and an empty section is omitted rather than labelled, so the one thing
    nothing else in the system can produce failed silently.

    Replayed through `seed_day`, which is idempotent per instance, rather than
    given a second persistence API inside `Ledger` - the composition belongs
    here, not in the thing being composed.
    """
    ledger = Ledger()
    if log is None:
        return ledger
    for payload in log.recorded(MEETING):
        if isinstance(payload, Mapping):
            ledger.seed_day([_Payloads.timed(dict(payload))])
    return ledger


def _remember(log: EventLog | None, events: Iterable[Mapping[str, Any]]) -> None:
    """Record today's meetings so the next run can ask what produced nothing."""
    if log is None:
        return
    for event in events:
        if isinstance(event, Mapping) and event.get("id"):
            log.record(MEETING, **{k: _jsonable(v) for k, v in event.items()})


def _jsonable(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _chase(folder: StateFolder | None, log: EventLog | None) -> str:
    """What is owed to him, oldest first.

    Not yet marked against the 2-business-day clock - that is the chaser's
    work (#18) and needs asked-on dates this loop does not have. An earlier
    docstring promised the marks; nothing produced them.

    Reads `State.md` rather than deriving the list: it is the file he CORRECTS
    BY HAND, and CLAUDE.md is explicit that a hand edit is an event which wins
    over derived state. A chaser that rebuilt the list from Slack every run
    would silently undo every correction he made.

    The nudges are drafted, never sent - guardrail 1. Nothing here addresses
    anyone but him.
    """
    if folder is None:
        return "owed to you: couldn't check - no vault is configured"
    try:
        written = folder.read_state()
    except Exception:
        return "owed to you: couldn't read State.md"

    lines = read_section(written, "Chase list", top_level=True)
    if not lines:
        return "owed to you: nothing open"

    # The same shape the morning brief gives the same file: each bullet through
    # `split_link` -> `claim`, so a trailing permalink renders as `(link)` and an
    # item without one is admitted as unsourced - which is what lets the shared
    # `brief.unsourced_claims` check see this loop's output at all. Hand-rolled
    # `f"- {line}"` bullets were a second format for one list.
    sections = [brief.Section("chase list", tuple(brief.line_from_state(b) for b in lines))]

    # Items the log is carrying that the file has not got to yet. `_visible`
    # is the ONE gate on sensitivity and it lives in `state`; this reads what
    # that gate already let through rather than making a second judgement.
    if log is not None:
        try:
            carried = [
                item for item in log.chase_items() if str(item.get("ask", "")) not in written
            ]
        except Exception:
            carried = []
        if carried:
            sections.append(
                brief.Section(
                    f"not yet in State.md ({len(carried)})",
                    # Through `claim`, so the row's own quote and permalink
                    # render - house rule 1 - and the line is bulleted like the
                    # section above it. Bare `owner: ask` strings dropped both
                    # and were invisible to `brief.unsourced_claims`.
                    tuple(
                        brief.claim(
                            f"{item.get('owner', 'someone')} · {item.get('ask', '')}",
                            item.get("permalink") or None,
                            quote=item.get("quote") or None,
                        )
                        for item in carried
                    ),
                )
            )
    return brief.render_push(f"owed to you ({len(lines)})", sections, ())


def _ingest(sources: _Payloads) -> str:
    """Classify what landed, and say plainly what could not be placed.

    `classify_items` returns `label=None` for an item it cannot place, and the
    whole point of surfacing those is that a guess here becomes a vault write
    later. Invariant 4: surface, do not resolve.
    """
    try:
        mail = list(sources.gmail(""))
    except RunError:
        return "ingest: couldn't check gmail"
    if not mail:
        return "ingest: nothing new landed"

    # `item_id`, not `id`: `classify_items` reads that key and returns an item
    # with no id as UNPLACED rather than under a made-up one - so the wrong key
    # here made every item unplaceable, with a blank name to show for it.
    items = [
        {
            "item_id": str(m.get("id") or m.get("subject") or n),
            "text": str(m.get("subject", "")),
        }
        for n, m in enumerate(mail)
        if isinstance(m, Mapping)
    ]
    placed = classify_items(items)
    counts = Counter(record.label for record in placed if record.label)
    sections = [
        brief.Section("placed", tuple(f"- {label}: {n}" for label, n in sorted(counts.items())))
    ]
    # `ingestion.unplaced` is the one definition of "could not be placed"; a
    # second predicate here stopped following it the moment the first changed.
    if missing := unplaced(placed):
        sections.append(
            brief.Section(
                f"unplaced ({len(missing)}) - these need you, not a guess",
                tuple(f"- {item_id}" for item_id in missing),
            )
        )
    return brief.render_push(
        f"ingest: {len(items)} item{'' if len(items) == 1 else 's'}", sections, ()
    )


def _prep(
    now: datetime,
    identities: Mapping[str, str],
    payloads: Mapping[str, Any],
    ledger: Ledger,
    selector: str = "",
    directory: People | None = None,
) -> str:
    """The meeting to prep for, and what to raise in it.

    Without a selector: the NEXT qualifying one, because a prep ping is the only
    push allowed to interrupt (`prep.may_interrupt`) and is worth exactly as
    much as its timing.

    With one: the meeting he NAMED, and `prep_worthy` is deliberately not
    consulted. That gate answers "is this worth interrupting him for", which is
    a question about an unprompted ping. He asked - a standup he wants prepped
    is a standup he gets prepped, and refusing on the grounds that it is a
    standup would be the tool arguing with the request.

    Several matches surface as several (`Selection.render`). Prep for the wrong
    meeting is worse than none: he reads it, trusts it, and walks into the other
    one cold.
    """
    # The directory when a log exists, so leadership is what he has said about
    # people rather than a CSV; the CSV is unioned in either way (#119).
    audience = (
        Audience.from_directory(directory, identities)
        if directory is not None
        else Audience.from_identities(identities)
    )

    if selector:
        tz = timezone_for(identities)
        day = now.astimezone(tz).date()
        # The END of the last window the plan fetched - same arithmetic as
        # `_calendar_windows`, so what was fetched and what can match are one
        # set rather than a 7-day fetch against an 8-day bound.
        until = datetime.combine(day + timedelta(days=HORIZON_DAYS), time.min, tzinfo=tz)
        try:
            found = select(
                ledger.open_rows(),
                selector,
                now=now,
                until=until,
                # EMAIL_PRINCIPAL, not SLACK_USER_PRINCIPAL: `Row.attendees`
                # holds email addresses, and a Slack id compared against one
                # matches nothing - the exclusion would be dead while looking
                # wired. Resolved the way every other identity is, so a missing
                # key REFUSES instead of silently switching the skip off.
                principal=resolve_reference(
                    "${EMAIL_PRINCIPAL}", identities, what="the principal's address", error=RunError
                ),
                tz=tz,
            )
        except ValueError as bad:
            raise RunError(str(bad)) from bad  # one line on stderr, not a traceback
        row = found.one
        if row is None:
            return found.render()
        # ASKED_FOR, unconditionally. He named it; the ping rules are not
        # consulted, so they must not be credited - a named 1:1 stamped "1:1"
        # would make the rules look better than they are.
        return build(row, Reason.ASKED_FOR, _points(payloads)).render()

    # `open_rows` is already oldest-first; filtering keeps that order.
    for row in (r for r in ledger.open_rows() if r.start >= now):
        reason = prep_worthy(row, audience)
        if reason is None:
            continue
        return build(row, reason, _points(payloads)).render()
    return "prep: nothing coming up that needs it"


def _points(payloads: Mapping[str, Any]) -> list[Any]:
    """Talking points from what the agent fetched, evidence or nothing.

    A message with no permalink is dropped rather than quoted: house rule 1 is
    that a claim carries its link, and a prep point he cannot click through to
    is one he has to take on trust in a meeting.
    """
    # `brief.short`, not a raw slice: it collapses a multi-line message onto
    # the one line `Point.render` has, marks a mid-word cut with an ellipsis
    # rather than presenting it as verbatim (house rule 1), and holds the quote
    # budget in one place. `or ""` because a file-only message carries
    # `"text": null`, and str(None) is the word None quoted as if he said it.
    points: list[Any] = []
    for message in payloads.get("slack", []):
        if not isinstance(message, Mapping) or not message.get("permalink"):
            continue
        text = brief.short(message.get("text") or "")
        if not text:
            continue
        points.append(
            point(
                text,
                "raised since you last met",
                quote=text,
                permalink=message.get("permalink"),
                source="slack",
            )
        )
        if len(points) == 3:
            break
    return points


def render(
    loop: str,
    *,
    now: datetime,
    identities: Mapping[str, str],
    payloads: Mapping[str, Any],
    state: StateFolder | None = None,
    log: Path | str | None = None,
    pulse: Any = None,
    write_state: bool = False,
    selector: str = "",
) -> str:
    """The push text for ``loop``, assembled from what the agent fetched.

    ``log`` is the event log's path. Given one, the loop REMEMBERS: meetings
    seeded today are readable tomorrow, and the run leaves a row either way.
    Without one every run starts blank, which is correct for a test and wrong
    for a morning.
    """
    _known(loop)
    _aware(now)
    sources = _Payloads(payloads)
    folder = state if state is not None else _vault(identities)
    events = log if log is None else EventLog.open(log)
    directory = People(events) if events is not None and loop in _NEEDS_LEDGER else None
    if loop not in _NEEDS_LEDGER:
        # `chase`, `ingest`, `ship` and `week-ahead` never read this ledger
        # (week-ahead builds its own). Rehydrating every remembered meeting and
        # offering every note to it is a full replay of the meeting table for a
        # loop that then ignores the result - and the table grows with every
        # run, so the waste grows with the log.
        ledger = Ledger()
    else:
        ledger = _remembered(events)
        # SEED TODAY BEFORE OFFERING NOTES. `brief._seed_and_gaps` seeds during
        # assembly, which is too late: a note offered to a ledger that has no
        # rows yet attaches to nothing, and on a FIRST run there are no
        # rehydrated rows either - so every meeting became a gap while its note
        # was listed by name in the section directly above. `seed_day` is
        # idempotent per instance, so brief's own seeding stays a no-op.
        ledger.seed_day(_seedable(payloads))
        _attach_notes(ledger, payloads, now)
        if directory is not None:
            _observe_past(directory, ledger, identities, now)
    runner = RunLog(events, clock=lambda: now) if events is not None else None

    def _assemble() -> str:
        if loop == "ship":
            # Nothing fetched, so nothing to serve - `pulse` already read the
            # mirrors. Absent, it degrades like any other source (guardrail 6)
            # rather than raising: one missing line, not a dead push.
            if pulse is None:
                return "shipped: couldn't check - no pulse was built"
            return pulse.render()
        if loop == "chase":
            return _chase(folder, events)
        if loop == "ingest":
            return _ingest(sources)
        if loop == "prep":
            return _prep(now, identities, payloads, ledger, selector, directory)
        if loop == "morning":
            return brief.assemble(
                now=now,
                sources=sources,
                identities=identities,
                state=folder,
                ledger=ledger,
                pulse=pulse,
            ).render()
        if loop == "eod":
            return eod_wrap.assemble(now=now, sources=sources, ledger=ledger, pulse=pulse).render()
        return week_ahead.assemble(
            now=now, sources=sources, identities=identities, state=folder, pulse=pulse
        ).render()

    if runner is None:
        text = _assemble()
    else:
        # A run that dies halfway is exactly the one somebody opens the log
        # for, so the row is written either way and the failure re-raised.
        with runner.run(f"loop: {loop}") as active:
            text = _assemble()
            active.observe([{"name": loop, "source": "daydag", "status": REACHED, "reason": ""}])

    if loop != "prep":
        # A prep is a QUESTION about the week ahead, not a day's seeding.
        # Remembering its seven fetched days persisted every future meeting;
        # one cancelled after the snapshot was re-seeded on every later run and
        # reported as a permanent "meeting w/ no notes", and a rescheduled one
        # became two rows - a phantom gap beside the real meeting.
        _remember(events, _seeded(payloads))
    # Only the ledger-carrying loops project. The original reason - that a loop
    # handed an empty ledger would REPLACE the notes-gaps section with nothing -
    # stopped applying when `update_state` became an append (#130): projecting
    # from an empty ledger now adds nothing and harms nothing. What the gate
    # still does is keep `chase`, `ingest`, `ship` and `week-ahead` from filing
    # their derived items, and whether it should is a separate decision from
    # this one, so it stays as it is rather than being widened in passing.
    if write_state and loop in _NEEDS_LEDGER and folder is not None and events is not None:
        try:
            _project(folder, events, ledger, now)
        except StateNotWritable as unwritable:
            # Guardrail 6: one line, never a dead push. The brief is already
            # assembled at this point and `main` prints the RETURN VALUE, so
            # letting this propagate threw away a complete brief over a file
            # the writer declined to touch - the loudest possible failure for
            # the most conservative possible refusal.
            text += f"\n\n- couldn't update State.md: {unwritable}"
    return text


def _observe_past(
    directory: People, ledger: Ledger, identities: Mapping[str, str], now: datetime
) -> None:
    """Teach the directory who was in the meetings that have already HAPPENED.

    Past only. A named prep seeds seven future days, and observing those would
    record him as having met people at meetings not yet held. Skipped entirely
    when EMAIL_PRINCIPAL is unset: without it he would be added to his own
    directory on every row.
    """
    principal = str(identities.get("EMAIL_PRINCIPAL", "") or "")
    if not principal:
        return
    for row in ledger.open_rows():
        if row.end <= now:
            directory.observe(row, principal=principal)


def _attach_notes(ledger: Ledger, payloads: Mapping[str, Any], now: datetime) -> None:
    """Offer every fetched Gemini note to the ledger.

    `Ledger.offer_note` was called by five test files and by NO production
    code - the matching half of the ledger existed and was never wired, the
    same way `title_from_gemini_subject` was once dead code the docs described
    as live. Unwired, no note ever attaches to a row, so every meeting is a
    gap forever and "meetings w/ no notes" becomes every meeting every day.
    Noise, which is worse than the absence it was meant to replace.

    A note whose title is ambiguous attaches to nothing and stays in
    `ambiguous()` - surface, do not resolve.
    """
    notes = payloads.get("gmail")
    if not isinstance(notes, list):
        return
    for mail in notes:
        if not isinstance(mail, Mapping):
            continue
        title = title_from_gemini_subject(str(mail.get("subject", "")))
        stamp = _Payloads._instant(
            mail.get("arrived") or mail.get("date") or mail.get("internalDate")
        )
        ledger.offer_note(
            Match(
                title=title,
                # The mail's OWN timestamp. `ledger._in_window` attaches a
                # note only inside `ARRIVAL_WINDOW` of the meeting ending, so
                # defaulting to `now` makes a note fetched the next morning
                # unmatchable - and a note that never attaches leaves its
                # meeting reported as a gap unless the calendar declared one.
                arrived=stamp if isinstance(stamp, datetime) else now,
                attendees=[str(a) for a in (mail.get("attendees") or [])],
                # "gemini", not "gmail": `ARRIVAL_WINDOW` is keyed by the
                # system that WROTE the note, not the one that carried it, and
                # an unknown key falls back to the default window silently.
                source="gemini",
            )
        )


def _seedable(payloads: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Today's events, parsed, and only the ones `seed_day` can actually take.

    Tolerant on purpose. This pre-seed exists so a note offered in the same run
    has a row to attach to; it is not the place that judges a malformed event.
    `brief._seed_and_gaps` still refuses one loudly during assembly, which is
    where a missing `end` should surface - skipping it twice would hide it.
    """
    needed = ("id", "start", "end", "summary")
    ready = []
    for raw in _seeded(payloads):
        event = dict(_Payloads.timed(raw))
        if all(event.get(key) for key in needed):
            ready.append(event)
    return ready


def _seeded(payloads: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = payloads.get("calendar")
    return [r for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []


def _project(folder: StateFolder, log: EventLog, ledger: Ledger, now: datetime) -> None:
    """Add what this run learned to `State.md`, leaving the rest alone.

    Through `update_state`'s own `_visible` gate rather than filtering here:
    the runner is not a second writer with its own idea of the rules, and the
    filter that a private carry-forward once slipped past is the one that has
    to hold.

    A notes gap is a meeting TITLE, and a title can be the sensitive fact - an
    exit interview, a comp conversation. Meeting rows are not a vault-bound
    kind (they reach the file through the ledger, not through `chase_items`),
    so the mark the gate reads is decided here, per title, by the same
    classifier every recorded item goes through. Unmarked would mean visible.
    """
    folder.update_state(
        chase=log.chase_items(),
        notes_gaps=[
            NotesGap(title=gap, sensitivity=classify_sensitivity(gap))
            for gap in ledger.notes_gaps(now)
        ],
    )


def main(argv: list[str] | None = None) -> int:
    """`plan` writes JSON to stdout; `render` reads payloads from stdin."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] not in {"plan", "render"}:
        print(
            "usage: python -m daydag.run {plan|render} {"
            + "|".join(LOOPS)
            + '} [--log PATH] [--write-state] [--for "<meeting or person>"]'
        )
        return 2

    from daydag.config import Identities

    command, loop = args[0], args[1]
    # `--log <path>` is what makes a run remember: without it the ledger starts
    # empty every morning and a meeting seeded today cannot be a gap tomorrow.
    # `--write-state` projects what the run learned back into `State.md`.
    log = args[args.index("--log") + 1] if "--log" in args[:-1] else None
    write_state = "--write-state" in args
    # `--for` names the meeting to prep. Without it `prep` takes the next
    # qualifying one, which is the scheduled ping's behaviour.
    selector = ""
    if "--for" in args:
        after = args[args.index("--for") + 1 :]
        if not after or after[0].startswith("--"):
            # Falling through to the next-qualifying meeting here would prep a
            # meeting he did not ask about and say nothing - the wrong-meeting
            # failure prep_selector calls worse than no prep.
            print("--for needs a meeting or a person after it", file=sys.stderr)
            return 2
        selector = after[0]
    try:
        identities = Identities.from_file(Path(".env"))
        now = datetime.now().astimezone()
        if command == "plan":
            built = plan(loop, now=now, identities=identities, selector=selector)
            print(json.dumps(built.to_dict(), indent=2))
        else:
            payloads = json.load(sys.stdin)
            print(
                render(
                    loop,
                    now=now,
                    identities=identities,
                    payloads=payloads,
                    log=log,
                    write_state=write_state,
                    selector=selector,
                )
            )
    except (RunError, ConfigError) as bad:
        print(f"{bad}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
