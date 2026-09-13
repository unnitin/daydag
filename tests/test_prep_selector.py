"""Naming the meeting you want prepped, instead of taking whatever is next.

`prep` preps the NEXT qualifying meeting, because a prep ping is the only push
allowed to interrupt and is worth exactly as much as its timing. Asking for a
particular one is a different question - *"prep me for the Finance call"* - and
it had nowhere to go.

The hard part is not matching. It is what to do when the selector matches more
than one meeting, which is the common case rather than the edge: "Will" is in a
recurring 1:1 and also in a steering meeting, and a week holds several of each.
Invariant 5 says surface, do not resolve. Guessing which one he meant produces
prep for the wrong meeting, and prep for the wrong meeting is worse than none -
he reads it, trusts it, and walks into the other one cold.

Names here are fixtures. The repo is public; real ones live in `.env` and
arrive as arguments.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from daydag.ledger import Row
from daydag.prep_selector import HORIZON_DAYS, Selection, select

NOW = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)


def _row(summary: str, *attendees: str, hours: int = 4, event_id: str = "") -> Row:
    start = NOW + timedelta(hours=hours)
    return Row(
        event_id=event_id or summary.lower().replace(" ", "-"),
        start=start,
        end=start + timedelta(minutes=30),
        summary=summary,
        attendees=list(attendees),
    )


PRINCIPAL = "principal@example.com"


# --------------------------------------------------------------------------
# matching a person
# --------------------------------------------------------------------------


def test_a_first_name_matches_the_attendee_it_belongs_to():
    rows = [
        _row("1:1 Bi-Weekly | Nitin x Wren", PRINCIPAL, "wren.alder@example.com"),
        _row("Pod Steering", PRINCIPAL, "bo.finch@example.com", hours=6),
    ]

    found = select(rows, "wren", now=NOW)

    assert found.one is not None
    assert found.one.summary.endswith("Wren")


def test_a_full_name_needs_both_halves_to_match():
    """ "Bo Finch" must not match a different Bo. Two tokens, both required."""
    rows = [
        _row("Sync", PRINCIPAL, "bo.alder@example.com"),
        _row("Review", PRINCIPAL, "bo.finch@example.com", hours=6),
    ]

    found = select(rows, "bo finch", now=NOW)

    assert found.one is not None
    assert found.one.summary == "Review"


def test_a_name_in_the_title_matches_even_with_no_attendee_list():
    """An ATS interview lists only the principal - the other party comes
    through a different calendar - so the title is all there is."""
    rows = [_row("Interview - Wren Alder - Staff Engineer", PRINCIPAL)]

    assert select(rows, "wren alder", now=NOW).one is not None


# --------------------------------------------------------------------------
# matching a title
# --------------------------------------------------------------------------


def test_a_phrase_matches_the_title_loosely():
    rows = [
        _row("Finance x Data meeting", PRINCIPAL, "a@example.com"),
        _row("Pod Steering", PRINCIPAL, "b@example.com", hours=6),
    ]

    assert select(rows, "finance x data", now=NOW).one is not None


def test_matching_ignores_case_and_spacing():
    rows = [_row("Finance x Data meeting", PRINCIPAL, "a@example.com")]

    assert select(rows, "  FINANCE X DATA  ", now=NOW).one is not None


# --------------------------------------------------------------------------
# the case that matters: more than one
# --------------------------------------------------------------------------


def test_two_matches_are_surfaced_rather_than_resolved():
    """Invariant 5. Prep for the wrong meeting is worse than no prep - he reads
    it, trusts it, and walks into the other one cold."""
    rows = [
        _row("1:1 | Nitin x Wren", PRINCIPAL, "wren.alder@example.com", hours=4),
        _row("Pod Steering", PRINCIPAL, "wren.alder@example.com", hours=30),
    ]

    found = select(rows, "wren", now=NOW)

    assert found.one is None
    assert len(found.candidates) == 2


def test_the_same_meeting_twice_in_the_horizon_is_still_ambiguous():
    """A recurring 1:1 twice a week is the common case, not the edge. Silently
    taking the sooner one is a guess dressed as an answer."""
    rows = [
        _row("1:1 | 2x weekly | Nitin x Wren", PRINCIPAL, "wren.alder@example.com", hours=4),
        _row(
            "1:1 | 2x weekly | Nitin x Wren",
            PRINCIPAL,
            "wren.alder@example.com",
            hours=72,
            event_id="later",
        ),
    ]

    found = select(rows, "wren", now=NOW)

    assert found.one is None
    assert len(found.candidates) == 2


def test_nothing_matching_says_what_it_looked_for():
    rows = [_row("Pod Steering", PRINCIPAL, "a@example.com")]

    found = select(rows, "wren", now=NOW)

    assert found.one is None
    assert found.candidates == ()
    assert "wren" in found.render().lower()


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------


def test_a_meeting_already_over_is_never_selected():
    """Prep is for something coming up. A meeting this morning cannot be
    prepped for this afternoon."""
    rows = [_row("1:1 | Nitin x Wren", PRINCIPAL, "wren.alder@example.com", hours=-2)]

    assert select(rows, "wren", now=NOW).one is None


def test_a_meeting_past_the_horizon_is_not_selected():
    """Bounded so the answer stays about this week. Two weeks out, the Slack
    and mail evidence a prep is built from has not happened yet."""
    rows = [
        _row(
            "1:1 | Nitin x Wren",
            PRINCIPAL,
            "wren.alder@example.com",
            hours=24 * (HORIZON_DAYS + 2),
        )
    ]

    assert select(rows, "wren", now=NOW).one is None


def test_an_empty_selector_is_refused_rather_than_matching_everything():
    """`""` as a substring is in every title. Matching all of them and taking
    the first would look exactly like a working selector."""
    rows = [_row("Pod Steering", PRINCIPAL, "a@example.com")]

    with pytest.raises(ValueError):
        select(rows, "   ", now=NOW)


# --------------------------------------------------------------------------
# it does not leak the principal
# --------------------------------------------------------------------------


def test_the_principal_is_not_matchable():
    """He is on every meeting, so his own name selects all of them - which
    reads as "ambiguous" on every query and hides a real miss."""
    rows = [
        _row("Pod Steering", PRINCIPAL, "a@example.com"),
        _row("Finance x Data", PRINCIPAL, "b@example.com", hours=6),
    ]

    found = select(rows, "principal", now=NOW, principal=PRINCIPAL)

    assert found.one is None
    assert found.candidates == ()


def test_selection_renders_the_candidates_with_their_times():
    rows = [
        _row("1:1 | Nitin x Wren", PRINCIPAL, "wren.alder@example.com", hours=4),
        _row("Pod Steering", PRINCIPAL, "wren.alder@example.com", hours=30),
    ]

    rendered = select(rows, "wren", now=NOW).render()

    assert "Pod Steering" in rendered
    assert "1:1" in rendered
    assert isinstance(Selection((), "x").render(), str)


def test_the_domain_half_of_an_address_is_not_matchable():
    """Every colleague shares it, so a selector hitting the domain would match
    the whole invite list and read as "ambiguous" on every query."""
    rows = [
        _row("Pod Steering", PRINCIPAL, "wren.alder@example.com"),
        _row("Finance x Data", PRINCIPAL, "bo.finch@example.com", hours=6),
    ]

    found = select(rows, "example", now=NOW)

    assert found.candidates == ()


def test_the_principal_is_skipped_by_address_not_by_luck():
    """`attendees` are EMAILS, so the principal has to arrive as one. The first
    wiring passed a Slack id, which matches no address - the skip was dead
    while looking wired, and the tests passed because the fixture principal
    happened to be email-shaped.
    """
    rows = [_row("Pod Steering", "nitin.example@example.com", "wren.alder@example.com")]

    by_email = select(rows, "nitin", now=NOW, principal="nitin.example@example.com")
    # Deliberately not shaped like a real Slack id - the secret scanner cannot
    # tell a fixture from the real thing and is right not to try. What matters
    # is only that it is NOT an email address.
    by_slack_id = select(rows, "nitin", now=NOW, principal="a-slack-id-not-an-address")

    assert by_email.candidates == (), "the principal matched himself"
    assert by_slack_id.candidates != (), (
        "this must differ - it is what proves the skip depends on the address"
    )


def test_a_surname_that_exists_only_in_the_display_name_still_matches():
    """Real addresses are often a bare first name - `jonathan@`, `alex@` - so
    the surname lives only in google's `displayName`. Shaped as the address
    alone, "jonathan strauss" cannot match and bare "jonathan" matches a
    DIFFERENT Jonathan. `SKILL.md` requires the `"Full Name <addr>"` form.
    """
    rows = [
        _row("Label Summit", PRINCIPAL, "Jonathan Strauss <jonathan@example.com>"),
        _row("Pod Steering", PRINCIPAL, "jonathan.other@example.com", hours=6),
    ]

    found = select(rows, "jonathan strauss", now=NOW)

    assert found.one is not None
    assert found.one.summary == "Label Summit"


def test_a_bare_address_still_matches_its_first_name():
    """The display name is an addition, not a replacement - an attendee shaped
    the old way must not stop matching."""
    rows = [_row("Sync", PRINCIPAL, "wren.alder@example.com")]

    assert select(rows, "wren", now=NOW).one is not None
