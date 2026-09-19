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
    3. A missing payload DEGRADES. A source the agent could not reach raises
       out of `_Payloads` and comes back through `push.Reader` as one
       "couldn't check X" line, with the push still shipping (guardrail 6). So
       is a payload of the wrong shape: the agent hands back whatever the
       connector said, and a string where a list belongs is a bad fetch, not a
       reason to lose the other three sources.
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
    NEXT meeting worth prepping; with it, the one he named (`prep.select`).
    Either way its points come from the overnight Slack payload rather than
    the row-specific searches `prep.sources` would build, because the two-phase
    plan cannot know the row before the fetch. FIRING a prep ping at a
    meeting's start time is scheduling, and belongs to #25, not here.

    The plan is advisory. Nothing verifies the agent actually ran the query it
    was given rather than one of its own, which is why every check that
    matters lives in `daydag.observe` and runs on the payloads.
"""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from daydag import brief, closure, eod_wrap, push, recipes, week_ahead
from daydag.config import resolve_reference, timezone_for
from daydag.eventlog import EventLog
from daydag.ingestion import classify_items, unplaced
from daydag.ledger import Ledger, Match, title_from_gemini_subject
from daydag.observe import REACHED, Result, RunLog
from daydag.people import People
from daydag.prep import HORIZON_DAYS, Audience, Reason, build, point, prep_worthy, select
from daydag.pulse import MirrorStore, Pulse, SyncReport, github_url, mirror_root, read_watchlist
from daydag.statedoc import NotesGap, StateFolder, StateNotWritable, classify_sensitivity

__all__ = ["LOOPS", "Plan", "RunError", "Step", "main", "plan", "render"]

#: Every loop `SKILL.md` advertises, and each one must plan and render:
#: `prep`, `ingestion` and `pulse` were once built, tested and unreachable, so
#: asking for "chase" got a refusal naming the other three (#95).
#:
#: `ship` is the odd one - it reads local git mirrors, not a connector, so its
#: plan has no fetch steps at all. See `recipes.loop_windows` for the rest.
LOOPS = ("morning", "eod", "week-ahead", "prep", "ingest", "chase", "ship")

#: Loops that READ the rehydrated ledger - and therefore the only loops whose
#: `--write-state` may project notes gaps. `week-ahead` builds its own ledger;
#: `chase`, `ingest` and `ship` never look at one. Gating on the no-calendar set
#: instead handed chase an EMPTY ledger and let `_project` blank the notes-gaps
#: section a morning run had just written - see
#: `test_chase_with_write_state_does_not_wipe_the_notes_gaps_a_morning_wrote`.
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
    # convention as `push.local` and `config.timezone_for`.
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

    windows = recipes.loop_windows(loop, day, tz=tz, selector=selector)
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
            # The window the BRIEF will ask for, not today's: at 6:40am it
            # reports on yesterday's meetings, `after:<the evening the overnight
            # window opened>`. `_Payloads.gmail` serves whatever was fetched
            # whatever query it is handed, so a today-only window here would
            # disagree with the brief in silence.
            #
            # Open-ended on purpose. `before:` dropped a note that arrived at
            # 17:12 PDT because gmail put it past the day boundary, and its
            # meeting was then reported as having no notes while the note sat
            # in the mailbox. Notes also genuinely arrive the next day: a
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
    if loop == "chase":
        # The chaser reads the file he corrects by hand and then READS THE
        # REPLIES - `closure` contract 1, and the 2026-09-18 failure its
        # WHY IT EXISTS records. So the plan is one read per ask: the
        # conversation after it, and its thread. The generic steps above fetch
        # nothing this loop uses.
        return Plan(loop=loop, at=now.isoformat(), steps=tuple(_closure_reads(identities)))
    if loop == "prep" and selector:
        # `_prep` reads the calendar and the Slack payload, nothing else. No
        # note can attach to a meeting that has not happened, and the weekly
        # note is never read - so gmail and vault were two connector round-trips
        # for nothing, the same waste `loop_windows` exists to prevent.
        steps = [step for step in steps if step.source in {"calendar", "slack"}]
    return Plan(loop=loop, at=now.isoformat(), steps=tuple(steps))


def _extra_notes(loop: str, day: date) -> list[Step]:
    """Vault notes a loop reads BEYOND its own weekly note.

    Only the EOD wrap has any: on a Friday it reports whether next week's plan
    and next week's meeting prep actually landed, reading both through
    `sources.vault_note`. That method and this fetch have to arrive together or
    the section never renders, however well either half works alone.

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


def _closure_reads(identities: Mapping[str, str]) -> list[Step]:
    """One `slack` step per checkable ask in `State.md` and `Decisions.md`.

    Without a vault there is nothing to check, and the plan says so in one
    step rather than emitting an empty list that reads as "nothing owed".
    """
    folder = _vault(identities)
    if folder is None:
        return [Step("vault", "no vault configured - nothing to chase", {})]
    try:
        state_text = folder.read_state()
    except OSError:
        return [Step("vault", "couldn't read State.md - nothing to chase", {})]
    try:
        decisions_text = folder.decisions_path.read_text(encoding="utf-8")
    except OSError:
        decisions_text = ""
    reads = closure.closure_steps(closure.asks_in(state_text, decisions_text))
    steps = [
        Step(
            "vault",
            "State.md and Decisions.md were read to build the steps below; no payload needed",
            {"state": str(folder.state_path), "decisions": str(folder.decisions_path)},
        )
    ]
    steps += [Step("slack", read.how, read.to_dict()) for read in reads]
    if not reads:
        steps.append(
            Step("slack", "no ask carries a slack permalink - nothing can be verified", {})
        )
    return steps


def _overnight_opened(window: recipes.OvernightWindow) -> Any:
    """The date the overnight window opened - what `brief` keys its gmail
    query off, so the plan asks for the same thing the consumer will."""
    return datetime.fromtimestamp(window.min_ts, tz=recipes.PACIFIC).date()


def _as_instant(value: Any) -> Any:
    """A JSON timestamp as a `datetime`, or the value untouched.

    The one place this seam can go wrong quietly. `push.local` returns None
    unless the value is a `datetime` OBJECT, and the agent fetches over MCP,
    where every instant is a string - so payloads passed through untouched
    rendered every meeting "all day", right title and wrong time, on every
    real run.

    Google nests it as `{"dateTime": ...}`; an all-day event carries
    `{"date": ...}` and has no instant, which stays untouched and renders
    as the all-day it actually is. An unparseable string also stays put:
    `push.local` will read it as no instant, which is a meeting without a
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


class _Payloads:
    """A `push.Sources` served from what the agent fetched.

    A source the agent could not reach is simply absent, and a source it
    fetched badly is the wrong shape. Both raise here, which is what puts them
    on the brief's own degrade path as one named line instead of taking the
    push down - see contract 3.
    """

    def __init__(self, payloads: Mapping[str, Any]) -> None:
        self._payloads = payloads

    #: Every field on a calendar record that is an instant. Both ends, not just
    #: `start`: an unparsed `end` made the ledger raise and the whole notes-gap
    #: mechanism degrade to "couldn't check the meeting ledger" on every run -
    #: see `test_the_ledger_gets_real_instants_for_both_ends_of_a_meeting`. A
    #: meeting with no row today is a gap that can never be surfaced tomorrow.
    _INSTANTS = ("start", "end")

    @classmethod
    def timed(cls, record: Any) -> Any:
        """A record with every instant field parsed. Shared with the replay
        path, which reads the same JSON back out of the event log."""
        if not isinstance(record, Mapping):
            return record
        parsed = {name: _as_instant(record[name]) for name in cls._INSTANTS if name in record}
        return {**record, **parsed} if parsed else record

    def _records(self, name: str) -> list[Mapping[str, Any]]:
        if name not in self._payloads:
            raise RunError(f"{name} was not fetched")
        found = recipes.records(self._payloads[name])
        if found is None:
            kind = type(self._payloads[name]).__name__
            raise RunError(f"{name} came back as {kind}, not a list of records")
        return found

    @staticmethod
    def _day_of(record: Mapping[str, Any]) -> date | None:
        """The local date a `timed()` record starts on, or None if unplaceable.

        Reads only what `timed` leaves behind: a `datetime` for anything it
        could parse, google's all-day `{"date": ...}` untouched, or a value
        neither understood. Deliberately not a second parse of the string
        `_instant` just parsed - one field parsed in two places is a field an
        alias can be added to in only one of them.
        """
        start = record.get("start")
        if isinstance(start, datetime):
            # Naive means already his wall-clock, same contract as `push.local`.
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

        Filtered, and that is the whole point. Unfiltered, a consumer asking
        for a day the plan never fetched - `eod_wrap` asks for TOMORROW,
        `week_ahead` for seven days one window at a time - was served a
        different day's events and could not tell, silently
        (`test_a_window_the_plan_never_fetched_comes_back_empty_not_wrong`).
        Wrong data in a push is worse than a missing section, because a missing
        one says so. An unfetched window now comes back empty.

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

    @staticmethod
    def _note(value: Any, path: str) -> str:
        """One vault note in its three states, mapped once for both readers.

        The caller raising `RunError` first means the vault could not be
        reached (degrade); ``None`` means the note does not exist
        (`FileNotFoundError` - the finding); text means it was read, and ""
        is a note that exists and is empty. `str(None)` once rendered a note
        body as the word None.
        """
        if value is None:
            raise FileNotFoundError(path)
        return str(value)

    def weekly_note(self, path: str) -> str:
        """THIS week's note, under `vault` - see `_note` for the three states."""
        if "vault" not in self._payloads:
            raise RunError("the weekly note was not read")
        return self._note(self._payloads["vault"], path)

    def vault_note(self, path: str) -> str:
        """Any other vault note, under `vault_notes` keyed by path."""
        notes = self._payloads.get("vault_notes")
        if not isinstance(notes, Mapping):
            raise RunError(f"{path} was not read")
        return self._note(notes.get(path), path)


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

    THE reason this module exists rather than `Ledger()` being enough. Today's
    rows are seeded so that tomorrow can report the ones that produced nothing,
    and a ledger rebuilt empty every run has no tomorrow: "meetings w/ no notes"
    could only ever report the empty set, and an empty section is omitted rather
    than labelled, so it failed silently.

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


def _chase(
    folder: StateFolder | None,
    log: EventLog | None,
    *,
    fetched: Mapping[str, Any] | None = None,
    principal: str = "",
) -> str:
    """What is owed to him and what he owes, each verified against its thread.

    KNOWN LIMIT: not marked against the 2-business-day clock. That is the
    chaser's work (#18) and needs asked-on dates this loop does not have.

    Reads `State.md` rather than deriving the list: it is the file he CORRECTS
    BY HAND, and CLAUDE.md is explicit that a hand edit is an event which wins
    over derived state. A chaser that rebuilt the list from Slack every run
    would silently undo every correction he made.

    ``fetched`` is the `closure` payload, keyed by each ask's permalink. An ask
    that is not in it renders as "couldn't verify", never as open (`closure`
    contract 1); absent altogether, the push says the reads were skipped rather
    than reporting every line open.

    The nudges are drafted, never sent - guardrail 1. Nothing here addresses
    anyone but him.
    """
    if folder is None:
        return "owed to you: couldn't check - no vault is configured"
    try:
        written = folder.read_state()
    except Exception:
        return "owed to you: couldn't read State.md"
    try:
        decisions = folder.decisions_path.read_text(encoding="utf-8")
    except OSError:
        decisions = ""

    asks = closure.asks_in(written, decisions)
    if not asks:
        return "owed to you: nothing open"

    verdicts = closure.judge(asks, fetched, principal=principal)
    established = closure.Closure(tuple(verdicts))
    sections: list[push.Section] = []
    for name in ("Chase list", "Owed by you", "Pending decisions"):
        sections += closure.render_closure(verdicts, section=name)
    unreachable: list[str] = []
    if fetched is None and established.read_nothing:
        # Not one thread was read. Say so once, up top, rather than letting
        # three "couldn't verify" buckets stand in for the reads that were
        # skipped - the plan named them, and skipping them is the bug.
        unreachable.append("the replies - no `closure` payload, so nothing here is verified")

    # Items the log is carrying that the file has not got to yet. `_visible`
    # is the ONE gate on sensitivity and it lives in `statedoc` (its contract
    # 3); this reads what that gate already let through rather than making a
    # second judgement.
    if log is not None:
        try:
            carried = [
                item for item in log.chase_items() if str(item.get("ask", "")) not in written
            ]
        except Exception:
            carried = []
        if carried:
            sections.append(
                push.Section(
                    f"not yet in State.md ({len(carried)})",
                    # Through `claim`, so the row's own quote and permalink
                    # render - house rule 1 - and the line is bulleted like the
                    # section above it. Bare `owner: ask` strings dropped both
                    # and were invisible to `push.unsourced_claims`.
                    tuple(
                        push.claim(
                            f"{item.get('owner', 'someone')} · {item.get('ask', '')}",
                            item.get("permalink") or None,
                            quote=item.get("quote") or None,
                        )
                        for item in carried
                    ),
                )
            )
    return push.render_push(
        f"open loops - {established.open_count} open, verified", sections, unreachable
    )


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

    # `item_id`, not `id`: that is the key `classify_items` reads, and an item
    # without it comes back UNPLACED under a blank name.
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
        push.Section("placed", tuple(f"- {label}: {n}" for label, n in sorted(counts.items())))
    ]
    # `ingestion.unplaced` is the one definition of "could not be placed"; a
    # second predicate here stopped following it the moment the first changed.
    if missing := unplaced(placed):
        sections.append(
            push.Section(
                f"unplaced ({len(missing)}) - these need you, not a guess",
                tuple(f"- {item_id}" for item_id in missing),
            )
        )
    return push.render_push(
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
    consulted - that gate answers "is this worth interrupting him for", a
    question about an unprompted ping. A standup he asks to have prepped is a
    standup he gets prepped.

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
        # `recipes.loop_windows`, so what was fetched and what can match are one
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
    # `push.short`, not a raw slice: the kernel holds the quote budget, the
    # one-line collapse, and the mid-word ellipsis that stops a cut quote from
    # reading as verbatim (house rule 1). `or ""` because a file-only message
    # carries `"text": null`, and str(None) is the word None quoted as his.
    points: list[Any] = []
    for message in payloads.get("slack", []):
        if not isinstance(message, Mapping) or not message.get("permalink"):
            continue
        text = push.short(message.get("text") or "")
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


def build_pulse(
    identities: Mapping[str, str],
    log: EventLog | None,
    *,
    url_for: Callable[[Any], str] = github_url,
) -> tuple[Pulse, SyncReport]:
    """The pulse for this run: mirrors synced, each read on from its stored cursor.

    Built here because `render` takes a pulse and nothing else makes one:
    without this, `ship` degraded on every real run and the shipping sections of
    the morning, the wrap and the week-ahead never rendered (#138). Cursors come
    from the event log and go back to it (`store_cursors`) after the push has
    rendered - without that every run starts at first sight and reports a quiet
    day forever.
    """
    folder = _vault(identities)
    if folder is None:
        raise RunError("no vault is configured, so there is no Watchlist.md to read repos from")
    watchlist = read_watchlist(folder.watchlist_path)
    store = MirrorStore(mirror_root(identities), url_for=url_for, log=log)
    cursors: dict[str, str] = {}
    if log is not None:
        for repo in watchlist.repos:
            cursor = log.last_cursor(repo.slug)
            if cursor:
                cursors[repo.slug] = cursor
    report = store.sync(watchlist, cursors=cursors)
    return Pulse.from_sync(report), report


def store_cursors(log: EventLog, report: SyncReport) -> None:
    """Remember where each mirror read up to. After the render, never before:
    the cursor advances when the pulse lists its items, and a cursor stored
    ahead of a push that then failed would bury those landings."""
    for mirror in report.mirrors:
        if not mirror.stale:
            log.record_cursor(mirror.label, mirror.cursor)


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
        # (week-ahead builds its own), so rehydrating it is a full replay of the
        # meeting table for nothing - and that table grows with every run, so
        # the waste grows with the log.
        ledger = Ledger()
    else:
        ledger = _remembered(events)
        # SEED TODAY BEFORE OFFERING NOTES. `brief._seed_and_gaps` seeds during
        # assembly, which is too late: a note offered to a ledger with no rows
        # attaches to nothing, and a FIRST run has no rehydrated rows either -
        # so every meeting became a gap while its note was listed by name in the
        # section above. `seed_day` is idempotent per instance, so brief's own
        # seeding stays a no-op.
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
            return _chase(
                folder,
                events,
                fetched=payloads.get("closure"),
                principal=identities.get("SLACK_USER_PRINCIPAL", ""),
            )
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
            active.observe([Result(loop, "daydag", REACHED)])

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
            _project(folder, events, ledger, now, runner)
        except StateNotWritable as unwritable:
            # Guardrail 6: one line, never a dead push. The brief is already
            # assembled and `main` prints the RETURN VALUE, so letting this
            # propagate would throw away a complete brief over a file the writer
            # declined to touch.
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

    The only production caller of `Ledger.offer_note`. Unwired, no note ever
    attaches to a row, so every meeting is a gap forever and "meetings w/ no
    notes" becomes every meeting every day - noise, which is worse than the
    absence it was meant to replace.

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
        stamp = _as_instant(mail.get("arrived") or mail.get("date") or mail.get("internalDate"))
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


def _project(
    folder: StateFolder,
    log: EventLog,
    ledger: Ledger,
    now: datetime,
    runner: RunLog | None = None,
) -> None:
    """Add what this run learned to `State.md`, leaving the rest alone.

    Including the run's own line under `## Run log` (SPEC 7): `timestamp ·
    loop · reached · skipped`, free text withheld. `observe.RunLog` renders
    that line; this is the only thing that carries it into the file.

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
        run_lines=[runner.projection_line(runner.current_loop)] if runner is not None else (),
    )


def main(argv: list[str] | None = None) -> int:
    """``python -m daydag.run {plan|render} <loop> ...`` - the one CLI, entered here."""
    from daydag.cli import main as cli_main

    return cli_main(["run", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
