"""The runner half of #168: the plan asks for bodies and today's calendar,
`render ingest` is a repeatable sweep, and the EOD wrap carries the calls.

Synthetic throughout - the repo is public.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from tests.test_call_notes import DEMO_PREP, ENG_SYNC, STANDUP

from daydag import recipes, run
from daydag.config import Identities

PT = recipes.PACIFIC
EOD = datetime(2026, 9, 25, 16, 30, tzinfo=PT)
SWEEP = datetime(2026, 9, 25, 10, 0, tzinfo=PT)


@pytest.fixture
def identities(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "SLACK_USER_PRINCIPAL=UPRINCIPAL1\n"
        "EMAIL_PRINCIPAL=alex.rivera@example.com\n"
        "EMAIL_VP_DATA=sam.okafor@example.com\n"
        f"VAULT_ROOT={tmp_path / 'vault'}\n",
        encoding="utf-8",
    )
    (tmp_path / "vault" / "Weekly Notes").mkdir(parents=True)
    return Identities.from_file(env)


def _event(eid, summary, start, end, rsvp):
    return {
        "id": eid,
        "summary": summary,
        "start": f"2026-09-25T{start}:00-07:00",
        "end": f"2026-09-25T{end}:00-07:00",
        "attendees": [{"email": "alex.rivera@example.com"}, {"email": "sam.okafor@example.com"}],
        "response_status": rsvp,
        "permalink": f"https://calendar.example.com/{eid}",
    }


def _mail(mid, title, arrived, body):
    return {
        "id": mid,
        "subject": f"Notes: “{title}” Sep 25, 2026",
        "date": arrived,
        "plaintextBody": body,  # the connector's own key
        "viewUrl": f"https://mail.example.com/#all/{mid}",
    }


CALENDAR = [
    _event("e1", "Pod Standup", "08:45", "09:15", "needsAction"),
    _event("e2", "Eng Sync", "09:15", "09:45", "tentative"),
    _event("e3", "Demo Prep", "11:00", "11:45", "declined"),
]
GMAIL = [
    _mail("m1", "Pod Standup", "2026-09-25T16:42:42Z", STANDUP),
    _mail("m2", "Eng Sync", "2026-09-25T17:13:04Z", ENG_SYNC),
    _mail("m3", "Demo Prep", "2026-09-25T18:40:44Z", DEMO_PREP),
]


def _payloads(**over):
    base = {"calendar": CALENDAR, "slack": [], "gmail": GMAIL, "vault": ""}
    base.update(over)
    return base


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------


@pytest.mark.parametrize("loop", ["eod", "ingest"])
def test_the_gmail_step_asks_for_bodies_and_names_the_fields(identities, loop):
    """Search results are metadata. Without the body there is no attendance
    evidence and no next steps - the 9/25 EOD had to read eight by hand."""
    (step,) = [
        s for s in run.plan(loop, now=EOD, identities=identities).steps if s.source == "gmail"
    ]

    assert "PLAIN_TEXT" in step.how
    assert step.detail["format"] == "PLAIN_TEXT"
    assert set(step.detail["fields"]) >= {"id", "subject", "date", "body", "permalink"}


def test_eod_fetches_today_as_well_as_tomorrow(identities):
    """Today's RSVPs are what the calls section labels attendance from. EOD is
    a Friday, so the preview window is Monday, not Saturday (#170)."""
    days = [
        s.detail["day"]
        for s in run.plan("eod", now=EOD, identities=identities).steps
        if s.source == "calendar"
    ]
    assert days == ["2026-09-25", "2026-09-28"]


def test_ingest_fetches_yesterday_and_today(identities):
    """A Gemini note can land up to 18h after its meeting ends (ledger
    ARRIVAL_WINDOW), so a morning sweep meets yesterday's calls."""
    days = [
        s.detail["day"]
        for s in run.plan("ingest", now=SWEEP, identities=identities).steps
        if s.source == "calendar"
    ]
    assert days == ["2026-09-24", "2026-09-25"]


# --------------------------------------------------------------------------
# render ingest: the repeatable sweep
# --------------------------------------------------------------------------


def test_a_second_ingest_over_the_same_notes_reports_nothing_new(identities, tmp_path):
    log = tmp_path / "events.db"

    first = run.render("ingest", now=SWEEP, identities=identities, payloads=_payloads(), log=log)
    second = run.render("ingest", now=SWEEP, identities=identities, payloads=_payloads(), log=log)

    assert "3 new notes" in first, first
    assert "nothing new" in second and "3 already seen" in second, second


def test_ingest_labels_attendance_per_note(identities, tmp_path):
    text = run.render(
        "ingest", now=SWEEP, identities=identities, payloads=_payloads(), log=tmp_path / "e.db"
    )

    assert "Pod Standup (unconfirmed)" in text
    assert "Eng Sync (attended)" in text
    assert "Demo Prep (not attended)" in text


def test_the_sweep_does_not_fill_the_meeting_table_every_half_hour(identities, tmp_path):
    """Morning and EOD seed the ledger. A sweep every 30 minutes that also
    remembered its two calendar days would write ~800 rows a day for nothing."""
    from daydag.state import EventLog

    log = tmp_path / "events.db"
    run.render("ingest", now=SWEEP, identities=identities, payloads=_payloads(), log=log)

    assert EventLog.open(log).recorded(run.MEETING) == []


# --------------------------------------------------------------------------
# render eod: the calls section
# --------------------------------------------------------------------------


def test_the_eod_wrap_carries_todays_calls_ranked_and_labelled(identities):
    text = run.render("eod", now=EOD, identities=identities, payloads=_payloads())

    assert "today's calls (3)" in text
    assert "priorities from today's calls - assigned to you" in text
    run_line = next(line for line in text.splitlines() if "Run Data Checks" in line)
    assert "not sure you were in it" in run_line and "may not have heard" in run_line
    assert "(from Demo Prep - you weren't in it)" in text


def test_eod_reads_open_decisions_from_the_vault(identities, tmp_path):
    folder = tmp_path / "vault" / "DayDAG"
    run.render("eod", now=EOD, identities=identities, payloads=_payloads())  # creates the folder
    (folder / "Decisions.md").write_text(
        "# Pending decisions\n\n"
        "- **2026-09-25 · revenue spine grain - ISRC or movement?** · open\n",
        encoding="utf-8",
    )

    text = run.render("eod", now=EOD, identities=identities, payloads=_payloads())

    section = text.split("touches an open decision", 1)[1].split("\n\n", 1)[0]
    assert "Revenue Spine Grain" in section, text


def test_eod_without_gmail_says_so_in_one_line(identities):
    payloads = _payloads()
    del payloads["gmail"]

    text = run.render("eod", now=EOD, identities=identities, payloads=payloads)

    assert "couldn't check today's call notes" in text


def test_eods_tomorrow_line_is_still_tomorrows(identities):
    """Adding today's calendar must not leak today's meetings into the
    preview section - `_Payloads.calendar` filters by the window's day. EOD is
    a Friday, so the preview is Monday's (#170)."""
    tomorrow = _event("t1", "Planning Review", "09:00", "09:30", "accepted")
    tomorrow["start"] = "2026-09-28T09:00:00-07:00"
    tomorrow["end"] = "2026-09-28T09:30:00-07:00"

    text = run.render(
        "eod", now=EOD, identities=identities, payloads=_payloads(calendar=[*CALENDAR, tomorrow])
    )

    block = text.split("monday 9/28", 1)[1]
    assert "Planning Review" in block and "Pod Standup" not in block.split("\n\n")[0]
