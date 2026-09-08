"""One pre-flight pass over every source: what was reached, what was skipped.

Three jobs, and they are the same code because they need the same answer. It is
the check that runs before a loop starts, so the loop knows what it is working
with. It is guardrail 6 made concrete - a brief ships with a one-line
"couldn't check X" rather than stalling or guessing. And its rows are the format
the run log (#26) appends.

**The thing this module exists to catch is overflow, not authentication.** The
#2 audit blew the output limit three times: Calendar at 156,681 chars for five
days, Jira at 125,231 for a 14-day query, the Jira project list at 60,000. None
of them raised, and none of them returned an error - an over-limit response
simply never arrives, so the loop that asked sees nothing and the brief reports
a quiet day. A smoke test that asked "did the call succeed" would have been
green through all three.

So a probe is never trusted because it failed to raise. Every check asserts a
*plausible shape came back*: a record with the fields it should have, a resolved
id that is actually an id, `SELECT 1` answering with 1. Where a source can be
honestly empty (a clear day on the calendar) the check says so and says why;
where emptiness is indistinguishable from a silent failure (Slack resolving a
display name, a JQL naming a dormant project) it is a skip, because the cost of
being wrong is one honest apology line against a board reported clear.

Nothing here is a client. Every probe is injected by the caller, which is what
lets the suite run offline and keeps the connector tripwire in
`tests/test_guardrails.py` meaningful: this module cannot reach anything.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from daydag.recipes import (
    GEMINI_LABEL,
    GEMINI_SENDER,
    GH_LIMIT_CAP,
    JIRA_FIELDS,
    JIRA_MAX_RESULTS_CAP,
)

# The bounds below describe what keeps each query small enough to answer, and
# `recipes` is what actually keeps it there. Importing the caps rather than
# retyping them is the difference between a description and a restatement: a
# cap that moves takes the report's wording with it. Constants only - no query
# is built here, and no client comes with them.
from daydag.voice import clipped

# -- statuses ---------------------------------------------------------------

REACHED = "reached"
SKIPPED = "skipped"
#: Not a failure. Granola was deliberately never wired up, and reporting it as
#: an outage sends someone to fix it every morning for the rest of time.
NOT_CONNECTED = "not connected"

# -- skip reasons -----------------------------------------------------------

#: The caller passed no probe for this check, so nothing was asked at all.
NO_PROBE = "no probe"
AUTH = "auth"
#: The answer was too big to arrive. The expensive case: it looks like silence.
OVERFLOW = "overflow"
#: It answered, and the answer is not what this source looks like when it works.
SHAPE = "shape"
#: A mirror could not be fetched. Deliberately distinct from an API failure -
#: one is fixed with `gh auth`, the other with a clone on disk, and reporting
#: either as "github is down" sends the reader to the wrong place.
FETCH = "fetch"
FAILED = "failed"

#: Above this, a response never reaches the model. Five days of calendar
#: measured 156,681 chars, so a day is roughly 31k and the ceiling has to catch
#: the *second* day - otherwise "just widen it to the week", the exact mistake
#: the audit recorded, passes the smoke run.
OUTPUT_CEILING_CHARS = 50_000

#: A detail is one line in a brief, not a stack trace pasted into a DM.
DETAIL_LIMIT = 120

# -- reading what a failure actually was ------------------------------------

#: An over-limit response, however the connector phrases it. Deliberately not a
#: bare "too many": a 429 is routine from Slack, GitHub and Jira and carries the
#: opposite fix - an overflow says bound the query harder, a rate limit says ask
#: again later, and reading one as the other sends the reader to rewrite a query
#: that was never the problem.
_OVERFLOW = re.compile(
    r"exceed|too (?:large|long|big)|too many (?:results|records|rows|tokens|characters|bytes)"
    r"|maximum (?:length|size)|output limit|truncat|token limit",
    re.IGNORECASE,
)
#: The Databricks trap: expired OAuth surfaces as a complaint about the shape of
#: the output rather than as an auth error, so an operator reading the report
#: goes and looks at the query. Classified here so nobody has to learn it twice.
_SCHEMA_TRAP = re.compile(r"outputschema|no structured output", re.IGNORECASE)
_AUTH = re.compile(
    r"\bauth|401|403|unauthor|forbidden|credential|token (?:expired|invalid)"
    r"|expired|permission denied|not logged in",
    re.IGNORECASE,
)
_LOGIN_FIX = "expired oauth reads exactly like this, so re-run the workspace login first"


def _one_line(text: str, room_for: str = "") -> str:
    """A failure message, trimmed to something a brief can carry.

    ``room_for`` is the text that will be appended afterwards, and the trim
    leaves space for it. Trimming first and appending second is how the
    Databricks fix got cut mid-word off the one message it exists to annotate:
    a real schema error names the tool and the endpoint and is already past the
    limit on its own.

    The collapse-and-clip itself is `voice.clipped`; what stays here is this
    module's budget and the reservation, which are the parts that are ours.
    """
    return clipped(text, DETAIL_LIMIT - len(room_for))


def _classify(text: str, default: str = "") -> tuple[str, str]:
    """``(reason, detail)`` for a failure message, or ``("", "")`` if it is not one.

    Applied to raised exceptions *and* to returned strings, because a connector
    is as likely to hand back its error as to throw it, and the two are the same
    failure. Order matters: an overflow often mentions a limit, and the schema
    trap must be read before the generic auth patterns so it keeps its fix.
    """
    if _OVERFLOW.search(text):
        return OVERFLOW, _one_line(text)
    if _SCHEMA_TRAP.search(text):
        suffix = f"; {_LOGIN_FIX}"
        return AUTH, _one_line(text, room_for=suffix) + suffix
    if _AUTH.search(text):
        return AUTH, _one_line(text)
    return (default, _one_line(text)) if default else ("", "")


#: Where a connector puts its error when it hands one back instead of raising.
#: The shape MCP and REST clients actually use, which the string-only reading
#: missed entirely: a `{"error": {"code": 401}}` fell through to the plausibility
#: check and reported "no event list came back", never mentioning the 401.
#: "message" is deliberately absent: it is only an error when it sits under one
#: of these, and `_flatten` already reads it there.
_ERROR_KEYS = ("error", "errors", "errorMessages", "error_description")


def _error_text(payload: Any) -> str:
    """The error a payload is carrying, flattened, or ``""`` if it carries none."""
    if not isinstance(payload, Mapping):
        return ""
    for key in _ERROR_KEYS:
        if payload.get(key):
            return " ".join(_flatten(payload[key]))
    return ""


def _flatten(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return [part for item in value.values() for part in _flatten(item)]
    if isinstance(value, list | tuple):
        return [part for item in value for part in _flatten(item)]
    return [str(value)]


def _measure(payload: Any) -> int:
    """Roughly how much text this payload would occupy on the way back."""
    return len(payload if isinstance(payload, str) else repr(payload))


# -- plausibility: what each source looks like when it genuinely answered ----

#: Keys a connector puts its records under. Checked in order, first list wins.
_RECORD_KEYS = (
    "events",
    "items",
    "messages",
    "threads",
    "issues",
    "repositories",
    "members",
    "results",
    "rows",
    "values",
    "data",
)


def _records(payload: Any) -> list[Any] | None:
    """The record list inside a payload, or ``None`` if there is not one.

    ``None`` and ``[]`` are different answers where a check can use the
    difference: no list at all means the call did not return this source's
    shape, an empty list means it did and matched nothing. Calendar, Jira and
    the GitHub API branch on it separately. Gmail, Slack and Notion do not -
    for those, zero records is a broken query either way, so both collapse to
    one message on purpose.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        for key in _RECORD_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return None


def _has(record: Any, *keys: str) -> bool:
    """Whether the record carries *any* of these, for keys that are alternatives."""
    return isinstance(record, Mapping) and any(record.get(key) for key in keys)


def _has_all(record: Any, *keys: str) -> bool:
    """Whether the record carries *every* one of these.

    Separate from `_has` because the difference is where two checks were wrong:
    `any` on `("id", "subject")` let Gmail's metadata-only search results
    through on the strength of the id, and the subject is the whole point.
    """
    return isinstance(record, Mapping) and all(record.get(key) for key in keys)


def _first_value(row: Any) -> Any:
    if isinstance(row, Mapping):
        return next(iter(row.values()), None)
    if isinstance(row, list | tuple):
        return row[0] if row else None
    return row


def _check_calendar(payload: Any) -> str | None:
    """One day of events. Empty is honest here and nowhere else.

    A day can genuinely be clear, so a count of zero is not evidence of a
    failure - and an overflow cannot hide behind that, because the ceiling
    catches it before this runs.
    """
    events = _records(payload)
    if events is None:
        return "no event list came back"
    if not all(_has(event, "id", "summary") for event in events):
        return "an entry has neither an id nor a summary, so it is not an event"
    if not all(_has_all(event, "start") for event in events):
        return "an event has no start, so the day cannot be ordered or prepped"
    return None


def _check_gmail(payload: Any) -> str | None:
    """Gemini notes, by sender and the `meeting notes` label.

    201 notes landed in a 30-day window when this was measured, so zero is a
    broken query rather than a quiet month.
    """
    notes = _records(payload)
    if not notes:
        return "no gemini note came back; 201 landed in the measured 30-day window"
    if not all(_has_all(note, "subject") for note in notes):
        return "a result has no subject, so it is metadata and carries no title"
    return None


def _check_slack(payload: Any) -> str | None:
    """One id resolved. The empty answer is the whole reason this check exists.

    A display-name lookup does not fail, it returns nothing - so nothing can
    never be read as reached.
    """
    if _has(payload, "id", "user"):
        return None
    resolved = _records(payload) or []
    if not resolved:
        return "nothing resolved, and a display-name lookup fails silently, so ask by id"
    if not all(_has(entry, "id", "user") for entry in resolved):
        return "a resolved entry carries no id"
    return None


def _check_notion(payload: Any) -> str | None:
    """The authenticated user."""
    if _has(payload, "id", "name", "person", "bot"):
        return None
    people = _records(payload) or []
    if not people:
        return "no authenticated user came back"
    if not all(_has(person, "id", "name") for person in people):
        return "the authenticated user has no id"
    return None


def _check_jira(payload: Any) -> str | None:
    """Issues from a bounded JQL on the live project.

    The audit queried four projects and every result came from one of them: the
    other three are dormant, not quiet, and a dormant project answers exactly
    like a healthy one with nothing in the window. So zero rows is a skip. The
    cost of being wrong is one "couldn't check jira" line; the cost of the other
    reading is a board reported clear that was never really queried.
    """
    issues = _records(payload)
    if issues is None:
        return "no issue list came back"
    if not issues:
        return "the bounded jql matched nothing, which is what a dormant project looks like"
    if not all(_has(issue, "key") for issue in issues):
        return "an issue has no key, and the key is the join to slack and prs"
    return None


def _check_github_api(payload: Any) -> str | None:
    """The `gh` API half: whatever the account can see."""
    if _has(payload, "login"):
        return None
    repos = _records(payload)
    if repos is None:
        return "no repository list came back"
    if not repos:
        return "the api answered with no repositories at all"
    if not all(_has(repo, "name", "full_name") for repo in repos):
        return "an entry names no repository"
    return None


def _check_github_mirror(payload: Any) -> str | None:
    """The mirror half: one fetch, against a clone that already exists.

    A verdict, not a payload. Anything that is not a clear success is a fetch
    failure, and the pulse then reports history as of the last run.
    """
    if payload is True:
        return None
    if isinstance(payload, Mapping):
        if payload.get("ok") is True or payload.get("fetched") is True:
            return None
        return "the fetch reported no success"
    if payload is False or payload is None:
        return "the fetch failed, so repo history is only as of the last run"
    return "the fetch answered with something that is not a verdict"


def _check_warehouse(payload: Any) -> str | None:
    """`SELECT 1`, and the answer has to be 1.

    Never verified against a live workspace. "It returned something" is not
    proof the warehouse answered, because the way this source fails is by
    handing back something unstructured that a looser check would accept.
    """
    rows = _records(payload)
    if rows is None:
        return f"no structured rows came back; {_LOGIN_FIX}"
    if not rows:
        return "select 1 returned no rows at all"
    answer = _first_value(rows[0])
    # `True == 1` in Python, so the bool is excluded explicitly. A driver
    # handing back a boolean is not the warehouse answering 1, and accepting it
    # is the same "it returned something" reading this check exists to refuse.
    if isinstance(answer, bool) or answer not in (1, "1", 1.0):
        return f"select 1 came back as {answer!r}, so the warehouse is not answering"
    return None


# -- the checks -------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    """One probe of one source, and what a real answer from it looks like.

    ``reaches`` and ``bound`` are carried rather than commented because they are
    the two things a reader of the report needs: what was actually proven, and
    what kept the query small enough to prove it.
    """

    name: str
    source: str
    #: What "reached" means for this source, in the report's own words.
    reaches: str
    #: The bound that keeps this query under the output ceiling.
    bound: str
    plausible: Callable[[Any], str | None] | None = None
    #: Reason used when the probe raises something unrecognised.
    default_reason: str = FAILED
    #: Reason used when the payload arrives but is not this source's shape.
    shape_reason: str = SHAPE
    #: False for a source that was deliberately never connected.
    wired: bool = True
    note: str = ""


#: Every source, in report order. GitHub is two checks under one source because
#: its two halves fail apart and are fixed apart.
CHECKS: tuple[Check, ...] = (
    Check(
        name="calendar",
        source="calendar",
        reaches="one day of events",
        bound="a single day - five days measured 156,681 chars and never arrived",
        plausible=_check_calendar,
    ),
    Check(
        name="gmail",
        source="gmail",
        reaches="gemini notes by sender and the meeting notes label",
        bound=f"one sender ({GEMINI_SENDER}), the {GEMINI_LABEL!r} label, a dated window",
        plausible=_check_gmail,
    ),
    Check(
        name="slack",
        source="slack",
        reaches="one id resolved",
        bound="one id, never a display-name search",
        plausible=_check_slack,
    ),
    Check(
        name="notion",
        source="notion",
        reaches="the authenticated user",
        bound="one call, no page search",
        plausible=_check_notion,
    ),
    Check(
        name="jira",
        source="jira",
        reaches="issues from a bounded jql on the live project",
        bound=(
            f"one project, a dated window, {len(JIRA_FIELDS)} named fields - "
            f"never all of them - and at most {JIRA_MAX_RESULTS_CAP} issues"
        ),
        plausible=_check_jira,
    ),
    Check(
        name="github api",
        source="github",
        reaches="the api answering for this account",
        bound=f"one page, at most {GH_LIMIT_CAP}",
        plausible=_check_github_api,
    ),
    Check(
        name="github mirror",
        source="github",
        reaches="one mirror fetched",
        bound="one already-cloned mirror",
        plausible=_check_github_mirror,
        default_reason=FETCH,
        shape_reason=FETCH,
    ),
    Check(
        name="databricks",
        source="databricks",
        reaches="select 1 answering with 1",
        bound="one row",
        plausible=_check_warehouse,
    ),
    Check(
        name="granola",
        source="granola",
        reaches="nothing is asked of it",
        bound="nothing is queried",
        wired=False,
        note="not wired up on purpose, the gemini notes cover the corpus",
    ),
)


# -- results ----------------------------------------------------------------


@dataclass(frozen=True)
class Result:
    """One check's outcome. Every field a string, so a log row needs no coercion."""

    name: str
    source: str
    status: str
    reason: str = ""
    detail: str = ""

    @property
    def reached(self) -> bool:
        return self.status == REACHED

    def as_row(self) -> dict[str, str]:
        return {
            "name": self.name,
            "source": self.source,
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
        }

    def line(self) -> str:
        if self.status == SKIPPED:
            return f"- {self.name}: {SKIPPED} ({self.reason}) - {self.detail}"
        return f"- {self.name}: {self.status} - {self.detail}"


@dataclass(frozen=True)
class SmokeReport:
    """What one pass reached and what it skipped, as rows before it is text."""

    results: tuple[Result, ...]

    @property
    def reached(self) -> tuple[str, ...]:
        return tuple(result.name for result in self.results if result.reached)

    @property
    def skipped(self) -> tuple[str, ...]:
        return tuple(result.name for result in self.results if result.status == SKIPPED)

    @property
    def not_connected(self) -> tuple[str, ...]:
        return tuple(result.name for result in self.results if result.status == NOT_CONNECTED)

    @property
    def reached_sources(self) -> tuple[str, ...]:
        """Sources every one of whose checks answered.

        The conjunction, not the disjunction: GitHub with a dead mirror is not
        GitHub, and a caller deciding whether to trust a section of the brief
        needs the pessimistic answer.
        """
        order = list(dict.fromkeys(result.source for result in self.results))
        by_source: dict[str, bool] = {}
        for result in self.results:
            by_source[result.source] = by_source.get(result.source, True) and result.reached
        return tuple(source for source in order if by_source[source])

    @property
    def ok(self) -> bool:
        """Whether every source that is connected at all answered."""
        return not self.skipped

    def as_rows(self) -> list[dict[str, str]]:
        return [result.as_row() for result in self.results]

    def degrade_lines(self) -> list[str]:
        """Guardrail 6, one line per source the loop could not read.

        A deliberately unconnected source produces nothing here: it is not a
        failure, and an apology for it would be a permanent line in the brief.
        """
        return [
            f"couldn't check {result.name} - {result.detail or result.reason}"
            for result in self.results
            if result.status == SKIPPED
        ]

    def summary(self) -> str:
        return (
            f"sources: {len(self.reached)} reached, {len(self.skipped)} skipped, "
            f"{len(self.not_connected)} not connected"
        )

    def render(self) -> str:
        """The block a run log keeps and a brief reads the failures out of."""
        return "\n".join([self.summary(), *(result.line() for result in self.results)])


# -- the run ----------------------------------------------------------------


def _probe_once(check: Check, probe: Callable[[], Any] | None) -> Result:
    """Run one probe, exactly once, and turn whatever happened into a row.

    Exactly once matters: a retry hides a flaky source and doubles the wait on a
    6:40am loop that is supposed to degrade rather than stall.
    """

    def skipped(reason: str, detail: str) -> Result:
        return Result(check.name, check.source, SKIPPED, reason, detail)

    if not check.wired:
        return Result(check.name, check.source, NOT_CONNECTED, detail=check.note)
    if probe is None:
        return skipped(NO_PROBE, "no probe was supplied, so this source was not checked at all")
    try:
        payload = probe()
    except Exception as failure:
        # Deliberately everything. A connector client raises whatever it likes,
        # and guardrail 6 says the brief ships regardless - so the only wrong
        # answer here is letting one source take the run down with it.
        reason, detail = _classify(str(failure) or type(failure).__name__, check.default_reason)
        return skipped(reason, detail)

    if isinstance(payload, str):
        # An error handed back as text rather than thrown. Only a recognised
        # one: a bare string can also be a bad answer, and the plausibility
        # check below has more to say about that than "failed" would.
        reason, detail = _classify(payload)
        if reason:
            return skipped(reason, detail)
    elif carried := _error_text(payload):
        # The error object MCP and REST clients actually use. A populated error
        # field is a failed call whether or not its wording matches a pattern,
        # so this one takes the check's default reason rather than falling
        # through to a shape complaint that never mentions the 401.
        return skipped(*_classify(carried, check.default_reason))

    size = _measure(payload)
    if size > OUTPUT_CEILING_CHARS:
        return skipped(
            OVERFLOW,
            f"{size} chars came back, over the {OUTPUT_CEILING_CHARS} ceiling - bound it harder",
        )

    wrong = check.plausible(payload) if check.plausible else None
    if wrong:
        # Trimmed here rather than in each check, for the same reason the vault
        # filters sensitivity at the writer: this is the boundary that has to
        # hold when a caller forgets. A check builds its message from the
        # payload - `_check_warehouse` interpolates the answer it got - and an
        # untrimmed one put a page of garbled driver output into the line the
        # brief ships. `_classify` already trims the raise path; this is its
        # twin, and it was the half that was missing.
        return skipped(check.shape_reason, _one_line(wrong))
    return Result(check.name, check.source, REACHED, detail=check.reaches)


def run(
    probes: Mapping[str, Callable[[], Any]] | None = None,
    *,
    checks: Sequence[Check] = CHECKS,
) -> SmokeReport:
    """Touch every source once and report what answered.

    Probes are injected and keyed by check name. A name that is not a check is
    refused rather than ignored: a typo would otherwise leave that source
    unprobed and reported as "no probe", which reads like configuration rather
    than like the mistake it is.

    A typo is judged against every check that exists, not against a narrowed
    ``checks`` run. Otherwise passing the full probe map to a one-check run -
    the obvious way to use the parameter - rejects every other probe as a typo.
    """
    supplied = dict(probes or {})
    known = {check.name for check in (*CHECKS, *checks)}
    unknown = sorted(set(supplied) - known)
    if unknown:
        raise ValueError(f"no such check: {', '.join(unknown)}. known: {', '.join(sorted(known))}")
    return SmokeReport(tuple(_probe_once(check, supplied.get(check.name)) for check in checks))
