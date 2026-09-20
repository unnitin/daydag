"""The meeting ledger: calendar drives, notes attach.

USING IT
    ledger = Ledger()
    ledger.seed_day(events)             # every qualifying event gets a row
    ledger.offer_note(note)             # -> Match; attaches to a row
    ledger.notes_gaps(as_of=now)        # meetings that produced NO notes
    ledger.ambiguous()                  # note matched >1 row, needs a human
    ledger.close_day()

    judge(event)                        # MEETING | NOT_MEETING | UNKNOWN, read two ways
    title_from_gemini_subject(subject)  # 'Notes: "<title>" <date>' -> title

CONTRACTS
    1. Calendar is the DRIVER. Seed rows first; a note is only ever attached to
       a row that already exists. Ingestion driven by arriving notes cannot
       notice the note that never came, which is the whole point.
    2. Qualification disqualifies on `response_status == "declined"` only, never
       on a missing `accepted` - see `DISQUALIFYING_RESPONSES` for what
       requiring one cost.
    3. An ambiguous match is SURFACED, never guessed. Back-to-back 1:1s with
       the same person are the case that produces one.
    4. A calendar event may DECLARE its note (`notes_attached`), and that beats
       every heuristic here: the source stating the fact, not this module
       inferring it from a title and a time window. Matching still runs, because
       ingestion wants the note itself, but a declared row is never a gap.

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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parseaddr
from typing import Any

#: How long after a meeting ends its notes may still arrive, by the system that
#: WROTE them. Source-dependent by necessity: one constant would either reject
#: every real Notion note or accept a Gemini note from two meetings later.
#:
#: Measured across ~100 real Gemini notes: delivery is tight (median 5 min, p90
#: 19, max 94 from generation to inbox) but GENERATION runs late - a Sep 10
#: 11:00-12:00 meeting was written up at 00:52 the next morning, 12.9 hours out,
#: and six hours dropped it forever. So the window is the generation lag, not
#: the delivery lag (`test_a_note_generated_hours_late_still_attaches`).
#:
#: This is now the FALLBACK path. When the calendar declares the note outright
#: (`notes_attached`, see `Row.notes_declared`) no window is consulted at all.
#:
#: Bounded BELOW 24 hours on purpose: a daily standup has rows 24 hours apart,
#: so a wider window lets one note match two rows - and an ambiguous note
#: attaches to neither, trading a late gap for a lost note
#: (`test_the_window_stays_short_enough_that_a_daily_standup_is_unambiguous`).
ARRIVAL_WINDOW = {
    "gemini": timedelta(hours=18),
    "granola": timedelta(hours=6),
    "notion": timedelta(days=10),
}
DEFAULT_ARRIVAL_WINDOW = timedelta(hours=6)

#: How far BEFORE a meeting's scheduled end its notes may still arrive. A
#: meeting that runs short ends when it ends, and Gemini sends notes then.
#:
#: 45, not 30: a real note arrived 37 minutes before its scheduled end, so 30
#: dropped it and the meeting was reported as a gap while its own note was
#: listed directly above. Still far short of a meeting's own length, which is
#: what keeps it from reaching back into whatever ran before.
ENDS_EARLY = timedelta(minutes=45)

#: Gemini's subject line, which the issue #2 audit found rigidly structured:
#: `Notes: "<meeting title>" <date>`. SPEC section 4 claimed the opposite - the
#: title in the body, the subject inconsistent - and all 201 notes over a 30-day
#: window contradict it. The quotes are the typographic pair, not ASCII.
_GEMINI_SUBJECT = re.compile(
    r'^Notes:\s*(?:\u201c(?P<curly>[^\u201d]+)\u201d|"(?P<straight>[^"]+)")(?:\s|$)'
)


def title_from_gemini_subject(subject: str) -> str | None:
    """The exact meeting title from a Gemini subject, or None if it is not one.

    None rather than a best guess: an unparseable subject falls back to fuzzy
    matching, which knows how to surface an ambiguity. A guess looks certain.
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
#: never answered. Requiring "accepted" dropped 61% of them, and dropped them
#: SILENTLY: a meeting with no row can never surface as a notes gap.
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
    #: Display names, aligned with `attendees`, "" where google gave none. Kept
    #: APART from the addresses: every consumer of `attendees` compares bare
    #: emails, and a display name folded into that string breaks all of them at
    #: once - see `attendee_parts`.
    attendee_names: list[str] = field(default_factory=list)
    #: The CALENDAR said a note artifact exists for this instance - Google
    #: attaches the "Notes by Gemini" doc to the event itself. Independent of
    #: `note`, which is set only when a note has actually been ingested: a
    #: declared note may not have been mailed yet, or ever.
    notes_declared: bool = False

    @property
    def key(self) -> tuple[str, datetime]:
        # Keyed on the instance, not the series: a weekly 1:1 is a new row each
        # week, not one row that never closes.
        return (self.event_id, self.start)


#: Google lists conference rooms as attendees, on this domain. A room is not a
#: participant: it cannot take a note, so counting it made a solo block with a
#: room booked look like a two-person meeting and chased it forever (#90).
_RESOURCE_DOMAIN = "resource.calendar.google.com"


def attendee_parts(attendee: Any) -> tuple[str, str]:
    """``(address, display_name)`` from however the connector shaped one attendee.

    Google's native ``{"email": ..., "displayName": ...}``, RFC-style
    ``"Full Name <addr>"``, or a bare address all come out the same way; the
    name is ``""`` when there is none.

    The split lives at this seam because a first attempt carried the name inside
    the attendee string, and every consumer that partitions on ``@`` or compares
    whole strings broke together: `prep.Audience.has_external` read the domain as
    ``example.com>`` and called every colleague external, `has_leadership` never
    matched, attendee overlap between a note and a meeting went to zero, and
    prep's principal skip died. Parse once, compare bare.
    """
    if isinstance(attendee, Mapping):
        return str(attendee.get("email", "")).strip(), str(attendee.get("displayName", "")).strip()
    name, addr = parseaddr(str(attendee))
    if not addr:
        # parseaddr gives ("", "") for a string with no address in it - keep the
        # raw text as the address so a name-only attendee is not silently lost.
        return str(attendee).strip(), ""
    return addr.strip(), name.strip()


def is_resource(attendee: Any) -> bool:
    """Whether an attendee is a room or other bookable thing, not a person."""
    if isinstance(attendee, Mapping):
        if attendee.get("resource"):
            return True
        attendee = attendee.get("email", "")
    return _RESOURCE_DOMAIN in str(attendee).casefold()


#: Google attaches the Gemini notes doc to the calendar event. Measured as
#: per-INSTANCE: one series carries a doc on the instance that produced a note
#: and none on the instance that did not.
#:
#: Identified by this URL marker, which Meet's notetaker puts on the docs it
#: creates, rather than by the attachment's TITLE. Two reasons, both measured:
#:
#: * The title is LOCALIZED - a real event carries "Notes by Gemini" and
#:   "Anotacoes do Gemini" both. Matching English would report a pt-BR meeting
#:   as a gap forever, and this org has a large Brazilian contingent on exactly
#:   these invites.
#: * "Has an attachment" is too loose the other way: a Drive RECORDING hangs off
#:   the series master and shows on every instance, so one 2024 recording would
#:   declare a note on every 2026 occurrence.
#:
#: A recording's url carries `usp=drive_web` instead, so the marker separates
#: the two without reading a word of any language.
_NOTES_DOC_MARKER = "usp=meet_tnfm_calendar"

#: The English title, kept only as a fallback for a payload that carried the
#: attachment titles but dropped the urls. Never the primary test - see above.
_NOTES_ATTACHMENT_TITLE = "notes by gemini"


def _declares_note(event: Mapping[str, Any]) -> bool:
    """Whether the calendar entry itself says a note artifact exists.

    Takes the shaped boolean when the caller supplied one, and otherwise reads
    Google's raw `attachments`: the realistic failure is an agent passing the
    connector payload through unshaped, and losing the signal there is silent.
    """
    if "notes_attached" in event:
        return bool(event["notes_attached"])
    return any(
        _NOTES_DOC_MARKER in str(item.get("fileUrl", ""))
        or _NOTES_ATTACHMENT_TITLE in str(item.get("title", "")).casefold()
        for item in (event.get("attachments") or [])
        if isinstance(item, Mapping)
    )


MEETING, NOT_MEETING, UNKNOWN = "meeting", "not a meeting", "unknown"


def judge(event: Mapping[str, Any]) -> str:
    """One verdict on a calendar entry: MEETING, NOT_MEETING or UNKNOWN (#110).

    `qualifies` and `part_of_the_week` were two predicates over the same rules
    and they drifted, so both are now one-line readings of this one. The ledger
    tracks what is positively a meeting; the week page drops only what is
    positively not - an UNKNOWN record is kept on the page (losing a real
    meeting is the failure he cannot notice) and left out of the ledger
    (nothing to track).

    * declined, or a non-meeting kind (OOO, focus, hold) - NOT_MEETING
    * two or more people once rooms are filtered - MEETING
    * a solo entry he organised himself, attendees PRESENT - NOT_MEETING
      (a personal errand); attendees ABSENT - UNKNOWN, we cannot judge it
    * somebody else put it in his day - MEETING. An ATS interview invite
      lists only him; the organizer is the evidence (#90)
    * anything else - UNKNOWN
    """
    if _positively_not_his_meeting(event):
        return NOT_MEETING
    attendees = event.get("attendees")
    people = None if attendees is None else [a for a in attendees if not is_resource(a)]
    if people is not None and len(people) >= 2:
        return MEETING
    if event.get("organizer_is_self"):
        return NOT_MEETING if people is not None else UNKNOWN
    organizer = str(event.get("organizer") or "").strip().casefold()
    principal = str(event.get("principal") or "").strip().casefold()
    return MEETING if organizer and organizer != principal else UNKNOWN


def qualifies(event: Mapping[str, Any]) -> bool:
    """Whether the ledger tracks this entry for notes: positively a meeting."""
    return judge(event) is MEETING


def part_of_the_week(event: Mapping[str, Any]) -> bool:
    """Whether the entry belongs on the page describing his week: not
    positively NOT a meeting. An unreadable record is kept here and dropped
    by the ledger, on purpose - see `judge`."""
    return judge(event) is not NOT_MEETING


def _positively_not_his_meeting(event: Mapping[str, Any]) -> bool:
    """The two rules both readings share, default literals included."""
    return (
        event.get("response_status", "needsAction") in DISQUALIFYING_RESPONSES
        or event.get("kind", "meeting") in NON_MEETING_KINDS
    )


_TOKENS = re.compile(r"[^a-z0-9]+")


def tokens(text: Any) -> set[str]:
    """The words in a string, however it was punctuated - casefolded."""
    return {part for part in _TOKENS.split(str(text).casefold()) if part}


def name_tokens(address: str, name: str = "") -> set[str]:
    """The name tokens for one person: the address's local part plus the
    display name, domain discarded - every colleague shares the domain, so a
    selector or a directory lookup that hit it would match everybody.

    One rule for `prep` and `people`, which each had their own copy."""
    return tokens(str(address).split("@", 1)[0]) | tokens(name)


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
            if not qualifies(event):
                continue
            # The one place an attendee is parsed. Everything downstream reads
            # `attendees` as bare addresses and `attendee_names` beside them.
            # Rooms are filtered HERE, not only in the qualifying count: stored,
            # a room reads as an outside party to `has_external` and as the
            # second person of a "1:1".
            parts = [
                attendee_parts(a) for a in (event.get("attendees") or []) if not is_resource(a)
            ]
            parts = [(addr, name) for addr, name in parts if addr]
            row = Row(
                event_id=event["id"],
                start=event["start"],
                end=event["end"],
                summary=event["summary"],
                attendees=[addr for addr, _ in parts],
                attendee_names=[name for _, name in parts],
                notes_declared=_declares_note(event),
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
        """Whether ``note`` arrived close enough to ``row`` ending to be its own.

        Both bounds are wider than "between the end and the end plus a lag"
        looks. The lower one is the SCHEDULED end minus `ENDS_EARLY`, because
        meetings finish early and Gemini sends notes when the meeting actually
        ends; the upper one is per-source and much wider, because what runs late
        is generation, not delivery. Each constant carries its measurement.
        """
        window = ARRIVAL_WINDOW.get(note.source, DEFAULT_ARRIVAL_WINDOW)
        return row.end - ENDS_EARLY <= note.arrived <= row.end + window

    def offer_note(self, note: Match) -> Row | None:
        """Attach a note to the row it belongs to, or surface that it is unclear.

        When the best two candidates are indistinguishable - two back-to-back
        1:1s with the same person, which is common - the note attaches to
        neither. Invariant 5 says surface, do not resolve.
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

        A row whose calendar entry DECLARES a note is never a gap, even with
        nothing ingested yet: the source states the fact `ARRIVAL_WINDOW` was
        reconstructing, and states it when the meeting ends rather than whenever
        the mail lands.
        """
        return [
            row.summary for row in self.open_rows() if row.end < as_of and not row.notes_declared
        ]
