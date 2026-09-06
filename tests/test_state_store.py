"""The DayDAG/ vault folder and the event log.

Two stores with opposite requirements (ARCHITECTURE, "State: two stores").
These tests describe the split; none of it is implemented yet.
"""

import pytest

from daydag.state import DecisionQueue, EventLog, StateFolder


@pytest.fixture
def folder(tmp_path):
    return StateFolder.create(tmp_path / "DayDAG")


def test_create_lays_out_the_four_files_and_two_dirs(folder):
    names = {p.name for p in folder.root.iterdir()}
    assert names == {"README.md", "State.md", "Decisions.md", "Watchlist.md",
                     "Proposals", "Archive"}


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
