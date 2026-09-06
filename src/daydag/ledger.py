"""The meeting ledger: calendar drives, notes attach.

The gap this closes is an absence, not a presence. Gemini mail puts the meeting
title in the body rather than a stable subject, Notion's meeting-notes database
lags about a week, and Granola only holds what was recorded. Nothing anywhere
says *this meeting happened and produced no notes* - so ingestion that only
reacts to arriving notes cannot notice one that never came.

Calendar is therefore the driver: every qualifying event gets a row at the start
of the day, and notes attach to rows rather than being discovered on their own.
The guarantee is not that every meeting has notes. It is that a missing one is
visible the next morning instead of a month later.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

#: How long after a meeting ends a note may still plausibly belong to it.
#: Source-dependent by necessity: Gemini mails within hours of the call, while
#: the Notion database lags about a week. One constant would either reject every
#: real Notion note or accept a Gemini note from two meetings later.
ARRIVAL_WINDOW = {
    "gemini": timedelta(hours=6),
    "granola": timedelta(hours=6),
    "notion": timedelta(days=10),
}
DEFAULT_ARRIVAL_WINDOW = timedelta(hours=6)

#: Below this, a note is not confidently placed. Ties surface rather than guess.
MATCH_THRESHOLD = 0.6

#: Calendar entries that are not meetings anyone takes notes at.
NON_MEETING_KINDS = frozenset({"ooo", "focus", "hold"})


@dataclass(frozen=True)
class Match:
    """A note offered to the ledger, from Gemini, Notion or Granola."""

    title: str
    arrived: datetime
    attendees: list[str]
    source: str


@dataclass
class Row:
    """One meeting instance, and whatever note has been attached to it."""

    event_id: str
    start: datetime
    end: datetime
    summary: str
    attendees: list[str]
    note: Match | None = None
    day_closed: bool = False

    @property
    def key(self) -> tuple[str, datetime]:
        # Keyed on the instance, not the series: a weekly 1:1 is a new row each
        # week, not one row that never closes.
        return (self.event_id, self.start)


def _qualifies(event: dict[str, Any]) -> bool:
    return (
        event.get("accepted", False)
        and event.get("kind", "meeting") not in NON_MEETING_KINDS
        and len(event.get("attendees") or []) >= 2
    )


class Ledger:
    """Rows for the meetings that happened, and the notes attached to them."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, datetime], Row] = {}
        self._ambiguous: list[Match] = []
        self._reingest: list[str] = []

    # -- calendar drives -------------------------------------------------

    def seed_day(self, events: Iterable[dict[str, Any]]) -> None:
        """Create a row per qualifying event. Idempotent per instance."""
        for event in events:
            if not _qualifies(event):
                continue
            row = Row(
                event_id=event["id"],
                start=event["start"],
                end=event["end"],
                summary=event["summary"],
                attendees=list(event.get("attendees") or []),
            )
            self._rows.setdefault(row.key, row)

    def close_day(self) -> None:
        """Mark open rows as belonging to a finished day.

        Rows are *not* closed for matching - Notion lands about a week late, so
        an unmatched row keeps being re-checked. This only records that the day
        has turned, which is what makes a late arrival a backfill.
        """
        for row in self._rows.values():
            if row.note is None:
                row.day_closed = True

    # -- notes attach ----------------------------------------------------

    def _score(self, row: Row, note: Match) -> float:
        window = ARRIVAL_WINDOW.get(note.source, DEFAULT_ARRIVAL_WINDOW)
        if not (row.end <= note.arrived <= row.end + window):
            return 0.0
        title = difflib.SequenceMatcher(None, row.summary.casefold(), note.title.casefold()).ratio()
        overlap = set(row.attendees) & set(note.attendees)
        shared = len(overlap) / max(len(row.attendees), 1)
        return (title * 0.7) + (shared * 0.3)

    def offer_note(self, note: Match) -> Row | None:
        """Attach a note to the row it belongs to, or surface that it is unclear.

        Two near-identical back-to-back 1:1s is the case that breaks naive
        matching, and it is common. When the best two candidates are
        indistinguishable the note attaches to neither - invariant 5 says
        surface, do not resolve.
        """
        scored = sorted(
            ((self._score(row, note), row) for row in self._rows.values() if row.note is None),
            key=lambda pair: pair[0],
            reverse=True,
        )
        if not scored or scored[0][0] < MATCH_THRESHOLD:
            return None

        best_score, best = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if abs(best_score - runner_up) < 0.05:
            self._ambiguous.append(note)
            return None

        best.note = note
        if best.day_closed:
            # A late arrival is new information about a meeting already reported
            # as a gap, so ingestion runs again for it.
            self._reingest.append(best.event_id)
        return best

    # -- what the brief asks for -----------------------------------------

    def open_rows(self) -> list[Row]:
        """Rows with no note attached, oldest first."""
        return sorted(
            (row for row in self._rows.values() if row.note is None),
            key=lambda row: row.start,
        )

    def ambiguous(self) -> list[Match]:
        """Notes that could not be confidently placed, for a human to settle."""
        return list(self._ambiguous)

    def reingest_queue(self) -> list[str]:
        """Event ids whose notes arrived late and need ingestion re-run."""
        return list(self._reingest)

    def notes_gaps(self, as_of: datetime) -> list[str]:
        """Meetings finished before ``as_of`` that still have no notes.

        This is the actual deliverable: the next morning's "3 meetings w/ no
        notes" line, which is how a missing note becomes visible at all.
        """
        return [row.summary for row in self.open_rows() if row.end < as_of]
