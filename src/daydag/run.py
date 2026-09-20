"""Running a loop: plan the fetch, hand it to the agent, assemble the push.

USING IT
    python -m daydag.run plan morning              # -> JSON: what to fetch
    python -m daydag.run render morning < payloads.json   # -> the push text

    plan("morning", now=now, identities=ids)       # -> Plan
    render("morning", now=now, identities=ids, payloads=payloads)   # -> str

    A payloads file is `{source: whatever the connector returned}`:
        {"calendar": [...] | {"<day>": [...]},  # flat, or keyed by the step's day
         "slack": [...], "gmail": [...],
         "vault": "..." | null,                 # this week's note; null: not written
         "vault_notes": {"<path>": "..." | null}}   # every other note, by path

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
    4. `now` must be timezone-aware, because every loop refuses a naive one -
       the overnight cutoff is an hour of his day and guessing which day is
       not a thing to do quietly. The runner does not re-add the guess.
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
    All seven loops in `loops.LOOPS` render. Without `--for`, `prep` renders the
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
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from daydag import closure, loops, recipes
from daydag.config import principal_id, timezone_for
from daydag.eventlog import EventLog, classify_sensitivity
from daydag.ledger import Ledger, Match, title_from_gemini_subject
from daydag.loops import Context, RunError
from daydag.observe import REACHED, Result, RunLog
from daydag.people import People
from daydag.pulse import MirrorStore, Pulse, SyncReport, github_url, mirror_root, read_watchlist
from daydag.statedoc import NotesGap, StateFolder, StateNotWritable

__all__ = ["LOOPS", "Plan", "RunError", "Step", "main", "plan", "render"]

#: Every loop `SKILL.md` advertises, in `loops.LOOPS` order. Each one plans
#: and renders: `prep`, `ingestion` and `pulse` were once built, tested and
#: unreachable, so asking for "chase" got a refusal naming the other three (#95).
LOOPS = loops.NAMES


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


def plan(loop: str, *, now: datetime, identities: Mapping[str, str], selector: str = "") -> Plan:
    """What the agent must fetch, with every bound the recipe already applies."""
    _known(loop)
    _aware(now)
    principal = principal_id(identities, what="the principal", error=RunError)
    # The PRINCIPAL'S day, not the runner's. `now.date()` is the runner's
    # timezone, and the loops render the Pacific day - so between 5pm and
    # midnight Pacific the plan fetched one day while the brief reported
    # another, and the two would have disagreed on every evening run. Same
    # convention as `push.local` and `config.timezone_for`.
    tz = timezone_for(identities)
    day = now.astimezone(tz).date()

    spec = loops.LOOPS[loop]
    overnight = recipes.slack_overnight(now, mentioning=principal, identities=identities)
    steps: list[Step] = []
    for source in spec.fetches(selector):
        if source == "calendar":
            steps += [
                Step(
                    "calendar",
                    "one day, never a range - a five-day pull blew the output limit",
                    {
                        "day": str(window.day),
                        "time_min": window.time_min,
                        "time_max": window.time_max,
                    },
                )
                for window in recipes.loop_windows(loop, day, tz=tz, selector=selector)
            ]
        elif source == "slack":
            steps.append(
                Step(
                    "slack",
                    "search with this query verbatim (the id is already resolved), newest first;"
                    " page until a result's ts falls below min_ts, or the older edge of the"
                    " window is silently missing on a busy night",
                    {"query": overnight.query, "min_ts": overnight.min_ts},
                )
            )
        elif source == "gmail":
            # The window the MORNING will ask for, not today's: at 6:40am it
            # reports on yesterday's meetings, `after:<the evening the overnight
            # window opened>`. Open-ended on purpose: `before:` dropped a note
            # that arrived at 17:12 PDT, and notes genuinely land the next day
            # (a Sep 10 meeting's note at 00:56 PDT on Sep 11).
            steps.append(
                Step(
                    "gmail",
                    "search, then fetch each thread in PLAIN_TEXT - results alone are metadata",
                    {"query": recipes.gmail_gemini_notes(after=_overnight_opened(overnight))},
                )
            )
        elif source == "vault":
            # `weekly_note` already returns the full connector-relative path,
            # prefix included. Prepending a folder to it named nothing.
            steps.append(
                Step(
                    "vault",
                    "read this note; it may not exist, which is itself a finding",
                    {"path": _weekly_path(now, identities)},
                )
            )
        elif source == "vault_notes":
            # Under `vault_notes`, keyed by path, because `vault` already means
            # one specific note and overloading it would make "which note is
            # missing" unanswerable. The read and this fetch arrive together or
            # the section never renders, however well either half works alone.
            steps.append(
                Step(
                    "vault_notes",
                    "read each; absent is the finding - put it under `vault_notes` keyed by path",
                    {"paths": spec.extra_notes(day) if spec.extra_notes else []},
                )
            )
        elif source == "closure":
            # The chaser reads the file he corrects by hand and then READS THE
            # REPLIES - `closure` contract 1. One read per ask.
            steps += _closure_reads(identities)
        elif source == "git":
            # No connector fetch at all: `pulse` reads the git mirrors on disk.
            steps.append(
                Step(
                    "git",
                    "no connector fetch - sync the mirrors and pass the Pulse to render",
                    {"watchlist": recipes.vault_relative("DayDAG", "Watchlist.md")},
                )
            )
    return Plan(loop=loop, at=now.isoformat(), steps=tuple(steps))


def _weekly_path(now: datetime, identities: Mapping[str, str]) -> str:
    """The weekly note's path for the principal's day - what `plan` emits under
    `vault` and what `render` answers `vault_note(<this path>)` from. Computed
    in one place so the alias and the fetch cannot name different weeks."""
    return recipes.weekly_note(now.astimezone(timezone_for(identities)).date())


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
    """The date the overnight window opened - what the morning loop keys its
    gmail query off, so the plan asks for the same thing the consumer will."""
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
    on the push's own degrade path as one named line instead of taking the
    push down - see contract 3.

    ``weekly_path`` is the note the plan asked for under `vault`; that key is
    shorthand for ``vault_notes[weekly_path]`` (#112), so every note reaches a
    loop through the one read, `vault_note(path)`.
    """

    def __init__(self, payloads: Mapping[str, Any], *, weekly_path: str = "") -> None:
        self._payloads = payloads
        notes = payloads.get("vault_notes")
        self._notes: dict[str, Any] = dict(notes) if isinstance(notes, Mapping) else {}
        if weekly_path and "vault" in payloads:
            self._notes.setdefault(weekly_path, payloads["vault"])

    #: Every field on a calendar record that is an instant. Both ends, not just
    #: `start`: an unparsed `end` made the ledger raise and the whole notes-gap
    #: mechanism degrade to "couldn't check the meeting ledger" on every run -
    #: see `test_the_ledger_gets_real_instants_for_both_ends_of_a_meeting`.
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

        Reads only what `timed` leaves behind - a `datetime`, google's all-day
        `{"date": ...}` untouched, or a value neither understood - never a
        second parse of the string `_as_instant` just parsed.
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

        Two shapes. Keyed by the day each step fetched (#111), a window comes
        back exactly as fetched: an unfetched day is empty because its key is
        absent, and an untimed record lives under the day it was fetched for.
        The flat list is filtered by each record's own start - the wrap asks
        for TOMORROW and the week-ahead for seven days one window at a time,
        and unfiltered they were served a different day's events and could
        not tell (`test_a_window_the_plan_never_fetched_comes_back_empty_not_wrong`).
        In the flat shape a record whose start cannot be placed is returned
        for every window rather than dropped: losing its ledger row loses a
        notes gap permanently, and `Ledger.seed_day` absorbs the repeat.
        """
        if "calendar" not in self._payloads:
            raise RunError("calendar was not fetched")
        keyed = recipes.day_keyed(self._payloads["calendar"])
        if keyed is not None:
            return [self.timed(record) for record in keyed.get(window.day, [])]
        records = [self.timed(record) for record in self._records("calendar")]
        return [r for r in records if self._day_of(r) in (window.day, None)]

    def slack(self, query: str) -> Sequence[Mapping[str, Any]]:
        return self._records("slack")

    def gmail(self, query: str) -> Sequence[Mapping[str, Any]]:
        return self._records("gmail")

    def vault_note(self, path: str) -> str:
        """One vault note in its three states, mapped once for every reader.

        `RunError` means the vault could not be reached for this path
        (degrade); ``None`` means the note does not exist (`FileNotFoundError`
        - the finding); text means it was read, and "" is a note that exists
        and is empty. `str(None)` once rendered a note body as the word None.
        """
        if path not in self._notes:
            raise RunError(f"{path} was not read")
        value = self._notes[path]
        if value is None:
            raise FileNotFoundError(path)
        return str(value)


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
        if not isinstance(payload, Mapping):
            continue
        try:
            ledger.seed_day([_Payloads.timed(dict(payload))])
        except (KeyError, TypeError, ValueError):
            # A stored row the ledger cannot take - an all-day entry whose start
            # is still google's `{"date": ...}` (#156), a row from before
            # `_remember` kept to `_seedable` - is skipped, not raised on: the
            # log's own torn-row rule (`EventLog` contract 3), one layer up.
            # This runs outside any degrade, and one such row was killing every
            # later morning with that log until someone deleted it from sqlite.
            continue
    return ledger


def _remember(log: EventLog | None, events: Iterable[Mapping[str, Any]]) -> None:
    """Record what today's ledger seeded, so the next run can ask what produced nothing.

    Whole, as a mapping: a google record carries its own `kind`
    (`calendar#event`), and splatting it into `EventLog.record(kind, ...)`
    raised on the first real morning with a log. Given `_seedable` rows, not
    every fetched record: one `seed_day` cannot take today is one it cannot
    take tomorrow either, and stored it was replayed into every later morning.
    """
    if log is None:
        return
    for event in events:
        if isinstance(event, Mapping) and event.get("id"):
            log.record(MEETING, event)


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
    spec = loops.LOOPS[loop]
    sources = _Payloads(payloads, weekly_path=_weekly_path(now, identities))
    folder = state if state is not None else _vault(identities)
    events = log if log is None else EventLog.open(log)
    directory = People(events) if events is not None and spec.needs_ledger else None
    if not spec.needs_ledger:
        # `chase`, `ingest`, `ship` and `week-ahead` never read this ledger
        # (week-ahead builds its own), so rehydrating it is a full replay of the
        # meeting table for nothing - and that table grows with every run.
        ledger = Ledger()
    else:
        ledger = _remembered(events)
        # SEED TODAY BEFORE OFFERING NOTES. `loops._seed_and_gaps` seeds during
        # assembly, which is too late: a note offered to a ledger with no rows
        # attaches to nothing, so every meeting became a gap while its note was
        # listed by name in the section above. `seed_day` is idempotent per
        # instance, so the loop's own seeding stays a no-op.
        ledger.seed_day(_seedable(payloads))
        _attach_notes(ledger, payloads, now)
        if directory is not None:
            _observe_past(directory, ledger, identities, now)
    runner = RunLog(events, clock=lambda: now) if events is not None else None
    context = Context(
        now=now,
        identities=identities,
        sources=sources,
        payloads=payloads,
        folder=folder,
        log=events,
        ledger=ledger,
        pulse=pulse,
        directory=directory,
        selector=selector,
    )

    if runner is None:
        text = spec.render(context)
    else:
        # A run that dies halfway is exactly the one somebody opens the log
        # for, so the row is written either way and the failure re-raised.
        with runner.run(f"loop: {loop}") as active:
            text = spec.render(context)
            active.observe([Result(loop, "daydag", REACHED)])

    if spec.remembers:
        _remember(events, _seedable(payloads))
    # Only the ledger-carrying loops project. Since `update_state` became an
    # append (#130) an empty ledger adds nothing and harms nothing; what the
    # gate still does is keep `chase`, `ingest`, `ship` and `week-ahead` from
    # filing their derived items, which is a separate decision from this one.
    if write_state and spec.needs_ledger and folder is not None and events is not None:
        try:
            _project(folder, events, ledger, now, runner)
        except StateNotWritable as unwritable:
            # Guardrail 6: one line, never a dead push. The push is already
            # assembled and `main` prints the RETURN VALUE, so letting this
            # propagate would throw away a complete push over a file the writer
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

    Also what `_remember` stores, for the same reason.

    Tolerant on purpose. This pre-seed exists so a note offered in the same run
    has a row to attach to; it is not the place that judges a malformed event.
    `loops._seed_and_gaps` still refuses one loudly during assembly, which is
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
    """Every calendar record the agent fetched, whichever shape it came in."""
    raw = payloads.get("calendar")
    keyed = recipes.day_keyed(raw)
    if keyed is not None:
        raw = [record for day in keyed.values() for record in day]
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
