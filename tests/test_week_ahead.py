"""The Sunday week-ahead (SPEC 3.6, issue #21).

`week_ahead` is a sibling of `brief`, not a rewrite of it: the same shape of
test (fake sources, real assembly) proves the same class of thing brief's
suite proves - that the seam between "what a source hands back" and "what the
push says" is honest. Two things are specific to this loop and get their own
tests: a missing week-ahead plan has to LEAD (SPEC 3.6 rule 3), and a Monday
meeting sharing an attendee with an OOO/travel event on the same week has to
be flagged rather than silently scheduled.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from daydag import brief
from daydag.pulse import Mirror, Pulse
from daydag.state import StateFolder
from daydag.voice import voice_violations
from daydag.week_ahead import WeekAheadError, assemble

PT = ZoneInfo("America/Los_Angeles")

#: A Sunday evening. The closing week is Aug31-Sep4; the week ahead is Sep7-11.
SUNDAY = datetime(2026, 9, 6, 17, 30, tzinfo=PT)
NEXT_MONDAY = date(2026, 9, 7)

PRINCIPAL = "UPRINCIPAL1"
IDENTITIES = {"SLACK_USER_PRINCIPAL": PRINCIPAL}

NEXT_WEEK_NOTE = "Create Music Group/Weekly Notes/0907-0911.md"
CLOSING_WEEK_NOTE = "Create Music Group/Weekly Notes/0831-0904.md"

CLOSING_NOTE_TEXT = "## 🔴 High\n- [ ] note to sponsor on data platform access\n- [x] pod update\n"


def _event(event_id, summary, start, *, minutes=60, attendees=None, kind="meeting", link=None):
    return {
        "id": event_id,
        "summary": summary,
        "start": start,
        "end": start + timedelta(minutes=minutes),
        "attendees": list(attendees or ["nitin", "vp-data"]),
        "response_status": "needsAction",
        "kind": kind,
        "permalink": link or f"https://calendar.example.com/e/{event_id}",
    }


class FakeSources:
    """The same four reads `brief.Sources` performs, recorded rather than done."""

    def __init__(self, *, events=(), notes=None, broken=()):
        self._events = list(events)
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
        return [e for e in self._events if e["start"].date() == window.day]

    def weekly_note(self, path):
        self._check("weekly_note")
        self.note_paths.append(path)
        if path not in self._notes:
            raise FileNotFoundError(path)
        return self._notes[path]

    def slack(self, query):
        self._check("slack")
        return []

    def gmail(self, query):
        self._check("gmail")
        return []


@pytest.fixture
def state(tmp_path):
    return StateFolder.create(tmp_path / "vault" / "DayDAG")


def _assemble(*, sources, state=None, pulse=None, now=SUNDAY):
    return assemble(now=now, sources=sources, identities=IDENTITIES, state=state, pulse=pulse)


# --------------------------------------------------------------------------
# contract 1: refuse a naive clock, same as brief
# --------------------------------------------------------------------------


def test_a_naive_now_is_refused():
    with pytest.raises(WeekAheadError, match="timezone-aware"):
        assemble(now=SUNDAY.replace(tzinfo=None), sources=FakeSources(), identities=IDENTITIES)


# --------------------------------------------------------------------------
# contract 2: a missing plan LEADS - never a footnote, never last week reused
# --------------------------------------------------------------------------


def test_a_missing_weeks_plan_leads_the_message():
    pushed = _assemble(sources=FakeSources())
    assert pushed.sections, "no sections at all - the leading finding is missing"
    lead = pushed.sections[0]
    assert "no week-ahead plan" in lead.heading
    assert "0907-0911" in lead.heading
    assert "run weekly-planning now" in lead.render()
    assert NEXT_WEEK_NOTE in lead.render(), "the claim carries the path it looked for"


def test_a_present_plan_does_not_lead_with_a_warning():
    sources = FakeSources(notes={NEXT_WEEK_NOTE: "# Week of Sep 7-11\n"})
    pushed = _assemble(sources=sources)
    assert not any("no week-ahead plan" in s.heading for s in pushed.sections)
    assert NEXT_WEEK_NOTE in sources.note_paths


def test_a_read_error_on_the_plan_degrades_rather_than_pretending_its_missing():
    """A broken connector and an absent file must not read the same way."""
    pushed = _assemble(sources=FakeSources(broken=["weekly_note"]))
    assert "the week-ahead plan" in pushed.unreachable
    assert not any("no week-ahead plan" in s.heading for s in pushed.sections), (
        "a dead connector was reported as a genuinely missing plan"
    )


# --------------------------------------------------------------------------
# carryover: the closing week's open reds, read never re-derived
# --------------------------------------------------------------------------


def test_carryover_reads_the_closing_weeks_open_red_items():
    sources = FakeSources(notes={CLOSING_WEEK_NOTE: CLOSING_NOTE_TEXT})
    pushed = _assemble(sources=sources)
    carrying = next(s for s in pushed.sections if s.heading.startswith("carrying in"))
    text = carrying.render()
    assert "note to sponsor on data platform access" in text
    assert f"{CLOSING_WEEK_NOTE}#L2" in text
    assert "pod update" not in text, "a ticked item is closed and must not carry over"


def test_a_closing_week_with_no_note_carries_nothing_and_is_not_an_error():
    pushed = _assemble(sources=FakeSources())
    assert not any(s.heading.startswith("carrying in") for s in pushed.sections)
    assert "the closing week's note" not in pushed.unreachable


def test_chase_and_watch_are_read_out_of_state_like_brief_reads_them(state):
    state.write_state(
        chase=[{"owner": "VP-Data", "ask": "cutover rehearsal"}],
        watch=[{"what": "10k e2e run"}],
    )
    pushed = _assemble(sources=FakeSources(), state=state)
    carrying = next(s for s in pushed.sections if s.heading.startswith("carrying in"))
    assert "cutover rehearsal" in carrying.render()
    watch = next(s for s in pushed.sections if s.heading == "watch")
    assert "10k e2e run" in watch.render()


# --------------------------------------------------------------------------
# the calendar: Monday in full, Tue-Fri as a shape, weekend feeds travel only
# --------------------------------------------------------------------------


def test_calendar_is_read_seven_days_one_request_per_day():
    sources = FakeSources()
    _assemble(sources=sources)
    days = [w.day for w in sources.calendar_windows]
    assert days == [NEXT_MONDAY + timedelta(days=n) for n in range(7)]


def test_an_on_demand_run_off_a_sunday_still_finds_the_real_next_monday():
    """SPEC 3.8's on-demand "week ahead" does not promise a Sunday call.

    `day + 1` only means "next Monday" when `now` is a Sunday - true for the
    scheduled push, not for a command run mid-week. A Wednesday call must
    still land on the Monday of the UPCOMING week, not the next calendar day.
    """
    wednesday_evening = datetime(2026, 9, 9, 17, 30, tzinfo=PT)  # Wed, closing week Sep7-11
    sources = FakeSources()
    _assemble(sources=sources, now=wednesday_evening)
    days = [w.day for w in sources.calendar_windows]
    upcoming_monday = date(2026, 9, 14)
    assert days == [upcoming_monday + timedelta(days=n) for n in range(7)], (
        f"a Wednesday call must still open on the week's real next Monday, got {days[0]}"
    )


def test_monday_meetings_render_with_time_and_permalink():
    sources = FakeSources(
        events=[_event("steering", "Pod Steering", datetime(2026, 9, 7, 9, 0, tzinfo=PT))]
    )
    pushed = _assemble(sources=sources)
    monday = next(s for s in pushed.sections if s.heading.startswith("monday"))
    assert "mon sep 7" in monday.heading
    assert "9:00 Pod Steering" in monday.render()
    assert "https://calendar.example.com/e/steering" in monday.render()


def test_a_day_with_nothing_on_it_is_omitted_not_labelled_open():
    """Contract 6: silence is information - there is no permalink for a gap."""
    sources = FakeSources(
        events=[_event("demo", "Sprint Demo", datetime(2026, 9, 10, 14, 0, tzinfo=PT))]
    )
    pushed = _assemble(sources=sources)
    week = next(s for s in pushed.sections if s.heading == "the week").render()
    assert "thu" in week and "Sprint Demo" in week
    for quiet_day in ("tue:", "wed:", "fri:"):
        assert quiet_day not in week
    assert "open" not in week.lower()


def test_the_week_section_counts_meetings_and_names_them():
    sources = FakeSources(
        events=[
            _event("a", "Ivan/Ruwen Sync", datetime(2026, 9, 8, 10, 0, tzinfo=PT)),
            _event("b", "1:1", datetime(2026, 9, 8, 15, 0, tzinfo=PT)),
        ]
    )
    pushed = _assemble(sources=sources)
    week = next(s for s in pushed.sections if s.heading == "the week").render()
    assert "tue: 2 meetings" in week
    assert "Ivan/Ruwen Sync" in week and "1:1" in week


# --------------------------------------------------------------------------
# OOO / travel detection - Nitin's own calendar, cross-referenced
# --------------------------------------------------------------------------


def test_a_meeting_with_a_travelling_attendee_is_flagged_with_both_links():
    sources = FakeSources(
        events=[
            _event(
                "sync",
                "Ivan/Ruwen Sync",
                datetime(2026, 9, 8, 10, 0, tzinfo=PT),
                attendees=["nitin", "ruwen"],
            ),
            _event(
                "ooo",
                "Ruwen OOO",
                datetime(2026, 9, 8, 0, 0, tzinfo=PT),
                minutes=24 * 60,
                attendees=["ruwen"],
                kind="ooo",
                link="https://calendar.example.com/e/ooo",
            ),
        ]
    )
    pushed = _assemble(sources=sources)
    week = next(s for s in pushed.sections if s.heading == "the week").render()
    assert "Ruwen OOO" in week, "the flag names the travel event"
    assert "move it" in week
    assert "https://calendar.example.com/e/sync" in week
    assert "https://calendar.example.com/e/ooo" in week


def test_an_ooo_event_is_not_itself_counted_as_a_meeting():
    sources = FakeSources(
        events=[
            _event(
                "sync",
                "Ivan/Ruwen Sync",
                datetime(2026, 9, 8, 10, 0, tzinfo=PT),
                attendees=["nitin", "ruwen"],
            ),
            _event(
                "ooo",
                "Ruwen OOO",
                datetime(2026, 9, 8, 0, 0, tzinfo=PT),
                minutes=24 * 60,
                attendees=["ruwen"],
                kind="ooo",
            ),
        ]
    )
    pushed = _assemble(sources=sources)
    week = next(s for s in pushed.sections if s.heading == "the week").render()
    assert "tue: 1 meeting" in week, "the OOO block inflated the day's meeting count"


def test_a_meeting_with_no_travelling_attendee_is_not_flagged():
    sources = FakeSources(
        events=[
            _event("sync", "Ivan/Ruwen Sync", datetime(2026, 9, 8, 10, 0, tzinfo=PT)),
            _event(
                "ooo",
                "Someone Else OOO",
                datetime(2026, 9, 9, 0, 0, tzinfo=PT),
                attendees=["a-third-person"],
                kind="ooo",
            ),
        ]
    )
    pushed = _assemble(sources=sources)
    week = next(s for s in pushed.sections if s.heading == "the week").render()
    assert "move it" not in week


# --------------------------------------------------------------------------
# Monday prep: pre-built, never pre-sent (contract 4)
# --------------------------------------------------------------------------


def test_a_qualifying_monday_meeting_is_queued_for_prep_with_the_right_week():
    sources = FakeSources(
        events=[
            _event(
                "steering",
                "Pod Steering",
                datetime(2026, 9, 7, 9, 0, tzinfo=PT),
                attendees=["nitin", "vp-data", "eng"],
            )
        ]
    )
    pushed = _assemble(sources=sources)
    assert len(pushed.monday_preps) == 1
    prepped = pushed.monday_preps[0]
    assert prepped.meeting == "Pod Steering"
    assert prepped.reason.value == "steering/pod"
    # The regression this loop exists to catch: `prep.sources` once named the
    # CALLER's week (the closing one) because it ran from Sunday, not Monday.
    assert prepped.plan.prep_note.endswith("Meeting Prep/0907-0911.md"), (
        f"prep note named the wrong week: {prepped.plan.prep_note}"
    )


def test_a_standup_never_gets_queued_for_prep():
    sources = FakeSources(
        events=[_event("standup", "DE Standup", datetime(2026, 9, 7, 9, 0, tzinfo=PT))]
    )
    pushed = _assemble(sources=sources)
    assert pushed.monday_preps == ()


def test_a_malformed_monday_event_degrades_the_prep_queue_not_the_whole_push():
    """A calendar record missing what the ledger needs must not crash assemble."""
    sources = FakeSources(
        events=[
            {
                "id": "steering",
                "summary": "Pod Steering",
                "start": datetime(2026, 9, 7, 9, 0, tzinfo=PT),
                # no "end", no "attendees"
                "permalink": "https://calendar.example.com/e/steering",
            }
        ]
    )
    pushed = _assemble(sources=sources)
    assert "monday prep" in pushed.unreachable
    assert pushed.monday_preps == ()
    # The rendered "monday" section still ships - degrade is per-source.
    assert any(s.heading.startswith("monday") for s in pushed.sections)


def test_a_monday_event_missing_a_summary_also_degrades_named_not_crashed():
    """`Ledger.seed_day` reads `summary` too - the guard has to name that gap.

    Found in review: the upfront check named `id`/`start`/`end`/`attendees`
    but not `summary`, so a payload missing only that field skipped the
    informative `KeyError` and fell through to a bare one raised three frames
    into `Ledger.seed_day` instead - still caught by `assemble`'s degrade, but
    silently short of the message this guard exists to give.
    """
    sources = FakeSources(
        events=[
            {
                "id": "steering",
                "start": datetime(2026, 9, 7, 9, 0, tzinfo=PT),
                "end": datetime(2026, 9, 7, 10, 0, tzinfo=PT),
                "attendees": ["nitin", "vp-data"],
                # no "summary"
            }
        ]
    )
    pushed = _assemble(sources=sources)
    assert "monday prep" in pushed.unreachable
    assert pushed.monday_preps == ()


# --------------------------------------------------------------------------
# shipping: reused verbatim from `pulse`, only when non-empty
# --------------------------------------------------------------------------


def test_shipping_is_folded_in_only_when_non_empty(git_env, tmp_path):
    import subprocess

    repo = tmp_path / "svc.git"
    repo.mkdir()

    def run(*args):
        subprocess.run(args, cwd=repo, check=True, capture_output=True, env=git_env)

    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (repo / "f").write_text("baseline")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "baseline")
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env=git_env,
    ).stdout.strip()
    (repo / "f").write_text("shipped")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "CDI-600 neo4j migration")

    pulse = Pulse(mirrors=[Mirror.attach(repo, cursor=baseline)])

    pushed = _assemble(sources=FakeSources(), pulse=pulse)
    shipping = next(s for s in pushed.sections if s.heading == "shipping")
    assert "CDI-600 neo4j migration" in shipping.render()


def test_a_quiet_pulse_adds_no_shipping_section():
    pulse = Pulse(mirrors=[])
    pushed = _assemble(sources=FakeSources(), pulse=pulse)
    assert not any(s.heading == "shipping" for s in pushed.sections)


# --------------------------------------------------------------------------
# degrade, never stall - and the whole render stays evidenced and voiced
# --------------------------------------------------------------------------


def test_a_dead_calendar_degrades_to_one_line_and_the_push_still_ships(state):
    """Seven day-windows can all fail; the degrade line still names it once."""
    state.write_state(watch=[{"what": "10k e2e run"}])
    pushed = _assemble(sources=FakeSources(broken=["calendar"]), state=state)
    assert pushed.unreachable == ("calendar",), "the same failing source was named more than once"
    assert any(s.heading == "watch" for s in pushed.sections), "a dead calendar took the rest down"


def test_render_carries_the_unreachable_footer():
    pushed = _assemble(sources=FakeSources(broken=["calendar"]))
    assert "couldn't check calendar" in pushed.render()


# --------------------------------------------------------------------------
# evidence-or-silence and house voice, over the whole assembled push
# --------------------------------------------------------------------------


def test_a_fully_loaded_week_is_evidenced_and_clean_in_the_house_voice(state):
    state.write_state(
        chase=[{"owner": "VP-Data", "ask": "cutover rehearsal"}],
        watch=[{"what": "10k e2e run"}],
    )
    sources = FakeSources(
        events=[
            _event("steering", "Pod Steering", datetime(2026, 9, 7, 9, 0, tzinfo=PT)),
            _event(
                "sync",
                "Ivan/Ruwen Sync",
                datetime(2026, 9, 8, 10, 0, tzinfo=PT),
                attendees=["nitin", "ruwen"],
            ),
            _event(
                "ooo",
                "Ruwen OOO",
                datetime(2026, 9, 8, 0, 0, tzinfo=PT),
                minutes=24 * 60,
                attendees=["ruwen"],
                kind="ooo",
            ),
        ],
        notes={CLOSING_WEEK_NOTE: CLOSING_NOTE_TEXT},
    )
    pushed = _assemble(sources=sources, state=state)
    text = pushed.render()

    assert brief.unsourced_claims(text) == [], f"a claim shipped with no evidence:\n{text}"
    assert voice_violations(text) == [], f"broke the house voice:\n{voice_violations(text)}\n{text}"


def test_a_thin_week_still_renders_a_short_honest_push():
    """Rule 4: a genuinely quiet week says so in a few lines, not padded out."""
    pushed = _assemble(sources=FakeSources())
    text = pushed.render()
    assert text.startswith("week ahead")
    assert brief.unsourced_claims(text) == []
    assert voice_violations(text) == []
