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
    4. A calendar event may DECLARE its note (`notes_attached`), and that beats
       every heuristic here: it is the source stating the fact rather than this
       module inferring it from a title and a time window. Matching still runs,
       because the gap list is not the only consumer - ingestion wants the note
       itself - but a declared row is never reported as missing.

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
#: Measured across 49 real Gemini notes: delivery is tight (median 5 min, p90
#: 19, max 94 from generation to inbox) but GENERATION runs late, and a Sep 10
#: 11:00-12:00 meeting was generated at 00:52 the next morning - 12.9 hours out.
#: Six hours dropped it and the meeting was a gap forever.
#:
#: This is now the FALLBACK path. When the calendar declares the note outright
#: (`notes_attached`, see `Row.notes_declared`) no window is consulted at all.
#:
#: Bounded BELOW 24 hours on purpose, and that is the real constraint rather
#: than a guess: a daily standup has rows 24 hours apart, so a wider window
#: lets one note match two rows - and an ambiguous note attaches to neither,
#: which trades a late gap for a lost note.
ARRIVAL_WINDOW = {
    "gemini": timedelta(hours=18),
    "granola": timedelta(hours=6),
    "notion": timedelta(days=10),
}
DEFAULT_ARRIVAL_WINDOW = timedelta(hours=6)

#: How far BEFORE a meeting's scheduled end its notes may still arrive. A
#: meeting that runs short ends when it ends, and Gemini sends notes then.
#:
#: 45, not 30: a real note arrived 37 minutes before its scheduled end
#: (Discovery Content Discussions, scheduled 10:15-11:00, note at 10:08), so
#: 30 dropped it. Still far short of a meeting's own length, which is what
#: keeps it from reaching back into whatever ran before.
ENDS_EARLY = timedelta(minutes=45)

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
    #: Display names, aligned with `attendees`, "" where google gave none. Kept
    #: APART from the addresses on purpose: every consumer of `attendees` -
    #: `Audience.has_external`, `has_leadership`, `_score`, the prep selector's
    #: principal skip - compares bare emails, and a display name folded into
    #: that string broke all four at once (see `attendee_parts`).
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
    name is ``""`` when there is none. THIS is why the split lives at the seam:
    a first attempt carried the name inside the attendee string, and every
    consumer that partitions on ``@`` or compares whole strings - `has_external`
    read the domain as ``example.com>`` and called every colleague external,
    `has_leadership` never matched, note-to-meeting attendee overlap went to
    zero, the principal skip died - broke together. Parse once, compare bare.
    """
    if isinstance(attendee, Mapping):
        return str(attendee.get("email", "")).strip(), str(attendee.get("displayName", "")).strip()
    name, addr = parseaddr(str(attendee))
    if not addr:
        # parseaddr gives ("", "") for a string with no address in it - keep the
        # raw text as the address so a name-only attendee is not silently lost.
        return str(attendee).strip(), ""
    return addr.strip(), name.strip()


def _is_resource(attendee: Any) -> bool:
    """Whether an attendee is a room or other bookable thing, not a person."""
    if isinstance(attendee, Mapping):
        if attendee.get("resource"):
            return True
        attendee = attendee.get("email", "")
    return _RESOURCE_DOMAIN in str(attendee).casefold()


#: Google attaches the Gemini notes doc to the calendar event. Measured as
#: per-INSTANCE: the "1:1 | 2x weekly" series carries one on the Sep 1
#: instance, which produced a note, and none on the Sep 10 one, which did not.
#:
#: Identified by this URL marker, which Meet's notetaker puts on the docs it
#: creates, rather than by the attachment's TITLE. Two reasons, both measured:
#:
#: * The title is LOCALIZED. A real event carries both "Notes by Gemini" and
#:   "Anotacoes do Gemini" - and a meeting run in a pt-BR locale would carry
#:   only the second. Matching English would report it as a gap forever, and
#:   this org has a large Brazilian contingent on exactly these invites.
#: * "Has an attachment" is too loose in the other direction: a Drive RECORDING
#:   is attached to the series master and shows on every instance, so
#:   "Data Health Check" carries a 2024 recording on every 2026 occurrence.
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
    Google's raw `attachments` - because the realistic failure here is an agent
    passing the connector payload through unshaped, and silently losing the
    signal is exactly the docs-ahead-of-code gap this repo keeps finding.
    """
    if "notes_attached" in event:
        return bool(event["notes_attached"])
    return any(
        _NOTES_DOC_MARKER in str(item.get("fileUrl", ""))
        or _NOTES_ATTACHMENT_TITLE in str(item.get("title", "")).casefold()
        for item in (event.get("attachments") or [])
        if isinstance(item, Mapping)
    )


def qualifies(event: Mapping[str, Any]) -> bool:
    """Whether a calendar entry is a meeting worth tracking or reporting.

    PUBLIC because more than one loop has to agree on it. The week-ahead built
    its own idea of "a meeting" - one check, `kind not in NON_MEETING_KINDS` -
    against the four here, and so rendered a meeting he had DECLINED and a
    personal errand with no attendees as part of his week, while
    `monday_prep_queue`, reading the same events through the ledger, dropped
    both. Two qualification paths in one module, disagreeing silently.

    Deliberately inclusive. A false positive costs one line in a brief that
    says "no notes"; a false negative is a meeting the system cannot see at all.
    """
    response = event.get("response_status", "needsAction")
    if response in DISQUALIFYING_RESPONSES:
        return False
    if event.get("kind", "meeting") in NON_MEETING_KINDS:
        return False

    people = [a for a in (event.get("attendees") or []) if not _is_resource(a)]
    if len(people) >= 2:
        return True

    # Somebody ELSE put this in his day. An ATS interview invite lists only
    # the principal - the candidate is invited through a separate calendar -
    # so the attendee count made a 45-minute interview invisible, and three
    # landed on one real Friday (#90). An organizer who is not him is the
    # evidence that distinguishes it from a hold he made for himself.
    if event.get("organizer_is_self"):
        # Every entry has an organizer, his own holds included. Google marks
        # the self case and the shaper passes it through; without it a focus
        # block would read as somebody else's meeting.
        return False
    organizer = str(event.get("organizer") or "").strip().casefold()
    principal = str(event.get("principal") or "").strip().casefold()
    return bool(organizer) and organizer != principal


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
            parts = [attendee_parts(a) for a in (event.get("attendees") or [])]
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

        The lower bound is the SCHEDULED end minus `ENDS_EARLY`, not the
        scheduled end itself. Meetings finish early and Gemini sends notes when
        the meeting actually ends, so a strict `row.end <= arrived` dropped
        notes that arrived first: a real Friday had "Discovery Content
        Discussions" scheduled 10:15-11:00 with its note at 10:48, and the
        brief reported it as a meeting with no notes while listing its note in
        the section directly above.

        Bounded rather than open: a note arriving long before a meeting ends
        belongs to something else. `ENDS_EARLY` is 45 minutes, measured rather
        than chosen - a real note landed 37 minutes before its meeting's
        scheduled end, so half an hour was not enough.

        The upper bound is per-source and much wider, because the lag is not
        delivery. Measured across ~100 real notes: generation-to-inbox runs 2
        to 94 minutes, while meeting-end-to-generation has a long tail - one
        Sep 10 meeting was written up at 00:52 the next morning, 12.9 hours
        after it ended, and delivered four minutes later. See `ARRIVAL_WINDOW`,
        which also carries why the window stays under 24 hours.
        """
        window = ARRIVAL_WINDOW.get(note.source, DEFAULT_ARRIVAL_WINDOW)
        return row.end - ENDS_EARLY <= note.arrived <= row.end + window

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

        A row whose calendar entry DECLARES a note is never a gap, even with
        nothing ingested yet. Google attaches the notes doc to the event, so
        the source states the fact the arrival window was reconstructing by
        guesswork - and states it as soon as the meeting ends rather than
        whenever the mail happens to land.
        """
        return [
            row.summary for row in self.open_rows() if row.end < as_of and not row.notes_declared
        ]


def part_of_the_week(event: Mapping[str, Any]) -> bool:
    """Whether an entry belongs on the page describing his week.

    Close to `qualifies`, and deliberately NOT the same question. `qualifies`
    asks "should the ledger track this for notes", and answers no for a record
    missing the fields it reads. Here the question is "is this his week", where
    dropping an unreadable record loses a real meeting from the page - the
    under-reporting failure he has no way to notice, as against a stray line he
    skims past.

    So this drops only what it can POSITIVELY read as not his week:

      * a response he declined
      * a non-meeting kind - OOO, a focus block, a hold
      * a solo entry he organised himself, which means `attendees` is PRESENT
        and holds fewer than two people. Present-and-empty is a personal
        errand; ABSENT is a record we cannot judge, and that one is kept.
    """
    if event.get("response_status", "needsAction") in DISQUALIFYING_RESPONSES:
        return False
    if event.get("kind", "meeting") in NON_MEETING_KINDS:
        return False
    attendees = event.get("attendees")
    if attendees is not None and event.get("organizer_is_self"):
        people = [a for a in attendees if not _is_resource(a)]
        if len(people) < 2:
            return False
    return True


#: The old private name. `tests/test_ledger.py` and any caller written before
#: the week-ahead needed this too still import it.
_qualifies = qualifies
