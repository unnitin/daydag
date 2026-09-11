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

    text = run.render("morning", now=MONDAY, identities=identities, payloads=payloads)

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

    text = run.render("morning", now=MONDAY, identities=identities, payloads=payloads)

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

    text = run.render("morning", now=MONDAY, identities=identities, payloads=payloads)

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

    text = run.render("morning", now=MONDAY, identities=identities, payloads=payloads)

    assert "couldn't check the meeting ledger" not in text, text
