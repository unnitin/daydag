"""The Sunday week-ahead (SPEC 3.6): a read of Friday's plan, pushed once.

USING IT
    pushed = assemble(
        now=datetime(2026, 9, 6, 17, 30, tzinfo=recipes.PACIFIC),  # a Sunday
        sources=connectors,     # daydag.brief.Sources - the same four reads
        identities=identities,
        state=state_folder,     # optional: chase + watch, read like `brief` reads them
        pulse=pulse,             # optional: the shipping block
    )
    pushed.render()              # the one Slack DM
    pushed.monday_preps          # Monday meetings due prep, and how to source them

CONTRACTS - break one and the guarantee is gone
    1. Read, never re-derive (SPEC 3.6, and ARCHITECTURE's ownership table).
       The week's plan is `weekly-planning`'s Friday output; this module's only
       interaction with it is :func:`daydag.brief.red_items` over the text it
       already wrote. Nothing here writes to ``Weekly Notes/`` or
       ``Fact Base/Workstreams.md`` - those stay `weekly-planning`'s.
    2. A missing plan LEADS the message (rule 3), not a footnote: when the
       next week's note does not exist, its section is the first one, ahead
       of a single meeting - the week-ahead never fabricates Priorities from
       the closing week's note instead.
    3. No chase goes out on a weekend (rule 1). This module holds no send path
       and drafts no nudge text: ``carrying_in`` is a plain, evidenced list,
       never a decision queued for a yes.
    4. Monday prep is pre-built, not pre-sent (rule 2). ``monday_preps`` names
       every Monday meeting that qualifies, reusing :mod:`daydag.ledger` for
       qualification and :mod:`daydag.prep` for the reason and the source
       queries - so the loop that threads the actual ping does not re-derive
       either, only fetches and reads what the query already names.
    5. Evidence or silence, same as `brief`: every claim carries a permalink
       or a note path#line, or says out loud that it has neither.
    6. Silence is information: an empty section is omitted, not labelled -
       a day with nothing on the calendar does not get a line saying so.
    7. Degrade, never stall: a source that raises costs one line and the push
       still ships.

WHY IT EXISTS
    Nothing else runs on a weekend, and Monday morning is too late to move a
    meeting that should not have survived the weekend. The two jobs this
    module exists for are cheap only if done Sunday night: pre-running Monday
    so 6:45am holds no surprise, and naming a collision - a meeting still on
    the books with someone who is flying - while there is still time to text
    about it. Both are composition over what four other modules already know
    (`brief.red_items`, `state.read_section`, `pulse.render`, `ledger` +
    `prep`'s qualification), which is the whole point: SPEC 3.6 calls this
    "largely composition of existing pieces," and every seam defect this repo
    has had came from a loop re-deriving a rule some other module already got
    right.

KNOWN LIMIT
    The chase list's clock is not yet dated. ``state.write_state`` renders
    ``owner · ask`` with no asked-on date, because the open-loop chaser (SPEC
    3.4) that would stamp one has not shipped - so ``carrying_in`` lists the
    WHOLE chase list rather than only the loops whose clock expires
    Monday-Wednesday, which is what rule 1 literally asks for. That is the
    honest degrade available today: a narrower cut would be inventing a date
    this module does not have, and CLAUDE.md's "surface, don't resolve"
    applies to a gap in the agent's own data the same as to anyone else's.

    OOO/travel detection reads Nitin's OWN calendar only. "The people he's
    waiting on" have no calendar this agent can query directly, so a travel
    notice is found only when it already shows up there - a shared OOO entry,
    or a meeting whose attendee list includes the traveller - never invented
    from a name in a chase item or a guess at somebody's schedule.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from daydag import recipes
from daydag.brief import UNSOURCED, Section, Sources, red_items
from daydag.ledger import NON_MEETING_KINDS, Ledger
from daydag.prep import Audience, Reason, SourcePlan, prep_worthy
from daydag.prep import sources as build_source_plan
from daydag.pulse import Pulse
from daydag.state import StateFolder, read_section, split_link
from daydag.voice import Push, render

__all__ = [
    "MondayPrep",
    "WeekAhead",
    "WeekAheadError",
    "assemble",
]


class WeekAheadError(RuntimeError):
    """The week-ahead was asked for something it cannot honestly produce.

    Raised only for a caller mistake - a naive clock - never for a source that
    failed. A failed source degrades to a line; a wrong parameter would
    silently push a message that reads fine and describes the wrong week.
    """


#: The sanctioned warning glyph: plain U+26A0. Its own constant, matching the
#: convention every loop in this package follows (`prep.py`'s `UNSOURCED` is
#: its own string too) rather than reaching into `brief`'s private one.
WARN = "⚠"

#: Loose net on purpose. A false positive here costs one inline flag Nitin
#: reads past in a second; a false negative is the "CTO flying Tue" case rule
#: 2 of SPEC 3.6 exists to catch - cheaper to be wrong-and-visible than
#: right-and-silent, discovered Tuesday morning instead of Sunday night.
_TRAVEL = re.compile(
    r"\b(ooo|pto|vacation|out\s*of\s*office|flight|flying|travel(?:l?ing)?)\b",
    re.IGNORECASE,
)

_WEEKDAY = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class MondayPrep:
    """A Monday meeting that qualifies for a prep ping, and how to source it.

    Not a :class:`daydag.prep.PrepPing` - point selection needs the raw
    evidence fetched and read, which stays the orchestrating loop's job, the
    same way it already is for a same-day prep ping (`prep.py`'s module
    docstring). What this saves the loop is re-deriving *whether* a meeting
    qualifies and *what to search for* - both already computed here.
    """

    meeting: str
    starts: datetime
    reason: Reason
    plan: SourcePlan


@dataclass(frozen=True)
class WeekAhead:
    """One Sunday's assembled week-ahead push."""

    day: date
    header: str
    sections: tuple[Section, ...]
    #: Display names of sources that could not be read this run.
    unreachable: tuple[str, ...] = ()
    #: Monday meetings due a prep ping, for the loop that threads them.
    monday_preps: tuple[MondayPrep, ...] = ()

    def render(self) -> str:
        blocks = [self.header]
        blocks += [section.render() for section in self.sections if section.lines]
        if self.unreachable:
            blocks.append("\n".join(f"- couldn't check {name}" for name in self.unreachable))
        return "\n\n".join(blocks)


# --------------------------------------------------------------------------
# rendering primitives
#
# Deliberately re-derived rather than imported from `brief` - only PUBLIC
# names cross module boundaries in this package (see `README.md`'s module
# table and `prep.py`'s own naive-start guard, which does the same rather
# than reaching into `brief._local`). `UNSOURCED`'s wording is imported,
# though: it is `brief.unsourced_claims`'s only lexical anchor for "this line
# admits it has no evidence", so a second string here would silently stop
# being recognised by the shared check.
# --------------------------------------------------------------------------


def _local(value: Any) -> datetime | None:
    """A calendar instant in the principal's zone.

    Same contract as `brief._local`: a naive value is assumed to already be
    his local wall-clock time, never reinterpreted via the host's zone.
    """
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=recipes.PACIFIC)
    return value.astimezone(recipes.PACIFIC)


def _clock(value: Any) -> str:
    moment = _local(value)
    if moment is None:
        return "all day"
    return f"{moment.hour % 12 or 12}:{moment.minute:02d}"


def _instant(event: Mapping[str, Any]) -> float:
    moment = _local(event.get("start"))
    return moment.timestamp() if moment else float("inf")


def _link(record: Mapping[str, Any]) -> str | None:
    value = record.get("permalink")
    return str(value) if value else None


def _day_label(day: date) -> str:
    return f"{day:%a %b} {day.day}".lower()


def _claim(text: str, *permalinks: str | None) -> str:
    """One push line, with its citation or an admission that it has none."""
    links = [str(link) for link in permalinks if link]
    if not links:
        return f"- {text} ({UNSOURCED})"
    return "- " + text + "".join(f" ({link})" for link in links)


def _line_from_state(body: str) -> str:
    text, link = split_link(body)
    return _claim(text, link)


# --------------------------------------------------------------------------
# OOO / travel - Nitin's own calendar only (KNOWN LIMIT above)
# --------------------------------------------------------------------------


def _is_travel(event: Mapping[str, Any]) -> bool:
    return event.get("kind") == "ooo" or bool(_TRAVEL.search(str(event.get("summary", ""))))


def _travel_index(events: Sequence[Mapping[str, Any]]) -> dict[str, tuple[str, str | None]]:
    """Every attendee named on a travel/OOO event this week, and why.

    First match wins per attendee: this is a flag, not a itinerary, and one
    reason to look twice at a meeting is all rule 2 asks for.
    """
    index: dict[str, tuple[str, str | None]] = {}
    for event in events:
        if not _is_travel(event):
            continue
        summary = str(event.get("summary", "travel"))
        permalink = _link(event)
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


# --------------------------------------------------------------------------
# the calendar: Monday in full, Tue-Fri as a shape
# --------------------------------------------------------------------------


def _monday_lines(
    events: Sequence[Mapping[str, Any]], travel: Mapping[str, tuple[str, str | None]]
) -> list[str]:
    lines: list[str] = []
    for event in sorted(events, key=_instant):
        summary = str(event.get("summary", "untitled"))
        head = f"{_clock(event.get('start'))} {summary}"
        flag = _traveller_flag(event.get("attendees") or [], travel)
        if flag is None:
            lines.append(_claim(head, _link(event)))
        else:
            text, travel_link = flag
            lines.append(_claim(f"{head} - {text}", _link(event), travel_link))
    return lines


def _week_lines(
    by_day: Mapping[date, Sequence[Mapping[str, Any]]],
    days: Sequence[date],
    travel: Mapping[str, tuple[str, str | None]],
) -> list[str]:
    """One bullet per Tue-Fri day that has something on it.

    A day with nothing on the calendar is omitted rather than labelled "open"
    (contract 6): there is no permalink for the absence of a meeting, and
    "silence is information" applies here exactly like an empty section.
    """
    lines: list[str] = []
    for day in days:
        events = sorted(by_day.get(day, ()), key=_instant)
        if not events:
            continue
        label = _WEEKDAY[day.weekday()]
        titles = [str(event.get("summary", "untitled")) for event in events]
        plural = "" if len(events) == 1 else "s"
        body = f"{label}: {len(events)} meeting{plural} - " + ", ".join(titles)
        link = next((_link(event) for event in events if _link(event)), None)
        found_flags = (_traveller_flag(e.get("attendees") or [], travel) for e in events)
        flag = next((flag for flag in found_flags if flag), None)
        if flag is None:
            lines.append(_claim(body, link))
        else:
            text, travel_link = flag
            lines.append(_claim(f"{body} - {text}", link, travel_link))
    return lines


# --------------------------------------------------------------------------
# Monday prep: which meetings qualify, and what to search for them
# --------------------------------------------------------------------------


def _monday_preps(
    events: Sequence[Mapping[str, Any]], now: datetime, identities: Mapping[str, str]
) -> list[MondayPrep]:
    """Qualifying Monday meetings, reusing the ledger and `prep` verbatim.

    A payload missing a field the ledger needs is refused rather than
    tolerated, same as `brief._seed_and_gaps`: silently seeding zero rows here
    means Monday's prep queue is empty and nothing says why.
    """
    for event in events:
        missing = [
            key for key in ("id", "start", "end", "summary", "attendees") if key not in event
        ]
        if missing:
            raise KeyError(
                f"calendar record is missing {', '.join(missing)}; "
                "monday's prep queue cannot qualify it"
            )
    ledger = Ledger()
    ledger.seed_day(events)
    audience = Audience.from_identities(identities)
    preps: list[MondayPrep] = []
    for row in ledger.open_rows():
        reason = prep_worthy(row, audience)
        if reason is None:
            continue
        plan = build_source_plan(row, now, identities=identities)
        preps.append(MondayPrep(meeting=row.summary, starts=row.start, reason=reason, plan=plan))
    return preps


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------


def assemble(
    *,
    now: datetime,
    sources: Sources,
    identities: Mapping[str, str],
    state: StateFolder | None = None,
    pulse: Pulse | None = None,
) -> WeekAhead:
    """Build the Sunday week-ahead push for ``now``'s local week.

    ``state`` and ``pulse`` are optional the same way they are in
    :func:`daydag.brief.assemble`: state the caller owns, not a source, and a
    run without either is silence rather than a failure.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise WeekAheadError(
            "now must be timezone-aware: the week-ahead is a Sunday-evening "
            "wall-clock push, and a naive clock is silently hours wrong on a "
            "UTC runner"
        )
    day = now.astimezone(recipes.PACIFIC).date()
    # `day + 1` only means "next Monday" when `now` actually falls on a
    # Sunday. The scheduled run always does, but SPEC 3.8's on-demand "week
    # ahead" command does not promise that - so this is derived from
    # `week_range`, the one place that arithmetic already lives, rather than
    # assumed: the Monday of `day`'s own week, one week further out, is the
    # upcoming Mon-Fri regardless of which weekday `now` happens to be.
    this_monday, _ = recipes.week_range(day)
    next_monday = this_monday + timedelta(days=7)
    unreachable: list[str] = []

    def read(name: str, call, default):
        try:
            return call()
        except Exception:  # any failure degrades identically
            # Named once, not once per failing call: the calendar alone is up
            # to seven calls (contract 7), and seven identical "couldn't check
            # calendar" lines is the opposite of "thin, still" (rule 4).
            if name not in unreachable:
                unreachable.append(name)
            return default

    sections: list[Section] = []

    # -- the plan weekly-planning produced Friday: read, never re-derived ---
    next_note_path = recipes.weekly_note(next_monday)
    missing_plan = False
    try:
        sources.weekly_note(next_note_path)
    except FileNotFoundError:
        # Not a failure: SPEC 3.6 rule 3 wants this as the LEADING finding,
        # not folded into `unreachable` beside a dead Slack connector.
        missing_plan = True
    except Exception:
        unreachable.append("the week-ahead plan")

    if missing_plan:
        sections.append(
            Section(
                f"{WARN} no week-ahead plan for {recipes.next_week_label(day)}",
                (
                    _claim(
                        "nothing to lead the week with - run weekly-planning now?",
                        next_note_path,
                    ),
                ),
            )
        )

    # -- carryover: the closing week's open red items ------------------------
    closing_note_path = recipes.weekly_note(day)
    try:
        closing_note = sources.weekly_note(closing_note_path)
    except FileNotFoundError:
        # A closing week with no note at all has nothing to carry forward -
        # not itself a finding; the missing-plan section above already leads.
        closing_note = ""
    except Exception:
        closing_note = ""
        unreachable.append("the closing week's note")

    carrying = [
        _claim(text, f"{closing_note_path}#L{lineno}") for lineno, text in red_items(closing_note)
    ]

    # -- chase + watch, read out of the file he corrects by hand -------------
    watch_lines: list[str] = []
    if state is not None:
        written = read("the chase list", state.read_state, "")
        carrying += [_line_from_state(body) for body in read_section(written, "Chase list")]
        watch_lines = [_line_from_state(body) for body in read_section(written, "Watch items")]

    if carrying:
        sections.append(Section(f"carrying in ({len(carrying)})", tuple(carrying)))

    # -- the next seven days of calendar, day by day (same as 3.1) ----------
    events: list[Mapping[str, Any]] = []
    for window in recipes.calendar_days(next_monday, next_monday + timedelta(days=6)):
        events += read("calendar", lambda w=window: list(sources.calendar(w)), [])

    # `travel` scans EVERY event, OOO included - that is the signal it exists
    # to find. `by_day` is what "monday"/"the week" render as meetings, so an
    # OOO block must not also count itself as one of the day's meetings.
    travel = _travel_index(events)
    by_day: dict[date, list[Mapping[str, Any]]] = {}
    for event in events:
        if event.get("kind") in NON_MEETING_KINDS:
            continue
        moment = _local(event.get("start"))
        if moment is not None:
            by_day.setdefault(moment.date(), []).append(event)
    monday_events = by_day.get(next_monday, [])

    if monday_events:
        monday_heading = f"monday ({_day_label(next_monday)})"
        sections.append(Section(monday_heading, tuple(_monday_lines(monday_events, travel))))

    week_days = [next_monday + timedelta(days=offset) for offset in range(1, 5)]
    week_lines = _week_lines(by_day, week_days, travel)
    if week_lines:
        sections.append(Section("the week", tuple(week_lines)))

    if watch_lines:
        sections.append(Section("watch", tuple(watch_lines)))

    if pulse is not None:
        block = read("shipping", pulse.render, "")
        if block.strip():
            sections.append(Section("shipping", tuple(block.splitlines())))

    # -- Monday prep, pre-built and never pre-sent (contract 4) --------------
    def _preps():
        return _monday_preps(monday_events, now, identities)

    monday_preps = tuple(read("monday prep", _preps, []))

    return WeekAhead(
        day=day,
        header=render(Push.WEEK_AHEAD, {}),
        sections=tuple(sections),
        unreachable=tuple(unreachable),
        monday_preps=monday_preps,
    )
