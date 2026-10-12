"""The morning push, fitted to one Slack message (#185).

USING IT
    fitted = fit_morning(assembled, now=now, gave_up=section, gave_up_titles=titles, late=lines)
    fitted.text()             # the DM; then THREAD_MARK and ONE reply, when one is needed
    fitted.gave_up_shown      # gave-up titles the DM or its reply names: record only these
    fitted.summary()          # one line for the run log (and stderr, in a terminal)
    head, thread = split_parts(fitted.text())
    dangling(fitted)          # [] - every "in the thread" line has its reply

CONTRACTS - break one and the guarantee is gone
    1. A morning whose full text fits `PUSH_BUDGET` comes back BYTE FOR BYTE, every
       meeting's own link and every pairwise overlap flag included. The budget
       only acts on a morning that would not fit.
    2. Otherwise the DM is at most `PUSH_BUDGET` characters, always. Each section
       is ranked and capped, then a deterministic loop shrinks caps in
       `SHRINK_ORDER` until it fits, and gives back any row that then fits
       again; with every cap at zero only headings, count lines, the degrade
       footer and the pulse's own notices (one per unreadable repo) remain,
       which is bounded by construction - by the sources and the watchlist.
    3. Nothing is dropped silently. Every row of a fitted section is shown,
       counted on an overflow line that cites where the rest lives (`State.md`,
       the weekly note, the calendar day), counted with a promise that is true
       ("listed again tomorrow"), or carried to the thread. `Fitted.cuts` holds
       shown + counted == total per section, and a meeting a pile-up row does
       not name is counted, not shown.
    4. No list row loses or cuts a link. A row is shortened in what it says; a
       link inside the words moves to the row's citation first, so every row
       still passes `brief.unsourced_claims`. (A packed meeting TITLE is cut at
       `brief.TITLE_CAP` as a title; its row cites the calendar's day.)
    5. Overflow with no other home - overnight, shipping, and gave-up titles -
       goes to ONE thread reply. Every section the DM says is "in the thread"
       keeps its heading there, with the count it really shows; rows the reply
       itself cannot hold are counted on a line saying where they still are,
       overnight first (still in Slack and Gmail). `dangling` reads the DM's
       own words and checks every promise it makes has its section.
    6. Pure and deterministic: no I/O, no clock, the same input gives the same text.

WHY IT EXISTS
    The morning render ran 23,000 to 59,000 characters against a Slack message
    of about 5,000, so every brief from 21 Sep to 7 Oct was rewritten by hand
    before it shipped, and the soak measured the rewrite instead of the
    renderer. The hand-written briefs he accepted ran 7-12% of the render: the
    whole day's meetings, three or four of each list with sources, counts for
    the rest. This module is that rewrite, as code, so the render is what ships.

KNOWN LIMIT
    "Newest appended" stands in for "moved since the last brief": nothing records
    what the last brief showed, so a stale row can hold a slot. `_ranked` is the
    one place a shown-lines record would plug in. A heavier day than 7 Oct
    relies on the shrink loop, which costs rows - watch, then no-notes, then
    gave-up go to count lines first. Gave-up titles the reply cannot hold wait
    for a later morning; its floor of `CAPS["gave-up"]` rows a day is what
    stops a run of heavy days from holding them forever.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from daydag import recipes
from daydag.brief import (
    NOT_SHOWN,
    PILE_WIDTH,
    UNSOURCED,
    Brief,
    MeetingRow,
    Section,
    _local,
    clip_line,
    day_link,
    packed_meeting_rows,
    split_citation,
)

__all__ = [
    "CAPS",
    "ITEM_CAP",
    "PUSH_BUDGET",
    "SHRINK_ORDER",
    "SLACK_TEXT_LIMIT",
    "STATE_PATH",
    "THREAD_MARK",
    "Cut",
    "Fitted",
    "dangling",
    "fit_morning",
    "split_parts",
]

#: What the DM may hold, as Python ``len`` of the exact markdown posted, links
#: included. The connector states 5,000 per text element and rewrites a few
#: glyphs on the way (each ⚠ and 🔴 grows); 4,500 leaves room for that.
PUSH_BUDGET = 4_500
SLACK_TEXT_LIMIT = 5_000

#: What a shown line may say before its citation, and how many lines each
#: section shows before it counts the rest. Meetings are not capped up front:
#: the packed form names every meeting in about ten lines, and it shrinks last.
ITEM_CAP = 120
CAPS: Mapping[str, int] = {
    "note": 3,
    "owed": 3,
    "overnight": 4,
    "shipping": 3,
    "watch": 2,
    "gaps": 3,
    "gave-up": 3,
}

#: Who gives up a line first when the DM is still over. Watch first - it never
#: made a hand-written brief - and meetings last, because the day is the brief.
#: Before a meeting row goes, a pile-up stops naming every meeting in it
#: ("pile": 1 names them all, 0 names them up to `brief.PILE_WIDTH`).
SHRINK_ORDER = (
    "watch",
    "gaps",
    "gave-up",
    "shipping",
    "owed",
    "note",
    "overnight",
    "pile",
    "meetings",
)

#: The line between the DM and its one reply. Not a bullet, so no guardrail reads it.
THREAD_MARK = "--- thread ---"
_SEPARATOR = f"\n\n{THREAD_MARK}\n\n"

STATE_PATH = "DayDAG/State.md"

#: The State.md refusal, held to this in a fitted DM (whole when the brief fits),
#: by cutting its middle: the start says what failed, the end how to fix it.
LATE_CAP = 200

#: Sections whose overflow goes to the thread, in the order the reply shows them,
#: with the heading each takes there.
_THREADED = {"overnight": "overnight", "shipping": "shipping", "gave-up": "gave up on notes"}

#: When the reply itself is over, who gives up a row, and down to how many.
#: Overnight first: its mentions and notes are still in Slack and Gmail. Then
#: gave-up down to its DM cap - a dropped title is named on a later morning, and
#: the floor keeps a run of heavy days still naming some. Then shipping: once
#: the cursors move, nothing re-reports a landing. Gave-up's floor goes last.
_THREAD_DROP = (("overnight", 0), ("gave-up", CAPS["gave-up"]), ("shipping", 0), ("gave-up", 0))

#: How the DM says a section's rest is in the thread - what `dangling` reads.
_PROMISE = {
    "overnight": f" overnight, {NOT_SHOWN} - in the thread",
    "shipping": f" shipping, {NOT_SHOWN} - in the thread",
    "gave-up": f" days, {NOT_SHOWN} - in the thread",
}

#: Where a row the reply could not hold still is, per section.
_STILL_AT = {
    "overnight": "still in slack and gmail",
    "shipping": "the repos' own history still has them",
    "gave-up": "named in a later brief",
}

#: The order the fitted DM reads in. A section the brief did not produce is skipped.
_ORDER = (
    "no-note",
    "meetings",
    "note",
    "owed",
    "overnight",
    "shipping",
    "watch",
    "gaps",
    "gave-up",
)


@dataclass(frozen=True)
class Cut:
    """One section's accounting: how many rows, how many shown, where the rest are."""

    section: str
    total: int
    shown: int
    home: str

    @property
    def counted(self) -> int:
        return self.total - self.shown


@dataclass(frozen=True)
class Fitted:
    """The DM, the reply under it (often empty), and what was cut."""

    head: str
    thread: str = ""
    gave_up_shown: frozenset[str] = frozenset()
    cuts: tuple[Cut, ...] = ()
    #: The sections whose DM overflow line says "in the thread".
    promised: tuple[str, ...] = ()
    #: The caps the fit settled on, per section (rows, for meetings); empty
    #: when the morning fit whole.
    caps: Mapping[str, int] = field(default_factory=dict)
    #: Rows the reply itself could not hold, per section - counted there.
    dropped: Mapping[str, int] = field(default_factory=dict)

    def text(self) -> str:
        return f"{self.head}{_SEPARATOR}{self.thread}" if self.thread else self.head

    def summary(self) -> str:
        """``morning push: 4,428 chars, thread 2,713; cut owed 112 -> DayDAG/State.md; ...``

        "fits whole" is a morning returned byte for byte; "fitted, nothing cut"
        one the budget repacked without counting a row away.
        """
        sizes = f"morning push: {len(self.head):,} chars"
        if self.thread:
            sizes += f", thread {len(self.thread):,}"
        if not self.cuts:
            return f"{sizes}; fits whole"
        moved = []
        for cut in self.cuts:
            if not cut.counted:
                continue
            gone = self.dropped.get(cut.section, 0)
            where = f"{cut.counted - gone} -> {cut.home}"
            if gone:
                where += f", {gone} -> {_STILL_AT[cut.section]}"
            moved.append(f"{cut.section} {where}")
        return f"{sizes}; cut {'; '.join(moved)}" if moved else f"{sizes}; fitted, nothing cut"


def split_parts(text: str) -> tuple[str, str]:
    """The DM and the reply, from `Fitted.text` - its inverse, for the agent and tests."""
    if _SEPARATOR in text:
        head, thread = text.split(_SEPARATOR, 1)
        return head, thread
    return text, ""


def dangling(fitted: Fitted) -> list[str]:
    """Every section the DM's own text says is in the thread whose heading the
    thread lacks. Read from the DM, not from what was routed, so a promise
    made without routing its rows is caught; a heading counts only at the start
    of a line, so a row that happens to quote it does not."""
    head, thread = fitted.head.splitlines(), fitted.thread.splitlines()
    return [
        key
        for key, words in _PROMISE.items()
        if any(line.endswith(words) for line in head)
        and not any(line.startswith(f"{_THREADED[key]}, the rest") for line in thread)
    ]


# --------------------------------------------------------------------------
# ranking: what a capped section shows first
# --------------------------------------------------------------------------


def _struck(line: str) -> bool:
    body = line[2:] if line.startswith("- ") else line
    while body[:1] in {"⭐", "⚠", " "} and body:
        body = body[1:]
    return body.startswith("~~")


def _unlinked(line: str) -> bool:
    return line.endswith(f"({UNSOURCED})")


def _ranked(lines: Sequence[str]) -> list[str]:
    """Open rows with a link first, newest-appended first; unlinked next; struck last.

    File position is the honest stand-in for "moved": rows are appended as they
    are learned, and nothing records what the last brief showed.
    """
    order = sorted(range(len(lines)), key=lambda i: (_struck(lines[i]), _unlinked(lines[i]), -i))
    return [lines[i] for i in order]


def _clip(line: str) -> str:
    """Shorten what a cited line says; leave an uncited line whole, so an
    admission like "no note found" cannot be clipped off its own line."""
    _, tail = split_citation(line)
    return clip_line(line, ITEM_CAP) if tail else line


def _qualifiers(rows: Sequence[str]) -> str:
    unlinked = sum(1 for row in rows if _unlinked(row))
    struck = sum(1 for row in rows if _struck(row))
    parts = [f"{unlinked} w/ no link yet"] if unlinked else []
    parts += [f"{struck} struck"] if struck else []
    return "".join(f", {part}" for part in parts)


# --------------------------------------------------------------------------
# one composition of the DM at a given set of caps
# --------------------------------------------------------------------------


@dataclass
class _Draft:
    head: str
    #: Rows for the reply, per threaded key, each with the gave-up title it names.
    threaded: dict[str, list[tuple[str, str]]]
    cuts: list[Cut]
    #: Rows shown and rows available, per section - the shrink and re-grow loops read these.
    shown: dict[str, int]
    available: dict[str, int]
    gave_up_shown: frozenset[str]


def _meetings(
    section: Section,
    events: Sequence[Mapping[str, Any]],
    day: Any,
    now: datetime,
    cap: int,
    *,
    name_all: bool,
) -> tuple[Section, Cut, dict[str, int], dict[str, int]]:
    """The packed meetings at ``cap`` rows, and the rows and pile-up names shown
    and available - what the shrink and re-grow loops read.

    A cut row is counted as already over or later today; a meeting a pile-up
    row does not name is counted on that row. Both cite the calendar's day, so
    `Cut.shown` is the meetings the DM names, and every other is counted.
    """
    whole = packed_meeting_rows(events, day)
    packed = packed_meeting_rows(events, day, pile_width=PILE_WIDTH)
    clips = {
        i
        for i, (named, short) in enumerate(zip(whole, packed, strict=True))
        if short.named < named.named
    }
    rows = whole if name_all else packed
    link = day_link(day)

    def over(row: MeetingRow) -> bool:
        ends = [_local(event.get("end")) for event in row.members]
        return bool(ends) and all(end is not None and end <= now for end in ends)

    # already-over rows go first, earliest first; then the latest of the rest
    over_rows = [i for i, row in enumerate(rows) if over(row)]
    later_rows = [i for i in reversed(range(len(rows))) if i not in over_rows]
    removal = over_rows + later_rows
    cut_rows = set(removal[: max(0, len(rows) - cap)])
    kept_rows = [i for i in range(len(rows)) if i not in cut_rows]
    kept = [rows[i].line for i in kept_rows]
    over_count = sum(len(rows[i].members) for i in cut_rows if i in over_rows)
    later_count = sum(len(rows[i].members) for i in cut_rows if i not in over_rows)
    if cut_rows:
        parts = [f"+{over_count} already over"] if over_count else []
        parts += [f"+{later_count} later today"] if later_count else []
        kept.append(f"- {', '.join(parts)} ({link})")
    total = sum(len(row.members) for row in rows)
    named = sum(rows[i].named for i in kept_rows)
    shown = {"meetings": len(kept_rows), "pile": int(name_all and bool(clips & set(kept_rows)))}
    available = {"meetings": len(rows), "pile": int(bool(clips))}
    return (
        Section(section.heading, tuple(kept), key="meetings"),
        Cut("meetings", total, named, link),
        shown,
        available,
    )


def _is_row(line: str) -> bool:
    """A cited bullet of a pulse block. Its sub-headings and its own degrade
    lines ("could not fetch") are not rows: they stay, uncapped and uncounted."""
    return line.startswith("- ") and bool(split_citation(line)[1])


def _capped(
    section: Section,
    *,
    key: str,
    cap: int,
    note_path: str,
    gave_up_titles: Sequence[str],
    gave_up_days: int,
) -> tuple[Section, Cut, list[tuple[str, str]], frozenset[str]]:
    """The section at ``cap`` lines, its overflow line, and the rows for the thread."""
    lines = list(section.lines)
    threaded: list[tuple[str, str]] = []
    titles: frozenset[str] = frozenset()
    if key == "note":
        shown, rest = lines[:cap], lines[cap:]
        lead = f"+{len(rest)} more" if shown else f"{len(rest)}"
        overflow = f"- {lead} 🔴 in the note ({note_path})"
        home = note_path
    elif key in {"owed", "watch"}:
        ranked = _ranked(lines)
        shown, rest = ranked[:cap], ranked[cap:]
        where = "in the chase list" if key == "owed" else "in watch items"
        lead = f"+{len(rest)} more" if shown else f"{len(rest)}"
        overflow = f"- {lead} {where}{_qualifiers(rest)} ({STATE_PATH})"
        home = STATE_PATH
    elif key in {"overnight", "shipping"}:
        rows = [i for i, line in enumerate(lines) if _is_row(line)]
        keep = set(rows[:cap])
        rest = [lines[i] for i in rows[cap:]]
        lead = f"+{len(rest)} more" if keep else f"{len(rest)}"
        body = [
            _clip(line) if i in keep else line
            for i, line in enumerate(lines)
            if i in keep or not _is_row(line)
        ]
        if rest:
            body.append(f"- {lead} {key}, {NOT_SHOWN} - in the thread")
        return (
            Section(section.heading, tuple(body), key=key),
            Cut(key, len(rows), len(keep), "the thread"),
            [(line, "") for line in rest],
            titles,
        )
    elif key == "gaps":
        newest_first = list(reversed(lines))
        shown, rest = newest_first[:cap], newest_first[cap:]
        overflow = (
            f"- +{len(rest)} older w/ no note found - listed again tomorrow"
            if shown
            else f"- {len(rest)} w/ no note found - listed again tomorrow"
        )
        home = "listed again tomorrow"
    elif key == "gave-up":
        shown, rest = lines[:cap], lines[cap:]
        titles = frozenset(gave_up_titles[: len(shown)])
        lead = f"+{len(rest)} more" if shown else f"{len(rest)}"
        overflow = f"- {lead} w/ no note found in {gave_up_days} days, {NOT_SHOWN} - in the thread"
        home = "the thread"
        threaded = list(zip(rest, gave_up_titles[len(shown) :], strict=False))
    else:  # a section the budget does not know is shown whole and never cut
        return section, Cut(key or "other", len(lines), len(lines), ""), [], titles
    kept = [line if key in {"gaps", "gave-up"} else _clip(line) for line in shown]
    if rest:
        kept.append(overflow)
    return (
        Section(section.heading, tuple(kept), key=key),
        Cut(key, len(lines), len(shown), home),
        threaded,
        titles,
    )


def _compose(
    assembled: Brief,
    caps: Mapping[str, int],
    *,
    now: datetime,
    gave_up: Section | None,
    gave_up_titles: Sequence[str],
    gave_up_days: int,
    late: Sequence[str],
) -> _Draft:
    by_key = {section.key: section for section in assembled.sections if section.lines}
    if gave_up is not None and gave_up.lines:
        by_key["gave-up"] = gave_up
    note_path = recipes.weekly_note(assembled.day)
    blocks = [assembled.header]
    threaded: dict[str, list[tuple[str, str]]] = {}
    cuts: list[Cut] = []
    shown: dict[str, int] = {}
    available: dict[str, int] = {}
    gave_up_shown: frozenset[str] = frozenset()
    extra = [s for s in assembled.sections if s.lines and s.key not in _ORDER]
    for key in _ORDER:
        section = by_key.get(key)
        if section is None:
            continue
        if key == "no-note":
            blocks.append(section.render())
            continue
        if key == "meetings" and assembled.events:
            fitted, cut, rows_shown, rows_available = _meetings(
                section,
                assembled.events,
                assembled.day,
                now,
                caps["meetings"],
                name_all=bool(caps["pile"]),
            )
            shown.update(rows_shown)
            available.update(rows_available)
        else:
            fitted, cut, rest, titles = _capped(
                section,
                key=key,
                cap=caps[key],
                note_path=note_path,
                gave_up_titles=gave_up_titles,
                gave_up_days=gave_up_days,
            )
            shown[key], available[key] = cut.shown, cut.total
            if rest:
                threaded[key] = rest
            if key == "gave-up":
                gave_up_shown = titles
        cuts.append(cut)
        blocks.append(fitted.render())
    blocks += [section.render() for section in extra]
    footer = [f"- couldn't check {name}" for name in assembled.unreachable]
    footer += [_middle(line, LATE_CAP) for line in late]
    if footer:
        blocks.append("\n".join(footer))
    return _Draft("\n\n".join(blocks), threaded, cuts, shown, available, gave_up_shown)


def _middle(line: str, cap: int) -> str:
    """``line`` at ``cap`` characters, cut in the middle: what failed, then the fix."""
    if len(line) <= cap:
        return line
    start = (cap - 3) // 2
    return line[:start] + "..." + line[len(line) - (cap - 3 - start) :]


def _thread(
    threaded: Mapping[str, list[tuple[str, str]]], budget: int
) -> tuple[str, frozenset[str], dict[str, int]]:
    """The one reply, fitted; the gave-up titles it names; the rows it dropped.

    Every section the DM promised keeps its heading with the count it really
    shows; rows the reply cannot hold are dropped as `_THREAD_DROP` says and
    counted on that section's own line saying where they still are.
    """
    kept = {key: list(rows) for key, rows in threaded.items() if rows}
    dropped = dict.fromkeys(kept, 0)
    if not kept:
        return "", frozenset(), {}

    def render() -> str:
        parts = []
        for key in _THREADED:
            if key not in kept:
                continue
            rows = [row for row, _ in kept[key]]
            heading = (
                f"{_THREADED[key]}, the rest ({len(rows)})"
                if rows
                else f"{_THREADED[key]}, the rest"
            )
            if dropped[key]:
                rows.append(f"- +{dropped[key]} more, {NOT_SHOWN} - {_STILL_AT[key]}")
            parts.append("\n".join([heading, *rows]))
        return "\n\n".join(parts)

    while len(render()) > budget:
        for key, floor in _THREAD_DROP:
            if len(kept.get(key, ())) > floor:
                kept[key].pop()
                dropped[key] += 1
                break
        else:
            break  # only headings and count lines remain
    named = frozenset(title for _, title in kept.get("gave-up", ()) if title)
    return render(), named, {key: count for key, count in dropped.items() if count}


def fit_morning(
    assembled: Brief,
    *,
    now: datetime,
    gave_up: Section | None = None,
    gave_up_titles: Sequence[str] = (),
    gave_up_days: int = 11,
    late: Sequence[str] = (),
    budget: int = PUSH_BUDGET,
) -> Fitted:
    """The morning as one DM of at most ``budget`` characters, plus at most one reply.

    ``gave_up`` is the runner's "gave up on notes" section and ``gave_up_titles``
    its titles, one per line, in order; ``late`` are lines the runner adds after
    assembly (the State.md refusal), kept whole when the morning fits. See the
    module contracts.

    Raises:
        ValueError: two sections share a key, or ``gave_up_titles`` is not one
            title per gave-up line - either would lose rows without a count.
    """
    keys = [section.key for section in assembled.sections if section.key]
    if len(keys) != len(set(keys)):
        raise ValueError(f"two sections share a key: {sorted(keys)}")
    lines = len(gave_up.lines) if gave_up is not None else 0
    if len(gave_up_titles) != lines:
        # More titles than lines would record a title nobody saw as said.
        raise ValueError(f"{lines} gave-up lines but {len(gave_up_titles)} titles")
    whole = assembled.render()
    if gave_up is not None and gave_up.lines:
        whole += "\n\n" + gave_up.render()
    for line in late:
        whole += "\n\n" + line
    if len(whole) <= budget:
        return Fitted(head=whole, gave_up_shown=frozenset(gave_up_titles))

    caps = dict(CAPS)
    caps["meetings"] = len(packed_meeting_rows(assembled.events, assembled.day))
    caps["pile"] = 1
    ceiling = dict(caps)

    def compose() -> _Draft:
        return _compose(
            assembled,
            caps,
            now=now,
            gave_up=gave_up,
            gave_up_titles=gave_up_titles,
            gave_up_days=gave_up_days,
            late=late,
        )

    draft = compose()
    while len(draft.head) > budget:
        for key in SHRINK_ORDER:
            if draft.shown.get(key, 0) > 0 and caps.get(key, 0) > 0:
                caps[key] = min(caps[key], draft.shown[key]) - 1
                break
        else:
            break  # the floor: nothing left to shrink
        draft = compose()
    # Give back what the shrink took and no longer needs: one row at a time,
    # latest-shrunk first, never past the starting caps, kept only if it fits.
    grew = True
    while grew:
        grew = False
        for key in reversed(SHRINK_ORDER):
            if caps.get(key, 0) < ceiling.get(key, 0) and draft.shown.get(
                key, 0
            ) < draft.available.get(key, 0):
                caps[key] += 1
                trial = compose()
                if len(trial.head) <= budget:
                    draft, grew = trial, True
                else:
                    caps[key] -= 1
    thread, named, dropped = _thread(draft.threaded, budget)
    promised = tuple(key for key in _THREADED if draft.threaded.get(key))
    return Fitted(
        head=draft.head,
        thread=thread,
        gave_up_shown=draft.gave_up_shown | named,
        cuts=tuple(draft.cuts),
        promised=promised,
        caps=dict(caps),
        dropped=dropped,
    )
