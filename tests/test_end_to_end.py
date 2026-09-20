"""End to end: the seams between modules, walked with real objects and no network.

Every unit suite tests its own module honestly and in isolation - and that is
exactly why none of them can catch the defect class this project actually has.
Every real defect here passed its own unit tests and was wrong about a
NEIGHBOUR: the ledger dropped 61% of meetings because `_qualifies` was correct
in isolation and wrong about the world; `update_state` filtered `chase` and not
`watch` because each filter was right on its own; the Gemini subject parser was
dead code that every unit test exercised directly and no production path
called; notes were offered to the ledger BEFORE today's rows were seeded, so a
real Friday reported 9 meetings as having no notes while 8 of those notes were
listed by name directly above. All seam defects. So this file walks the seams,
one section per walk:

    the morning brief   calendar -> ledger -> notes gap -> a brief line;
                        mirror -> pulse -> shipping block -> the same brief;
                        event log -> State.md (private items withheld) -> owed to you;
                        weekly note -> the one OPEN red item, cited by path and line
    the eod wrap        the ledger's notes gap -> the prep-gap admission for tomorrow;
                        pulse -> the moved block; the TICKED red item -> closed today
    the week-ahead      real events -> a real Ledger -> prep.prep_worthy -> monday_preps,
                        with the meeting's OWN week named even though the push runs
                        from the week before; the closing note -> carrying in
    prep                rows -> which one earns the one interrupt, and when;
                        meeting -> the recipes, as literal queries; points -> voice;
                        the day -> State.md (the meeting with no notes reaches the vault)
    the pulse walk      Watchlist.md -> clone -> fetch, against the clock -> shipping
                        block -> State.md. Both failures it asserts are composition
                        failures: an item with no permalink and a stale mirror with
                        no date each look fine alone, and read as a lie only once
                        they are inside a brief someone is deciding from
    the run log         the row is written to the store that holds the chase list,
                        and to nothing under the vault. ARCHITECTURE keeps the two
                        apart for reasons that only show up with both in play: the
                        `DayDAG/` folder is hand-editable markdown synced by iCloud,
                        the event log is SQLite outside it. Get the seam wrong and
                        the run log either lands in a file five loops are already
                        fighting over, or carries a connector's error text - urls,
                        tokens in query strings - into plaintext on every device
    memory              two runs on two days, and what the second remembers of the
                        first. The ledger seeds today's meetings so that tomorrow
                        can report the ones that produced no notes; with an
                        in-memory ledger per run there is no tomorrow, and the
                        section is omitted rather than labelled, so it fails silently

    and every push -> the house voice, with every claim sourced.

One world for all of it: a real git repo with two landings (`landings`), a real
SQLite event log under tmp_path (`log`), a real vault folder (`folder`), one
`.env` (`identities`), and a hand-wound clock (`clock`) that starts at the
pulse's Friday fetch and is wound forward through Sunday's week-ahead, Monday's
meetings and wrap, and Tuesday's brief. Only the connector reads are faked
(`support.FakeSources`), because they are the only thing here that would need a
network - and the runner takes payloads rather than fetching, so the memory
tests fake nothing at all.
"""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from daydag import loops, observe, prep, push, run
from daydag.eventlog import EventLog
from daydag.ledger import Ledger, Match
from daydag.observe import FAILURE_LIMIT, RUN, UNCHECKED, RunLog, RunRow
from daydag.pulse import Item, Mirror, MirrorStore, Pulse, WatchedRepo, read_watchlist
from daydag.voice import Push, voice_violations
from support import PRINCIPAL, PT, Clock, FakeSources, calendar_event, rev

# --------------------------------------------------------------------------
# the world: one story from Friday to Tuesday
# --------------------------------------------------------------------------

#: The pulse's clean fetch, and where the clock starts.
FRIDAY = datetime(2026, 9, 4, 6, 40, tzinfo=UTC)
#: The week-ahead's own slot.
SUNDAY = datetime(2026, 9, 6, 17, 30, tzinfo=PT)
#: 6:40am PT: the pre-brief slot, which is the run whose absence gets noticed.
MONDAY = datetime(2026, 9, 7, 6, 40, tzinfo=PT)
MONDAY_9AM = MONDAY.replace(hour=9, minute=0)
#: The wrap's own time. "Tomorrow" is Tuesday - a real 1:1.
MONDAY_WRAP = MONDAY.replace(hour=16, minute=30)
TUESDAY = MONDAY + timedelta(days=1)
#: The brief that reports Monday runs Tuesday at 6:45am - which is the only time
#: Monday's notes gap can exist, since a note has until end of day to land.
TUESDAY_BRIEF = TUESDAY.replace(minute=45)
TUESDAY_1ON1 = TUESDAY.replace(hour=15, minute=0)

PRINCIPAL_EMAIL = "principal@example.com"
VP_DATA = "vp-data@example.com"
ENG = "eng@example.com"
SPONSOR = "sponsor@example.com"

#: The two landings in `landings`, oldest first.
LANDINGS = ("CDI-596 cutover rehearsal", "CDI-600 neo4j migration")

NOTE_0831 = "Create Music Group/Weekly Notes/0831-0904.md"
NOTE_0907 = "Create Music Group/Weekly Notes/0907-0911.md"

WEEKLY_NOTE = """# Week of Sep 7-11
triage: 🔴 high · 🟡 medium · 🟢 low

## Priorities
- [ ] 🔴 note to Sponsor on data platform access *(mine)*
- [x] 🔴 pod update *(mine)*
"""

CLOSING_NOTE_TEXT = "## 🔴 High\n- [ ] note to sponsor on data platform access\n- [x] pod update\n"


@pytest.fixture
def clock():
    """The world's clock, starting where its story does. Each test winds it on."""
    return Clock(FRIDAY)


@pytest.fixture
def identities(make_identities):
    """One `.env` for the whole world, over conftest's default: the principal
    under the org's domain (or prep reads him as the outside party), the pod
    channel a prep plan scopes to, and the sponsor as leadership."""
    return make_identities(
        EMAIL_PRINCIPAL=PRINCIPAL_EMAIL,
        SLACK_CH_POD="CPODCHANNEL",
        ORG_EMAIL_DOMAIN="example.com",
        PREP_LEADERSHIP=SPONSOR,
    )


@pytest.fixture
def log_path(tmp_path):
    """Where the event log lives: under tmp_path, outside the vault."""
    return tmp_path / "events.db"


@pytest.fixture
def log(log_path):
    """A real SQLite event log on disk - the one store the chase list, the
    run log and the mirror cursors share."""
    return EventLog.open(log_path)


@pytest.fixture
def landings(make_origin):
    """A real repo with two landings, so the pulse has something true to read."""
    return make_origin("svc", messages=("baseline", *LANDINGS))


@pytest.fixture
def mirror(landings, git_env):
    """A healthy mirror over `landings`, cursor at the baseline: both landings are news."""
    return Mirror.attach(
        landings, cursor=rev(landings, git_env, "rev-list", "--max-parents=0", "HEAD")
    )


@pytest.fixture
def pulse(mirror):
    return Pulse(mirrors=[mirror])


@pytest.fixture
def stale_mirror(tmp_path):
    """A mirror whose repo is gone and whose last fetch failed."""
    broken = Mirror.attach(tmp_path / "not-a-repo.git", cursor="HEAD")
    broken.mark_fetch_failed()
    return broken


# --------------------------------------------------------------------------
# the morning brief: calendar -> ledger -> notes gap -> a brief line
# --------------------------------------------------------------------------


def test_a_whole_morning_arrives_as_one_assembled_brief(folder, log, pulse, identities):
    """The integration path, asserted at each seam rather than only at the end."""
    # -- calendar -> ledger ------------------------------------------------
    ledger = Ledger()
    ledger.seed_day(
        [
            calendar_event("standup", "DE Standup", MONDAY_9AM),
            calendar_event("steering", "Pod Steering", MONDAY_9AM + timedelta(hours=3)),
            calendar_event(
                "declined", "Optional Sync", MONDAY_9AM + timedelta(hours=5), response="declined"
            ),
        ]
    )
    # Two rows, not three: declined is the only response that means it did not
    # happen. Requiring `accepted` here would have dropped both real meetings.
    assert [row.event_id for row in ledger.open_rows()] == ["standup", "steering"]

    # One meeting produces notes; the other does not. That asymmetry is the
    # whole point - the gap is the output, not the note.
    attached = ledger.offer_note(
        Match(
            title="DE Standup",
            arrived=MONDAY_9AM + timedelta(hours=2),
            attendees=["nitin", "vp-data"],
            source="gemini",
        )
    )
    assert attached is not None and attached.event_id == "standup"

    ledger.close_day()
    gaps = ledger.notes_gaps(as_of=MONDAY_9AM + timedelta(days=1))
    assert gaps == ["Pod Steering"], "the meeting with no notes must be the one surfaced"

    # -- mirror -> pulse ---------------------------------------------------
    assert [item.title for item in pulse.items()] == list(LANDINGS), (
        "landings arrive oldest-first and include squashed (single-parent) commits"
    )

    shipping = pulse.render()
    assert shipping.strip(), "two landings must produce a shipping block"
    for banned in ("commits", "lines changed", "contributions"):
        assert banned not in shipping.lower(), "the block must not read as a productivity metric"

    # -- repo evidence marks a loop, and never closes it -------------------
    pulse.observe_slack("can you pick up CDI-596 this week?")
    pulse.observe_pr(title="CDI-596 cutover rehearsal", number=412, state="merged")
    loop = {"key": "CDI-596", "owner": "VP-Data", "ask": "cutover rehearsal", "status": "open"}
    after = pulse.apply_evidence(loop)
    assert after["evidence_of_movement"] is True
    assert after["status"] == "open", "merged is not the same as what was asked for"

    # -- event log -> State.md, private items withheld ---------------------
    log.record(
        "loop_opened",
        sensitivity="normal",
        key="CDI-596",
        owner="VP-Data",
        ask="cutover rehearsal",
        day=1,
    )
    log.record(
        "carry_forward",
        sensitivity="private",
        key="perf-conversation",
        owner="VP-Data",
        ask="perf-conversation follow-up",
        day=1,
    )
    chase = log.chase_items()
    assert any(item.get("sensitivity") == "private" for item in chase), "fixture must be meaningful"

    folder.update_state(
        chase=chase,
        watch=[{"what": "nightly ingest", "sensitivity": "private"}, {"what": "R1.5 staging"}],
        notes_gaps=gaps,
    )
    written = folder.read_state()
    assert "cutover rehearsal" in written
    assert "perf-conversation" not in written, "a private chase item reached the vault"
    assert "nightly ingest" not in written, "a private watch item reached the vault"

    # -- a decision survives being answered by hand ------------------------
    folder.add_decision("close the CDI-596 loop? · status: open")
    decisions = folder.decisions_path
    decisions.write_text(decisions.read_text().replace("status: open", "status: no - not yet"))
    before = decisions.read_bytes()
    folder.add_decision("draft the nudge? · status: open")
    assert decisions.read_bytes().startswith(before), "a hand edit is an event and wins"

    # -- and the assembler turns all of it into one message ----------------
    # Every input below is the object an earlier stanza actually produced: the
    # same ledger, the same pulse, the same State.md on disk.
    connectors = FakeSources(
        events=[calendar_event("1on1", "VP-Data 1:1", TUESDAY_BRIEF + timedelta(hours=3))],
        note=WEEKLY_NOTE,
    )
    assembled = loops.morning(
        now=TUESDAY_BRIEF,
        sources=connectors,
        identities=identities,
        state=folder,
        ledger=ledger,
        pulse=pulse,
    )
    text = assembled.render()

    assert len(connectors.calendar_windows) == 1, "the calendar must be asked one day at a time"
    assert assembled.unreachable == (), f"a healthy run reported a dead source:\n{text}"

    # the ledger's gap survived becoming a brief line
    assert "Pod Steering" in text and "no note found" in text
    # the weekly note's one OPEN red item, cited by path and line
    assert "note to Sponsor on data platform access" in text
    assert "Weekly Notes/0907-0911.md#L5" in text
    assert "pod update" not in text, "a ticked item is closed and must not come back"
    # the chase loop reached him, and the private carry-forward did not
    assert "cutover rehearsal" in text
    assert "perf-conversation" not in text, "a private item reached the brief"
    # the pulse's landings, with the links the pulse itself built
    assert "shipping" in text
    assert "CDI-600 neo4j migration" in text

    # A real chase line carries no permalink: the event log records owner and
    # ask and nothing to click. The brief says so rather than asserting it -
    # this is the seam, and the fix belongs in what the log records.
    assert "couldn't source" in text
    assert push.unsourced_claims(text) == [], "a claim shipped with no evidence"
    assert voice_violations(text) == [], f"the assembled brief broke the voice rules:\n{text}"


def test_a_quiet_morning_assembles_a_brief_with_no_shipping_block(landings, git_env, identities):
    """SPEC 3.7 rule 3, across the seam: silence is information.

    The pulse obeying the rule is only half of it - a brief that prints an empty
    `shipping` heading has broken it while the pulse was behaving. A line saying
    nothing happened trains the reader to skim, which is how the whole brief
    stops being read.
    """
    pulse = Pulse(
        mirrors=[Mirror.attach(landings, cursor=rev(landings, git_env, "rev-parse", "HEAD"))]
    )
    assert pulse.items() == []
    assert pulse.render().strip() == "", "a quiet day must render nothing at all"

    text = loops.morning(
        now=TUESDAY_BRIEF,
        sources=FakeSources(
            events=[calendar_event("1on1", "VP-Data 1:1", TUESDAY_BRIEF + timedelta(hours=3))],
            note=WEEKLY_NOTE,
        ),
        identities=identities,
        pulse=pulse,
    ).render()

    assert "VP-Data 1:1" in text, "the brief still ships on a quiet day"
    assert "shipping" not in text
    assert "no updates" not in text


def test_a_dead_connector_and_a_dead_mirror_both_degrade_into_the_same_brief(
    mirror, stale_mirror, identities
):
    """Guardrail 6 across two seams at once.

    The two failures reach the brief by different routes - the mirror's through
    the pulse's own block, the connector's through the assembler - and both have
    to arrive as one line each without taking the brief down.
    """
    assembled = loops.morning(
        now=TUESDAY_BRIEF,
        sources=FakeSources(
            events=[calendar_event("1on1", "VP-Data 1:1", TUESDAY_BRIEF + timedelta(hours=3))],
            note=WEEKLY_NOTE,
            broken=("slack",),
        ),
        identities=identities,
        pulse=Pulse(mirrors=[mirror, stale_mirror]),
    )
    text = assembled.render()

    assert assembled.unreachable == ("slack",)
    assert "couldn't check slack" in text, "the dead connector is named"
    assert "not-a-repo" in text, "the stale mirror is named rather than dropped"
    assert "as of" in text.lower(), "a stale mirror says so instead of asserting freshness"
    assert "CDI-596 cutover rehearsal" in text, "the healthy mirror still reports"
    assert "VP-Data 1:1" in text, "the healthy connector still reports"
    assert push.unsourced_claims(text) == []
    assert voice_violations(text) == []


# --------------------------------------------------------------------------
# the eod wrap: the ledger's gap -> tomorrow's admission; the ticked item -> closed
# --------------------------------------------------------------------------


def test_a_whole_evening_arrives_as_one_assembled_wrap(pulse):
    """The integration path, asserted at each seam rather than only at the end."""
    # -- a real repo with two landings, so the pulse has something true ----
    assert [item.title for item in pulse.items()] == list(LANDINGS)

    # -- a real ledger row: last week's instance of tomorrow's meeting -----
    ledger = Ledger()
    last_week = MONDAY_WRAP - timedelta(days=6)
    ledger.seed_day([calendar_event("1on1-lastweek", "VP-Data 1:1", last_week, link=False)])
    ledger.close_day()
    assert ledger.notes_gaps(as_of=MONDAY_WRAP) == ["VP-Data 1:1"], (
        "the fixture must be meaningful: last week's instance really has no note"
    )

    # -- and the assembler turns all of it into one message ----------------
    connectors = FakeSources(
        events=[calendar_event("1on1", "VP-Data 1:1", TUESDAY_1ON1)],
        notes={NOTE_0907: WEEKLY_NOTE},
    )
    wrap = loops.eod(
        now=MONDAY_WRAP,
        sources=connectors,
        ledger=ledger,
        pulse=pulse,
    )
    text = wrap.render()

    assert len(connectors.calendar_windows) == 1, "the calendar must be asked one day at a time"
    assert wrap.unreachable == (), f"a healthy run reported a dead source:\n{text}"

    # the weekly note's one CLOSED red item, cited by path and line
    assert "pod update" in text
    assert "Weekly Notes/0907-0911.md#L6" in text
    assert "note to Sponsor" not in text, "an open item is not closed and must not appear here"

    # the pulse's landings, with the links the pulse itself built
    assert "moved (2)" in text
    assert "CDI-600 neo4j migration" in text

    # tomorrow's meeting, and the real ledger's real notes gap for it
    assert "3:00 VP-Data 1:1" in text
    assert "no note found" in text

    assert push.unsourced_claims(text) == [], "a claim shipped with no evidence"
    assert voice_violations(text) == [], f"the assembled wrap broke the voice rules:\n{text}"


def test_a_late_note_closes_the_gap_before_the_wrap_ever_sees_it():
    """The ledger's own late-arrival path (ARCHITECTURE's meeting ledger): a
    note that lands for last week's instance means the wrap has nothing to
    flag for tomorrow's, without the wrap doing anything gap-specific itself.
    """
    ledger = Ledger()
    last_week = MONDAY_WRAP - timedelta(days=6)
    ledger.seed_day([calendar_event("1on1-lastweek", "VP-Data 1:1", last_week, link=False)])
    attached = ledger.offer_note(
        Match(
            title="VP-Data 1:1",
            arrived=last_week + timedelta(hours=2),
            attendees=["nitin", "vp-data"],
            source="gemini",
        )
    )
    assert attached is not None

    connectors = FakeSources(events=[calendar_event("1on1", "VP-Data 1:1", TUESDAY_1ON1)])
    text = loops.eod(now=MONDAY_WRAP, sources=connectors, ledger=ledger).render()

    assert "3:00 VP-Data 1:1" in text
    assert "no note found" not in text


def test_a_dead_connector_and_a_dead_mirror_both_degrade_into_the_same_wrap(mirror, stale_mirror):
    """Guardrail 6 across two seams at once - mirrors the brief's own
    end-to-end coverage of the same property."""
    wrap = loops.eod(
        now=MONDAY_WRAP,
        sources=FakeSources(broken=("calendar",)),
        pulse=Pulse(mirrors=[mirror, stale_mirror]),
    )
    text = wrap.render()

    assert wrap.unreachable == ("calendar",)
    assert "couldn't check calendar" in text
    assert "not-a-repo" in text, "the stale mirror is named rather than dropped"
    assert "as of" in text.lower()
    assert "CDI-596 cutover rehearsal" in text, "the healthy mirror still reports"
    assert push.unsourced_claims(text) == []
    assert voice_violations(text) == []


# --------------------------------------------------------------------------
# the week-ahead: events -> ledger -> prep_worthy -> monday_preps (issue #21)
# --------------------------------------------------------------------------


def test_a_sunday_evening_arrives_as_one_assembled_week_ahead(folder, log, pulse, identities):
    """The integration path, asserted at each seam rather than only at the end."""
    # -- a real weekly note -> the closing week's open red item -------------
    # (read via push.red_items - never re-derived, contract 1)

    # -- a real mirror -> pulse ---------------------------------------------
    assert [item.title for item in pulse.items()] == list(LANDINGS)

    # -- a real event log -> State.md, private items withheld ---------------
    log.record(
        "loop_opened",
        sensitivity="normal",
        key="CDI-596",
        owner="VP-Data",
        ask="cutover rehearsal",
        day=1,
    )
    log.record(
        "carry_forward",
        sensitivity="private",
        key="perf-conversation",
        owner="VP-Data",
        ask="perf-conversation follow-up",
        day=1,
    )
    folder.update_state(
        chase=log.chase_items(),
        watch=[{"what": "10k e2e run"}],
    )
    assert "perf-conversation" not in folder.read_state(), "fixture must be meaningful"

    # -- real calendar events -> a real ledger -> prep's own qualification --
    connectors = FakeSources(
        events=[
            calendar_event("standup", "DE Standup", MONDAY_9AM),
            calendar_event("steering", "Pod Steering", MONDAY_9AM + timedelta(hours=1)),
            calendar_event(
                "sync",
                "Ivan/Ruwen Sync",
                TUESDAY.replace(hour=10, minute=0),
                attendees=("nitin", "ruwen"),
            ),
            calendar_event(
                "ooo",
                "Ruwen OOO",
                TUESDAY.replace(hour=0, minute=0),
                minutes=24 * 60,
                attendees=("ruwen",),
                kind="ooo",
            ),
        ],
        notes={NOTE_0831: CLOSING_NOTE_TEXT},
        # NOTE_0907 is deliberately absent - the missing-plan case is covered
        # in its own end-to-end variant below.
    )

    pushed = loops.week_ahead(
        now=SUNDAY,
        sources=connectors,
        identities=identities,
        state=folder,
        pulse=pulse,
    )
    text = pushed.render()

    assert len(connectors.calendar_windows) == 7, "the next seven days, one request per day"

    # the missing plan leads
    assert text.startswith("week ahead")
    assert "no week-ahead plan for 0907-0911" in text

    # the closing week's open red item, cited by path and line
    assert "note to sponsor on data platform access" in text
    assert f"{NOTE_0831}#L2" in text
    assert "pod update" not in text, "a ticked item is closed and must not come back"

    # the chase loop reached the push, and the private carry-forward did not
    assert "cutover rehearsal" in text
    assert "perf-conversation" not in text

    # the standup never made it to Monday's prep queue; the steering did
    assert "DE Standup" in text, "still shows up as a real Monday meeting"
    preps = {p.meeting: p for p in pushed.monday_preps}
    assert "DE Standup" not in preps
    assert "Pod Steering" in preps
    # the regression this loop exists to catch: prep's own week, not Sunday's
    assert preps["Pod Steering"].plan.prep_note.endswith("Meeting Prep/0907-0911.md")

    # the travelling attendee is flagged on the meeting sharing his calendar
    assert "Ruwen OOO" in text
    assert "Ruwen OOO" not in [p.meeting for p in pushed.monday_preps]  # not itself a meeting

    # the pulse's landing, with the link the pulse itself built
    assert "shipping" in text
    assert "CDI-596 cutover rehearsal" in text

    assert push.unsourced_claims(text) == [], f"a claim shipped with no evidence:\n{text}"
    assert voice_violations(text) == [], f"broke the house voice:\n{voice_violations(text)}\n{text}"


def test_a_dead_connector_and_a_stale_mirror_both_degrade_into_the_same_push(
    mirror, stale_mirror, identities
):
    """Guardrail 6 across two seams at once, the same property brief asserts."""

    class DeadClosingNote(FakeSources):
        """One vault read dead, the others answering: `broken=` cannot scope a
        failure to a single path, and the point here is that the OTHER note
        still reads as missing rather than unreachable."""

        def vault_note(self, path):
            if path == NOTE_0831:
                raise RuntimeError("vault read timed out")
            return super().vault_note(path)

    connectors = DeadClosingNote(events=[calendar_event("steering", "Pod Steering", MONDAY_9AM)])

    pushed = loops.week_ahead(
        now=SUNDAY,
        sources=connectors,
        identities=identities,
        pulse=Pulse(mirrors=[mirror, stale_mirror]),
    )
    text = pushed.render()

    assert "the closing week's note" in pushed.unreachable
    assert "couldn't check the closing week's note" in text
    assert "not-a-repo" in text, "the stale mirror is named rather than dropped"
    assert "as of" in text.lower()
    assert "CDI-600 neo4j migration" in text, "the healthy mirror still reports"
    # the healthy Monday meeting still reports and still queues for prep -
    # a dead vault read on a DIFFERENT note must not take it down
    assert "Pod Steering" in text
    assert any(p.meeting == "Pod Steering" for p in pushed.monday_preps)
    assert push.unsourced_claims(text) == []
    assert voice_violations(text) == []


# --------------------------------------------------------------------------
# prep: rows -> the one interrupt -> recipes -> voice -> State.md
# --------------------------------------------------------------------------


def test_a_whole_morning_passes_through_every_module(folder, identities):
    """The integration path, asserted at each seam rather than only at the end."""
    audience = prep.Audience.from_identities(identities)

    # -- calendar -> ledger ------------------------------------------------
    ledger = Ledger()
    ledger.seed_day(
        [
            calendar_event(
                "standup", "DE Standup", MONDAY_9AM, attendees=(PRINCIPAL_EMAIL, VP_DATA, ENG)
            ),
            calendar_event(
                "steering",
                "Pod Steering",
                MONDAY_9AM + timedelta(hours=3),
                attendees=(PRINCIPAL_EMAIL, VP_DATA, ENG),
            ),
            calendar_event(
                "11",
                "Nitin / VP-Data",
                MONDAY_9AM + timedelta(hours=5),
                attendees=(PRINCIPAL_EMAIL, VP_DATA),
            ),
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
    assert prep.recipient(identities) == PRINCIPAL

    # -- the day turns: the meeting with no notes reaches the vault ---------
    ledger.offer_note(
        Match(
            title="DE Standup",
            arrived=MONDAY_9AM + timedelta(hours=2),
            attendees=[PRINCIPAL_EMAIL, VP_DATA, ENG],
            source="gemini",
        )
    )
    ledger.close_day()
    gaps = ledger.notes_gaps(as_of=MONDAY_9AM + timedelta(days=1))
    assert "Pod Steering" in gaps, "prepping a meeting does not excuse it from the gap list"

    folder.update_state(notes_gaps=gaps)
    assert "Pod Steering" in folder.read_state()


def test_a_meeting_with_nothing_behind_it_still_gets_an_honest_ping(identities):
    """Degrade, never stall - at the moment invention is most tempting.

    Four weeks of a brand-new meeting turn up nothing. Silence would look
    identical to a broken loop, and three plausible points would be a lie.
    """
    audience = prep.Audience.from_identities(identities)
    ledger = Ledger()
    ledger.seed_day(
        [
            calendar_event(
                "new", "Vendor Sync", MONDAY_9AM, attendees=(PRINCIPAL_EMAIL, "x@vendor.example")
            )
        ]
    )
    row = ledger.open_rows()[0]

    schedule = prep.Schedule()
    due = schedule.due([row], MONDAY_9AM - prep.PREP_LEAD, audience)
    assert [reason for _, reason in due] == [prep.Reason.EXTERNAL]

    ping = prep.build(row, prep.Reason.EXTERNAL, [])
    rendered = ping.render()
    assert rendered.strip(), "silence is not a degrade, it is a disappearance"
    assert prep.NO_MATERIAL in rendered
    assert voice_violations(rendered) == [], rendered


def test_a_ping_that_missed_its_window_is_dropped_rather_than_arriving_late(identities):
    """The interrupt is time-boxed; a late one spends the budget for nothing."""
    audience = prep.Audience.from_identities(identities)
    ledger = Ledger()
    ledger.seed_day(
        [calendar_event("11", "Nitin / VP-Data", MONDAY_9AM, attendees=(PRINCIPAL_EMAIL, VP_DATA))]
    )
    rows = ledger.open_rows()

    schedule = prep.Schedule()
    assert schedule.due(rows, MONDAY_9AM + timedelta(minutes=1), audience) == []
    # And the missed instance is not resurrected on the next sweep.
    assert schedule.due(rows, MONDAY_9AM + timedelta(minutes=30), audience) == []


# --------------------------------------------------------------------------
# the pulse walk: Watchlist.md -> clone -> fetch -> shipping block -> State.md
# --------------------------------------------------------------------------

#: An item line, split into what it claims and what backs the claim.
ITEM_LINE = re.compile(r"^- (?P<claim>.+) \((?P<source>[^()]*)\)$")


@pytest.fixture
def walk(tmp_path, folder, log, clock, make_origin):
    """The repo half of a morning, wired: `Watchlist.md` in the vault folder,
    the mirror store on the event log and the clock, the origins on tmpfs.

    `url_for` is the seam that keeps this offline - in production it resolves to
    github.com, here to a path on tmpfs. A repo with no origin resolves to a
    path that does not exist, which is exactly what an unreachable repo looks
    like to git.
    """
    origins: dict[str, object] = {}

    def url_for(repo):
        return str(origins.get(repo.slug, tmp_path / "origins" / f"{repo.name}-absent"))

    class Walk:
        def __init__(self):
            self.folder = folder
            self.log = log
            self.origins = origins
            self.store = MirrorStore(tmp_path / "mirrors", url_for=url_for, log=log, clock=clock)

        def add_repo(self, name, messages=("base",)):
            origins[f"ExampleOrg/{name}"] = make_origin(name, messages)
            return WatchedRepo("ExampleOrg", name)

        def watchlist(self, *names):
            body = "# Watchlist\n\n## repos\n" + "".join(f"- ExampleOrg/{n}\n" for n in names)
            folder.watchlist_path.write_text(body, encoding="utf-8")
            return read_watchlist(folder.watchlist_path)

        def run(self, watchlist, cursors=None):
            """One pulse pass: sync, render, hand back the advanced cursors.

            The cursors are read *after* rendering on purpose - that is the
            real ordering, and it is the property `test_pulse` pins separately:
            a cursor advances only once the caller has the result.
            """
            report = self.store.sync(watchlist, cursors=cursors or {})
            block = Pulse.from_sync(report).render()
            return block, {m.label: m.cursor for m in report.mirrors}

        def ship(self, block):
            """Put the block where a human reads it, the way a loop would."""
            watch = [{"what": line.removeprefix("- ")} for line in block.splitlines()]
            folder.update_state(watch=watch)
            return folder.read_state()

    return Walk()


def test_the_whole_morning_walks_from_watchlist_to_state_file(walk, add_commit):
    """Watchlist -> clone -> fetch -> pulse -> State.md, with no step mocked."""
    walk.add_repo("service-a")
    walk.add_repo("service-b")
    watchlist = walk.watchlist("service-a", "service-b")

    first, cursors = walk.run(watchlist)
    assert first == "", "first sight reported its own history as news"

    add_commit(walk.origins["ExampleOrg/service-a"], "Merge PR #412")
    block, _ = walk.run(watchlist, cursors)

    assert "Merge PR #412" in block
    assert "Merge PR #412" in walk.ship(block)


@pytest.mark.guardrail
def test_a_broken_mirror_degrades_and_the_rest_still_ships(walk, add_commit, clock):
    """Guardrail 6, and #60: the degrade line is *dated*.

    Two repos, one whose origin disappears over the weekend. The brief must ship
    the healthy repo's landing, name the broken one, and say when the broken one
    was last read cleanly - twenty minutes stale and four days stale mean
    different things about whether to trust the rest of the block, and an
    undated "as of last run" cannot tell them apart.
    """
    walk.add_repo("service-a")
    walk.add_repo("service-b")
    watchlist = walk.watchlist("service-a", "service-b")
    walk.run(watchlist)  # first sight clones
    _, cursors = walk.run(watchlist)  # a clean fetch on the Friday

    shutil.rmtree(walk.origins["ExampleOrg/service-b"])  # origin gone by Monday
    add_commit(walk.origins["ExampleOrg/service-a"], "Merge PR #413")

    clock.set(MONDAY)
    block, _ = walk.run(watchlist, cursors)

    assert "Merge PR #413" in block, "one dead source suppressed a healthy one"
    assert "ExampleOrg/service-b" in block, "the failure is unattributed"
    assert "could not fetch" in block
    assert "2026-09-04 06:40 UTC" in block, "staleness is admitted but not dated"
    assert "as of last run" not in block, "the undated wording survived"
    assert "no updates" not in block
    assert "2026-09-04 06:40 UTC" in walk.ship(block), "the date was lost on the way to the vault"


@pytest.mark.guardrail
def test_every_claim_that_reaches_the_morning_carries_a_source(walk, add_commit):
    """Invariant 3 across the walk, not just at the dataclass.

    Two halves, and the second is the one #59 is about. Every item line the walk
    produces must have something inside its brackets - `- title ()` reads as a
    formatting glitch rather than as a claim nothing can back - and an item that
    has no source cannot be constructed in the first place, so no code path can
    put one there.
    """
    walk.add_repo("service-a")
    watchlist = walk.watchlist("service-a")
    _, cursors = walk.run(watchlist)
    add_commit(walk.origins["ExampleOrg/service-a"], "Merge PR #412")

    block, _ = walk.run(watchlist, cursors)

    claims = [ITEM_LINE.match(line) for line in block.splitlines()]
    assert any(claims), "the walk produced no item line to check"
    for line, claim in zip(block.splitlines(), claims, strict=True):
        assert claim is not None, f"a line that is not a sourced claim: {line!r}"
        assert claim["source"].strip(), f"unsourced claim reached the brief: {line!r}"
    assert "()" not in walk.ship(block)

    with pytest.raises(ValueError, match="permalink"):
        Item(title="someone said it landed", permalink="")


# --------------------------------------------------------------------------
# the run log: a row in the store that holds the chase list, nothing in the vault
# --------------------------------------------------------------------------

#: Two of the real `smoke` checks, so the walk is fed genuine rows rather than
#: hand-written dicts. Calendar is probed and answers; Jira is given no probe,
#: which is the case that must never come out the far end as "reached".
RUNLOG_CHECKS = tuple(check for check in observe.CHECKS if check.name in {"calendar", "jira"})


def _morning_smoke():
    """One pre-flight pass: the calendar answers, nothing probed Jira."""
    return observe.run({"calendar": lambda: {"events": []}}, checks=RUNLOG_CHECKS).as_rows()


def test_a_morning_run_leaves_its_row_in_the_log_and_nothing_in_the_vault(
    folder, log, pulse, clock
):
    """The seam, asserted on both sides of it.

    One event log holds the chase items that become `State.md` AND the run row
    that must not. One vault folder gets rewritten from that same log and has to
    come out carrying nothing of the run.
    """
    clock.set(TUESDAY)
    runlog = RunLog(log, clock=clock)

    # -- the morning, inside the run --------------------------------------
    with runlog.run("morning brief") as run_:
        run_.observe(_morning_smoke())

        assert [item.title for item in pulse.items()] == list(LANDINGS)

        log.record(
            "loop_opened",
            sensitivity="normal",
            key="CDI-596",
            owner="VP-Data",
            ask="cutover rehearsal",
            day=1,
        )
        log.record(
            "carry_forward",
            sensitivity="private",
            key="perf-conversation",
            owner="VP-Data",
            ask="perf-conversation follow-up",
            day=1,
        )
        folder.update_state(chase=log.chase_items(), watch=[], notes_gaps=[])

    # -- the row is in the log, stamped, and names what it could not read --
    (payload,) = log.recorded(RUN)
    row = RunRow.from_payload(payload)
    assert row.at == TUESDAY.isoformat(), "the row is stamped by the injected clock"
    assert row.completed and row.reached == ("calendar",)
    assert [(skip.name, skip.reason) for skip in row.skipped] == [("jira", observe.NO_PROBE)]

    # -- and the vault, rewritten from the same log, carries none of it -----
    written = folder.read_state()
    assert "cutover rehearsal" in written, "the walk must be doing real work"
    assert "perf-conversation" not in written, "a private chase item reached the vault"
    for leaked in ("morning brief", "reached:", observe.NO_PROBE):
        assert leaked not in written, f"{leaked!r} reached a synced markdown file"

    # The one line the vault MAY carry keeps the wording of nothing.
    (projection,) = runlog.projection_lines()
    assert "morning brief" in projection and "jira (no probe)" in projection


def test_a_morning_that_dies_partway_still_leaves_a_row(tmp_path, log, clock):
    """The failure-honesty path, end to end.

    Nothing on this machine notices a brief that never arrived. If the run dies
    at the mirror step, this row is all that is left - and it has to say which
    sources it had already read, or it is no better than the silence.
    """
    clock.set(TUESDAY)
    runlog = RunLog(log, clock=clock)

    # A real failure from a real module rather than a planted `raise`: the
    # mirror path does not exist, and reading it raises instead of reporting a
    # quiet day. Matched on the mirror name, not on the exception's wording,
    # which is the operating system's to phrase.
    with pytest.raises(Exception, match=r"gone\.git"):
        with runlog.run("morning brief") as run_:
            run_.observe(_morning_smoke())
            Pulse(mirrors=[Mirror.attach(tmp_path / "gone.git", cursor="HEAD")]).items()

    row = RunRow.from_payload(log.recorded(RUN)[0])
    assert not row.completed, "a run that raised must not be recorded as finished"
    assert row.at == TUESDAY.isoformat()
    assert "calendar" in row.reached, "what it did read before dying is the diagnosis"
    assert "jira (no probe)" in row.line(), "a source it never got to is still named"
    # The failure is kept, head-first and trimmed to one line - a long temp path
    # is exactly the case the trim exists for, so assert the head, not the tail.
    assert row.failure.startswith("[Errno 2] No such file or directory")
    assert len(row.failure) <= FAILURE_LIMIT, "a run log row is a line, not a traceback"


def test_a_morning_that_bailed_before_reading_anything_is_not_a_success(log, clock):
    """The quietest failure of the lot: a loop that ran and read nothing.

    Every field a reader checks looks like a healthy run unless the outcome says
    otherwise, and "the agent last ran fine at 6:40" would then be a claim made
    by a run that never asked a single source anything.
    """
    clock.set(TUESDAY)
    runlog = RunLog(log, clock=clock)

    with runlog.run("morning brief"):
        pass

    row = RunRow.from_payload(log.recorded(RUN)[0])
    assert row.outcome == UNCHECKED and not row.completed
    assert "reached: none" in row.line()
    assert runlog.last_completed_run("morning brief") is None
    assert "last completed: never" in runlog.projection_line("morning brief")


def test_a_dead_loop_is_still_visible_beside_a_healthy_one(log, clock):
    """Five loops share one log, so the projection is per loop.

    A noon chaser running fine at 12:05 must not be the line `State.md` carries
    while the morning brief has failed every day since tuesday.
    """
    clock.set(TUESDAY)
    runlog = RunLog(log, clock=clock)

    with pytest.raises(RuntimeError):
        with runlog.run("morning brief") as run_:
            run_.observe(_morning_smoke())
            raise RuntimeError("calendar auth expired")
    clock.set(TUESDAY.replace(hour=12, minute=5))
    runlog.record("noon chaser", _morning_smoke())

    assert runlog.last_run().loop == "noon chaser", "the fixture must be meaningful"
    brief = next(line for line in runlog.projection_lines() if "morning brief" in line)
    assert "failed" in brief and "last completed: never" in brief
    assert "calendar auth expired" not in brief, "the wording stays in the log"


# --------------------------------------------------------------------------
# memory - the differentiator: a gap created today, reported tomorrow
# --------------------------------------------------------------------------


def _meeting(day: datetime, summary: str, event_id: str):
    """`calendar_event`, with its instants as the ISO strings a real payload
    carries - the runner's own parse is part of this seam.

    Explicit minutes: `replace(hour=10)` on a 06:40 base is 10:40, which
    silently put the note's arrival BEFORE the meeting ended.
    """
    event = calendar_event(
        event_id, summary, day.replace(hour=9, minute=0), attendees=(PRINCIPAL_EMAIL, VP_DATA)
    )
    # No `kind`: `run._remember` splats the record into `EventLog.record(kind,
    # ...)`, so a record carrying one raises. Google's records all do.
    del event["kind"]
    return {**event, "start": event["start"].isoformat(), "end": event["end"].isoformat()}


def _payloads(calendar=(), vault=""):
    return {"calendar": list(calendar), "slack": [], "gmail": [], "vault": vault}


def test_a_meeting_today_is_a_notes_gap_tomorrow(identities, log_path):
    """The one section nothing else in the system can produce.

    `_seed_and_gaps` seeds today's rows precisely so tomorrow can report the
    ones that produced nothing. A ledger rebuilt empty on every run cannot do
    that - and says nothing about it, because an empty section is omitted.
    """
    run.render(
        "morning",
        now=MONDAY,
        identities=identities,
        payloads=_payloads(calendar=[_meeting(MONDAY, "Pod Steering", "e1")]),
        log=log_path,
    )

    tomorrow = run.render(
        "morning",
        now=TUESDAY,
        identities=identities,
        payloads=_payloads(),
        log=log_path,
    )

    assert "Pod Steering" in tomorrow, (
        f"yesterday's meeting was forgotten, so its gap can never be reported:\n{tomorrow}"
    )


def test_a_meeting_that_did_produce_a_note_is_not_a_gap(identities, log_path):
    """The other half - remembering must not mean reporting everything."""
    run.render(
        "morning",
        now=MONDAY,
        identities=identities,
        payloads=_payloads(calendar=[_meeting(MONDAY, "Pod Steering", "e1")]),
        log=log_path,
    )

    tomorrow = run.render(
        "morning",
        now=TUESDAY,
        identities=identities,
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
        log=log_path,
    )

    assert "no note found" not in tomorrow, (
        f"a meeting with a note was still reported as a gap:\n{tomorrow}"
    )


def test_without_a_log_each_run_still_works_and_simply_remembers_nothing(identities):
    """The log is optional, because a first run has none and a test may not
    want one. What it must not do is fail."""
    text = run.render(
        "morning",
        now=MONDAY,
        identities=identities,
        payloads=_payloads(calendar=[_meeting(MONDAY, "Pod Steering", "e1")]),
    )

    assert text.strip()


# --------------------------------------------------------------------------
# memory - state: his edits are the input
# --------------------------------------------------------------------------


def test_a_hand_written_chase_item_reaches_the_brief(identities, folder, log_path):
    """CLAUDE.md: a hand edit is an event and wins over derived state. The
    loop reads `State.md` before it runs, or his corrections are noise it
    overwrites."""
    folder.update_state(chase=[{"owner": "VP-Data", "ask": "the compute consolidation plan"}])

    text = run.render(
        "morning",
        now=MONDAY,
        identities=identities,
        payloads=_payloads(),
        log=log_path,
    )

    assert "compute consolidation" in text, f"State.md was not read:\n{text}"


def test_a_private_chase_item_never_reaches_the_vault_through_the_runner(identities, log, log_path):
    """The runner writes `State.md` back, so it inherits the one `_visible`
    gate - or it becomes a second writer with its own idea of the rules."""
    log.record("carry_forward", sensitivity="private", owner="VP-AI", ask="SECRET-ASK", key="k1")

    run.render(
        "morning",
        now=MONDAY,
        identities=identities,
        payloads=_payloads(),
        log=log_path,
        write_state=True,
    )

    leaked = [
        path
        for path in (Path(identities["VAULT_ROOT"]) / "DayDAG").rglob("*")
        if path.is_file() and "SECRET-ASK" in path.read_text(encoding="utf-8")
    ]
    assert not leaked, f"a private chase item reached the vault: {leaked}"


# --------------------------------------------------------------------------
# memory - the run log: a failed run leaves a row
# --------------------------------------------------------------------------


def test_a_run_leaves_a_row_so_a_silent_failure_is_diagnosable(identities, log, log_path):
    run.render(
        "morning",
        now=MONDAY,
        identities=identities,
        payloads=_payloads(),
        log=log_path,
    )

    rows = log.recorded("run")

    assert rows, "a run left no trace, which is what runlog exists to prevent"


def test_a_note_that_arrived_today_matches_todays_meeting(identities, log_path):
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
        identities=identities,
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
        log=log_path,
    )

    assert "no note found" not in text, (
        f"a meeting whose note is listed above was still called a gap:\n{text}"
    )
