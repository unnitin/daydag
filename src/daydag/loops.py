"""The seven loops, in one table: what each fetches, and how its push is assembled.

USING IT
    LOOPS["morning"].fetches()                 # ("calendar", "slack", "gmail", "vault")
    LOOPS["prep"].fetches("finance")           # a NAMED prep: calendar and slack only
    LOOPS["eod"].extra_notes(day)              # vault paths beyond the weekly note
    text = LOOPS[name].render(context)         # the runner builds the Context
    morning(now=now, sources=sources, identities=ids, state=folder, ledger=ledger, pulse=pulse)
    eod(now=now, sources=sources, ledger=ledger, pulse=pulse)
    week_ahead(now=now, sources=sources, identities=ids, state=folder, pulse=pulse)
    unsourced_claims(text)                     # from daydag.push - must come back empty

CONTRACTS
    1. One table. `LOOPS` is the only place a loop's name is bound to what it
       reads, whether it carries the ledger, and what renders it. `run.plan`
       and `run.render` walk the table; neither matches a loop name.
    2. Each loop fetches what it reads and nothing else (#109). The wrap reads
       no Slack; the week-ahead reads no Slack and no mail; ingest reads mail
       alone; a named prep reads the calendar and Slack. A connector round-trip
       for a payload nobody opens is the waste `recipes.loop_windows` already
       refused for the calendar.
    3. The three pushes share one prologue - a refused naive clock, the
       principal's day, one `Reader`, the weekly note through `vault_note` -
       and `push`'s contracts: silence is information, evidence or silence,
       degrade never stall. Restated nowhere here.
    4. Sources are INJECTED, as an object of methods - never data. A push has
       to observe HOW it asked (one calendar day at a time, an id-scoped Slack
       query), because those are the failures that come back as a plausible
       empty result rather than an error.
    5. The weekly note may NOT EXIST. The series has a gap, so a real run
       meets one; a missing note LEADS a push, and is never conflated with a
       vault that could not be reached (`push.read_vault_note`).
    6. Friday's wrap never OFFERS to run `weekly-planning`. It states, as a
       fact either way, whether the two files that routine writes exist yet
       (SPEC 3.5). The offer is never built, on any day.
    7. The week-ahead READS, never re-derives (SPEC 3.6). Its only use of the
       plan is `push.red_items` over the text `weekly-planning` wrote; Monday
       prep is pre-built and never pre-sent; no chase goes out on a weekend.

WHY IT EXISTS
    Three modules each carried one push body, the runner carried the other
    four, and two if-ladders in the runner plus a set of loop names bound them
    together. Every seam defect this repo has had came from a rule written
    twice, and the three prologues were the same twelve lines written three
    times. The bodies are assembly - and assembly is where the defects have
    lived, because every module was right on its own.

KNOWN LIMIT
    The chase list's clock is not ACTED on: the carrying-in section lists the
    whole chase list rather than only the loops whose clock expires early in
    the week (SPEC 3.6 rule 1), because `asked-on` is whatever he typed and the
    chaser that would read it (#18) has not shipped. Travel detection reads
    his OWN calendar only; the people he waits on have no calendar this agent
    can query. Friday's outcome names the two planning files and stops: there
    is no Workstreams parser and no gmail-draft-status source to cite.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from daydag import closure, push, recipes, voice
from daydag.config import principal_id, resolve_reference, timezone_for
from daydag.eventlog import EventLog
from daydag.ingestion import classify_items, unplaced
from daydag.ledger import Ledger, part_of_the_week, title_from_gemini_subject
from daydag.people import People
from daydag.prep import HORIZON_DAYS, Audience, Reason, SourcePlan, build, point, prep_worthy
from daydag.prep import select as select_meeting
from daydag.prep import sources as build_source_plan
from daydag.pulse import Pulse
from daydag.push import (
    WARN,
    Push,
    PushError,
    Reader,
    Section,
    Sources,
    aware,
    claim,
    clock,
    closed_red_items,
    day_label,
    first_meeting_line,
    instant,
    link,
    local,
    meeting_lines,
    missing_note_section,
    overlap_clusters,
    read_vault_note,
    red_items,
    seed_ledger,
    shipping_lines,
    state_lines,
)
from daydag.statedoc import StateFolder

__all__ = [
    "LOOPS",
    "NAMES",
    "Context",
    "Loop",
    "MondayPrep",
    "RunError",
    "WeekAhead",
    "chase",
    "eod",
    "ingest",
    "morning",
    "prep",
    "ship",
    "week_ahead",
]


class RunError(RuntimeError):
    """A loop was asked for something it cannot do, in the caller's terms."""


@dataclass(frozen=True)
class Context:
    """Everything the runner hands a loop body. State the caller owns, not sources.

    ``ledger`` and ``pulse`` may be empty or ``None`` and that is silence, not a
    failure: a run without a pulse has no shipping section. A source that
    RAISES is the other case, and lands in `Push.unreachable`.
    """

    now: datetime
    identities: Mapping[str, str]
    sources: Sources
    payloads: Mapping[str, Any]
    folder: StateFolder | None
    log: EventLog | None
    ledger: Ledger
    pulse: Pulse | None
    directory: People | None
    selector: str = ""


# --------------------------------------------------------------------------
# the prologue every push shares
# --------------------------------------------------------------------------


def _open(now: datetime, why: str) -> tuple[date, Reader, list[Section]]:
    """Refuse a naive clock, take the principal's day, start one reader."""
    aware(now, why)
    return now.astimezone(recipes.PACIFIC).date(), Reader(), []


def _weekly(
    read: Reader, sources: Sources, day: date, *, label: str = "the weekly note"
) -> tuple[str, str, bool]:
    """The weekly note for ``day``'s week: its path, its text, whether nobody wrote it."""
    path = recipes.weekly_note(day)
    text, missing = read_vault_note(read, lambda: sources.vault_note(path), label=label)
    return path, text, missing


def _calendar(read: Reader, sources: Sources, loop: str, day: date) -> list[Mapping[str, Any]]:
    """Every event in the loop's windows, one read per day.

    `list` INSIDE the lambda: the protocol returns an Iterable, and a paginated
    adapter is naturally a generator that raises on iteration rather than on
    the call. Materialised outside, that failure walks past the degrade path.
    """
    events: list[Mapping[str, Any]] = []
    for window in recipes.loop_windows(loop, day):
        events += read("calendar", lambda w=window: list(sources.calendar(w)), [])
    return events


# --------------------------------------------------------------------------
# morning (SPEC 3.1) - the day's first push, and the fullest one
# --------------------------------------------------------------------------


def morning(
    *,
    now: datetime,
    sources: Sources,
    identities: Mapping[str, str],
    state: StateFolder | None = None,
    ledger: Ledger | None = None,
    pulse: Pulse | None = None,
) -> Push:
    """The morning brief for ``now``'s local day."""
    day, read, sections = _open(now, "the 6pm overnight cutoff is a local wall-clock time")
    principal = principal_id(identities, what="the principal's Slack id", error=PushError)

    events = _calendar(read, sources, "morning", day)
    note_path, note, missing_note = _weekly(read, sources, day)
    if missing_note:
        sections.append(
            missing_note_section(day, note_path, "can't triage today against the week's priorities")
        )
    if events:
        sections.append(Section(f"meetings ({len(events)})", tuple(meeting_lines(events))))

    red = red_items(note)
    if red:
        sections.append(
            Section(
                f"top of the note ({recipes.week_label(day)})",
                tuple(claim(text, f"{note_path}#L{lineno}") for lineno, text in red),
            )
        )

    chase_lines, watch = state_lines(read, state)
    if chase_lines:
        sections.append(Section(f"owed to you ({len(chase_lines)})", tuple(chase_lines)))

    overnight = _overnight_lines(now, principal, sources, read)
    if overnight:
        sections.append(Section(f"overnight ({len(overnight)})", tuple(overnight)))

    shipping = shipping_lines(read, pulse)
    if shipping:
        sections.append(Section("shipping", tuple(shipping)))

    if watch:
        sections.append(Section("watch", tuple(watch)))

    if ledger is not None:
        # Degraded like a source, because it consumes one: `seed_day` indexes
        # calendar keys straight off the record and `notes_gaps` compares `end`
        # to an aware `now`, so a connector that omits a key would take the
        # whole brief down over the one section that reports an absence
        # (test_a_malformed_calendar_record_costs_the_gap_line_not_the_brief).
        gaps = read("the meeting ledger", lambda: _seed_and_gaps(ledger, events, now), [])
        if gaps:
            sections.append(
                Section(
                    f"meetings w/ no notes ({len(gaps)})",
                    tuple(f"- {gap} - no note found in gmail, notion or granola" for gap in gaps),
                )
            )

    header = voice.render(
        voice.Push.MORNING_BRIEF,
        {
            "day": day_label(day),
            # Not `len(events)` when the read failed: zero events and an unread
            # calendar are the same number and not the same fact.
            "count": "?" if "calendar" in read.unreachable else len(events),
        },
    )
    return Push(
        day=day, header=header, sections=tuple(sections), unreachable=tuple(read.unreachable)
    )


def _seed_and_gaps(ledger: Ledger, events: Sequence[Mapping[str, Any]], now: datetime) -> list[str]:
    """Seed today's rows, then report yesterday's meetings that produced nothing.

    Seeding first is the whole mechanism: a meeting with no row can never be
    surfaced as a gap, so the gap the agent reports tomorrow is created today.
    """
    seed_ledger(ledger, events)
    return ledger.notes_gaps(as_of=now)


#: Timestamp fields an adapter may carry. Slack's is `ts`; Gmail's arrival time
#: is `internalDate`, which is what makes the mail half filterable at all -
#: `after:` is day-granular, so the query alone cannot mean "since 6pm".
_STAMP_FIELDS = ("ts", "internal_date", "internalDate")


def _is_overnight(record: Mapping[str, Any], window: recipes.OvernightWindow) -> bool:
    """Whether a record landed inside the overnight window - both bounds.

    Read off the window they were computed with, so the filter and the
    definition cannot drift (`recipes.OvernightWindow.max_ts`,
    test_a_message_from_after_now_is_not_overnight). A record with no usable
    timestamp is KEPT: over-reporting is a line he skims past, under-reporting
    is a silence he has no way to notice.
    """
    for field in _STAMP_FIELDS:
        raw = record.get(field)
        if raw is None:
            continue
        try:
            stamp = float(raw)
        except (TypeError, ValueError):
            return True
        # Gmail's internalDate is milliseconds; Slack's ts is seconds. A value
        # three orders of magnitude past now is the former.
        seconds = stamp / 1000 if stamp > 1e11 else stamp
        return window.min_ts <= seconds <= window.max_ts
    return True


def _overnight_lines(now: datetime, principal: str, sources: Sources, read: Reader) -> list[str]:
    """Slack since 6pm yesterday, plus the Gemini notes that landed with it.

    Two steps for both halves: Slack search resolves to whole days and Gmail's
    ``after:`` is a date, so each query over-fetches and the real cutoff is
    applied here. Dropping the second step is a whole extra workday in a
    6:45am DM, and the same notes re-reported every morning they stay inside
    the ``after:`` day.
    """
    window = recipes.slack_overnight(now, mentioning=principal)
    lines: list[str] = []
    for message in read("slack", lambda: list(sources.slack(window.query)), []):
        if not _is_overnight(message, window):
            continue
        who = message.get("who") or message.get("from") or "someone"
        # `or ""`: a file-only Slack message carries `"text": null`, and str(None)
        # is the word None, quoted as if he had said it.
        lines.append(claim(str(who), link(message), quote=str(message.get("text") or "")))

    # `after` is the evening the window opens, not today: a note that landed
    # at 7pm yesterday is overnight mail.
    opened_on = datetime.fromtimestamp(window.min_ts, tz=recipes.PACIFIC).date()
    query = recipes.gmail_gemini_notes(after=opened_on)
    for mail in read("gmail", lambda: list(sources.gmail(query)), []):
        if not _is_overnight(mail, window):
            continue
        subject = str(mail.get("subject", ""))
        title = title_from_gemini_subject(subject)
        who = "notes landed" if title else str(mail.get("who") or mail.get("from") or "mail")
        lines.append(claim(who, link(mail), quote=title or subject))
    return lines


# --------------------------------------------------------------------------
# eod (SPEC 3.5) - the brief's sibling, a different question
# --------------------------------------------------------------------------

#: Friday, 0-indexed from Monday - `date.weekday()`'s own convention.
_FRIDAY = 4

#: How far ahead the wrap looks for the planning outcome: the week that
#: `weekly-planning` writes on Fridays is the one starting the next Monday.
_NEXT_WEEK = timedelta(days=7)


def eod(
    *,
    now: datetime,
    sources: Sources,
    ledger: Ledger | None = None,
    pulse: Pulse | None = None,
) -> Push:
    """The EOD wrap for ``now``'s local day: what closed, what moved, tomorrow."""
    day, read, sections = _open(
        now, "today's close and tomorrow's first meeting are local wall-clock questions"
    )

    note_path, note, missing_note = _weekly(read, sources, day)
    if missing_note:
        sections.append(
            missing_note_section(
                day, note_path, "can't say what closed today without the week's priorities"
            )
        )
    closed = closed_red_items(note)
    if closed:
        sections.append(
            Section(
                f"closed today ({len(closed)})",
                tuple(claim(text, f"{note_path}#L{lineno}") for lineno, text in closed),
            )
        )

    moved_lines = shipping_lines(read, pulse)
    count = 0
    if moved_lines and pulse is not None:
        # Counted from `items()`, not from the rendered lines: the block also
        # carries stale-mirror notices, and a failure to read is not a thing
        # that moved (test_the_moved_count_counts_movement_not_failure_notices).
        count = len(read("the pulse", pulse.items, []))
        sections.append(Section(f"moved ({count})", tuple(moved_lines)))

    events = _calendar(read, sources, "eod", day)
    picked = first_meeting_line(events)
    if picked is not None:
        line, summary = picked
        tomorrow_lines = [line]
        if ledger is not None:
            gaps = read("the meeting ledger", lambda: ledger.notes_gaps(as_of=now), [])
            if summary in gaps:
                tomorrow_lines.append(
                    f"- {summary}: no note found from last time - want prep built another way? lmk"
                )
        sections.append(Section("tomorrow", tuple(tomorrow_lines)))

    if day.weekday() == _FRIDAY:
        sections += _friday_outcome(read, sources, day)

    header = voice.render(
        voice.Push.EOD_WRAP,
        {
            "closed": "?" if "the weekly note" in read.unreachable else len(closed),
            "moved": "?" if "the pulse" in read.unreachable else count,
        },
    )
    return Push(
        day=day, header=header, sections=tuple(sections), unreachable=tuple(read.unreachable)
    )


def _friday_outcome(read: Reader, sources: Sources, day: date) -> list[Section]:
    """Whether the two files `weekly-planning` writes on Fridays landed.

    A statement either way, never a question (contract 6): a line ending in
    "?" would read as the offer SPEC 3.5 rules out. A file that could not be
    read asserts neither "landed" nor "not yet" - it degrades via
    `read.unreachable` and says nothing here.
    """
    next_week_day = day + _NEXT_WEEK
    checks = (
        ("next week's plan", recipes.weekly_note(next_week_day)),
        ("next week's meeting prep", recipes.meeting_prep(next_week_day)),
    )
    lines: list[str] = []
    for label, path in checks:
        _text, missing = read_vault_note(read, lambda p=path: sources.vault_note(p), label=label)
        if label in read.unreachable:
            continue
        state = "hasn't landed yet" if missing else "landed"
        lines.append(claim(f"{label} {state}", path))
    return [Section("friday - weekly-planning outcome", tuple(lines))] if lines else []


# --------------------------------------------------------------------------
# week-ahead (SPEC 3.6) - a read of Friday's plan, pushed once on Sunday
# --------------------------------------------------------------------------

#: Loose net on purpose. A false positive costs one inline flag he reads past
#: in a second; a false negative is the "CTO flying Tue" case rule 2 exists to
#: catch - cheaper wrong-and-visible Sunday night than right-and-silent Tuesday.
_TRAVEL = re.compile(
    r"\b(ooo|pto|vacation|out\s*of\s*office|flight|flying|travel(?:l?ing)?)\b",
    re.IGNORECASE,
)

_WEEKDAY = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: Titles named inline on a "the week" bullet before it collapses to a count.
_WEEK_TITLES = 3


@dataclass(frozen=True)
class MondayPrep:
    """A Monday meeting that qualifies for a prep ping, and how to source it.

    Not a `prep.PrepPing` - point selection needs the raw evidence fetched and
    read, which stays the orchestrating loop's job. What this saves that loop
    is re-deriving *whether* a meeting qualifies and *what to search for*.
    """

    meeting: str
    starts: datetime
    reason: Reason
    plan: SourcePlan
    permalink: str | None = None

    def line(self) -> str:
        """The push line: when, what, why it qualifies, cited to the invite."""
        why = self.reason.value if hasattr(self.reason, "value") else str(self.reason)
        return claim(f"{clock(self.starts)} {self.meeting} - {why}", self.permalink)


@dataclass(frozen=True)
class WeekAhead(Push):
    """One Sunday's push, plus the Monday prep queue a later loop threads.

    The preps are RENDERED as a section (SPEC 3.6 rule 2, "pre-built, not
    pre-sent") and carried as objects for the loop that fetches their sources.
    """

    monday_preps: tuple[MondayPrep, ...] = ()


def _is_travel(event: Mapping[str, Any]) -> bool:
    return event.get("kind") == "ooo" or bool(_TRAVEL.search(str(event.get("summary", ""))))


def _travel_index(events: Sequence[Mapping[str, Any]]) -> dict[str, tuple[str, str | None]]:
    """Every attendee named on a travel/OOO event this week, and why. First
    match wins per attendee: a flag, not an itinerary."""
    index: dict[str, tuple[str, str | None]] = {}
    for event in events:
        if not _is_travel(event):
            continue
        summary = str(event.get("summary", "travel"))
        permalink = link(event)
        for attendee in event.get("attendees") or []:
            key = str(attendee).strip().casefold()
            if key:
                index.setdefault(key, (summary, permalink))
    return index


def _traveller_flag(
    attendees: Sequence[Any], travel: Mapping[str, tuple[str, str | None]]
) -> tuple[str, str | None] | None:
    """The flag text and its permalink, if any attendee is travelling this week."""
    for attendee in attendees:
        hit = travel.get(str(attendee).strip().casefold())
        if hit:
            summary, permalink = hit
            return f"{WARN} {summary} this week - still on, move it?", permalink
    return None


def _monday_lines(
    events: Sequence[Mapping[str, Any]], travel: Mapping[str, tuple[str, str | None]]
) -> list[str]:
    lines: list[str] = []
    for event in sorted(events, key=instant):
        summary = str(event.get("summary", "untitled"))
        head = f"{clock(event.get('start'))} {summary}"
        flag = _traveller_flag(event.get("attendees") or [], travel)
        if flag is None:
            lines.append(claim(head, link(event)))
        else:
            text, travel_link = flag
            lines.append(claim(f"{head} - {text}", link(event), travel_link))
    return lines


def _week_lines(
    by_day: Mapping[date, Sequence[Mapping[str, Any]]],
    days: Sequence[date],
    travel: Mapping[str, tuple[str, str | None]],
) -> list[str]:
    """One bullet per Tue-Fri day that has something on it.

    A day with nothing on it is omitted rather than labelled "open": there is
    no permalink for the absence of a meeting. Titles are NAMED, not
    enumerated - nineteen inline is a wall of text in a Slack DM; the point
    of the line is the shape of the day.
    """
    lines: list[str] = []
    for day in days:
        events = sorted(by_day.get(day, ()), key=instant)
        if not events:
            continue
        label = _WEEKDAY[day.weekday()]
        titles = [str(event.get("summary", "untitled")) for event in events]
        plural = "" if len(events) == 1 else "s"
        shown = titles[:_WEEK_TITLES]
        tail = "" if len(titles) <= _WEEK_TITLES else f", +{len(titles) - _WEEK_TITLES} more"
        body = f"{label}: {len(events)} meeting{plural} - " + ", ".join(shown) + tail
        cite = next((link(event) for event in events if link(event)), None)
        found_flags = (_traveller_flag(e.get("attendees") or [], travel) for e in events)
        flag = next((flag for flag in found_flags if flag), None)
        if flag is None:
            lines.append(claim(body, cite))
        else:
            text, travel_link = flag
            lines.append(claim(f"{body} - {text}", cite, travel_link))
    return lines


def _clash_lines(
    by_day: Mapping[date, Sequence[Mapping[str, Any]]], days: Sequence[date]
) -> list[str]:
    """Overlapping meetings across the week, one line per pile-up.

    CLUSTERED (`push.overlap_clusters`), not pairwise like the brief's
    `overlap_flags`: four meetings stacked at 11:00 are six near-identical
    pairs for ONE decision (test_a_pile_up_is_one_line_not_every_pair). Which
    invite wins is his call; surfacing that there is a choice is the job.
    """
    lines: list[str] = []
    for day in days:
        for cluster in overlap_clusters(by_day.get(day, ())):
            if len(cluster) < 2:
                continue
            titles = ", ".join(str(e.get("summary", "untitled")) for e in cluster)
            lines.append(
                claim(
                    f"{WARN} {_WEEKDAY[day.weekday()]} {clock(cluster[0].get('start'))}"
                    f" - {len(cluster)} at once: {titles}",
                    *[link(e) for e in cluster],
                )
            )
    return lines


def _monday_preps(
    events: Sequence[Mapping[str, Any]], now: datetime, identities: Mapping[str, str]
) -> list[MondayPrep]:
    """Qualifying Monday meetings, reusing the ledger and `prep` verbatim.

    A payload missing a field the ledger needs is refused rather than
    tolerated - `push.seed_ledger`'s rule. Silently seeding zero rows means
    Monday's prep queue is empty and nothing says why
    (test_a_malformed_monday_event_degrades_the_prep_queue_not_the_whole_push).
    """
    ledger = Ledger()
    seed_ledger(ledger, events, required=("id", "start", "end", "summary", "attendees"))
    permalinks = {str(event.get("id")): link(event) for event in events}
    audience = Audience.from_identities(identities)
    preps: list[MondayPrep] = []
    for row in ledger.open_rows():
        reason = prep_worthy(row, audience)
        if reason is None:
            continue
        plan = build_source_plan(row, now, identities=identities)
        preps.append(
            MondayPrep(
                meeting=row.summary,
                starts=row.start,
                reason=reason,
                plan=plan,
                permalink=permalinks.get(row.event_id),
            )
        )
    return preps


def week_ahead(
    *,
    now: datetime,
    sources: Sources,
    identities: Mapping[str, str],
    state: StateFolder | None = None,
    pulse: Pulse | None = None,
) -> WeekAhead:
    """The Sunday week-ahead push for ``now``'s coming week."""
    day, read, sections = _open(now, "the week-ahead is a Sunday-evening wall-clock push")
    next_monday = recipes.next_monday(day)

    # A missing plan is not a failure: SPEC 3.6 rule 3 wants it as the LEADING
    # finding, not folded into `unreachable` beside a dead connector.
    next_note_path, _, missing_plan = _weekly(
        read, sources, next_monday, label="the week-ahead plan"
    )
    if missing_plan:
        sections.append(
            Section(
                f"{WARN} no week-ahead plan for {recipes.next_week_label(day)}",
                (
                    claim(
                        "nothing to lead the week with - run weekly-planning now?", next_note_path
                    ),
                ),
            )
        )

    # A closing week with no note has nothing to carry forward - not itself a
    # finding; the missing-plan section above already leads.
    closing_note_path, closing_note, _ = _weekly(
        read, sources, day, label="the closing week's note"
    )
    carrying = [
        claim(text, f"{closing_note_path}#L{lineno}") for lineno, text in red_items(closing_note)
    ]
    chase_lines, watch_lines = state_lines(read, state)
    carrying += chase_lines
    if carrying:
        sections.append(Section(f"carrying in ({len(carrying)})", tuple(carrying)))

    events = _calendar(read, sources, "week-ahead", day)

    # `travel` scans EVERY event, OOO included - that is the signal it exists
    # to find. `by_day` is what "monday"/"the week" render as meetings, so an
    # OOO block must not also count itself as one of the day's meetings.
    travel = _travel_index(events)
    by_day: dict[date, list[Mapping[str, Any]]] = {}
    for event in events:
        # `ledger.part_of_the_week`, NOT a local `kind` check: a local rule
        # applied one test where the ledger applies four, and two paths in one
        # module disagreed about a DECLINED meeting. Its own ledger function
        # rather than `qualifies`, because an UNREADABLE record is kept here
        # and dropped there - losing a real meeting off this page is the
        # failure he cannot notice.
        if not part_of_the_week(event):
            continue
        moment = local(event.get("start"))
        if moment is not None:
            by_day.setdefault(moment.date(), []).append(event)
    monday_events = by_day.get(next_monday, [])

    if monday_events:
        monday_heading = f"monday ({day_label(next_monday)})"
        sections.append(Section(monday_heading, tuple(_monday_lines(monday_events, travel))))

    weekdays = [next_monday + timedelta(days=offset) for offset in range(5)]
    week_lines = _week_lines(by_day, weekdays[1:], travel)
    if week_lines:
        sections.append(Section("the week", tuple(week_lines)))

    # Measured on a real week: 15 overlapping clusters, every one unflagged
    # until the week-ahead called the rule the brief had applied for months.
    clash_lines = _clash_lines(by_day, weekdays)
    if clash_lines:
        sections.append(Section(f"clashes ({len(clash_lines)})", tuple(clash_lines)))

    if watch_lines:
        sections.append(Section("watch", tuple(watch_lines)))

    shipping = shipping_lines(read, pulse)
    if shipping:
        sections.append(Section("shipping", tuple(shipping)))

    monday_preps = tuple(
        read("monday prep", lambda: _monday_preps(monday_events, now, identities), [])
    )
    if monday_preps:
        sections.append(
            Section(
                f"monday prep - pre-built ({len(monday_preps)})",
                tuple(item.line() for item in monday_preps),
            )
        )

    return WeekAhead(
        day=day,
        header=voice.render(voice.Push.WEEK_AHEAD, {}),
        sections=tuple(sections),
        unreachable=tuple(read.unreachable),
        monday_preps=monday_preps,
    )


# --------------------------------------------------------------------------
# prep - the meeting to prep for, and what to raise in it
# --------------------------------------------------------------------------


def prep(ctx: Context) -> str:
    """Without a selector, the NEXT qualifying meeting - a prep ping is the one
    push allowed to interrupt (`prep.may_interrupt`) and is worth exactly as
    much as its timing. With one, the meeting he NAMED, and `prep_worthy` is
    not consulted: a standup he asks to have prepped is a standup he gets
    prepped. Several matches surface as several (`Selection.render`): prep for
    the wrong meeting is worse than none.
    """
    # The directory when a log exists, so leadership is what he has said about
    # people rather than a CSV; the CSV is unioned in either way (#119).
    audience = (
        Audience.from_directory(ctx.directory, ctx.identities)
        if ctx.directory is not None
        else Audience.from_identities(ctx.identities)
    )
    if ctx.selector:
        tz = timezone_for(ctx.identities)
        day = ctx.now.astimezone(tz).date()
        # The END of the last window the plan fetched - same arithmetic as
        # `recipes.loop_windows`, so what was fetched and what can match are one
        # set rather than a 7-day fetch against an 8-day bound.
        until = datetime.combine(day + timedelta(days=HORIZON_DAYS), time.min, tzinfo=tz)
        try:
            found = select_meeting(
                ctx.ledger.open_rows(),
                ctx.selector,
                now=ctx.now,
                until=until,
                # EMAIL_PRINCIPAL, not SLACK_USER_PRINCIPAL: `Row.attendees`
                # holds addresses, and a Slack id compared against one matches
                # nothing - the exclusion would be dead while looking wired.
                principal=resolve_reference(
                    "${EMAIL_PRINCIPAL}",
                    ctx.identities,
                    what="the principal's address",
                    error=RunError,
                ),
                tz=tz,
            )
        except ValueError as bad:
            raise RunError(str(bad)) from bad  # one line on stderr, not a traceback
        row = found.one
        if row is None:
            return found.render()
        # ASKED_FOR, unconditionally: the ping rules were not consulted, so
        # they must not be credited.
        return build(row, Reason.ASKED_FOR, _points(ctx.payloads)).render()

    # `open_rows` is already oldest-first; filtering keeps that order.
    for row in (r for r in ctx.ledger.open_rows() if r.start >= ctx.now):
        reason = prep_worthy(row, audience)
        if reason is None:
            continue
        return build(row, reason, _points(ctx.payloads)).render()
    return "prep: nothing coming up that needs it"


def _points(payloads: Mapping[str, Any]) -> list[Any]:
    """Talking points from what the agent fetched, evidence or nothing.

    A message with no permalink is dropped rather than quoted (house rule 1).
    `push.short` holds the quote budget and the mid-word ellipsis that stops a
    cut quote from reading as verbatim; `or ""` because a file-only message
    carries `"text": null`.
    """
    points: list[Any] = []
    for message in payloads.get("slack", []):
        if not isinstance(message, Mapping) or not message.get("permalink"):
            continue
        text = push.short(message.get("text") or "")
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


# --------------------------------------------------------------------------
# ingest - classify what landed, and say plainly what could not be placed
# --------------------------------------------------------------------------


def ingest(ctx: Context) -> str:
    """`classify_items` returns `label=None` for an item it cannot place, and
    the point of surfacing those is that a guess here becomes a vault write
    later. Surface, do not resolve."""
    read = Reader()
    mail = read("gmail", lambda: list(ctx.sources.gmail("")), None)
    if mail is None:
        return "ingest: couldn't check gmail"
    if not mail:
        return "ingest: nothing new landed"

    # `item_id`, not `id`: that is the key `classify_items` reads, and an item
    # without it comes back UNPLACED under a blank name.
    items = [
        {
            "item_id": str(m.get("id") or m.get("subject") or n),
            "text": str(m.get("subject", "")),
        }
        for n, m in enumerate(mail)
        if isinstance(m, Mapping)
    ]
    placed = classify_items(items)
    counts = Counter(record.label for record in placed if record.label)
    sections = [Section("placed", tuple(f"- {label}: {n}" for label, n in sorted(counts.items())))]
    # `ingestion.unplaced` is the one definition of "could not be placed".
    if missing := unplaced(placed):
        sections.append(
            Section(
                f"unplaced ({len(missing)}) - these need you, not a guess",
                tuple(f"- {item_id}" for item_id in missing),
            )
        )
    return push.render_push(
        f"ingest: {len(items)} item{'' if len(items) == 1 else 's'}", sections, ()
    )


# --------------------------------------------------------------------------
# chase - what is owed to him and what he owes, each verified against its thread
# --------------------------------------------------------------------------


def chase(ctx: Context) -> str:
    """Reads `State.md` rather than deriving the list: it is the file he
    CORRECTS BY HAND, and a hand edit wins over derived state. The `closure`
    payload is keyed by each ask's permalink; an ask not in it renders as
    "couldn't verify", never as open (`closure` contract 1). The nudges are
    drafted, never sent.

    KNOWN LIMIT: not marked against the 2-business-day clock - the chaser's
    work (#18), which needs asked-on dates this loop does not have.
    """
    folder, log = ctx.folder, ctx.log
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

    fetched = ctx.payloads.get("closure")
    verdicts = closure.judge(
        asks, fetched, principal=ctx.identities.get("SLACK_USER_PRINCIPAL", "")
    )
    established = closure.Closure(tuple(verdicts))
    sections: list[Section] = []
    for name in ("Chase list", "Owed by you", "Pending decisions"):
        sections += closure.render_closure(verdicts, section=name)
    unreachable: list[str] = []
    if fetched is None and established.read_nothing:
        # Not one thread was read. Say so once, up top, rather than letting
        # three "couldn't verify" buckets stand in for the reads that were
        # skipped - the plan named them, and skipping them is the bug.
        unreachable.append("the replies - no `closure` payload, so nothing here is verified")

    # Items the log is carrying that the file has not got to yet, read past
    # the ONE sensitivity gate (`statedoc._visible`) rather than judged again.
    if log is not None:
        try:
            carried = [
                item for item in log.chase_items() if str(item.get("ask", "")) not in written
            ]
        except Exception:
            carried = []
        if carried:
            sections.append(
                Section(
                    f"not yet in State.md ({len(carried)})",
                    # Through `claim`, so the row's own quote and permalink
                    # render (house rule 1) and `unsourced_claims` sees the line.
                    tuple(
                        claim(
                            f"{item.get('owner', 'someone')} · {item.get('ask', '')}",
                            item.get("permalink") or None,
                            quote=item.get("quote") or None,
                        )
                        for item in carried
                    ),
                )
            )
    return push.render_push(
        f"open loops - {established.open_count} open, verified", sections, unreachable
    )


# --------------------------------------------------------------------------
# ship - the pulse's own block, or one line saying it was not built
# --------------------------------------------------------------------------


def ship(ctx: Context) -> str:
    """Nothing fetched, so nothing to serve - `pulse` already read the mirrors.
    Absent, it degrades like any other source (guardrail 6) rather than
    raising: one missing line, not a dead push."""
    if ctx.pulse is None:
        return "shipped: couldn't check - no pulse was built"
    return ctx.pulse.render()


# --------------------------------------------------------------------------
# the table
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Loop:
    """One loop: what the plan fetches for it, what the runner sets up, what renders it."""

    name: str
    render: Callable[[Context], str]
    #: The sources the plan fetches, in the order its steps are emitted. `closure`
    #: and `git` are the two non-connector reads (`run.plan` knows how to emit each).
    reads: tuple[str, ...]
    #: What a NAMED prep fetches instead. No note can attach to a meeting that
    #: has not happened, so `--for` reads the calendar and Slack only.
    named_reads: tuple[str, ...] | None = None
    #: Vault notes BEYOND the weekly note, by path, for a `vault_notes` step.
    extra_notes: Callable[[date], list[str]] | None = None
    #: Whether the runner rehydrates the ledger, teaches the directory from the
    #: meetings that have happened, and may project to State.md. `week-ahead`
    #: builds its own ledger; `chase`, `ingest` and `ship` never look at one.
    needs_ledger: bool = False
    #: Whether today's calendar is remembered for tomorrow. A named prep's seven
    #: fetched days are a QUESTION about the week, not a day's seeding: remembering
    #: them re-seeded a cancelled meeting as a permanent gap on every later run.
    remembers: bool = True

    def fetches(self, selector: str = "") -> tuple[str, ...]:
        """The sources to fetch, given whether a meeting was named."""
        if selector and self.named_reads is not None:
            return self.named_reads
        return self.reads


def _render_morning(ctx: Context) -> str:
    return morning(
        now=ctx.now,
        sources=ctx.sources,
        identities=ctx.identities,
        state=ctx.folder,
        ledger=ctx.ledger,
        pulse=ctx.pulse,
    ).render()


def _render_eod(ctx: Context) -> str:
    return eod(now=ctx.now, sources=ctx.sources, ledger=ctx.ledger, pulse=ctx.pulse).render()


def _render_week_ahead(ctx: Context) -> str:
    return week_ahead(
        now=ctx.now,
        sources=ctx.sources,
        identities=ctx.identities,
        state=ctx.folder,
        pulse=ctx.pulse,
    ).render()


def _next_week_notes(day: date) -> list[str]:
    """Friday's wrap reports whether next week's plan and prep landed."""
    next_week = day + _NEXT_WEEK
    return [recipes.weekly_note(next_week), recipes.meeting_prep(next_week)]


def _next_plan(day: date) -> list[str]:
    """The week-ahead leads with next week's plan, or with its absence."""
    return [recipes.weekly_note(recipes.next_monday(day))]


#: Every loop `SKILL.md` advertises, in the order it lists them. `ship` reads
#: local git mirrors, not a connector, so its plan has no fetch steps.
LOOPS: Mapping[str, Loop] = {
    loop.name: loop
    for loop in (
        Loop(
            "morning",
            _render_morning,
            reads=("calendar", "slack", "gmail", "vault"),
            needs_ledger=True,
        ),
        Loop(
            "eod",
            _render_eod,
            reads=("calendar", "gmail", "vault", "vault_notes"),
            extra_notes=_next_week_notes,
            needs_ledger=True,
        ),
        Loop(
            "week-ahead",
            _render_week_ahead,
            reads=("calendar", "vault", "vault_notes"),
            extra_notes=_next_plan,
        ),
        Loop(
            "prep",
            prep,
            reads=("calendar", "slack", "gmail"),
            named_reads=("calendar", "slack"),
            needs_ledger=True,
            remembers=False,
        ),
        Loop("ingest", ingest, reads=("gmail",)),
        Loop("chase", chase, reads=("closure",)),
        Loop("ship", ship, reads=("git",)),
    )
}

#: The names, in table order - what the CLI offers and the goldens cover.
NAMES = tuple(LOOPS)
