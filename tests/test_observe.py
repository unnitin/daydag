"""What ran and what it reached: the smoke run (issue #3) and the run log (issue #26).

`src/daydag/observe.py` is one module - the pre-flight over injected probes,
and the log that records what each run reached - so this is one file. The
first half asks whether the *world* answered; the second asks whether the
record of that answer survives the run.

THE SMOKE RUN is written against the one finding from the #2 audit that
changes the shape of the answer:

    an overflowing query returns NOTHING, and nothing reads as a quiet day.

Three sources blew the output limit during that audit - Calendar at 156,681
chars for five days, Jira at 125,231 for a 14-day query, the Jira project list
at 60,000. None of them raised. A smoke test that only asked "did the call
succeed" would have gone green while every real query in the brief came back
empty, and the brief would have said the day was quiet. So every assertion
about the run is about a *plausible shape coming back*, never about the
absence of an exception.

THE RUN LOG is the only thing that makes a silent failure diagnosable. There
is no platform observability behind this agent. SPEC section 7 states the
consequence plainly: *the failure mode is Nitin noticing a brief didn't
arrive*. By then the run is over, the process is gone, and nothing anywhere
says which source went quiet. The run log is the record that survives that,
so every test of it is written against one of the three ways such a record
is useless:

1. **It says a source was skipped without saying which, or why.** "some sources
   were unavailable" sends the reader to check all eight by hand, which is the
   work the log exists to save.
2. **It only records successes.** A log that writes its row at the end of a
   clean run is missing exactly the runs anybody would ever read it for.
3. **It ends up in the vault.** Five scheduled loops appending to one
   iCloud-synced markdown file with no locking is a lost update, which is the
   reason ARCHITECTURE splits the stores at all.

No test in this file touches the network. Probes are injected callables and
their outcomes are fixtures, which is also the design constraint on the
module: `observe.py` holds no client, so there is nothing for it to reach even
by accident. `test_the_module_carries_no_client_of_its_own` asserts that. The
run rows come from `observe.run(...).as_rows()` rather than from rendered
text, and the clock is injected, because a report that cannot be reproduced
byte-for-byte in a test is a report nobody can assert anything about.
"""

from __future__ import annotations

import ast
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from daydag import observe
from daydag.eventlog import EventLog
from daydag.observe import (
    AUTH,
    CHECKS,
    DEGRADED,
    FAILED,
    FETCH,
    NOT_CONNECTED,
    OK,
    OUTPUT_CEILING_CHARS,
    OVERFLOW,
    REACHED,
    RUN,
    SHAPE,
    SKIPPED,
    UNCHECKED,
    UNREADABLE,
    RunLog,
    RunRow,
    SmokeReport,
)
from daydag.voice import voice_violations
from support import PT

SIX_FORTY = datetime(2026, 9, 8, 6, 40, tzinfo=PT)


# --------------------------------------------------------------------------
# fixtures: one plausible payload per check, so a test can break exactly one;
# real smoke rows, produced by real probes, with no network
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


def _probe(answer: object):
    """The probe for one answer: a callable as given, an exception raised, a value returned."""
    if isinstance(answer, BaseException):

        def probe():
            raise answer

        return probe
    if callable(answer):
        return answer
    return lambda: answer


def probes(**overrides: object) -> dict[str, object]:
    """Probes for every check: healthy unless this call names one as broken.

    A broken source is given as the thing the connector hands back or raises,
    because those are the two ways a source actually fails and `observe` reads
    both. A callable is used as the probe itself. A check whose name has a
    space, `github api`, is spelt `github_api` so it can be a keyword.
    """
    wired = {name: _probe(payload) for name, payload in HEALTHY.items()}
    for name, answer in overrides.items():
        wired[name.replace("_", " ")] = _probe(answer)
    return wired


def _rows(**broken: object) -> list[dict[str, str]]:
    """The rows a run log takes, from a real run with these sources broken."""
    return observe.run(probes(**broken)).as_rows()


def result_for(report: SmokeReport, name: str):
    found = [result for result in report.results if result.name == name]
    assert found, f"the run produced no row for {name!r}"
    return found[0]


@pytest.fixture
def log():
    """A real event log, in memory. It is SQLite in production too."""
    return EventLog(sqlite3.connect(":memory:"))


@pytest.fixture
def clock():
    """A clock the test drives, so a timestamp assertion is exact rather than fuzzy.

    `observe.py` deliberately takes no clock - a stamped report would be
    non-deterministic and therefore unassertable. The stamp has to happen
    somewhere, so it happens here, injected, for the same reason.

    Not `support.Clock`: that one stands still until a test moves it, which is
    what a brief wants. This one advances a minute per read, so two runs
    recorded back to back carry distinct, ordered timestamps without the test
    having to wind anything between them.
    """
    ticks = iter(SIX_FORTY + timedelta(minutes=n) for n in range(0, 600))
    return lambda: next(ticks)


@pytest.fixture
def runlog(log, clock):
    return RunLog(log, clock=clock)


# --------------------------------------------------------------------------
# 1. one invocation touches every source
# --------------------------------------------------------------------------


def test_one_run_reports_a_row_for_every_source():
    """The point of the run is completeness: a source with no row was not checked."""
    report = observe.run(probes())

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
    report = observe.run(probes())

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
    report = observe.run(probes(gmail=failure))

    assert result_for(report, "gmail").status == SKIPPED
    assert "calendar" in report.reached
    assert "gmail" not in report.reached
    assert report.render(), "a failing connector emptied the report"


@pytest.mark.guardrail
def test_the_broken_connector_is_named_in_the_line_the_brief_ships():
    """Guardrail 6 verbatim: a one-line "couldn't check X", never a stall."""
    report = observe.run(probes(databricks=RuntimeError("workspace unreachable")))

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

    observe.run(
        probes(calendar=counted("calendar", None), gmail=counted("gmail", HEALTHY["gmail"]))
    )

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

    report = observe.run(probes(**{name: oversized}))

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
    by_return = observe.run(probes(calendar=text))
    by_raise = observe.run(probes(calendar=RuntimeError(text)))

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
    report = observe.run(probes(**{name: payload}))

    result = result_for(report, name)
    assert result.status == SKIPPED, f"{payload!r} was read as a reachable {name}"
    assert result.reason == SHAPE
    assert result.detail, "a shape skip that does not say what was wrong is unactionable"


def test_a_databricks_answer_must_actually_be_the_one_that_was_asked_for():
    """SELECT 1 returning anything but 1 is a warehouse that is not answering."""
    report = observe.run(probes(databricks={"rows": [[0]]}))

    assert result_for(report, "databricks").status == SKIPPED


def test_slack_resolving_nothing_is_a_skip_because_the_failure_is_silent():
    """Display-name search returns an empty result rather than an error.

    That is the documented trap (SPEC section 4), so the empty result is the
    thing this check exists to catch - it can never be read as "reached".
    """
    report = observe.run(probes(slack={"members": []}))

    result = result_for(report, "slack")
    assert result.status == SKIPPED
    assert "id" in result.detail, "the skip does not point at resolving by id"


def test_jira_returning_no_issues_is_a_skip_not_a_quiet_board():
    """`DED`, `CING` and `DCTF` returned nothing in the audit: dormant, not quiet.

    A bounded window on the live project comes back with rows. Zero rows most
    likely means the JQL names a dormant project, and the cost of being wrong
    is one honest "couldn't check jira" line rather than a board reported clear.
    """
    report = observe.run(probes(jira={"issues": []}))

    assert result_for(report, "jira").status == SKIPPED


def test_a_calendar_day_with_no_events_is_still_reached():
    """The one place emptiness is honest: a day can genuinely be clear.

    The guard against an overflow hiding here is the ceiling, not the count.
    """
    report = observe.run(probes(calendar={"events": []}))

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
    report = observe.run(probes(databricks=RuntimeError(text)))

    result = result_for(report, "databricks")
    assert result.reason == AUTH, "the schema error was read as a broken query"
    assert "login" in result.detail, f"the report does not name the fix: {result.detail!r}"


@pytest.mark.parametrize("text", ["401 unauthorized", "oauth token expired", "forbidden"])
def test_a_plain_auth_failure_is_still_read_as_auth(text: str):
    report = observe.run(probes(notion=RuntimeError(text)))

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
    fetch_broke = observe.run(probes(github_mirror=False))
    api_broke = observe.run(probes(github_api=RuntimeError("bad credentials")))

    assert result_for(fetch_broke, "github mirror").reason == FETCH
    assert result_for(fetch_broke, "github api").status == REACHED
    assert result_for(api_broke, "github api").reason != FETCH
    assert result_for(api_broke, "github mirror").status == REACHED


def test_github_counts_as_reached_only_when_both_halves_answer():
    """`reached_sources` is what a caller asks before trusting a section.

    GitHub is two probes under one name, so the source-level answer has to be
    the conjunction: half of GitHub answering is not GitHub answering.
    """
    half_broken = observe.run(probes(github_mirror=False))

    assert "github mirror" not in half_broken.reached
    assert "github api" in half_broken.reached
    assert "github" not in half_broken.reached_sources
    assert observe.run(probes()).reached_sources == (
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
    report = observe.run(probes())

    result = result_for(report, "granola")
    assert result.status == NOT_CONNECTED
    assert result.reason == ""
    assert "granola" not in " ".join(report.degrade_lines())
    assert "granola" in report.render()


@pytest.mark.guardrail
def test_a_source_with_no_probe_is_skipped_rather_than_assumed_reached():
    """An unconfigured environment must fail closed, and say so per source."""
    report = observe.run({})

    assert report.reached == ()
    assert set(report.skipped) == set(HEALTHY)
    assert report.render()


def test_a_probe_under_an_unknown_name_is_refused():
    """A typo would otherwise leave a source unchecked and silently 'not wired'."""
    with pytest.raises(ValueError, match="calender"):
        observe.run({"calender": lambda: HEALTHY["calendar"]})


# --------------------------------------------------------------------------
# 8. the format, which the run log (#26) reuses
# --------------------------------------------------------------------------


def test_the_report_renders_one_line_per_check_plus_a_summary():
    report = observe.run(probes(jira=RuntimeError("down")))
    lines = report.render().splitlines()

    assert lines[0] == "sources: 7 reached, 1 skipped, 1 not connected"
    assert len(lines) == 1 + len(CHECKS)
    for check, line in zip(CHECKS, lines[1:], strict=True):
        assert line.startswith(f"- {check.name}: ")


def test_the_report_is_rows_before_it_is_text():
    """#26 appends a run line to State.md and needs fields, not a parsed string."""
    rows = observe.run(probes(slack=RuntimeError("401 unauthorized"))).as_rows()

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
    first, second = observe.run(probes()), observe.run(probes())

    assert first.as_rows() == second.as_rows()
    assert first.render() == second.render()


def test_the_report_reads_in_the_house_voice():
    report = observe.run(probes(databricks=RuntimeError("outputSchema")))

    assert voice_violations(report.render()) == []
    assert voice_violations("\n".join(report.degrade_lines())) == []


def test_ok_is_true_only_when_every_connected_source_answered():
    assert observe.run(probes()).ok is True
    assert observe.run(probes(gmail={"messages": []})).ok is False


def test_a_detail_never_carries_more_than_a_line():
    """A stack trace pasted into a brief is how the one-line rule dies."""
    noisy = "Traceback (most recent call last):\n  File 'x'\n" + "y" * 500
    report = observe.run(probes(gmail=RuntimeError(noisy)))

    detail = result_for(report, "gmail").detail
    assert "\n" not in detail
    assert len(detail) <= observe.DETAIL_LIMIT


def test_a_shape_failure_is_trimmed_like_a_raised_one():
    """The cap has to hold on the plausibility path too, not just on raises.

    `_classify` trims what a probe throws, but a check's own message is built
    from the payload - `_check_warehouse` interpolates the answer it got - and
    that path reached `Result.detail` untrimmed. A driver handing back a page
    of garbled text put the whole page in the line the brief ships, which is
    the DM-sized paste `DETAIL_LIMIT` exists to prevent.
    """
    garbled = "ERROR: " + "x" * 800
    report = observe.run(probes(databricks={"rows": [[garbled]]}))

    result = result_for(report, "databricks")
    assert result.status == observe.SKIPPED
    assert len(result.detail) <= observe.DETAIL_LIMIT
    assert all(len(line) <= 200 for line in report.degrade_lines())


# --------------------------------------------------------------------------
# 9. the module holds no client, which is what keeps this suite offline
# --------------------------------------------------------------------------


def test_the_module_carries_no_client_of_its_own():
    """Probes are injected, so `observe.py` imports nothing that can reach out.

    This is the structural half of "tests must not hit the network": there is
    no import here that could, whatever a future edit passes in. It also keeps
    the connector-client tripwire in `test_guardrails.py` meaningful.
    """
    source = Path(observe.__file__).read_text(encoding="utf-8")
    imported = {
        node.module.split(".")[0] if isinstance(node, ast.ImportFrom) else alias.name.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    stdlib = {"__future__", "collections", "contextlib", "dataclasses", "datetime", "re", "typing"}
    assert imported <= stdlib | {"daydag"}


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
    import importlib.util
    import sys

    from daydag import recipes

    # Rebuilt as a fresh module object under its own name, not `importlib.reload`
    # in place: a reload replaces `RunRow` and friends under every test that
    # imported them by name, and a row written by the new class no longer
    # compares equal to one read back by the old - which the run-log half of
    # this file does. It sits in `sys.modules` only while it executes, because
    # `dataclass` resolves string annotations through the module it names.
    spec = importlib.util.spec_from_file_location("daydag.observe_rebuilt", observe.__file__)
    assert spec is not None and spec.loader is not None
    rebuilt = importlib.util.module_from_spec(spec)

    original = recipes.JIRA_MAX_RESULTS_CAP
    sys.modules[spec.name] = rebuilt
    try:
        recipes.JIRA_MAX_RESULTS_CAP = 4242
        spec.loader.exec_module(rebuilt)
        moved = {check.name: check.bound for check in rebuilt.CHECKS}
        assert "4242" in moved["jira"], "the bound is a restatement, not a reference"
    finally:
        recipes.JIRA_MAX_RESULTS_CAP = original
        sys.modules.pop(spec.name, None)

    bounds = {check.name: check.bound for check in observe.CHECKS}
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
    report = observe.run(probes(databricks=RuntimeError(real)))

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
    report = observe.run(probes(slack=RuntimeError(text)))

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
    report = observe.run(probes(calendar=payload))

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
    report = observe.run(probes(gmail={"messages": [{"id": "m1", "threadId": "t1"}]}))

    assert result_for(report, "gmail").status == SKIPPED


def test_a_calendar_entry_with_no_start_is_not_an_event():
    """A brief full of meetings with no times, under a healthy calendar row."""
    report = observe.run(probes(calendar={"events": [{"id": "e1", "summary": "standup"}]}))

    assert result_for(report, "calendar").status == SKIPPED


def test_select_1_does_not_accept_a_boolean():
    """`True == 1` in Python, so a driver returning a bool slipped through.

    This check's entire premise is that "it returned something" is not proof,
    and a boolean is the something a loose comparison accepts.
    """
    report = observe.run(probes(databricks={"rows": [[True]]}))

    assert result_for(report, "databricks").status == SKIPPED


def test_a_narrowed_run_still_takes_the_full_probe_map():
    """`checks=` is for running a subset, not for having to trim the probes too.

    Validating probe names against the narrowed list rejected every probe
    outside it as a typo, which made the parameter unusable for what it is for.
    """
    report = observe.run(probes(), checks=[CHECKS[0]])

    assert [row["name"] for row in report.as_rows()] == ["calendar"]
    with pytest.raises(ValueError, match="calender"):
        observe.run({"calender": lambda: None}, checks=[CHECKS[0]])


def test_a_calendar_payload_keyed_by_day_is_an_event_list():
    """#111: the shape `run.plan`'s one-day steps invite passes the same
    checks as a flat list - and fails them the same way."""
    from daydag.observe import _check_calendar

    keyed = {"2026-09-07": [{"id": "e1", "summary": "pod standup", "start": "2026-09-07T09:00"}]}
    assert _check_calendar(keyed) is None
    assert _check_calendar({"2026-09-07": [{"id": "e1", "summary": "pod standup"}]}) is not None
    assert _check_calendar({"not-a-day": [{"id": "e1"}]}) == "no event list came back"


# --------------------------------------------------------------------------
# 11. one row per run, in the log outside the vault
# --------------------------------------------------------------------------


def test_a_run_appends_exactly_one_row(runlog, log):
    """One row per run. Not one per source - that is a trace, and nobody reads it."""
    runlog.record("morning brief", _rows())
    runlog.record("noon chaser", _rows())

    rows = log.recorded(RUN)
    assert len(rows) == 2
    assert [row["loop"] for row in rows] == ["morning brief", "noon chaser"]


def test_the_row_carries_the_four_fields_the_spec_names(runlog):
    """`timestamp · loop · sources reached · sources skipped`, verbatim from SPEC 7."""
    row = runlog.record("morning brief", _rows(jira=ConnectionError("401 unauthorized")))

    assert row.at == SIX_FORTY.isoformat()
    assert row.loop == "morning brief"
    assert "calendar" in row.reached
    assert [skip.name for skip in row.skipped] == ["jira"]


def test_the_timestamp_comes_from_the_injected_clock(log):
    """Pinned time, exact assertion. A test that cannot control time flakes."""
    frozen = RunLog(log, clock=lambda: SIX_FORTY)
    assert frozen.record("morning brief", _rows()).at == "2026-09-08T06:40:00-07:00"


def test_two_runs_of_the_same_loop_are_both_kept(runlog, log):
    """Append, never overwrite. Yesterday's run is how you see when this started."""
    first = runlog.record("morning brief", _rows())
    second = runlog.record("morning brief", _rows(jira=ConnectionError("401 unauthorized")))

    kept = [RunRow.from_payload(payload) for payload in log.recorded(RUN)]
    assert [row.at for row in kept] == [first.at, second.at]
    assert kept[0].skipped == () and [s.name for s in kept[1].skipped] == ["jira"]


def test_a_row_survives_the_round_trip_through_sqlite(tmp_path, clock):
    """The log is read back by a later process, which is the only time it matters."""
    path = tmp_path / "events.db"
    written = RunLog(EventLog.open(path), clock=clock).record(
        "morning brief", _rows(jira=ConnectionError("401 unauthorized"))
    )

    reread = RunRow.from_payload(EventLog.open(path).recorded(RUN)[0])
    assert reread == written, "a row that does not survive json is not a record"


# --------------------------------------------------------------------------
# 12. a skipped source is NAMED, with its reason
# --------------------------------------------------------------------------


def test_a_skipped_source_is_named_with_its_reason(runlog):
    """The whole point. "couldn't check jira (auth)" beats "some sources failed"."""
    row = runlog.record(
        "morning brief",
        _rows(jira=ConnectionError("401 unauthorized"), databricks="outputSchema missing"),
    )

    named = {skip.name: skip for skip in row.skipped}
    assert set(named) == {"jira", "databricks"}
    assert named["jira"].reason == observe.AUTH
    assert named["databricks"].reason == observe.AUTH
    assert named["jira"].detail, "a reason without a detail is half a diagnosis"


def test_the_line_names_every_skipped_source_and_its_reason(runlog):
    """The one line a human reads. Nothing may be summarised out of it."""
    row = runlog.record(
        "morning brief",
        _rows(jira=ConnectionError("401 unauthorized"), calendar="response exceeds output limit"),
    )
    line = row.line()

    assert row.at in line and "morning brief" in line
    for name, reason in (("jira", observe.AUTH), ("calendar", observe.OVERFLOW)):
        assert f"{name} ({reason})" in line, f"{name} is not named with its reason in {line!r}"


def test_no_source_is_dropped_between_the_smoke_rows_and_the_run_row(runlog):
    """Every check that ran is accounted for under exactly one heading.

    A source that appears in neither list is a source nobody knows went
    unchecked, which is indistinguishable from one that answered.
    """
    rows = _rows(jira=ConnectionError("401 unauthorized"))
    row = runlog.record("morning brief", rows)

    accounted = {*row.reached, *(skip.name for skip in row.skipped), *row.not_connected}
    assert accounted == {check["name"] for check in rows}


def test_an_unrecognised_status_is_refused_rather_than_dropped(runlog):
    """Fail loudly. A row silently binned is the failure this module exists to stop."""
    with pytest.raises(ValueError, match="invented"):
        runlog.record(
            "morning brief",
            [{"name": "jira", "source": "jira", "status": "invented", "reason": "", "detail": ""}],
        )


def test_a_row_with_no_check_name_is_refused_the_same_way(runlog):
    """The name is the key the row is filed under, so a missing one loses it.

    Refused with a message rather than a bare `KeyError`, which says nothing
    about what was wrong with it.
    """
    with pytest.raises(ValueError, match="no check name"):
        runlog.record("morning brief", [{"source": "jira", "status": observe.REACHED}])


def test_a_malformed_row_still_leaves_a_run_row_behind(runlog, log):
    """Write, then raise. The loud error must not cost the record of the run.

    The bad case is a caller already in an `except`, recording the failure that
    brought it there: refusing the append loses that failure too, and the run
    goes down with no trace at all - the exact silence this module is for.
    """
    good = {
        "name": "calendar",
        "source": "calendar",
        "status": observe.REACHED,
        "reason": "",
        "detail": "one day of events",
    }
    with pytest.raises(ValueError, match="no check name"):
        runlog.record(
            "morning brief",
            [good, {"source": "jira", "status": observe.REACHED}],
            failure=RuntimeError("mirror fetch died"),
        )

    (payload,) = log.recorded(RUN)
    row = RunRow.from_payload(payload)
    assert row.outcome == FAILED
    assert row.reached == ("calendar",), "the rows it did read are still kept"
    assert "mirror fetch died" in row.failure, "and so is the failure it came to record"
    assert "no check name" in row.failure


@pytest.mark.parametrize(
    "bad_rows",
    [
        pytest.param(["not a record"], id="a list of strings"),
        pytest.param(42, id="not iterable at all"),
        pytest.param([{"name": 5, "status": observe.REACHED}], id="a name that is not text"),
    ],
)
def test_any_malformed_input_still_leaves_a_run_row_behind(runlog, log, bad_rows):
    """`rows` is whatever the caller passed, and it can fail in more ways than one.

    Handing over the `SmokeReport` instead of its `as_rows()` raises `TypeError`
    from the iteration, a list of strings raises `AttributeError`. Catching only
    the shape you thought of writes no row at all.
    """
    with pytest.raises(Exception):  # noqa: B017 - the type is the caller's, the row is the point
        runlog.record("morning brief", bad_rows, failure=RuntimeError("mirror fetch died"))

    (payload,) = log.recorded(RUN)
    row = RunRow.from_payload(payload)
    assert row.outcome == FAILED
    assert "mirror fetch died" in row.failure
    assert row.line(), "the row must render rather than raise from a bad name"


def test_a_deliberately_unconnected_source_is_not_reported_as_a_failure(runlog):
    """Granola was never wired up. Reporting it as an outage sends someone to fix
    it every morning for the rest of time - `smoke` says so, and the log agrees."""
    row = runlog.record("morning brief", _rows())

    assert "granola" in row.not_connected
    assert "granola" not in [skip.name for skip in row.skipped]
    assert "granola" not in row.line()
    assert row.outcome == OK, "a source nobody connected is not a degraded run"


def test_a_run_with_a_skip_is_degraded_not_ok(runlog):
    row = runlog.record("morning brief", _rows(gmail=ConnectionError("401 unauthorized")))
    assert row.outcome == DEGRADED
    assert row.completed, "degrading is guardrail 6 working, not the run failing"


def test_a_run_that_reached_nothing_says_so_rather_than_looking_clean(runlog):
    """`reached: none` is visibly wrong. An empty field reads as a tidy run."""
    row = runlog.record("morning brief", [])
    assert "reached: none" in row.line()


def test_a_run_that_observed_no_source_is_not_a_successful_run(runlog):
    """A loop that bailed before the smoke run, or swallowed its own error.

    Otherwise it is identical to a healthy run in every field a reader checks,
    and "the agent last ran fine at 6:40" becomes a claim made by a run that
    read nothing at all.
    """
    row = runlog.record("morning brief", [])

    assert row.outcome == UNCHECKED
    assert not row.completed
    assert runlog.last_completed_run("morning brief") is None


def test_a_run_of_nothing_but_unconnected_sources_is_not_a_successful_run(runlog):
    """Rows arrived, and every one of them read nothing.

    Counting observations rather than answers scores this `OK`. It is reachable
    today with an all-unwired `checks=` subset, and permanently the day a second
    connector is parked as `wired=False` during a migration.
    """
    row = runlog.record(
        "morning brief",
        [
            {
                "name": "granola",
                "source": "granola",
                "status": observe.NOT_CONNECTED,
                "reason": "",
                "detail": "not wired up on purpose",
            }
        ],
    )

    assert row.not_connected == ("granola",)
    assert row.outcome == UNCHECKED and not row.completed


# --------------------------------------------------------------------------
# 13. a run that fails partway still writes its row
# --------------------------------------------------------------------------


def test_a_run_that_fails_partway_still_writes_its_row(runlog, log):
    """The runs worth reading the log for are exactly the ones that did not finish."""
    with pytest.raises(RuntimeError, match="mirror fetch died"):
        with runlog.run("morning brief") as run:
            run.observe(_rows(jira=ConnectionError("401 unauthorized")))
            raise RuntimeError("mirror fetch died")

    (payload,) = log.recorded(RUN)
    row = RunRow.from_payload(payload)
    assert row.outcome == FAILED
    assert not row.completed
    assert "mirror fetch died" in row.failure
    assert "mirror fetch died" in row.line()


def test_the_partial_row_keeps_what_was_reached_before_the_failure(runlog):
    """Half a run is still evidence. Which half is the diagnosis."""
    with pytest.raises(RuntimeError):
        with runlog.run("morning brief") as run:
            run.observe(_rows(jira=ConnectionError("401 unauthorized")))
            raise RuntimeError("mirror fetch died")

    row = runlog.last_run()
    assert "calendar" in row.reached
    assert [skip.name for skip in row.skipped] == ["jira"]


def test_the_failure_propagates_after_it_is_recorded(runlog):
    """Recording is not handling. A swallowed failure is a silent failure again."""
    with pytest.raises(ZeroDivisionError):
        with runlog.run("morning brief"):
            raise ZeroDivisionError("division by zero")

    assert runlog.last_run().outcome == FAILED


def test_a_run_that_dies_before_probing_anything_still_leaves_a_row(runlog, log):
    """Nothing observed is itself the finding: the run never got as far as a source."""
    with pytest.raises(RuntimeError):
        with runlog.run("morning brief"):
            raise RuntimeError("config missing")

    row = RunRow.from_payload(log.recorded(RUN)[0])
    assert row.reached == () and row.skipped == ()
    assert "reached: none" in row.line()


def test_a_clean_run_through_the_context_manager_writes_one_ok_row(runlog, log):
    with runlog.run("morning brief") as run:
        run.observe(_rows())

    (payload,) = log.recorded(RUN)
    assert RunRow.from_payload(payload).outcome == OK


def test_a_stack_trace_is_trimmed_to_something_a_line_can_carry(runlog):
    with pytest.raises(RuntimeError):
        with runlog.run("morning brief"):
            raise RuntimeError("boom " * 200)

    assert len(runlog.last_run().failure) <= 200, "a run log row is a line, not a traceback"


def test_a_loop_name_is_bounded_like_every_other_word_on_the_line(runlog):
    """`RunRow.line` claims its parts are "all of them the agent's own words".

    They are caller-supplied, not the agent's: `loop` comes straight from
    `record`/`run`, and nothing trimmed it. `RunRow.unreadable` trims the same
    field on the degraded-read path, so this was one path capped and its twin
    not - and the uncapped one is the live write.

    The newline matters more than the length: `projection_line` is offered to
    `State.md`, and a name carrying one breaks the file's structure rather
    than merely making a long line.
    """
    runlog.record("morning\nbrief " + "X" * 2000, [])

    row = runlog.last_run()
    assert len(row.loop) <= observe.FAILURE_LIMIT
    assert "\n" not in row.loop
    assert "\n" not in runlog.projection_line(row.loop)


def test_a_check_name_is_bounded_too(runlog):
    """Same claim, same gap: a skip's name renders raw into the projection.

    `Skip.line` already holds `reason` to smoke's closed vocabulary because a
    connector's own sentence must not reach the vault. The name beside it had
    no such guard.
    """
    runlog.record(
        "morning brief",
        [{"name": "jira\n" + "Y" * 2000, "source": "jira", "status": "skipped", "reason": "auth"}],
    )

    skip = runlog.last_run().skipped[0]
    assert len(skip.name) <= observe.FAILURE_LIMIT
    assert "\n" not in skip.name


def test_a_later_observation_of_the_same_source_replaces_the_earlier_one(runlog):
    """A source re-probed and now answering is reached, not both at once."""
    with runlog.run("morning brief") as run:
        run.observe(_rows(slack=ConnectionError("401 unauthorized")))
        run.observe(_rows())

    row = runlog.last_run()
    assert row.skipped == ()
    assert "slack" in row.reached
    assert row.reached.count("slack") == 1


# --------------------------------------------------------------------------
# 14. the log is the record; the vault gets a projection at most
# --------------------------------------------------------------------------


@pytest.mark.guardrail
def test_no_run_row_reaches_the_vault(folder, runlog, log):
    """ARCHITECTURE splits the stores for this. The row lives outside the vault.

    Marked as a guardrail because the failure is silent and permanent: a
    connector's error text lands in a plaintext file synced to every device
    Nitin owns, and nothing about the vault ever says it got there.
    """
    runlog.record("morning brief", _rows(jira=ConnectionError("401 unauthorized")))
    folder.update_state(chase=log.chase_items(), watch=[], notes_gaps=[])

    body = "".join(
        path.read_text(encoding="utf-8") for path in folder.root.rglob("*.md") if path.is_file()
    )
    for leaked in ("morning brief", "401 unauthorized", observe.AUTH, "reached:"):
        assert leaked not in body, f"{leaked!r} reached a synced markdown file"

    # And the one line that IS offered to the vault carries no connector wording,
    # because the module docstring invites its owner to put it there.
    for line in runlog.projection_lines():
        assert "401 unauthorized" not in line


def test_a_run_row_is_not_a_chase_item(runlog, log):
    """`chase_items` is what feeds State.md. A run row must be invisible to it."""
    runlog.record("morning brief", _rows())
    assert log.chase_items() == []


def test_a_damaged_run_row_cannot_blank_the_chase_list(runlog, log):
    """The two readers share one table, and the run log is its busiest writer.

    `chase_items` feeds `update_state`, so a torn run payload raising out of the
    decode would take `State.md` down wholesale - over a row that has nothing to
    do with the chase list at all.
    """
    log.record(
        "loop_opened",
        sensitivity="normal",
        key="CDI-596",
        owner="VP-Data",
        ask="cutover rehearsal",
        day=1,
    )
    log._db.execute("INSERT INTO events (kind, payload) VALUES (?, ?)", (RUN, '{"at": "2026-'))
    log._db.commit()

    assert [item["key"] for item in log.chase_items()] == ["CDI-596"]
    assert runlog.rows()[0].outcome == UNREADABLE


def test_the_projection_names_both_the_last_run_and_the_last_that_finished(runlog):
    """What State.md may carry: one line, derived, and rebuilt from the log.

    The pair is the finding. "last run failed" alone does not say whether this
    is a blip or whether nothing has worked since tuesday.
    """
    good = runlog.record("morning brief", _rows())
    with pytest.raises(RuntimeError):
        with runlog.run("morning brief"):
            raise RuntimeError("mirror fetch died")

    assert runlog.last_run().outcome == FAILED
    assert runlog.last_completed_run() == good

    line = runlog.projection_line("morning brief")
    assert "failed" in line, "the projection must not hide that the run failed"
    assert f"last completed: {good.at}" in line


def test_the_projection_of_a_healthy_loop_does_not_nag_about_history(runlog):
    """A run that finished needs no second timestamp beside it."""
    runlog.record("morning brief", _rows())
    assert "last completed" not in runlog.projection_line("morning brief")


def test_the_projection_says_never_when_nothing_has_ever_finished(runlog):
    with pytest.raises(RuntimeError):
        with runlog.run("morning brief"):
            raise RuntimeError("config missing")
    assert "last completed: never" in runlog.projection_line("morning brief")


def test_the_projection_admits_when_a_loop_has_never_run(runlog):
    """An empty log must not render as a healthy one."""
    assert runlog.last_run() is None
    assert "no run recorded" in runlog.projection_line("morning brief")


def test_a_healthy_loop_cannot_render_a_clean_line_for_a_dead_one(runlog):
    """The one that mattered: five loops share this log.

    The noon chaser running fine at 12:05 must not produce the line that
    `State.md` carries while the morning brief has been failing all week. There
    is no unscoped projection to reach for by accident, and every loop gets its
    own line.
    """
    with pytest.raises(RuntimeError):
        with runlog.run("morning brief"):
            raise RuntimeError("calendar auth expired")
    runlog.record("noon chaser", _rows())

    assert runlog.last_run().loop == "noon chaser", "the fixture must be meaningful"
    with pytest.raises(TypeError):
        runlog.projection_line()  # no unscoped form exists

    lines = runlog.projection_lines()
    assert len(lines) == 2
    brief = next(line for line in lines if "morning brief" in line)
    assert "failed" in brief and "last completed: never" in brief


def test_the_projection_never_repeats_the_failure_text(runlog):
    """Vault-bound, so the wording a connector chose does not travel with it.

    A 403 that quotes the url it was refused can carry a token in the query
    string. Fine in the log, which is outside the vault; not fine in a line
    offered to a synced markdown file.
    """
    secret = "https://example.invalid/api?token=not-a-real-token"
    with pytest.raises(RuntimeError):
        with runlog.run("morning brief"):
            raise RuntimeError(f"403 for {secret}")

    line = runlog.projection_line("morning brief")
    assert secret not in line and "403" not in line
    assert "failed: see the event log" in line, "the fact of the failure still shows"
    assert secret in runlog.last_run().failure, "the log itself keeps the whole thing"


def test_the_projection_is_scoped_to_one_loop_when_asked(runlog):
    runlog.record("morning brief", _rows())
    runlog.record("noon chaser", _rows())
    assert runlog.last_run("morning brief").loop == "morning brief"


# --------------------------------------------------------------------------
# 15. the reader degrades: one bad row must not cost the whole history
# --------------------------------------------------------------------------


def test_a_row_this_version_cannot_parse_does_not_take_the_history_with_it(runlog, log):
    """Guardrail 6 across time. The schema moving is when somebody is reading."""
    runlog.record("morning brief", _rows())
    log.record(RUN, at="2026-09-08T07:00:00-07:00", loop="noon chaser", surprise="from the future")
    good = runlog.record("eod wrap", _rows())

    rows = runlog.rows()
    assert [row.outcome for row in rows] == [OK, UNREADABLE, OK]
    assert "could not be read" in rows[1].line()
    assert "failed" not in rows[1].line(), "it may well be a run that succeeded"
    assert not rows[1].completed, "an unreadable row is neither a success nor a failed run"
    assert runlog.last_completed_run() == good


def test_an_unreadable_rows_timestamp_is_kept_only_if_it_still_looks_like_one(runlog, log):
    """The last unvetted field on the projection line.

    Everything else there is checked; `at` on the unreadable path comes from a
    payload nobody could parse, and a schema that made it a record would print
    its `str()`, urls and all, into the synced file.
    """
    log.record(RUN, at={"start": "https://example.invalid/?token=nope"}, loop="morning brief")
    log.record(RUN, at="2026-09-09T06:40:00-07:00", loop="morning brief", surprise="?")

    first, second = runlog.rows()
    assert first.at == "unknown time"
    assert "token=nope" not in runlog.projection_line("morning brief")
    assert second.at == "2026-09-09T06:40:00-07:00", "a real stamp is still worth keeping"


def test_a_skip_field_that_is_not_text_is_refused_on_the_way_in(runlog, log):
    """The write-side gate matches the read-side one.

    Gating only `name` let this serialise cleanly and come back `UNREADABLE` on
    every later read - a run that succeeded, reported forever as unparseable.
    """
    with pytest.raises(ValueError, match="reason that is not text"):
        runlog.record(
            "morning brief",
            [{"name": "jira", "source": "jira", "status": observe.SKIPPED, "reason": None}],
        )

    assert runlog.rows()[0].outcome != UNREADABLE, "what it did write must stay readable"


def test_an_unreadable_row_keeps_the_loop_it_can_still_read(runlog, log):
    """Enough to say which loop lost a row, which is the diagnosis."""
    log.record(RUN, at="2026-09-08T07:00:00-07:00", loop="noon chaser", surprise="?")
    assert runlog.rows("noon chaser")[0].outcome == UNREADABLE


def test_a_list_a_newer_schema_enriched_is_caught_here_and_not_later(runlog, log):
    """The keys are all present, so only a type check catches this one.

    Left unchecked it parses, and then raises from inside `line()` - well past
    the point where `rows` could have degraded it, and inside the projection
    that is supposed to be the thing still working.
    """
    log.record(
        RUN,
        at="2026-09-08T07:00:00-07:00",
        loop="morning brief",
        outcome=OK,
        reached=[{"name": "calendar", "ms": 12}],
    )

    assert runlog.rows()[0].outcome == UNREADABLE
    assert runlog.projection_lines(), "the projection must still render"


def test_an_enriched_skip_reason_never_reaches_the_projection_verbatim(runlog, log):
    """The one stored list whose contents are rendered into the vault line.

    `Skip(**entry)` accepts any value types, so an unchecked skip parses cleanly
    and `Skip.line` then prints whatever it holds straight into the line that
    `free_text=False` exists to keep free of connector-chosen text.
    """
    log.record(
        RUN,
        at="2026-09-08T07:00:00-07:00",
        loop="morning brief",
        outcome=DEGRADED,
        skipped=[
            {
                "name": "jira",
                "source": "jira",
                "reason": {"code": 403, "url": "https://example.invalid/?token=nope"},
                "detail": "",
            }
        ],
    )

    assert runlog.rows()[0].outcome == UNREADABLE
    assert "token=nope" not in runlog.projection_line("morning brief")


def test_a_skip_reason_outside_smokes_vocabulary_is_withheld_from_the_projection(runlog, log):
    """The string case, which no type check can catch.

    A reason is meant to be a word like `auth` that sends the reader somewhere.
    Nothing stops a caller putting a connector's sentence there, and the
    projection prints reasons verbatim into a synced markdown line.
    """
    leak = "403 https://example.invalid/?token=nope"
    log.record(
        RUN,
        at=SIX_FORTY.isoformat(),
        loop="morning brief",
        outcome=DEGRADED,
        skipped=[{"name": "jira", "source": "jira", "reason": leak, "detail": ""}],
    )

    assert leak in runlog.last_run().line(), "the log keeps what the connector said"
    projected = runlog.projection_line("morning brief")
    assert "token=nope" not in projected
    assert "jira (see the event log)" in projected, "the source is still named"


def test_a_payload_that_is_not_a_record_at_all_still_degrades(runlog, log):
    """The handler that exists to degrade must not raise on its own input class."""
    # Written under the public API, because `record` takes keywords and so can
    # only ever store a record. A future writer, or a hand-repaired row, has no
    # such manners - which is the whole reason the reader cannot assume one.
    log._db.execute("INSERT INTO events (kind, payload) VALUES (?, ?)", (RUN, "[1, 2, 3]"))
    log._db.commit()

    assert [row.outcome for row in runlog.rows()] == [UNREADABLE]
    # No readable loop name anywhere, so there is no loop to group under - which
    # is exactly when a caller has to name the loops it expects.
    assert runlog.projection_lines() == []
    (line,) = runlog.projection_lines(["morning brief"])
    assert UNREADABLE in line and "morning brief" in line


def test_a_row_that_cannot_say_which_loop_it_was_still_breaks_the_silence(runlog, log):
    """The subtlest form of the clean-line failure.

    If the `loop` key is the one a newer schema moved, every new row drops out
    of that loop's history, and the last readable row - from before the trouble
    started - answers as `last run:` with nothing saying anything is missing.
    """
    good = runlog.record("morning brief", _rows())
    log.record(RUN, at="2026-09-09T06:40:00-07:00", loopname="morning brief", outcome=OK)

    line = runlog.projection_line("morning brief")
    assert UNREADABLE in line, "the loop cannot be reported clean while a row is unparseable"
    assert f"last completed: {good.at}" in line
    assert "morning brief" in line, "and the line still says which loop it is about"


def test_an_unknown_loop_never_becomes_a_loop_of_its_own(runlog, log):
    """It is not a loop, and a line for it is a permanent phantom outage.

    Each real loop's line already reports the unparseable row, which is where
    the finding belongs - beside the loop it might have been about.
    """
    runlog.record("morning brief", _rows())
    runlog.record("noon chaser", _rows())
    log.record(RUN, at="2026-09-09T06:40:00-07:00", loopname="?", outcome=OK)

    assert runlog.loops() == ["morning brief", "noon chaser"]
    lines = runlog.projection_lines()
    assert len(lines) == 2
    assert len(set(lines)) == 2, "two loops must not render the same line"
    assert all(UNREADABLE in line for line in lines)


def test_the_projection_can_be_asked_about_loops_that_have_never_run(runlog):
    """Day one, or a lost event log: silence is the loudest thing there is.

    Grouping only by what the log holds renders nothing when it holds nothing,
    so `State.md` goes blank exactly where the failure would show.
    """
    assert runlog.projection_lines() == []

    lines = runlog.projection_lines(["morning brief", "noon chaser"])
    assert lines == [
        "last run: no run recorded for morning brief yet",
        "last run: no run recorded for noon chaser yet",
    ]


def test_an_expected_loop_that_has_run_is_not_listed_twice(runlog):
    runlog.record("morning brief", _rows())
    assert len(runlog.projection_lines(["morning brief", "noon chaser"])) == 2


def test_a_payload_that_will_not_decode_degrades_like_any_other_bad_row(runlog, log):
    """A torn write. It raises from inside the read, before any row can be blamed."""
    good = runlog.record("morning brief", _rows())
    log._db.execute(
        "INSERT INTO events (kind, payload) VALUES (?, ?)", (RUN, '{"at": "2026-09-09", "loo')
    )
    log._db.commit()

    assert [row.outcome for row in runlog.rows()] == [OK, UNREADABLE]
    assert f"last completed: {good.at}" in runlog.projection_line("morning brief")


def test_an_unreadable_last_row_is_not_reported_as_a_failed_run(runlog, log):
    """It may well have succeeded. All that is known is the row cannot be read."""
    good = runlog.record("morning brief", _rows())
    log.record(RUN, at="2026-09-08T07:00:00-07:00", loop="morning brief", surprise="?")

    line = runlog.projection_line("morning brief")
    assert "failed" not in line
    assert UNREADABLE in line
    assert f"last completed: {good.at}" in line


# --------------------------------------------------------------------------
# 16. a failure that says nothing is still a failure
# --------------------------------------------------------------------------


@pytest.mark.parametrize("blank", [RuntimeError(), RuntimeError("   "), RuntimeError("\n\t")])
def test_a_failure_with_no_message_is_not_scored_as_a_success(runlog, blank):
    """`str(RuntimeError())` is empty, and empty read as "no failure at all".

    The whitespace cases are the same bug one step later: `RuntimeError("   ")`
    is truthy, so a fallback applied before the trim never fires and the trim
    then produces the empty string anyway.
    """
    with pytest.raises(RuntimeError):
        with runlog.run("morning brief"):
            raise blank

    row = runlog.last_run()
    assert row.outcome == FAILED and not row.completed
    assert row.failure == "RuntimeError", "the type is all there is, so it is what is kept"
    assert "failed: RuntimeError" in row.line()


def test_the_direct_form_treats_a_blank_failure_the_same_way(runlog):
    """`record` is the API a caller reaches for with `failure=str(exc)` in hand."""
    row = runlog.record("morning brief", _rows(), failure=str(RuntimeError()))
    assert row.outcome == OK, "an empty string genuinely means no failure was reported"

    passed_the_object = runlog.record("morning brief", _rows(), failure=RuntimeError())
    assert passed_the_object.outcome == FAILED
    assert passed_the_object.failure == "RuntimeError"


def test_a_whitespace_only_failure_is_reported_rather_than_swallowed(runlog):
    row = runlog.record("morning brief", _rows(), failure="   \n ")
    assert row.outcome == FAILED
    assert "no message" in row.failure


def test_the_direct_form_can_be_told_when_the_run_started(runlog):
    """So `at` means the same thing whichever API wrote the row."""
    row = runlog.record("morning brief", _rows(), started=SIX_FORTY)
    assert row.at == SIX_FORTY.isoformat()


# --------------------------------------------------------------------------
# 17. the row is built from rows, and reads like him
# --------------------------------------------------------------------------


def test_the_row_is_built_from_smoke_rows_and_never_from_rendered_text(runlog):
    """The handover note: the rows are the reusable part, not the text.

    Passing rows that no `render()` could have produced still works, which is
    the proof that nothing here parses a rendered block back.
    """
    row = runlog.record(
        "morning brief",
        [
            {
                "name": "calendar",
                "source": "calendar",
                "status": observe.REACHED,
                "reason": "",
                "detail": "one day of events",
            }
        ],
    )
    assert row.reached == ("calendar",)


def test_the_line_holds_the_house_voice(runlog):
    """It can surface in a DM, so SPEC section 5 applies: hyphens, no stray emoji."""
    row = runlog.record("morning brief", _rows(jira=ConnectionError("401 unauthorized")))
    assert voice_violations(row.line()) == []
    assert voice_violations(runlog.projection_line("morning brief")) == []


def test_the_module_reads_no_clock_of_its_own(runlog):
    """Same constraint `smoke` holds, for the same reason: determinism.

    A module that can call `datetime.now()` will, and the test that was supposed
    to pin the timestamp starts passing for the wrong reason.

    Scanned as CODE, not as text. A substring scan of the whole file also
    fires on the module docstring's own usage example - and a contract that
    cannot be written down is worse documented for the sake of a cruder check.
    Docstrings and comments cannot call anything, so dropping them costs the
    guardrail nothing.
    """
    tree = ast.parse(Path(observe.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            node.value = ast.Constant(value="")  # a docstring, not a call
    code = ast.unparse(tree)  # also drops every comment

    for banned in ("datetime.now", "utcnow", "time.time", "time.monotonic"):
        assert banned not in code, f"{banned} called in observe.py - the clock is injected"
