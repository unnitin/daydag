"""The meeting ledger: calendar drives, notes attach.

USING IT
    ledger = Ledger()
    ledger.seed_day(events)             # every qualifying event gets a row
    ledger.offer_note(note)             # -> Match; attaches to a row
    ledger.notes_gaps()                 # meetings that produced NO notes
    ledger.ambiguous()                  # note matched >1 row, needs a human
    ledger.close_day()

    title_from_gemini_subject(subject)  # 'Notes: "<title>" <date>' -> title

CONTRACTS
    1. Calendar is the DRIVER. Seed rows first; a note is only ever attached to
       a row that already exists. Ingestion driven by arriving notes cannot
       notice the note that never came, which is the whole point.
    2. Qualification disqualifies on `response_status == "declined"` only. It
       does NOT require `accepted` - about 70% of real invites are never
       answered, and requiring it dropped 61% of real meetings silently.
    3. An ambiguous match is SURFACED, never guessed. Back-to-back 1:1s with
       the same person are the case that produces one.

WHY IT EXISTS
    The gap this closes is an absence, not a presence. Gemini mail puts the
    title in a subject that has to be parsed, Notion's meeting-notes database
    lags about a week, and Granola holds only what was recorded. Nothing
    anywhere says "this meeting happened and produced no notes".

    The guarantee is not that every meeting has notes. It is that a missing one
    is visible the next morning instead of a month later.
"""

from __future__ import annotations

import difflib
import re
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

#: Gemini's subject line, which the issue #2 audit found to be rigidly
#: structured: `Notes: "<meeting title>" <date>`. SPEC section 4 claimed the
#: opposite - that the title lived in the body and the subject was inconsistent -
#: and every one of 201 notes over 30 days contradicts it. The quotes are the
#: typographic pair, not ASCII.
_GEMINI_SUBJECT = re.compile(
    r'^Notes:\s*(?:\u201c(?P<curly>[^\u201d]+)\u201d|"(?P<straight>[^"]+)")(?:\s|$)'
)


def title_from_gemini_subject(subject: str) -> str | None:
    """The exact meeting title from a Gemini subject, or None if it is not one.

    Returning None rather than a best guess matters: an unparseable subject
    falls back to fuzzy matching, which knows how to surface an ambiguity.
    A guess here would look like certainty.
    """
    match = _GEMINI_SUBJECT.match(subject or "")
    if not match:
        return None
    return match.group("curly") or match.group("straight")


#: Below this, a note is not confidently placed. Ties surface rather than guess.
MATCH_THRESHOLD = 0.6

#: Calendar entries that are not meetings anyone takes notes at.
NON_MEETING_KINDS = frozenset({"ooo", "focus", "hold"})

#: The only response that means the meeting did not happen for him.
#:
#: Measured over 5 real days (issue #2 connector audit): of 77 calendar events,
#: 23 were "accepted" but 60 were genuine meetings - most invites are simply
#: never answered. Requiring "accepted" dropped 61% of them, including standups
#: with 13 and 21 attendees, and dropped them silently: a meeting with no row
#: can never be surfaced as a notes gap, so the absence is invisible by design.
DISQUALIFYING_RESPONSES = frozenset({"declined"})


@dataclass(frozen=True)
class Match:
    """A note offered to the ledger, from Gemini, Notion or Granola."""

    title: str | None
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
    """Whether a calendar entry is a meeting the ledger should track.

    Deliberately inclusive. A false positive costs one line in a brief that
    says "no notes"; a false negative is a meeting the system cannot see at all.
    """
    response = event.get("response_status", "needsAction")
    return (
        response not in DISQUALIFYING_RESPONSES
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
        if not self._in_window(row, note):
            return 0.0
        title = difflib.SequenceMatcher(
            None, row.summary.casefold(), (note.title or "").casefold()
        ).ratio()
        overlap = set(row.attendees) & set(note.attendees)
        shared = len(overlap) / max(len(row.attendees), 1)
        return (title * 0.7) + (shared * 0.3)

    def _exact_title_match(self, note: Match) -> Row | None:
        """The one open row whose summary equals the note title, if exactly one.

        Zero or several means the title cannot settle it, and the fuzzy path -
        which knows how to surface an ambiguity - takes over.
        """
        title = (note.title or "").strip().casefold()
        if not title:
            return None
        hits = [
            row
            for row in self._rows.values()
            if row.note is None
            and row.summary.strip().casefold() == title
            and self._in_window(row, note)
        ]
        return hits[0] if len(hits) == 1 else None

    def _in_window(self, row: Row, note: Match) -> bool:
        window = ARRIVAL_WINDOW.get(note.source, DEFAULT_ARRIVAL_WINDOW)
        return row.end <= note.arrived <= row.end + window

    def offer_note(self, note: Match) -> Row | None:
        """Attach a note to the row it belongs to, or surface that it is unclear.

        Two near-identical back-to-back 1:1s is the case that breaks naive
        matching, and it is common. When the best two candidates are
        indistinguishable the note attaches to neither - invariant 5 says
        surface, do not resolve.
        """
        exact = self._exact_title_match(note)
        if exact is not None:
            return self._attach(exact, note)

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

        return self._attach(best, note)

    def _attach(self, row: Row, note: Match) -> Row:
        row.note = note
        if row.day_closed:
            # A late arrival is new information about a meeting already reported
            # as a gap, so ingestion runs again for it.
            self._reingest.append(row.event_id)
        return row

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
