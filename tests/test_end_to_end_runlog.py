"""End to end: one morning's walk, and the row it leaves behind.

`tests/test_runlog.py` tests the module honestly and in isolation. This one asks
the different question - whether the run log FITS - because the failure it
exists to catch is a seam failure, not a unit one:

    the row is written to the store that holds the chase list,
    and to nothing under the vault.

Two stores, and ARCHITECTURE keeps them apart for reasons that only show up when
both are in play: the `DayDAG/` folder is hand-editable markdown synced by
iCloud, the event log is SQLite outside it. Get the seam wrong and the run log
either lands in a file five loops are already fighting over, or carries a
connector's error text - urls, tokens in query strings - into plaintext on every
device Nitin owns. Both failures pass a unit test of `runlog` alone, because
neither module is wrong on its own.

So this walks a morning with real objects: a real git repo, a real SQLite log, a
real vault folder in a temp dir, real `smoke` rows from injected probes. The
clock is injected, and no test here touches the network.

Named `test_end_to_end_runlog.py`, and self-contained, on purpose: several
parallel branches each grew a `test_end_to_end.py` and they are due to be
consolidated in one deliberate pass rather than merged four ways by accident.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from daydag import smoke
from daydag.pulse import Mirror, Pulse
from daydag.runlog import FAILURE_LIMIT, RUN, UNCHECKED, RunLog, RunRow
from daydag.state import EventLog, StateFolder

PT = timezone(timedelta(hours=-7))
#: 6:40am PT: the pre-brief slot, which is the run whose absence gets noticed.
SIX_FORTY = datetime(2026, 9, 8, 6, 40, tzinfo=PT)

#: Two of the real `smoke` checks, so the walk is fed genuine rows rather than
#: hand-written dicts. Calendar is probed and answers; Jira is given no probe,
#: which is the case that must never come out the far end as "reached".
RUNLOG_CHECKS = tuple(check for check in smoke.CHECKS if check.name in {"calendar", "jira"})


@pytest.fixture
def morning_repo(tmp_path, git_env):
    """A real repo with one landing, so the pulse has something true to read."""
    path = tmp_path / "svc.git"
    path.mkdir()

    def run(*args):
        subprocess.run(args, cwd=path, check=True, capture_output=True, env=git_env)

    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    for message in ("baseline", "CDI-596 cutover rehearsal"):
        (path / "f.txt").write_text(message)
        run("git", "add", "-A")
        run("git", "commit", "-q", "-m", message)
    return path


def _first_commit(repo, git_env):
    return subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env=git_env,
    ).stdout.strip()


def _morning_smoke():
    """One pre-flight pass: the calendar answers, nothing probed Jira."""
    return smoke.run({"calendar": lambda: {"events": []}}, checks=RUNLOG_CHECKS).as_rows()


def test_a_morning_run_leaves_its_row_in_the_log_and_nothing_in_the_vault(
    tmp_path, morning_repo, git_env
):
    """The seam, asserted on both sides of it.

    One event log holds the chase items that become `State.md` AND the run row
    that must not. One vault folder gets rewritten from that same log and has to
    come out carrying nothing of the run.
    """
    log = EventLog.open(tmp_path / "events.db")
    folder = StateFolder.create(tmp_path / "DayDAG")
    runlog = RunLog(log, clock=lambda: SIX_FORTY)

    # -- the morning, inside the run --------------------------------------
    with runlog.run("morning brief") as run:
        run.observe(_morning_smoke())

        pulse = Pulse(
            mirrors=[Mirror.attach(morning_repo, cursor=_first_commit(morning_repo, git_env))]
        )
        assert [item.title for item in pulse.items()] == ["CDI-596 cutover rehearsal"]

        log.record("loop_opened", key="CDI-596", owner="VP-Data", ask="cutover rehearsal", day=1)
        log.record(
            "carry_forward",
            sensitivity="private",
            key="perf-conversation",
            owner="VP-Data",
            ask="perf-conversation follow-up",
            day=1,
        )
        folder.write_state(chase=log.chase_items(), watch=[], notes_gaps=[])

    # -- the row is in the log, stamped, and names what it could not read --
    (payload,) = log.recorded(RUN)
    row = RunRow.from_payload(payload)
    assert row.at == SIX_FORTY.isoformat(), "the row is stamped by the injected clock"
    assert row.completed and row.reached == ("calendar",)
    assert [(skip.name, skip.reason) for skip in row.skipped] == [("jira", smoke.NO_PROBE)]

    # -- and the vault, rewritten from the same log, carries none of it -----
    written = folder.read_state()
    assert "cutover rehearsal" in written, "the walk must be doing real work"
    assert "perf-conversation" not in written, "a private chase item reached the vault"
    for leaked in ("morning brief", "reached:", smoke.NO_PROBE):
        assert leaked not in written, f"{leaked!r} reached a synced markdown file"

    # The one line the vault MAY carry keeps the wording of nothing.
    (projection,) = runlog.projection_lines()
    assert "morning brief" in projection and "jira (no probe)" in projection


def test_a_morning_that_dies_partway_still_leaves_a_row(tmp_path, morning_repo, git_env):
    """The failure-honesty path, end to end.

    Nothing on this machine notices a brief that never arrived. If the run dies
    at the mirror step, this row is all that is left - and it has to say which
    sources it had already read, or it is no better than the silence.
    """
    log = EventLog.open(tmp_path / "events.db")
    runlog = RunLog(log, clock=lambda: SIX_FORTY)

    # A real failure from a real module rather than a planted `raise`: the
    # mirror path does not exist, and reading it raises instead of reporting a
    # quiet day. Matched on the mirror name, not on the exception's wording,
    # which is the operating system's to phrase.
    with pytest.raises(Exception, match=r"gone\.git"):
        with runlog.run("morning brief") as run:
            run.observe(_morning_smoke())
            Pulse(mirrors=[Mirror.attach(tmp_path / "gone.git", cursor="HEAD")]).items()

    row = RunRow.from_payload(log.recorded(RUN)[0])
    assert not row.completed, "a run that raised must not be recorded as finished"
    assert row.at == SIX_FORTY.isoformat()
    assert "calendar" in row.reached, "what it did read before dying is the diagnosis"
    assert "jira (no probe)" in row.line(), "a source it never got to is still named"
    # The failure is kept, head-first and trimmed to one line - a long temp path
    # is exactly the case the trim exists for, so assert the head, not the tail.
    assert row.failure.startswith("[Errno 2] No such file or directory")
    assert len(row.failure) <= FAILURE_LIMIT, "a run log row is a line, not a traceback"


def test_a_morning_that_bailed_before_reading_anything_is_not_a_success(tmp_path):
    """The quietest failure of the lot: a loop that ran and read nothing.

    Every field a reader checks looks like a healthy run unless the outcome says
    otherwise, and "the agent last ran fine at 6:40" would then be a claim made
    by a run that never asked a single source anything.
    """
    log = EventLog.open(tmp_path / "events.db")
    runlog = RunLog(log, clock=lambda: SIX_FORTY)

    with runlog.run("morning brief"):
        pass

    row = RunRow.from_payload(log.recorded(RUN)[0])
    assert row.outcome == UNCHECKED and not row.completed
    assert "reached: none" in row.line()
    assert runlog.last_completed_run("morning brief") is None
    assert "last completed: never" in runlog.projection_line("morning brief")


def test_a_dead_loop_is_still_visible_beside_a_healthy_one(tmp_path):
    """Five loops share one log, so the projection is per loop.

    A noon chaser running fine at 12:05 must not be the line `State.md` carries
    while the morning brief has failed every day since tuesday.
    """
    log = EventLog.open(tmp_path / "events.db")
    ticks = iter(SIX_FORTY + timedelta(hours=n) for n in range(0, 12))
    runlog = RunLog(log, clock=lambda: next(ticks))

    with pytest.raises(RuntimeError):
        with runlog.run("morning brief") as run:
            run.observe(_morning_smoke())
            raise RuntimeError("calendar auth expired")
    runlog.record("noon chaser", _morning_smoke())

    assert runlog.last_run().loop == "noon chaser", "the fixture must be meaningful"
    brief = next(line for line in runlog.projection_lines() if "morning brief" in line)
    assert "failed" in brief and "last completed: never" in brief
    assert "calendar auth expired" not in brief, "the wording stays in the log"
