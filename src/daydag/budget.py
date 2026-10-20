"""The morning push, fitted to one Slack message (#185).

USING IT
    fitted = fit_morning(assembled, gave_up=section)
    fitted.text()             # the DM; then THREAD_MARK and ONE reply, when one is needed
    fitted.gave_up_said       # how many gave-up lines, from the first, the posts name
    fitted.summary()          # one line for the run log (and stderr, in a terminal)
    head, thread = split_parts(fitted.text())

CONTRACTS - break one and the guarantee is gone
    1. A morning whose full text fits `PUSH_BUDGET` comes back BYTE FOR BYTE, every
       meeting's own link and every pairwise overlap flag included. The budget
       only acts on a morning that would not fit.
    2. Otherwise the DM is at most `PUSH_BUDGET` characters, always. Each section
       is ranked; its cap restarts at zero and fills a row at a time in reverse
       `SHRINK_ORDER`, meetings first and watch last, while the DM still fits;
       with every cap at zero only headings, count lines, the degrade
       footer and the pulse's own notices (one per unreadable repo) remain,
       which is bounded by construction - by the sources and the watchlist.
    3. Nothing is dropped silently. Every row of a fitted section is shown,
       counted on an overflow line that cites where the rest lives (`State.md`,
       the weekly note, the calendar day), counted with a promise that is true
       ("listed again tomorrow"), or carried to the thread. `Fitted.cuts` holds
       shown + counted == total per section.
    4. No list row loses or cuts a link. A row is shortened in what it says,
       never its citation; a row whose words carry a link is shown whole, so
       every row still passes `brief.unsourced_claims`. (A packed meeting TITLE
       is cut at `brief.TITLE_CAP` as a title; its row cites the calendar's day.)
    5. Overflow with no other home - overnight, shipping, and gave-up titles -
       goes to ONE thread reply. Every section the DM says is "in the thread"
       keeps its heading there, with the count it really shows; rows the reply
       itself cannot hold are counted on a line saying where they still are,
       overnight first (still in Slack and Gmail).
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
    relies on the fill, which costs rows - watch, then no-notes, then gave-up
    are the last to get room. A pile-up is one row, so one longer than the
    whole DM (sixty or more meetings at once; the most a real day held was nine)
    is counted with the meetings after it, not named. Gave-up titles the reply
    cannot hold wait for a later morning; its floor of `CAPS["gave-up"]` rows a
    day is what stops a run of heavy days from holding them forever.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from daydag import recipes
from daydag.brief import (
    NOT_SHOWN,
    UNSOURCED,
    Brief,
    Section,
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
    "note": 6,
    "owed": 3,
    "owed-by": 2,
    "overnight": 4,
    "shipping": 3,
    "watch": 2,
    "gaps": 3,
    "gave-up": 3,
}

#: Who gives up a line first when the DM is still over. Watch first - it never
#: made a hand-written brief - and meetings last, because the day is the brief.
SHRINK_ORDER = (
    "watch",
    "gaps",
    "gave-up",
    "shipping",
    "owed-by",
    "owed",
    "note",
    "overnight",
    "meetings",
)

#: The line between the DM and its one reply. Not a bullet, so no guardrail reads it.
THREAD_MARK = "--- thread ---"
_SEPARATOR = f"\n\n{THREAD_MARK}\n\n"

STATE_PATH = "DayDAG/State.md"

#: Sections whose overflow goes to the thread, in the order the reply shows them,
#: with the heading each takes there.
_THREADED = {"overnight": "overnight", "shipping": "shipping", "gave-up": "gave up on notes"}

#: When the reply itself is over, who gives up a row, and down to how many.
#: Overnight first: its mentions and notes are still in Slack and Gmail. Then
#: gave-up down to its DM cap - a dropped title is named on a later morning, and
#: the floor keeps a run of heavy days still naming some. Then shipping: once
#: the cursors move, nothing re-reports a landing. Gave-up's floor goes last.
_THREAD_DROP = (("overnight", 0), ("gave-up", CAPS["gave-up"]), ("shipping", 0), ("gave-up", 0))

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
    "owed-by",
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
    #: How many gave-up lines, from the first, the DM or its reply names:
    #: record only those as said.
    gave_up_said: int = 0
    cuts: tuple[Cut, ...] = ()
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


# --------------------------------------------------------------------------
# ranking: what a capped section shows first
# --------------------------------------------------------------------------


def _unlinked(line: str) -> bool:
    return line.endswith(f"({UNSOURCED})")


def _ranked(lines: Sequence[str]) -> list[int]:
    """Rows with a link first, newest-appended first; unlinked after. A struck
    row never gets here: the brief leaves it out (#188).

    File position is the honest stand-in for "moved": rows are appended as they
    are learned, and nothing records what the last brief showed.
    """
    return sorted(range(len(lines)), key=lambda i: (_unlinked(lines[i]), -i))


def _clip(line: str) -> str:
    """Shorten what a cited line says; leave an uncited line whole, so an
    admission like "no note found" cannot be clipped off its own line."""
    _, tail = split_citation(line)
    return clip_line(line, ITEM_CAP) if tail else line


def _qualifiers(rows: Sequence[str]) -> str:
    unlinked = sum(1 for row in rows if _unlinked(row))
    return f", {unlinked} w/ no link yet" if unlinked else ""


# --------------------------------------------------------------------------
# one composition of the DM at a given set of caps
# --------------------------------------------------------------------------


@dataclass
class _Draft:
    head: str
    #: Rows for the reply, per threaded key.
    threaded: dict[str, list[str]]
    cuts: list[Cut]


def _meetings(
    section: Section, events: Sequence[Mapping[str, Any]], day: Any, cap: int
) -> tuple[Section, Cut]:
    """The first ``cap`` packed meeting rows. The rest are counted on one line
    citing the calendar's day, so `Cut.shown` is the meetings the DM names."""
    rows = packed_meeting_rows(events, day)
    link = day_link(day)
    kept = [row.line for row in rows[:cap]]
    rest = sum(len(row.members) for row in rows[cap:])
    if rest:
        kept.append(f"- +{rest} more today ({link})")
    total = sum(len(row.members) for row in rows)
    return Section(section.heading, tuple(kept), key="meetings"), Cut(
        "meetings", total, total - rest, link
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
    gave_up_days: int,
) -> tuple[Section, Cut, list[str]]:
    """The section at ``cap`` rows, its overflow line, and the rows for the thread.

    A pulse block's sub-headings and degrade lines are not rows: they keep
    their place, uncapped and uncounted.
    """
    lines = list(section.lines)
    if key not in CAPS:  # a section the budget does not know is shown whole and never cut
        return section, Cut(key or "other", len(lines), len(lines), ""), []
    pulse = key in {"overnight", "shipping"}
    rows = [i for i, line in enumerate(lines) if not pulse or _is_row(line)]
    if key in {"owed", "owed-by", "watch"}:
        rows = _ranked(lines)
    elif key == "gaps":
        rows.reverse()  # newest first
    shown, rest = set(rows[:cap]), [lines[i] for i in rows[cap:]]
    kept = sorted(set(range(len(lines))) - set(rows[cap:])) if pulse else rows[:cap]
    body = [_clip(lines[i]) if i in shown else lines[i] for i in kept]
    lead = f"+{len(rest)} more" if shown else f"{len(rest)}"
    overflow, home = {
        "note": (f"{lead} 🔴 in the note ({note_path})", note_path),
        "owed": (f"{lead} in the chase list{_qualifiers(rest)} ({STATE_PATH})", STATE_PATH),
        "owed-by": (f"{lead} owed by you{_qualifiers(rest)} ({STATE_PATH})", STATE_PATH),
        "watch": (f"{lead} in watch items{_qualifiers(rest)} ({STATE_PATH})", STATE_PATH),
        "overnight": (f"{lead} overnight, {NOT_SHOWN} - in the thread", "the thread"),
        "shipping": (f"{lead} shipping, {NOT_SHOWN} - in the thread", "the thread"),
        "gaps": (
            f"{f'+{len(rest)} older' if shown else len(rest)} w/ no note found"
            " - listed again tomorrow",
            "listed again tomorrow",
        ),
        "gave-up": (
            f"{lead} w/ no note found in {gave_up_days} days, {NOT_SHOWN} - in the thread",
            "the thread",
        ),
    }[key]
    if rest:
        body.append(f"- {overflow}")
    return (
        Section(section.heading, tuple(body), key=key),
        Cut(key, len(rows), len(shown), home),
        rest if key in _THREADED else [],
    )


def _compose(
    assembled: Brief,
    caps: Mapping[str, int],
    *,
    gave_up: Section | None,
    gave_up_days: int,
) -> _Draft:
    by_key = {section.key: section for section in assembled.sections if section.lines}
    if gave_up is not None and gave_up.lines:
        by_key["gave-up"] = gave_up
    note_path = recipes.weekly_note(assembled.day)
    blocks = [assembled.header]
    threaded: dict[str, list[str]] = {}
    cuts: list[Cut] = []
    extra = [s for s in assembled.sections if s.lines and s.key not in _ORDER]
    for key in _ORDER:
        section = by_key.get(key)
        if section is None:
            continue
        if key == "no-note":
            blocks.append(section.render())
            continue
        if key == "meetings" and assembled.events:
            fitted, cut = _meetings(section, assembled.events, assembled.day, caps["meetings"])
        else:
            fitted, cut, rest = _capped(
                section,
                key=key,
                cap=caps[key],
                note_path=note_path,
                gave_up_days=gave_up_days,
            )
            if rest:
                threaded[key] = rest
        cuts.append(cut)
        blocks.append(fitted.render())
    blocks += [section.render() for section in extra]
    footer = [f"- couldn't check {name}" for name in assembled.unreachable]
    if footer:
        blocks.append("\n".join(footer))
    return _Draft("\n\n".join(blocks), threaded, cuts)


def _thread(threaded: Mapping[str, list[str]], budget: int) -> tuple[str, int, dict[str, int]]:
    """The one reply, fitted; how many gave-up rows it names; the rows it dropped.

    Every section the DM promised keeps its heading with the count it really
    shows; rows the reply cannot hold are dropped as `_THREAD_DROP` says and
    counted on that section's own line saying where they still are.
    """
    kept = {key: list(rows) for key, rows in threaded.items() if rows}
    dropped = dict.fromkeys(kept, 0)
    if not kept:
        return "", 0, {}

    def render() -> str:
        parts = []
        for key in _THREADED:
            if key not in kept:
                continue
            rows = list(kept[key])
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
    said = len(kept.get("gave-up", ()))
    return render(), said, {key: count for key, count in dropped.items() if count}


def fit_morning(
    assembled: Brief,
    *,
    gave_up: Section | None = None,
    gave_up_days: int = 11,
    budget: int = PUSH_BUDGET,
) -> Fitted:
    """The morning as one DM of at most ``budget`` characters, plus at most one reply.

    ``gave_up`` is the runner's "gave up on notes" section, one title per line;
    the posts name a prefix of its lines, and `Fitted.gave_up_said` says how
    long. See the module contracts.
    """
    whole = assembled.render()
    if gave_up is not None and gave_up.lines:
        whole += "\n\n" + gave_up.render()
    if len(whole) <= budget:
        return Fitted(head=whole, gave_up_said=len(gave_up.lines) if gave_up else 0)

    caps = dict(CAPS)
    caps["meetings"] = len(packed_meeting_rows(assembled.events, assembled.day))
    ceiling = dict(caps)

    def compose() -> _Draft:
        return _compose(
            assembled,
            caps,
            gave_up=gave_up,
            gave_up_days=gave_up_days,
        )

    draft = compose()
    if len(draft.head) > budget:
        # From the floor, give each section rows in priority order - meetings
        # first, watch last - one at a time, never past its starting cap, until
        # its next row does not fit. Later sections only add text, so a row
        # that did not fit never fits after: every row that fits is shown.
        caps = dict.fromkeys(ceiling, 0)
        draft = compose()
        for key in reversed(SHRINK_ORDER):
            while caps[key] < ceiling[key]:
                caps[key] += 1
                trial = compose()
                if len(trial.head) > budget:
                    caps[key] -= 1
                    break
                draft = trial
    thread, named, dropped = _thread(draft.threaded, budget)
    shown = next((cut.shown for cut in draft.cuts if cut.section == "gave-up"), 0)
    return Fitted(
        head=draft.head,
        thread=thread,
        gave_up_said=shown + named,
        cuts=tuple(draft.cuts),
        caps=dict(caps),
        dropped=dropped,
    )
