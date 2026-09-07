"""Source recipes: SPEC section 4's prose as literal queries (issue #7).

Every loop asks the same handful of questions of the same six sources. Written
per loop, they drift - two loops end up asking subtly different things and the
difference only shows up as a brief that quietly omits a day. So they live here
once, as pure functions from parameters to a query string or a parameter dict.
Nothing in this module performs I/O, which is what makes the part that must be
right checkable without a connector.

Three shapes here contradict what SPEC section 4 originally asserted, and the
issue #2 connector audit is why. They are marked in place:

* **Gmail.** The spec said Gemini's subject was inconsistent and the title had
  to be recovered from the body. All 201 notes in a 30-day window carry
  ``Notes: "<title>" <date>``, and every one also carries the ``meeting notes``
  label. Subject beats body, and it resolves the back-to-back-1:1 ambiguity
  that body matching cannot.
* **Calendar.** Day-by-day was a hunch; it is now measured. One 5-day pull
  returned 156,681 characters and exceeded the output limit.
* **Jira.** The same failure with a different shape: a 14-day, 4-project JQL
  with unbounded fields returned 125,231 characters. Hence explicit ``fields``
  and a bounded ``maxResults``, never ``*all``.

The recurring theme in all three, and the reason several tests here are
guardrails: a query that overflows the connector's output limit is not a
degraded read, it is a *silent* one. Guardrail 6 buys honest failure, and an
overflow spends it - the loop reports nothing while looking like it ran.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# One parser for Gemini subjects, re-exported rather than reimplemented. The
# ledger owns it because the ledger is what a parsed title is *for*; a second
# copy here would be a second set of bugs that disagree only on hard cases.
from daydag.config import DEFAULT_TIMEZONE, resolve_reference
from daydag.ledger import title_from_gemini_subject

__all__ = [
    "GEMINI_LABEL",
    "GEMINI_SENDER",
    "GH_LIMIT_CAP",
    "GH_WRITE_VERBS",
    "JIRA_FIELDS",
    "JIRA_MAX_RESULTS_CAP",
    "PACIFIC",
    "VAULT_PREFIX",
    "DayWindow",
    "OvernightWindow",
    "RecipeError",
    "calendar_day",
    "calendar_days",
    "gh_default_branch",
    "gh_open_prs",
    "gh_pr_checks",
    "gh_recent_runs",
    "gmail_gemini_notes",
    "jira_jql",
    "jira_search",
    "meeting_prep",
    "next_week_label",
    "slack_overnight",
    "slack_search",
    "title_from_gemini_subject",
    "vault_path",
    "vault_relative",
    "vault_root",
    "week_label",
    "week_range",
    "weekly_note",
    "workstreams_paths",
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
    without overlapping - an event starting exactly at midnight belongs to one
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

    Built from local midnights rather than ``start + 24h``: two days a year a
    PT day is 23 or 25 hours long, and fixed arithmetic silently clips an hour
    off one of them.
    """
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return DayWindow(day=day, time_min=start.isoformat(), time_max=end.isoformat())


def calendar_days(start: date, end: date, *, tz: ZoneInfo = PACIFIC) -> list[DayWindow]:
    """One window per day across an inclusive range - never a single wide one.

    A 5-day pull was measured at 156,681 characters and exceeded the connector's
    output limit (#2 audit), so a week of calendar is seven requests. Returning
    a list rather than a generator keeps the count assertable by callers and by
    the guardrail test.
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
    take ids: ``from:@display-name`` returns zero results *and no error*, which
    is the worst failure available - the brief then reports a silence it never
    checked. So a non-id raises here instead.

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
    # Move a real instant rather than pinning `now.tzinfo` onto another date: an
    # aware datetime's tzinfo is a FIXED offset, so across a DST change
    # yesterday-at-6pm came out an hour wrong and silently dropped an hour of
    # overnight Slack - the window nobody would think to check.
    # PACIFIC, not `astimezone()` with no argument: the bare form converts to
    # whatever the MACHINE's timezone is, so this passed on a Pacific laptop and
    # was seven hours wrong on a UTC CI runner. The cutoff is the principal's
    # local 6pm wherever the loop happens to run, and a real zone (not the fixed
    # offset `now.tzinfo` carries) is what makes it survive a DST change.
    local = now.astimezone(PACIFIC)
    # Keep the cutoff in the PRINCIPAL's zone and take its date from there.
    # Converting first and then reading .date() was the bug: `cutoff` carries
    # the caller's tzinfo, so a UTC-aware `now` rolled the date forward (6pm PT
    # is 01:00 UTC) and, `after:` being exclusive, the window skipped the exact
    # 6pm-to-midnight hours it exists to capture - while min_ts still claimed
    # them. Query and cutoff disagreed silently, which reads as a complete brief.
    cutoff_local = datetime.combine(local.date() - _DAY, time(hour=since_hour), tzinfo=PACIFIC)
    after_day = cutoff_local.date() - _DAY
    cutoff = cutoff_local.astimezone(now.tzinfo)
    user = _slack_id(mentioning, _USER_ID, identities, "mentioning")
    query = " ".join(
        [
            # A raw id in angle brackets is how a mention appears in message
            # text, so this finds threads he was pulled into as well as his own.
            f"<@{user}>",
            # local.date(), NOT cutoff.date(): `cutoff` is converted back to the
            # caller's tzinfo, so on a UTC-aware `now` its .date() rolls forward
            # a day - 6pm PT is 01:00 UTC. `after:` is exclusive, so the window
            # then skipped the exact 6pm-to-midnight hours it exists to capture,
            # while min_ts still claimed them. The two disagreed silently.
            f"after:{after_day.isoformat()}",
            "sort:timestamp sort_dir:asc",
        ]
    )
    return OvernightWindow(query=query, min_ts=cutoff.timestamp())


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

    ``title`` searches the **subject**, which is the correction the #2 audit
    made to SPEC section 4. The subject is rigidly ``Notes: "<title>" <date>``,
    so an exact title lands on one thread - where body matching cannot separate
    two back-to-back 1:1s, the case that actually breaks ingestion.

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
    # front of the first one. weekly_note() and workstreams_paths() return the
    # joined form, so feeding their output back in doubled the prefix and put
    # the write in a sibling folder that only looks right.
    first = cleaned[0]
    if first == VAULT_PREFIX:
        cleaned = cleaned[1:]
    elif first.startswith(VAULT_PREFIX + "/"):
        cleaned[0] = first[len(VAULT_PREFIX) + 1 :]
    cleaned = [part for part in cleaned if part]
    if not cleaned:
        raise RecipeError("no path given")
    return "/".join([VAULT_PREFIX, *cleaned])


def vault_root(identities: Mapping[str, str]) -> Path:
    """The local vault directory, prefix included, from ``VAULT_ROOT``.

    Configured rather than hardcoded: an absolute home path leaks a username
    into a public repo. The prefix is appended only when the configured root
    does not already end in it, so both conventions - pointing at ``Documents``
    or at the vault proper - land in the same place rather than one of them
    landing in the decoy.
    """
    if "VAULT_ROOT" not in identities:
        raise RecipeError("VAULT_ROOT is not set. Add it to .env; see .env.example.")
    resolved = os.path.expanduser(os.path.expandvars(str(identities["VAULT_ROOT"]).strip()))
    # Same failure as pulse.mirror_root: an empty value resolves to "." and an
    # unset ${VAR} passes through as literal text, so both would write into a
    # directory nobody would think to look in.
    if not resolved.strip() or "$" in resolved:
        raise RecipeError("VAULT_ROOT is empty or names an unset variable. Fix it in .env.")
    root = Path(resolved)
    return root if root.name == VAULT_PREFIX else root / VAULT_PREFIX


def vault_path(identities: Mapping[str, str], *parts: str) -> Path:
    """A local filesystem path inside the vault."""
    return vault_root(identities).joinpath(*(_safe_part(part) for part in parts))


def week_range(day: date) -> tuple[date, date]:
    """The Monday and Friday of ``day``'s week.

    Mon-Fri, not Sun-Sat: `0817-0821.md` is "Week of August 17-21, 2026". A
    Saturday or Sunday therefore belongs to the week just ending, which is the
    section 3.6 trap - the Sunday week-ahead loop runs inside the *old* week and
    wants :func:`next_week_label`.
    """
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=4)


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


def workstreams_paths() -> tuple[str, str]:
    """Where Workstreams.md may be, in the order to look.

    Custody transfers from `weekly-planning` to DayDAG at the cut (#37) and the
    file moves with it, so both locations are live states of the same system.
    DayDAG first: after the cut that is the real one and the old path may linger.
    """
    return (
        vault_relative(DAYDAG, "Workstreams.md"),
        vault_relative(FACT_BASE, "Workstreams.md"),
    )


# ---------------------------------------------------------------------------
# Jira - bounded, because the unbounded form has already overflowed
# ---------------------------------------------------------------------------

#: What the pulse and the chase list actually read off a ticket. Explicit
#: because `*all` ships every custom field on every issue: that is what turned
#: a 14-day, 4-project query into 125,231 characters (#2 audit).
JIRA_FIELDS: tuple[str, ...] = (
    "key",
    "summary",
    "status",
    "assignee",
    "updated",
    "issuetype",
    "parent",
)

#: Page size ceiling. The connector's limit is on response *size*, which no
#: count can guarantee - but 100 issues of 7 fields stays comfortably inside it.
JIRA_MAX_RESULTS_CAP = 100
JIRA_DEFAULT_MAX_RESULTS = 50
JIRA_DEFAULT_WINDOW_DAYS = 7

#: Atlassian project keys: 2-10 uppercase alphanumerics starting with a letter.
_PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


def _project_keys(projects: Iterable[str]) -> list[str]:
    keys = [str(project).strip() for project in projects]
    if not keys:
        raise RecipeError(
            "no project keys given. Read them from the jira block of "
            "DayDAG/Watchlist.md - an unscoped JQL reads every project."
        )
    for key in keys:
        if not _PROJECT_KEY.match(key):
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
# GitHub - argv for the `gh` CLI, read-only
# ---------------------------------------------------------------------------

#: Subcommands that change something on GitHub. Asserted against every recipe:
#: SPEC section 4 lists GitHub as read-only, and guardrail 1 keeps it that way.
GH_WRITE_VERBS = frozenset(
    {
        "merge",
        "close",
        "reopen",
        "comment",
        "review",
        "edit",
        "create",
        "delete",
        "ready",
        "lock",
        "unlock",
        "rerun",
        "cancel",
    }
)

#: What the pulse reads off an open PR. `statusCheckRollup` is the CI half.
GH_PR_FIELDS: tuple[str, ...] = (
    "number",
    "title",
    "author",
    "createdAt",
    "updatedAt",
    "isDraft",
    "reviewDecision",
    "headRefName",
    "url",
    "statusCheckRollup",
)

GH_RUN_FIELDS: tuple[str, ...] = (
    "databaseId",
    "displayTitle",
    "headBranch",
    "conclusion",
    "status",
    "createdAt",
    "url",
)

GH_LIMIT_CAP = 100
GH_DEFAULT_PR_LIMIT = 50
GH_DEFAULT_RUN_LIMIT = 20

_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
#: Git ref characters. Leading `-` excluded by the character class, so a branch
#: cannot arrive at `gh` as a flag.
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _slug(repo: object) -> str:
    """``owner/name`` from a :class:`daydag.pulse.WatchedRepo` or a plain string.

    Duck-typed on ``.slug`` rather than imported: the pulse owns ``WatchedRepo``
    on its own path, and a shared module reaching into a path-owned one is the
    coupling CONTRIBUTING's branch table exists to prevent.
    """
    slug = getattr(repo, "slug", repo)
    if not isinstance(slug, str) or not _SLUG.match(slug):
        raise RecipeError(f"{repo!r} is not an owner/name repo slug")
    return slug


def _limit(value: int, *, what: str) -> str:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= GH_LIMIT_CAP:
        raise RecipeError(f"{what} must be 1-{GH_LIMIT_CAP}, got {value!r}")
    return str(value)


def gh_open_prs(repo: object, *, limit: int = GH_DEFAULT_PR_LIMIT) -> list[str]:
    """Open PRs for one watched repo, with review state and the CI rollup.

    argv, never a shell string: the slug comes from a hand-edited watchlist, and
    a string handed to a shell is a command injection. Same reason
    ``pulse._run_git`` takes a list.
    """
    return [
        "gh",
        "pr",
        "list",
        "--repo",
        _slug(repo),
        "--state",
        "open",
        "--limit",
        _limit(limit, what="limit"),
        "--json",
        ",".join(GH_PR_FIELDS),
    ]


def gh_pr_checks(repo: object, number: int) -> list[str]:
    """CI check state for one PR."""
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise RecipeError(f"PR number must be a positive integer, got {number!r}")
    return ["gh", "pr", "checks", str(number), "--repo", _slug(repo)]


def gh_default_branch(repo: object) -> list[str]:
    """Resolve the default branch instead of assuming ``main``.

    `createos-dsp-ingestion` defaults to ``develop``. A branch-health check
    hardcoding ``main`` finds nothing there and reports green - guardrail 3
    inverted, since it asserts health it never checked.
    """
    return ["gh", "repo", "view", _slug(repo), "--json", "defaultBranchRef"]


def gh_recent_runs(
    repo: object,
    *,
    branch: str | None = None,
    limit: int = GH_DEFAULT_RUN_LIMIT,
) -> list[str]:
    """Recent CI runs. ``branch`` is omitted rather than defaulted - see above."""
    argv = [
        "gh",
        "run",
        "list",
        "--repo",
        _slug(repo),
        "--limit",
        _limit(limit, what="limit"),
        "--json",
        ",".join(GH_RUN_FIELDS),
    ]
    if branch is not None:
        if not isinstance(branch, str) or not _BRANCH.match(branch):
            raise RecipeError(f"{branch!r} is not a branch name")
        argv += ["--branch", branch]
    return argv
