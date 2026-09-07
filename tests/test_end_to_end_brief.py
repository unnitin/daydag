"""End to end: one morning arriving as a brief, through every module, no network.

`tests/test_brief.py` tests the assembler honestly and in isolation - and that
is exactly why it cannot catch the defect class this project actually has. Its
chase items, its notes gap and its shipping block are all fixtures, so nothing
there asks whether a REAL ledger row, a REAL `State.md` line or a REAL pulse
item survives becoming a brief line.

Every defect this project has had passed its own unit tests: the ledger dropped
61% of meetings because `_qualifies` was correct in isolation and wrong about
the world; `write_state` filtered `chase` and not `watch` because each filter
was right on its own; the Gemini subject parser was dead code that every unit
test exercised directly and no production path called. All three are seam
defects. So this walks the seams:

    calendar -> ledger  -> notes gap -> a brief line
    mirror   -> pulse   -> shipping block -> the same brief
    event log -> State.md (private items withheld) -> owed to you
    weekly note -> the open red item, cited by path and line
    the whole thing -> the house voice, and every claim sourced

Real objects throughout - a real git repo, a real SQLite log, a real vault
folder in a temp dir. Only the four connector reads are faked, because they are
the only thing here that would need a network.

Deliberately self-contained: it shares no fixture with any other end-to-end
suite so the files can be consolidated in one deliberate pass rather than
colliding.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from daydag import brief as brief_module
from daydag.ledger import Ledger, Match
from daydag.pulse import Mirror, Pulse
from daydag.state import DecisionQueue, EventLog, StateFolder
from daydag.voice import voice_violations

PT = timezone(timedelta(hours=-7))
MONDAY_9AM = datetime(2026, 9, 7, 9, 0, tzinfo=PT)
#: The brief that reports Monday runs Tuesday at 6:45am - which is the only time
#: Monday's notes gap can exist, since a note has until end of day to land.
TUESDAY_BRIEF = datetime(2026, 9, 8, 6, 45, tzinfo=PT)
IDENTITIES = {"SLACK_USER_PRINCIPAL": "UPRINCIPAL1"}

WEEKLY_NOTE = """# Week of Sep 7-11
triage: 🔴 high · 🟡 medium · 🟢 low

## Priorities
- [ ] 🔴 note to Sponsor on data platform access *(mine)*
- [x] 🔴 pod update *(mine)*
"""


class Connectors:
    """The four reads the brief performs, answered from fixtures.

    Only the network is absent. Everything the brief then does with the answers -
    the day window, the 6pm cutoff, the note parse - is the real code path.
    """

    def __init__(self, events=(), note=WEEKLY_NOTE, messages=(), mail=()):
        self._events = list(events)
        self._note = note
        self._messages = list(messages)
        self._mail = list(mail)
        self.windows = []
        self.queries = []

    def calendar(self, window):
        self.windows.append(window)
        return list(self._events)

    def weekly_note(self, path):
        if self._note is None:
            raise FileNotFoundError(path)
        return self._note

    def slack(self, query):
        self.queries.append(query)
        return list(self._messages)

    def gmail(self, query):
        self.queries.append(query)
        return list(self._mail)


def _calendar_event(event_id, summary, start, *, attendees=("nitin", "vp-data"), response=None):
    return {
        "id": event_id,
        "summary": summary,
        "start": start,
        "end": start + timedelta(hours=1),
        "attendees": list(attendees),
        "response_status": response or "needsAction",
        "kind": "meeting",
        # The brief cites it; the ledger ignores it. One record shape, two
        # consumers - which is the point of walking them together.
        "permalink": f"https://calendar.example.com/e/{event_id}",
    }


@pytest.fixture
def landings(tmp_path, git_env):
    """A real repo with two landings, so the pulse has something true to read."""
    path = tmp_path / "svc.git"
    path.mkdir()

    def run(*args):
        subprocess.run(args, cwd=path, check=True, capture_output=True, env=git_env)

    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    for message in ("baseline", "CDI-596 cutover rehearsal", "CDI-600 neo4j migration"):
        (path / "f.txt").write_text(message)
        run("git", "add", "-A")
        run("git", "commit", "-q", "-m", message)
    return path


def _rev(repo, git_env, *args):
    out = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env=git_env,
    )
    return out.stdout.strip()


def test_a_whole_morning_arrives_as_one_assembled_brief(tmp_path, landings, git_env):
    """The integration path, asserted at each seam rather than only at the end."""
    # -- calendar -> ledger ------------------------------------------------
    ledger = Ledger()
    ledger.seed_day(
        [
            _calendar_event("standup", "DE Standup", MONDAY_9AM),
            _calendar_event("steering", "Pod Steering", MONDAY_9AM + timedelta(hours=3)),
            _calendar_event(
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
    first = _rev(landings, git_env, "rev-list", "--max-parents=0", "HEAD")
    pulse = Pulse(mirrors=[Mirror.attach(landings, cursor=first)])
    assert [item.title for item in pulse.items()] == [
        "CDI-596 cutover rehearsal",
        "CDI-600 neo4j migration",
    ], "landings arrive oldest-first and include squashed (single-parent) commits"

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
    chase = log.chase_items()
    assert any(item.get("sensitivity") == "private" for item in chase), "fixture must be meaningful"

    folder.write_state(
        chase=chase,
        watch=[{"what": "nightly ingest", "sensitivity": "private"}, {"what": "R1.5 staging"}],
        notes_gaps=gaps,
    )
    written = folder.read_state()
    assert "cutover rehearsal" in written
    assert "perf-conversation" not in written, "a private chase item reached the vault"
    assert "nightly ingest" not in written, "a private watch item reached the vault"

    # -- a decision survives being answered by hand ------------------------
    queue = DecisionQueue(folder)
    item = queue.add("close the CDI-596 loop?")
    assert queue.status(item) == "open"
    decisions = folder.decisions_path
    decisions.write_text(decisions.read_text().replace(f"[{item}] close", f"[{item}] no - close"))
    assert queue.answer_for(item) == "no", "a hand edit is an event and wins"

    # -- and the assembler turns all of it into one message ----------------
    # Every input below is the object an earlier stanza actually produced: the
    # same ledger, the same pulse, the same State.md on disk.
    connectors = Connectors(
        events=[_calendar_event("1on1", "VP-Data 1:1", TUESDAY_BRIEF + timedelta(hours=3))]
    )
    assembled = brief_module.assemble(
        now=TUESDAY_BRIEF,
        sources=connectors,
        identities=IDENTITIES,
        state=folder,
        ledger=ledger,
        pulse=pulse,
    )
    text = assembled.render()

    assert len(connectors.windows) == 1, "the calendar must be asked one day at a time"
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
    assert brief_module.unsourced_claims(text) == [], "a claim shipped with no evidence"
    assert voice_violations(text) == [], f"the assembled brief broke the voice rules:\n{text}"


def test_a_quiet_morning_assembles_a_brief_with_no_shipping_block(landings, git_env):
    """SPEC 3.7 rule 3, across the seam: silence is information.

    The pulse obeying the rule is only half of it - a brief that prints an empty
    `shipping` heading has broken it while the pulse was behaving. A line saying
    nothing happened trains the reader to skim, which is how the whole brief
    stops being read.
    """
    pulse = Pulse(
        mirrors=[Mirror.attach(landings, cursor=_rev(landings, git_env, "rev-parse", "HEAD"))]
    )
    assert pulse.items() == []
    assert pulse.render().strip() == "", "a quiet day must render nothing at all"

    text = brief_module.assemble(
        now=TUESDAY_BRIEF,
        sources=Connectors(
            events=[_calendar_event("1on1", "VP-Data 1:1", TUESDAY_BRIEF + timedelta(hours=3))]
        ),
        identities=IDENTITIES,
        pulse=pulse,
    ).render()

    assert "VP-Data 1:1" in text, "the brief still ships on a quiet day"
    assert "shipping" not in text
    assert "no updates" not in text


def test_a_dead_connector_and_a_dead_mirror_both_degrade_into_the_same_brief(
    tmp_path, landings, git_env
):
    """Guardrail 6 across two seams at once.

    The two failures reach the brief by different routes - the mirror's through
    the pulse's own block, the connector's through the assembler - and both have
    to arrive as one line each without taking the brief down.
    """
    healthy = Mirror.attach(
        landings, cursor=_rev(landings, git_env, "rev-list", "--max-parents=0", "HEAD")
    )
    broken = Mirror.attach(tmp_path / "not-a-repo.git", cursor="HEAD")
    broken.mark_fetch_failed()

    class DeadSlack(Connectors):
        def slack(self, query):
            raise RuntimeError("slack timed out")

    assembled = brief_module.assemble(
        now=TUESDAY_BRIEF,
        sources=DeadSlack(
            events=[_calendar_event("1on1", "VP-Data 1:1", TUESDAY_BRIEF + timedelta(hours=3))]
        ),
        identities=IDENTITIES,
        pulse=Pulse(mirrors=[healthy, broken]),
    )
    text = assembled.render()

    assert assembled.unreachable == ("slack",)
    assert "couldn't check slack" in text, "the dead connector is named"
    assert "not-a-repo" in text, "the stale mirror is named rather than dropped"
    assert "as of" in text.lower(), "a stale mirror says so instead of asserting freshness"
    assert "CDI-596 cutover rehearsal" in text, "the healthy mirror still reports"
    assert "VP-Data 1:1" in text, "the healthy connector still reports"
    assert brief_module.unsourced_claims(text) == []
    assert voice_violations(text) == []
