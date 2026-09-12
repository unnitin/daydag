"""End to end: one Sunday evening arriving as a week-ahead push (issue #21).

Same reasoning as `test_end_to_end_brief.py`: every real defect this project
has had passed its own module's unit tests and was wrong about a NEIGHBOUR -
the ledger's qualification rule, the state-filter that covered one list and
not its twin, a parser exercised only by its own test. `test_week_ahead.py`
tests the assembler honestly in isolation, with fakes standing in for the
event log, the ledger and the pulse. This walks the seams with the real
objects instead:

    a real event log      -> State.md, private items withheld     -> carrying in
    a real weekly note     -> brief.red_items, cited by path#line  -> carrying in
    a real git mirror      -> pulse                                -> shipping
    real calendar events   -> a real Ledger -> prep.prep_worthy    -> monday_preps,
                              with the SAME meeting's week named correctly even
                              though `assemble` itself runs from the week before

Only the four connector reads are faked, because they are the only thing here
that would need a network.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

from daydag import brief, week_ahead
from daydag.pulse import Mirror, Pulse
from daydag.state import EventLog, StateFolder
from daydag.voice import voice_violations

PT = timezone(timedelta(hours=-7))
SUNDAY = datetime(2026, 9, 6, 17, 30, tzinfo=PT)

IDENTITIES = {"SLACK_USER_PRINCIPAL": "UPRINCIPAL1"}

CLOSING_WEEK_NOTE = "Create Music Group/Weekly Notes/0831-0904.md"
NEXT_WEEK_NOTE = "Create Music Group/Weekly Notes/0907-0911.md"

CLOSING_NOTE_TEXT = "## 🔴 High\n- [ ] note to sponsor on data platform access\n- [x] pod update\n"


def _event(event_id, summary, start, *, minutes=60, attendees=("nitin", "vp-data"), kind="meeting"):
    return {
        "id": event_id,
        "summary": summary,
        "start": start,
        "end": start + timedelta(minutes=minutes),
        "attendees": list(attendees),
        "response_status": "needsAction",
        "kind": kind,
        "permalink": f"https://calendar.example.com/e/{event_id}",
    }


class Connectors:
    """The four reads `week_ahead.assemble` performs, answered from fixtures."""

    def __init__(self, events=(), notes=None):
        self._events = list(events)
        self._notes = dict(notes or {})
        self.windows = []

    def calendar(self, window):
        self.windows.append(window)
        return [e for e in self._events if e["start"].date() == window.day]

    def weekly_note(self, path):
        if path not in self._notes:
            raise FileNotFoundError(path)
        return self._notes[path]

    def slack(self, query):
        return []

    def gmail(self, query):
        return []


def test_a_sunday_evening_arrives_as_one_assembled_week_ahead(tmp_path, git_env):
    """The integration path, asserted at each seam rather than only at the end."""
    # -- a real weekly note -> the closing week's open red item -------------
    # (read via brief.red_items - never re-derived, contract 1)

    # -- a real mirror -> pulse ---------------------------------------------
    repo = tmp_path / "svc.git"
    repo.mkdir()

    def run(*args):
        subprocess.run(args, cwd=repo, check=True, capture_output=True, env=git_env)

    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (repo / "f.txt").write_text("baseline")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "baseline")
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env=git_env,
    ).stdout.strip()
    (repo / "f.txt").write_text("shipped")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "CDI-596 cutover rehearsal")
    pulse = Pulse(mirrors=[Mirror.attach(repo, cursor=baseline)])
    assert [item.title for item in pulse.items()] == ["CDI-596 cutover rehearsal"]

    # -- a real event log -> State.md, private items withheld ---------------
    folder = StateFolder.create(tmp_path / "DayDAG")
    log = EventLog.open(tmp_path / "events.db")
    log.record("loop_opened", key="CDI-596", owner="VP-Data", ask="cutover rehearsal", day=1)
    log.record(
        "carry_forward",
        sensitivity="private",
        key="perf-conversation",
        owner="VP-Data",
        ask="perf-conversation follow-up",
        day=1,
    )
    folder.write_state(
        chase=log.chase_items(),
        watch=[{"what": "10k e2e run"}],
    )
    assert "perf-conversation" not in folder.read_state(), "fixture must be meaningful"

    # -- real calendar events -> a real ledger -> prep's own qualification --
    connectors = Connectors(
        events=[
            _event("standup", "DE Standup", datetime(2026, 9, 7, 9, 0, tzinfo=PT)),
            _event("steering", "Pod Steering", datetime(2026, 9, 7, 10, 0, tzinfo=PT)),
            _event(
                "sync",
                "Ivan/Ruwen Sync",
                datetime(2026, 9, 8, 10, 0, tzinfo=PT),
                attendees=("nitin", "ruwen"),
            ),
            _event(
                "ooo",
                "Ruwen OOO",
                datetime(2026, 9, 8, 0, 0, tzinfo=PT),
                minutes=24 * 60,
                attendees=("ruwen",),
                kind="ooo",
            ),
        ],
        notes={CLOSING_WEEK_NOTE: CLOSING_NOTE_TEXT},
        # NEXT_WEEK_NOTE is deliberately absent - the missing-plan case is
        # covered in its own end-to-end variant below.
    )

    pushed = week_ahead.assemble(
        now=SUNDAY,
        sources=connectors,
        identities=IDENTITIES,
        state=folder,
        pulse=pulse,
    )
    text = pushed.render()

    assert len(connectors.windows) == 7, "the next seven days, one request per day"

    # the missing plan leads
    assert text.startswith("week ahead")
    assert "no week-ahead plan for 0907-0911" in text

    # the closing week's open red item, cited by path and line
    assert "note to sponsor on data platform access" in text
    assert f"{CLOSING_WEEK_NOTE}#L2" in text
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

    assert brief.unsourced_claims(text) == [], f"a claim shipped with no evidence:\n{text}"
    assert voice_violations(text) == [], f"broke the house voice:\n{voice_violations(text)}\n{text}"


def test_a_dead_connector_and_a_stale_mirror_both_degrade_into_the_same_push(tmp_path, git_env):
    """Guardrail 6 across two seams at once, the same property brief asserts."""
    healthy_repo = tmp_path / "svc.git"
    healthy_repo.mkdir()

    def run(*args):
        subprocess.run(args, cwd=healthy_repo, check=True, capture_output=True, env=git_env)

    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (healthy_repo / "f").write_text("baseline")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "baseline")
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=healthy_repo,
        capture_output=True,
        text=True,
        check=True,
        env=git_env,
    ).stdout.strip()
    (healthy_repo / "f").write_text("shipped")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "CDI-600 neo4j migration")
    healthy = Mirror.attach(healthy_repo, cursor=baseline)

    broken = Mirror.attach(tmp_path / "not-a-repo.git", cursor="HEAD")
    broken.mark_fetch_failed()

    class DeadClosingNote(Connectors):
        def weekly_note(self, path):
            if path == CLOSING_WEEK_NOTE:
                raise RuntimeError("vault read timed out")
            return super().weekly_note(path)

    connectors = DeadClosingNote(
        events=[_event("steering", "Pod Steering", datetime(2026, 9, 7, 9, 0, tzinfo=PT))]
    )

    pushed = week_ahead.assemble(
        now=SUNDAY,
        sources=connectors,
        identities=IDENTITIES,
        pulse=Pulse(mirrors=[healthy, broken]),
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
    assert brief.unsourced_claims(text) == []
    assert voice_violations(text) == []
