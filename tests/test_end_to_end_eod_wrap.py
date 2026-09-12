"""End to end: one evening arriving as a wrap, through every module, no network.

Same rationale as `tests/test_end_to_end_brief.py`: the wrap's own unit tests
(`tests/test_eod_wrap.py`) drive the assembler honestly with fixtures for a
ledger row, a real note and a real pulse item. This walks the seams instead -
a REAL meeting ledger row, a REAL pulse built from a REAL git repo, a REAL
weekly note on disk-shaped text - so a defect in how one module's output
survives becoming a wrap line has somewhere to show up.

    calendar -> ledger's notes gap -> the prep-gap admission on the wrap
    mirror   -> pulse   -> the moved block -> the same wrap
    the weekly note's ticked red item -> "closed today", cited by path and line
    the whole thing -> the house voice, and every claim sourced

Only the two connector reads (calendar, vault note) are faked, because they
are the only thing here that would need a network or a filesystem. Everything
downstream of them is the real code path.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

from daydag import eod_wrap
from daydag.brief import unsourced_claims
from daydag.ledger import Ledger, Match
from daydag.pulse import Mirror, Pulse
from daydag.voice import voice_violations

PT = timezone(timedelta(hours=-7))
#: A Monday, run at the wrap's own time. "Tomorrow" is Tuesday - a real 1:1.
MONDAY_WRAP = datetime(2026, 9, 7, 16, 30, tzinfo=PT)
TUESDAY_1ON1 = datetime(2026, 9, 8, 15, 0, tzinfo=PT)

WEEKLY_NOTE = """# Week of Sep 7-11
triage: 🔴 high · 🟡 medium · 🟢 low

## Priorities
- [ ] 🔴 note to Sponsor on data platform access *(mine)*
- [x] 🔴 pod update *(mine)*
"""


class Connectors:
    """The two reads the wrap performs, answered from fixtures."""

    def __init__(self, events=(), notes=None):
        self._events = list(events)
        self._notes = dict(notes or {})
        self.windows = []
        self.note_paths = []

    def calendar(self, window):
        self.windows.append(window)
        return list(self._events)

    def vault_note(self, path):
        self.note_paths.append(path)
        if path not in self._notes:
            raise FileNotFoundError(path)
        return self._notes[path]


def _calendar_event(event_id, summary, start, *, attendees=("nitin", "vp-data")):
    return {
        "id": event_id,
        "summary": summary,
        "start": start,
        "end": start + timedelta(hours=1),
        "attendees": list(attendees),
        "response_status": "needsAction",
        "kind": "meeting",
        "permalink": f"https://calendar.example.com/e/{event_id}",
    }


def _rev(repo, git_env, *args):
    out = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, env=git_env
    )
    return out.stdout.strip()


def test_a_whole_evening_arrives_as_one_assembled_wrap(tmp_path, git_env):
    """The integration path, asserted at each seam rather than only at the end."""
    # -- a real repo with two landings, so the pulse has something true ----
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

    first = _rev(path, git_env, "rev-list", "--max-parents=0", "HEAD")
    pulse = Pulse(mirrors=[Mirror.attach(path, cursor=first)])
    assert [item.title for item in pulse.items()] == [
        "CDI-596 cutover rehearsal",
        "CDI-600 neo4j migration",
    ]

    # -- a real ledger row: last week's instance of tomorrow's meeting -----
    ledger = Ledger()
    last_week = MONDAY_WRAP - timedelta(days=6)
    ledger.seed_day(
        [
            {
                "id": "1on1-lastweek",
                "summary": "VP-Data 1:1",
                "start": last_week,
                "end": last_week + timedelta(hours=1),
                "attendees": ["nitin", "vp-data"],
                "response_status": "needsAction",
                "kind": "meeting",
            }
        ]
    )
    ledger.close_day()
    assert ledger.notes_gaps(as_of=MONDAY_WRAP) == ["VP-Data 1:1"], (
        "the fixture must be meaningful: last week's instance really has no note"
    )

    # -- and the assembler turns all of it into one message ----------------
    connectors = Connectors(
        events=[_calendar_event("1on1", "VP-Data 1:1", TUESDAY_1ON1)],
        notes={"Create Music Group/Weekly Notes/0907-0911.md": WEEKLY_NOTE},
    )
    wrap = eod_wrap.assemble(
        now=MONDAY_WRAP,
        sources=connectors,
        ledger=ledger,
        pulse=pulse,
    )
    text = wrap.render()

    assert len(connectors.windows) == 1, "the calendar must be asked one day at a time"
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

    assert unsourced_claims(text) == [], "a claim shipped with no evidence"
    assert voice_violations(text) == [], f"the assembled wrap broke the voice rules:\n{text}"


def test_a_late_note_closes_the_gap_before_the_wrap_ever_sees_it(git_env):
    """The ledger's own late-arrival path (ARCHITECTURE's meeting ledger): a
    note that lands for last week's instance means the wrap has nothing to
    flag for tomorrow's, without the wrap doing anything gap-specific itself.
    """
    ledger = Ledger()
    last_week = MONDAY_WRAP - timedelta(days=6)
    ledger.seed_day(
        [
            {
                "id": "1on1-lastweek",
                "summary": "VP-Data 1:1",
                "start": last_week,
                "end": last_week + timedelta(hours=1),
                "attendees": ["nitin", "vp-data"],
                "response_status": "needsAction",
                "kind": "meeting",
            }
        ]
    )
    attached = ledger.offer_note(
        Match(
            title="VP-Data 1:1",
            arrived=last_week + timedelta(hours=2),
            attendees=["nitin", "vp-data"],
            source="gemini",
        )
    )
    assert attached is not None

    connectors = Connectors(events=[_calendar_event("1on1", "VP-Data 1:1", TUESDAY_1ON1)])
    text = eod_wrap.assemble(now=MONDAY_WRAP, sources=connectors, ledger=ledger).render()

    assert "3:00 VP-Data 1:1" in text
    assert "no note found" not in text


def test_a_dead_connector_and_a_dead_mirror_both_degrade_into_the_same_wrap(tmp_path, git_env):
    """Guardrail 6 across two seams at once - mirrors the brief's own
    end-to-end coverage of the same property."""
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

    first = _rev(path, git_env, "rev-list", "--max-parents=0", "HEAD")
    healthy = Mirror.attach(path, cursor=first)

    broken = Mirror.attach(tmp_path / "not-a-repo.git", cursor="HEAD")
    broken.mark_fetch_failed()

    class DeadCalendar(Connectors):
        def calendar(self, window):
            raise RuntimeError("calendar timed out")

    wrap = eod_wrap.assemble(
        now=MONDAY_WRAP,
        sources=DeadCalendar(),
        pulse=Pulse(mirrors=[healthy, broken]),
    )
    text = wrap.render()

    assert wrap.unreachable == ("calendar",)
    assert "couldn't check calendar" in text
    assert "not-a-repo" in text, "the stale mirror is named rather than dropped"
    assert "as of" in text.lower()
    assert "CDI-596 cutover rehearsal" in text, "the healthy mirror still reports"
    assert unsourced_claims(text) == []
    assert voice_violations(text) == []
