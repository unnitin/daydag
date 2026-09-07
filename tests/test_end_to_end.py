"""End to end: one morning's work, through every module, with no network.

Each suite tests its own module honestly and in isolation. Nothing until now has
asked whether the modules FIT - whether a ledger row survives becoming a brief
line, whether a pulse item can actually be rendered, whether the sensitivity tag
one module sets is the one another module reads.

That is where this project's real defects have lived. Every one of them passed
its own unit tests: the ledger dropped 61% of meetings because `_qualifies`
was correct in isolation and wrong about the world; `write_state` filtered
`chase` and not `watch` because each filter was right on its own; the Gemini
parser was dead code that every unit test exercised directly and no production
path called.

So this walks one weekday:

    calendar -> ledger -> notes gap
    mirror   -> pulse  -> shipping block
    evidence -> chase loop (surfaced, never closed)
    all of it -> State.md, with the private items withheld
    the brief -> rendered in the house voice

Real objects throughout - a real git repo, a real SQLite log, a real vault
folder in a temp dir. Only the network is absent, because none of these modules
should need it.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from daydag.ledger import Ledger, Match
from daydag.pulse import Mirror, Pulse
from daydag.state import DecisionQueue, EventLog, StateFolder
from daydag.voice import Push, render, voice_violations

PT = timezone(timedelta(hours=-7))
MONDAY_9AM = datetime(2026, 9, 7, 9, 0, tzinfo=PT)


def _event(event_id, summary, start, *, attendees=("nitin", "vp-data"), response="needsAction"):
    return {
        "id": event_id,
        "summary": summary,
        "start": start,
        "end": start + timedelta(hours=1),
        "attendees": list(attendees),
        "response_status": response,
        "kind": "meeting",
    }


@pytest.fixture
def repo(tmp_path, git_env):
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


def _first_commit(repo, git_env):
    out = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env=git_env,
    )
    return out.stdout.strip()


def test_a_whole_morning_passes_through_every_module(tmp_path, repo, git_env):
    """The integration path, asserted at each seam rather than only at the end."""
    # -- calendar -> ledger ------------------------------------------------
    ledger = Ledger()
    ledger.seed_day(
        [
            _event("standup", "DE Standup", MONDAY_9AM),
            _event("steering", "Pod Steering", MONDAY_9AM + timedelta(hours=3)),
            _event(
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
    pulse = Pulse(mirrors=[Mirror.attach(repo, cursor=_first_commit(repo, git_env))])
    items = pulse.items()
    assert [item.title for item in items] == [
        "CDI-596 cutover rehearsal",
        "CDI-600 neo4j migration",
    ], "landings arrive oldest-first and include squashed (single-parent) commits"

    shipping = pulse.render()
    assert shipping.strip(), "two landings must produce a shipping block"
    for banned in ("commits", "lines changed", "contributions"):
        assert banned not in shipping.lower(), "the block must not read as a productivity metric"

    # -- evidence -> chase loop, surfaced and NEVER closed -----------------
    pulse.observe_slack("can you pick up CDI-596 this week?")
    pulse.observe_pr(title="CDI-596 cutover rehearsal", number=412, state="merged")
    loop = {"key": "CDI-596", "owner": "VP-Data", "ask": "cutover rehearsal", "status": "open"}
    after = pulse.apply_evidence(loop)
    assert after["evidence_of_movement"] is True
    assert after["status"] == "open", "merged is not the same as what was asked for"

    # -- everything -> State.md, private items withheld ---------------------
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
    assert "⚠ chase item" not in written, "a chase item rendered as unreadable"
    assert "Pod Steering" in written, "the notes gap must reach the brief"
    assert "perf-conversation" not in written, "a private chase item reached the vault"
    assert "nightly ingest" not in written, "a private watch item reached the vault"

    # -- a decision survives being answered by hand ------------------------
    queue = DecisionQueue(folder)
    item = queue.add("close the CDI-596 loop?")
    assert queue.status(item) == "open"
    path = folder.decisions_path
    path.write_text(path.read_text().replace(f"[{item}] close", f"[{item}] no - close"))
    assert queue.answer_for(item) == "no", "a hand edit is an event and wins"

    # -- the brief renders in the house voice ------------------------------
    brief = render(Push.MORNING_BRIEF, {"day": "mon", "count": len(ledger.open_rows()) + 1})
    assert voice_violations(brief) == [], f"the brief broke the voice rules: {brief!r}"


def test_a_quiet_morning_produces_no_shipping_block(tmp_path, repo, git_env):
    """SPEC 3.7 rule 3, end to end: silence is information.

    A line saying nothing happened trains the reader to skim, which is how the
    whole brief stops being read.
    """
    tip = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env=git_env,
    ).stdout.strip()
    pulse = Pulse(mirrors=[Mirror.attach(repo, cursor=tip)])
    assert pulse.items() == []
    assert pulse.render().strip() == "", "a quiet day must render nothing at all"


def test_a_broken_mirror_degrades_and_the_rest_still_ships(tmp_path, repo, git_env):
    """Guardrail 6 across a seam: one dead source must not take the brief with it."""
    good = Mirror.attach(repo, cursor=_first_commit(repo, git_env))
    broken = Mirror.attach(tmp_path / "not-a-repo.git", cursor="HEAD")
    broken.mark_fetch_failed()

    pulse = Pulse(mirrors=[good, broken])
    out = pulse.render()
    assert "CDI-596 cutover rehearsal" in out, "the healthy mirror still reports"
    assert "not-a-repo" in out, "the failed one is named rather than dropped"
    assert "as of" in out.lower(), "a stale mirror says so instead of asserting freshness"
