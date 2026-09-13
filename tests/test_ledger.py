"""Meeting ledger: calendar drives, notes attach (ARCHITECTURE, issue #35).

The point is not that every meeting has notes. It is that a missing one is
visible the next morning instead of discovered a month later.
"""

from datetime import datetime, timedelta, timezone

import pytest

from daydag.ledger import Ledger, Match, qualifies, title_from_gemini_subject

_PT = timezone(timedelta(hours=-7))

T = datetime(2026, 9, 8, 14, 0)


def ev(**kw):
    base = dict(
        id="e1",
        start=T,
        end=T + timedelta(hours=1),
        summary="Discovery sync",
        attendees=["nitin", "jon"],
        response_status="needsAction",
        kind="meeting",
    )
    return {**base, **kw}


# Measured against 5 real days of the principal's calendar (issue #2 audit):
# 77 DEFAULT events, of which only 23 were "accepted" but 60 were real meetings.
# Requiring `accepted` dropped 61% of them - including standups with 13 and 21
# attendees - and dropped them *silently*, which is worse: a meeting with no row
# can never be reported as a notes gap, so the absence is invisible by design.
@pytest.mark.parametrize("status", ["accepted", "needsAction", "tentative"])
def test_unanswered_and_tentative_invites_still_qualify(status):
    """Most invites are never RSVP'd. They are still meetings that happen."""
    led = Ledger()
    led.seed_day([ev(response_status=status)])
    assert len(led.open_rows()) == 1, f"{status!r} was dropped"


def test_declined_invites_do_not_qualify():
    """Declining is the one response that means the meeting did not happen for him."""
    led = Ledger()
    led.seed_day([ev(response_status="declined")])
    assert led.open_rows() == []


def test_qualifying_events_get_a_row():
    led = Ledger()
    led.seed_day([ev()])
    assert len(led.open_rows()) == 1


@pytest.mark.parametrize(
    "skip",
    [
        dict(attendees=["nitin"]),  # solo
        dict(response_status="declined"),
        dict(kind="ooo"),
        dict(kind="focus"),
    ],
)
def test_non_qualifying_events_are_skipped(skip):
    led = Ledger()
    led.seed_day([ev(**skip)])
    assert led.open_rows() == []


def test_recurring_instances_are_distinct_rows():
    """Key on (event id, instance start) - a weekly 1:1 is not one row forever."""
    led = Ledger()
    led.seed_day([ev()])
    led.seed_day([ev(start=T + timedelta(days=7), end=T + timedelta(days=7, hours=1))])
    assert len(led.open_rows()) == 2


def test_attaches_a_note_arriving_in_the_window():
    led = Ledger()
    led.seed_day([ev()])
    led.offer_note(
        Match(
            title="Discovery sync",
            arrived=T + timedelta(hours=2),
            attendees=["nitin", "jon"],
            source="gemini",
        )
    )
    assert led.open_rows() == []


def test_note_outside_the_window_does_not_attach():
    led = Ledger()
    led.seed_day([ev()])
    led.offer_note(
        Match(
            title="Discovery sync",
            arrived=T + timedelta(hours=20),
            attendees=["nitin", "jon"],
            source="gemini",
        )
    )
    assert len(led.open_rows()) == 1


@pytest.mark.guardrail
def test_ambiguous_match_surfaces_rather_than_guessing():
    """Two near-identical back-to-back 1:1s is what breaks naive matching."""
    led = Ledger()
    led.seed_day(
        [
            ev(id="a", summary="1:1 VP-Data"),
            ev(
                id="b",
                summary="1:1 VP-Data",
                start=T + timedelta(hours=1),
                end=T + timedelta(hours=2),
            ),
        ]
    )
    led.offer_note(
        Match(
            title="1:1 VP-Data",
            arrived=T + timedelta(hours=2, minutes=30),
            attendees=["nitin", "seth"],
            source="gemini",
        )
    )
    assert led.ambiguous(), "attached a note it could not confidently place"
    assert len(led.open_rows()) == 2


# Issue #2 audit: SPEC section 4 said Gemini "uses the meeting title as body,
# not a consistent subject". The live mail says otherwise - every one of 201
# notes in 30 days carries `Notes: "<title>" <date>`. Parsing that beats fuzzy
# body matching, and removes the ambiguity that forces a surface-not-guess.
@pytest.mark.parametrize(
    "subject,expected",
    [
        ("Notes: \u201cDE Standup\u201d Sep 4, 2026", "DE Standup"),
        ("Notes: \u201c[AI Platform] Stand Ups\u201d Sep 4, 2026", "[AI Platform] Stand Ups"),
        ("Notes: \u201c1:1 w/ VP-Data\u201d Sep 4, 2026", "1:1 w/ VP-Data"),
    ],
)
def test_gemini_subject_yields_an_exact_title(subject, expected):
    assert title_from_gemini_subject(subject) == expected


@pytest.mark.parametrize("subject", ["Re: standup notes", "Notes from the call", ""])
def test_non_gemini_subjects_yield_nothing(subject):
    """A malformed subject must fall back to fuzzy matching, not guess a title."""
    assert title_from_gemini_subject(subject) is None


def test_exact_subject_title_resolves_what_fuzzy_cannot():
    """Two titles one character apart - fuzzy scores them within the tie band.

    The earlier version of this test used titles 0.14 apart, so it passed on
    fuzzy matching alone and proved nothing about the parser.
    """
    led = Ledger()
    led.seed_day(
        [
            ev(id="a", summary="Pod 10 Daily Standup"),
            ev(
                id="b",
                summary="Pod 11 Daily Standup",
                start=T + timedelta(hours=1),
                end=T + timedelta(hours=2),
            ),
        ]
    )
    note = Match(
        title=title_from_gemini_subject("Notes: \u201cPod 11 Daily Standup\u201d Sep 8, 2026"),
        arrived=T + timedelta(hours=2, minutes=30),
        attendees=["nitin", "DataEng-1"],
        source="gemini",
    )
    assert led.offer_note(note) is not None, "exact title did not attach"
    assert not led.ambiguous()
    assert [r.event_id for r in led.open_rows()] == ["a"]


def test_fuzzy_alone_would_have_been_ambiguous_here():
    """Proves the previous test is not passing on the fuzzy path by accident."""
    led = Ledger()
    led.seed_day(
        [
            ev(id="a", summary="Pod 10 Daily Standup"),
            ev(
                id="b",
                summary="Pod 11 Daily Standup",
                start=T + timedelta(hours=1),
                end=T + timedelta(hours=2),
            ),
        ]
    )
    # A title that matches neither summary exactly falls to the fuzzy path.
    led.offer_note(
        Match(
            title="Pod 1! Daily Standup",
            arrived=T + timedelta(hours=2, minutes=30),
            attendees=["nitin"],
            source="gemini",
        )
    )
    assert led.ambiguous(), "fuzzy should not have been able to settle this"


def test_unparseable_subject_falls_back_instead_of_crashing():
    """title_from_gemini_subject returns None; the sweep must survive it."""
    led = Ledger()
    led.seed_day([ev()])
    led.offer_note(
        Match(
            title=title_from_gemini_subject("Fwd: something"),
            arrived=T + timedelta(hours=2),
            attendees=["nitin", "jon"],
            source="gemini",
        )
    )
    assert len(led.open_rows()) == 1


def test_late_notion_note_backfills_and_retriggers_ingestion():
    """Notion lags ~a week, so an unmatched row is re-checked, not closed."""
    led = Ledger()
    led.seed_day([ev()])
    led.close_day()
    led.offer_note(
        Match(
            title="Discovery sync",
            arrived=T + timedelta(days=6),
            attendees=["nitin", "jon"],
            source="notion",
        )
    )
    assert led.reingest_queue() == ["e1"]


def test_unmatched_by_next_morning_becomes_a_notes_gap():
    led = Ledger()
    led.seed_day([ev(summary="Discovery sync"), ev(id="e2", summary="Deal Modeler")])
    gaps = led.notes_gaps(as_of=T + timedelta(days=1))
    assert sorted(gaps) == ["Deal Modeler", "Discovery sync"]


def test_a_note_from_a_meeting_that_ended_early_still_attaches():
    """Meetings end early, and Gemini sends notes when the meeting ACTUALLY
    ends - not when the invite said it would.

    `_in_window` required `row.end <= note.arrived`, so a note that landed
    before the scheduled end attached to nothing and its meeting was reported
    as a gap. Found on a real Friday: "Discovery Content Discussions" was
    scheduled 10:15-11:00, the note arrived 10:48, and the brief called it a
    meeting with no notes while listing its note directly above.
    """
    ledger = Ledger()
    start = datetime(2026, 9, 11, 10, 15, tzinfo=_PT)
    ledger.seed_day(
        [
            {
                "id": "e1",
                "summary": "Discovery Content Discussions",
                "start": start,
                "end": start.replace(hour=11, minute=0),
                "attendees": ["a@example.com", "b@example.com"],
            }
        ]
    )

    attached = ledger.offer_note(
        Match(
            title="Discovery Content Discussions",
            arrived=start.replace(hour=10, minute=48),
            attendees=[],
            source="gemini",
        )
    )

    assert attached is not None, "a note from a meeting that ran short was dropped"
    assert ledger.notes_gaps(start.replace(hour=17)) == []


# --------------------------------------------------------------------------
# qualification, from a backtest against real calendar data (#90)
# --------------------------------------------------------------------------


def _event(**over):
    base = {
        "id": "e1",
        "summary": "a meeting",
        "start": datetime(2026, 9, 8, 9, 0, tzinfo=_PT),
        "end": datetime(2026, 9, 8, 10, 0, tzinfo=_PT),
        "attendees": ["a@example.com", "b@example.com"],
    }
    base.update(over)
    return base


def test_an_interview_qualifies_even_though_only_the_principal_is_invited():
    """An ATS invite lists ONLY the principal - the candidate is invited
    through a separate calendar - so the 2+ attendee rule made a 45-minute
    interview invisible. Three of them landed on one real Friday.

    The organizer is the evidence: somebody else put this in his day, which
    is what distinguishes it from a focus block he made for himself.
    """
    interview = _event(
        summary="Interview - a candidate - Product Lead",
        attendees=["principal@example.com"],
        organizer="recruiting@example.com",
    )

    assert qualifies(interview)


def test_a_solo_block_he_made_himself_is_still_not_a_meeting():
    """The other side of that rule - it must not sweep in his own holds."""
    own_hold = _event(summary="Focus time", attendees=["principal@example.com"])

    assert not qualifies(own_hold)
    assert not qualifies(_event(summary="Veda pick up", attendees=[]))


def test_a_booked_room_is_not_a_participant():
    """Google lists conference rooms as attendees. A solo block with a room
    booked therefore had two "attendees" and was chased as a meeting - and a
    room cannot take a note, so it was a gap that could never close.
    """
    room = "c_1887ml11ltcrgh14m2e0ahj80ojns@resource.calendar.google.com"
    solo_in_a_room = _event(
        summary="Focus block, room booked", attendees=["principal@example.com", room]
    )

    assert not qualifies(solo_in_a_room)


def test_a_real_meeting_in_a_room_still_qualifies():
    room = "c_1887ml11ltcrgh14m2e0ahj80ojns@resource.calendar.google.com"
    real = _event(attendees=["a@example.com", "b@example.com", room])

    assert qualifies(real)


def test_a_hold_he_organised_himself_is_not_a_meeting_even_with_an_organizer():
    """Every calendar entry has an organizer, including his own holds. The
    rule is "somebody ELSE put this in his day", so the self case has to be
    distinguishable - google marks it, and the shaper passes it through."""
    own = _event(
        summary="Focus time",
        attendees=["principal@example.com"],
        organizer="principal@example.com",
        organizer_is_self=True,
    )

    assert not qualifies(own)


def test_an_organizer_with_no_self_marker_and_no_principal_is_not_assumed():
    """If we cannot tell whose hold it is, we do not invent an answer - the
    attendee count is the only evidence left, and it says no."""
    unknown = _event(attendees=["principal@example.com"], organizer="someone@example.com")

    assert qualifies(unknown), "an organizer we cannot match to him reads as somebody else"


def test_a_note_generated_hours_late_still_attaches():
    """Measured across ~100 real notes: DELIVERY is tight (2-94 min from
    generation to inbox), but GENERATION runs late. A Sep 10 11:00-12:00
    meeting was generated Sep 11 00:52 - 12.9 hours after it ended - and the
    six-hour window dropped it, so the meeting was a gap forever.
    """
    ledger = Ledger()
    start = datetime(2026, 9, 10, 11, 0, tzinfo=_PT)
    ledger.seed_day(
        [_event(summary="Finance x Data meeting", start=start, end=start.replace(hour=12))]
    )

    attached = ledger.offer_note(
        Match(
            title="Finance x Data meeting",
            arrived=datetime(2026, 9, 11, 0, 56, tzinfo=_PT),
            attendees=[],
            source="gemini",
        )
    )

    assert attached is not None, "a late-generated note was dropped"


def test_the_window_stays_short_enough_that_a_daily_standup_is_unambiguous():
    """The bound on widening. A daily recurring meeting has rows 24h apart, so
    a window of 24h or more lets one note match two rows - and an ambiguous
    note attaches to NEITHER, which trades a late gap for a lost note.
    """
    from daydag.ledger import ARRIVAL_WINDOW, ENDS_EARLY

    span = ARRIVAL_WINDOW["gemini"] + ENDS_EARLY

    assert span < timedelta(hours=24), (
        f"window {span} spans a daily recurrence; two rows can match one note"
    )


# --------------------------------------------------------------------------
# the calendar declares the note (#91)
# --------------------------------------------------------------------------
#
# Google attaches the Gemini notes doc to the event itself. That is the source
# STATING a note exists, where `ARRIVAL_WINDOW` was reconstructing the same
# fact from a title and a guessed time bound - and it is knowable the moment
# the meeting ends rather than whenever the mail lands.


def test_a_meeting_whose_calendar_entry_declares_a_note_is_not_a_gap():
    """No note ingested yet, and still not missing - because the calendar said so."""
    led = Ledger()
    led.seed_day([ev(notes_attached=True)])

    assert led.notes_gaps(as_of=T + timedelta(hours=2)) == []


def test_a_meeting_with_no_declaration_and_no_note_is_still_a_gap():
    """The other half: declaring must not mean nothing is ever reported."""
    led = Ledger()
    led.seed_day([ev(notes_attached=False)])

    assert led.notes_gaps(as_of=T + timedelta(hours=2)) == ["Discovery sync"]


def test_googles_raw_attachment_list_is_read_when_nobody_shaped_it():
    """The agent hands back the connector payload; shaping is a contract in
    SKILL.md, and a contract is exactly what gets forgotten. Losing the signal
    silently is the docs-ahead-of-code failure this repo keeps finding."""
    led = Ledger()
    led.seed_day([ev(attachments=[{"title": "Notes by Gemini", "fileUrl": "https://d/1"}])])

    assert led.notes_gaps(as_of=T + timedelta(hours=2)) == []


def test_a_recording_attachment_is_not_a_note_declaration():
    """The false positive that makes 'has an attachment' the wrong test.

    A Drive recording attached to the SERIES master shows up on every instance:
    the real "Data Health Check" carries a 2024/10/28 recording on its Sep 2026
    occurrences. Matching on the title keeps that from declaring a note for a
    meeting that never produced one - forever, on every future instance.
    """
    led = Ledger()
    led.seed_day(
        [ev(attachments=[{"title": "Data Health Check - 2024/10/28 09:00 CST - Recording"}])]
    )

    assert led.notes_gaps(as_of=T + timedelta(hours=2)) == ["Discovery sync"]


def test_a_declared_row_still_accepts_the_note_when_it_arrives():
    """Declaration silences the GAP, it does not switch matching off. Ingestion
    wants the note itself, and a declared row that refused to match would strand
    every note whose meeting was declared."""
    led = Ledger()
    led.seed_day([ev(notes_attached=True)])

    matched = led.offer_note(
        Match(
            title="Discovery sync",
            arrived=T + timedelta(hours=1, minutes=30),
            attendees=["nitin", "jon"],
            source="gemini",
        )
    )

    assert matched is not None, "a declared meeting could not receive its own note"
    assert matched.note is not None


def test_the_declaration_is_per_instance_not_per_series():
    """Measured on the real "1:1 | 2x weekly" series: the Sep 1 occurrence
    carries a notes doc and produced a note; the Sep 10 one carries neither.
    Same `event_id` series, different instances - so a declaration on one must
    not silence the other, or a recurring 1:1 becomes permanently unreportable
    after its first note.
    """
    led = Ledger()
    led.seed_day(
        [
            ev(id="s1", start=T, end=T + timedelta(minutes=30), notes_attached=True),
            ev(
                id="s1",
                start=T + timedelta(days=9),
                end=T + timedelta(days=9, minutes=30),
                notes_attached=False,
            ),
        ]
    )

    assert led.notes_gaps(as_of=T + timedelta(days=10)) == ["Discovery sync"]


def test_a_notes_doc_in_another_language_still_declares_a_note():
    """The title is LOCALIZED, so the title cannot be the test.

    A real event carries both "Notes by Gemini" and "Anotações do Gemini"; a
    meeting run in a pt-BR locale carries only the second. Matching English
    would report it as a gap forever - and these invites carry a large
    Brazilian contingent and São Paulo / Mexico City timezones, so this is a
    live case rather than a hypothetical one.
    """
    led = Ledger()
    led.seed_day(
        [
            ev(
                attachments=[
                    {
                        "title": "Anotações do Gemini",
                        "fileUrl": "https://docs.google.com/document/d/1f/edit?usp=meet_tnfm_calendar",
                    }
                ]
            )
        ]
    )

    assert led.notes_gaps(as_of=T + timedelta(hours=2)) == []


def test_a_recording_of_a_meeting_named_gemini_is_still_not_a_note():
    """The false positive that a substring match on the title would let in.

    `usp=drive_web` vs `usp=meet_tnfm_calendar` separates a recording from a
    notes doc structurally, which is why the url marker is the primary test and
    the English title is only a fallback.
    """
    led = Ledger()
    led.seed_day(
        [
            ev(
                attachments=[
                    {
                        "title": "Gemini rollout - 2026/09/02 16:00 EDT - Recording",
                        "fileUrl": "https://drive.google.com/file/d/1j/view?usp=drive_web",
                    }
                ]
            )
        ]
    )

    assert led.notes_gaps(as_of=T + timedelta(hours=2)) == ["Discovery sync"]
