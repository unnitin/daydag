"""The five-day gate, and the number that decides whether it passed.

USING IT
    journal = Soak(EventLog.open(path))
    journal.shipped(date(2026, 9, 14))          # a brief went out
    journal.note("drop the emoji", day=...)     # one edit he asked for
    journal.folded("drop the emoji")            # it is in the skill now
    report = journal.report()                   # -> Report
    report.passed, report.why, report.repeats

CONTRACTS
    1. A day with NO edits still counts as shipped. The best morning is the one
       with nothing to log, and a gate that counted only edits would score it
       as a day that never happened.
    2. `passed` needs three things, not one: five days shipped, the count
       trending down, and no outstanding repeat. Any of the three alone
       flatters.
    3. A repeat is the same edit on two DIFFERENT days. Twice in one sitting is
       one correction.
    4. Nothing here touches the vault. Edits quote the brief, which carries
       meeting titles and names.

WHY IT EXISTS
    the M2-5 gate (#13): run the brief by hand for five working days, log every
    edit, pass when the count trends down. It was set, and then seven loops
    were built without it ever running - so the format churn it exists to catch
    for free is now rendered into several templates instead of one.

    SPEC §8 adds the rule that actually changes the product: *anything he asks
    twice gets folded into the skill*. That makes a repeat more informative than
    the count, because a falling count with an outstanding repeat means a known,
    named defect is still in the brief and the arithmetic is flattering it.

KNOWN LIMIT
    `report.trending` over five points is a direction, not a statistic. It
    compares the first two days against the last two and ignores the middle,
    which is robust to one noisy morning and nothing more. With n=5 no honest
    test does better; the repeats are the part to read.

    A repeat is matched on normalised TEXT - casefolded, depunctuated,
    whitespace-collapsed. It catches the same words twice, not the same idea in
    different words. The second is his call, and `folded()` is how he records
    having made it.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from daydag.eventlog import EventLog

__all__ = ["REQUIRED_DAYS", "Report", "Soak", "main"]

#: Working days the gate requires. Five is the number in M2-5, and it is not a
#: rounding target - a quiet Tuesday and Wednesday can hide format churn that a
#: busy Thursday surfaces immediately.
REQUIRED_DAYS = 5

#: Event kinds. Separate rather than one kind with a field, so `recorded()`
#: reads each back without filtering.
SHIPPED = "soak_shipped"
EDIT = "soak_edit"
FOLDED = "soak_folded"

_PUNCTUATION = re.compile(r"[^\w\s]+")
_SPACES = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """An edit reduced to what makes two of them the same request.

    He is typing these into a terminal on five different mornings; matching the
    literal string would miss almost every real repeat.
    """
    return _SPACES.sub(" ", _PUNCTUATION.sub(" ", str(text).casefold())).strip()


@dataclass(frozen=True)
class Report:
    """What the gate knows, and whether that is enough to open it."""

    days_shipped: int
    per_day: Mapping[date, int]
    repeats: tuple[str, ...]
    trending: bool
    passed: bool
    why: str

    def render(self) -> str:
        """The report as the terminal shows it."""
        lines = [f"soak: {self.days_shipped}/{REQUIRED_DAYS} days - {self.why}"]
        for day in sorted(self.per_day):
            count = self.per_day[day]
            lines.append(f"  {day}  {count} edit{'' if count == 1 else 's'}")
        for repeat in self.repeats:
            lines.append(f"  ⚠ asked twice, fold it into the skill: {repeat}")
        return "\n".join(lines)


class Soak:
    """The journal behind M2-5, kept in the event log outside the vault."""

    def __init__(self, events: EventLog) -> None:
        self._events = events

    # -- writing -----------------------------------------------------------

    def shipped(self, day: date) -> None:
        """Record that a brief went out on ``day``.

        Idempotent by day: re-running a brief after a fix is normal, and must
        not inflate the gate into passing a day early.
        """
        self._events.record(SHIPPED, day=day.isoformat())

    def note(self, edit: str, *, day: date) -> None:
        """Record one edit he asked for.

        ``sensitivity="private"`` unconditionally. An edit quotes the brief it
        is about - *"cut the 1:1 with the candidate"* names a candidate - and
        guardrail 3 fails open when the caller has to decide (#105). Here the
        caller cannot: nothing written by this module is ever visible to the
        vault projection.
        """
        self._events.record(EDIT, sensitivity="private", day=day.isoformat(), edit=str(edit))

    def folded(self, edit: str) -> None:
        """Record that a repeated edit is now in the skill (SPEC §8)."""
        self._events.record(FOLDED, sensitivity="private", edit=str(edit))

    # -- reading -----------------------------------------------------------

    def _days(self) -> set[date]:
        return {d for row in self._events.recorded(SHIPPED) if (d := _as_day(row)) is not None}

    def _edits(self) -> list[tuple[date, str]]:
        found: list[tuple[date, str]] = []
        for row in self._events.recorded(EDIT):
            day, text = _as_day(row), _as_text(row, "edit")
            if day is not None and text:
                found.append((day, text))
        return found

    def report(self) -> Report:
        """Whether the gate is open, and the one number that says why not."""
        edits = self._edits()
        # A day with edits shipped, whether or not `shipped()` was called for
        # it - so a morning logged in a hurry still counts.
        days = self._days() | {day for day, _ in edits}
        per_day: dict[date, int] = dict.fromkeys(days, 0)
        for day, _ in edits:
            per_day[day] += 1

        folded = {_normalise(_as_text(row, "edit")) for row in self._events.recorded(FOLDED)}
        repeats = _repeats(edits, folded)
        trending = _trending_down(per_day)

        passed = len(days) >= REQUIRED_DAYS and trending and not repeats
        return Report(
            days_shipped=len(days),
            per_day=per_day,
            repeats=repeats,
            trending=trending,
            passed=passed,
            why=_why(len(days), trending, repeats),
        )


def _repeats(edits: list[tuple[date, str]], folded: set[str]) -> tuple[str, ...]:
    """Edits asked on two different days and not yet folded into the skill.

    Keyed on the normalised text but REPORTED as he first wrote it, because the
    normalised form is for matching and reads like neither of the two things he
    actually typed.
    """
    seen: dict[str, set[date]] = {}
    first_wording: dict[str, str] = {}
    for day, text in edits:
        key = _normalise(text)
        if not key:
            continue
        seen.setdefault(key, set()).add(day)
        first_wording.setdefault(key, text)
    return tuple(
        first_wording[key]
        for key, days in seen.items()
        if len(days) > 1 and key not in folded  # two DIFFERENT days, contract 3
    )


def _trending_down(per_day: Mapping[date, int]) -> bool:
    """Whether the edit count is falling.

    First two days against the last two, middle ignored - see KNOWN LIMIT.

    Fewer than four days cannot show a trend, and says so rather than
    defaulting to True: the gate's failure mode to avoid is the flattering one.
    """
    days = sorted(per_day)
    if len(days) < 4:
        return False
    opening = sum(per_day[d] for d in days[:2])
    closing = sum(per_day[d] for d in days[-2:])
    return closing < opening


def _why(days: int, trending: bool, repeats: tuple[str, ...]) -> str:
    """One line naming what is still missing - never a bare "not yet"."""
    if days < REQUIRED_DAYS:
        return f"{REQUIRED_DAYS - days} more working day(s) to run"
    if repeats:
        plural = "" if len(repeats) == 1 else "s"
        return f"{len(repeats)} repeat{plural} to fold into the skill before this closes"
    if not trending:
        return "edits are not trending down yet"
    return "passed"


def _as_day(row: Any) -> date | None:
    raw = _as_text(row, "day")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _as_text(row: Any, key: str) -> str:
    return str(row.get(key, "")) if isinstance(row, Mapping) else ""


def main(argv: list[str] | None = None) -> int:
    """``python -m daydag.soak ...`` - the one CLI (`daydag.cli`), entered here."""

    from daydag.cli import main as cli_main

    return cli_main(["soak", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
