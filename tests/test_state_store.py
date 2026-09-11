"""The DayDAG/ vault folder and the event log.

Two stores with opposite requirements (ARCHITECTURE, "State: two stores").
These tests describe the split; none of it is implemented yet.
"""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest

from daydag.state import ChaseItem, DecisionQueue, EventLog, NotesGap, StateFolder

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


# --------------------------------------------------------------------------
# the chase-item shape (#63): EventLog and write_state agree on one contract
# --------------------------------------------------------------------------


def test_chase_items_returns_the_one_shape_both_sides_are_held_to():
    """`EventLog.chase_items` builds `ChaseItem`, not a bare dict merge."""
    log = EventLog.open(":memory:")
    log.record("loop_opened", key="a", owner="seth", ask="compute consolidation", day=0)

    (item,) = log.chase_items()

    assert isinstance(item, ChaseItem)
    # Both access styles work, so every existing caller - dict-style or
    # attribute-style - keeps working unchanged.
    assert item["owner"] == "seth" == item.get("owner") == item.owner
    assert item["ask"] == "compute consolidation"
    assert item.get("sensitivity") == "normal"


def test_a_chase_item_recorded_with_only_a_key_warns_instead_of_a_bare_bullet(folder):
    """The exact bug in #63: a log entry with only `key` used to render `- ?`,
    a formatting glitch standing in for data nobody agreed had to be there."""
    log = EventLog.open(":memory:")
    log.record("loop_opened", key="DATA-812", day=0)

    folder.write_state(chase=log.chase_items())

    written = folder.read_state()
    assert "- ?" not in written.splitlines(), "the old silent-glitch shape is back"
    assert "DATA-812" in written
    assert "has no owner or ask recorded" in written


def test_a_chase_item_with_only_one_of_owner_or_ask_still_renders_the_other(folder):
    """Half a chase item is not the same failure as none of it - only a total
    miss on both fields is the case `write_state` has to call out by name."""
    folder.write_state(chase=[{"key": "DATA-812", "ask": "compute consolidation"}])

    written = folder.read_state()
    assert "compute consolidation" in written
    assert "has no owner or ask recorded" not in written


def test_write_state_still_accepts_a_bare_dict_for_chase(folder):
    """`ChaseItem` is a stricter shape underneath, but no existing caller that
    builds a plain dict by hand should have to change to keep working."""
    folder.write_state(chase=[{"owner": "VP-Data", "ask": "silver trigger"}])
    assert "silver trigger" in folder.read_state()


@pytest.mark.guardrail
def test_a_private_chase_item_is_filtered_whether_it_arrives_as_a_dict_or_a_chase_item(folder):
    """The filter reads `sensitivity` off either shape the same way."""
    folder.write_state(
        chase=[
            ChaseItem(key="a", owner="seth", ask="growth area", sensitivity="private"),
            {"owner": "seth", "ask": "compute consolidation"},
        ]
    )
    written = folder.read_state()
    assert "growth area" not in written
    assert "compute consolidation" in written


# --------------------------------------------------------------------------
# the notes-gap shape (#61 / GAP 3): a sensitive meeting title can be withheld
# --------------------------------------------------------------------------


def test_notes_gap_from_value_reads_a_bare_string_as_normal_sensitivity():
    """`ledger.notes_gaps()` still hands back a list[str]; this is the
    backward-compatible half of #61 - no existing caller has to change."""
    gap = NotesGap.from_value("Pod Steering")
    assert gap.title == "Pod Steering"
    assert gap.sensitivity == "normal"


def test_notes_gap_from_value_reads_a_tagged_dict():
    gap = NotesGap.from_value({"title": "exit interview follow-up", "sensitivity": "private"})
    assert gap.title == "exit interview follow-up"
    assert gap.sensitivity == "private"


def test_a_private_notes_gap_string_mix_still_renders_the_normal_one(folder):
    folder.write_state(
        notes_gaps=["Pod Steering", {"title": "comp review", "sensitivity": "private"}]
    )
    written = folder.read_state()
    assert "Pod Steering" in written
    assert "comp review" not in written


# --------------------------------------------------------------------------
# what review found after the first pass at #63
# --------------------------------------------------------------------------


def test_a_chase_item_keeps_fields_outside_its_own_schema():
    log = EventLog(sqlite3.connect(":memory:"))
    """`ChaseItem` contract 3 claims it is "a drop-in wherever a chase item was
    already a bare dict - every existing caller".

    `chase_items()` used to return `{**payload, ...}`, so a caller reading
    `item["day"]` got it. Whitelisting the nine named fields silently dropped
    every other key, which makes the drop-in claim false and turns an existing
    read into a `KeyError`. The nine are GUARANTEED to exist; they were never
    meant to be all there is.
    """
    log.record("loop_opened", key="k1", owner="VP-Data", ask="ship it", day=5)

    item = log.chase_items()[0]

    assert item["day"] == 5, "a recorded field vanished on the way out"
    assert item["owner"] == "VP-Data"
    assert item["status"] == "open", "the guaranteed fields still get defaults"


def test_reading_a_chase_item_out_of_a_hand_written_row_never_raises():
    log = EventLog(sqlite3.connect(":memory:"))
    """`from_payload`'s docstring says "never raises" - a human can hand-edit
    this log, so a row whose payload is valid JSON but not an object must
    degrade, not take `write_state` down with an AttributeError."""
    log._db.execute(
        "INSERT INTO events (kind, sensitivity, payload) VALUES (?, ?, ?)",
        ("carry_forward", "normal", "null"),
    )
    log._db.commit()

    items = log.chase_items()

    assert all(not item.has_owner_or_ask for item in items if not item.get("owner"))


def test_a_notes_gap_from_a_calendar_shaped_record_is_named_not_blank(tmp_path):
    """A mapping keyed `summary` rather than `title` produced `title=""` and
    rendered a bare `- ` bullet - the same shapeless-item failure the chase
    path in this very branch gave a named warning line."""
    folder = StateFolder.create(tmp_path / "DayDAG")
    folder.write_state(notes_gaps=[{"summary": "Pod Steering", "sensitivity": "normal"}])

    body = folder.read_state()

    assert "\n- \n" not in body and not body.rstrip().endswith("- "), (
        f"a blank bullet reached State.md:\n{body}"
    )
