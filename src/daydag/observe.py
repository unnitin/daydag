"""What ran and what it reached: the pre-flight over injected probes, and the run log.

USING IT
    report = run({"calendar": lambda: client.day(...), "jira": ...})   # every source, once
    report.degrade_lines()                  # "couldn't check X - <detail>" per skip
    report.as_rows()                        # what the run log takes - never render()

    log = RunLog(EventLog.open(path), clock=lambda: datetime.now(UTC))
    with log.run("morning brief") as run:   # writes a row either way, re-raises
        run.observe(report.results)
    log.last_run("morning brief")           # RunRow | None
    log.projection_line("morning brief")    # the line State.md carries

CONTRACTS
    1. One row shape. `Result` (name · source · status · reason · detail) is
       what a probe produces, what a run observes, what a row stores and what
       the projection prints. It was converted five times between two
       modules; now it is type-checked once, on the way in (`Result.from_row`).
    2. Never trust a probe because it did not raise. Every check asserts a
       plausible SHAPE came back, and a response over `OUTPUT_CEILING_CHARS`
       is an overflow, not a quiet day - the failure the #2 audit met three
       times and a "did it succeed" check would have called green.
    3. Nothing here is a client. Every probe is injected; the package cannot
       reach a connector on its own, and the tripwire in `test_guardrails.py`
       holds that open.
    4. Rows live in the event log, never in the vault. `projection_line`
       RETURNS a line and withholds free text: a connector's 403 can quote the
       url it was refused, and a synced plaintext file must not carry it.
    5. Pass the exception, not `str(exc)` - `str(RuntimeError())` is empty and
       an empty failure scores the run as a success. The clock is injected.

WHY IT EXISTS
    There is no platform observability behind this agent. The failure mode is
    him noticing a brief did not arrive, by which time the process is gone and
    nothing says whether the run started, which source went quiet, or whether
    it died halfway. This is the only record that outlives the run.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from daydag.recipes import (
    GEMINI_LABEL,
    GEMINI_SENDER,
    GH_LIMIT_CAP,
    JIRA_FIELDS,
    JIRA_MAX_RESULTS_CAP,
    error_text,
    first_value,
    has,
    has_all,
    measure,
    records,
)
from daydag.voice import clipped

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from daydag.eventlog import EventLog

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


# -- plausibility: what each source looks like when it genuinely answered ----
#
# The readers live in `daydag.recipes`: finding a record list or an error
# object is the same job at every connector edge, and it was private here only
# because this was the first edge to need it. What stays in this module is the
# per-source judgement - which is the half that actually differs, and the half
# that was wrong seven times in section 10 below.


def _check_calendar(payload: Any) -> str | None:
    """One day of events. Empty is honest here and nowhere else.

    A day can genuinely be clear, so a count of zero is not evidence of a
    failure - and an overflow cannot hide behind that, because the ceiling
    catches it before this runs.
    """
    events = records(payload)
    if events is None:
        return "no event list came back"
    if not all(has(event, "id", "summary") for event in events):
        return "an entry has neither an id nor a summary, so it is not an event"
    if not all(has_all(event, "start") for event in events):
        return "an event has no start, so the day cannot be ordered or prepped"
    return None


def _check_gmail(payload: Any) -> str | None:
    """Gemini notes, by sender and the `meeting notes` label.

    201 notes landed in a 30-day window when this was measured, so zero is a
    broken query rather than a quiet month.
    """
    notes = records(payload)
    if not notes:
        return "no gemini note came back; 201 landed in the measured 30-day window"
    if not all(has_all(note, "subject") for note in notes):
        return "a result has no subject, so it is metadata and carries no title"
    return None


def _check_slack(payload: Any) -> str | None:
    """One id resolved. The empty answer is the whole reason this check exists.

    A display-name lookup does not fail, it returns nothing - so nothing can
    never be read as reached.
    """
    if has(payload, "id", "user"):
        return None
    resolved = records(payload) or []
    if not resolved:
        return "nothing resolved, and a display-name lookup fails silently, so ask by id"
    if not all(has(entry, "id", "user") for entry in resolved):
        return "a resolved entry carries no id"
    return None


def _check_notion(payload: Any) -> str | None:
    """The authenticated user."""
    if has(payload, "id", "name", "person", "bot"):
        return None
    people = records(payload) or []
    if not people:
        return "no authenticated user came back"
    if not all(has(person, "id", "name") for person in people):
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
    issues = records(payload)
    if issues is None:
        return "no issue list came back"
    if not issues:
        return "the bounded jql matched nothing, which is what a dormant project looks like"
    if not all(has(issue, "key") for issue in issues):
        return "an issue has no key, and the key is the join to slack and prs"
    return None


def _check_github_api(payload: Any) -> str | None:
    """The `gh` API half: whatever the account can see."""
    if has(payload, "login"):
        return None
    repos = records(payload)
    if repos is None:
        return "no repository list came back"
    if not repos:
        return "the api answered with no repositories at all"
    if not all(has(repo, "name", "full_name") for repo in repos):
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
    rows = records(payload)
    if rows is None:
        return f"no structured rows came back; {_LOGIN_FIX}"
    if not rows:
        return "select 1 returned no rows at all"
    answer = first_value(rows[0])
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

    def fragment(self, *, free_text: bool = True) -> str:
        """This skip inside a run row: `jira (auth)`.

        `free_text=False` prints the reason only if it is one of the pre-flight's
        own - a closed vocabulary of words like `auth` and `overflow`. This
        fragment reaches the line offered to `State.md`, and a reason carrying
        a connector's own sentence (a url with a token) must not land in a
        synced plaintext file. The log keeps the reason either way.
        """
        reason = self.reason
        if not free_text and reason not in _KNOWN_REASONS:
            reason = WITHHELD
        return f"{self.name} ({reason})" if reason else self.name

    @classmethod
    def from_row(cls, entry: Any, *, strict_status: bool = False) -> Result:
        """One stored row back into a `Result`, type-checked field by field.

        The ONE check on the way in. `Result(**entry)` accepts any value types,
        and `fragment` renders the reason into the `State.md` line - so a row
        that is not text degrades to `UNREADABLE` like the rest instead of
        printing whatever it holds. ``strict_status`` refuses a status outside
        the three the run log knows, for rows arriving from a caller rather
        than from the log.
        """
        if not isinstance(entry, Mapping):
            raise TypeError(f"a row is {type(entry).__name__}, not a record")
        name = entry.get("name", "")
        if not name or not isinstance(name, str):
            raise ValueError(f"a row carries no check name: {dict(entry)!r}")
        status = entry.get("status", "")
        if strict_status and status not in (REACHED, SKIPPED, NOT_CONNECTED):
            raise ValueError(
                f"{name!r} carries status {status!r}; expected reached/skipped/not connected"
            )
        for key in ("source", "reason", "detail"):
            if not isinstance(entry.get(key, ""), str):
                raise ValueError(f"{name!r} carries a {key} that is not text: {entry[key]!r}")
        return cls(
            name=name,
            source=str(entry.get("source", "")),
            status=str(status),
            reason=str(entry.get("reason", "")),
            detail=str(entry.get("detail", "")),
        )


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
    elif carried := error_text(payload):
        # The error object MCP and REST clients actually use. A populated error
        # field is a failed call whether or not its wording matches a pattern,
        # so this one takes the check's default reason rather than falling
        # through to a shape complaint that never mentions the 401.
        return skipped(*_classify(carried, check.default_reason))

    size = measure(payload)
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


#: The event-log `kind` every run row is written under. One kind, so a reader
#: can pull the run history without knowing which loops ever existed.
RUN = "run"

#: Every source that is connected at all answered.
OK = "ok"
#: The run finished and shipped, with at least one source named as unreachable.
#: Guardrail 6 working, not the run failing.
DEGRADED = "degraded"
#: The run raised before it finished (the same word `smoke` uses for a probe
#: that raised - one literal, one meaning: something did not get through).
#: The run reported no failure and also never observed a single source. Not `OK`:
#: a loop that bailed before the smoke run, or that swallowed its own connector
#: error and forgot to report it, looks identical to a healthy one otherwise -
#: and "the agent last ran successfully at 6:40" would then be a lie told by a
#: run that read nothing.
UNCHECKED = "unchecked"
#: A row in the log that this version of the code cannot parse. Not an outcome
#: any run produces - it is what a *reader* reports when the schema has moved
#: under it, so one bad row degrades instead of taking the history with it.
UNREADABLE = "unreadable"

#: The outcomes that mean the run got to the end. Named as a set rather than as
#: `!= FAILED` so an outcome nobody has invented yet - `UNREADABLE` was the
#: first - is not silently counted as a success.
_COMPLETED = (OK, DEGRADED)

#: A failure trimmed to something one line can carry. A run row is read in a
#: list of run rows; a pasted traceback makes the whole list unreadable, and the
#: traceback itself was never in the log to begin with.
FAILURE_LIMIT = 160

#: Rendered where a list would otherwise be empty. "reached:" followed by
#: nothing reads like a tidy run; `reached: none` reads like what it is.
NONE = "none"

#: Stands in for the failure text where the text itself must not be repeated -
#: the `State.md` projection. See `RunRow.line`.
WITHHELD = "see the event log"

#: A failure that was reported without saying anything. Better than the empty
#: string, which is indistinguishable from no failure at all.
UNSTATED = "failure reported with no message"

_STATUSES = (REACHED, SKIPPED, NOT_CONNECTED)

#: `smoke`'s own skip reasons, and the empty string a reached row carries. The
#: closed vocabulary the projection is allowed to print: a reason is a word like
#: `auth` or `overflow` that sends the reader somewhere, never a sentence a
#: connector wrote. A reason outside this set is still kept in the log - see
#: `Skip.line`.
_KNOWN_REASONS = frozenset({"", NO_PROBE, AUTH, OVERFLOW, SHAPE, FETCH, FAILED})

#: The loop of a row whose `loop` field could not be read. Not a real loop: it
#: means "some loop ran and this row cannot say which", and it is never itself
#: reported as a loop - such a row is folded into every real loop's history.
UNKNOWN_LOOP = "?"

#: The timestamp of a row whose `at` could not be read. Its own sentinel: a row
#: that cannot say *when* and a row that cannot say *which loop* are different
#: unknowns, and sharing one marker renders "? · ?" and reads like a bug.
UNKNOWN_AT = "unknown time"


def _failure_line(text: str) -> str:
    """A run row's budget for a failure, by the shared collapse-then-clip."""
    return clipped(text, FAILURE_LIMIT)


def _describe(failure: str | BaseException) -> str:
    """A failure as one line, however the caller reported it.

    Takes the exception itself as well as a string, because the natural call -
    `failure=str(exc)` - is empty for `RuntimeError()`, `KeyError()` and every
    other bare-arg exception, and a row whose failure field is blank is scored
    as a successful run. Passing the exception is the preferred form; a string
    that says nothing is turned into one that admits it rather than trusted.
    """
    if isinstance(failure, BaseException):
        # Trim FIRST, then fall back. `RuntimeError("   ")` is truthy, so
        # falling back on the raw string leaves the type name unused and the
        # trim then produces "" - a failed run scored as a clean one.
        return _failure_line(str(failure)) or type(failure).__name__
    trimmed = _failure_line(failure)
    return UNSTATED if failure and not trimmed else trimmed


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is {type(value).__name__}, not text")
    return value


#: What an ISO-8601 stamp is made of, and nothing else.
_STAMP_CHARS = frozenset("0123456789-:.+TZ ")


def _timestamp_or_unknown(value: Any) -> str:
    """A stored `at`, kept only if it still looks like a timestamp.

    Used on the unreadable path, where the payload is by definition one this
    version could not parse - so its `at` is arbitrary text that would otherwise
    be interpolated straight into the `State.md` projection line. Everything
    else on that line is vetted; a schema that made `at` a record would print
    its `str()`, urls and all, into a plaintext file synced to every device.
    """
    if isinstance(value, str) and value and len(value) <= 40 and set(value) <= _STAMP_CHARS:
        return value
    return UNKNOWN_AT


def _names(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"{field} is not a list of check names")
    return tuple(value)


@dataclass(frozen=True)
class RunRow:
    """One run: `timestamp · loop · sources reached · sources skipped`.

    Frozen, and every field either a string or a tuple of them, so the row that
    comes back out of SQLite compares equal to the one that went in.
    """

    #: When the run *started*, ISO-8601, from the caller's clock. Start rather
    #: than finish because that is the end a reader has in hand: the brief was
    #: due at 6:40 and never came.
    at: str
    loop: str
    outcome: str
    reached: tuple[str, ...] = ()
    skipped: tuple[Result, ...] = ()
    #: Sources deliberately never wired up. Kept for the reader who wonders why
    #: a source is in neither list, and left out of `line()` because an apology
    #: for Granola would be a permanent line in every run of every loop.
    not_connected: tuple[str, ...] = ()
    failure: str = ""

    @property
    def completed(self) -> bool:
        """Whether the run got to the end having actually read something.

        A degraded run did - naming a source it could not reach is guardrail 6
        working. A failed one did not, and neither did one that observed no
        source at all, because "it last ran fine" has to mean it read something.
        """
        return self.outcome in _COMPLETED

    def line(self, *, free_text: bool = True) -> str:
        """The one line SPEC section 7 asks for, plus the failure when there was one.

        `free_text=False` is the form for a line leaving the log: it keeps the
        fact of a failure and drops its wording, and holds each skip's reason to
        `smoke`'s own vocabulary. What is left is check names, loop names and a
        timestamp - all of them the agent's own words. The rest is whatever a
        connector chose to say, and a 403 that quotes the url it was refused can
        carry a token in the query string. Fine in the log, which is outside the
        vault; not fine in a line offered to a synced markdown file.
        """
        parts = [
            self.at,
            self.loop,
            f"reached: {', '.join(self.reached) or NONE}",
            f"skipped: {', '.join(s.fragment(free_text=free_text) for s in self.skipped) or NONE}",
        ]
        if self.failure:
            # An unreadable row is labelled as one wherever it is rendered, not
            # only in the projection. It may well be a run that succeeded, and
            # any other consumer of `line()` - a push, a `status` command -
            # would otherwise report a failure that never happened.
            label = UNREADABLE if self.outcome == UNREADABLE else "failed"
            parts.append(f"{label}: {self.failure if free_text else WITHHELD}")
        return " · ".join(parts)

    def payload(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "loop": self.loop,
            "outcome": self.outcome,
            "reached": list(self.reached),
            "skipped": [skip.as_row() for skip in self.skipped],
            "not_connected": list(self.not_connected),
            "failure": self.failure,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> RunRow:
        """Rebuild a row. Raises on a payload this version cannot read.

        Strict on purpose, and strict about *types* rather than only about keys.
        A newer schema that enriches a list - `reached: [{"name": ..., "ms": 12}]`
        - parses fine if nothing checks, and then blows up later inside
        `line()`, which is well past the point where `rows` could have degraded
        it. Failing here is what makes the `UNREADABLE` fallback work at all.
        """
        return cls(
            at=_text(payload["at"], "at"),
            loop=_text(payload["loop"], "loop"),
            outcome=_text(payload["outcome"], "outcome"),
            reached=_names(payload.get("reached", ()), "reached"),
            skipped=tuple(Result.from_row(entry) for entry in payload.get("skipped", ())),
            not_connected=_names(payload.get("not_connected", ()), "not_connected"),
            failure=_text(payload.get("failure", ""), "failure"),
        )

    @classmethod
    def unreadable(cls, payload: Mapping[str, Any]) -> RunRow:
        """A stand-in for a row this version of the code cannot parse.

        Guardrail 6 across time rather than across sources: one row written by a
        newer schema must not take `rows`, `last_run` and the projection down
        with it, because the moment the schema moved is exactly the moment
        somebody is reading the history. The row keeps its place in the
        sequence, says it cannot be read, and counts as neither a success nor a
        failed run.
        """
        # A payload that is not a record at all has no `.get`, and raising from
        # inside the handler that exists to degrade would take down the whole
        # history - the failure this fallback was added to prevent.
        fields = payload if isinstance(payload, Mapping) else {}
        loop = fields.get("loop")
        return cls(
            at=_timestamp_or_unknown(fields.get("at")),
            loop=_failure_line(loop) if isinstance(loop, str) and loop else UNKNOWN_LOOP,
            outcome=UNREADABLE,
            failure="this row could not be read by this version",
        )


@dataclass
class Run:
    """A run in progress: what it has learned so far, before anything is written.

    Mutable and deliberately dumb. It collects `smoke.as_rows()` output and
    nothing else, so a loop can hand it a pre-flight report and then a second
    one after a retry without the log having to reconcile them.
    """

    loop: str
    at: str
    _seen: dict[str, Result] = field(default_factory=dict)

    def observe(self, rows: Iterable[Result | Mapping[str, str]]) -> None:
        """Take `Result`s, or the `as_rows()` dicts they round-trip through.

        Keyed by check name, last write winning: a source re-probed and now
        answering is reached, not reached *and* skipped. Order is first-seen,
        so the report order the pre-flight fixed is the order the row renders
        in. One type-check, on the way in (`Result.from_row`), and none after:
        a row with an unnamed source or an unknown status would otherwise
        vanish, and a source that vanished is the failure this exists to stop.
        """
        for row in rows:
            # One path for both shapes: a `Result` round-trips through its own
            # row, so the check on the way in is the same check either way.
            entry = row.as_row() if hasattr(row, "as_row") else row
            result = Result.from_row(entry, strict_status=True)
            self._seen[result.name] = result

    def row(self, *, failure: str | BaseException = "") -> RunRow:
        """Freeze what has been observed into the row that gets written."""
        described = _describe(failure)
        by_status: dict[str, list[Result]] = {status: [] for status in _STATUSES}
        for result in self._seen.values():
            by_status[result.status].append(result)

        skipped = tuple(
            # `fragment` holds `reason` to the closed vocabulary so a
            # connector's own sentence cannot reach the vault; the name and
            # source printed beside it are bounded here.
            Result(
                name=_failure_line(result.name),
                source=_failure_line(result.source),
                status=SKIPPED,
                reason=result.reason,
                detail=result.detail,
            )
            for result in by_status[SKIPPED]
        )
        if described:
            outcome = FAILED
        elif not by_status[REACHED] and not skipped:
            # Keyed off what was actually asked of a source, not off how many
            # rows arrived. A run whose every check is `not connected` observed
            # rows and read nothing, and scoring that `OK` is the healthy-looking
            # run that read nothing all over again - permanently, the day a
            # second source is parked as unwired.
            outcome = UNCHECKED
        elif skipped:
            outcome = DEGRADED
        else:
            outcome = OK
        return RunRow(
            at=self.at,
            # Trimmed here, because `RunRow.line` says the parts it keeps are
            # "all of them the agent's own words" - and `loop` is the caller's.
            # `unreadable()` already trims this same field on the degraded-read
            # path, so this was one path capped and its twin not, with the live
            # write being the uncapped one. The newline matters more than the
            # length: `projection_line` is offered to `State.md`, and a name
            # carrying one breaks the file rather than just making a long line.
            loop=_failure_line(self.loop),
            outcome=outcome,
            reached=tuple(result.name for result in by_status[REACHED]),
            skipped=skipped,
            not_connected=tuple(result.name for result in by_status[NOT_CONNECTED]),
            failure=described,
        )


class RunLog:
    """The run history, in the event log outside the vault.

    Reads and writes go through `EventLog`, so the run log inherits the store
    that already exists rather than introducing a second one. Nothing here
    touches the `DayDAG/` folder.
    """

    def __init__(self, log: EventLog, *, clock: Callable[[], datetime]) -> None:
        self._log = log
        self._clock = clock
        #: The loop whose row was written last - what a caller projecting into
        #: `State.md` right after the run asks about.
        self.current_loop = ""

    def _stamp(self, started: datetime | None = None) -> str:
        """When the run started, ISO-8601, from the caller's clock.

        ISO rather than an epoch because this is read by a human in six months,
        and it sorts lexicographically anyway.
        """
        return (started or self._clock()).isoformat()

    def _append(self, row: RunRow) -> RunRow:
        self._log.record(RUN, **row.payload())
        return row

    def record(
        self,
        loop: str,
        rows: Iterable[Mapping[str, str]] = (),
        *,
        started: datetime | None = None,
        failure: str | BaseException = "",
    ) -> RunRow:
        """Write one row for a run that is already over. Returns the row.

        Args:
            loop: which loop ran. Rendered VERBATIM by `projection_*`, so keep
                it a literal - see contract 3 in the module docstring.
            rows: `smoke.as_rows()` output. Anything else raises, and the row is
                still written before the raise.
            started: when the run BEGAN. Without it `at` is the moment this was
                called, which is a different meaning from `run`'s `at` - two
                loops using the two APIs would write one field two ways and
                their timestamps would differ by a run's duration.
            failure: the exception, preferably, not `str(exc)` - which is empty
                for `RuntimeError()` and scores the run as a success.

        Raises:
            Whatever iterating ``rows`` raises, AFTER writing a row that says
            so. Use `run` instead for a loop that might not reach this call.
        """
        run = Run(loop=loop, at=self._stamp(started))
        try:
            run.observe(rows)
        except Exception as bad:
            # Write, then raise - the same order `run` uses, and for the same
            # reason. A caller that reached here from its own `except` would
            # otherwise lose both rows: the malformed one it was told about, and
            # the failure it came here to record in the first place.
            #
            # `Exception`, not `ValueError`: `rows` is whatever the caller
            # passed. Handing over the `SmokeReport` instead of its `as_rows()`
            # raises `TypeError` from the iteration, a list of strings raises
            # `AttributeError`, and a generator can raise anything at all
            # halfway through - every one of which wrote no row while this
            # caught the one shape it had thought of.
            # Both halves through `_describe`: `bad` can be a bare `TypeError()`
            # too, and interpolating it raw records "brief died; " - the exact
            # blank-failure problem, one operand over.
            described, why = _describe(failure), _describe(bad)
            self._append(run.row(failure=f"{described}; {why}" if described else why))
            raise
        return self._append(run.row(failure=failure))

    @contextmanager
    def run(self, loop: str) -> Iterator[Run]:
        """Run a loop and write its row either way. Yields a `Run` to observe into.

            with log.run("morning brief") as run:
                run.observe(report.as_rows())

        ``at`` is stamped on ENTRY, so it is the run's start time.

        A raise from the body is recorded and then RE-RAISED - recording is not
        handling, and swallowing here would turn a loud failure back into a
        silent one. `BaseException`, so a cancelled or interrupted scheduled run
        still leaves a row.

        Prefer this over `record` whenever the body might not finish: a `record`
        at the end of a block is skipped by the exception, and the runs that
        never finished are exactly the ones this log exists for.
        """
        run = Run(loop=loop, at=self._stamp())
        self.current_loop = loop
        try:
            yield run
        except BaseException as failure:
            self._append(run.row(failure=failure))
            raise
        self._append(run.row())

    # -- reading it back --------------------------------------------------

    def rows(self, loop: str | None = None) -> list[RunRow]:
        """Every run row, oldest first, optionally for one loop.

        A payload this version cannot parse becomes an `UNREADABLE` row rather
        than an exception - see `RunRow.unreadable`.

        A row whose *loop* could not be read is returned for every loop asked
        for, because it might be that loop's. Filtering it out is how a dead
        loop keeps rendering a clean line: the rows that would have said so are
        the ones that stopped parsing, and the last readable row - from before
        the trouble started - answers in their place.
        """
        rows = []
        for payload in self._log.recorded(RUN, raw=True):
            try:
                rows.append(RunRow.from_payload(payload))
            except (KeyError, TypeError, AttributeError, ValueError):
                rows.append(RunRow.unreadable(payload))
        return [row for row in rows if loop is None or row.loop in (loop, UNKNOWN_LOOP)]

    def loops(self) -> list[str]:
        """Every loop that has ever recorded a run, in first-seen order.

        `UNKNOWN_LOOP` is not among them: it is not a loop, and reporting it as
        one puts a permanent phantom outage in `State.md`. Such a row is folded
        into every real loop's history by `rows`, which is where it does its work.
        """
        seen = dict.fromkeys(row.loop for row in self.rows())
        return [name for name in seen if name != UNKNOWN_LOOP]

    def last_run(self, loop: str | None = None) -> RunRow | None:
        """The most recent run, whether or not it finished."""
        rows = self.rows(loop)
        return rows[-1] if rows else None

    def last_completed_run(self, loop: str | None = None) -> RunRow | None:
        """The most recent run that got to the end, degraded or not."""
        return next((row for row in reversed(self.rows(loop)) if row.completed), None)

    def projection_line(self, loop: str) -> str:
        """One derived line for one loop, for `State.md` to carry if it wants.

        ``loop`` is required, and that is the whole point. Five loops share this
        log, so the last row overall is whichever loop ran most recently - and a
        healthy noon chaser at 12:05 renders a perfectly clean line while the
        morning brief has failed every day since tuesday. That is precisely the
        silence this module exists to break, so there is no unscoped form to
        reach for by accident. `projection_lines` covers all of them.

        Leads with the *last* run rather than only the last successful one: on a
        thursday morning "last successful run: tuesday" is the entire finding,
        and a line that only ever showed successes would bury it. When the last
        run did not finish, the last one that did is named after it - that pair
        says whether this is a blip or has been broken since tuesday.

        The failure's wording is withheld, because this line is offered to a
        plaintext file synced to every device Nitin owns. Returned rather than
        written: `State.md` has one writer and it is not this module.

        ``loop`` itself is rendered VERBATIM, and it is the one part of this
        line that is not withheld or vocabulary-gated. That is safe only while
        a loop name is a fixed identifier the agent chose - "morning brief",
        "noon chaser". It stops being safe the moment one is derived from data:
        a prep loop named for its meeting would put that meeting's title into
        a synced plaintext file, and guardrail 7 keeps exactly that out. The
        trim on the way in bounds the length and strips newlines, so a name
        cannot break the file's structure - it does not, and cannot, judge
        whether the words are sensitive. Keep loop names literal.
        """
        return self._projection(loop, self.rows(loop))

    def projection_lines(self, expected: Iterable[str] = ()) -> list[str]:
        """One line per loop. The safe default for `State.md`.

        Per loop rather than one line for everything, because the failure worth
        surfacing is always "this loop stopped", and any single line across five
        loops is a line that can report the four healthy ones.

        ``expected`` names the loops that are *supposed* to run, and each one
        gets a line whether or not it ever has. Without it a log holding no rows
        renders nothing at all - so on day one, or after a lost event log, the
        file goes blank exactly where it should be loudest. A caller that knows
        its five scheduled loops should pass them.

        The history is parsed once for all of them. Going through
        `projection_line` per loop re-ran the query and re-parsed every row
        three times per loop, against an append-only log that is never pruned,
        on every `State.md` rewrite.
        """
        rows = self.rows()
        observed = (
            name for name in dict.fromkeys(row.loop for row in rows) if name != UNKNOWN_LOOP
        )
        return [
            self._projection(loop, [r for r in rows if r.loop in (loop, UNKNOWN_LOOP)])
            for loop in dict.fromkeys((*expected, *observed))
        ]

    @staticmethod
    def _projection(loop: str, rows: Sequence[RunRow]) -> str:
        """One projection line from one loop's rows, already parsed and in order."""
        if not rows:
            return f"last run: no run recorded for {loop} yet"
        last = rows[-1]
        if last.completed:
            return f"last run: {last.line(free_text=False)}"
        completed = next((row for row in reversed(rows) if row.completed), None)
        since = f"· last completed: {completed.at if completed else 'never'}"
        if last.outcome == UNREADABLE:
            # Its own branch, because the generic one renders "failed:" and this
            # run may well have succeeded - all that is known is that the row
            # cannot be read by this version. Named by the loop asked about, not
            # by the row's own: an unreadable row belongs to every loop, and
            # printing its `?` renders each loop's line identically.
            return f"last run: {last.at} · {loop} · {UNREADABLE} row {since}"
        return f"last run: {last.line(free_text=False)} {since}"
