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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from daydag import brief, eod_wrap, recipes, week_ahead
from daydag.config import ConfigError, resolve_reference, timezone_for
from daydag.ledger import Ledger
from daydag.state import StateFolder

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

    def _timed(self, record: Any) -> Any:
        if not isinstance(record, Mapping):
            return record
        parsed = {name: self._instant(record[name]) for name in self._INSTANTS if name in record}
        return {**record, **parsed} if parsed else record

    def _records(self, name: str) -> list[Mapping[str, Any]]:
        if name not in self._payloads:
            raise RunError(f"{name} was not fetched")
        value = self._payloads[name]
        if not isinstance(value, list):
            raise RunError(f"{name} came back as {type(value).__name__}, not a list of records")
        return value

    def calendar(self, window: recipes.DayWindow) -> Sequence[Mapping[str, Any]]:
        return [self._timed(record) for record in self._records("calendar")]

    def slack(self, query: str) -> Sequence[Mapping[str, Any]]:
        return self._records("slack")

    def gmail(self, query: str) -> Sequence[Mapping[str, Any]]:
        return self._records("gmail")

    def weekly_note(self, path: str) -> str:
        if "vault" not in self._payloads:
            raise RunError("the weekly note was not read")
        return str(self._payloads["vault"])


def render(
    loop: str,
    *,
    now: datetime,
    identities: Mapping[str, str],
    payloads: Mapping[str, Any],
    state: StateFolder | None = None,
) -> str:
    """The push text for ``loop``, assembled from what the agent fetched."""
    _known(loop)
    _aware(now)
    sources = _Payloads(payloads)
    ledger = Ledger()

    if loop == "morning":
        return brief.assemble(
            now=now, sources=sources, identities=identities, state=state, ledger=ledger
        ).render()
    if loop == "eod":
        return eod_wrap.assemble(now=now, sources=sources, ledger=ledger).render()
    return week_ahead.assemble(
        now=now, sources=sources, identities=identities, state=state
    ).render()


def main(argv: list[str] | None = None) -> int:
    """`plan` writes JSON to stdout; `render` reads payloads from stdin."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] not in {"plan", "render"}:
        print("usage: python -m daydag.run {plan|render} {" + "|".join(LOOPS) + "}")
        return 2

    from pathlib import Path

    from daydag.config import Identities

    command, loop = args[0], args[1]
    try:
        identities = Identities.from_file(Path(".env"))
        now = datetime.now().astimezone()
        if command == "plan":
            print(json.dumps(plan(loop, now=now, identities=identities).to_dict(), indent=2))
        else:
            payloads = json.load(sys.stdin)
            print(render(loop, now=now, identities=identities, payloads=payloads))
    except (RunError, ConfigError) as bad:
        print(f"{bad}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
