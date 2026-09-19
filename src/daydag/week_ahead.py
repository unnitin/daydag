"""The Sunday week-ahead (SPEC 3.6): a read of Friday's plan, pushed once.

USING IT
    pushed = assemble(
        now=datetime(2026, 9, 6, 17, 30, tzinfo=recipes.PACIFIC),  # a Sunday
        sources=connectors,     # daydag.push.Sources - the same reads
        identities=identities,
        state=state_folder,     # optional: chase + watch, via push.state_lines
        pulse=pulse,             # optional: the shipping block
    )
    pushed.render()              # the one Slack DM
    pushed.monday_preps          # Monday meetings due prep, and how to source them

CONTRACTS - break one and the guarantee is gone
    1. Read, never re-derive (SPEC 3.6, and ARCHITECTURE's ownership table).
       The week's plan is `weekly-planning`'s Friday output; this module's only
       interaction with it is :func:`daydag.push.red_items` over the text it
       already wrote. Nothing here writes to ``Weekly Notes/`` or
       ``Fact Base/Workstreams.md`` - those stay `weekly-planning`'s.
    2. A missing plan LEADS the message (rule 3), not a footnote: when the
       next week's note does not exist, its section is the first one, ahead
       of a single meeting - the week-ahead never fabricates Priorities from
       the closing week's note instead.
    3. No chase goes out on a weekend (rule 1). This module holds no send path
       and drafts no nudge text: the carrying-in section is a plain, evidenced
       list, never a decision queued for a yes.
    4. Monday prep is pre-built, not pre-sent (rule 2). ``monday_preps`` names
       every Monday meeting that qualifies, reusing :mod:`daydag.ledger` for
       qualification and :mod:`daydag.prep` for the reason and the source
       queries - so the loop that threads the actual ping does not re-derive
       either, only fetches and reads what the query already names.
    5. Silence is information, evidence or silence, degrade never stall -
       `daydag.push`'s contracts 1-3, inherited by rendering through its
       `Section`, `claim` and `Reader` rather than restated here.
    6. Nothing in this module renders. The push shape, the clock, the
       weekly-note scan and the overlap rule are all `daydag.push`'s,
       imported and never re-derived.

WHY IT EXISTS
    Nothing else runs on a weekend, and Monday morning is too late to move a
    meeting that should not have survived the weekend. The two jobs this
    module exists for are cheap only if done Sunday night: pre-running Monday
    so 6:45am holds no surprise, and naming a collision - a meeting still on
    the books with someone who is flying - while there is still time to text
    about it. Both are composition over what other modules already know
    (`push.red_items` and `push.overlap_clusters`, `statedoc`'s chase and watch
    sections, `pulse.render`, `ledger` + `prep`'s qualification), which is the
    whole point: SPEC 3.6 calls this "largely composition of existing pieces,"
    and every seam defect this repo has had came from a loop re-deriving a rule
    some other module already got right.

KNOWN LIMIT
    The chase list's clock is not yet ACTED on.
    ``statedoc.StateFolder.update_state`` does render ``asked-on`` now (#130),
    but the open-loop chaser (SPEC 3.4) that would compare it against today
    has not shipped, and most rows are his own and carry whatever he typed -
    so the carrying-in section lists the WHOLE chase list rather than only the
    loops whose clock expires Monday-Wednesday, which is what rule 1 literally
    asks for. That is the honest degrade available today: a narrower cut would
    be inventing a date this module does not have, and CLAUDE.md's "surface,
    don't resolve" applies to a gap in the agent's own data the same as to
    anyone else's.

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

from daydag import recipes, voice
from daydag.ledger import Ledger, part_of_the_week
from daydag.prep import Audience, Reason, SourcePlan, prep_worthy
from daydag.prep import sources as build_source_plan
from daydag.pulse import Pulse
from daydag.push import (
    WARN,
    Push,
    Reader,
    Section,
    Sources,
    aware,
    claim,
    clock,
    day_label,
    instant,
    link,
    local,
    overlap_clusters,
    read_vault_note,
    red_items,
    seed_ledger,
    shipping_lines,
    state_lines,
)
from daydag.statedoc import StateFolder

__all__ = [
    "MondayPrep",
    "WeekAhead",
    "assemble",
]

#: Loose net on purpose. A false positive here costs one inline flag Nitin
#: reads past in a second; a false negative is the "CTO flying Tue" case rule
#: 2 of SPEC 3.6 exists to catch - cheaper to be wrong-and-visible than
#: right-and-silent, discovered Tuesday morning instead of Sunday night.
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
    permalink: str | None = None

    def line(self) -> str:
        """The push line: when, what, why it qualifies, cited to the invite."""
        why = self.reason.value if hasattr(self.reason, "value") else str(self.reason)
        return claim(f"{clock(self.starts)} {self.meeting} - {why}", self.permalink)


@dataclass(frozen=True)
class WeekAhead(Push):
    """One Sunday's push, plus the Monday prep queue a later loop threads.

    A `Push` with one more field: the preps are RENDERED as a section (SPEC
    3.6 rule 2, "pre-built, not pre-sent") and carried as objects for the
    loop that fetches their sources.
    """

    monday_preps: tuple[MondayPrep, ...] = ()


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


# --------------------------------------------------------------------------
# the calendar: Monday in full, Tue-Fri as a shape
# --------------------------------------------------------------------------


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

    A day with nothing on the calendar is omitted rather than labelled "open"
    (contract 6): there is no permalink for the absence of a meeting, and
    "silence is information" applies here exactly like an empty section.
    """
    lines: list[str] = []
    for day in days:
        events = sorted(by_day.get(day, ()), key=instant)
        if not events:
            continue
        label = _WEEKDAY[day.weekday()]
        titles = [str(event.get("summary", "untitled")) for event in events]
        plural = "" if len(events) == 1 else "s"
        # Named, not enumerated. Nineteen titles inline is a wall of text in a
        # Slack DM, which is the delivery surface - the point of this line is
        # the SHAPE of the day. The full list is one section up for Monday and
        # on his calendar for the rest.
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
    `push.overlap_flags`, which is the difference between a usable line and a
    wall: a real week measured FOUR meetings stacked at Thursday 11:00, and
    pairwise that is six near-identical lines for one conflict. Here they
    collapse into one item naming the span and everything in it, so the count
    matches the number of decisions he has to make
    (test_a_pile_up_is_one_line_not_every_pair).

    Which invite wins is his call - surfacing that there is a choice is the job
    (invariant 4).
    """
    lines: list[str] = []
    for day in days:
        for cluster in overlap_clusters(by_day.get(day, ())):
            if len(cluster) < 2:
                continue
            titles = ", ".join(str(e.get("summary", "untitled")) for e in cluster)
            # Clusters come back in time order, so the first member is the earliest.
            lines.append(
                claim(
                    f"{WARN} {_WEEKDAY[day.weekday()]} {clock(cluster[0].get('start'))}"
                    f" - {len(cluster)} at once: {titles}",
                    *[link(e) for e in cluster],
                )
            )
    return lines


# --------------------------------------------------------------------------
# Monday prep: which meetings qualify, and what to search for them
# --------------------------------------------------------------------------


def _monday_preps(
    events: Sequence[Mapping[str, Any]], now: datetime, identities: Mapping[str, str]
) -> list[MondayPrep]:
    """Qualifying Monday meetings, reusing the ledger and `prep` verbatim.

    A payload missing a field the ledger needs is refused rather than
    tolerated - `push.seed_ledger`'s rule, with the extra keys this queue
    reads. Silently seeding zero rows here means Monday's prep queue is empty
    and nothing says why
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
    aware(now, "the week-ahead is a Sunday-evening wall-clock push")
    day = now.astimezone(recipes.PACIFIC).date()
    next_monday = recipes.next_monday(day)
    read = Reader()
    sections: list[Section] = []

    # -- the plan weekly-planning produced Friday: read, never re-derived ---
    # A missing plan is not a failure: SPEC 3.6 rule 3 wants it as the LEADING
    # finding, not folded into `unreachable` beside a dead Slack connector.
    next_note_path = recipes.weekly_note(next_monday)
    _, missing_plan = read_vault_note(
        read, lambda: sources.weekly_note(next_note_path), label="the week-ahead plan"
    )

    if missing_plan:
        sections.append(
            Section(
                f"{WARN} no week-ahead plan for {recipes.next_week_label(day)}",
                (
                    claim(
                        "nothing to lead the week with - run weekly-planning now?",
                        next_note_path,
                    ),
                ),
            )
        )

    # -- carryover: the closing week's open red items ------------------------
    # A closing week with no note has nothing to carry forward - not itself a
    # finding; the missing-plan section above already leads.
    closing_note_path = recipes.weekly_note(day)
    closing_note, _ = read_vault_note(
        read, lambda: sources.weekly_note(closing_note_path), label="the closing week's note"
    )

    carrying = [
        claim(text, f"{closing_note_path}#L{lineno}") for lineno, text in red_items(closing_note)
    ]

    # -- chase + watch, read out of the file he corrects by hand -------------
    chase_lines, watch_lines = state_lines(read, state)
    carrying += chase_lines

    if carrying:
        sections.append(Section(f"carrying in ({len(carrying)})", tuple(carrying)))

    # -- the next seven days of calendar, day by day (same as 3.1) ----------
    events: list[Mapping[str, Any]] = []
    for window in recipes.loop_windows("week-ahead", day):
        events += read("calendar", lambda w=window: list(sources.calendar(w)), [])

    # `travel` scans EVERY event, OOO included - that is the signal it exists
    # to find. `by_day` is what "monday"/"the week" render as meetings, so an
    # OOO block must not also count itself as one of the day's meetings.
    travel = _travel_index(events)
    by_day: dict[date, list[Mapping[str, Any]]] = {}
    for event in events:
        # `ledger.part_of_the_week`, NOT a local `kind` check. A local rule
        # applied one test where the ledger applies four, so a DECLINED meeting
        # and a solo errand rendered as part of his week while `_monday_preps`,
        # reading the same events through the ledger, dropped both - two paths
        # in one module, disagreeing.
        #
        # Its own ledger function rather than `qualifies`, for the reason
        # written out there: an UNREADABLE record is kept here and dropped by
        # the ledger, because losing a real meeting off this page is the
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

    # -- clashes, across the WHOLE week ------------------------------------
    # The single most valuable thing on a "prepare my week" page, and it was
    # absent: the brief has done this for one day since the start, and the
    # week-ahead never called it. Measured on a real week: 15 overlapping
    # clusters, every one unflagged.
    clash_lines = _clash_lines(by_day, weekdays)
    if clash_lines:
        sections.append(Section(f"clashes ({len(clash_lines)})", tuple(clash_lines)))

    if watch_lines:
        sections.append(Section("watch", tuple(watch_lines)))

    shipping = shipping_lines(read, pulse)
    if shipping:
        sections.append(Section("shipping", tuple(shipping)))

    # -- Monday prep, pre-built and never pre-sent (contract 4) --------------
    def _preps():
        return _monday_preps(monday_events, now, identities)

    monday_preps = tuple(read("monday prep", _preps, []))
    if monday_preps:
        sections.append(
            Section(
                f"monday prep - pre-built ({len(monday_preps)})",
                tuple(prep.line() for prep in monday_preps),
            )
        )

    return WeekAhead(
        day=day,
        header=voice.render(voice.Push.WEEK_AHEAD, {}),
        sections=tuple(sections),
        unreachable=tuple(read.unreachable),
        monday_preps=monday_preps,
    )
