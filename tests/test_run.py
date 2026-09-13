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

from daydag import run
from daydag.config import Identities

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
        f"SLACK_USER_PRINCIPAL=UPRINCIPAL1\nVAULT_ROOT={tmp_path / 'vault'}\n",
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
