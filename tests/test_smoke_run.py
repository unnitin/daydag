"""The connector smoke run (issue #3): what did we reach, what did we skip.

Distinct from `tests/test_smoke.py`, which asks whether the package imports.
This one asks whether the *world* answered, and it is written against the one
finding from the #2 audit that changes the shape of the answer:

    an overflowing query returns NOTHING, and nothing reads as a quiet day.

Three sources blew the output limit during that audit - Calendar at 156,681
chars for five days, Jira at 125,231 for a 14-day query, the Jira project list
at 60,000. None of them raised. A smoke test that only asked "did the call
succeed" would have gone green while every real query in the brief came back
empty, and the brief would have said the day was quiet. So every assertion here
is about a *plausible shape coming back*, never about the absence of an
exception.

No test in this file touches the network. Probes are injected callables and
their outcomes are fixtures, which is also the design constraint on the module:
`smoke.py` holds no client, so there is nothing for it to reach even by
accident. `test_the_module_carries_no_client_of_its_own` asserts that.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from daydag import smoke
from daydag.smoke import (
    AUTH,
    CHECKS,
    FETCH,
    NOT_CONNECTED,
    OUTPUT_CEILING_CHARS,
    OVERFLOW,
    REACHED,
    SHAPE,
    SKIPPED,
    SmokeReport,
)
from daydag.voice import voice_violations

# --------------------------------------------------------------------------
# fixtures: one plausible payload per check, so a test can break exactly one
# --------------------------------------------------------------------------

#: What each probe returns when the source is genuinely reachable. Shapes are
#: taken from the audit, trimmed to the fields the plausibility checks read.
HEALTHY: dict[str, object] = {
    "calendar": {"events": [{"id": "e1", "summary": "pod standup", "start": "2026-09-08T09:00"}]},
    "gmail": {"messages": [{"id": "m1", "subject": 'Notes: "pod standup" Sep 8, 2026'}]},
    "slack": {"id": "U000000000", "name": "principal"},
    "notion": {"id": "00000000-0000-0000-0000-000000000000", "type": "person"},
    "jira": {"issues": [{"key": "CDI-101", "status": "In Progress"}]},
    "github api": {"repositories": [{"full_name": "org/createos-ai-platform"}]},
    "github mirror": True,
    "databricks": {"rows": [[1]]},
}


def probes(**overrides: object) -> dict[str, object]:
    """Every wired probe healthy, except the ones named."""

    def constant(value: object):
        return lambda: value

    wired = {name: constant(payload) for name, payload in HEALTHY.items()}
    wired.update(overrides)
    return wired


def result_for(report: SmokeReport, name: str):
    found = [result for result in report.results if result.name == name]
    assert found, f"the run produced no row for {name!r}"
    return found[0]


def raises(error: Exception):
    def probe():
        raise error

    return probe


# --------------------------------------------------------------------------
# 1. one invocation touches every source
# --------------------------------------------------------------------------


def test_one_run_reports_a_row_for_every_source():
    """The point of the run is completeness: a source with no row was not checked."""
    report = smoke.run(probes())

    assert [result.name for result in report.results] == [check.name for check in CHECKS]
    assert {result.source for result in report.results} == {
        "calendar",
        "gmail",
        "slack",
        "notion",
        "jira",
        "github",
        "databricks",
        "granola",
    }


def test_every_check_declares_what_reached_means_and_how_it_is_bounded():
    """A check with no stated bound is the unbounded query that returns nothing."""
    for check in CHECKS:
        assert check.reaches, f"{check.name} does not say what reaching it looks like"
        assert check.bound, f"{check.name} does not say what bounds it"


def test_a_healthy_run_reaches_everything_that_is_connected():
    report = smoke.run(probes())

    assert report.reached == tuple(HEALTHY)
    assert report.skipped == ()
    assert report.not_connected == ("granola",)


# --------------------------------------------------------------------------
# 2. the done-when: a broken connector produces a report, not an exception
# --------------------------------------------------------------------------


@pytest.mark.guardrail
@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("connection reset by peer"),
        TimeoutError("timed out"),
        KeyError("token"),
        ValueError(""),
    ],
)
def test_a_deliberately_broken_connector_reports_rather_than_raising(failure: Exception):
    """Issue #3's done-when, and guardrail 6 in one line.

    Whatever a connector throws - and an MCP client throws whatever it likes -
    the run returns a report. The healthy sources still say they were reached,
    because a brief that ships six sources and one apology is the whole point.
    """
    report = smoke.run(probes(gmail=raises(failure)))

    assert result_for(report, "gmail").status == SKIPPED
    assert "calendar" in report.reached
    assert "gmail" not in report.reached
    assert report.render(), "a failing connector emptied the report"


@pytest.mark.guardrail
def test_the_broken_connector_is_named_in_the_line_the_brief_ships():
    """Guardrail 6 verbatim: a one-line "couldn't check X", never a stall."""
    report = smoke.run(probes(databricks=raises(RuntimeError("workspace unreachable"))))

    lines = report.degrade_lines()
    assert lines == ["couldn't check databricks - workspace unreachable"]
    assert all(line.startswith("couldn't check ") for line in lines)


def test_every_probe_runs_exactly_once_even_when_it_fails():
    """No silent retry. A retry hides a flaky source and doubles the 6:40am wait."""
    calls: list[str] = []

    def counted(name: str, payload: object):
        def probe():
            calls.append(name)
            if payload is None:
                raise RuntimeError("down")
            return payload

        return probe

    smoke.run(probes(calendar=counted("calendar", None), gmail=counted("gmail", HEALTHY["gmail"])))

    assert calls == ["calendar", "gmail"]


# --------------------------------------------------------------------------
# 3. overflow, which is the case a plain auth check misses entirely
# --------------------------------------------------------------------------


@pytest.mark.guardrail
@pytest.mark.parametrize(
    ("name", "measured"),
    [("calendar", 156_681), ("jira", 125_231), ("github api", 60_000)],
)
def test_a_payload_over_the_ceiling_is_a_skip_not_a_quiet_day(name: str, measured: int):
    """The three overflows measured in the #2 audit, each as a skip.

    A payload this size never reaches the model, so the loop that asked for it
    sees nothing. "Nothing" and "nothing happened" must not render the same.
    """
    assert measured > OUTPUT_CEILING_CHARS, "the ceiling no longer catches a measured overflow"
    oversized = {"issues": [{"key": "CDI-1", "status": "x" * measured}]}

    report = smoke.run(probes(**{name: lambda: oversized}))

    result = result_for(report, name)
    assert result.status == SKIPPED
    assert result.reason == OVERFLOW
    assert result.source not in report.reached_sources


def test_the_ceiling_sits_below_a_two_day_calendar_pull():
    """Five days measured 156,681 chars, so a day is roughly 31k.

    The ceiling has to catch the second day, or "just widen it to the week" -
    the exact mistake the audit recorded - passes the smoke run.
    """
    per_day = 156_681 / 5
    assert per_day < OUTPUT_CEILING_CHARS < 2 * per_day


@pytest.mark.parametrize(
    "text",
    [
        "response exceeds maximum length",
        "Error: output is too large to return",
        "result truncated: token limit reached",
    ],
)
def test_an_overflow_reported_as_text_is_read_as_overflow(text: str):
    """Some connectors return the overflow as a value rather than raising.

    Either way it is the same failure, and neither is authentication - which is
    why classifying on the message beats classifying on "did it throw".
    """
    by_return = smoke.run(probes(calendar=lambda: text))
    by_raise = smoke.run(probes(calendar=raises(RuntimeError(text))))

    assert result_for(by_return, "calendar").reason == OVERFLOW
    assert result_for(by_raise, "calendar").reason == OVERFLOW


# --------------------------------------------------------------------------
# 4. authenticated, answered, and still not reached
# --------------------------------------------------------------------------


@pytest.mark.guardrail
@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("calendar", {"calendars": []}),
        ("gmail", {"messages": []}),
        ("slack", []),
        ("slack", {"members": []}),
        ("notion", {}),
        ("jira", {"issues": []}),
        ("github api", {"repositories": []}),
        ("databricks", {"rows": []}),
        ("databricks", "1"),
    ],
)
def test_a_call_that_did_not_raise_is_still_not_proof_the_source_answered(
    name: str, payload: object
):
    """The failure mode the audit found: silence that looks like success.

    Every payload here comes back from an authenticated, non-raising call and
    carries no record. Reading any of them as "reached" is how a brief reports
    a quiet day on a board that was never actually queried.
    """
    report = smoke.run(probes(**{name: lambda: payload}))

    result = result_for(report, name)
    assert result.status == SKIPPED, f"{payload!r} was read as a reachable {name}"
    assert result.reason == SHAPE
    assert result.detail, "a shape skip that does not say what was wrong is unactionable"


def test_a_databricks_answer_must_actually_be_the_one_that_was_asked_for():
    """SELECT 1 returning anything but 1 is a warehouse that is not answering."""
    report = smoke.run(probes(databricks=lambda: {"rows": [[0]]}))

    assert result_for(report, "databricks").status == SKIPPED


def test_slack_resolving_nothing_is_a_skip_because_the_failure_is_silent():
    """Display-name search returns an empty result rather than an error.

    That is the documented trap (SPEC section 4), so the empty result is the
    thing this check exists to catch - it can never be read as "reached".
    """
    report = smoke.run(probes(slack=lambda: {"members": []}))

    result = result_for(report, "slack")
    assert result.status == SKIPPED
    assert "id" in result.detail, "the skip does not point at resolving by id"


def test_jira_returning_no_issues_is_a_skip_not_a_quiet_board():
    """`DED`, `CING` and `DCTF` returned nothing in the audit: dormant, not quiet.

    A bounded window on the live project comes back with rows. Zero rows most
    likely means the JQL names a dormant project, and the cost of being wrong
    is one honest "couldn't check jira" line rather than a board reported clear.
    """
    report = smoke.run(probes(jira=lambda: {"issues": []}))

    assert result_for(report, "jira").status == SKIPPED


def test_a_calendar_day_with_no_events_is_still_reached():
    """The one place emptiness is honest: a day can genuinely be clear.

    The guard against an overflow hiding here is the ceiling, not the count.
    """
    report = smoke.run(probes(calendar=lambda: {"events": []}))

    assert result_for(report, "calendar").status == REACHED


# --------------------------------------------------------------------------
# 5. the Databricks trap: expired OAuth does not look like expired OAuth
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "outputSchema validation failed",
        "no structured output was returned",
    ],
)
def test_the_schema_error_is_reported_as_expired_auth_with_the_fix(text: str):
    """Never verified against a live workspace, and it fails in disguise.

    Expired Databricks OAuth surfaces as a schema complaint, so an operator
    reading the report goes looking at the query. The classification, and the
    fix, are encoded here instead.
    """
    report = smoke.run(probes(databricks=raises(RuntimeError(text))))

    result = result_for(report, "databricks")
    assert result.reason == AUTH, "the schema error was read as a broken query"
    assert "login" in result.detail, f"the report does not name the fix: {result.detail!r}"


@pytest.mark.parametrize("text", ["401 unauthorized", "oauth token expired", "forbidden"])
def test_a_plain_auth_failure_is_still_read_as_auth(text: str):
    report = smoke.run(probes(notion=raises(RuntimeError(text))))

    assert result_for(report, "notion").reason == AUTH


# --------------------------------------------------------------------------
# 6. github: a mirror fetch and an API call fail differently
# --------------------------------------------------------------------------


def test_a_mirror_fetch_failure_is_a_distinct_skip_from_an_api_failure():
    """Two halves, two fixes: one is `gh auth`, the other is a clone on disk.

    Reporting either as "github is down" sends whoever reads it to the wrong
    place, which is the same reasoning `MirrorUnavailable.reason` already
    encodes for the pulse.
    """
    fetch_broke = smoke.run(probes(**{"github mirror": lambda: False}))
    api_broke = smoke.run(probes(**{"github api": raises(RuntimeError("bad credentials"))}))

    assert result_for(fetch_broke, "github mirror").reason == FETCH
    assert result_for(fetch_broke, "github api").status == REACHED
    assert result_for(api_broke, "github api").reason != FETCH
    assert result_for(api_broke, "github mirror").status == REACHED


def test_github_counts_as_reached_only_when_both_halves_answer():
    """`reached_sources` is what a caller asks before trusting a section.

    GitHub is two probes under one name, so the source-level answer has to be
    the conjunction: half of GitHub answering is not GitHub answering.
    """
    half_broken = smoke.run(probes(**{"github mirror": lambda: False}))

    assert "github mirror" not in half_broken.reached
    assert "github api" in half_broken.reached
    assert "github" not in half_broken.reached_sources
    assert smoke.run(probes()).reached_sources == (
        "calendar",
        "gmail",
        "slack",
        "notion",
        "jira",
        "github",
        "databricks",
    )


# --------------------------------------------------------------------------
# 7. not connected is not a failure
# --------------------------------------------------------------------------


def test_granola_reports_as_deliberately_not_connected():
    """It was never wired up: Gemini covers the corpus. A skip would read as an
    outage, and someone would go and try to fix it every morning."""
    report = smoke.run(probes())

    result = result_for(report, "granola")
    assert result.status == NOT_CONNECTED
    assert result.reason == ""
    assert "granola" not in " ".join(report.degrade_lines())
    assert "granola" in report.render()


@pytest.mark.guardrail
def test_a_source_with_no_probe_is_skipped_rather_than_assumed_reached():
    """An unconfigured environment must fail closed, and say so per source."""
    report = smoke.run({})

    assert report.reached == ()
    assert set(report.skipped) == set(HEALTHY)
    assert report.render()


def test_a_probe_under_an_unknown_name_is_refused():
    """A typo would otherwise leave a source unchecked and silently 'not wired'."""
    with pytest.raises(ValueError, match="calender"):
        smoke.run({"calender": lambda: HEALTHY["calendar"]})


# --------------------------------------------------------------------------
# 8. the format, which the run log (#26) reuses
# --------------------------------------------------------------------------


def test_the_report_renders_one_line_per_check_plus_a_summary():
    report = smoke.run(probes(jira=raises(RuntimeError("down"))))
    lines = report.render().splitlines()

    assert lines[0] == "sources: 7 reached, 1 skipped, 1 not connected"
    assert len(lines) == 1 + len(CHECKS)
    for check, line in zip(CHECKS, lines[1:], strict=True):
        assert line.startswith(f"- {check.name}: ")


def test_the_report_is_rows_before_it_is_text():
    """#26 appends a run line to State.md and needs fields, not a parsed string."""
    rows = smoke.run(probes(slack=raises(RuntimeError("401 unauthorized")))).as_rows()

    assert all(set(row) == {"name", "source", "status", "reason", "detail"} for row in rows)
    assert all(isinstance(value, str) for row in rows for value in row.values())
    slack = next(row for row in rows if row["name"] == "slack")
    assert slack == {
        "name": "slack",
        "source": "slack",
        "status": SKIPPED,
        "reason": AUTH,
        "detail": "401 unauthorized",
    }


def test_the_run_is_deterministic_and_ordered():
    """A report whose row order moves between runs cannot be diffed in a log."""
    first, second = smoke.run(probes()), smoke.run(probes())

    assert first.as_rows() == second.as_rows()
    assert first.render() == second.render()


def test_the_report_reads_in_the_house_voice():
    report = smoke.run(probes(databricks=raises(RuntimeError("outputSchema"))))

    assert voice_violations(report.render()) == []
    assert voice_violations("\n".join(report.degrade_lines())) == []


def test_ok_is_true_only_when_every_connected_source_answered():
    assert smoke.run(probes()).ok is True
    assert smoke.run(probes(gmail=lambda: {"messages": []})).ok is False


def test_a_detail_never_carries_more_than_a_line():
    """A stack trace pasted into a brief is how the one-line rule dies."""
    noisy = "Traceback (most recent call last):\n  File 'x'\n" + "y" * 500
    report = smoke.run(probes(gmail=raises(RuntimeError(noisy))))

    detail = result_for(report, "gmail").detail
    assert "\n" not in detail
    assert len(detail) <= smoke.DETAIL_LIMIT


def test_a_shape_failure_is_trimmed_like_a_raised_one():
    """The cap has to hold on the plausibility path too, not just on raises.

    `_classify` trims what a probe throws, but a check's own message is built
    from the payload - `_check_warehouse` interpolates the answer it got - and
    that path reached `Result.detail` untrimmed. A driver handing back a page
    of garbled text put the whole page in the line the brief ships, which is
    the DM-sized paste `DETAIL_LIMIT` exists to prevent.
    """
    garbled = "ERROR: " + "x" * 800
    report = smoke.run(probes(databricks=lambda: {"rows": [[garbled]]}))

    result = result_for(report, "databricks")
    assert result.status == smoke.SKIPPED
    assert len(result.detail) <= smoke.DETAIL_LIMIT
    assert all(len(line) <= 200 for line in report.degrade_lines())


# --------------------------------------------------------------------------
# 9. the module holds no client, which is what keeps this suite offline
# --------------------------------------------------------------------------


def test_the_module_carries_no_client_of_its_own():
    """Probes are injected, so `smoke.py` imports nothing that can reach out.

    This is the structural half of "tests must not hit the network": there is
    no import here that could, whatever a future edit passes in. It also keeps
    the connector-client tripwire in `test_guardrails.py` meaningful.
    """
    source = Path(smoke.__file__).read_text(encoding="utf-8")
    imported = {
        node.module.split(".")[0] if isinstance(node, ast.ImportFrom) else alias.name.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert imported <= {"__future__", "collections", "dataclasses", "re", "typing", "daydag"}


def test_the_bounds_are_anchored_to_the_recipes_that_enforce_them():
    """A bound is a restatement of `recipes` unless it is built from it.

    `bound=` describes what keeps each query under the ceiling - but `recipes`
    is what actually keeps it there. Two copies of one fact, and the prose copy
    is the one nothing tests: raise `JIRA_MAX_RESULTS_CAP` to 500 and a hand
    written "never all of them" still reads correct while no longer being true.

    So the bounds that have a constant behind them are built from it. This
    fails the moment a cap moves and the report keeps quoting the old one.

    Asserting the cap's *value* appears in the text would not show that: a
    hand-typed "at most 100" contains it just as well. So this moves the cap
    and rebuilds the module, which only follows if the bound is built from it.
    """
    import importlib

    from daydag import recipes
    from daydag import smoke as smoke_module

    original = recipes.JIRA_MAX_RESULTS_CAP
    try:
        recipes.JIRA_MAX_RESULTS_CAP = 4242
        rebuilt = importlib.reload(smoke_module)
        moved = {check.name: check.bound for check in rebuilt.CHECKS}
        assert "4242" in moved["jira"], "the bound is a restatement, not a reference"
    finally:
        recipes.JIRA_MAX_RESULTS_CAP = original
        importlib.reload(smoke_module)

    bounds = {check.name: check.bound for check in smoke_module.CHECKS}
    assert str(original) in bounds["jira"]
    assert str(recipes.GH_LIMIT_CAP) in bounds["github api"]
    assert recipes.GEMINI_LABEL in bounds["gmail"]


# --------------------------------------------------------------------------
# 10. the near-misses: a check that is nearly right reports a source as healthy
#
# Every case below passed the first version of this module. They are one defect
# in seven places - a check that stops a field short of what it claims to prove
# - which is the defect the whole module exists to catch.
# --------------------------------------------------------------------------


def test_the_login_fix_survives_a_realistic_schema_error():
    """The fix is the point of classifying the trap, so it cannot be trimmed off.

    A real message names the tool and the endpoint and runs well past the line
    limit. Trimming the error and then appending the fix put the fix over the
    limit and cut it mid-word, so the operator got "auth" and no instruction.
    """
    real = (
        "outputSchema validation failed for tool execute_sql: no structured "
        "output was returned by the serving endpoint"
    )
    report = smoke.run(probes(databricks=raises(RuntimeError(real))))

    detail = result_for(report, "databricks").detail
    assert detail.endswith("re-run the workspace login first"), detail
    assert len(detail) <= 120
    assert "outputSchema" in detail, "the original message was trimmed away entirely"


@pytest.mark.parametrize("text", ["429 Too Many Requests", "rate limited: too many requests"])
def test_a_rate_limit_is_not_reported_as_an_overflow(text: str):
    """Routine from Slack, GitHub and Jira, and the opposite fix.

    An overflow says "bound the query harder". A 429 says "ask again later".
    Reading one as the other sends whoever is on it to rewrite a query that was
    never the problem.
    """
    report = smoke.run(probes(slack=raises(RuntimeError(text))))

    assert result_for(report, "slack").reason != OVERFLOW


@pytest.mark.parametrize(
    "payload",
    [
        {"error": {"code": 401, "message": "invalid_auth"}},
        {"errorMessages": ["Unauthorized (401)"]},
        {"error": "401 unauthorized"},
    ],
)
def test_an_error_handed_back_as_an_object_is_classified_not_read_as_a_shape(payload: object):
    """The common MCP and REST failure shape, and it does not raise.

    Falling through to the plausibility check reports "no event list came back"
    and never mentions the 401 at all, so the operator goes and looks at the
    query rather than re-authenticating.
    """
    report = smoke.run(probes(calendar=lambda: payload))

    result = result_for(report, "calendar")
    assert result.status == SKIPPED
    assert result.reason == AUTH, f"the error object was read as {result.reason!r}"
    assert "401" in result.detail or "auth" in result.detail


def test_gmail_metadata_alone_is_not_a_reachable_note():
    """Search results are metadata; the subject is what carries the title.

    SPEC section 4 says search results alone are metadata, and the audit's whole
    correction was that the *subject* is the parseable part. A result set with
    no subject in it has not shown the note corpus is readable.
    """
    report = smoke.run(probes(gmail=lambda: {"messages": [{"id": "m1", "threadId": "t1"}]}))

    assert result_for(report, "gmail").status == SKIPPED


def test_a_calendar_entry_with_no_start_is_not_an_event():
    """A brief full of meetings with no times, under a healthy calendar row."""
    report = smoke.run(probes(calendar=lambda: {"events": [{"id": "e1", "summary": "standup"}]}))

    assert result_for(report, "calendar").status == SKIPPED


def test_select_1_does_not_accept_a_boolean():
    """`True == 1` in Python, so a driver returning a bool slipped through.

    This check's entire premise is that "it returned something" is not proof,
    and a boolean is the something a loose comparison accepts.
    """
    report = smoke.run(probes(databricks=lambda: {"rows": [[True]]}))

    assert result_for(report, "databricks").status == SKIPPED


def test_a_narrowed_run_still_takes_the_full_probe_map():
    """`checks=` is for running a subset, not for having to trim the probes too.

    Validating probe names against the narrowed list rejected every probe
    outside it as a typo, which made the parameter unusable for what it is for.
    """
    report = smoke.run(probes(), checks=[CHECKS[0]])

    assert [row["name"] for row in report.as_rows()] == ["calendar"]
    with pytest.raises(ValueError, match="calender"):
        smoke.run({"calender": lambda: None}, checks=[CHECKS[0]])
