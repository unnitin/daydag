"""Running a loop: plan the fetch, hand it to the agent, assemble the push.

USING IT
    python -m daydag.run plan morning              # -> JSON: what to fetch
    python -m daydag.run render morning < payloads.json   # -> the push text

    plan("morning", now=now, identities=ids)       # -> Plan
    render("morning", now=now, identities=ids, payloads=payloads)   # -> str

    A payloads file is `{source: whatever the connector returned}`:
        {"calendar": [...], "slack": [...], "gmail": [...], "vault": "..."}

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
    Three loops are wired: morning, eod, week-ahead. Prep pings are not - they
    are the one interrupt and fire off a meeting's start time rather than a
    fetch plan, so they belong to a scheduler (#25), not here.

    The plan is advisory. Nothing verifies the agent actually ran the query it
    was given rather than one of its own, which is why every check that
    matters lives in `daydag.smoke` and runs on the payloads.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from daydag import brief, eod_wrap, recipes, week_ahead
from daydag.config import ConfigError, resolve_reference, timezone_for
from daydag.ledger import Ledger, Match, title_from_gemini_subject
from daydag.runlog import RunLog
from daydag.smoke import REACHED
from daydag.state import EventLog, NotesGap, StateFolder

__all__ = ["LOOPS", "Plan", "RunError", "Step", "main", "plan", "render"]

#: The loops that run off a fetch plan. Prep pings are absent on purpose - see
#: the module docstring's KNOWN LIMIT.
LOOPS = ("morning", "eod", "week-ahead")


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


def plan(loop: str, *, now: datetime, identities: Mapping[str, str]) -> Plan:
    """What the agent must fetch, with every bound the recipe already applies."""
    _known(loop)
    _aware(now)
    principal = _principal(identities)
    # The PRINCIPAL'S day, not the runner's. `now.date()` is the runner's
    # timezone, and `brief` renders the Pacific day - so between 5pm and
    # midnight Pacific the plan fetched one day while the brief reported
    # another, and the two would have disagreed on every evening run. Same
    # convention as `brief._local` and `recipes.timezone_for`.
    day = now.astimezone(timezone_for(identities)).date()

    window = recipes.calendar_day(day)
    overnight = recipes.slack_overnight(now, mentioning=principal, identities=identities)
    note = recipes.weekly_note(day)

    steps = [
        Step(
            "calendar",
            "one day, never a range - a five-day pull blew the output limit",
            {"day": str(window.day), "time_min": window.time_min, "time_max": window.time_max},
        ),
        Step(
            "slack",
            "search with this query verbatim; the id is already resolved",
            {"query": overnight.query, "min_ts": overnight.min_ts},
        ),
        Step(
            "gmail",
            "search, then fetch each thread in PLAIN_TEXT - results alone are metadata",
            {"query": recipes.gmail_gemini_notes(after=day, before=day)},
        ),
        Step(
            "vault",
            "read this note; it may not exist, which is itself a finding",
            {"path": f"Weekly Notes/{note}"},
        ),
    ]
    return Plan(loop=loop, at=now.isoformat(), steps=tuple(steps))


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

    def calendar(self, window: recipes.DayWindow) -> Sequence[Mapping[str, Any]]:
        return [self.timed(record) for record in self._records("calendar")]

    def slack(self, query: str) -> Sequence[Mapping[str, Any]]:
        return self._records("slack")

    def gmail(self, query: str) -> Sequence[Mapping[str, Any]]:
        return self._records("gmail")

    def weekly_note(self, path: str) -> str:
        if "vault" not in self._payloads:
            raise RunError("the weekly note was not read")
        return str(self._payloads["vault"])


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
    ledger = _remembered(events)
    _attach_notes(ledger, payloads, now)
    runner = RunLog(events, clock=lambda: now) if events is not None else None

    def _assemble() -> str:
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

    _remember(events, _seeded(payloads))
    if write_state and folder is not None and events is not None:
        _project(folder, events, ledger, now)
    return text


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
                # The mail's OWN timestamp. `ledger._in_window` only attaches a
                # note that arrived within six hours of the meeting ending, so
                # defaulting to `now` makes a note fetched the next morning
                # unmatchable - and a note that never attaches leaves its
                # meeting reported as a gap forever.
                arrived=stamp if isinstance(stamp, datetime) else now,
                attendees=[str(a) for a in (mail.get("attendees") or [])],
                # "gemini", not "gmail": `ARRIVAL_WINDOW` is keyed by the
                # system that WROTE the note, not the one that carried it, and
                # an unknown key falls back to the default window silently.
                source="gemini",
            )
        )


def _seeded(payloads: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = payloads.get("calendar")
    return [r for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []


def _project(folder: StateFolder, log: EventLog, ledger: Ledger, now: datetime) -> None:
    """Rewrite `State.md` from what this run learned.

    Through `write_state`'s own `_visible` gate rather than filtering here:
    the runner is not a second writer with its own idea of the rules, and the
    filter that a private carry-forward once slipped past is the one that has
    to hold.
    """
    folder.write_state(
        chase=log.chase_items(),
        notes_gaps=[NotesGap(title=gap) for gap in ledger.notes_gaps(now)],
    )


def main(argv: list[str] | None = None) -> int:
    """`plan` writes JSON to stdout; `render` reads payloads from stdin."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] not in {"plan", "render"}:
        print(
            "usage: python -m daydag.run {plan|render} {"
            + "|".join(LOOPS)
            + "} [--log PATH] [--write-state]"
        )
        return 2

    from pathlib import Path

    from daydag.config import Identities

    command, loop = args[0], args[1]
    # `--log <path>` is what makes a run remember: without it the ledger starts
    # empty every morning and a meeting seeded today cannot be a gap tomorrow.
    # `--write-state` projects what the run learned back into `State.md`.
    log = args[args.index("--log") + 1] if "--log" in args[:-1] else None
    write_state = "--write-state" in args
    try:
        identities = Identities.from_file(Path(".env"))
        now = datetime.now().astimezone()
        if command == "plan":
            print(json.dumps(plan(loop, now=now, identities=identities).to_dict(), indent=2))
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
                )
            )
    except (RunError, ConfigError) as bad:
        print(f"{bad}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
