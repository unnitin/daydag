"""One row per run, so a silent failure is diagnosable after the fact.

There is no platform observability behind this agent and SPEC section 7 accepts
that deliberately, along with its consequence: **the failure mode is Nitin
noticing a brief didn't arrive.** By then the process is gone. Nothing says
whether the run started, which source went quiet, or whether it died halfway.
This module is the only record that outlives the run, so it is written to be
read exactly once - months later, by someone asking "what happened on the 8th".

Three decisions follow from that, and each is held by a test:

**The row goes in the event log, not in `State.md`.** ARCHITECTURE splits the
stores on this: five scheduled loops appending to one iCloud-synced markdown
file with no locking is a lost update, and the run log is the write that
happens most. `State.md` may carry `projection_lines()` as derived lines - one
per loop, and per loop rather than one line overall because any single line
across five loops is a line that can report the four healthy ones while the
brief has been dead since tuesday. Those lines withhold the failure's wording:
everything else on them comes from a fixed vocabulary, but a connector's error
text is whatever it chose to say, and a 403 that quotes the url it was refused
can carry a token. That belongs in the log, which is outside the vault.

**The rows come from `smoke.as_rows()`, never from its rendered text.** Those
rows are `{name, source, status, reason, detail}`, all strings, in a
deterministic order - a format designed to be appended to. Reading the reached
and skipped sets back out of a rendered report would make the log a parser of
its own output, and the report's wording would then be load-bearing.

**A run that fails partway still writes its row.** `RunLog.run` is a context
manager that records on the way out whether or not the body raised, and then
re-raises. A log that only records clean runs is missing precisely the runs
anybody would ever open it for. The honest limit: a process killed hard - SIGKILL,
the laptop shut - never reaches the recording code and leaves no row at all. So
absence of a row is itself a finding, which is why the projection reports when
the last run was rather than only that one succeeded.

The reader degrades too. A payload written by a newer schema becomes one
`UNREADABLE` row instead of an exception, because the moment the schema moved
is exactly the moment somebody is reading the history.

The clock is injected. `smoke.py` takes no clock on purpose, because a stamped
report is non-deterministic and therefore unassertable; the stamp has to happen
somewhere, so it happens here, where a test can pin it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from daydag.smoke import AUTH, FETCH, NO_PROBE, NOT_CONNECTED, OVERFLOW, REACHED, SHAPE, SKIPPED
from daydag.smoke import FAILED as SMOKE_FAILED

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from daydag.state import EventLog

#: The event-log `kind` every run row is written under. One kind, so a reader
#: can pull the run history without knowing which loops ever existed.
RUN = "run"

#: Every source that is connected at all answered.
OK = "ok"
#: The run finished and shipped, with at least one source named as unreachable.
#: Guardrail 6 working, not the run failing.
DEGRADED = "degraded"
#: The run raised before it finished. The row is what it managed to learn first.
FAILED = "failed"
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
_KNOWN_REASONS = frozenset({"", NO_PROBE, AUTH, OVERFLOW, SHAPE, FETCH, SMOKE_FAILED})

#: The loop of a row whose `loop` field could not be read. Not a real loop: it
#: means "some loop ran and this row cannot say which", and it is never itself
#: reported as a loop - such a row is folded into every real loop's history.
UNKNOWN_LOOP = "?"

#: The timestamp of a row whose `at` could not be read. Its own sentinel: a row
#: that cannot say *when* and a row that cannot say *which loop* are different
#: unknowns, and sharing one marker renders "? · ?" and reads like a bug.
UNKNOWN_AT = "unknown time"


def _one_line(text: str) -> str:
    return " ".join(text.split())[:FAILURE_LIMIT].rstrip()


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
        return _one_line(str(failure)) or type(failure).__name__
    trimmed = _one_line(failure)
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
class Skip:
    """One source that was not reached, and why. Never a bare name.

    The reason is what makes the row actionable: `auth` sends the reader to a
    login, `overflow` to the query, `fetch` to a clone on disk. Reporting all
    three as "unavailable" sends them to all three.
    """

    name: str
    source: str
    reason: str
    detail: str = ""

    def line(self, *, free_text: bool = True) -> str:
        """This skip as one fragment: `jira (auth)`.

        `free_text=False` prints the reason only if it is one of `smoke`'s own -
        a closed vocabulary of words like `auth` and `overflow`. Nothing checks
        what a caller puts in a row, and this fragment is rendered into the line
        offered to `State.md`, so a reason carrying a connector's own sentence
        (a url with a token in the query string) would land in a synced
        plaintext file. The log keeps the reason either way.
        """
        reason = self.reason
        if not free_text and reason not in _KNOWN_REASONS:
            reason = WITHHELD
        return f"{self.name} ({reason})" if reason else self.name


def _skip(entry: Any) -> Skip:
    """One stored skip, type-checked field by field.

    `Skip(**entry)` alone accepts any value types, and this is the one list
    whose contents reach the vault: `Skip.line` renders the reason verbatim into
    `projection_line`, which is the line `free_text=False` exists to keep
    free of connector-chosen text. So it degrades to `UNREADABLE` like the rest.
    """
    if not isinstance(entry, Mapping):
        raise TypeError(f"a skip is {type(entry).__name__}, not a record")
    return Skip(
        name=_text(entry["name"], "skip name"),
        source=_text(entry["source"], "skip source"),
        reason=_text(entry["reason"], "skip reason"),
        detail=_text(entry.get("detail", ""), "skip detail"),
    )


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
    skipped: tuple[Skip, ...] = ()
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
            f"skipped: {', '.join(s.line(free_text=free_text) for s in self.skipped) or NONE}",
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
            "skipped": [vars(skip) for skip in self.skipped],
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
            skipped=tuple(_skip(entry) for entry in payload.get("skipped", ())),
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
            loop=_one_line(loop) if isinstance(loop, str) and loop else UNKNOWN_LOOP,
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
    _seen: dict[str, Mapping[str, str]] = field(default_factory=dict)

    def observe(self, rows: Iterable[Mapping[str, str]]) -> None:
        """Take `smoke.as_rows()` output. Callable more than once.

        Keyed by check name, last write winning: a source re-probed and now
        answering is reached, not reached *and* skipped. Order is first-seen, so
        the report order `smoke` fixed is the order the row renders in.
        """
        for row in rows:
            name, status = row.get("name", ""), row.get("status", "")
            # Both checked, because both are the key to something: an unnamed
            # row cannot be filed under a source, and a row with an unknown
            # status cannot be filed under reached or skipped. Either way it
            # would vanish, and a source that vanished is the failure this
            # module exists to stop.
            if not name or not isinstance(name, str):
                # The type matters as much as the presence. A truthy non-string
                # name is accepted, stored, serialised - and then rejected by
                # the reader, so the row that was written successfully comes
                # back `UNREADABLE` forever and `line()` raises on the way.
                raise ValueError(f"a row carries no check name: {dict(row)!r}")
            if status not in _STATUSES:
                raise ValueError(
                    f"{name!r} carries status {status!r}; expected one of {', '.join(_STATUSES)}"
                )
            # The same fields `_skip` checks on the way back out. Gating only
            # `name` let a `reason=None` through: it serialised cleanly, and
            # then every read of that row came back `UNREADABLE` - a run that
            # succeeded, reported forever as one nobody can parse.
            for key in ("source", "reason", "detail"):
                if not isinstance(row.get(key, ""), str):
                    raise ValueError(f"{name!r} carries a {key} that is not text: {row[key]!r}")
            self._seen[name] = row

    def row(self, *, failure: str | BaseException = "") -> RunRow:
        """Freeze what has been observed into the row that gets written."""
        described = _describe(failure)
        by_status: dict[str, list[Mapping[str, str]]] = {status: [] for status in _STATUSES}
        for row in self._seen.values():
            by_status[row["status"]].append(row)

        skipped = tuple(
            Skip(
                name=row["name"],
                source=row.get("source", ""),
                reason=row.get("reason", ""),
                detail=row.get("detail", ""),
            )
            for row in by_status[SKIPPED]
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
            loop=self.loop,
            outcome=outcome,
            reached=tuple(row["name"] for row in by_status[REACHED]),
            skipped=skipped,
            not_connected=tuple(row["name"] for row in by_status[NOT_CONNECTED]),
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
        """Write one row for a run that is already over.

        The direct form, for a loop that has its smoke rows in hand. A loop that
        might not survive to the end should use `run` instead, which cannot be
        skipped by the exception.

        ``started`` keeps `at` meaning the same thing in both forms. `run`
        stamps the clock on entry, so its `at` is the start; this one is called
        after the fact, so without ``started`` its `at` is the moment it was
        reported. A caller that knows when its run began should say so -
        otherwise two loops using the two APIs write one field with two
        meanings, and comparing their timestamps is off by a run's duration.

        ``failure`` takes the exception itself as well as a string. Prefer the
        exception: `str(RuntimeError())` is empty, and an empty failure scores
        the run as a success.
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
        """Run a loop and write its row either way.

        The failure path is the reason this is a context manager rather than a
        pair of calls: a `record` at the end of the body is skipped by the
        exception, and the runs that never finished are exactly the ones the log
        exists for. `BaseException` rather than `Exception` so a scheduled run
        cancelled or interrupted still leaves a row saying so.

        The failure is recorded and then re-raised. Recording is not handling -
        swallowing it here would turn a loud failure back into a silent one.
        """
        run = Run(loop=loop, at=self._stamp())
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
        for payload in self._log.payloads(RUN):
            try:
                rows.append(RunRow.from_payload(payload))
            except (KeyError, TypeError, AttributeError, ValueError):
                rows.append(RunRow.unreadable(payload))
        return [row for row in rows if loop is None or row.loop in (loop, UNKNOWN_LOOP)]

    def loops(self) -> list[str]:
        """Every loop that has ever recorded a run, in first-seen order.

        `UNKNOWN_LOOP` is not among them: it is not a loop, and reporting it as
        one puts a permanent phantom outage in `State.md` for a loop that never
        existed. Such a row is already folded into every real loop's history by
        `rows`, which is where it does its work.
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
