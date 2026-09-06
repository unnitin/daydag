"""Meeting ledger: calendar drives, notes attach (ARCHITECTURE, issue #35).

The point is not that every meeting has notes. It is that a missing one is
visible the next morning instead of discovered a month later.
"""

from datetime import datetime, timedelta

import pytest

from daydag.ledger import Ledger, Match, title_from_gemini_subject

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
