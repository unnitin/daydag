"""Running a loop: plan the fetch, hand it to the agent, assemble the push.

USING IT
    python -m daydag.run plan morning              # -> JSON: what to fetch
    python -m daydag.run render morning < payloads.json   # -> the push text

    plan("morning", now=now, identities=ids)       # -> Plan
    render("morning", now=now, identities=ids, payloads=payloads)   # -> str

    A payloads file is `{source: whatever the connector returned}`:
        {"calendar": [...], "slack": [...], "gmail": [...],
         "vault": "..." | null,                 # null: the note does not exist
         "vault_notes": {"<path>": "..." | null},   # eod's Friday reads, by path
         # eod only - the evening sweep (#167); absent = "couldn't check X"
         "slack_sent": [...], "gmail_sent": [...], "slack_sweep": [...],
         "jira": [...], "github": [...]}

CONTRACTS - break one and the guarantee is gone
    1. This module FETCHES NOTHING. Python cannot call an MCP connector; the
       agent can. So a loop runs in two halves with the fetch in between, and
       the package keeps holding no client - the tripwire in
       `tests/test_guardrails.py` stays true because it stays true by
       construction, not by discipline.
    2. The plan carries the recipe's OWN bounds, not a restatement of them.
       One calendar day, an id-scoped Slack query, a bounded Jira window. An
       agent following the plan cannot widen a window by accident, which is
       how a 5-day calendar pull returned 156,681 chars and never arrived.
    3. A missing payload DEGRADES. A source the agent could not reach is one
       "couldn't check X" line and the push still ships (guardrail 6). So is a
       payload of the wrong shape: the agent hands back whatever the connector
       said, and a string where a list belongs is a bad fetch, not a reason to
       lose the other three sources.
    4. `now` must be timezone-aware, because `brief.assemble` refuses a naive
       one - the overnight cutoff is an hour of his day and guessing which day
       is not a thing to do quietly. The runner does not re-add the guess.
    5. Rendering is not sending. This returns text; `daydag.delivery` is the
       only thing that posts, and it has no destination parameter.

WHY IT EXISTS
    Every module took its world as an argument and nothing supplied one. The
    logic was complete and unrunnable: `Sources` is a Protocol with no
    implementation, so a brief could be assembled in a test and nowhere else.

    This is the missing half, and its shape is forced rather than chosen.
    Python in this runtime cannot reach Slack, Gmail or Calendar - only the
    vault and `gh` are direct. The agent holds the connectors. So the seam
    goes exactly where the capability boundary already is.

KNOWN LIMIT
    All eight loops in `LOOPS` render. `prep-ahead` plans in two stages (the day,
    then the reads for each call a prep rule matched) - see `prep_ahead`.
    Without `--for`, `prep` renders the NEXT meeting worth prepping; with it,
    the one he named (`prep_selector`).
    Either way its points come from the overnight Slack payload rather than
    the row-specific searches `prep.sources` would build, because the two-phase
    plan cannot know the row before the fetch. FIRING a prep ping at a
    meeting's start time is scheduling, and belongs to #25, not here.

    The plan is advisory. Nothing verifies the agent actually ran the query it
    was given rather than one of its own, which is why every check that
    matters lives in `daydag.smoke` and runs on the payloads.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from daydag import brief, call_notes, closure, eod_wrap, movement, prep_ahead, recipes, week_ahead
from daydag.board import read_board_watchlist
from daydag.config import ConfigError, resolve_reference, timezone_for
from daydag.ledger import (
    CANCELLED,
    REPLAY_HORIZON,
    Ledger,
    Match,
    Row,
    title_from_gemini_subject,
)
from daydag.people import People
from daydag.prep import Audience, Reason, build, point, prep_worthy
from daydag.prep_selector import HORIZON_DAYS, select
from daydag.pulse import (
    FIRST_SIGHT,
    READ_FAILED,
    READ_OK,
    MirrorStore,
    Pulse,
    PulseError,
    SyncReport,
    github_url,
    mirror_root,
    read_watchlist,
)
from daydag.runlog import RunLog
from daydag.smoke import REACHED
from daydag.state import (
    EventLog,
    NotesGap,
    StateFolder,
    StateNotWritable,
    classify_sensitivity,
)

__all__ = ["LOOPS", "Plan", "RunError", "Step", "main", "plan", "render"]

#: Every loop `SKILL.md` advertises. Three of these used to be absent, and the
#: skill advertised them anyway: `prep.py`, `ingestion.py` and `pulse.py` were
#: built and tested with no way to reach them, so asking for "chase" got a
#: refusal naming the other three (#95).
#:
#: `ship` is the odd one - it reads local git mirrors, not a connector, so its
#: plan has no fetch steps at all. See `_calendar_windows` for the rest.
LOOPS = ("morning", "eod", "week-ahead", "prep", "ingest", "chase", "ship", "prep-ahead")

#: Loops that ask about FUTURE meetings. Remembering what they fetched seeds
#: meetings that have not happened; one cancelled after the snapshot becomes a
#: permanent "meeting w/ no notes" (see the note at `_remember`'s call).
_LOOKS_AHEAD = frozenset({"prep", "prep-ahead"})

#: Loops whose plan asks for no calendar at all. `ingest` left this set in
#: #168: attendance is read off the calendar row's RSVP, so the sweep needs
#: the days its notes can belong to.
_NO_CALENDAR = frozenset({"chase", "ship"})

#: Loops that do NOT remember the calendar they fetched. `prep` fetches the
#: week ahead (see `render`); `ingest` runs every ~30 minutes and would write
#: its two days into the meeting table ~24 times a day, for rows the morning
#: and EOD runs already seed.
_NO_REMEMBER = frozenset({"prep", "ingest"})

#: What the gmail step must hand back per note (#168). Search results are
#: metadata only; the body is where attendance evidence and next steps live.
GMAIL_FIELDS = ("id", "subject", "date", "body", "permalink")

#: Loops that READ the rehydrated ledger - and therefore the only loops whose
#: `--write-state` may project notes gaps. `week-ahead` builds its own ledger;
#: `chase`, `ingest` and `ship` never look at one. The first gate was
#: `_NO_CALENDAR`, which handed chase an EMPTY ledger and then let `_project`
#: write `notes_gaps=[]` over the section the morning run had just recorded.
_NEEDS_LEDGER = frozenset({"morning", "eod", "prep"})


class RunError(RuntimeError):
    """A loop was asked for something it cannot do, in the caller's terms."""


@dataclass(frozen=True)
class Step:
    """One fetch the agent must perform before the loop can be assembled."""

    source: str
    #: Human-readable, for the agent following the plan by hand.
    how: str
    #: Machine-readable: the query, path or window the recipe produced.
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "how": self.how, "detail": dict(self.detail)}


@dataclass(frozen=True)
class Plan:
    """Everything one loop needs fetched, with the recipe's bounds attached."""

    loop: str
    at: str
    steps: tuple[Step, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"loop": self.loop, "at": self.at, "steps": [s.to_dict() for s in self.steps]}


def _known(loop: str) -> str:
    if loop not in LOOPS:
        raise RunError(f"{loop!r} is not a loop; try one of {', '.join(LOOPS)}")
    return loop


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise RunError(
            "now must be timezone-aware - the overnight cutoff is an hour of his day, "
            "and which day that is cannot be guessed from a naive value"
        )
    return now


def _principal(identities: Mapping[str, str]) -> str:
    return resolve_reference(
        "${SLACK_USER_PRINCIPAL}", identities, what="the principal", error=RunError
    )


def plan(
    loop: str,
    *,
    now: datetime,
    identities: Mapping[str, str],
    selector: str = "",
    calendar: Sequence[Mapping[str, Any]] | None = None,
    log: Path | str | None = None,
) -> Plan:
    """What the agent must fetch, with every bound the recipe already applies.

    ``calendar`` and ``log`` are read by `prep-ahead` only, whose plan has two
    stages: without a calendar it asks for the next working day; with one it
    names the reads for each meeting a prep rule matched (`prep_ahead`).
    """
    _known(loop)
    _aware(now)
    principal = _principal(identities)
    # The PRINCIPAL'S day, not the runner's. `now.date()` is the runner's
    # timezone, and `brief` renders the Pacific day - so between 5pm and
    # midnight Pacific the plan fetched one day while the brief reported
    # another, and the two would have disagreed on every evening run. Same
    # convention as `brief._local` and `recipes.timezone_for`.
    tz = timezone_for(identities)
    day = now.astimezone(tz).date()

    if loop == "prep-ahead":
        return _plan_prep_ahead(now, day, identities, calendar, log)

    if loop == "ship":
        # No connector steps at all. `pulse` reads the git mirrors on disk, so
        # this loop is the one that genuinely needs nothing fetched - the plan
        # says so rather than emitting four steps whose payloads it ignores.
        return Plan(
            loop=loop,
            at=now.isoformat(),
            steps=(
                Step(
                    "git",
                    "no connector fetch - sync the mirrors and pass the Pulse to render",
                    {"watchlist": recipes.vault_relative("DayDAG", "Watchlist.md")},
                ),
            ),
        )

    windows = _calendar_windows(loop, day, tz=tz, selector=selector)
    overnight = recipes.slack_overnight(now, mentioning=principal, identities=identities)
    note = recipes.weekly_note(day)

    steps = [
        Step(
            "calendar",
            "one day, never a range - a five-day pull blew the output limit",
            {"day": str(window.day), "time_min": window.time_min, "time_max": window.time_max},
        )
        for window in windows
    ] + [
        Step(
            "slack",
            "search with this query verbatim (the id is already resolved), newest first;"
            " page until a result's ts falls below min_ts, or the older edge of the"
            " window is silently missing on a busy night",
            {"query": overnight.query, "min_ts": overnight.min_ts},
        ),
        Step(
            "gmail",
            "search, then get_message EVERY hit in PLAIN_TEXT - results alone are"
            " metadata, and a note without its body has no next steps and no"
            " attendance evidence. One record per note: id (the message id - the"
            " ingest sweep dedupes on it), subject, date (the mail's own), body"
            " (plaintextBody), permalink (viewUrl)",
            # The window the BRIEF will ask for, not today's. At 6:40am the
            # brief reports on yesterday's meetings and asks gmail for
            # `after:<the evening the overnight window opened>`. A closed
            # today-only window fetched the wrong mail, and `_Payloads.gmail`
            # serves whatever was fetched regardless of the query it is handed,
            # so the two disagreed in silence.
            #
            # Open-ended on purpose. `before:` dropped a note that arrived at
            # 17:12 PDT because gmail put it past the day boundary, and its
            # meeting was then reported as having no notes - while the note
            # sat in the mailbox. Notes also genuinely arrive the next day: a
            # Sep 10 meeting's note landed 00:56 PDT on Sep 11.
            {
                "query": recipes.gmail_gemini_notes(after=_overnight_opened(overnight)),
                "format": "PLAIN_TEXT",
                "fields": list(GMAIL_FIELDS),
            },
        ),
        Step(
            "vault",
            "read this note; it may not exist, which is itself a finding",
            # `weekly_note` already returns the full connector-relative path,
            # prefix included. Prepending a folder to it named nothing.
            {"path": note},
        ),
        *_extra_notes(loop, day),
        *_evening_sweep(loop, day, principal=principal, identities=identities),
    ]
    if loop == "chase":
        # The chaser reads the file he corrects by hand and then READS THE
        # REPLIES. Open is a verdict, not a default: on 2026-09-18 three items
        # were reported open that were answered in the thread under the ask,
        # because the ask's text was matched and the reply never read. So the
        # plan is one read per ask - the conversation after it, and its thread
        # - and `_chase` renders anything not read as "couldn't verify",
        # never as open. The generic steps above fetch nothing this loop uses.
        return Plan(loop=loop, at=now.isoformat(), steps=tuple(_closure_reads(identities)))
    if loop == "prep" and selector:
        # `_prep` reads the calendar and the Slack payload, nothing else. No
        # note can attach to a meeting that has not happened, and the weekly
        # note is never read - so gmail and vault were two connector round-trips
        # for nothing, the same waste `_NO_CALENDAR` exists to prevent.
        steps = [step for step in steps if step.source in {"calendar", "slack"}]
    return Plan(loop=loop, at=now.isoformat(), steps=tuple(steps))


def _prep_ahead_rules(identities: Mapping[str, str]) -> prep_ahead.Rules:
    """His `## Prep rules`, read before every run (the seed if he has none)."""
    folder = _vault(identities)
    return prep_ahead.read_rules(folder.watchlist_path if folder is not None else None)


def _prep_ahead_match(
    events: Sequence[Mapping[str, Any]],
    rules: prep_ahead.Rules,
    directory: People | None,
    identities: Mapping[str, str],
    days: Sequence[date],
) -> tuple[list[prep_ahead.Matched], list[str]]:
    """The matched meetings on the target days, and every line worth saying."""
    wanted = set(days)
    on_day = [
        e
        for e in events
        if isinstance(e, Mapping) and _Payloads._day_of(_Payloads.timed(e)) in wanted
    ]
    roles = {} if directory is None else {person.key: person for person in directory.all()}
    matched, warnings = prep_ahead.match(
        on_day, rules, roles, principal=str(identities.get("EMAIL_PRINCIPAL", "") or "")
    )
    lines = [*rules.notes, *rules.warnings]
    if directory is None and any(rule.attendees for rule in rules.rules):
        lines.append("no people directory (pass --log) - attendee rules can't match anyone")
    else:
        lines += warnings
    return matched, lines


def _plan_prep_ahead(
    now: datetime,
    day: date,
    identities: Mapping[str, str],
    calendar: Sequence[Mapping[str, Any]] | None,
    log: Path | str | None,
) -> Plan:
    """Stage one: the next working day's calendar. Stage two: the reads."""
    rules = _prep_ahead_rules(identities)
    days = prep_ahead.target_days(day, rules)
    tz = timezone_for(identities)
    if calendar is None:
        return Plan(
            loop="prep-ahead",
            at=now.isoformat(),
            steps=tuple(
                Step(
                    "calendar",
                    "one day, never a range; keep attachments and description. then run"
                    " `plan prep-ahead --calendar <that json> --log <db>` for the reads",
                    {"day": str(w.day), "time_min": w.time_min, "time_max": w.time_max},
                )
                for w in (recipes.calendar_day(d, tz=tz) for d in days)
            ),
        )
    directory = People(EventLog.open(log)) if log is not None else None
    matched, _ = _prep_ahead_match(calendar, rules, directory, identities, days)
    reads = prep_ahead.plan_reads(matched, today=day, identities=identities)
    return Plan(
        loop="prep-ahead",
        at=now.isoformat(),
        steps=tuple(Step(r["source"], r["how"], r["detail"]) for r in reads),
    )


def _prep_ahead(
    now: datetime,
    identities: Mapping[str, str],
    payloads: Mapping[str, Any],
    directory: People | None,
) -> str:
    """Tonight's prep for the next working day's calls his rules name (#171)."""
    rules = _prep_ahead_rules(identities)
    day = now.astimezone(timezone_for(identities)).date()
    days = prep_ahead.target_days(day, rules)
    events = payloads.get("calendar")
    if not isinstance(events, list):
        return f"prep: couldn't check the calendar for {days[0]}"
    matched, lines = _prep_ahead_match(events, rules, directory, identities, days)
    return prep_ahead.assemble(days[0], matched, payloads.get("prep_ahead"), notes=lines).render()


def _extra_notes(loop: str, day: date) -> list[Step]:
    """Vault notes a loop reads BEYOND its own weekly note.

    The week-ahead's is next week's note: its lead finding is that the note
    is missing, and before #112 it was never fetched, only guessed from this
    week's.

    Only the EOD wrap has any: on a Friday it reports whether next week's plan
    and next week's meeting prep actually landed, reading both through
    `sources.vault_note`. The plan never asked for them, so even once that
    method existed there was nothing for it to serve - the fix and the fetch
    have to arrive together or the section still never renders.

    They go under `vault_notes`, keyed by path, because `vault` already means
    one specific note and overloading it would make "which note is missing"
    unanswerable.
    """
    if loop == "week-ahead":
        this_monday, _ = recipes.week_range(day)
        return [
            Step(
                "vault_notes",
                "read it; absent is the finding - put it under `vault_notes` keyed by path",
                {"paths": [recipes.weekly_note(this_monday + timedelta(days=7))]},
            )
        ]
    if loop != "eod":
        return []
    next_week = day + timedelta(days=7)
    return [
        Step(
            "vault_notes",
            "read each; absent is the finding - put it under `vault_notes` keyed by path",
            {"paths": [recipes.weekly_note(next_week), recipes.meeting_prep(next_week)]},
        )
    ]


#: The data team's live board (#2 audit), asked for when the watchlist names
#: none or cannot be read - the evening still gets its one bounded Jira read.
_DEFAULT_JIRA_PROJECTS = ("CDI",)

#: Pages of 20 per Slack sweep query. A watched channel on a busy day runs
#: past one page; three is 60 messages a channel, which is the bound.
_SWEEP_MAX_PAGES = 3


def _watchlist_path(identities: Mapping[str, str]) -> Path | None:
    """`DayDAG/Watchlist.md`, or None with no vault configured.

    Built directly rather than through `_vault`, because `StateFolder.create`
    lays out missing files and a PLAN must not write to the vault.
    """
    try:
        return recipes.vault_path(identities, recipes.DAYDAG, "Watchlist.md")
    except recipes.RecipeError:
        return None


def _watchlist_text(path: Path | None) -> str:
    """The watchlist's text, or ``""`` if absent or evicted - an empty
    watchlist, which the plan then reports one step at a time."""
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _evening_sweep(
    loop: str, day: date, *, principal: str, identities: Mapping[str, str]
) -> list[Step]:
    """What the EOD wrap needs to see what MOVED today (#141, #167).

    The morning's two queries - @-mentions and Gemini notes - cannot carry it:
    his reply on an email thread, a doc dropped in a DM, channel traffic that
    never names him, a merged PR, a ticket that changed column. Each step here
    is one bounded query from `daydag.recipes`, and each lands under its own
    payload key so a source that could not be reached degrades to its own
    "couldn't check" line instead of silently thinning another's evidence.

        slack_sent   his own messages today           -> answered / sent
        gmail_sent   his sent mail today              -> answered / sent
        slack_sweep  watched channels + DMs to him    -> discussed
        jira         status/assignee changes, per project -> ticket-moved
        github       merged / reviewed / closed, watched repos -> merged ...

    Evening only: the morning brief's queries are unchanged, per #141.
    """
    if loop != "eod":
        return []
    path = _watchlist_path(identities)
    watchlist = _watchlist_text(path)
    sweep_how = (
        "search with this query verbatim, newest first, at most max_pages pages; "
        "concatenate every step's records into ONE `slack_sweep` list"
    )
    steps = [
        Step(
            "slack_sent",
            "his own messages today, every conversation type; newest first, at most "
            "max_pages pages. Keep channel, channel_name, thread_ts and permalink",
            {
                "query": recipes.slack_sent_on(day, principal=principal, identities=identities),
                "max_pages": _SWEEP_MAX_PAGES,
            },
        ),
        Step(
            "gmail_sent",
            "search, then keep only messages labelled SENT dated today - one record per "
            "message with threadId, to, subject, snippet, date and the permalink (viewUrl)",
            {"query": recipes.gmail_sent_on(day)},
        ),
        Step(
            "slack_sweep",
            sweep_how,
            {
                "query": recipes.slack_dms_on(day, principal=principal, identities=identities),
                "channel_types": "im,mpim",
                "max_pages": _SWEEP_MAX_PAGES,
            },
        ),
    ]
    steps += [
        Step(
            "slack_sweep",
            sweep_how,
            {
                "query": recipes.slack_channel_on(day, channel=channel, identities=identities),
                "max_pages": _SWEEP_MAX_PAGES,
            },
        )
        for channel in recipes.watched_channels(watchlist)
    ]
    steps += _jira_steps(path if watchlist else None, day)
    steps += _github_steps(path if watchlist else None, day)
    return steps


def _jira_steps(watchlist: Path | None, day: date) -> list[Step]:
    """One bounded JQL per watched project - the three-project form overflowed."""
    projects: list[str] = []
    if watchlist is not None:
        try:
            projects = [project.key for project in read_board_watchlist(watchlist).projects]
        except PulseError:  # unreadable; degrade to the default board
            projects = []
    how = (
        "searchJiraIssuesUsingJql with exactly these jql, fields and maxResults (read-only); "
        "concatenate every step's issues into ONE `jira` list, each with its webUrl"
    )
    return [
        Step("jira", how, recipes.jira_moved_on(project, day))
        for project in (projects or list(_DEFAULT_JIRA_PROJECTS))
    ]


def _github_steps(watchlist: Path | None, day: date) -> list[Step]:
    """Merged, reviewed and closed across every watched repo - one search each.

    A merged PR is evidence of movement and never closure (#18): the wrap
    renders it under "moved", and nothing it reads can close a loop.
    """
    repos: list[str] = []
    if watchlist is not None:
        try:
            repos = [repo.slug for repo in read_watchlist(watchlist).repos]
        except PulseError:
            repos = []
    if not repos:
        return [
            Step(
                "github",
                "no watched repos in DayDAG/Watchlist.md - leave `github` out of the payloads",
                {},
            )
        ]
    how = (
        "run this argv with `gh` (read-only); tag every hit with this step's kind and "
        "concatenate all four into ONE `github` list"
    )
    searches = [
        ("merged", recipes.gh_merged_on(repos, day)),
        ("approved", recipes.gh_reviewed_on(repos, day, review="approved")),
        ("changes_requested", recipes.gh_reviewed_on(repos, day, review="changes_requested")),
        ("issue_closed", recipes.gh_closed_issues_on(repos, day)),
    ]
    return [Step("github", how, {"kind": kind, "argv": argv}) for kind, argv in searches]


def _closure_reads(identities: Mapping[str, str]) -> list[Step]:
    """One `slack` step per checkable ask in `State.md` and `Decisions.md`.

    Without a vault there is nothing to check, and the plan says so in one
    step rather than emitting an empty list that reads as "nothing owed".
    """
    folder = _vault(identities)
    if folder is None:
        return [Step("vault", "no vault configured - nothing to chase", {})]
    try:
        state_text = folder.read_state()
    except OSError:
        return [Step("vault", "couldn't read State.md - nothing to chase", {})]
    try:
        decisions_text = folder.decisions_path.read_text(encoding="utf-8")
    except OSError:
        decisions_text = ""
    reads = closure.closure_steps(closure.asks_in(state_text, decisions_text))
    steps = [
        Step(
            "vault",
            "State.md and Decisions.md were read to build the steps below; no payload needed",
            {"state": str(folder.state_path), "decisions": str(folder.decisions_path)},
        )
    ]
    steps += [Step("slack", read.how, read.to_dict()) for read in reads]
    if not reads:
        steps.append(
            Step("slack", "no ask carries a slack permalink - nothing can be verified", {})
        )
    return steps


def _calendar_windows(
    loop: str, day: date, *, tz: Any = recipes.PACIFIC, selector: str = ""
) -> list[recipes.DayWindow]:
    """The calendar windows this LOOP will actually ask its sources for.

    Every loop used to get the same single window - the principal's today -
    because the plan never looked at `loop` at all. Each consumer then asked
    for something else and was served today's events anyway, silently:

        morning      today                    matched, by luck
        eod          TOMORROW                 served today
        week-ahead   next mon-sun, 7 windows  served today, seven times

    So the two loops nobody had run were both fetching the wrong days. Kept in
    step with the consumers deliberately - `eod_wrap` and `week_ahead` derive
    their windows from these same `recipes` helpers, so the arithmetic (and
    the Monday-of-next-week rule) lives in one place rather than two.
    """
    if loop in _NO_CALENDAR:
        # `chase` reads the chase list he maintains by hand and `ship` reads
        # git. Neither looks at the calendar, and fetching a day they ignore
        # is a connector round-trip for nothing.
        return []
    if loop == "ingest":
        # Attendance comes off the calendar row (#168). A Gemini note lands up
        # to 18h after its meeting (`ledger.ARRIVAL_WINDOW`), so a morning
        # sweep meets yesterday's calls: two days, one window each.
        return [recipes.calendar_day(day - timedelta(days=1)), recipes.calendar_day(day)]
    if loop == "prep" and selector:
        # A NAMED prep searches the week, not today - the meeting he wants
        # prepped is usually not today's, that is why he named it. Seven
        # windows, in HIS zone: an earlier version recomputed the day in
        # hardcoded Pacific and fetched an eighth day the match then discarded,
        # so a London principal got windows a day off and the answer "nothing
        # matches" for a meeting that existed. `_prep` derives its `until` from
        # this same arithmetic, so fetch and match are one set.
        return recipes.calendar_days(day, day + timedelta(days=HORIZON_DAYS - 1), tz=tz)
    if loop == "eod":
        # TWO windows, and they have different consumers. `eod_wrap` reads the
        # next WORKING day's, to preview the first meeting of the next day he
        # works - Monday from a Friday, not an empty Saturday (#170).
        # `daydag.movement` reads today's, because a room that was asked for
        # and has now HAPPENED is the cheapest evidence there is that a loop
        # moved - his own example for #134 was "drokit meeting w/ chris has
        # been scheduled, you can confirm that yourself through calendar".
        # Today's window also carries the RSVPs the calls section labels
        # attendance from (#168). `_Payloads.calendar` filters by window, so the
        # wrap's preview still sees only the day it previews.
        return [recipes.calendar_day(day), recipes.calendar_day(recipes.next_working_day(day))]
    if loop == "week-ahead":
        this_monday, _ = recipes.week_range(day)
        next_monday = this_monday + timedelta(days=7)
        # Seven requests, never one wide one - a five-day pull measured 156,681
        # characters and exceeded the connector's output limit (#2 audit).
        return recipes.calendar_days(next_monday, next_monday + timedelta(days=6))
    return [recipes.calendar_day(day)]


def _overnight_opened(window: recipes.OvernightWindow) -> Any:
    """The date the overnight window opened - what `brief` keys its gmail
    query off, so the plan asks for the same thing the consumer will."""
    return datetime.fromtimestamp(window.min_ts, tz=recipes.PACIFIC).date()


class _Payloads:
    """A `brief.Sources` served from what the agent fetched.

    A source the agent could not reach is simply absent, and a source it
    fetched badly is the wrong shape. Both raise here, which is what puts them
    on the brief's own degrade path as one named line instead of taking the
    push down - see contract 3.
    """

    def __init__(self, payloads: Mapping[str, Any], *, weekly_path: str | None = None) -> None:
        self._payloads = payloads
        #: The path the plan fetched `vault` from - this run's weekly note.
        #: `vault` is that one note and no other (#112).
        self._weekly_path = weekly_path

    @staticmethod
    def _instant(value: Any) -> Any:
        """A JSON timestamp as a `datetime`, or the value untouched.

        The one place this seam can go wrong quietly. `brief._local` returns
        None unless the value is a `datetime` OBJECT, and the agent fetches
        over MCP, where every instant is a string - so passing payloads
        through untouched rendered every meeting "all day", right title and
        wrong time, on every real run. No test caught it because tests build
        datetimes directly.

        Google nests it as `{"dateTime": ...}`; an all-day event carries
        `{"date": ...}` and has no instant, which stays untouched and renders
        as the all-day it actually is. An unparseable string also stays put:
        `_local` will read it as no instant, which is a meeting without a
        time rather than a meeting at a guessed one.
        """
        if isinstance(value, Mapping):
            value = value.get("dateTime") or value.get("date_time") or value
        if not isinstance(value, str):
            return value
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value

    #: Every field on a calendar record that is an instant. `start` alone was
    #: parsed and `end` was not - one field, not its twin - so the ledger got a
    #: string, raised, and the whole notes-gap mechanism degraded to "couldn't
    #: check the meeting ledger" on every run. That is the differentiator
    #: quietly not working: a meeting with no row today is a gap that can never
    #: be surfaced tomorrow.
    _INSTANTS = ("start", "end")

    @classmethod
    def timed(cls, record: Any) -> Any:
        """A record with every instant field parsed. Shared with the replay
        path, which reads the same JSON back out of the event log."""
        if not isinstance(record, Mapping):
            return record
        parsed = {name: cls._instant(record[name]) for name in cls._INSTANTS if name in record}
        return {**record, **parsed} if parsed else record

    def _records(self, name: str) -> list[Mapping[str, Any]]:
        if name not in self._payloads:
            raise RunError(f"{name} was not fetched")
        value = self._payloads[name]
        if not isinstance(value, list):
            raise RunError(f"{name} came back as {type(value).__name__}, not a list of records")
        return value

    @staticmethod
    def _day_of(record: Mapping[str, Any]) -> date | None:
        """The local date a `timed()` record starts on, or None if unplaceable.

        Reads only what `timed` leaves behind: a `datetime` for anything it
        could parse, google's all-day `{"date": ...}` untouched, or a value
        neither of them understood. It used to re-run the string parse `_instant`
        had just done one line earlier - two parsers of one field in one class,
        which an alias added to one would silently not reach in the other.
        """
        start = record.get("start")
        if isinstance(start, datetime):
            # Naive means already his wall-clock, same contract as `brief._local`.
            return (start.astimezone(recipes.PACIFIC) if start.tzinfo else start).date()
        if isinstance(start, date):
            return start
        if isinstance(start, Mapping):  # all-day: {"date": "YYYY-MM-DD"}
            try:
                return date.fromisoformat(str(start.get("date", "")))
            except ValueError:
                return None
        return None

    def calendar(self, window: recipes.DayWindow) -> Sequence[Mapping[str, Any]]:
        """The fetched events that fall on ``window``'s day.

        Filtered, and that is the whole point. This used to return the entire
        bucket for ANY window, so a consumer asking for a day the plan never
        fetched was served a different day's events and could not tell:

        * `eod_wrap` asks for TOMORROW and was handed today, so the wrap
          printed this morning's 8:15 standup as tomorrow's first meeting.
        * `week_ahead` asks for seven days, one window at a time, and was
          handed the same single day seven times over.

        Neither raised. Wrong data in a push is worse than a missing section,
        because a missing one says so. Now an unfetched window comes back
        empty, which is a section omitted rather than a section that lies.

        A record whose start cannot be placed at all is returned for every
        window rather than dropped: an untimed meeting is still a meeting, and
        losing its ledger row loses a notes gap permanently. `Ledger.seed_day`
        keys on (id, start) and so absorbs the repeat.
        """
        records = [self.timed(record) for record in self._records("calendar")]
        return [r for r in records if self._day_of(r) in (window.day, None)]

    def slack(self, query: str) -> Sequence[Mapping[str, Any]]:
        return self._records("slack")

    def gmail(self, query: str) -> Sequence[Mapping[str, Any]]:
        return self._records("gmail")

    def weekly_note(self, path: str) -> str:
        """The weekly note, in the THREE states `brief.read_vault_note` tells apart.

        It splits on exception type - text, `FileNotFoundError` for a note that
        was never written, anything else for a source it could not reach - and
        this layer could only ever produce two of the three. `null` had no
        meaning, so an agent reporting "the file is not there" had to choose
        between omitting the key, which renders "couldn't check the weekly
        note" and reads as a downed connector, and sending "", which renders
        NOTHING AT ALL because an empty note is a note that was read.

        The third state is the one that is true: the note is hand-written, and
        the series has had a gap for weeks, so every real run meets it. It is
        also the one worth saying out loud, because the brief cannot triage the
        day against a plan of record that does not exist.

        `str(None)` also used to render the note's body as the literal text
        "None".

            key absent  -> could not reach the vault   (RunError -> degrade)
            null        -> the note does not exist     (FileNotFoundError)
            ""          -> it exists and is empty
            text        -> the note

        Served under the path it was fetched for and no other (#112). This
        used to ignore ``path``, so the week-ahead's check for NEXT week's note
        was handed this week's and "no week-ahead plan" could never fire while
        this week's note existed. Any other path is a `vault_note` read.
        """
        if self._weekly_path is not None and path != self._weekly_path:
            return self.vault_note(path)
        if "vault" not in self._payloads:
            raise RunError("the weekly note was not read")
        note = self._payloads["vault"]
        if note is None:
            raise FileNotFoundError(path)
        return str(note)

    def vault_note(self, path: str) -> str:
        """Any vault note by path, carried in `vault_notes`.

        `eod_wrap` reads next week's plan and next week's meeting prep through
        this to report whether Friday's planning actually landed. The `Sources`
        protocol never declared it, so this class never implemented it, so both
        reads raised and the whole "friday - weekly-planning outcome" section
        was dropped on every real run. Both test doubles have the method, which
        is exactly why nothing failed.

        Same three states as `weekly_note`, one level down:

            vault_notes absent       -> could not read any of them (degrade)
            path absent from the map -> that note does not exist
            null                     -> that note does not exist
            text                     -> the note
        """
        notes = self._payloads.get("vault_notes")
        if not isinstance(notes, Mapping):
            raise RunError(f"{path} was not read")
        note = notes.get(path)
        if note is None:
            raise FileNotFoundError(path)
        return str(note)


#: The event kind a seeded meeting is recorded under, so the next run can
#: rehydrate it. Meetings are not sensitive as a class - a title can be, which
#: is what `NotesGap.sensitivity` is for on the way back out.
MEETING = "meeting"


def _vault(identities: Mapping[str, str]) -> StateFolder | None:
    """The `DayDAG/` folder, or None when no vault is configured.

    Optional because a test and a first run both have none, and because a
    missing vault must not stop a brief - it is one absent section, not a
    stall (guardrail 6).
    """
    root = identities.get("VAULT_ROOT")
    if not root:
        return None
    return StateFolder.create(Path(root) / "DayDAG")


#: Recorded once per remembered meeting that aged past `REPLAY_HORIZON` with no
#: note and was surfaced as "gave up" - so it is said once, not every morning.
GAVE_UP = "meeting_gave_up"

_Key = tuple[str, datetime]


def _key(payload: Mapping[str, Any], *, seedable: bool = True) -> _Key | None:
    """``(event_id, start)`` of a calendar record or remembered row, or None.

    None too, unless ``seedable=False``, for a record `seed_day` cannot take.
    """
    record = _Payloads.timed(dict(payload))
    start = record.get("start")
    needed = _SEEDABLE if seedable else ("id",)
    if not isinstance(start, datetime) or not all(record.get(k) for k in needed):
        return None
    return (str(payload["id"]), start)


#: The fields `Ledger.seed_day` indexes. A record missing one is never keyed,
#: so it is neither remembered nor replayed - replayed, it raised KeyError.
_SEEDABLE = ("id", "start", "end", "summary")


def _local(instant: datetime, tz: Any) -> datetime:
    """``instant`` in ``tz``; a naive one is already his wall clock."""
    return instant.astimezone(tz) if instant.tzinfo else instant.replace(tzinfo=tz)


def _latest(log: EventLog | None) -> dict[_Key, dict[str, Any]]:
    """The newest remembered snapshot of every meeting instance, by key.

    The log is replayed oldest first, so a later snapshot - a decline, the
    evening's `notes_attached`, a tombstone - overwrites an earlier one. This
    is contract 5 of `ledger` applied at the source, and what makes each
    later read O(instances) rather than O(renders).
    """
    latest: dict[_Key, dict[str, Any]] = {}
    if log is None:
        return latest
    for payload in log.recorded(MEETING):
        if isinstance(payload, Mapping) and (key := _key(payload)) is not None:
            latest[key] = dict(payload)
    return latest


def _snapshot(
    latest: Mapping[_Key, Mapping[str, Any]],
    payloads: Mapping[str, Any],
    *,
    loop: str,
    now: datetime,
    tz: Any,
) -> list[dict[str, Any]]:
    """What this run knows about TODAY's meetings that the log does not yet.

    Three rules, each an issue:

    * Only records whose local start day is ``now``'s (#155). eod fetches
      tomorrow and week-ahead next week; a future meeting remembered and then
      cancelled was a permanent gap.
    * Only records that differ from the newest remembered snapshot of the
      same ``(event_id, start)`` (#114). Every render used to append the same
      rows again.
    * A remembered meeting absent from today's fetch gets a `CANCELLED`
      tombstone - but only when today was actually fetched (#169). Absence
      from a day nobody asked about is not evidence.
    """
    day = now.astimezone(tz).date()
    todays: dict[_Key, dict[str, Any]] = {}
    for raw in _seeded(payloads):
        key = _key(raw)
        if key is not None and _local(key[1], tz).date() == day:
            todays[key] = json.loads(json.dumps({k: _jsonable(v) for k, v in raw.items()}))
    changed = [record for key, record in todays.items() if latest.get(key) != record]
    if not _fetched(day, payloads, loop=loop, tz=tz):
        return changed
    gone = [
        {**payload, "status": CANCELLED}
        for key, payload in latest.items()
        if key not in todays
        and _local(key[1], tz).date() == day
        and payload.get("status") != CANCELLED
    ]
    return changed + gone


def _fetched(day: date, payloads: Mapping[str, Any], *, loop: str, tz: Any) -> bool:
    """Whether the calendar for ``day`` was actually read this run.

    Yes when the loop's own plan asks for that day, or when the payload holds
    an event that both starts and ends on it - no other day's window can
    return one. Starting on it is not enough: tomorrow's window returns an
    overnight event that began tonight, and reading that as today's fetch
    would tombstone every meeting he had today.
    """
    if not isinstance(payloads.get("calendar"), list):
        return False
    if any(window.day == day for window in _calendar_windows(loop, day, tz=tz)):
        return True
    for raw in _seeded(payloads):
        record = _Payloads.timed(dict(raw))
        start, end = record.get("start"), record.get("end")
        if isinstance(start, datetime) and isinstance(end, datetime):
            if _local(start, tz).date() == day == _local(end, tz).date():
                return True
    return False


def _gave_up(
    log: EventLog | None, latest: Mapping[_Key, Mapping[str, Any]], now: datetime, tz: Any
) -> list[Row]:
    """Remembered gaps past `REPLAY_HORIZON` that have not been surfaced yet (#125).

    Past the horizon no source can still produce the note, so the gap is a
    decision for him rather than a line to repeat forever. Returned once:
    the caller records `GAVE_UP` for each after showing them.
    """
    if log is None:
        return []
    horizon = now - REPLAY_HORIZON
    surfaced = {
        key
        for payload in log.recorded(GAVE_UP)
        if isinstance(payload, Mapping) and (key := _key(payload, seedable=False))
    }
    old = Ledger()
    old.seed_day(
        [
            record
            for key, payload in latest.items()
            if key not in surfaced and _past(record := _Payloads.timed(dict(payload)), horizon, tz)
        ]
    )
    return [row for row in old.open_rows() if not row.notes_declared]


def _past(record: Mapping[str, Any], horizon: datetime, tz: Any) -> bool:
    end = record.get("end")
    return isinstance(end, datetime) and _local(end, tz) < horizon


def _gave_up_section(rows: Sequence[Row], tz: Any) -> str:
    """One line per title, its dates beside it: a daily standup that never
    produced a note is one decision, not five lines."""
    dates: dict[str, list[str]] = {}
    for row in rows:
        start = _local(row.start, tz)
        dates.setdefault(row.summary, []).append(f"{start:%a %b} {start.day}".lower())
    return brief.Section(
        f"gave up on notes ({len(rows)})",
        tuple(
            f"- {summary} ({', '.join(days)}) - no note found in"
            f" {REPLAY_HORIZON.days} days, dropping it from the list"
            for summary, days in dates.items()
        ),
    ).render()


def _remembered(
    latest: Mapping[_Key, Mapping[str, Any]], now: datetime, tz: Any = recipes.PACIFIC
) -> Ledger:
    """A ledger carrying every meeting seeded on a previous run, inside the horizon.

    THE reason this module exists rather than `Ledger()` being enough.
    `_seed_and_gaps` seeds today's rows precisely so that tomorrow can report
    the ones that produced nothing - and a ledger rebuilt empty every run has
    no tomorrow. "meetings w/ no notes" could only ever report the empty set,
    and an empty section is omitted rather than labelled, so the one thing
    nothing else in the system can produce failed silently.

    Replayed through `seed_day`, which is idempotent per instance, rather than
    given a second persistence API inside `Ledger` - the composition belongs
    here, not in the thing being composed.

    Reads `_latest`, so only the newest snapshot of each instance is replayed,
    and skips anything that ended before `REPLAY_HORIZON` - `_gave_up` owns
    those.
    """
    horizon = now - REPLAY_HORIZON
    ledger = Ledger()
    ledger.seed_day(
        [
            record
            for payload in latest.values()
            if not _past(record := _Payloads.timed(dict(payload)), horizon, tz)
        ]
    )
    return ledger


def _remember(log: EventLog | None, snapshot: Iterable[Mapping[str, Any]]) -> None:
    """Record what `_snapshot` found, so the next run can ask what produced nothing."""
    if log is None:
        return
    for record in snapshot:
        # Whole, as a mapping: the raw Calendar API puts its own `kind` on
        # every event, and splatted into `record(kind, ...)` it raised.
        log.record(MEETING, record)


def _jsonable(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _chase(
    folder: StateFolder | None,
    log: EventLog | None,
    *,
    fetched: Mapping[str, Any] | None = None,
    principal: str = "",
) -> str:
    """What is owed to him and what he owes, each verified against its thread.

    Not yet marked against the 2-business-day clock - that is the chaser's
    work (#18) and needs asked-on dates this loop does not have. An earlier
    docstring promised the marks; nothing produced them.

    Reads `State.md` rather than deriving the list: it is the file he CORRECTS
    BY HAND, and CLAUDE.md is explicit that a hand edit is an event which wins
    over derived state. A chaser that rebuilt the list from Slack every run
    would silently undo every correction he made.

    ``fetched`` is the `closure` payload: what the agent read for each ask the
    plan named, keyed by the ask's permalink. An ask that is not in it renders
    as "couldn't verify", never as open (`closure` contract 1) - the whole
    reason this loop stopped listing the chase list verbatim. Absent
    altogether, the push says the reads were skipped rather than reporting
    every line open, which was the 2026-09-18 failure.

    The nudges are drafted, never sent - guardrail 1. Nothing here addresses
    anyone but him.
    """
    if folder is None:
        return "owed to you: couldn't check - no vault is configured"
    try:
        written = folder.read_state()
    except Exception:
        return "owed to you: couldn't read State.md"
    try:
        decisions = folder.decisions_path.read_text(encoding="utf-8")
    except OSError:
        decisions = ""

    asks = closure.asks_in(written, decisions)
    if not asks:
        return "owed to you: nothing open"

    verdicts = closure.judge(asks, fetched, principal=principal)
    established = closure.Closure(tuple(verdicts))
    sections: list[brief.Section] = []
    for name in ("Chase list", "Owed by you", "Pending decisions"):
        sections += closure.render_closure(verdicts, section=name)
    unreachable: list[str] = []
    if fetched is None and established.read_nothing:
        # Not one thread was read. Say so once, up top, rather than letting
        # three "couldn't verify" buckets stand in for the reads that were
        # skipped - the plan named them, and skipping them is the bug.
        unreachable.append("the replies - no `closure` payload, so nothing here is verified")

    # Items the log is carrying that the file has not got to yet. `_visible`
    # is the ONE gate on sensitivity and it lives in `state`; this reads what
    # that gate already let through rather than making a second judgement.
    if log is not None:
        try:
            carried = [
                item for item in log.chase_items() if str(item.get("ask", "")) not in written
            ]
        except Exception:
            carried = []
        if carried:
            sections.append(
                brief.Section(
                    f"not yet in State.md ({len(carried)})",
                    # Through `claim`, so the row's own quote and permalink
                    # render - house rule 1 - and the line is bulleted like the
                    # section above it. Bare `owner: ask` strings dropped both
                    # and were invisible to `brief.unsourced_claims`.
                    tuple(
                        brief.claim(
                            f"{item.get('owner', 'someone')} · {item.get('ask', '')}",
                            item.get("permalink") or None,
                            quote=item.get("quote") or None,
                        )
                        for item in carried
                    ),
                )
            )
    return brief.render_push(
        f"open loops - {established.open_count} open, verified", sections, unreachable
    )


def _ingest(
    payloads: Mapping[str, Any],
    identities: Mapping[str, str],
    log: EventLog | None,
    folder: StateFolder | None = None,
    *,
    write_state: bool = False,
) -> str:
    """The sweep: new notes only, each with attendance, marked seen (#168),
    and - with ``write_state`` - their actionable items filed to the
    `State.md` chase list (#180).

    Safe on a ~30-minute cadence - `call_notes.sweep` dedupes on the Gmail
    message id against the event log, and each filed item on (message id,
    item text). A note that is not Gemini-shaped or came back without its
    body is named under "unplaced", never guessed at and never marked seen.

    Files ONLY `call_notes.CHASED` rows. `loop_opened` and `carry_forward`
    stay the morning / EOD projection's to file - this loop does not take
    over their cadence in passing.
    """
    mail = payloads.get("gmail")
    if not isinstance(mail, list):
        return "ingest: couldn't check gmail"
    if not mail:
        return "ingest: nothing new landed"
    text = call_notes.sweep(
        mail,
        _timed_calendar(payloads),
        call_notes.Principal.from_identities(identities),
        log=log,
        decisions=_open_decisions(folder),
        tz=timezone_for(identities),
    )
    if write_state and folder is not None and log is not None:
        try:
            filed = folder.update_state(chase=log.chase_items(kinds={call_notes.CHASED}))
        except StateNotWritable as unwritable:
            # Guardrail 6, as in `render`: one line, never a dead push.
            return text + f"\n\n- couldn't update State.md: {unwritable}"
        if filed:
            text += f"\n\n- filed {filed} to the State.md chase list"
    return text


def _open_decisions(folder: StateFolder | None) -> list[str]:
    """Open decisions' texts from `Decisions.md`, through `closure.asks_in` -
    the same reading the chaser uses, so "open" means one thing. A vault that
    cannot be read costs the decision ranking, nothing else."""
    if folder is None:
        return []
    try:
        text = folder.decisions_path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [ask.text for ask in closure.asks_in("", text) if ask.section == "Pending decisions"]


def _timed_calendar(payloads: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every fetched calendar record, instants parsed. Declined ones included:
    attendance needs them, which is why this is not `_seedable`."""
    return [_Payloads.timed(dict(record)) for record in _seeded(payloads)]


def _calls(
    payloads: Mapping[str, Any],
    identities: Mapping[str, str],
    folder: StateFolder | None,
    now: datetime,
) -> list[brief.Section]:
    """The EOD calls section: today's attendance roll and ranked priorities.

    Open decisions come from `Decisions.md` through `closure.asks_in` - the
    same reading the chaser uses, so "open" means one thing. A vault that
    cannot be read costs the decision ranking, not the section.
    """
    mail = payloads.get("gmail")
    if not isinstance(mail, list):
        return [brief.Section("today's calls", ("- couldn't check today's call notes",))]
    tz = timezone_for(identities)
    return call_notes.priorities(
        mail,
        _timed_calendar(payloads),
        call_notes.Principal.from_identities(identities),
        decisions=_open_decisions(folder),
        day=now.astimezone(tz).date(),
        tz=tz,
    )


def _prep(
    now: datetime,
    identities: Mapping[str, str],
    payloads: Mapping[str, Any],
    ledger: Ledger,
    selector: str = "",
    directory: People | None = None,
) -> str:
    """The meeting to prep for, and what to raise in it.

    Without a selector: the NEXT qualifying one, because a prep ping is the only
    push allowed to interrupt (`prep.may_interrupt`) and is worth exactly as
    much as its timing.

    With one: the meeting he NAMED, and `prep_worthy` is deliberately not
    consulted. That gate answers "is this worth interrupting him for", which is
    a question about an unprompted ping. He asked - a standup he wants prepped
    is a standup he gets prepped, and refusing on the grounds that it is a
    standup would be the tool arguing with the request.

    Several matches surface as several (`Selection.render`). Prep for the wrong
    meeting is worse than none: he reads it, trusts it, and walks into the other
    one cold.
    """
    # The directory when a log exists, so leadership is what he has said about
    # people rather than a CSV; the CSV is unioned in either way (#119).
    audience = (
        Audience.from_directory(directory, identities)
        if directory is not None
        else Audience.from_identities(identities)
    )

    if selector:
        tz = timezone_for(identities)
        day = now.astimezone(tz).date()
        # The END of the last window the plan fetched - same arithmetic as
        # `_calendar_windows`, so what was fetched and what can match are one
        # set rather than a 7-day fetch against an 8-day bound.
        until = datetime.combine(day + timedelta(days=HORIZON_DAYS), time.min, tzinfo=tz)
        try:
            found = select(
                ledger.open_rows(),
                selector,
                now=now,
                until=until,
                # EMAIL_PRINCIPAL, not SLACK_USER_PRINCIPAL: `Row.attendees`
                # holds email addresses, and a Slack id compared against one
                # matches nothing - the exclusion would be dead while looking
                # wired. Resolved the way every other identity is, so a missing
                # key REFUSES instead of silently switching the skip off.
                principal=resolve_reference(
                    "${EMAIL_PRINCIPAL}", identities, what="the principal's address", error=RunError
                ),
                tz=tz,
            )
        except ValueError as bad:
            raise RunError(str(bad)) from bad  # one line on stderr, not a traceback
        row = found.one
        if row is None:
            return found.render()
        # ASKED_FOR, unconditionally. He named it; the ping rules are not
        # consulted, so they must not be credited - a named 1:1 stamped "1:1"
        # would make the rules look better than they are.
        return build(row, Reason.ASKED_FOR, _points(payloads)).render()

    # `open_rows` is already oldest-first; filtering keeps that order.
    for row in (r for r in ledger.open_rows() if r.start >= now):
        reason = prep_worthy(row, audience)
        if reason is None:
            continue
        return build(row, reason, _points(payloads)).render()
    return "prep: nothing coming up that needs it"


def _points(payloads: Mapping[str, Any]) -> list[Any]:
    """Talking points from what the agent fetched, evidence or nothing.

    A message with no permalink is dropped rather than quoted: house rule 1 is
    that a claim carries its link, and a prep point he cannot click through to
    is one he has to take on trust in a meeting.
    """
    # `brief.short`, not a raw slice: it collapses a multi-line message onto
    # the one line `Point.render` has, marks a mid-word cut with an ellipsis
    # rather than presenting it as verbatim (house rule 1), and holds the quote
    # budget in one place. `or ""` because a file-only message carries
    # `"text": null`, and str(None) is the word None quoted as if he said it.
    points: list[Any] = []
    for message in payloads.get("slack", []):
        if not isinstance(message, Mapping) or not message.get("permalink"):
            continue
        text = brief.short(message.get("text") or "")
        if not text:
            continue
        points.append(
            point(
                text,
                "raised since you last met",
                quote=text,
                permalink=message.get("permalink"),
                source="slack",
            )
        )
        if len(points) == 3:
            break
    return points


def build_pulse(
    identities: Mapping[str, str],
    log: EventLog | None,
    *,
    url_for: Callable[[Any], str] = github_url,
) -> tuple[Pulse, SyncReport]:
    """The pulse for this run: mirrors synced, each read on from its stored cursor.

    The CLI path #138 was missing. `render` accepted a pulse and `main` never
    built one, so `ship` degraded on every real run and the shipping sections
    of the morning, the wrap and the week-ahead never rendered. Cursors come
    from the event log and go back to it (`store_cursors`) once the push has
    rendered - without that every run started at first sight and reported a
    quiet day forever.

    Raises:
        RunError: no vault, so no `Watchlist.md` to read repos from.
        PulseError, ConfigError: an unreadable watchlist, no `MIRROR_DIR`.
    """
    folder = _vault(identities)
    if folder is None:
        raise RunError("no vault is configured, so there is no Watchlist.md to read repos from")
    watchlist = read_watchlist(folder.watchlist_path)
    store = MirrorStore(mirror_root(identities), url_for=url_for, log=log)
    cursors: dict[str, str] = {}
    if log is not None:
        for repo in watchlist.repos:
            # A repo marked `wiki` is two mirrors, each with its own cursor.
            for target in (repo, repo.wiki_repo()) if repo.wiki else (repo,):
                if cursor := log.last_cursor(target.slug):
                    cursors[target.slug] = cursor
    report = store.sync(watchlist, cursors=cursors)
    return Pulse.from_sync(report), report


def store_cursors(log: EventLog, report: SyncReport) -> None:
    """Remember where each mirror read up to. After the render, never before:
    the cursor advances when the pulse lists its items, and a cursor stored
    ahead of a push that then failed would bury those landings.

    Only a cursor a read produced is stored. A mirror nobody read (a stale
    one, or a loop that never listed the pulse's items) still holds what it
    started as - for a `branch:` row the branch NAME, which stored back reads
    as an empty range forever. A read that FAILED goes back to first sight:
    the usual cause is an upstream force-push that left the stored sha
    unreachable, and storing it again failed every run after.
    """
    for mirror in report.mirrors:
        if mirror.read == READ_OK:
            log.record_cursor(mirror.label, mirror.cursor)
        elif mirror.read == READ_FAILED:
            log.record_cursor(mirror.label, FIRST_SIGHT)


def render(
    loop: str,
    *,
    now: datetime,
    identities: Mapping[str, str],
    payloads: Mapping[str, Any],
    state: StateFolder | None = None,
    log: Path | str | None = None,
    pulse: Any = None,
    write_state: bool = False,
    selector: str = "",
) -> str:
    """The push text for ``loop``, assembled from what the agent fetched.

    ``log`` is the event log's path. Given one, the loop REMEMBERS: meetings
    seeded today are readable tomorrow, and the run leaves a row either way.
    Without one every run starts blank, which is correct for a test and wrong
    for a morning.
    """
    _known(loop)
    _aware(now)
    sources = _Payloads(
        payloads, weekly_path=recipes.weekly_note(now.astimezone(timezone_for(identities)).date())
    )
    folder = state if state is not None else _vault(identities)
    events = log if log is None else EventLog.open(log)
    directory = (
        People(events)
        if events is not None and (loop in _NEEDS_LEDGER or loop == "prep-ahead")
        else None
    )
    tz = timezone_for(identities)
    # The meeting table is read only by a loop that fetched a calendar or
    # reads the ledger; `chase`, `ingest` and `ship` have nothing to add to it.
    fetched_calendar = isinstance(payloads.get("calendar"), list)
    latest = _latest(events) if fetched_calendar or loop in _NEEDS_LEDGER else {}
    snapshot = _snapshot(latest, payloads, loop=loop, now=now, tz=tz)
    gave_up: list[Row] = []
    if loop not in _NEEDS_LEDGER:
        # `chase`, `ingest`, `ship` and `week-ahead` never read this ledger
        # (week-ahead builds its own). Rehydrating every remembered meeting and
        # offering every note to it is a full replay of the meeting table for a
        # loop that then ignores the result - and the table grows with every
        # run, so the waste grows with the log.
        ledger = Ledger()
    else:
        ledger = _remembered(latest, now, tz)
        # This run's snapshot is newer than anything remembered: a meeting
        # declined or removed since the last run leaves the ledger now, not
        # only on the next run (#169).
        ledger.seed_day([_Payloads.timed(record) for record in snapshot])
        if loop == "morning":
            gave_up = _gave_up(events, latest, now, tz)
        # SEED TODAY BEFORE OFFERING NOTES. `brief._seed_and_gaps` seeds during
        # assembly, which is too late: a note offered to a ledger that has no
        # rows yet attaches to nothing, and on a FIRST run there are no
        # rehydrated rows either - so every meeting became a gap while its note
        # was listed by name in the section directly above. `seed_day` is
        # idempotent per instance, so brief's own seeding stays a no-op.
        ledger.seed_day(_seedable(payloads))
        _attach_notes(ledger, payloads, now)
        if directory is not None:
            _observe_past(directory, ledger, identities, now)
    runner = RunLog(events, clock=lambda: now) if events is not None else None

    def _assemble() -> str:
        if loop == "ship":
            # Nothing fetched, so nothing to serve - `pulse` already read the
            # mirrors. Absent, it degrades like any other source (guardrail 6)
            # rather than raising: one missing line, not a dead push.
            if pulse is None:
                return "shipped: couldn't check - no pulse was built"
            return pulse.render()
        if loop == "chase":
            return _chase(
                folder,
                events,
                fetched=payloads.get("closure"),
                principal=identities.get("SLACK_USER_PRINCIPAL", ""),
            )
        if loop == "ingest":
            return _ingest(payloads, identities, events, folder, write_state=write_state)
        if loop == "prep":
            return _prep(now, identities, payloads, ledger, selector, directory)
        if loop == "prep-ahead":
            return _prep_ahead(now, identities, payloads, directory)
        if loop == "morning":
            return brief.assemble(
                now=now,
                sources=sources,
                identities=identities,
                state=folder,
                ledger=ledger,
                pulse=pulse,
            ).render()
        if loop == "eod":
            rows = _movement_rows(folder, payloads, now)
            return eod_wrap.assemble(
                now=now,
                sources=sources,
                ledger=ledger,
                pulse=pulse,
                movement=rows,
                unclaimed=movement.unclaimed(
                    rows, jira=_evening(payloads, "jira"), github=_evening(payloads, "github")
                ),
                unchecked=_unchecked(payloads),
                calls=_calls(payloads, identities, folder, now),
            ).render()
        return week_ahead.assemble(
            now=now, sources=sources, identities=identities, state=folder, pulse=pulse
        ).render()

    if runner is None:
        text = _assemble()
    else:
        # A run that dies halfway is exactly the one somebody opens the log
        # for, so the row is written either way and the failure re-raised.
        with runner.run(f"loop: {loop}") as active:
            text = _assemble()
            active.observe([{"name": loop, "source": "daydag", "status": REACHED, "reason": ""}])

    if gave_up:
        # Said once, in the morning, then recorded so it is not said again.
        text += "\n\n" + _gave_up_section(gave_up, tz)
        if events is not None:
            for row in gave_up:
                events.record(GAVE_UP, id=row.event_id, start=row.start.isoformat())
    if loop not in _NO_REMEMBER and loop not in _LOOKS_AHEAD:
        # A prep is a QUESTION about the week ahead, not a day's seeding.
        # Remembering its seven fetched days persisted every future meeting;
        # one cancelled after the snapshot was re-seeded on every later run and
        # reported as a permanent "meeting w/ no notes", and a rescheduled one
        # became two rows - a phantom gap beside the real meeting. `_snapshot`
        # now keeps only today's rows, but prep still records nothing.
        _remember(events, snapshot)
    # Only the ledger-carrying loops project. The original reason - that a loop
    # handed an empty ledger would REPLACE the notes-gaps section with nothing -
    # stopped applying when `update_state` became an append (#130): projecting
    # from an empty ledger now adds nothing and harms nothing. What the gate
    # still does is keep `chase`, `ingest`, `ship` and `week-ahead` from filing
    # their derived items, and whether it should is a separate decision from
    # this one, so it stays as it is rather than being widened in passing.
    if write_state and loop in _NEEDS_LEDGER and folder is not None and events is not None:
        try:
            _project(folder, events, ledger, now)
        except StateNotWritable as unwritable:
            # Guardrail 6: one line, never a dead push. The brief is already
            # assembled at this point and `main` prints the RETURN VALUE, so
            # letting this propagate threw away a complete brief over a file
            # the writer declined to touch - the loudest possible failure for
            # the most conservative possible refusal.
            text += f"\n\n- couldn't update State.md: {unwritable}"
    return text


def _observe_past(
    directory: People, ledger: Ledger, identities: Mapping[str, str], now: datetime
) -> None:
    """Teach the directory who was in the meetings that have already HAPPENED.

    Past only. A named prep seeds seven future days, and observing those would
    record him as having met people at meetings not yet held. Skipped entirely
    when EMAIL_PRINCIPAL is unset: without it he would be added to his own
    directory on every row.
    """
    principal = str(identities.get("EMAIL_PRINCIPAL", "") or "")
    if not principal:
        return
    for row in ledger.open_rows():
        if row.end <= now:
            directory.observe(row, principal=principal)


def _attach_notes(ledger: Ledger, payloads: Mapping[str, Any], now: datetime) -> None:
    """Offer every fetched Gemini note to the ledger.

    `Ledger.offer_note` was called by five test files and by NO production
    code - the matching half of the ledger existed and was never wired, the
    same way `title_from_gemini_subject` was once dead code the docs described
    as live. Unwired, no note ever attaches to a row, so every meeting is a
    gap forever and "meetings w/ no notes" becomes every meeting every day.
    Noise, which is worse than the absence it was meant to replace.

    A note whose title is ambiguous attaches to nothing and stays in
    `ambiguous()` - surface, do not resolve.
    """
    notes = payloads.get("gmail")
    if not isinstance(notes, list):
        return
    for mail in notes:
        if not isinstance(mail, Mapping):
            continue
        title = title_from_gemini_subject(str(mail.get("subject", "")))
        stamp = _Payloads._instant(
            mail.get("arrived") or mail.get("date") or mail.get("internalDate")
        )
        ledger.offer_note(
            Match(
                title=title,
                # The mail's OWN timestamp. `ledger._in_window` attaches a
                # note only inside `ARRIVAL_WINDOW` of the meeting ending, so
                # defaulting to `now` makes a note fetched the next morning
                # unmatchable - and a note that never attaches leaves its
                # meeting reported as a gap unless the calendar declared one.
                arrived=stamp if isinstance(stamp, datetime) else now,
                attendees=[str(a) for a in (mail.get("attendees") or [])],
                # "gemini", not "gmail": `ARRIVAL_WINDOW` is keyed by the
                # system that WROTE the note, not the one that carried it, and
                # an unknown key falls back to the default window silently.
                source="gemini",
            )
        )


def _seedable(payloads: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Today's events, parsed, and only the ones `seed_day` can actually take.

    Tolerant on purpose. This pre-seed exists so a note offered in the same run
    has a row to attach to; it is not the place that judges a malformed event.
    `brief._seed_and_gaps` still refuses one loudly during assembly, which is
    where a missing `end` should surface - skipping it twice would hide it.
    """
    needed = ("id", "start", "end", "summary")
    ready = []
    for raw in _seeded(payloads):
        event = dict(_Payloads.timed(raw))
        if all(event.get(key) for key in needed):
            ready.append(event)
    return ready


def _seeded(payloads: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = payloads.get("calendar")
    return [r for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []


def _movement_rows(
    folder: StateFolder | None, payloads: Mapping[str, Any], now: datetime
) -> list[movement.Movement]:
    """Evidence from the live sources that an open item moved (#134).

    Built here rather than inside `eod_wrap` because the wrap owns no source
    and its `Sources` protocol deliberately does not reach Slack or Gmail -
    but `plan eod` already fetches both, so the payloads are sitting right
    here. `daydag.movement` is pure, so this is the only place the two meet.

    Degrades to no rows rather than raising, for the same reason every other
    read in this file does: a detector is not worth a dead wrap (guardrail 6).
    """
    state = ""
    if folder is not None:
        try:
            state = folder.read_state()
        except OSError:
            state = ""
    note = payloads.get("vault")
    day = now.astimezone(recipes.PACIFIC).date()
    return movement.detect(
        open_items=movement.open_items(
            state=state,
            note=note if isinstance(note, str) else "",
            note_path=recipes.weekly_note(day),
        ),
        calendar=payloads.get("calendar", ()),
        slack=payloads.get("slack", ()),
        gmail=payloads.get("gmail", ()),
        slack_sent=_evening(payloads, "slack_sent"),
        gmail_sent=_evening(payloads, "gmail_sent"),
        slack_sweep=_evening(payloads, "slack_sweep"),
        jira=_evening(payloads, "jira"),
        github=_evening(payloads, "github"),
        now=now,
    )


#: The evening sweep's payload keys (`_evening_sweep`), and the name each one
#: goes by in a "couldn't check" line.
_EVENING_SOURCES = {
    "slack_sent": "his slack messages",
    "gmail_sent": "his sent mail",
    "slack_sweep": "slack channels + dms",
    "jira": "jira",
    "github": "github",
}


def _evening(payloads: Mapping[str, Any], key: str) -> list[Any]:
    """One evening source's records, or ``[]`` when it is absent or malformed.

    The two are told apart by `_unchecked`, not here: to the detector both are
    simply no evidence, and to the wrap they are a "couldn't check" line.
    """
    value = payloads.get(key)
    return value if isinstance(value, list) else []


def _unchecked(payloads: Mapping[str, Any]) -> list[str]:
    """Evening sources that were not fetched, or came back as something other
    than a list - contract 3. An EMPTY list is not here: it means the query
    ran and found nothing, which is silence, not a degrade."""
    return [
        name for key, name in _EVENING_SOURCES.items() if not isinstance(payloads.get(key), list)
    ]


def _project(folder: StateFolder, log: EventLog, ledger: Ledger, now: datetime) -> None:
    """Add what this run learned to `State.md`, leaving the rest alone.

    Through `update_state`'s own `_visible` gate rather than filtering here:
    the runner is not a second writer with its own idea of the rules, and the
    filter that a private carry-forward once slipped past is the one that has
    to hold.

    A notes gap is a meeting TITLE, and a title can be the sensitive fact - an
    exit interview, a comp conversation. Meeting rows are not a vault-bound
    kind (they reach the file through the ledger, not through `chase_items`),
    so the mark the gate reads is decided here, per title, by the same
    classifier every recorded item goes through. Unmarked would mean visible.
    """
    folder.update_state(
        chase=log.chase_items(),
        notes_gaps=[
            NotesGap(title=gap, sensitivity=classify_sensitivity(gap))
            for gap in ledger.notes_gaps(now)
        ],
    )


def main(argv: list[str] | None = None) -> int:
    """`plan` writes JSON to stdout; `render` reads payloads from stdin, or
    from the file `--payloads <path>` names."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] not in {"plan", "render"}:
        print(
            "usage: python -m daydag.run {plan|render} {"
            + "|".join(LOOPS)
            + '} [--log PATH] [--write-state] [--mirrors] [--for "<meeting or person>"]'
            + " [--calendar PATH] [--payloads PATH]"
        )
        return 2

    from daydag.config import Identities

    command, loop = args[0], args[1]
    # `--log <path>` is what makes a run remember: without it the ledger starts
    # empty every morning and a meeting seeded today cannot be a gap tomorrow.
    # `--write-state` projects what the run learned back into `State.md`.
    log = args[args.index("--log") + 1] if "--log" in args[:-1] else None
    write_state = "--write-state" in args
    # `--mirrors` syncs the watchlist's repos and builds the pulse (#138), so
    # `ship` and the shipping sections have something to render.
    with_mirrors = "--mirrors" in args
    # `--for` names the meeting to prep. Without it `prep` takes the next
    # qualifying one, which is the scheduled ping's behaviour.
    selector = ""
    if "--for" in args:
        after = args[args.index("--for") + 1 :]
        if not after or after[0].startswith("--"):
            # Falling through to the next-qualifying meeting here would prep a
            # meeting he did not ask about and say nothing - the wrong-meeting
            # failure prep_selector calls worse than no prep.
            print("--for needs a meeting or a person after it", file=sys.stderr)
            return 2
        selector = after[0]
    # `--calendar <json>` is `prep-ahead`'s second plan stage: the day it asked
    # for, fetched, so the plan can name each matched meeting's reads.
    calendar = None
    if "--calendar" in args:
        after = args[args.index("--calendar") + 1 :]
        if not after or after[0].startswith("--"):
            print("--calendar needs the path of the fetched calendar json", file=sys.stderr)
            return 2
        calendar = json.loads(Path(after[0]).read_text(encoding="utf-8"))
    # `--payloads <json>` hands `render` its payload as a file. A scheduled run
    # is headless Claude, which may not pipe or redirect into a command, so
    # stdin alone left it able to fetch everything and render nothing.
    payloads_path = None
    if "--payloads" in args:
        after = args[args.index("--payloads") + 1 :]
        if not after or after[0].startswith("--"):
            print("--payloads needs the path of the fetched payloads json", file=sys.stderr)
            return 2
        payloads_path = Path(after[0])
    try:
        identities = Identities.from_file(Path(".env"))
        now = datetime.now().astimezone()
        if command == "plan":
            built = plan(
                loop, now=now, identities=identities, selector=selector, calendar=calendar, log=log
            )
            print(json.dumps(built.to_dict(), indent=2))
        else:
            payloads = (
                json.loads(payloads_path.read_text(encoding="utf-8"))
                if payloads_path is not None
                else json.load(sys.stdin)
            )
            pulse, report = None, None
            if with_mirrors:
                try:
                    pulse, report = build_pulse(identities, EventLog.open(log) if log else None)
                except (RunError, ConfigError, PulseError) as unbuilt:
                    # Guardrail 6: no pulse is one degrade line in the push,
                    # never a dead run. `render` already says it for `ship`.
                    print(f"couldn't build the pulse: {unbuilt}", file=sys.stderr)
            text = render(
                loop,
                now=now,
                identities=identities,
                payloads=payloads,
                log=log,
                pulse=pulse,
                write_state=write_state,
                selector=selector,
            )
            if log and report is not None:
                store_cursors(EventLog.open(log), report)
            if text:
                print(text)
            else:
                # Stdout stays empty so a scheduled run posts nothing; a run by
                # hand still learns why.
                print(f"{loop}: nothing to post", file=sys.stderr)
    except (RunError, ConfigError) as bad:
        print(f"{bad}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
