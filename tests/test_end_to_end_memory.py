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
                    # only matched inside `ARRIVAL_WINDOW` of the meeting ending.
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
    folder.update_state(chase=[{"owner": "VP-Data", "ask": "the compute consolidation plan"}])

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


def test_a_note_that_arrived_today_matches_todays_meeting(home):
    """The same-day case, which the two-day test above does not reach.

    Notes were offered to the ledger BEFORE `_seed_and_gaps` created today's
    rows, so on a first run there was nothing to attach to and every meeting
    became a gap - while its note was listed, by name, in the section directly
    above. Found by running it against a real Friday: 9 meetings reported as
    having no notes, 8 of them with notes printed above.
    """
    meeting = _meeting(MONDAY, "Pod Steering", "e1")
    text = run.render(
        "morning",
        now=MONDAY.replace(hour=17),
        identities=home["identities"],
        payloads={
            "calendar": [meeting],
            "slack": [],
            "gmail": [
                {
                    "subject": "Notes: “Pod Steering” Sep 7, 2026",
                    "date": MONDAY.replace(hour=10, minute=30).isoformat(),
                    "id": "m1",
                }
            ],
            "vault": "",
        },
        log=home["log"],
    )

    assert "no note found" not in text, (
        f"a meeting whose note is listed above was still called a gap:\n{text}"
    )


# --------------------------------------------------------------------------
# #169: "meetings w/ no notes" has to be trustworthy
# --------------------------------------------------------------------------

FRIDAY = datetime(2026, 9, 25, 6, 40, tzinfo=PT)
FRIDAY_EVENING = FRIDAY.replace(hour=18, minute=0)
SATURDAY = FRIDAY + timedelta(days=1)


def _at(day: datetime, hour: int, summary: str, event_id: str, **extra):
    base = {
        "id": event_id,
        "summary": summary,
        "start": day.replace(hour=hour, minute=0).isoformat(),
        "end": day.replace(hour=hour, minute=30).isoformat(),
        "attendees": ["principal@example.com", "vp-data@example.com"],
        "response_status": "needsAction",
    }
    return {**base, **extra}


def _render(home, loop, now, calendar=()):
    return run.render(
        loop,
        now=now,
        identities=home["identities"],
        payloads=_payloads(calendar=calendar),
        log=home["log"],
    )


def _gap_lines(text: str) -> list[str]:
    """The lines of the "meetings w/ no notes" section, and only that one."""
    for block in text.split("\n\n"):
        heading, *lines = block.splitlines()
        if heading.startswith("meetings w/ no notes"):
            return lines
    return []


def _meetings(home) -> list[dict]:
    return [row for row in EventLog.open(home["log"]).recorded("meeting") if isinstance(row, dict)]


def test_the_2026_09_25_replay_leaves_only_the_meeting_that_really_has_no_note(home):
    """The morning snapshot, then the evening calendar, then the next morning.

    Three invites declined after the morning seeded them, one removed from
    the calendar, and one interview that genuinely produced no note. Before
    #169 all five were reported; `seed_day`'s setdefault let the morning win.
    """
    interview = _at(
        FRIDAY,
        8,
        "Interview - Eng candidate",
        "zoom-1",
        attendees=["principal@example.com"],
        organizer="ats@example.com",
    )
    one_on_one = _at(FRIDAY, 9, "Eng-2 / principal 1:1", "e-1on1")
    intro = _at(FRIDAY, 10, "Intro call - vendor", "e-intro")
    social = _at(FRIDAY, 11, "Team social", "e-social")
    vendor = _at(FRIDAY, 12, "Vendor check-in", "e-vendor")
    _render(home, "morning", FRIDAY, [interview, one_on_one, intro, social, vendor])

    declined = {"response_status": "declined"}
    evening = [
        interview,
        {**one_on_one, **declined},
        {**intro, **declined},
        {**social, **declined},
        # the vendor check-in is gone from the calendar entirely
    ]
    # The wrap does not list gaps itself; the next morning reads what it left.
    _render(home, "eod", FRIDAY_EVENING, evening)
    tomorrow = _render(home, "morning", SATURDAY)

    gaps = _gap_lines(tomorrow)
    assert len(gaps) == 1 and "Interview" in gaps[0], "\n".join(gaps) or tomorrow


def test_absence_from_a_day_that_was_not_fetched_is_not_evidence(home):
    """Tuesday's morning fetches Tuesday. Monday's meeting not being in it
    says nothing about Monday."""
    _render(home, "morning", MONDAY, [_at(MONDAY, 9, "Pod Steering", "e1")])
    tuesday = _render(home, "morning", TUESDAY, [_at(TUESDAY, 9, "Other sync", "e2")])
    assert "Pod Steering" in tuesday


def test_an_overnight_event_from_tomorrows_fetch_does_not_prove_today_was_fetched(home):
    """eod fetches tomorrow. An event that starts tonight and runs past
    midnight comes back in that window - it must not tombstone today."""
    _render(home, "morning", MONDAY, [_at(MONDAY, 9, "Pod Steering", "e1")])
    red_eye = {
        **_at(MONDAY, 23, "Red-eye", "flight"),
        "end": TUESDAY.replace(hour=2, minute=0).isoformat(),
    }
    _render(home, "eod", MONDAY.replace(hour=18), [red_eye])
    assert "Pod Steering" in _render(home, "morning", TUESDAY)


def test_the_evening_does_not_remember_tomorrows_meetings(home):
    """#155: a future meeting cancelled after the snapshot would otherwise
    become a permanent gap."""
    _render(home, "eod", MONDAY.replace(hour=18), [_at(TUESDAY, 9, "Tomorrow's sync", "e9")])
    assert _meetings(home) == []
    wednesday = _render(home, "morning", TUESDAY + timedelta(days=1))
    assert "Tomorrow's sync" not in wednesday


def test_the_week_ahead_remembers_nothing_from_next_week(home):
    sunday = MONDAY - timedelta(days=1)
    _render(home, "week-ahead", sunday, [_at(MONDAY, 9, "Monday sync", "e8")])
    assert _meetings(home) == []


def test_rendering_the_same_day_three_times_records_each_meeting_once(home):
    """#114: morning, prep and eod on one day appended the same rows each time."""
    meeting = _at(MONDAY, 9, "Pod Steering", "e1")
    for hour in (6, 12, 18):
        _render(home, "morning", MONDAY.replace(hour=hour), [meeting])
    assert len(_meetings(home)) == 1


def test_a_changed_snapshot_is_recorded_so_the_latest_one_wins(home):
    meeting = _at(MONDAY, 9, "Pod Steering", "e1")
    _render(home, "morning", MONDAY, [meeting])
    _render(home, "morning", MONDAY.replace(hour=18), [{**meeting, "notes_attached": True}])
    assert len(_meetings(home)) == 2
    assert "Pod Steering" not in _render(home, "morning", TUESDAY)


def test_a_meeting_that_comes_back_after_vanishing_is_a_row_again(home):
    """A tombstone is a snapshot like any other - a later sighting beats it."""
    meeting = _at(MONDAY, 9, "Pod Steering", "e1")
    other = _at(MONDAY, 11, "Other sync", "e2")
    _render(home, "morning", MONDAY, [meeting, other])
    _render(home, "morning", MONDAY.replace(hour=12), [other])
    _render(home, "morning", MONDAY.replace(hour=18), [meeting, other])
    assert "Pod Steering" in _render(home, "morning", TUESDAY)


def test_a_gap_past_the_horizon_is_given_up_on_once_then_dropped(home):
    """#125: past the slowest source's lag no note can still arrive. Say so
    once, as a decision for him, then stop repeating it forever."""
    _render(home, "morning", MONDAY, [_at(MONDAY, 9, "Pod Steering", "e1")])

    inside = _render(home, "morning", MONDAY + timedelta(days=10))
    assert any("Pod Steering" in line for line in _gap_lines(inside))

    past = _render(home, "morning", MONDAY + timedelta(days=12))
    assert not any("Pod Steering" in line for line in _gap_lines(past))
    assert "gave up" in past and "Pod Steering" in past, past

    later = _render(home, "morning", MONDAY + timedelta(days=13))
    assert "Pod Steering" not in later
