"""The two-phase contract that makes the package runnable.

Python cannot call an MCP connector - the agent can. So a loop runs in two
halves with the fetch in between:

    plan(loop)  ->  the bounded queries      [python]
                    the agent runs them      [MCP]
    render(loop, payloads)  ->  the push     [python]

That is not a workaround for the injection design, it IS the injection design
with a name: every module already took its world as an argument, and nothing
had yet supplied one. It also keeps the connector tripwire true - no client
enters the package, because the package never fetches.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from daydag import brief, run
from daydag.config import Identities
from daydag.state import StateFolder

MONDAY = datetime(2026, 9, 7, 6, 40, tzinfo=UTC)

#: 06:40 PACIFIC on the same Monday - the hour a morning brief actually runs.
#: `MONDAY` above is 06:40 UTC, which is the previous EVENING in Pacific, and
#: the tests that use it are specifically about that convention. A test whose
#: payload carries Sep 7 events needs a clock whose Pacific day is Sep 7 too,
#: or it asserts on a brief for a day those events do not belong to - which is
#: what these did, silently, while `_Payloads.calendar` served any window from
#: one bucket and hid the mismatch.
MONDAY_PT = datetime(2026, 9, 7, 13, 40, tzinfo=UTC)


@pytest.fixture
def identities(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        f"SLACK_USER_PRINCIPAL=UPRINCIPAL1\nEMAIL_PRINCIPAL=principal@x.com\n"
        f"VAULT_ROOT={tmp_path / 'vault'}\n",
        encoding="utf-8",
    )
    (tmp_path / "vault" / "Weekly Notes").mkdir(parents=True)
    return Identities.from_file(env)


# --------------------------------------------------------------------------
# plan: what to fetch, bounded
# --------------------------------------------------------------------------


def test_the_plan_names_every_source_the_loop_reads(identities):
    plan = run.plan("morning", now=MONDAY, identities=identities)

    assert {step.source for step in plan.steps} == {"calendar", "slack", "gmail", "vault"}


def test_the_calendar_step_is_one_day_never_a_range(identities):
    """SPEC's measured rule: a 5-day pull returned 156,681 chars and never
    arrived. The plan carries the same bound the recipe does, so an agent
    following it cannot widen the window by accident."""
    plan = run.plan("morning", now=MONDAY, identities=identities)

    calendar = [step for step in plan.steps if step.source == "calendar"]

    assert len(calendar) == 1
    # The PRINCIPAL's day, not the runner's. 06:40 UTC is the previous evening
    # in Pacific, and `brief` renders that Pacific day - so a plan keyed off
    # `now.date()` fetched one day while the brief reported another, every run
    # between 5pm and midnight Pacific.
    assert calendar[0].detail["day"] == "2026-09-06"


def test_the_slack_step_carries_a_resolved_id_never_a_display_name(identities):
    """`from:@someone` does not fail in Slack search, it silently matches
    nothing - which is the whole reason ids are resolved before the query is
    built rather than after it comes back empty."""
    plan = run.plan("morning", now=MONDAY, identities=identities)

    (slack,) = [step for step in plan.steps if step.source == "slack"]

    query = slack.detail["query"]

    # `<@Uxxxx>` is Slack's id-mention syntax, so an `@` is expected. What must
    # be absent is the DISPLAY-NAME form - `from:@someone` - which does not
    # fail, it silently matches nothing.
    assert "UPRINCIPAL1" in query
    assert "from:@" not in query


def test_an_unknown_loop_is_refused_with_the_ones_that_exist(identities):
    with pytest.raises(run.RunError) as bad:
        run.plan("mornign", now=MONDAY, identities=identities)

    assert "morning" in str(bad.value), "the refusal names what it accepts"


def test_the_plan_round_trips_through_json(identities):
    """The agent reads this off stdout and writes payloads back, so it has to
    survive a serialise/parse with nothing lost."""
    plan = run.plan("morning", now=MONDAY, identities=identities)

    reloaded = json.loads(json.dumps(plan.to_dict()))

    assert reloaded["loop"] == "morning"
    assert len(reloaded["steps"]) == len(plan.steps)


# --------------------------------------------------------------------------
# render: payloads in, one push out
# --------------------------------------------------------------------------


def _payloads(**over):
    base = {"calendar": [], "slack": [], "gmail": [], "vault": ""}
    base.update(over)
    return base


def test_render_returns_the_push_text(identities):
    text = run.render("morning", now=MONDAY, identities=identities, payloads=_payloads())

    assert isinstance(text, str)
    assert text.strip(), "a brief with nothing in it still says something"


def test_a_missing_payload_degrades_rather_than_raising(identities):
    """Guardrail 6. An agent that could not reach gmail omits that key; the
    brief still ships, and says which source it could not check."""
    payloads = _payloads()
    del payloads["gmail"]

    text = run.render("morning", now=MONDAY, identities=identities, payloads=payloads)

    assert "gmail" in text.lower(), f"the unreachable source is not named:\n{text}"


def test_a_payload_of_the_wrong_shape_is_a_degrade_not_a_crash(identities):
    """The agent hands back whatever the connector said. A string where a list
    belongs is a bad fetch, not a reason to lose the other three sources."""
    text = run.render(
        "morning", now=MONDAY, identities=identities, payloads=_payloads(calendar="nope")
    )

    assert text.strip()
    assert "calendar" in text.lower()


def test_render_refuses_a_naive_now(identities):
    """`brief.assemble` refuses one because the overnight cutoff is an hour of
    his day; the runner must not quietly re-add a guess."""
    with pytest.raises(run.RunError):
        run.render(
            "morning",
            now=datetime(2026, 9, 7, 6, 40),
            identities=identities,
            payloads=_payloads(),
        )


# --------------------------------------------------------------------------
# the package still holds no connector
# --------------------------------------------------------------------------


def test_the_runner_fetches_nothing_itself():
    """The tripwire this whole two-phase shape exists to keep true."""
    import ast
    from pathlib import Path

    source = Path(run.__file__).read_text(encoding="utf-8")
    imported = {
        node.module.split(".")[0] if isinstance(node, ast.ImportFrom) else alias.name.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }

    assert not imported & {"requests", "httpx", "urllib", "socket", "subprocess"}


def test_a_json_timestamp_becomes_a_datetime_before_the_brief_sees_it(identities):
    """The seam this whole module is, and the one place it can go wrong quietly.

    `brief._local` returns None unless the value is a `datetime` OBJECT. The
    agent fetches over MCP and hands back JSON, where every instant is a
    string - so passing payloads through untouched made every meeting render
    "all day", with the right title and the wrong time, on every real run.
    Nothing caught it because every test builds datetimes directly.
    """
    payloads = _payloads(
        calendar=[
            {
                "id": "e1",
                "summary": "Pod Steering",
                "start": "2026-09-07T09:00:00-07:00",
                "attendees": ["a@example.com", "b@example.com"],
            }
        ]
    )

    text = run.render("morning", now=MONDAY_PT, identities=identities, payloads=payloads)

    assert "all day" not in text, f"a timed meeting rendered as all-day:\n{text}"
    assert "9:00" in text or "9am" in text.lower(), f"the time is missing:\n{text}"


def test_googles_nested_start_shape_is_understood_too(identities):
    """Calendar hands back `{"start": {"dateTime": ...}}`, not a bare string."""
    payloads = _payloads(
        calendar=[
            {
                "id": "e1",
                "summary": "Pod Steering",
                "start": {"dateTime": "2026-09-07T09:00:00-07:00"},
                "attendees": ["a@example.com", "b@example.com"],
            }
        ]
    )

    text = run.render("morning", now=MONDAY_PT, identities=identities, payloads=payloads)

    assert "all day" not in text, f"the nested shape was not read:\n{text}"


def test_an_unparseable_timestamp_stays_all_day_rather_than_guessing(identities):
    payloads = _payloads(
        calendar=[
            {
                "id": "e1",
                "summary": "Mystery",
                "start": "sometime tuesday",
                "attendees": ["a@example.com", "b@example.com"],
            }
        ]
    )

    text = run.render("morning", now=MONDAY_PT, identities=identities, payloads=payloads)

    assert "Mystery" in text, "the meeting still ships"


def test_the_ledger_gets_real_instants_for_both_ends_of_a_meeting(identities):
    """`start` was parsed and `end` was not - one field, not its twin.

    The ledger reads both, so it raised on the string and the whole notes-gap
    mechanism degraded to "couldn't check the meeting ledger" on every run.
    That is the differentiator quietly not working: a meeting with no row can
    never be surfaced as a gap tomorrow.
    """
    payloads = _payloads(
        calendar=[
            {
                "id": "e1",
                "summary": "Pod Steering",
                "start": "2026-09-07T09:00:00-07:00",
                "end": "2026-09-07T10:00:00-07:00",
                "attendees": ["a@example.com", "b@example.com"],
            }
        ]
    )

    text = run.render("morning", now=MONDAY_PT, identities=identities, payloads=payloads)

    assert "couldn't check the meeting ledger" not in text, text


def test_the_cli_accepts_the_log_flag_the_skill_documents(tmp_path, monkeypatch, capsys):
    """`SKILL.md` tells the agent to pass `--log` so the run remembers. A
    documented flag the parser rejects is the same docs-ahead-of-code failure
    this repo keeps finding, and it would have failed on the first real run."""
    env = tmp_path / ".env"
    env.write_text(
        f"SLACK_USER_PRINCIPAL=UPRINCIPAL1\nVAULT_ROOT={tmp_path / 'vault'}\n", encoding="utf-8"
    )
    (tmp_path / "vault" / "Weekly Notes").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    payloads = '{"calendar": [], "slack": [], "gmail": [], "vault": ""}'
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payloads))

    code = run.main(["render", "morning", "--log", str(tmp_path / "events.db")])

    assert code == 0, capsys.readouterr().err
    assert (tmp_path / "events.db").exists(), "the run did not remember anything"


def test_the_vault_step_names_the_note_once_not_twice(identities):
    """`recipes.weekly_note` already returns a full connector-relative path -
    the vault prefix included, because the Obsidian connector addresses from
    the vault's PARENT. Prepending "Weekly Notes/" to it produced
    `Weekly Notes/Create Music Group/Weekly Notes/0907-0911.md`, which names
    nothing. Found by running `plan` against a real vault and reading it.
    """
    (vault,) = [
        s
        for s in run.plan("morning", now=MONDAY, identities=identities).steps
        if s.source == "vault"
    ]

    path = vault.detail["path"]

    assert path.count("Weekly Notes") == 1, f"the prefix is doubled: {path}"
    assert path.endswith(".md")


def test_the_gmail_step_covers_the_window_the_brief_will_actually_ask_for(identities):
    """The plan is what the agent fetches; `brief` is what consumes it. If the
    two disagree the payload is simply wrong, and silently - `_Payloads.gmail`
    serves whatever was fetched regardless of the query it is handed.

    At 6:40am the brief reports on YESTERDAY's meetings and asks gmail for
    `after:<yesterday>`. The plan asked for today only, so a note for a
    meeting that ended at 16:30 the previous day was never fetched, and its
    meeting was reported as having no notes. Found on real mail: ML Model
    Review's note existed and arrived at 17:12 PDT; the query excluded it.
    """
    plan = run.plan("morning", now=MONDAY, identities=identities)

    (gmail,) = [s for s in plan.steps if s.source == "gmail"]
    query = gmail.detail["query"]

    # MONDAY is 06:40 UTC = the previous evening in Pacific, so the window the
    # brief opens starts the day before that again.
    assert "before:" not in query, (
        f"a closed day-window drops a note that arrives after it: {query}"
    )
    assert "2026/09/0" in query, f"the window does not reach back to yesterday: {query}"


# --------------------------------------------------------------------------
# the plan must fetch the window the loop will actually ask for (#94)
# --------------------------------------------------------------------------


def test_the_eod_plan_fetches_tomorrow_not_today(identities):
    """`eod_wrap` reads exactly one calendar window and it is TOMORROW's.

    The plan fetched today for every loop, because it never looked at `loop`
    at all - and `_Payloads.calendar` then answered the tomorrow request from
    the today bucket. The wrap printed this morning's 8:15 standup as
    tomorrow's first meeting, and nothing failed.
    """
    (calendar,) = [
        s
        for s in run.plan("eod", now=MONDAY_PT, identities=identities).steps
        if s.source == "calendar"
    ]

    assert calendar.detail["day"] == "2026-09-08", "the wrap previews tomorrow, not today"


def test_the_week_ahead_plan_fetches_seven_days_of_next_week(identities):
    """SKILL.md advertises "next 7 days". The plan fetched one, so the Monday
    prep queue could only ever see a single day - and that day was in the past
    relative to the week being previewed, so the loop rendered nothing at all.
    """
    days = [
        s.detail["day"]
        for s in run.plan("week-ahead", now=MONDAY_PT, identities=identities).steps
        if s.source == "calendar"
    ]

    # MONDAY_PT is Mon Sep 7, so the week ahead is Mon Sep 14 - Sun Sep 20.
    assert days == [f"2026-09-{d}" for d in range(14, 21)], days


def test_the_morning_plan_still_fetches_exactly_one_day(identities):
    """The loop that already worked must not change shape."""
    days = [
        s.detail["day"]
        for s in run.plan("morning", now=MONDAY_PT, identities=identities).steps
        if s.source == "calendar"
    ]

    assert days == ["2026-09-07"]


def test_a_window_the_plan_never_fetched_comes_back_empty_not_wrong(identities):
    """The tripwire under all of the above.

    Wrong data in a push is worse than a missing section, because a missing
    section says so. A consumer asking for a day that was never fetched used to
    be served a different day's events with no way to tell.
    """
    payloads = _payloads(
        calendar=[
            {
                "id": "today1",
                "summary": "Today's standup",
                "start": "2026-09-07T08:15:00-07:00",
                "end": "2026-09-07T08:30:00-07:00",
                "attendees": ["a@example.com", "b@example.com"],
            }
        ]
    )

    text = run.render("eod", now=MONDAY_PT, identities=identities, payloads=payloads)

    assert "Today's standup" not in text, f"today's meeting was served as tomorrow's:\n{text}"


def test_the_right_day_is_served_when_the_payload_carries_several(identities):
    """The other half - filtering must not mean filtering everything out."""
    payloads = _payloads(
        calendar=[
            {
                "id": "today1",
                "summary": "Today's standup",
                "start": "2026-09-07T08:15:00-07:00",
                "end": "2026-09-07T08:30:00-07:00",
                "attendees": ["a@example.com", "b@example.com"],
            },
            {
                "id": "tmrw1",
                "summary": "Tomorrow's kickoff",
                "start": "2026-09-08T09:00:00-07:00",
                "end": "2026-09-08T09:30:00-07:00",
                "attendees": ["a@example.com", "b@example.com"],
            },
        ]
    )

    text = run.render("eod", now=MONDAY_PT, identities=identities, payloads=payloads)

    assert "Tomorrow's kickoff" in text, f"tomorrow's meeting was dropped:\n{text}"
    assert "Today's standup" not in text, f"today's meeting leaked into tomorrow:\n{text}"


def test_an_unplaceable_event_is_still_offered_rather_than_dropped(identities):
    """An untimed meeting is still a meeting, and losing its ledger row loses a
    notes gap permanently. It is returned for every window rather than for
    none; `Ledger.seed_day` keys on (id, start) and absorbs the repeat."""
    payloads = _payloads(
        calendar=[
            {
                "id": "e1",
                "summary": "Mystery",
                "start": "sometime tuesday",
                "attendees": ["a@example.com", "b@example.com"],
            }
        ]
    )

    text = run.render("eod", now=MONDAY_PT, identities=identities, payloads=payloads)

    assert "Mystery" in text, f"an unplaceable meeting was silently dropped:\n{text}"


# --------------------------------------------------------------------------
# the weekly note has three states, not two (#97)
# --------------------------------------------------------------------------


def test_a_note_that_was_never_written_says_so(identities):
    """`brief.read_vault_note` splits three ways on exception type, and this
    layer could only ever produce two of them. The missing one is the state the
    vault is actually in: the note is hand-written and the series has had a gap
    for weeks, so every real run meets it."""
    payloads = _payloads()
    payloads["vault"] = None

    text = run.render("morning", now=MONDAY_PT, identities=identities, payloads=payloads)

    assert "no weekly note" in text, f"a note that does not exist went unmentioned:\n{text}"


def test_an_unreachable_vault_does_not_read_as_a_missing_note(identities):
    """An absent note is a FACT about the week; a downed source is a DEGRADE.
    They must not render the same, or a connector outage looks like "he hasn't
    planned the week" and vice versa."""
    payloads = _payloads()
    del payloads["vault"]

    text = run.render("morning", now=MONDAY_PT, identities=identities, payloads=payloads)

    assert "couldn't check the weekly note" in text, text
    assert "no weekly note" not in text, f"a degrade was reported as a fact:\n{text}"


def test_an_empty_note_is_a_note_that_was_read(identities):
    """The distinction `null` exists to make. "" means the file is there and
    has nothing in it, which is not the same as it never having been written -
    and sending "" was the only way to say "missing" before, so it said
    nothing at all."""
    payloads = _payloads(vault="")

    text = run.render("morning", now=MONDAY_PT, identities=identities, payloads=payloads)

    assert "no weekly note" not in text, f"an empty note was reported as absent:\n{text}"


def test_a_null_note_never_renders_as_the_text_None(identities):
    """`str(None)` is "None", so the body of the brief used to carry the note's
    content as that literal four-letter string."""
    payloads = _payloads()
    payloads["vault"] = None

    text = run.render("morning", now=MONDAY_PT, identities=identities, payloads=payloads)

    assert "None" not in text, f"the note body rendered as the string None:\n{text}"


# --------------------------------------------------------------------------
# every loop the skill advertises is reachable (#95)
# --------------------------------------------------------------------------


def test_every_advertised_loop_can_be_planned(identities):
    """`SKILL.md` advertised seven loops and the runner implemented three.

    `prep.py`, `ingestion.py` and `pulse.py` were built and tested with no way
    to reach them, so asking for "chase" got a refusal naming the other three.
    This is the tripwire: the skill and the runner agree, or this fails.
    """
    for loop in ("morning", "eod", "week-ahead", "prep", "ingest", "chase", "ship"):
        assert run.plan(loop, now=MONDAY_PT, identities=identities).steps or loop == "ship"


def test_every_advertised_loop_renders_something(identities):
    for loop in run.LOOPS:
        text = run.render(loop, now=MONDAY_PT, identities=identities, payloads=_payloads())
        assert text.strip(), f"{loop} rendered nothing at all"


def test_ship_asks_for_no_connector_fetch(identities):
    """`pulse` reads the git mirrors on disk. Emitting four connector steps
    whose payloads it ignores would be a round-trip for nothing."""
    (step,) = run.plan("ship", now=MONDAY_PT, identities=identities).steps

    assert step.source == "git"


def test_the_loops_that_ignore_the_calendar_do_not_fetch_it(identities):
    for loop in ("ingest", "chase", "ship"):
        sources = {s.source for s in run.plan(loop, now=MONDAY_PT, identities=identities).steps}
        assert "calendar" not in sources, f"{loop} fetches a calendar it never reads"


def test_chase_reads_the_file_he_corrects_by_hand(identities, tmp_path):
    """CLAUDE.md: a hand edit is an event and WINS over derived state. A chaser
    that rebuilt the list from Slack each run would undo every correction."""
    folder = StateFolder.create(tmp_path / "DayDAG")
    folder.write_state(chase=[{"owner": "VP-Data", "ask": "the compute consolidation plan"}])

    text = run.render(
        "chase", now=MONDAY_PT, identities=identities, payloads=_payloads(), state=folder
    )

    assert "compute consolidation" in text, f"State.md was not read:\n{text}"


def test_ingest_names_the_items_it_could_not_place(identities):
    """`classify_items` reads `item_id`. Handing it `id` made every item
    unplaceable with a BLANK name to show for it - the guess this section
    exists to refuse, with nothing he could act on."""
    payloads = _payloads(
        gmail=[{"id": "m1", "subject": "Re: advance mapping"}, {"id": "m2", "subject": "hello"}]
    )

    text = run.render("ingest", now=MONDAY_PT, identities=identities, payloads=payloads)

    assert "m1" in text and "m2" in text, f"unplaced items have no names:\n{text}"


def test_ship_without_a_pulse_degrades_rather_than_raising(identities):
    text = run.render("ship", now=MONDAY_PT, identities=identities, payloads=_payloads())

    assert "couldn't check" in text


# --------------------------------------------------------------------------
# the adapter implements what the protocol declares (#95)
# --------------------------------------------------------------------------


def test_the_payload_adapter_serves_every_source_method(identities):
    """`eod_wrap` reads next week's plan through `sources.vault_note`, which
    the `Sources` protocol never declared - so this adapter never implemented
    it, both reads raised, and the whole "friday - weekly-planning outcome"
    section was dropped on every real run. BOTH test doubles have the method,
    which is precisely why the suite stayed green: the fake was more capable
    than the thing it stood in for.
    """
    for name in ("calendar", "slack", "gmail", "weekly_note", "vault_note"):
        assert hasattr(brief.Sources, name), f"the protocol lost {name}"
        assert callable(getattr(run._Payloads(_payloads()), name, None)), (
            f"_Payloads does not implement {name}, so every read of it degrades"
        )


def test_the_friday_section_renders_when_its_notes_are_fetched(identities):
    friday = datetime(2026, 9, 11, 17, 0, tzinfo=UTC)
    plan = run.plan("eod", now=friday, identities=identities)
    (step,) = [s for s in plan.steps if s.source == "vault_notes"]

    payloads = _payloads()
    payloads["vault_notes"] = dict.fromkeys(step.detail["paths"])

    text = run.render("eod", now=friday, identities=identities, payloads=payloads)

    assert "weekly-planning outcome" in text, f"the friday section never renders:\n{text}"
    assert "hasn't landed yet" in text


# --------------------------------------------------------------------------
# prep, for a meeting he named
# --------------------------------------------------------------------------


def _meeting(summary, *attendees, day="2026-09-07", at="15:00"):
    return {
        "id": summary.lower().replace(" ", "-"),
        "summary": summary,
        "start": f"{day}T{at}:00-07:00",
        "end": f"{day}T{at[:2]}:45:00-07:00",
        "attendees": list(attendees),
        "response_status": "accepted",
        "permalink": f"https://cal/{summary[:6]}",
    }


def test_a_named_prep_picks_the_meeting_he_asked_for(identities):
    """`prep` alone takes the next qualifying meeting. Named, it takes the one
    named - even when another sorts earlier."""
    payloads = _payloads(
        calendar=[
            _meeting("Pod Steering", "a@x.com", "b@x.com", at="11:00"),
            _meeting("Finance x Data meeting", "a@x.com", "c@x.com", at="16:00"),
        ]
    )

    text = run.render(
        "prep",
        now=MONDAY_PT,
        identities=identities,
        payloads=payloads,
        selector="finance x data",
    )

    assert "Finance x Data" in text, text
    assert "Pod Steering" not in text, f"it prepped the sooner meeting instead:\n{text}"


def test_a_named_prep_that_matches_two_asks_rather_than_guessing(identities):
    """Invariant 5, and the common case: a recurring 1:1 appears twice in any
    horizon worth searching. Prep for the wrong one is worse than none."""
    payloads = _payloads(
        calendar=[
            _meeting("1:1 | Nitin x Wren", "a@x.com", "wren.alder@x.com", at="11:00"),
            _meeting(
                "1:1 | Nitin x Wren", "a@x.com", "wren.alder@x.com", day="2026-09-09", at="11:00"
            ),
        ]
    )

    text = run.render(
        "prep", now=MONDAY_PT, identities=identities, payloads=payloads, selector="wren"
    )

    assert "which one" in text.lower(), text


def test_a_named_prep_that_matches_nothing_says_what_it_searched(identities):
    text = run.render(
        "prep",
        now=MONDAY_PT,
        identities=identities,
        payloads=_payloads(calendar=[_meeting("Pod Steering", "a@x.com", "b@x.com")]),
        selector="nobody by that name",
    )

    assert "nobody by that name" in text.lower(), text


def test_a_named_standup_is_prepped_even_though_a_ping_never_would_be(identities):
    """`prep_worthy` answers "is this worth interrupting him unprompted". He
    asked - so the gate does not apply, and refusing would be the tool arguing
    with the request."""
    payloads = _payloads(calendar=[_meeting("DE Standup", "a@x.com", "b@x.com", at="11:00")])

    text = run.render(
        "prep", now=MONDAY_PT, identities=identities, payloads=payloads, selector="de standup"
    )

    assert "DE Standup" in text, text
    assert "nothing coming up" not in text, text


# --------------------------------------------------------------------------
# what the review of the selector found, at the runner
# --------------------------------------------------------------------------


def test_a_named_prep_plan_fetches_seven_days_in_his_zone_and_only_what_it_reads(identities):
    """Eight windows for a seven-day horizon, in hardcoded Pacific, plus gmail
    and vault steps `_prep` never reads. Now: seven, in `timezone_for`, and only
    the two sources the loop consumes."""
    steps = run.plan("prep", now=MONDAY_PT, identities=identities, selector="wren").steps

    days = [s.detail["day"] for s in steps if s.source == "calendar"]
    assert days == [f"2026-09-{d:02d}" for d in range(7, 14)], days
    assert {s.source for s in steps} == {"calendar", "slack"}


def test_a_shaped_attendee_does_not_break_the_ping_rules(identities):
    """The shape review caught: a display name folded into the address made
    every internal colleague read as external. Google's dict form goes straight
    through and is split at the seam."""
    from daydag.prep import Audience

    payloads = _payloads(
        calendar=[
            {
                "id": "e1",
                "summary": "Roadmap review",
                "start": "2026-09-07T11:00:00-07:00",
                "end": "2026-09-07T12:00:00-07:00",
                "attendees": [
                    {"email": "principal@x.com", "displayName": "Prin Cipal"},
                    {"email": "wren@x.com", "displayName": "Wren Alder"},
                    "Jo Strauss <jo@x.com>",
                ],
                "response_status": "accepted",
                "permalink": "https://cal/e1",
            }
        ]
    )
    from daydag.ledger import Ledger

    ledger = Ledger()
    ledger.seed_day(run._seedable(payloads))
    (row,) = ledger.open_rows()

    assert row.attendees == ["principal@x.com", "wren@x.com", "jo@x.com"]
    assert row.attendee_names == ["Prin Cipal", "Wren Alder", "Jo Strauss"]
    audience = Audience(leadership=frozenset({"jo@x.com"}), internal_domains=frozenset({"x.com"}))
    assert not audience.has_external(row.attendees), "a colleague read as external"
    assert audience.has_leadership(row.attendees), "leadership stopped matching"


def test_a_named_prep_does_not_persist_the_week_it_fetched(identities, tmp_path):
    """A prep is a question, not a day's seeding. Remembering its seven fetched
    days made a meeting cancelled after the snapshot a permanent notes gap."""
    from daydag.state import EventLog

    log = tmp_path / "events.db"
    payloads = _payloads(
        calendar=[_meeting("Finance x Data meeting", "a@x.com", "b@x.com", day="2026-09-10")]
    )
    run.render(
        "prep", now=MONDAY_PT, identities=identities, payloads=payloads, log=log, selector="finance"
    )

    assert EventLog.open(log).recorded("meeting") == [], "a named prep wrote future meetings"


def test_a_trailing_for_is_refused_not_silently_dropped(tmp_path, monkeypatch, capsys):
    """`render prep --for` with the name forgotten used to prep the next
    qualifying meeting and say nothing - the wrong-meeting failure."""
    env = tmp_path / ".env"
    env.write_text(
        f"SLACK_USER_PRINCIPAL=UPRINCIPAL1\nEMAIL_PRINCIPAL=p@x.com\n"
        f"VAULT_ROOT={tmp_path / 'vault'}\n"
    )
    (tmp_path / "vault" / "Weekly Notes").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO('{"calendar":[],"slack":[]}'))

    assert run.main(["render", "prep", "--for"]) == 2
    assert "--for needs" in capsys.readouterr().err


def test_a_blank_selector_is_one_line_on_stderr_not_a_traceback(identities):
    """CLAUDE.md rule 6. `select` raises ValueError; the runner turns it into
    the RunError path every other refusal takes."""
    with pytest.raises(run.RunError):
        run.render(
            "prep", now=MONDAY_PT, identities=identities, payloads=_payloads(), selector="  "
        )


# --------------------------------------------------------------------------
# what the review of the simplify pass found
# --------------------------------------------------------------------------


def test_two_meetings_at_the_same_time_cluster_instead_of_crashing():
    """`sorted((start, end, event))` compared the event dicts when the instants
    tied - TypeError on exactly the stacked-at-11:00 case, and `_clash_lines`
    sits outside `read()`, so the whole Sunday push died."""
    from datetime import UTC, datetime

    from daydag.brief import overlap_clusters

    at = datetime(2026, 9, 17, 18, 0, tzinfo=UTC)
    a = {"summary": "Finance x Data", "start": at, "end": at.replace(hour=19)}
    b = {"summary": "Nitin x Tony", "start": at, "end": at.replace(hour=19)}

    clusters = overlap_clusters([a, b])

    assert len(clusters) == 1 and len(clusters[0]) == 2


def test_a_meeting_with_a_start_but_no_end_can_still_sit_inside_another():
    """Same asymmetry as `_overlap_flags`, so the two detectors agree: a
    tentative invite with no end that begins inside a hold is a clash."""
    from datetime import UTC, datetime

    from daydag.brief import overlap_clusters

    hold = {
        "summary": "Hold",
        "start": datetime(2026, 9, 17, 16, 0, tzinfo=UTC),
        "end": datetime(2026, 9, 17, 19, 0, tzinfo=UTC),
    }
    tentative = {"summary": "Maybe", "start": datetime(2026, 9, 17, 18, 0, tzinfo=UTC)}

    assert len(overlap_clusters([hold, tentative])) == 1


def test_chase_with_write_state_does_not_wipe_the_notes_gaps_a_morning_wrote(identities, tmp_path):
    """The ledger gate handed chase an EMPTY ledger, and `_project` still ran,
    writing `notes_gaps=[]` over the section the morning had just recorded."""
    log = tmp_path / "events.db"
    yesterday = _meeting("Pod Steering", "a@x.com", "b@x.com", day="2026-09-06", at="09:00")
    run.render(
        "morning",
        now=MONDAY_PT,
        identities=identities,
        payloads=_payloads(calendar=[yesterday]),
        log=log,
        write_state=True,
    )
    (state,) = [p for p in (tmp_path / "vault").rglob("State.md")]
    assert "Pod Steering" in state.read_text(), "the morning did not record the gap"

    run.render(
        "chase",
        now=MONDAY_PT,
        identities=identities,
        payloads=_payloads(),
        log=log,
        write_state=True,
    )

    assert "Pod Steering" in state.read_text(), "chase --write-state erased the notes gaps"


def test_ingest_lines_are_bulleted_like_every_other_section(identities):
    text = run.render(
        "ingest",
        now=MONDAY_PT,
        identities=identities,
        payloads=_payloads(gmail=[{"id": "m1", "subject": "x"}, {"id": "m2"}]),
    )

    body = [
        line
        for line in text.splitlines()[1:]
        if line and not line.startswith(("placed", "unplaced"))
    ]
    assert body and all(line.startswith("- ") for line in body), text


def test_a_carried_chase_item_keeps_its_quote_and_permalink(identities, tmp_path):
    """House rule 1. Bare `owner: ask` dropped both, and was invisible to
    `brief.unsourced_claims` for want of a bullet."""
    from daydag.state import EventLog, StateFolder

    folder = StateFolder.create(tmp_path / "vault" / "DayDAG")
    folder.write_state(chase=[{"owner": "VP-Data", "ask": "the compute plan"}])
    log = tmp_path / "events.db"
    EventLog.open(log).record(
        "carry_forward",
        sensitivity="normal",
        owner="VP-AI",
        ask="confirm the cutover",
        quote="we cut over friday iirc",
        permalink="https://slack/x",
        key="k9",
    )

    text = run.render("chase", now=MONDAY_PT, identities=identities, payloads=_payloads(), log=log)

    assert "https://slack/x" in text and "cut over friday" in text, text


def test_a_slack_message_with_null_text_is_not_quoted_as_the_word_None(identities):
    payloads = _payloads(
        calendar=[_meeting("1:1 | Nitin x Wren", "a@x.com", "wren@x.com", at="11:00")],
        slack=[{"text": None, "permalink": "https://slack/file-only"}],
    )

    text = run.render("prep", now=MONDAY_PT, identities=identities, payloads=payloads)

    assert "None" not in text, text


def test_a_room_never_reaches_the_stored_attendees(identities):
    """Rooms were filtered only inside the qualifying COUNT; stored, the room's
    domain read as an outside party to has_external and as the second person
    of a 1:1 (#116)."""
    from daydag.ledger import Ledger

    ledger = Ledger()
    ledger.seed_day(
        run._seedable(
            _payloads(
                calendar=[
                    _meeting(
                        "Finance x Data",
                        "principal@x.com",
                        "yoni@x.com",
                        "c_1@resource.calendar.google.com",
                    )
                ]
            )
        )
    )
    (row,) = ledger.open_rows()

    assert row.attendees == ["principal@x.com", "yoni@x.com"]


def test_a_past_meeting_teaches_the_directory_and_a_future_one_does_not(identities, tmp_path):
    from daydag.people import People
    from daydag.state import EventLog

    log = tmp_path / "events.db"
    payloads = _payloads(
        calendar=[
            _meeting(
                "Yesterday sync", "principal@x.com", "wren@x.com", day="2026-09-06", at="09:00"
            ),
            _meeting("Tomorrow sync", "principal@x.com", "bo@x.com", day="2026-09-08", at="09:00"),
        ]
    )
    run.render("morning", now=MONDAY_PT, identities=identities, payloads=payloads, log=log)

    directory = People(EventLog.open(log))
    assert directory.resolve("wren@x.com") is not None, "a past meeting taught nothing"
    assert directory.resolve("bo@x.com") is None, "a meeting not yet held was recorded as met"
    assert directory.resolve("principal@x.com") is None, "he was added to his own directory"


# --------------------------------------------------------------------------
# the event log does not grow with every render (#114) or get read whole (#115)
# --------------------------------------------------------------------------


def _recorded_meetings(log):
    from daydag.state import EventLog

    return EventLog.open(log).recorded("meeting")


def test_rendering_the_same_day_repeatedly_remembers_it_once(identities, tmp_path):
    """Morning, a re-run, then eod: three renders of one day appended the
    day's meetings three times, and every later run replayed all of it."""
    log = tmp_path / "events.db"
    payloads = _payloads(calendar=[_meeting("Pod Steering", "a@x.com", "b@x.com", at="09:00")])
    for loop in ("morning", "morning", "eod"):
        run.render(loop, now=MONDAY_PT, identities=identities, payloads=payloads, log=log)

    assert len(_recorded_meetings(log)) == 1


def test_only_what_the_ledger_accepted_is_remembered(identities, tmp_path):
    """A solo hold and a declined invite never enter the ledger, so a dedupe
    keyed on the ledger never saw them - and they were re-appended on every
    render, forever. The log holds what a replay can use, nothing else."""
    log = tmp_path / "events.db"
    payloads = _payloads(
        calendar=[
            _meeting("Pod Steering", "a@x.com", "b@x.com", at="09:00"),
            {**_meeting("Focus", "me@x.com", at="11:00"), "organizer_is_self": True},
            {
                **_meeting("Declined sync", "a@x.com", "b@x.com", at="14:00"),
                "response_status": "declined",
            },
        ]
    )
    for _ in range(3):
        run.render("morning", now=MONDAY_PT, identities=identities, payloads=payloads, log=log)

    assert [m["summary"] for m in _recorded_meetings(log)] == ["Pod Steering"]


def test_an_all_day_entry_neither_crashes_the_render_nor_gets_remembered(identities, tmp_path):
    """Google's all-day start is `{"date": ...}`, which `timed` leaves alone and
    which cannot be hashed into a ledger key. A holiday on the calendar took
    the whole morning render down AFTER the brief was assembled."""
    log = tmp_path / "events.db"
    holiday = {
        "id": "holiday-1",
        "summary": "Labor Day",
        "start": {"date": "2026-09-07"},
        "end": {"date": "2026-09-08"},
        "attendees": ["a@x.com", "b@x.com"],
        "response_status": "accepted",
    }
    payloads = _payloads(calendar=[holiday, _meeting("Pod Steering", "a@x.com", "b@x.com")])

    text = run.render("morning", now=MONDAY_PT, identities=identities, payloads=payloads, log=log)

    assert "Pod Steering" in text
    assert [m["summary"] for m in _recorded_meetings(log)] == ["Pod Steering"]


def test_a_raw_calendar_record_carrying_a_kind_field_is_remembered_and_replayed(
    identities, tmp_path
):
    """Every raw Google event has `kind`, and so did `record(kind, **payload)`:
    the collision was a TypeError on the first remember."""
    log = tmp_path / "events.db"
    payloads = _payloads(
        calendar=[{**_meeting("Pod Steering", "a@x.com", "b@x.com"), "kind": "calendar#event"}]
    )
    run.render("morning", now=MONDAY_PT, identities=identities, payloads=payloads, log=log)
    run.render("morning", now=MONDAY_PT, identities=identities, payloads=payloads, log=log)

    rows = _recorded_meetings(log)
    assert [r["kind"] for r in rows] == ["calendar#event"]


@pytest.mark.parametrize(
    ("loop", "day", "extra"),
    [
        ("prep", "2026-09-10", {"selector": "steering"}),
        ("eod", "2026-09-08", {}),
        ("week-ahead", "2026-09-14", {}),
    ],
)
def test_a_meeting_in_the_future_is_not_persisted_by_the_loop_that_fetched_it(
    identities, tmp_path, loop, day, extra
):
    """Prep fetches seven days ahead, eod fetches TOMORROW, week-ahead next
    week. Remembered now, each is a phantom notes gap if cancelled before it
    happens. The morning that seeds a day is its writer - decided from the
    row's date, not from which loop ran."""
    log = tmp_path / "events.db"
    payloads = _payloads(calendar=[_meeting("Pod Steering", "a@x.com", "b@x.com", day=day)])

    run.render(loop, now=MONDAY_PT, identities=identities, payloads=payloads, log=log, **extra)

    assert _recorded_meetings(log) == []


def test_a_prep_that_sees_today_remembers_today(identities, tmp_path):
    """The rule is the row's date, not the loop: a named prep whose window
    starts now sees today's meetings too, and today is fair to remember."""
    log = tmp_path / "events.db"
    payloads = _payloads(
        calendar=[
            _meeting("Pod Steering", "a@x.com", "b@x.com", at="15:00"),
            _meeting("Finance x Data", "a@x.com", "c@x.com", day="2026-09-10"),
        ]
    )

    run.render(
        "prep", now=MONDAY_PT, identities=identities, payloads=payloads, log=log, selector="finance"
    )

    assert [m["summary"] for m in _recorded_meetings(log)] == ["Pod Steering"]


# --------------------------------------------------------------------------
# the directory decides "leadership in the room" for the week-ahead too (#119)
# --------------------------------------------------------------------------


def test_the_monday_prep_queue_reads_leadership_from_the_directory(identities, tmp_path):
    """A meeting with no 1:1 pattern and no steering keyword qualifies only
    because someone senior is in it. The week-ahead built its Audience from
    .env, so a leader who existed only in the directory never qualified it."""
    from datetime import timedelta

    from daydag import week_ahead
    from daydag.people import STATED, People
    from daydag.prep import Audience
    from daydag.state import EventLog

    log = tmp_path / "events.db"
    People(EventLog.open(log)).remember("ceo", email="jo@x.com", leadership=True, source=STATED)
    sunday = MONDAY_PT - timedelta(days=1)  # Sun Sep 6 -> the week ahead is Mon Sep 7 - Sun Sep 13
    review = _meeting(
        "Roadmap review", "principal@x.com", "jo@x.com", "wren@x.com", day="2026-09-07"
    )
    payloads = _payloads(calendar=[review])

    with_directory = week_ahead.assemble(
        now=sunday,
        sources=run._Payloads(payloads),
        identities=identities,
        audience=Audience.from_directory(People(EventLog.open(log)), identities),
    )
    without = week_ahead.assemble(
        now=sunday, sources=run._Payloads(payloads), identities=identities
    )

    assert [p.meeting for p in with_directory.monday_preps] == ["Roadmap review"]
    assert without.monday_preps == ()


def test_render_hands_the_week_ahead_the_directory_audience(identities, tmp_path):
    from datetime import timedelta

    from daydag.people import STATED, People
    from daydag.state import EventLog

    log = tmp_path / "events.db"
    People(EventLog.open(log)).remember("ceo", email="jo@x.com", leadership=True, source=STATED)
    review = _meeting(
        "Roadmap review", "principal@x.com", "jo@x.com", "wren@x.com", day="2026-09-07"
    )

    text = run.render(
        "week-ahead",
        now=MONDAY_PT - timedelta(days=1),
        identities=identities,
        payloads=_payloads(calendar=[review]),
        log=log,
    )

    assert "Roadmap review" in text
