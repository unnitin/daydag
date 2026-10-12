"""The morning push, fitted to one Slack message (#185).

USING IT
    fitted = fit_morning(assembled, now=now, gave_up=section, gave_up_titles=titles, late=lines)
    fitted.text()             # the DM; then THREAD_MARK and ONE reply, when one is needed
    fitted.gave_up_shown      # the gave-up titles the DM names - record GAVE_UP for these only
    fitted.summary()          # one line for stderr and the run log
    head, thread = split_parts(fitted.text())
    dangling(fitted)          # [] - every "in the thread" line has its reply

CONTRACTS - break one and the guarantee is gone
    1. A morning whose full text fits `PUSH_BUDGET` comes back BYTE FOR BYTE, every
       meeting's own link and every pairwise overlap flag included. The budget
       only acts on a morning that would not fit.
    2. Otherwise the DM is at most `PUSH_BUDGET` characters, always. Each section
       is ranked and capped, then a deterministic loop shrinks caps in
       `SHRINK_ORDER` until it fits; with every cap at zero only headings, count
       lines and the degrade footer remain, which is bounded by construction.
    3. Nothing is dropped silently. Every row of a fitted section is shown,
       counted on an overflow line that cites where the rest lives (`State.md`,
       the weekly note, the calendar day), counted with a promise that is true
       ("listed again tomorrow", "named in tomorrow's brief"), or copied whole
       into the thread. `Fitted.cuts` holds shown + counted == total per section.
    4. Citations are never clipped. A line is shortened in what it says, never in
       its link, so every line still passes `brief.unsourced_claims`.
    5. Only overnight and shipping overflow goes to the thread - the two sections
       nothing re-raises tomorrow. `brief.NOT_SHOWN` appears only on their
       overflow lines, and `dangling` checks the reply carries them.
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
    one place a shown-lines record would plug in. The 7 Oct margin is about 70
    characters; a heavier day relies on the shrink loop, which costs rows -
    watch, then no-notes, then gave-up go to count lines first.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from daydag import recipes
from daydag.brief import (
    NOT_SHOWN,
    UNSOURCED,
    Brief,
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
SHRINK_ORDER = ("watch", "gaps", "gave-up", "shipping", "owed", "note", "overnight", "meetings")

#: The line between the DM and its one reply. Not a bullet, so no guardrail reads it.
THREAD_MARK = "--- thread ---"
_SEPARATOR = f"\n\n{THREAD_MARK}\n\n"

STATE_PATH = "DayDAG/State.md"

#: Sections whose overflow goes to the thread: nothing re-raises them tomorrow.
_THREADED = ("overnight", "shipping")

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

    def text(self) -> str:
        return f"{self.head}{_SEPARATOR}{self.thread}" if self.thread else self.head

    def summary(self) -> str:
        """``morning push: 4,428 chars, thread 2,713; cut owed 112 -> DayDAG/State.md; ...``"""
        sizes = f"morning push: {len(self.head):,} chars"
        if self.thread:
            sizes += f", thread {len(self.thread):,}"
        moved = [f"{cut.section} {cut.counted} -> {cut.home}" for cut in self.cuts if cut.counted]
        return f"{sizes}; cut {'; '.join(moved)}" if moved else f"{sizes}; fits whole"


def split_parts(text: str) -> tuple[str, str]:
    """The DM and the reply, from `Fitted.text` - its inverse, for the agent and tests."""
    if _SEPARATOR in text:
        head, thread = text.split(_SEPARATOR, 1)
        return head, thread
    return text, ""


def dangling(fitted: Fitted) -> list[str]:
    """Every DM line that says its rows are in the thread when the thread lacks them."""
    missing: list[str] = []
    for line in fitted.head.splitlines():
        for key in _THREADED:
            if f"{key}, {NOT_SHOWN} - in the thread" in line and f"{key}, the rest (" not in (
                fitted.thread
            ):
                missing.append(line)
    return missing


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
    threaded: dict[str, list[str]]
    cuts: list[Cut]
    shown: dict[str, int]
    gave_up_shown: frozenset[str]


def _meetings(
    section: Section, events: Sequence[Mapping[str, Any]], day: Any, now: datetime, cap: int
) -> tuple[Section, Cut, int]:
    rows = packed_meeting_rows(events, day)
    link = day_link(day)

    def over(row: tuple[str, tuple[Mapping[str, Any], ...]]) -> bool:
        ends = [_local(event.get("end")) for event in row[1]]
        return bool(ends) and all(end is not None and end <= now for end in ends)

    # already-over rows go first, earliest first; then the latest of the rest
    over_rows = [i for i, row in enumerate(rows) if over(row)]
    later_rows = [i for i in reversed(range(len(rows))) if i not in over_rows]
    removal = over_rows + later_rows
    cut_rows = set(removal[: max(0, len(rows) - cap)])
    kept = [rows[i][0] for i in range(len(rows)) if i not in cut_rows]
    over_count = sum(len(rows[i][1]) for i in cut_rows if i in over_rows)
    later_count = sum(len(rows[i][1]) for i in cut_rows if i not in over_rows)
    if cut_rows:
        parts = [f"+{over_count} already over"] if over_count else []
        parts += [f"+{later_count} later today"] if later_count else []
        kept.append(f"- {', '.join(parts)} ({link})")
    total = len(events)
    shown = total - over_count - later_count
    return (
        Section(section.heading, tuple(kept), key="meetings"),
        Cut("meetings", total, shown, link),
        len(rows) - len(cut_rows),
    )


def _capped(
    section: Section,
    *,
    key: str,
    cap: int,
    note_path: str,
    gave_up_titles: Sequence[str],
    gave_up_days: int,
) -> tuple[Section, Cut, list[str], frozenset[str]]:
    """The section at ``cap`` lines, its overflow line, and the rows for the thread."""
    lines = list(section.lines)
    threaded: list[str] = []
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
    elif key in _THREADED:
        bullets = [line for line in lines if line.startswith("- ")]
        shown, rest = bullets[:cap], bullets[cap:]
        lead = f"+{len(rest)} more" if shown else f"{len(rest)}"
        overflow = f"- {lead} {key}, {NOT_SHOWN} - in the thread"
        home = "the thread"
        threaded = rest
        # non-bullet lines of a pulse block (its own sub-headings) pass through
        passthrough = [line for line in lines if not line.startswith("- ")]
        shown = passthrough + shown
        lines = bullets
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
        overflow = f"- {lead} w/ no note found in {gave_up_days} days - named in tomorrow's brief"
        home = "named in tomorrow's brief"
    else:  # a section the budget does not know is shown whole and never cut
        return section, Cut(key or "other", len(lines), len(lines), ""), [], titles
    kept = [_clip(line) if key not in {"gaps", "gave-up"} else line for line in shown]
    if rest:
        kept.append(overflow)
    shown_rows = len([line for line in shown if line.startswith("- ")])
    return (
        Section(section.heading, tuple(kept), key=key),
        Cut(key, len(lines), shown_rows, home),
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
    threaded: dict[str, list[str]] = {}
    cuts: list[Cut] = []
    shown: dict[str, int] = {}
    gave_up_shown: frozenset[str] = frozenset()
    extra = [s for s in assembled.sections if s.lines and s.key not in _ORDER]
    for key in _ORDER:
        section = by_key.get(key)
        if section is None:
            continue
        if key == "no-note":
            blocks.append(section.render())
            continue
        if key == "meetings":
            fitted, cut, rows_shown = _meetings(
                section, assembled.events, assembled.day, now, caps["meetings"]
            )
            shown[key] = rows_shown
        else:
            fitted, cut, rest, titles = _capped(
                section,
                key=key,
                cap=caps[key],
                note_path=note_path,
                gave_up_titles=gave_up_titles,
                gave_up_days=gave_up_days,
            )
            shown[key] = cut.shown
            if rest:
                threaded[key] = rest
            if key == "gave-up":
                gave_up_shown = titles
        cuts.append(cut)
        blocks.append(fitted.render())
    blocks += [section.render() for section in extra]
    footer = [f"- couldn't check {name}" for name in assembled.unreachable] + list(late)
    if footer:
        blocks.append("\n".join(footer))
    return _Draft("\n\n".join(blocks), threaded, cuts, shown, gave_up_shown)


def _thread(threaded: Mapping[str, list[str]], budget: int) -> str:
    """The one reply: overnight and shipping rows the DM cut, verbatim, fitted."""
    sections = [
        (f"{key}, the rest ({len(rows)})", list(rows))
        for key in _THREADED
        if (rows := threaded.get(key))
    ]
    if not sections:
        return ""

    def render(parts: list[tuple[str, list[str]]], dropped: int) -> str:
        text = "\n\n".join("\n".join([heading, *rows]) for heading, rows in parts if rows)
        if dropped:
            text += f"\n- +{dropped} more, {NOT_SHOWN} - `render morning --full` prints them"
        return text

    dropped = 0
    while len(render(sections, dropped)) > budget and any(rows for _, rows in sections):
        for _, rows in reversed(sections):
            if rows:
                rows.pop()
                dropped += 1
                break
    return render(sections, dropped)


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
    assembly (the State.md refusal). See the module contracts.
    """
    whole = assembled.render()
    if gave_up is not None and gave_up.lines:
        whole += "\n\n" + gave_up.render()
    for line in late:
        whole += "\n\n" + line
    if len(whole) <= budget:
        return Fitted(head=whole, gave_up_shown=frozenset(gave_up_titles))

    caps = dict(CAPS)
    caps["meetings"] = len(packed_meeting_rows(assembled.events, assembled.day))

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
                caps[key] -= 1
                break
        else:
            break  # the floor: nothing left to shrink
        draft = compose()
    return Fitted(
        head=draft.head,
        thread=_thread(draft.threaded, budget),
        gave_up_shown=draft.gave_up_shown,
        cuts=tuple(draft.cuts),
    )
