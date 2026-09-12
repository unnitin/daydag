"""Two runs on two days, and what the second one remembers of the first.

Every other end-to-end suite walks ONE run. This one is about the seam between
runs, which is where the product's differentiator actually lives: the ledger
seeds today's meetings so that tomorrow can report the ones that produced no
notes. With an in-memory ledger per run there is no tomorrow, and
"meetings w/ no notes" can only ever report the empty set - silently, because
an empty section is omitted rather than labelled.

Real objects throughout: a real SQLite event log on disk, a real vault folder
in a temp dir, a hand-wound clock. Only the connectors are absent, and they are
absent by construction - the runner takes payloads, it does not fetch.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daydag import run
from daydag.config import Identities
from daydag.state import EventLog, StateFolder

PT = timezone(timedelta(hours=-7))
MONDAY = datetime(2026, 9, 7, 6, 40, tzinfo=PT)
TUESDAY = MONDAY + timedelta(days=1)


@pytest.fixture
def home(tmp_path):
    """A vault, a log and an identities file - the three things a real run has."""
    vault = tmp_path / "vault"
    (vault / "Weekly Notes").mkdir(parents=True)
    env = tmp_path / ".env"
    env.write_text(f"SLACK_USER_PRINCIPAL=UPRINCIPAL1\nVAULT_ROOT={vault}\n", encoding="utf-8")
    return {
        "identities": Identities.from_file(env),
        "log": tmp_path / "events.db",
        "vault": vault,
    }


def _meeting(day: datetime, summary: str, event_id: str):
    return {
        "id": event_id,
        "summary": summary,
        # Explicit minutes: `replace(hour=10)` on a 06:40 base is 10:40, which
        # silently put the note's arrival BEFORE the meeting ended.
        "start": day.replace(hour=9, minute=0).isoformat(),
        "end": day.replace(hour=10, minute=0).isoformat(),
        "attendees": ["principal@example.com", "vp-data@example.com"],
    }


def _payloads(calendar=(), vault=""):
    return {"calendar": list(calendar), "slack": [], "gmail": [], "vault": vault}


# --------------------------------------------------------------------------
# the differentiator: a gap created today, reported tomorrow
# --------------------------------------------------------------------------


def test_a_meeting_today_is_a_notes_gap_tomorrow(home):
    """The one section nothing else in the system can produce.

    `_seed_and_gaps` seeds today's rows precisely so tomorrow can report the
    ones that produced nothing. A ledger rebuilt empty on every run cannot do
    that - and says nothing about it, because an empty section is omitted.
    """
    run.render(
        "morning",
        now=MONDAY,
        identities=home["identities"],
        payloads=_payloads(calendar=[_meeting(MONDAY, "Pod Steering", "e1")]),
        log=home["log"],
    )

    tomorrow = run.render(
        "morning",
        now=TUESDAY,
        identities=home["identities"],
        payloads=_payloads(),
        log=home["log"],
    )

    assert "Pod Steering" in tomorrow, (
        f"yesterday's meeting was forgotten, so its gap can never be reported:\n{tomorrow}"
    )


def test_a_meeting_that_did_produce_a_note_is_not_a_gap(home):
    """The other half - remembering must not mean reporting everything."""
    run.render(
        "morning",
        now=MONDAY,
        identities=home["identities"],
        payloads=_payloads(calendar=[_meeting(MONDAY, "Pod Steering", "e1")]),
        log=home["log"],
    )

    tomorrow = run.render(
        "morning",
        now=TUESDAY,
        identities=home["identities"],
        payloads={
            "calendar": [],
            "slack": [],
            "gmail": [
                {
                    "subject": 'Notes: "Pod Steering" 2026-09-07',
                    "id": "m1",
                    # Its own arrival, as a real payload carries - a note is
                    # only matched inside six hours of the meeting ending.
                    "date": MONDAY.replace(hour=10, minute=30).isoformat(),  # after it ended
                }
            ],
            "vault": "",
        },
        log=home["log"],
    )

    assert "no note found" not in tomorrow, (
        f"a meeting with a note was still reported as a gap:\n{tomorrow}"
    )


def test_without_a_log_each_run_still_works_and_simply_remembers_nothing(home):
    """The log is optional, because a first run has none and a test may not
    want one. What it must not do is fail."""
    text = run.render(
        "morning",
        now=MONDAY,
        identities=home["identities"],
        payloads=_payloads(calendar=[_meeting(MONDAY, "Pod Steering", "e1")]),
    )

    assert text.strip()


# --------------------------------------------------------------------------
# state: his edits are the input
# --------------------------------------------------------------------------


def test_a_hand_written_chase_item_reaches_the_brief(home):
    """CLAUDE.md: a hand edit is an event and wins over derived state. The
    loop reads `State.md` before it runs, or his corrections are noise it
    overwrites."""
    folder = StateFolder.create(home["vault"] / "DayDAG")
    folder.write_state(chase=[{"owner": "VP-Data", "ask": "the compute consolidation plan"}])

    text = run.render(
        "morning",
        now=MONDAY,
        identities=home["identities"],
        payloads=_payloads(),
        log=home["log"],
    )

    assert "compute consolidation" in text, f"State.md was not read:\n{text}"


def test_a_private_chase_item_never_reaches_the_vault_through_the_runner(home):
    """The runner writes `State.md` back, so it inherits the one `_visible`
    gate - or it becomes a second writer with its own idea of the rules."""
    log = EventLog.open(home["log"])
    log.record("carry_forward", sensitivity="private", owner="VP-AI", ask="SECRET-ASK", key="k1")

    run.render(
        "morning",
        now=MONDAY,
        identities=home["identities"],
        payloads=_payloads(),
        log=home["log"],
        write_state=True,
    )

    leaked = [
        path
        for path in (home["vault"] / "DayDAG").rglob("*")
        if path.is_file() and "SECRET-ASK" in path.read_text(encoding="utf-8")
    ]
    assert not leaked, f"a private chase item reached the vault: {leaked}"


# --------------------------------------------------------------------------
# the run log: a failed run leaves a row
# --------------------------------------------------------------------------


def test_a_run_leaves_a_row_so_a_silent_failure_is_diagnosable(home):
    run.render(
        "morning",
        now=MONDAY,
        identities=home["identities"],
        payloads=_payloads(),
        log=home["log"],
    )

    rows = EventLog.open(home["log"]).recorded("run")

    assert rows, "a run left no trace, which is what runlog exists to prevent"
