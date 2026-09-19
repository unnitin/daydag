"""Source recipes: SPEC section 4's prose as literal queries (#7).

USING IT
    calendar_day(day)                       # ONE day. Never a week - see 1
    loop_windows("morning", day)            # the days ONE loop reads
    slack_overnight(now, mentioning=principal, identities=ids, since_hour=17)
    gmail_gemini_notes(after=day, before=day)   # from:GEMINI_SENDER + label
    jira_jql(["PROJ"], updated_within_days=14)  # bounded fields and results
    vault_relative(WEEKLY_NOTES, "0817-0821.md")
    week_range(day), week_label(day), next_week_label(day)
    title_from_gemini_subject(subject)
    records(payload), has(record, "ts"), error_text(payload)   # the read side

CONTRACTS
    1. Calendar is queried DAY BY DAY. One 5-day pull returned 156,681 chars
       and exceeded the output limit - measured, not a hunch.
    2. Jira always names `fields` explicitly and bounds `maxResults`. Never
       `*all`: a 14-day 4-project query with unbounded fields returned 125,231
       chars. `JIRA_MAX_RESULTS_CAP` and `GH_LIMIT_CAP` are the caps, and
       `observe` builds its reported bounds FROM them.
    3. Slack is addressed by ID, never display name. `from:@someone` does not
       fail, it silently matches nothing.
    4. Gmail matches the SUBJECT: `Notes: "<title>" <date>`, plus the
       `meeting notes` label. All 201 notes in a 30-day window carry both.
       This reverses SPEC's original "never search by subject" - subject beats
       body, and it resolves the back-to-back-1:1 ambiguity body matching
       cannot.
    5. Nothing here performs I/O. Pure functions from parameters to a query
       string or a parameter dict, and from a returned payload to the records
       inside it - which is what makes the part that must be right checkable
       without a connector.
    6. A recipe RAISES (`RecipeError`) rather than returning a best-effort
       query, because every failure it guards is silent at the connector.
    7. Both halves of the connector edge live here: the query going out, and
       `records`/`has`/`error_text` reading what comes back. They are one
       concept - what this codebase believes a source looks like.

WHY IT EXISTS
    Every loop asks the same handful of questions of the same six sources.
    Written per loop they drift, and the difference shows up only as a brief
    that quietly omits a day.

    The theme behind contracts 1, 2 and 4, and the reason several tests here
    are guardrails: a query that overflows the connector's output limit is not
    a degraded read, it is a SILENT one. Guardrail 6 buys honest failure and an
    overflow spends it - the loop reports nothing while looking like it ran.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# One parser for Gemini subjects, re-exported rather than reimplemented. The
# ledger owns it because the ledger is what a parsed title is *for*; a second
# copy here would be a second set of bugs that disagree only on hard cases.
from daydag.config import DEFAULT_TIMEZONE, resolve_reference
from daydag.ledger import title_from_gemini_subject

__all__ = [
    "ERROR_KEYS",
    "GEMINI_LABEL",
    "GEMINI_SENDER",
    "GH_LIMIT_CAP",
    "JIRA_FIELDS",
    "JIRA_MAX_RESULTS_CAP",
    "PACIFIC",
    "RECORD_KEYS",
    "VAULT_PREFIX",
    "DayWindow",
    "OvernightWindow",
    "RecipeError",
    "calendar_day",
    "calendar_days",
    "error_text",
    "first_value",
    "flatten",
    "gmail_gemini_notes",
    "has",
    "has_all",
    "is_user_id",
    "jira_jql",
    "jira_search",
    "loop_windows",
    "measure",
    "meeting_prep",
    "next_monday",
    "next_week_label",
    "records",
    "slack_overnight",
    "slack_search",
    "title_from_gemini_subject",
    "vault_relative",
    "week_label",
    "week_range",
    "weekly_note",
]


class RecipeError(ValueError):
    """A recipe was asked for a query that would be wrong or unsafe to run.

    Raised rather than returning a best-effort query, because every failure this
    guards against is *silent* at the connector: a display name matches nothing,
    an unexpanded ``${VAR}`` searches for a dollar sign, an oversized page
    overflows. All three come back as an empty result that reads as "nothing
    happened", which is the one answer the agent must never invent.
    """


#: The principal's timezone. Sourced from config rather than written here: it is
#: configuration, not a constant, and a zone hardcoded in a module means the
#: agent works for exactly one person. Kept as a module name because every
#: wall-clock boundary here - the calendar day, the overnight cutoff - is local.
PACIFIC = ZoneInfo(DEFAULT_TIMEZONE)


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _resolve(value: str, identities: Mapping[str, str] | None, *, what: str) -> str:
    """Thin delegate to :func:`daydag.config.resolve_reference`.

    Kept as a local name so call sites read the same, but the logic and its
    regex live in config - there were two regexes for one concept before.
    """
    return resolve_reference(value, identities, what=what, error=RecipeError)


#: A JQL ORDER BY is one field plus an optional direction. It was the only input
#: concatenated straight into the query while keys and statuses were validated.
_ORDER_BY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\s+(ASC|DESC))?$", re.IGNORECASE)


def _safe_order_by(order_by: str) -> str:
    if not isinstance(order_by, str) or not _ORDER_BY.match(order_by.strip()):
        raise RecipeError(f"{order_by!r} is not a field name plus an optional ASC/DESC")
    return order_by.strip()


def _as_day(value: object, *, what: str) -> date:
    """A calendar day, refusing a ``datetime``.

    ``datetime`` is a subclass of ``date``, so it satisfied the annotation and
    then rendered as ``after:2026-09-04T00:00:00-07:00`` - an operator Slack
    cannot parse, silently returning nothing rather than erroring.
    """
    if isinstance(value, datetime):
        raise RecipeError(f"{what} must be a date, not a datetime; Slack's {what}: takes a day")
    if not isinstance(value, date):
        raise RecipeError(f"{what} must be a date")
    return value


def _no_quotes(value: str, *, what: str) -> str:
    """Refuse a value that would close the operator quoting it.

    Both Gmail's ``subject:"..."`` and JQL's ``status in ("...")`` are built by
    concatenation, and both draw their inputs from places a human types freely -
    a meeting title, a hand-edited watchlist. Escaping differs per source and
    gets it wrong quietly; refusing is one rule that cannot.
    """
    if '"' in value or "\\" in value:
        raise RecipeError(f"{what} contains a quote or backslash and cannot be quoted safely")
    return value


# ---------------------------------------------------------------------------
# Google Calendar - one request per day, always
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DayWindow:
    """One local day, half-open: ``[midnight, next midnight)``.

    Half-open rather than ``23:59:59`` so consecutive windows are contiguous
    without overlapping: an event starting exactly at midnight belongs to one
    day, and to exactly one.
    """

    day: date
    time_min: str
    time_max: str

    @property
    def params(self) -> dict[str, str]:
        """The window as connector arguments."""
        return {"time_min": self.time_min, "time_max": self.time_max}


def calendar_day(day: date, *, tz: ZoneInfo = PACIFIC) -> DayWindow:
    """The RFC3339 window for a single local day.

    Built from local midnights, never ``start + 24h``: two days a year a PT day
    is 23 or 25 hours long, and fixed arithmetic silently clips an hour off one
    of them (test_windows_follow_dst_rather_than_adding_24_hours).
    """
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return DayWindow(day=day, time_min=start.isoformat(), time_max=end.isoformat())


def calendar_days(start: date, end: date, *, tz: ZoneInfo = PACIFIC) -> list[DayWindow]:
    """One window per day across an inclusive range - never a single wide one.

    A 5-day pull measured 156,681 characters and exceeded the connector's
    output limit (`reference/connector-audit.md`), so a week of calendar is
    seven requests. A list rather than a generator, so the count stays
    assertable by callers and by the guardrail test.
    """
    if end < start:
        raise RecipeError(f"end {end} is before start {start}")
    span = (end - start).days
    return [calendar_day(start + timedelta(days=offset), tz=tz) for offset in range(span + 1)]


# ---------------------------------------------------------------------------
# Slack - person-scoped by id, ordered explicitly
# ---------------------------------------------------------------------------

#: Slack ids: ``U``/``W`` for people, ``B`` for bots, ``C``/``D``/``G`` for
#: conversations. Deliberately shape-only - the point is to reject a *name*,
#: and a stricter length rule would reject real ids as workspaces grow.
_USER_ID = re.compile(r"^[UWB][A-Z0-9]{6,}$")
_CONVERSATION_ID = re.compile(r"^[CDG][A-Z0-9]{6,}$")

#: Slack's `after:`/`before:` exclude the date named, so an inclusive bound is
#: nudged outward by a day. Erring wide is deliberate: an extra day is trimmed
#: by the caller, a missing day is invisible.
_DAY = timedelta(days=1)


def is_user_id(value: str) -> bool:
    """Whether ``value`` is shaped like a Slack user id rather than a name.

    Public because a *destination* needs this check too, not just a query:
    guardrail 1 permits exactly one, and an id that is not one addresses a DM
    at nothing. One regex, one concept.
    """
    return bool(_USER_ID.match((value or "").strip()))


def _slack_id(value: str, pattern: re.Pattern[str], identities, what: str) -> str:
    resolved = _resolve(value, identities, what=what)
    if not pattern.match(resolved):
        raise RecipeError(
            f"{what} {value!r} is not a Slack id. Resolve it first - a display name "
            "or #channel-name in a search silently matches nothing."
        )
    return resolved


def slack_search(
    *,
    sender: str | None = None,
    channel: str | None = None,
    after: date | None = None,
    before: date | None = None,
    terms: Sequence[str] = (),
    ascending: bool = True,
    identities: Mapping[str, str] | None = None,
) -> str:
    """A scoped, chronologically ordered Slack search query.

    ``from:<@USER_ID>`` and ``in:<#CHANNEL_ID>`` beat keyword search, and both
    take ids (contract 3), so a non-id raises here rather than reaching Slack.

    ``sort:timestamp`` is explicit because Slack's default is relevance, and a
    relevance-ordered read of a conversation is not a chronology.

    ``after``/``before`` are the inclusive days the caller means; the emitted
    query widens each by one because Slack's own operators are exclusive.
    """
    parts: list[str] = []
    if sender is not None:
        parts.append(f"from:<@{_slack_id(sender, _USER_ID, identities, 'sender')}>")
    if channel is not None:
        parts.append(f"in:<#{_slack_id(channel, _CONVERSATION_ID, identities, 'channel')}>")
    if after is not None:
        parts.append(f"after:{_as_day(after, what='after') - _DAY}")
    if before is not None:
        parts.append(f"before:{_as_day(before, what='before') + _DAY}")
    for term in terms:
        # Quoted so a phrase stays one phrase; Slack ORs bare words.
        parts.append(f'"{_no_quotes(term, what="search term")}"')
    parts.append(f"sort:timestamp sort_dir:{'asc' if ascending else 'desc'}")
    return " ".join(parts)


@dataclass(frozen=True)
class OvernightWindow:
    """A Slack query plus the cutoff the query itself cannot express."""

    query: str
    #: Epoch seconds. Slack message ``ts`` values are epoch strings, so the
    #: caller compares ``float(message["ts"]) >= min_ts``.
    min_ts: float
    #: Epoch seconds for the other end - ``now``. Slack's ``after:`` resolves to
    #: whole days and cannot say "until 06:40", so the upper cutoff comes back
    #: here the same way the lower one does. Not pedantry: `now` is a parameter,
    #: so a BACKFILLED run has a `now` in the past and the query happily returns
    #: everything since. A 06:40 brief reported a message sent at 18:06 that
    #: evening as "overnight" - twelve hours of its own future.
    max_ts: float


#: Section 3.1's overnight window opens at 6pm the previous evening.
OVERNIGHT_FROM_HOUR = 18


def slack_overnight(
    now: datetime,
    *,
    mentioning: str,
    identities: Mapping[str, str] | None = None,
    since_hour: int = OVERNIGHT_FROM_HOUR,
) -> OvernightWindow:
    """The morning brief's overnight delta: everything since 6pm yesterday.

    Slack search resolves to whole days - there is no way to say "since 6pm" in
    a query. So the query over-fetches by date and the real cutoff comes back
    alongside it as a timestamp for the caller to filter on. Without that second
    half the "overnight" delta is a whole extra day of Slack in a 6:45am DM.
    """
    if now.tzinfo is None:
        raise RecipeError("now must be timezone-aware; the cutoff is a local wall-clock time")
    # Move a real instant, and into PACIFIC by name. An aware datetime's tzinfo
    # is a FIXED offset, so pinning `now.tzinfo` onto another date put
    # yesterday-at-6pm an hour out across a DST change and silently dropped an
    # hour of overnight Slack - the window nobody would think to check. And a
    # bare `astimezone()` converts to the MACHINE's zone: right on a Pacific
    # laptop, seven hours wrong on a UTC CI runner. The cutoff is his local 6pm
    # wherever the loop runs, and only a real zone survives a DST change.
    local = now.astimezone(PACIFIC)
    # The cutoff stays in the PRINCIPAL's zone and its date is read from there
    # (test_the_overnight_after_date_does_not_depend_on_the_callers_tzinfo).
    # Converting first and reading `.date()` off the result rolled a UTC-aware
    # `now` forward a day - 6pm PT is 01:00 UTC - and `after:` being exclusive,
    # the window then skipped the exact 6pm-to-midnight hours it exists to
    # capture while min_ts still claimed them. Query and cutoff disagreed
    # silently, which reads as a complete brief.
    cutoff_local = datetime.combine(local.date() - _DAY, time(hour=since_hour), tzinfo=PACIFIC)
    after_day = cutoff_local.date() - _DAY
    cutoff = cutoff_local.astimezone(now.tzinfo)
    user = _slack_id(mentioning, _USER_ID, identities, "mentioning")
    query = " ".join(
        [
            # A raw id in angle brackets is how a mention appears in message
            # text, so this finds threads he was pulled into as well as his own.
            f"<@{user}>",
            # Derived from `cutoff_local`, never from `cutoff` - see above.
            f"after:{after_day.isoformat()}",
            # DESCENDING, and this is load-bearing rather than cosmetic.
            # `after:` resolves to a whole day, so the query returns from
            # midnight while the window opens at 6pm - and Slack pages the
            # results. Ascending therefore fills page one with the OLDEST
            # messages in the range, every one of which this window discards,
            # and puts the ones it wants on a page nobody fetches. Measured on
            # a real day: all 20 ascending results predated the cutoff, so the
            # overnight section was structurally empty on any busy day.
            # Descending starts at the recent end, which is where the window is.
            "sort:timestamp sort_dir:desc",
        ]
    )
    return OvernightWindow(query=query, min_ts=cutoff.timestamp(), max_ts=now.timestamp())


# ---------------------------------------------------------------------------
# Gmail - Gemini meeting notes, by sender + label + subject
# ---------------------------------------------------------------------------

#: Public address, not an internal one - allowlisted in scripts/scan_secrets.py.
GEMINI_SENDER = "gemini-notes@google.com"

#: Every one of the 201 notes measured in a 30-day window carried it (#2 audit).
GEMINI_LABEL = "meeting notes"


def gmail_gemini_notes(
    *,
    after: date | None = None,
    before: date | None = None,
    title: str | None = None,
) -> str:
    """Gemini meeting notes, scoped by sender and label, optionally by title.

    Sender *and* label, not either: the label alone sweeps in anything a human
    filed there, and the sender alone includes notes for meetings that are not
    his. Together they are the exact set.

    ``title`` searches the **subject** (contract 4), so it lands on one thread
    where body matching cannot separate two back-to-back 1:1s.

    ``before`` is the last day the caller wants included; Gmail's own operator
    is exclusive, so it goes out as the day after.
    """
    parts = [f"from:{GEMINI_SENDER}", f'label:"{GEMINI_LABEL}"']
    if title is not None:
        cleaned = _no_quotes(title, what="meeting title").strip()
        if not cleaned:
            raise RecipeError("meeting title is empty")
        parts.append(f'subject:"{cleaned}"')
    elif title is None and after is None:
        # `before` alone is still unbounded - it bounds the recent end and leaves
        # the far end open, which is the direction that overflows.
        raise RecipeError("give a start date or a title; an unbounded sweep overflows")
    if after is not None:
        parts.append(f"after:{after.strftime('%Y/%m/%d')}")
    if before is not None:
        parts.append(f"before:{(before + _DAY).strftime('%Y/%m/%d')}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Obsidian vault - the prefix, and the decoy that exists without it
# ---------------------------------------------------------------------------

#: Vault-relative paths must carry it. `Documents/Weekly Notes/` exists at the
#: vault root holding one stray `claude-write-test.md` - a previous write that
#: missed the prefix and landed in a folder nobody reads.
VAULT_PREFIX = "Create Music Group"

WEEKLY_NOTES = "Weekly Notes"
MEETING_PREP = "Meeting Prep"
FACT_BASE = "Fact Base"
DAYDAG = "DayDAG"


def _safe_part(part: str) -> str:
    if not isinstance(part, str) or not part.strip():
        raise RecipeError("empty path segment")
    if part.startswith("/") or ".." in Path(part).parts:
        raise RecipeError(f"{part!r} escapes the vault")
    return part.strip("/")


def vault_relative(*parts: str) -> str:
    """A connector-relative vault path, prefix included exactly once."""
    cleaned = [_safe_part(part) for part in parts]
    if not cleaned:
        raise RecipeError("no path given")
    # Strip the prefix whether it arrives as its own segment or joined onto the
    # front of the first one: `weekly_note()` and `meeting_prep()` return the
    # joined form, so feeding their output back in doubled the prefix and put
    # the write in a sibling folder that only looks right
    # (test_the_prefix_is_not_doubled).
    first = cleaned[0]
    if first == VAULT_PREFIX:
        cleaned = cleaned[1:]
    elif first.startswith(VAULT_PREFIX + "/"):
        cleaned[0] = first[len(VAULT_PREFIX) + 1 :]
    cleaned = [part for part in cleaned if part]
    if not cleaned:
        raise RecipeError("no path given")
    return "/".join([VAULT_PREFIX, *cleaned])


def week_range(day: date) -> tuple[date, date]:
    """The Monday and Friday of ``day``'s week.

    Mon-Fri, not Sun-Sat: `0817-0821.md` is "Week of August 17-21, 2026". A
    Saturday or Sunday therefore belongs to the week just ending, which is the
    section 3.6 trap - the Sunday week-ahead loop runs inside the *old* week and
    wants :func:`next_week_label`.
    """
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=4)


def next_monday(day: date) -> date:
    """The Monday after ``day``'s Mon-Fri week - what a Sunday run plans for.

    Derived from `week_range` rather than ``day + 1``: the scheduled week-ahead
    runs on a Sunday, the on-demand one runs whenever he asks.
    """
    monday, _ = week_range(day)
    return monday + timedelta(days=7)


#: How far ahead a named prep will look. A week, because that is the span the
#: evidence a prep is built from actually covers.
PREP_HORIZON_DAYS = 7


def loop_windows(
    loop: str, day: date, *, tz: ZoneInfo = PACIFIC, selector: str = ""
) -> list[DayWindow]:
    """The calendar days one loop reads, one window each - never a range.

    The ONE place this arithmetic lives. `run.plan` asks for these windows and
    every consumer asks for the same ones, so what was fetched and what is
    read cannot drift apart (#109): the morning reads today, the wrap reads
    tomorrow, the week-ahead reads next Mon-Sun, a named prep reads the next
    seven days, and `ingest`, `chase` and `ship` read no calendar at all.
    """
    if loop in {"ingest", "chase", "ship"}:
        return []
    if loop == "prep" and selector:
        return calendar_days(day, day + timedelta(days=PREP_HORIZON_DAYS - 1), tz=tz)
    if loop == "eod":
        return [calendar_day(day + timedelta(days=1), tz=tz)]
    if loop == "week-ahead":
        first = next_monday(day)
        return calendar_days(first, first + timedelta(days=6), tz=tz)
    return [calendar_day(day, tz=tz)]


def week_label(day: date) -> str:
    """``MMDD-MMDD`` for the Mon-Fri week containing ``day``."""
    monday, friday = week_range(day)
    return f"{monday.strftime('%m%d')}-{friday.strftime('%m%d')}"


def next_week_label(day: date) -> str:
    """``MMDD-MMDD`` for the week after ``day``'s - what a Sunday run wants."""
    return week_label(day + timedelta(days=7))


def weekly_note(day: date) -> str:
    """The weekly note for ``day``'s week.

    The file may not exist: the note is hand-written and was three weeks stale
    at the last check, which is the state the first real run will meet. Callers
    surface a missing note rather than treating it as an empty one.
    """
    return vault_relative(WEEKLY_NOTES, f"{week_label(day)}.md")


def meeting_prep(day: date) -> str:
    """The Meeting Prep file for ``day``'s week - plain date name, no suffix."""
    return vault_relative(MEETING_PREP, f"{week_label(day)}.md")


# ---------------------------------------------------------------------------
# Jira - bounded, because the unbounded form has already overflowed
# ---------------------------------------------------------------------------

#: What the pulse and the chase list actually read off a ticket. Explicit
#: because `*all` ships every custom field on every issue: that is what turned
#: a 14-day, 4-project query into 125,231 characters
#: (`reference/connector-audit.md`).
#:
#: ``resolutiondate`` is what tells a close from a mention that merely
#: predates it; ``labels`` is the "newly blocked" signal. Both were grown here
#: rather than requested ad hoc by a board reader - a second, shorter field
#: list for the same board is the drift this module prevents.
JIRA_FIELDS: tuple[str, ...] = (
    "key",
    "summary",
    "status",
    "assignee",
    "updated",
    "issuetype",
    "parent",
    "resolutiondate",
    "labels",
)

#: Page size ceiling. The connector's limit is on response *size*, which no
#: count can guarantee - but 100 issues of 7 fields stays comfortably inside it.
JIRA_MAX_RESULTS_CAP = 100
JIRA_DEFAULT_MAX_RESULTS = 50
JIRA_DEFAULT_WINDOW_DAYS = 7

#: A Jira project key: 2-10 uppercase alphanumerics starting with a letter.
#: Public so anything reading keys out of the hand-edited watchlist validates
#: them against this one pattern rather than a second copy of it.
PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


def _project_keys(projects: Iterable[str]) -> list[str]:
    keys = [str(project).strip() for project in projects]
    if not keys:
        raise RecipeError(
            "no project keys given. Read them from the jira block of "
            "DayDAG/Watchlist.md - an unscoped JQL reads every project."
        )
    for key in keys:
        if not PROJECT_KEY.match(key):
            raise RecipeError(
                f"{key!r} is not a Jira project key. The watchlist is hand-edited, "
                "so keys are untrusted input to a JQL built by concatenation."
            )
    return keys


def jira_jql(
    projects: Iterable[str],
    *,
    updated_within_days: int = JIRA_DEFAULT_WINDOW_DAYS,
    statuses: Sequence[str] | None = None,
    order_by: str = "updated DESC",
) -> str:
    """The data team's board state as JQL.

    The window defaults to a week rather than the fortnight that overflowed, and
    it is a relative ``-Nd`` so a saved query does not silently age.
    """
    if updated_within_days < 1:
        raise RecipeError("updated_within_days must be at least 1")
    keys = ", ".join(f'"{key}"' for key in _project_keys(projects))
    clauses = [f"project in ({keys})", f"updated >= -{updated_within_days}d"]
    if statuses:
        quoted = ", ".join(f'"{_no_quotes(status, what="status")}"' for status in statuses)
        clauses.append(f"status in ({quoted})")
    return f"{' AND '.join(clauses)} ORDER BY {_safe_order_by(order_by)}"


def _as_field_tuple(fields: object) -> tuple[str, ...]:
    """Field selections, refusing a bare string.

    A `str` is iterable, so `fields="key,summary"` silently became one field per
    character. The wildcard guard then matched `"*all"` letter by letter and
    looked like it was working.
    """
    if isinstance(fields, str):
        raise RecipeError("fields must be a sequence of names, not a single string")
    return tuple(fields)


def jira_search(
    projects: Iterable[str],
    *,
    updated_within_days: int = JIRA_DEFAULT_WINDOW_DAYS,
    statuses: Sequence[str] | None = None,
    fields: Sequence[str] = JIRA_FIELDS,
    max_results: int = JIRA_DEFAULT_MAX_RESULTS,
    page: int = 0,
) -> dict[str, object]:
    """Search parameters for one page of board state.

    Paged rather than widened: the connector's failure mode is an output
    overflow, and an overflow is not a truncated answer, it is no answer at all.
    A caller that needs more asks for ``page=1``.
    """
    requested = [str(field).strip() for field in fields]
    wildcards = [field for field in requested if field.startswith("*")]
    if wildcards:
        raise RecipeError(
            f"{wildcards} asks for every field. That is what returned 125,231 chars "
            "and overflowed the connector; name the fields you need."
        )
    if not requested:
        raise RecipeError("no fields given")
    if not 1 <= max_results <= JIRA_MAX_RESULTS_CAP:
        raise RecipeError(f"maxResults must be 1-{JIRA_MAX_RESULTS_CAP}, got {max_results}")
    if page < 0:
        raise RecipeError("page must not be negative")
    return {
        "jql": jira_jql(projects, updated_within_days=updated_within_days, statuses=statuses),
        "fields": ",".join(requested),
        "maxResults": max_results,
        "startAt": page * max_results,
    }


# ---------------------------------------------------------------------------
# GitHub - read-only by token (reference/github-access.md)
# ---------------------------------------------------------------------------

#: One page of anything from the GitHub API. History comes from the git
#: mirrors; the review/CI half by API is retired at tag `pre-simplification`
#: until M4-7 wires it, and this cap is what `observe` states as its bound.
GH_LIMIT_CAP = 100


# ---------------------------------------------------------------------------
# reading what came back - the other half of the connector edge (contract 7)
# ---------------------------------------------------------------------------

#: Where a connector puts its error when it hands one back instead of raising.
#: The shape MCP and REST clients actually use, which a string-only reading
#: missed entirely: a `{"error": {"code": 401}}` fell through to the caller's
#: plausibility check and reported "no event list came back", never the 401.
#: "message" is deliberately absent: it is only an error when it sits under one
#: of these, and `flatten` already reads it there.
ERROR_KEYS = ("error", "errors", "errorMessages", "error_description")

#: Keys a connector puts its records under. Checked in order, first list wins.
RECORD_KEYS = (
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


def flatten(value: Any) -> list[str]:
    """Every leaf in a nested structure, as strings, depth first."""
    if isinstance(value, Mapping):
        return [part for item in value.values() for part in flatten(item)]
    if isinstance(value, list | tuple):
        return [part for item in value for part in flatten(item)]
    return [str(value)]


def error_text(payload: Any) -> str:
    """The error a payload is carrying, flattened, or ``""`` if it carries none."""
    if not isinstance(payload, Mapping):
        return ""
    for key in ERROR_KEYS:
        if payload.get(key):
            return " ".join(flatten(payload[key]))
    return ""


def measure(payload: Any) -> int:
    """Roughly how much text this payload would occupy on the way back."""
    return len(payload if isinstance(payload, str) else repr(payload))


def records(payload: Any) -> list[Any] | None:
    """The record list inside a payload, or ``None`` if there is not one.

    ``None`` and ``[]`` are different answers, and keeping them apart is the
    whole reason this returns an optional rather than an empty list: no list at
    all means the call did not return this source's shape, an empty list means
    it did and matched nothing. Which of those is a failure depends on the
    source, so that call belongs to the caller and is not made here.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        for key in RECORD_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return None


def has(record: Any, *keys: str) -> bool:
    """Whether the record carries *any* of these, for keys that are alternatives."""
    return isinstance(record, Mapping) and any(record.get(key) for key in keys)


def has_all(record: Any, *keys: str) -> bool:
    """Whether the record carries *every* one of these.

    Separate from `has` because the difference is where two checks were wrong:
    `any` on `("id", "subject")` let Gmail's metadata-only search results
    through on the strength of the id, and the subject is the whole point.
    """
    return isinstance(record, Mapping) and all(record.get(key) for key in keys)


def first_value(row: Any) -> Any:
    """The first value in a row, however the driver shaped it."""
    if isinstance(row, Mapping):
        return next(iter(row.values()), None)
    if isinstance(row, list | tuple):
        return row[0] if row else None
    return row
