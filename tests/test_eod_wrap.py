"""The EOD wrap (SPEC 3.5) - the brief's sibling, assembled the same way.

Same rationale as `tests/test_brief.py`: every source here is injected and
fake, because the wrap's job is assembly and assembly is where this project's
defects have actually lived. These tests drive the seams the wrap owns -
tomorrow's single calendar day, the weekly note's ticked side, a pulse with
nothing to say, the Friday-only outcome line - rather than the formatting.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from daydag.eod_wrap import WrapError, assemble
from daydag.ledger import Ledger
from daydag.pulse import Item, Mirror, Pulse
from daydag.voice import voice_violations

PT = ZoneInfo("America/Los_Angeles")

#: A Monday. "Tomorrow" is Tuesday Sep 8.
MONDAY = date(2026, 9, 7)
NOW = datetime(2026, 9, 7, 16, 30, tzinfo=PT)
TOMORROW = date(2026, 9, 8)

#: A Friday, so the planning-outcome section is in play.
FRIDAY_NOW = datetime(2026, 9, 11, 16, 30, tzinfo=PT)


def _event(event_id, summary, hour, minute=0, *, minutes=60, link=None, attendees=None):
    start = datetime(2026, 9, 8, hour, minute, tzinfo=PT)
    return {
        "id": event_id,
        "summary": summary,
        "start": start,
        "end": start + timedelta(minutes=minutes),
        "attendees": list(attendees or ["nitin", "vp-data"]),
        "response_status": "needsAction",
        "kind": "meeting",
        "permalink": link or f"https://calendar.example.com/e/{event_id}",
    }


NOTE_WITH_CLOSED = """# Week of Sep 7-11

## Priorities
- [ ] 🔴 note to Sponsor on data platform access *(mine)*
- [x] 🔴 send the pod update *(mine)*
- [x] 🟡 R1.5 staging validation *(tracking: VP-Data)*
"""


class FakeSources:
    """The two reads the wrap performs, recorded rather than performed."""

    def __init__(self, *, events=(), notes=None, broken=()):
        self._events = list(events)
        #: path -> text. A path absent here raises `FileNotFoundError`.
        self._notes = dict(notes or {})
        self._broken = set(broken)
        self.calendar_windows = []
        self.note_paths = []

    def _check(self, name):
        if name in self._broken:
            raise RuntimeError(f"{name} is down")

    def calendar(self, window):
        self._check("calendar")
        self.calendar_windows.append(window)
        return list(self._events)

    def vault_note(self, path):
        self._check("vault_note")
        self.note_paths.append(path)
        if path not in self._notes:
            raise FileNotFoundError(path)
        return self._notes[path]


def _assemble(sources, **kwargs):
    kwargs.setdefault("now", NOW)
    return assemble(sources=sources, **kwargs)


# --------------------------------------------------------------------------
# calendar: tomorrow, one day, never a range
# --------------------------------------------------------------------------


def test_the_calendar_is_asked_for_tomorrow_exactly_one_day():
    sources = FakeSources(events=[_event("a", "pod steering", 9)])
    _assemble(sources)

    assert len(sources.calendar_windows) == 1
    window = sources.calendar_windows[0]
    assert window.day == TOMORROW
    start = datetime.fromisoformat(window.time_min)
    end = datetime.fromisoformat(window.time_max)
    assert (end - start) == timedelta(days=1)


def test_tomorrows_first_meeting_carries_its_time_and_a_permalink():
    sources = FakeSources(events=[_event("a", "pod steering", 9), _event("b", "later thing", 15)])
    text = _assemble(sources).render()

    assert "9:00 pod steering" in text
    assert "https://calendar.example.com/e/a" in text
    assert "later thing" not in text, "only the first meeting is reported"


def test_no_events_tomorrow_omits_the_section_entirely():
    text = _assemble(FakeSources()).render()

    assert "tomorrow" not in text


# --------------------------------------------------------------------------
# what closed today: the weekly note's ticked red items
# --------------------------------------------------------------------------


def test_closed_today_reads_the_ticked_red_items():
    sources = FakeSources(notes={"Create Music Group/Weekly Notes/0907-0911.md": NOTE_WITH_CLOSED})
    text = _assemble(sources).render()

    assert "closed today (1)" in text
    assert "send the pod update" in text
    assert "note to Sponsor" not in text, "an open item is not closed"
    assert "R1.5 staging validation" not in text, "a non-red tick is not this section's business"


def test_no_closed_items_omits_the_section():
    sources = FakeSources(
        notes={"Create Music Group/Weekly Notes/0907-0911.md": "# Week\n\n- [ ] 🔴 open one\n"}
    )
    text = _assemble(sources).render()

    assert "closed today" not in text


def test_a_missing_weekly_note_is_a_fact_not_a_downed_source():
    text = _assemble(FakeSources()).render()

    assert "no weekly note" in text
    assert "couldn't check the weekly note" not in text


def test_a_weekly_note_that_cannot_be_read_is_a_downed_source():
    text = _assemble(FakeSources(broken=["vault_note"])).render()

    assert "couldn't check the weekly note" in text
    assert "no weekly note" not in text


# --------------------------------------------------------------------------
# what moved: the pulse, reused verbatim
# --------------------------------------------------------------------------


def test_a_quiet_pulse_produces_no_moved_block():
    """The header still states its literal (zero) count - same as the morning
    brief's own header - but there is no "moved (N)" section to go with it."""
    text = _assemble(FakeSources(), pulse=Pulse(mirrors=[])).render()

    assert "moved (" not in text


def test_no_pulse_at_all_is_also_silent():
    assert "moved (" not in _assemble(FakeSources()).render()


def test_a_pulse_with_landings_produces_a_moved_block(fake_repo):
    pulse = Pulse(mirrors=[Mirror.attach(fake_repo, cursor="HEAD~2")])
    text = _assemble(FakeSources(), pulse=pulse).render()

    assert "moved (2)" in text
    assert "Merge PR #413" in text


def test_a_stale_mirror_reaches_the_wrap_as_its_own_line(tmp_path):
    broken = Mirror.attach(tmp_path / "gone.git", cursor="HEAD~2")
    broken.mark_fetch_failed()
    text = _assemble(FakeSources(), pulse=Pulse(mirrors=[broken])).render()

    assert "gone.git" in text
    assert "could not fetch" in text


# --------------------------------------------------------------------------
# the prep gap: tomorrow's meeting cross-referenced against the ledger
# --------------------------------------------------------------------------


def test_a_notes_gap_for_tomorrows_meeting_is_surfaced():
    ledger = Ledger()
    last_week = datetime(2026, 9, 1, 9, 0, tzinfo=PT)
    ledger.seed_day(
        [
            {
                "id": "steering-lastweek",
                "summary": "pod steering",
                "start": last_week,
                "end": last_week + timedelta(hours=1),
                "attendees": ["nitin", "sponsor"],
                "response_status": "needsAction",
                "kind": "meeting",
            }
        ]
    )
    sources = FakeSources(events=[_event("a", "pod steering", 9)])
    text = _assemble(sources, ledger=ledger).render()

    assert "9:00 pod steering" in text
    assert "no note found" in text


def test_no_gap_when_the_ledger_has_a_note_for_it():
    from daydag.ledger import Match

    ledger = Ledger()
    last_week = datetime(2026, 9, 1, 9, 0, tzinfo=PT)
    ledger.seed_day(
        [
            {
                "id": "steering-lastweek",
                "summary": "pod steering",
                "start": last_week,
                "end": last_week + timedelta(hours=1),
                "attendees": ["nitin", "sponsor"],
                "response_status": "needsAction",
                "kind": "meeting",
            }
        ]
    )
    attached = ledger.offer_note(
        Match(
            title="pod steering",
            arrived=last_week + timedelta(hours=2),
            attendees=["nitin", "sponsor"],
            source="gemini",
        )
    )
    assert attached is not None
    sources = FakeSources(events=[_event("a", "pod steering", 9)])
    text = _assemble(sources, ledger=ledger).render()

    assert "9:00 pod steering" in text
    assert "no note found" not in text


def test_no_ledger_at_all_skips_the_gap_check():
    sources = FakeSources(events=[_event("a", "pod steering", 9)])
    text = _assemble(sources).render()

    assert "9:00 pod steering" in text
    assert "no note found" not in text


# --------------------------------------------------------------------------
# the header, the "?" for a source that could not be checked
# --------------------------------------------------------------------------


def test_the_header_counts_closed_and_moved(fake_repo):
    sources = FakeSources(notes={"Create Music Group/Weekly Notes/0907-0911.md": NOTE_WITH_CLOSED})
    pulse = Pulse(mirrors=[Mirror.attach(fake_repo, cursor="HEAD~2")])
    text = _assemble(sources, pulse=pulse).render()

    assert text.startswith("wrap: 1 closed, 2 moved")


def test_the_header_does_not_count_a_note_it_could_not_read():
    text = _assemble(FakeSources(broken=["vault_note"])).render()

    assert "0 closed" not in text
    assert "? closed" in text


# --------------------------------------------------------------------------
# guardrails: degrade, evidence, voice - on the assembled wrap
# --------------------------------------------------------------------------


def test_the_wrap_refuses_a_naive_clock():
    with pytest.raises(WrapError, match="timezone"):
        assemble(now=datetime(2026, 9, 7, 16, 30), sources=FakeSources())


@pytest.mark.guardrail
def test_one_downed_source_costs_one_line_and_the_wrap_still_ships():
    sources = FakeSources(
        events=[_event("a", "pod steering", 9)],
        broken=["vault_note"],
    )
    result = _assemble(sources)
    text = result.render()

    assert result.unreachable == ("the weekly note",)
    assert text.count("couldn't check") == 1
    assert "9:00 pod steering" in text, "one dead source suppressed a healthy one"


@pytest.mark.guardrail
def test_every_source_down_still_ships_a_wrap():
    sources = FakeSources(broken=["calendar", "vault_note"])
    text = _assemble(sources).render()

    assert text.startswith("wrap:")
    for name in ("calendar", "the weekly note"):
        assert f"couldn't check {name}" in text


@pytest.mark.guardrail
def test_every_claim_in_a_full_wrap_carries_evidence_or_admits_it(fake_repo):
    sources = FakeSources(
        events=[_event("a", "pod steering", 9)],
        notes={"Create Music Group/Weekly Notes/0907-0911.md": NOTE_WITH_CLOSED},
    )
    ledger = Ledger()
    text = _assemble(
        sources,
        ledger=ledger,
        pulse=Pulse(mirrors=[Mirror.attach(fake_repo, cursor="HEAD~2")]),
    ).render()

    from daydag.brief import unsourced_claims

    assert unsourced_claims(text) == []


def test_a_full_wrap_reads_in_his_voice(fake_repo):
    sources = FakeSources(
        events=[_event("a", "pod steering", 9)],
        notes={"Create Music Group/Weekly Notes/0907-0911.md": NOTE_WITH_CLOSED},
    )
    text = _assemble(
        sources, pulse=Pulse(mirrors=[Mirror.attach(fake_repo, cursor="HEAD~2")])
    ).render()

    assert text.startswith("wrap")
    assert voice_violations(text) == [], f"the wrap broke the voice rules:\n{text}"


def test_items_from_the_pulse_render_with_their_own_links():
    """A defensive check on the seam: the wrap must not rebuild the pulse's
    line and lose the link `Item` carries."""
    item = Item(title="CDI-596 cutover", permalink="mirrors/x.git#abc1234")
    from daydag.brief import unsourced_claims

    assert unsourced_claims(f"- {item.title} ({item.permalink})") == []


# --------------------------------------------------------------------------
# Friday: the planning outcome, never an offer to run it
# --------------------------------------------------------------------------


def test_a_non_friday_wrap_has_no_planning_section():
    text = _assemble(FakeSources()).render()

    assert "next week" not in text


def test_friday_reports_whether_the_two_planning_files_landed():
    sources = FakeSources(
        notes={
            "Create Music Group/Weekly Notes/0914-0918.md": "# Week of Sep 14-18\n",
            "Create Music Group/Meeting Prep/0914-0918.md": "# Meeting Prep\n",
        }
    )
    text = _assemble(sources, now=FRIDAY_NOW).render()

    assert "next week's plan landed" in text
    assert "next week's meeting prep landed" in text
    assert "Weekly Notes/0914-0918.md" in text
    assert "Meeting Prep/0914-0918.md" in text


def test_friday_says_a_planning_file_has_not_landed_yet_and_never_offers_to_run_it():
    """SPEC 3.5: Friday's wrap reports the outcome. It does NOT offer to run
    weekly-planning - that already ran at 1pm, and asking again is stale."""
    text = _assemble(FakeSources(), now=FRIDAY_NOW).render()

    assert "next week's plan hasn't landed yet" in text
    assert "next week's meeting prep hasn't landed yet" in text
    assert "?" not in text, "an offer to run weekly-planning must not appear here"
    assert "run weekly-planning" not in text.lower()


def test_a_downed_planning_file_read_does_not_claim_landed_or_not():
    """A dead connector is not evidence either way - it must not be asserted
    as "landed" (a false positive) nor claimed "not landed" (unverified)."""
    text = _assemble(FakeSources(broken=["vault_note"]), now=FRIDAY_NOW).render()

    assert "landed" not in text
    assert "couldn't check next week's plan" in text
    assert "couldn't check next week's meeting prep" in text


def test_the_moved_count_counts_movement_not_failure_notices(tmp_path):
    """`moved (1)` where the only line says a source could not be read.

    The count was `len(pulse.render().splitlines())`, but that block carries
    stale-mirror lines, unavailable repos and unparsed watchlist lines as well
    as items. A day where nothing shipped and a source degraded announced that
    something moved. `brief.assemble` renders the same block with no count at
    all, so the number is new here and was wrong from the start.
    """
    broken = Mirror.attach(tmp_path / "gone.git", cursor="HEAD~2")
    broken.mark_fetch_failed()

    text = _assemble(FakeSources(), pulse=Pulse(mirrors=[broken])).render()

    assert "could not fetch" in text, "the degrade line still ships"
    assert "moved (1)" not in text, f"a failure notice was counted as movement:\n{text}"
