"""The morning brief (SPEC 3.1) - the walking skeleton, asserted at its seams.

Every source here is injected and fake. That is not a shortcut around the real
connectors: the brief's job is assembly, and assembly is exactly the part that
stays wrong when each module is tested alone. So these tests drive the seams -
the day-by-day calendar window, the 6pm cutoff that Slack search cannot express,
a weekly note that does not exist, a pulse with nothing to say - rather than the
formatting.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from daydag import brief as brief_module
from daydag.brief import BriefError, assemble, red_items, unsourced_claims
from daydag.ledger import Ledger
from daydag.pulse import Item, Mirror, Pulse
from daydag.state import StateFolder
from daydag.voice import voice_violations

PT = ZoneInfo("America/Los_Angeles")

#: A Monday. The brief runs at 6:45am, so "overnight" opens 6pm Sunday.
MONDAY = date(2026, 9, 7)
NOW = datetime(2026, 9, 7, 6, 45, tzinfo=PT)
CUTOFF = datetime(2026, 9, 6, 18, 0, tzinfo=PT)

PRINCIPAL = "UPRINCIPAL1"
IDENTITIES = {"SLACK_USER_PRINCIPAL": PRINCIPAL}


def _event(event_id, summary, hour, minute=0, *, minutes=60, link=None, attendees=None):
    start = datetime(2026, 9, 7, hour, minute, tzinfo=PT)
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


def _message(text, when, *, who="VP-Data", link="https://slack.example.com/p/1"):
    return {"ts": f"{when.timestamp():.6f}", "text": text, "who": who, "permalink": link}


class FakeSources:
    """Every source the brief reads, recorded rather than performed."""

    def __init__(self, *, events=(), note=None, messages=(), mail=(), broken=()):
        self._events = list(events)
        self._note = note
        self._messages = list(messages)
        self._mail = list(mail)
        self._broken = set(broken)
        self.calendar_windows = []
        self.slack_queries = []
        self.gmail_queries = []
        self.note_paths = []

    def _check(self, name):
        if name in self._broken:
            raise RuntimeError(f"{name} is down")

    def calendar(self, window):
        self._check("calendar")
        self.calendar_windows.append(window)
        return list(self._events)

    def weekly_note(self, path):
        self._check("weekly_note")
        self.note_paths.append(path)
        if self._note is None:
            raise FileNotFoundError(path)
        return self._note

    def slack(self, query):
        self._check("slack")
        self.slack_queries.append(query)
        return list(self._messages)

    def gmail(self, query):
        self._check("gmail")
        self.gmail_queries.append(query)
        return list(self._mail)


@pytest.fixture
def state(tmp_path):
    return StateFolder.create(tmp_path / "vault" / "DayDAG")


def _assemble(sources, **kwargs):
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("identities", IDENTITIES)
    return assemble(sources=sources, **kwargs)


# --------------------------------------------------------------------------
# calendar: one request per day, never a wide one
# --------------------------------------------------------------------------


def test_the_calendar_is_asked_for_exactly_one_day():
    """A 5-day pull was measured at 156,681 chars and overflowed the connector.

    An overflow is not a short answer, it is no answer at all - and the brief
    would report an empty day it never actually read.
    """
    sources = FakeSources(events=[_event("a", "pod steering", 9)])
    _assemble(sources)

    assert len(sources.calendar_windows) == 1
    window = sources.calendar_windows[0]
    assert window.day == MONDAY
    start = datetime.fromisoformat(window.time_min)
    end = datetime.fromisoformat(window.time_max)
    assert (end - start) == timedelta(days=1), "the window spans more than one day"


def test_a_meeting_carries_its_local_time_and_a_permalink():
    sources = FakeSources(events=[_event("a", "pod steering w/ Sponsor", 9)])
    text = _assemble(sources).render()

    assert "9:00 pod steering w/ Sponsor" in text
    assert "https://calendar.example.com/e/a" in text
    assert "meetings (1)" in text


def test_an_unlinkable_meeting_says_it_could_not_be_sourced():
    """Guardrail 3's other half: if it cannot be sourced, say so."""
    event = _event("a", "mystery hold", 9)
    del event["permalink"]
    text = _assemble(FakeSources(events=[event])).render()

    assert "mystery hold" in text
    assert unsourced_claims(text) == []


def test_two_meetings_in_the_same_slot_are_surfaced_never_resolved():
    """Invariant 5. A duplicate slot is flagged; both still appear.

    Picking one would be resolving a discrepancy on his behalf, which is the
    one thing the agent is not allowed to do with a conflict.
    """
    sources = FakeSources(
        events=[_event("a", "modeler sync", 11), _event("b", "artifacts call", 11, 30)]
    )
    text = _assemble(sources).render()

    assert "modeler sync" in text and "artifacts call" in text
    overlap = [line for line in text.splitlines() if "overlap" in line]
    assert len(overlap) == 1, f"expected exactly one overlap flag, got {overlap}"
    assert "modeler sync" in overlap[0] and "artifacts call" in overlap[0]
    assert unsourced_claims(text) == [], "the flag must carry the links it is about"


def test_back_to_back_meetings_are_not_an_overlap():
    """A half-open window: 9:00-10:00 and 10:00-11:00 do not collide."""
    sources = FakeSources(events=[_event("a", "standup", 9), _event("b", "1:1", 10)])
    assert "overlap" not in _assemble(sources).render()


# --------------------------------------------------------------------------
# the weekly note: the note's own layout wins, and it may not exist at all
# --------------------------------------------------------------------------

NOTE_PRIORITIES = """# Week of Sep 7-11

## Priorities
- [ ] 🔴 note to Sponsor on data platform access *(mine)*
- [ ] 🟡 R1.5 staging validation *(tracking: VP-Data)*
- [x] 🔴 send the pod update *(mine)*
- [ ] 🔴 chase the artifacts access call *(mine)*
"""

NOTE_SECTIONS = """# Week of Sep 7-11
triage: 🔴 high · 🟡 medium · 🟢 low

## Section A - mine
* [ ] 🔴 note to Sponsor on data platform access *(mine)*
* [x] 🔴 send the pod update *(mine)*

## Section C - x-team
* [ ] 🟡 R1.5 staging validation *(tracking: VP-Data)*
* [ ] 🔴 chase the artifacts access call *(mine)*
"""


@pytest.mark.parametrize("note", [NOTE_PRIORITIES, NOTE_SECTIONS])
def test_the_note_layout_does_not_have_to_match_a_template(note):
    """Guardrail 2: the layout evolves and the note's own header wins.

    The same two open red items must survive a rename of every heading, a
    change of bullet character, and a legend line that names all three colours.
    """
    found = [text for _, text in red_items(note)]

    assert found == [
        "🔴 note to Sponsor on data platform access *(mine)*",
        "🔴 chase the artifacts access call *(mine)*",
    ]


def test_a_ticked_item_is_closed_and_does_not_come_back():
    """Practice beats stated convention: closed items are ticked in place."""
    assert red_items("- [x] 🔴 done already") == []


def test_a_legend_naming_every_colour_is_not_a_priority():
    assert red_items("- triage: 🔴 high, 🟡 medium, 🟢 low") == []


def test_a_red_item_carries_the_note_path_and_its_line():
    text = _assemble(FakeSources(note=NOTE_PRIORITIES)).render()

    assert "top of the note (0907-0911)" in text
    assert "Create Music Group/Weekly Notes/0907-0911.md#L4" in text


def test_a_missing_weekly_note_leads_the_brief_instead_of_crashing():
    """CLAUDE.md records a gap in the series; the first real run will meet it."""
    result = _assemble(FakeSources(events=[_event("a", "pod steering", 9)], note=None))
    text = result.render()

    assert result.sections[0].heading.startswith("⚠"), "the gap must lead"
    assert "0907-0911" in text
    assert "meetings (1)" in text, "the rest of the brief still ships"
    assert "couldn't check" not in text, "an absent note is a fact, not a dead source"


def test_a_weekly_note_that_cannot_be_read_is_a_downed_source_not_a_gap():
    """An evicted iCloud placeholder is not the same as a note nobody wrote."""
    text = _assemble(FakeSources(broken=["weekly_note"])).render()

    assert "couldn't check the weekly note" in text
    assert "no weekly note" not in text


# --------------------------------------------------------------------------
# the overnight delta: the cutoff Slack search cannot express
# --------------------------------------------------------------------------


def test_slack_is_asked_by_id_and_ordered_by_time():
    sources = FakeSources()
    _assemble(sources)

    assert len(sources.slack_queries) == 1
    query = sources.slack_queries[0]
    assert f"<@{PRINCIPAL}>" in query, "a display name in a search silently matches nothing"
    assert "sort:timestamp sort_dir:asc" in query


def test_the_overnight_delta_is_cut_at_6pm_not_at_midnight():
    """Slack search resolves to whole days, so the query over-fetches by a day.

    Without the timestamp filter the "overnight" delta is a whole extra day of
    Slack in a 6:45am DM - and the over-fetch is invisible, because the query
    itself looks right.
    """
    sources = FakeSources(
        messages=[
            _message("yesterday at lunch", CUTOFF - timedelta(hours=5)),
            _message("after dinner", CUTOFF + timedelta(hours=2)),
        ]
    )
    text = _assemble(sources).render()

    assert "after dinner" in text
    assert "yesterday at lunch" not in text, "the 6pm cutoff was not applied"


def test_an_overnight_message_is_quoted_with_its_permalink():
    sources = FakeSources(
        messages=[_message("are we ready to run e2e", CUTOFF + timedelta(hours=2))]
    )
    text = _assemble(sources).render()

    assert '"are we ready to run e2e"' in text
    assert "https://slack.example.com/p/1" in text


def test_gemini_notes_are_asked_for_by_sender_and_label():
    sources = FakeSources()
    _assemble(sources)

    assert len(sources.gmail_queries) == 1
    query = sources.gmail_queries[0]
    assert "from:gemini-notes@google.com" in query
    assert 'label:"meeting notes"' in query


def test_a_gemini_note_is_reported_by_its_parsed_title():
    """The structured subject is the #2 audit's correction to SPEC 4.

    Parsing it here is what makes the parser a production path rather than a
    function only its own unit test ever calls.
    """
    sources = FakeSources(
        mail=[
            {
                "subject": "Notes: “Pod Steering” - 2026/09/06",
                "permalink": "https://mail.example.com/t/9",
            }
        ]
    )
    text = _assemble(sources).render()

    assert '"Pod Steering"' in text
    assert "https://mail.example.com/t/9" in text


# --------------------------------------------------------------------------
# chase, watch and notes gaps
# --------------------------------------------------------------------------


def test_the_chase_list_and_watch_items_come_from_the_state_file(state):
    """A hand edit is an event and wins over anything the agent derived."""
    state.state_path.write_text(
        "# State\n\n"
        "## Chase list\n\n"
        "- VP-Data · bronze tables refreshed? https://slack.example.com/p/7\n\n"
        "## Watch items\n\n"
        "- 10k e2e run - https://slack.example.com/p/8\n",
        encoding="utf-8",
    )
    text = _assemble(FakeSources(), state=state).render()

    assert "owed to you (1)" in text
    assert "bronze tables refreshed?" in text
    assert "https://slack.example.com/p/7" in text
    assert "10k e2e run" in text
    assert "https://slack.example.com/p/8" in text


def test_a_chase_line_with_no_link_admits_it(state):
    """`write_state` records owner and ask but no permalink, so most lines have
    none. Saying so is guardrail 3; asserting it anyway is what it forbids."""
    state.write_state(chase=[{"owner": "VP-Data", "ask": "silver trigger"}])
    text = _assemble(FakeSources(), state=state).render()

    assert "silver trigger" in text
    assert unsourced_claims(text) == []
    assert "couldn't source" in text


def test_notes_gaps_from_the_ledger_reach_the_brief():
    """The ledger's actual deliverable: a meeting that produced nothing."""
    ledger = Ledger()
    yesterday = datetime(2026, 9, 6, 14, 0, tzinfo=PT)
    ledger.seed_day(
        [
            {
                "id": "steering",
                "summary": "Pod Steering",
                "start": yesterday,
                "end": yesterday + timedelta(hours=1),
                "attendees": ["nitin", "sponsor"],
                "response_status": "needsAction",
                "kind": "meeting",
            }
        ]
    )
    text = _assemble(FakeSources(events=[_event("a", "standup", 9)]), ledger=ledger).render()

    assert "Pod Steering" in text
    assert "no note found" in text
    assert "standup" in text, "today's meetings are seeded, not reported as gaps"
    assert unsourced_claims(text) == []


def test_todays_meetings_are_seeded_into_the_ledger():
    """Calendar drives; notes attach to rows. A meeting with no row can never
    be surfaced as a gap, so seeding is what makes tomorrow's gap possible."""
    ledger = Ledger()
    _assemble(FakeSources(events=[_event("a", "pod steering", 9)]), ledger=ledger)

    assert [row.event_id for row in ledger.open_rows()] == ["a"]


# --------------------------------------------------------------------------
# shipping: silence is information
# --------------------------------------------------------------------------


def test_a_quiet_pulse_produces_no_shipping_block():
    """SPEC 3.7 rule 3. A line saying nothing happened trains the reader to
    skim, which is how the whole brief stops being read."""
    text = _assemble(FakeSources(), pulse=Pulse(mirrors=[])).render()

    assert "shipping" not in text
    assert "no updates" not in text


def test_no_pulse_at_all_is_also_silent():
    assert "shipping" not in _assemble(FakeSources()).render()


def test_a_pulse_with_landings_produces_a_shipping_block(fake_repo):
    pulse = Pulse(mirrors=[Mirror.attach(fake_repo, cursor="HEAD~2")])
    text = _assemble(FakeSources(), pulse=pulse).render()

    assert "shipping" in text
    assert "Merge PR #413" in text
    assert unsourced_claims(text) == [], "the pulse's own links must survive the brief"


def test_a_stale_mirror_reaches_the_brief_as_its_own_line(tmp_path):
    """Guardrail 6 travels: the pulse's degraded line is not dropped in
    assembly, which is exactly where a "one dead source" line goes missing."""
    broken = Mirror.attach(tmp_path / "gone.git", cursor="HEAD~2")
    broken.mark_fetch_failed()
    text = _assemble(FakeSources(), pulse=Pulse(mirrors=[broken])).render()

    assert "gone.git" in text
    assert "could not fetch" in text


def test_an_empty_section_is_omitted_rather_than_labelled():
    text = _assemble(FakeSources()).render()

    for absent in ("meetings (0)", "owed to you (0)", "nothing", "no updates", "none"):
        assert absent not in text, f"{absent!r} is a line that says nothing happened"


# --------------------------------------------------------------------------
# the guardrails, on the assembled brief rather than on a module
# --------------------------------------------------------------------------


def _busy_sources():
    return FakeSources(
        events=[_event("a", "pod steering w/ Sponsor", 9), _event("b", "VP-Data 1:1", 15)],
        note=NOTE_PRIORITIES,
        messages=[_message("did that not get done?", CUTOFF + timedelta(hours=3))],
        mail=[
            {
                "subject": "Notes: “VP-Data 1:1” - 2026/09/06",
                "permalink": "https://mail.example.com/t/3",
            }
        ],
    )


def test_a_full_brief_reads_in_his_voice(state, fake_repo):
    state.write_state(
        chase=[{"owner": "VP-Data", "ask": "silver trigger"}],
        watch=[{"what": "10k e2e run"}],
    )
    text = _assemble(
        _busy_sources(),
        state=state,
        pulse=Pulse(mirrors=[Mirror.attach(fake_repo, cursor="HEAD~2")]),
        ledger=Ledger(),
    ).render()

    assert text.startswith("morning")
    assert voice_violations(text) == [], f"the brief broke the voice rules:\n{text}"


@pytest.mark.guardrail
def test_every_claim_in_a_full_brief_carries_evidence_or_admits_it(state, fake_repo):
    """Guardrail 3 / invariant 3, asserted on the rendered brief.

    On the render rather than the objects: a permalink held on something nobody
    prints is not a citation.
    """
    state.write_state(chase=[{"owner": "VP-Data", "ask": "silver trigger"}])
    text = _assemble(
        _busy_sources(),
        state=state,
        pulse=Pulse(mirrors=[Mirror.attach(fake_repo, cursor="HEAD~2")]),
    ).render()

    assert unsourced_claims(text) == []


@pytest.mark.guardrail
def test_the_unsourced_scanner_can_actually_fail():
    """A checker that never fires is worse than no checker at all."""
    assert unsourced_claims("- bronze tables are refreshed") == ["- bronze tables are refreshed"]
    assert unsourced_claims("- refreshed (https://slack.example.com/p/1)") == []
    assert unsourced_claims("meetings (2)") == [], "a heading is not a claim"


@pytest.mark.guardrail
def test_one_downed_source_costs_one_line_and_the_brief_still_ships():
    """Guardrail 6. Degrading is not the same as going quiet."""
    sources = FakeSources(note=NOTE_PRIORITIES, broken=["slack"])
    result = _assemble(sources)
    text = result.render()

    assert result.unreachable == ("slack",)
    assert text.count("couldn't check") == 1
    assert "couldn't check slack" in text
    assert "top of the note" in text, "one dead source suppressed a healthy one"


@pytest.mark.guardrail
def test_every_source_down_still_ships_a_brief():
    """The worst case is still a message, not a silence he cannot distinguish
    from a machine that never woke up."""
    sources = FakeSources(broken=["calendar", "weekly_note", "slack", "gmail"])
    text = _assemble(sources).render()

    assert text.startswith("morning")
    for name in ("calendar", "the weekly note", "slack", "gmail"):
        assert f"couldn't check {name}" in text
    assert unsourced_claims(text) == []
    assert voice_violations(text) == []


@pytest.mark.guardrail
def test_a_broken_state_file_degrades_like_any_other_source(tmp_path):
    text = _assemble(FakeSources(), state=StateFolder(tmp_path / "nope")).render()

    assert "couldn't check the chase list" in text


def test_the_brief_refuses_a_naive_clock():
    """The 6pm cutoff is a local wall-clock time; a naive `now` is seven hours
    wrong on a UTC runner and silently drops an evening of Slack."""
    with pytest.raises(BriefError, match="timezone"):
        assemble(now=datetime(2026, 9, 7, 6, 45), sources=FakeSources(), identities=IDENTITIES)


def test_an_unresolved_identity_reference_is_refused():
    """`${SLACK_USER_PRINCIPAL}` as literal text is a query matching nothing,
    which is indistinguishable from a quiet night."""
    with pytest.raises(BriefError):
        assemble(now=NOW, sources=FakeSources(), identities={})


def test_the_brief_never_reaches_for_a_network_client():
    """Every source is injected. A client constructed inside the assembler is a
    source that cannot be faked, and therefore one nobody tests degrading."""
    source = brief_module.__file__
    with open(source, encoding="utf-8") as handle:
        body = handle.read()
    for banned in ("import requests", "import httpx", "urlopen", "WebClient"):
        assert banned not in body


def test_a_long_quote_is_trimmed_but_keeps_its_link():
    long_text = "x" * 400
    sources = FakeSources(messages=[_message(long_text, CUTOFF + timedelta(hours=1))])
    text = _assemble(sources).render()

    assert "https://slack.example.com/p/1" in text
    assert "x" * 400 not in text
    assert max(len(line) for line in text.splitlines()) < 300


def test_items_from_the_pulse_render_with_their_own_links():
    """A defensive check on the seam, not on the pulse: `Item` carries the link
    and the brief must not rebuild the line and lose it."""
    item = Item(title="CDI-596 cutover", permalink="mirrors/x.git#abc1234")
    assert unsourced_claims(f"- {item.title} ({item.permalink})") == []


# --------------------------------------------------------------------------
# regressions from review - each of these shipped green and was wrong
# --------------------------------------------------------------------------


def test_a_message_with_no_timestamp_is_kept_not_dropped():
    """It cannot be placed against the cutoff, and the two errors are not equal.

    Over-reporting is a line he skims; under-reporting is a silence he has no
    way to notice. A missing `ts` used to default to 0 and fall out of the
    window - the one direction that is invisible.
    """
    sources = FakeSources(
        messages=[{"text": "urgent overnight thing", "who": "VP-AI", "permalink": "https://s/1"}]
    )
    assert "urgent overnight thing" in _assemble(sources).render()


def test_mail_from_yesterday_morning_is_not_overnight():
    """Gmail's `after:` is day-granular, so the query alone cannot mean 6pm.

    Without the second filter every note from yesterday's workday is reported
    as overnight - and re-reported every morning it stays inside the day.
    """
    morning = CUTOFF - timedelta(hours=9)
    evening = CUTOFF + timedelta(hours=1)
    sources = FakeSources(
        mail=[
            {
                "subject": "Notes: “yesterday 9am standup”",
                "permalink": "https://mail.example.com/t/1",
                "internalDate": str(int(morning.timestamp() * 1000)),
            },
            {
                "subject": "Notes: “evening sync”",
                "permalink": "https://mail.example.com/t/2",
                "internalDate": str(int(evening.timestamp() * 1000)),
            },
        ]
    )
    text = _assemble(sources).render()

    assert "evening sync" in text
    assert "yesterday 9am standup" not in text


def test_meetings_are_ordered_by_the_instant_not_by_how_it_was_written():
    """A 9am PT meeting may arrive as 16:00Z. Sorting the string ignores the
    offset, puts the afternoon first, and then invents an overlap between two
    meetings six hours apart."""
    utc = ZoneInfo("UTC")
    early = _event("early", "9am stated as 1600Z", 9)
    early["start"] = early["start"].astimezone(utc)
    early["end"] = early["end"].astimezone(utc)
    text = _assemble(FakeSources(events=[_event("late", "3pm PT", 15), early])).render()

    # Match the meeting-line SHAPE, not a hostname substring. `"host" in line`
    # is `py/incomplete-url-substring-sanitization` (high): harmless as a test
    # filter, but it is the same expression that, used as a real check, passes
    # `calendar.example.com.attacker.net`. Not worth teaching the pattern here,
    # and matching the rendered form is a sharper assertion anyway.
    lines = [line for line in text.splitlines() if re.match(r"- \d{1,2}:\d{2}\b", line)]
    assert lines[0].startswith("- 9:00"), f"ordered by string, not by instant: {lines}"
    assert "overlap" not in text, "two meetings six hours apart are not a collision"


def test_a_meeting_inside_a_long_block_is_still_flagged():
    """Adjacent-pair comparison hid every collision past the first, which is
    exactly the case the flag exists for: an invite inside a long hold."""
    sources = FakeSources(
        events=[
            _event("hold", "focus block", 9, minutes=180),
            _event("mid", "modeler sync", 10, minutes=30),
            _event("late", "artifacts call", 11, minutes=30),
        ]
    )
    flags = [line for line in _assemble(sources).render().splitlines() if "overlap" in line]

    assert len(flags) == 2, f"the 9-12 hold collides with both invites, got {flags}"
    assert any("artifacts call" in flag for flag in flags)


def test_a_source_that_raises_while_iterating_still_degrades():
    """`Sources` returns an Iterable, so a paginated adapter is a generator that
    raises on iteration rather than on the call. Materialised outside the guard,
    that failure walks straight past the degrade path."""

    class Paginated(FakeSources):
        def slack(self, query):
            def pages():
                raise RuntimeError("page 2 timed out")
                yield  # pragma: no cover - unreachable, keeps this a generator

            return pages()

    result = _assemble(Paginated(note=NOTE_PRIORITIES))

    assert result.unreachable == ("slack",)
    assert "top of the note" in result.render()


def test_a_malformed_calendar_record_costs_the_gap_line_not_the_brief():
    """The ledger indexes `id`/`start`/`end`/`summary` straight off a calendar
    record. A connector that omits one must not take the whole brief down over
    the single section that reports an absence."""
    event = _event("a", "pod steering", 9)
    del event["id"]
    result = _assemble(FakeSources(events=[event]), ledger=Ledger())

    assert "the meeting ledger" in result.unreachable
    assert "pod steering" in result.render(), "the brief still ships"


def test_the_header_does_not_count_a_calendar_it_could_not_read():
    """Zero meetings and an unread calendar are the same number and not the
    same fact - and the header is not a bullet, so the scanner never sees it."""
    text = _assemble(FakeSources(broken=["calendar"])).render()

    assert "0 meetings" not in text
    assert "? meetings" in text
    assert "couldn't check calendar" in text


# --- regressions from the review of this PR ---------------------------------


def test_red_items_reads_the_real_note_layout_where_items_are_unmarked():
    """The vault scopes priority by HEADING, not by marking each item.

    `## 🔴 High - needs my hand this week` with plain items beneath is what the
    real notes look like (reference/vault-recipes.md). Requiring the emoji in
    the item body returned nothing against a real note and silently dropped the
    brief's lead section - and passed, because the fixtures repeated the emoji
    on every item. Invariant 6: follow the vault, not the template.
    """
    note = (
        "# Priorities\n\n"
        "## 🔴 High — needs my hand this week\n"
        "- [ ] land the discovery cutover *(mine)*\n"
        "- [x] send the sponsor note *(mine)*\n"
        "- [ ] unblock the ingestion rerun *(tracking)*\n\n"
        "## 🟡 Medium\n"
        "- [ ] revisit the modeler backlog\n"
    )
    got = red_items(note)
    assert [text for _, text in got] == [
        "land the discovery cutover *(mine)*",
        "unblock the ingestion rerun *(tracking)*",
    ]


def test_a_triage_legend_heading_is_not_a_priority_section():
    """A heading naming several tiers is the legend, and leads no brief."""
    assert red_items("## Triage: 🔴 High / 🟡 Medium / 🟢 Lower\n- [ ] not a priority\n") == []


def test_a_naive_event_start_is_his_local_time_not_the_hosts():
    """`astimezone()` on a naive value adopts the runner's zone.

    A 9am event printed as 2:00 under TZ=UTC and mis-sorted against everything
    else. Naive means his wall clock, which is what the connector hands back.
    """
    from datetime import datetime as _dt

    from daydag.brief import _local

    naive = _local(_dt(2026, 9, 7, 9, 0))
    assert naive is not None
    assert (naive.hour, naive.minute) == (9, 0)


def test_a_parenthesised_bare_url_does_not_capture_its_bracket():
    from daydag.state import _BARE_URL

    found = _BARE_URL.search("see (https://example.com/x) for detail")
    assert found is not None
    assert found["url"] == "https://example.com/x"


# --- second review pass: regressions from the first pass's fixes ------------


def test_a_nested_heading_does_not_end_the_red_section():
    """`### Ingestion` under `## 🔴 High` must not drop the items beneath it.

    The heading-scoping fix cleared the flag on ANY heading, which reintroduced
    the very failure it was written to fix, one level down. Real notes nest.
    """
    note = (
        "## 🔴 High — needs my hand this week\n"
        "- [ ] land the discovery cutover\n"
        "### Ingestion\n"
        "- [ ] unblock the silver trigger\n"
        "## 🟡 Medium\n"
        "- [ ] not this one\n"
    )
    assert [text for _, text in red_items(note)] == [
        "land the discovery cutover",
        "unblock the silver trigger",
    ]


def test_a_nested_heading_does_not_end_a_state_section():
    """State.md is hand-edited; a `### Snoozed` under `## Chase list` is normal."""
    from daydag.state import read_section

    text = (
        "## Chase list\n- VP-Data · rehearsal\n"
        "### Snoozed\n- CTO · vpc move\n"
        "## Watch items\n- nightly\n"
    )
    assert read_section(text, "Chase list") == ["VP-Data · rehearsal", "CTO · vpc move"]


def test_removing_a_bare_url_does_not_strand_its_bracket():
    """Excluding `)` from the URL kept it out of the link but left "(see )"."""
    from daydag.state import split_link

    text, url = split_link("see (https://example.com/7) for detail")
    assert url == "https://example.com/7"
    assert "(" not in text and ")" not in text
    assert text == "see for detail"


@pytest.mark.guardrail
def test_a_calendar_record_without_attendees_is_refused_not_ignored():
    """Silently seeding zero rows makes tomorrow's notes gap impossible.

    The ledger's qualification reads `attendees`; an adapter omitting it seeded
    nothing and the section vanished with no error and no degrade line, while a
    missing `id` raised and was surfaced. The asymmetry was the bug.
    """
    from daydag.brief import _seed_and_gaps
    from daydag.ledger import Ledger

    with pytest.raises(KeyError, match="attendees"):
        _seed_and_gaps(
            Ledger(),
            [{"id": "e1", "start": NOW, "end": NOW, "summary": "standup"}],
            now=NOW,
        )


# --------------------------------------------------------------------------
# shared with the EOD wrap: closed_red_items, first_meeting_line,
# read_vault_note, render_push, Reader - factored out rather than copied.
# --------------------------------------------------------------------------


def test_closed_red_items_is_the_mirror_of_red_items():
    """Same note, same heading-scoped legend rules - the opposite side of the
    checkbox. The wrap's "what closed today" (SPEC 3.5) reads this."""
    from daydag.brief import closed_red_items

    assert [text for _, text in closed_red_items(NOTE_PRIORITIES)] == [
        "🔴 send the pod update *(mine)*",
    ]
    # And `red_items` itself is unaffected by the extraction.
    assert [text for _, text in red_items(NOTE_PRIORITIES)] == [
        "🔴 note to Sponsor on data platform access *(mine)*",
        "🔴 chase the artifacts access call *(mine)*",
    ]


def test_closed_red_items_respects_the_same_heading_scoping():
    """The heading-scoped `## 🔴 High` layout, mirrored for the ticked side."""
    from daydag.brief import closed_red_items

    note = (
        "## 🔴 High — needs my hand this week\n"
        "- [ ] land the discovery cutover *(mine)*\n"
        "- [x] send the sponsor note *(mine)*\n"
        "## 🟡 Medium\n"
        "- [x] not a red item, ignore\n"
    )
    assert [text for _, text in closed_red_items(note)] == ["send the sponsor note *(mine)*"]


def test_closed_red_items_skips_the_triage_legend_too():
    from daydag.brief import closed_red_items

    assert closed_red_items("- [x] triage: 🔴 high, 🟡 medium, 🟢 low") == []


def test_first_meeting_line_is_none_with_no_events():
    from daydag.brief import first_meeting_line

    assert first_meeting_line([]) is None


def test_first_meeting_line_picks_the_earliest_by_instant_not_by_order():
    from daydag.brief import first_meeting_line

    events = [_event("b", "later thing", 15), _event("a", "pod steering", 9)]
    result = first_meeting_line(events)

    assert result is not None
    line, summary = result
    assert summary == "pod steering"
    assert "9:00 pod steering" in line
    assert "https://calendar.example.com/e/a" in line


def test_first_meeting_line_admits_a_missing_link():
    from daydag.brief import first_meeting_line, unsourced_claims

    event = _event("a", "mystery hold", 9)
    del event["permalink"]
    line, _summary = first_meeting_line([event])

    assert unsourced_claims(line) == []


def _raise(exc):
    def _fetch():
        raise exc

    return _fetch


def test_read_vault_note_tells_apart_clean_missing_and_downed():
    from daydag.brief import Reader, read_vault_note

    read = Reader()
    text, missing = read_vault_note(read, lambda: "hello", label="x")
    assert (text, missing, read.unreachable) == ("hello", False, [])

    read = Reader()
    text, missing = read_vault_note(read, _raise(FileNotFoundError()), label="x")
    assert (text, missing, read.unreachable) == ("", True, [])

    read = Reader()
    text, missing = read_vault_note(read, _raise(RuntimeError("down")), label="x")
    assert (text, missing, read.unreachable) == ("", False, ["x"])


def test_render_push_omits_empty_sections_and_names_dead_sources():
    from daydag.brief import Section, render_push

    text = render_push(
        "wrap: 1 closed, 0 moved",
        [Section("closed today (1)", ("- thing (path.md#L1)",)), Section("moved (0)", ())],
        ["the pulse"],
    )

    assert "closed today (1)" in text
    assert "moved (0)" not in text
    assert "couldn't check the pulse" in text
