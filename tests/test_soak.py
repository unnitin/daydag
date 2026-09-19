"""The gate's own instrument, which has to be right before it can judge.

the M2-5 gate (#13) says: run the brief by hand for five working days, log every
edit requested, and pass when the count trends down. SPEC §8 adds the rule that
actually changes the product - *anything he asks twice gets folded into the
skill*. So the instrument has two jobs, and the second matters more: counting,
and noticing a repeat.

The failure to guard against is the flattering one. A gate whose arithmetic
rounds toward "passed" tells you the brief is fit when it is not, and the whole
point of the five days is to find out before four more loops render into the
same templates.
"""

from __future__ import annotations

from datetime import date

import pytest

from daydag.eventlog import EventLog
from daydag.soak import REQUIRED_DAYS, Soak

MON = date(2026, 9, 14)


@pytest.fixture
def journal(tmp_path):
    return Soak(EventLog.open(tmp_path / "events.db"))


def _run(journal, day, *edits):
    journal.shipped(day)
    for edit in edits:
        journal.note(edit, day=day)


# --------------------------------------------------------------------------
# counting
# --------------------------------------------------------------------------


def test_a_day_with_no_edits_still_counts_as_shipped(journal):
    """The best possible day is the one with nothing to log, and a gate that
    only counts edits would treat it as a day that never happened."""
    journal.shipped(MON)

    assert journal.report().days_shipped == 1


def test_edits_are_counted_per_day(journal):
    _run(journal, MON, "drop the emoji", "shorter overnight section")
    _run(journal, MON.replace(day=15), "shorter overnight section")

    report = journal.report()

    assert report.per_day == {MON: 2, MON.replace(day=15): 1}


def test_the_same_day_recorded_twice_is_still_one_day(journal):
    """Re-running a brief after a fix is normal and must not inflate the gate."""
    journal.shipped(MON)
    journal.shipped(MON)

    assert journal.report().days_shipped == 1


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------


def test_four_clean_days_do_not_pass_the_gate(journal):
    """Five is the number in the ticket. Four perfect days is not four fifths
    of an answer - the five exist to outlast a quiet week."""
    for offset in range(4):
        journal.shipped(MON.replace(day=14 + offset))

    report = journal.report()

    assert not report.passed
    assert report.days_shipped < REQUIRED_DAYS


def test_five_days_trending_down_passes(journal):
    _run(journal, date(2026, 9, 14), "a", "b", "c")
    _run(journal, date(2026, 9, 15), "d", "e")
    _run(journal, date(2026, 9, 16), "f")
    _run(journal, date(2026, 9, 17), "g")
    _run(journal, date(2026, 9, 18))

    assert journal.report().passed


def test_five_days_with_edits_still_climbing_does_not_pass(journal):
    """The gate is the trend, not the attendance."""
    _run(journal, date(2026, 9, 14), "a")
    _run(journal, date(2026, 9, 15), "b")
    _run(journal, date(2026, 9, 16), "c", "d")
    _run(journal, date(2026, 9, 17), "e", "f")
    _run(journal, date(2026, 9, 18), "g", "h", "i")

    report = journal.report()

    assert not report.passed
    assert "trend" in report.why.lower(), report.why


def test_an_outstanding_repeat_blocks_the_gate_however_good_the_trend(journal):
    """SPEC §8: anything asked twice gets folded into the skill. A repeat is
    unfinished work on the skill itself, so a falling count does not excuse it -
    that is exactly the case where the number flatters and the product has a
    known, named defect sitting in it.
    """
    _run(journal, date(2026, 9, 14), "put the overlaps last", "drop the emoji")
    _run(journal, date(2026, 9, 15), "put the overlaps last")
    _run(journal, date(2026, 9, 16))
    _run(journal, date(2026, 9, 17))
    _run(journal, date(2026, 9, 18))

    report = journal.report()

    assert not report.passed
    assert "put the overlaps last" in report.repeats
    assert "repeat" in report.why.lower(), report.why


def test_a_repeat_that_was_folded_in_stops_blocking(journal):
    """Or the gate can never close once anything is asked twice."""
    _run(journal, date(2026, 9, 14), "put the overlaps last", "drop the emoji")
    _run(journal, date(2026, 9, 15), "put the overlaps last")
    for offset in (2, 3, 4):
        journal.shipped(date(2026, 9, 14 + offset))
    journal.folded("put the overlaps last")

    report = journal.report()

    assert report.repeats == ()
    assert report.passed, report.why


# --------------------------------------------------------------------------
# spotting the repeat
# --------------------------------------------------------------------------


def test_a_repeat_is_seen_through_casing_and_punctuation(journal):
    """He is typing these into a terminal on five different mornings. Matching
    the literal string would miss almost every real repeat."""
    _run(journal, date(2026, 9, 14), "Drop the emoji.")
    _run(journal, date(2026, 9, 15), "drop the emoji")

    assert journal.report().repeats == ("Drop the emoji.",)


def test_two_edits_asked_on_the_same_day_are_not_a_repeat(journal):
    """Saying it twice in one sitting is one correction, not a trend."""
    _run(journal, date(2026, 9, 14), "drop the emoji", "drop the emoji")

    assert journal.report().repeats == ()


def test_different_edits_are_not_collapsed(journal):
    _run(journal, date(2026, 9, 14), "drop the emoji")
    _run(journal, date(2026, 9, 15), "drop the overnight section")

    assert journal.report().repeats == ()


# --------------------------------------------------------------------------
# it lives outside the vault
# --------------------------------------------------------------------------


def test_the_journal_writes_nothing_to_the_vault(tmp_path):
    """CLAUDE.md §8: history and metrics go to the event log, outside the vault,
    and a brief's edits quote its content - meeting titles and names."""
    vault = tmp_path / "vault"
    vault.mkdir()
    journal = Soak(EventLog.open(tmp_path / "events.db"))

    _run(journal, MON, "cut the 1:1 with the candidate from the list")

    assert not any(p.is_file() for p in vault.rglob("*"))


def test_too_few_days_reports_no_trend_rather_than_a_favourable_one(journal):
    """`trending` is read on its own, not only through `passed`.

    Two points cannot show a direction. Defaulting them to True is the
    flattering failure this whole instrument exists to avoid - it would report
    "edits are trending down" on day two of five.
    """
    _run(journal, date(2026, 9, 14), "a", "b")
    _run(journal, date(2026, 9, 15), "c")

    assert journal.report().trending is False


def test_a_day_logged_only_by_its_edits_still_counts(journal):
    """A morning where he fires off a correction without anyone calling
    `shipped()` is a day the brief ran - the edit is the proof. Counting only
    explicit `shipped()` calls would quietly need six mornings to pass a
    five-day gate.
    """
    journal.note("drop the emoji", day=date(2026, 9, 14))

    report = journal.report()

    assert report.days_shipped == 1
    assert report.per_day == {date(2026, 9, 14): 1}
