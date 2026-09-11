"""The EOD wrap (SPEC 3.5) - the morning brief's sibling, a different question.

USING IT
    class Connectors:
        def calendar(self, window):
            return []            # events in one local day

        def vault_note(self, path):
            raise FileNotFoundError(path)   # nobody has written it yet

    wrap = assemble(now=now, sources=Connectors(), ledger=ledger, pulse=pulse)
    wrap.render()
    unsourced_claims(wrap.render())   # from daydag.brief - must be empty

CONTRACTS
    1. Silence is information (SPEC 3.7 rule 3). An empty section - nothing
       closed, nothing moved, no meeting tomorrow - is OMITTED, not labelled.
    2. Evidence or silence (guardrail 3). Every claim carries a permalink or a
       vault path, or admits it could not be sourced - checked with
       `daydag.brief.unsourced_claims`, the same scanner the brief is checked
       with, because the rule is not brief-specific.
    3. Degrade, never stall (guardrail 6). A source that raises costs one
       "couldn't check X" line; the wrap still ships regardless.
    4. A naive `now` is REFUSED with `WrapError`, never read against the
       host's zone - `astimezone()` with no argument adopts the runner's
       timezone, which is the bug `daydag.brief.assemble` was fixed for and
       this must not reintroduce.
    5. Friday never OFFERS to run `weekly-planning`. On that one day it states,
       as a fact either way and never a question, whether the two files that
       routine writes for the coming week exist yet - because the routine
       already ran at 1pm and asking again at 4:30pm would be stale, not
       helpful (SPEC 3.5). This holds on every day, by construction, rather
       than by a Friday-only suppression: the offer is simply never built.
    6. Reads exactly two sources of its own - `Sources.calendar` for tomorrow,
       `Sources.vault_note` for a vault path. Everything else it reports comes
       from a `Ledger` or `Pulse` the caller already built and owns; this
       module never queries either directly.

WHY IT EXISTS
    It owns no source and no query, same as `daydag.brief`. Tomorrow's window
    and every vault path come from `daydag.recipes`; the notes-gap cross
    reference from `daydag.ledger`; the moved block from `daydag.pulse`; the
    header template from `daydag.voice`; and the degrade path, the citation
    renderer, the missing-vs-downed note split and the push shape all come
    from `daydag.brief` itself - `Reader`, `claim`, `read_vault_note`,
    `render_push` - factored out there rather than copied here. What is left
    is ASSEMBLY, same as the brief: a header plus three sections - what closed
    today (`daydag.brief.closed_red_items`, the ticked side of the same note
    the brief reads for its open items), what moved (the pulse's own block,
    reused verbatim), and tomorrow's first meeting plus any prep gap (the same
    ordering as `daydag.brief.first_meeting_line`, cross-referenced against
    the ledger's notes gaps).

KNOWN LIMIT
    Friday's addition is narrower than SPEC 3.5's full description of the
    outcome report. It states whether the two `weekly-planning` files for the
    coming week exist yet, each cited by its own path - and stops there. The
    Workstreams changelog and outstanding Gmail-draft approvals SPEC also asks
    for are NOT built: there is no Workstreams parser and no gmail-draft-status
    source anywhere in this codebase yet, and inventing either would spend
    guardrail 3 on a claim nothing actually sourced.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from daydag import recipes
from daydag.brief import (
    WARN,
    Reader,
    Section,
    claim,
    closed_red_items,
    first_meeting_line,
    read_vault_note,
    render_push,
)
from daydag.ledger import Ledger
from daydag.pulse import Pulse
from daydag.voice import Push, render

__all__ = [
    "Sources",
    "Wrap",
    "WrapError",
    "assemble",
]

#: Friday, 0-indexed from Monday - `date.weekday()`'s own convention.
_FRIDAY = 4

#: How far ahead the wrap looks for the planning outcome: the week that
#: `weekly-planning` writes on Fridays is the one starting the next Monday.
_NEXT_WEEK = timedelta(days=7)


class WrapError(RuntimeError):
    """The wrap was asked for something it cannot honestly produce.

    Raised only for a caller mistake - a naive clock - never for a source that
    failed. A failed source degrades to a line; a wrong parameter would
    silently produce a wrap that reads fine and is not true. Mirrors
    :class:`daydag.brief.BriefError` on purpose, so a caller that already
    catches one knows to catch the other.
    """


class Sources(Protocol):
    """The two reads the wrap performs. Every one may raise.

    Narrower than :class:`daydag.brief.Sources` on purpose: the wrap asks a
    different question and does not need Slack or Gmail to answer it. Taking
    the *query* rather than the parameters behind it is the same discipline
    as the brief's own protocol - the queries come from :mod:`daydag.recipes`.
    """

    def calendar(self, window: recipes.DayWindow) -> Iterable[Mapping[str, Any]]:
        """Events in one local day."""

    def vault_note(self, path: str) -> str:
        """A vault note's text. ``FileNotFoundError`` means nobody wrote it.

        One method for every vault-relative path the wrap reads - today's
        weekly note, and on a Friday the coming week's plan and meeting prep
        file. All three are "read whatever is at this path", not three
        different kinds of read.
        """


@dataclass(frozen=True)
class Wrap:
    """One evening's assembled wrap."""

    day: date
    header: str
    sections: tuple[Section, ...]
    #: Display names of sources that could not be read this run.
    unreachable: tuple[str, ...] = ()

    def render(self) -> str:
        return render_push(self.header, self.sections, self.unreachable)


def assemble(
    *,
    now: datetime,
    sources: Sources,
    ledger: Ledger | None = None,
    pulse: Pulse | None = None,
) -> Wrap:
    """Build the EOD wrap for ``now``'s local day.

    ``ledger`` and ``pulse`` are optional because they are *state the caller
    owns*, not sources: a run with no pulse has no moved block, and that is
    silence rather than a failure - the same contract as
    :func:`daydag.brief.assemble`.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise WrapError(
            "now must be timezone-aware: today's close and tomorrow's first "
            "meeting are both local wall-clock questions, and a naive clock "
            "is silently hours wrong on a UTC runner"
        )
    day = now.astimezone(recipes.PACIFIC).date()
    tomorrow = day + timedelta(days=1)
    read = Reader()
    sections: list[Section] = []

    # -- what closed today: the weekly note's own ticked red items --------
    note_path = recipes.weekly_note(day)
    note, missing_note = read_vault_note(
        read, lambda: sources.vault_note(note_path), label="the weekly note"
    )
    if missing_note:
        sections.append(
            Section(
                f"{WARN} no weekly note for {recipes.week_label(day)}",
                (claim("can't say what closed today without the week's priorities", note_path),),
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

    # -- what moved: the pulse's own block, reused verbatim ----------------
    moved_lines: list[str] = []
    if pulse is not None:
        block = read("the pulse", pulse.render, "")
        if block.strip():
            moved_lines = block.splitlines()
            # Counted from `items()`, not from the rendered lines. That block
            # also carries stale-mirror, unavailable-repo and unparsed-watchlist
            # notices, so a day where nothing shipped and two sources degraded
            # announced "moved (2)" with both lines being failure notices. The
            # lines all still ship - degrading loudly is the point - but a
            # failure to read is not a thing that moved.
            count = len(read("the pulse", pulse.items, []))
            sections.append(Section(f"moved ({count})", tuple(moved_lines)))

    # -- tomorrow's first meeting, plus any prep gap -----------------------
    window = recipes.calendar_day(tomorrow)
    # `list` inside the lambda, not outside it - see brief.assemble for why:
    # a paginated adapter is a generator that raises on iteration, and
    # materialised outside the guard that failure walks past the degrade path.
    events = read("calendar", lambda: list(sources.calendar(window)), [])
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

    # -- Friday only: the planning outcome, never an offer to run it -------
    if day.weekday() == _FRIDAY:
        sections += _friday_outcome(read, sources, day)

    header = render(
        Push.EOD_WRAP,
        {
            "closed": "?" if "the weekly note" in read.unreachable else len(closed),
            "moved": "?" if "the pulse" in read.unreachable else len(moved_lines),
        },
    )
    return Wrap(
        day=day,
        header=header,
        sections=tuple(sections),
        unreachable=tuple(read.unreachable),
    )


def _friday_outcome(read: Reader, sources: Sources, day: date) -> list[Section]:
    """Whether the two files `weekly-planning` writes on Fridays landed.

    A statement of fact either way, never a question: SPEC 3.5 is explicit
    that the wrap does not offer to run `weekly-planning` again at 4:30pm, and
    a line ending in "?" here would read as exactly that offer. A source that
    could not be read asserts neither "landed" nor "not yet" - it degrades
    like any other, via `read.unreachable`, and says nothing here at all.
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
