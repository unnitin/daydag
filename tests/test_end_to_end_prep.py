"""End to end: one morning's work, through every module, with no network.

Each suite tests its own module honestly and in isolation. Nothing until now has
asked whether the modules FIT - whether a ledger row survives becoming a prep
ping, whether the source queries built for that ping are the ones the recipe
module would build, whether the reason a meeting qualified survives the trip.

That is where this project's real defects have lived. Every one of them passed
its own unit tests: the ledger dropped 61% of meetings because `_qualifies` was
correct in isolation and wrong about the world; the Gemini subject parser was
dead code that every unit test exercised directly and no production path called.

So this walks one weekday:

    calendar -> ledger      (rows, and the standup that is still a row)
    rows     -> prep        (which of them earns the one interrupt, and when)
    meeting  -> recipes     (the last 4 weeks, as literal queries)
    points   -> voice       (rendered, and clean in his own words)
    the day  -> State.md    (the meeting with no notes reaches the vault)

Real objects throughout - a real vault folder in a temp dir, a real identity
file. Only the network is absent, because none of these modules should need it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from daydag import prep
from daydag.config import Identities
from daydag.ledger import Ledger, Match
from daydag.state import StateFolder
from daydag.voice import Push, voice_violations

PT = timezone(timedelta(hours=-7))
MONDAY_9AM = datetime(2026, 9, 7, 9, 0, tzinfo=PT)

PRINCIPAL = "principal@example.com"
VP_DATA = "vp-data@example.com"
ENG = "eng@example.com"
SPONSOR = "sponsor@example.com"


def _event(event_id, summary, start, attendees):
    return {
        "id": event_id,
        "summary": summary,
        "start": start,
        "end": start + timedelta(hours=1),
        "attendees": list(attendees),
        "response_status": "needsAction",
        "kind": "meeting",
    }


def _identities(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "SLACK_USER_PRINCIPAL=UPRINCIPAL1\n"
        "SLACK_CH_POD=CPODCHANNEL\n"
        "ORG_EMAIL_DOMAIN=example.com\n"
        f"PREP_LEADERSHIP={SPONSOR}\n",
        encoding="utf-8",
    )
    return Identities.from_file(env)


def test_a_whole_morning_passes_through_every_module(tmp_path):
    """The integration path, asserted at each seam rather than only at the end."""
    identities = _identities(tmp_path)
    audience = prep.Audience.from_identities(identities)

    # -- calendar -> ledger ------------------------------------------------
    ledger = Ledger()
    ledger.seed_day(
        [
            _event("standup", "DE Standup", MONDAY_9AM, (PRINCIPAL, VP_DATA, ENG)),
            _event(
                "steering",
                "Pod Steering",
                MONDAY_9AM + timedelta(hours=3),
                (PRINCIPAL, VP_DATA, ENG),
            ),
            _event("11", "Nitin / VP-Data", MONDAY_9AM + timedelta(hours=5), (PRINCIPAL, VP_DATA)),
        ]
    )
    rows = ledger.open_rows()
    assert [row.event_id for row in rows] == ["standup", "steering", "11"], (
        "the ledger keeps the standup - it can still be a notes gap"
    )

    # -- rows -> prep: the standup is dropped HERE, not upstream -----------
    # The bias inverts across this seam on purpose. The ledger errs wide
    # because an unseen meeting is invisible forever; prep errs narrow because
    # a false positive spends the one interrupt the architecture allows.
    schedule = prep.Schedule()
    at_prep_time = MONDAY_9AM + timedelta(hours=3) - prep.PREP_LEAD
    due = schedule.due(rows, at_prep_time, audience)
    assert [(row.event_id, reason) for row, reason in due] == [
        ("steering", prep.Reason.STEERING)
    ], "only the steering meeting is 30 min out, and the standup never qualifies"

    # The 1:1 two hours later is not due yet, and firing once does not consume
    # it - a schedule that marks everything on the first pass loses the day.
    later = schedule.due(rows, MONDAY_9AM + timedelta(hours=5) - prep.PREP_LEAD, audience)
    assert [(row.event_id, reason) for row, reason in later] == [("11", prep.Reason.ONE_ON_ONE)]

    steering_row, reason = due[0]

    # -- meeting -> recipes: the File 2 recipe, run just in time -----------
    plan = prep.sources(
        steering_row, at_prep_time, identities=identities, channels=("${SLACK_CH_POD}",)
    )
    start, end = plan.window
    assert (end - start).days == prep.LOOKBACK_DAYS, "the ping reads ~4 weeks back"
    assert 'subject:"Pod Steering"' in plan.gemini, "notes are found by exact subject"
    assert "in:<#CPODCHANNEL>" in plan.slack[0], "the channel is scoped by id, not name"
    assert plan.prep_note.endswith("Meeting Prep/0907-0911.md")
    assert plan.degraded == (), "every source was asked for"

    # -- points -> voice ---------------------------------------------------
    quote = "please answer if we already merged all PRs"
    link = "https://example.com/archives/C01/p1757000000"
    ping = prep.build(
        steering_row,
        reason,
        [
            prep.point(
                "the 10k end-to-end run",
                "he owes an answer since thu and the demo is friday",
                quote=quote,
                permalink=link,
                source="slack",
            ),
            prep.point("the modeler handoff", "owner is out from wed, so decide today"),
            # No why-now, so it is dropped rather than padding the ping out.
            prep.point("sprint demo"),
        ],
    )
    assert len(ping.points) == 2, "a point with no reason to raise it today is noise"
    body = ping.render()
    assert ping.headline() == "Pod Steering in 30 - talking points in thread"
    assert quote in body and link in body, "evidence or silence, end to end"
    assert prep.UNSOURCED in body, "the point with no quote admits it"
    assert voice_violations(ping.authored_text()) == [], ping.authored_text()

    # -- the one interrupt, to the one destination -------------------------
    assert prep.may_interrupt(Push.PREP_PING) is True
    assert prep.may_interrupt(Push.MORNING_BRIEF) is False
    assert prep.recipient(identities) == "UPRINCIPAL1"

    # -- the day turns: the meeting with no notes reaches the vault ---------
    ledger.offer_note(
        Match(
            title="DE Standup",
            arrived=MONDAY_9AM + timedelta(hours=2),
            attendees=[PRINCIPAL, VP_DATA, ENG],
            source="gemini",
        )
    )
    ledger.close_day()
    gaps = ledger.notes_gaps(as_of=MONDAY_9AM + timedelta(days=1))
    assert "Pod Steering" in gaps, "prepping a meeting does not excuse it from the gap list"

    folder = StateFolder.create(tmp_path / "DayDAG")
    folder.write_state(notes_gaps=gaps)
    assert "Pod Steering" in folder.read_state()


def test_a_meeting_with_nothing_behind_it_still_gets_an_honest_ping(tmp_path):
    """Degrade, never stall - at the moment invention is most tempting.

    Four weeks of a brand-new meeting turn up nothing. Silence would look
    identical to a broken loop, and three plausible points would be a lie.
    """
    identities = _identities(tmp_path)
    audience = prep.Audience.from_identities(identities)
    ledger = Ledger()
    ledger.seed_day([_event("new", "Vendor Sync", MONDAY_9AM, (PRINCIPAL, "x@vendor.example"))])
    row = ledger.open_rows()[0]

    schedule = prep.Schedule()
    due = schedule.due([row], MONDAY_9AM - prep.PREP_LEAD, audience)
    assert [reason for _, reason in due] == [prep.Reason.EXTERNAL]

    ping = prep.build(row, prep.Reason.EXTERNAL, [])
    rendered = ping.render()
    assert rendered.strip(), "silence is not a degrade, it is a disappearance"
    assert prep.NO_MATERIAL in rendered
    assert voice_violations(rendered) == [], rendered


def test_a_ping_that_missed_its_window_is_dropped_rather_than_arriving_late(tmp_path):
    """The interrupt is time-boxed; a late one spends the budget for nothing."""
    identities = _identities(tmp_path)
    audience = prep.Audience.from_identities(identities)
    ledger = Ledger()
    ledger.seed_day([_event("11", "Nitin / VP-Data", MONDAY_9AM, (PRINCIPAL, VP_DATA))])
    rows = ledger.open_rows()

    schedule = prep.Schedule()
    assert schedule.due(rows, MONDAY_9AM + timedelta(minutes=1), audience) == []
    # And the missed instance is not resurrected on the next sweep.
    assert schedule.due(rows, MONDAY_9AM + timedelta(minutes=30), audience) == []
