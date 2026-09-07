"""The DayDAG/ vault folder and the event log.

Two stores with opposite requirements (ARCHITECTURE, "State: two stores").
These tests describe the split; none of it is implemented yet.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from daydag.state import DecisionQueue, EventLog, StateFolder

#: A fixed offset, so an offset other than UTC is exercised without pulling in
#: a tz database or depending on the machine's own zone.
PACIFIC = timezone(timedelta(hours=-7))


@pytest.fixture
def folder(tmp_path):
    return StateFolder.create(tmp_path / "DayDAG")


def test_create_lays_out_the_four_files_and_two_dirs(folder):
    names = {p.name for p in folder.root.iterdir()}
    assert names == {
        "README.md",
        "State.md",
        "Decisions.md",
        "Watchlist.md",
        "Proposals",
        "Archive",
    }


def test_state_is_regenerated_each_loop(folder):
    """State.md is a projection - the agent rewrites it wholesale every run."""
    folder.write_state(chase=[{"owner": "seth", "ask": "compute consolidation"}])
    folder.write_state(chase=[{"owner": "jon", "ask": "wave 2 scope"}])
    assert "seth" not in folder.read_state()
    assert "jon" in folder.read_state()


def test_decisions_are_appended_never_regenerated(folder):
    """An answer written in the margin must survive the next loop."""
    q = DecisionQueue(folder)
    first = q.add("draft nudge to seth?")
    q.add("close the compute loop?")
    assert first in q.render(), "an earlier decision was dropped on append"


def test_a_hand_edit_wins_over_derived_state(folder):
    """ARCHITECTURE: 'A hand edit is itself an event, and wins over derived state.'"""
    q = DecisionQueue(folder)
    item = q.add("draft nudge to seth?")
    folder.decisions_path.write_text(
        folder.decisions_path.read_text().replace(f"[{item}]", f"[{item}] no")
    )
    assert q.answer_for(item) == "no"


def test_decision_ages_to_parked_after_three_pushes(folder):
    """Silence is an answer; the agent says out loud that it read it that way."""
    q = DecisionQueue(folder)
    item = q.add("draft nudge to seth?")
    for _ in range(3):
        q.render()
    assert q.status(item) == "parked"


@pytest.mark.guardrail
def test_sensitive_items_never_reach_the_vault(folder):
    """weekly-feedback-scan carry-forward lives only in the log (ARCHITECTURE)."""
    log = EventLog.open(":memory:")
    log.record("carry_forward", subject="seth", body="growth area", sensitivity="private")
    folder.write_state(chase=log.chase_items())
    assert "growth area" not in folder.read_state()


def test_event_log_answers_median_days_to_answer():
    """The reason the log exists: markdown cannot answer this (SPEC section 8)."""
    log = EventLog.open(":memory:")
    log.record("loop_opened", key="a", day=0)
    log.record("loop_answered", key="a", day=4)
    log.record("loop_opened", key="b", day=0)
    log.record("loop_answered", key="b", day=2)
    assert log.median_days_to_answer() == 3


# -- mirror fetch times (#60) ---------------------------------------------


def test_the_log_remembers_when_each_mirror_last_fetched_cleanly():
    """A stale mirror's line is dated from here, so the log is what carries it.

    Per repo, and the *latest* success wins - the log is append-only, so a repo
    that has fetched a hundred times has a hundred rows and only the newest one
    is the answer to "how old is what you are showing me".
    """
    log = EventLog.open(":memory:")
    log.record_fetch("ExampleOrg/service-a", at=datetime(2026, 9, 5, 6, 40, tzinfo=UTC))
    log.record_fetch("ExampleOrg/service-a", at=datetime(2026, 9, 7, 6, 40, tzinfo=UTC))
    log.record_fetch("ExampleOrg/service-b", at=datetime(2026, 9, 6, 6, 40, tzinfo=UTC))

    assert log.last_fetch("ExampleOrg/service-a") == datetime(2026, 9, 7, 6, 40, tzinfo=UTC)
    assert log.last_fetch("ExampleOrg/service-b") == datetime(2026, 9, 6, 6, 40, tzinfo=UTC)


def test_a_repo_that_has_never_fetched_has_no_last_fetch():
    """`None`, not `now()`. A default of "now" would date a mirror that has
    never once been read as if it were fresh - the exact lie #60 is about."""
    assert EventLog.open(":memory:").last_fetch("ExampleOrg/never-seen") is None


def test_an_unparseable_fetch_row_is_skipped_rather_than_crashing_the_pulse():
    """The log is a file on disk a human can also touch. A row that is not a
    timestamp must not take the 6:40am brief down with it."""
    log = EventLog.open(":memory:")
    log.record("mirror_fetched", repo="ExampleOrg/service-a", at="last tuesday")
    log.record_fetch("ExampleOrg/service-a", at=datetime(2026, 9, 7, 6, 40, tzinfo=UTC))

    assert log.last_fetch("ExampleOrg/service-a") == datetime(2026, 9, 7, 6, 40, tzinfo=UTC)


def test_a_naive_stamp_beside_an_aware_one_does_not_crash_the_comparison():
    """The failure mode this cost: one naive row and one aware row for the same
    repo made them incomparable, and `>` raised TypeError inside `ensure` -
    which catches only MirrorUnavailable, so the whole brief died on a stamp.

    A naive stamp is read as UTC rather than refused. The caller is a scheduled
    pre-step; a missing tzinfo must not stop a morning.
    """
    log = EventLog.open(":memory:")
    log.record_fetch("ExampleOrg/service-a", at=datetime(2026, 9, 5, 6, 40))  # naive
    log.record_fetch("ExampleOrg/service-a", at=datetime(2026, 9, 7, 6, 40, tzinfo=UTC))
    log.record("mirror_fetched", repo="ExampleOrg/service-a", at="2026-09-06T06:40:00")  # by hand

    assert log.last_fetch("ExampleOrg/service-a") == datetime(2026, 9, 7, 6, 40, tzinfo=UTC)


def test_a_stamp_in_another_offset_comes_back_as_utc():
    """Two mirrors dated in two offsets would otherwise render side by side in
    one block, both looking like local time and neither saying which."""
    log = EventLog.open(":memory:")
    log.record_fetch("ExampleOrg/service-a", at=datetime(2026, 9, 7, 6, 40, tzinfo=PACIFIC))

    fetched = log.last_fetch("ExampleOrg/service-a")

    assert fetched == datetime(2026, 9, 7, 13, 40, tzinfo=UTC)
    assert fetched.utcoffset() == timedelta(0)
