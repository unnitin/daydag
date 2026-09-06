"""Meeting ledger: calendar drives, notes attach (ARCHITECTURE, issue #35).

The point is not that every meeting has notes. It is that a missing one is
visible the next morning instead of discovered a month later.
"""

from datetime import datetime, timedelta

import pytest

from daydag.ledger import Ledger, Match

T = datetime(2026, 9, 8, 14, 0)


def ev(**kw):
    base = dict(
        id="e1",
        start=T,
        end=T + timedelta(hours=1),
        summary="Discovery sync",
        attendees=["nitin", "jon"],
        accepted=True,
        kind="meeting",
    )
    return {**base, **kw}


def test_qualifying_events_get_a_row():
    led = Ledger()
    led.seed_day([ev()])
    assert len(led.open_rows()) == 1


@pytest.mark.parametrize(
    "skip",
    [
        dict(attendees=["nitin"]),  # solo
        dict(accepted=False),  # declined
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
