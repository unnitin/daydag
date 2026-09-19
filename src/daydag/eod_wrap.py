"""The EOD wrap (SPEC 3.5) - the morning brief's sibling, a different question.

USING IT
    class Connectors:
        def calendar(self, window):
            return []            # events in one local day

        def weekly_note(self, path):
            return "# 0914-0918"            # THIS week's plan of record

        def vault_note(self, path):
            raise FileNotFoundError(path)   # any other path; nobody wrote it

    wrap = assemble(now=now, sources=Connectors(), ledger=ledger, pulse=pulse)
    wrap.render()
    unsourced_claims(wrap.render())   # from daydag.push - must be empty

CONTRACTS
    1. Silence is information (SPEC 3.7 rule 3). An empty section - nothing
       closed, nothing moved, no meeting tomorrow - is OMITTED, not labelled.
    2. Evidence or silence (guardrail 3), checked with
       `daydag.push.unsourced_claims` - the same scanner the brief is checked
       with, because the rule is not brief-specific.
    3. Degrade, never stall (guardrail 6). A source that raises costs one
       "couldn't check X" line; the wrap still ships regardless.
    4. A naive `now` is REFUSED with `PushError` (`push.aware`), never read
       against the host's zone - `astimezone()` with no argument adopts the
       runner's timezone, which is the bug the brief was fixed for and this
       must not reintroduce.
    5. Friday never OFFERS to run `weekly-planning`. On that one day it states,
       as a fact either way and never a question, whether the two files that
       routine writes for the coming week exist yet - because the routine
       already ran at 1pm and asking again at 4:30pm would be stale, not
       helpful (SPEC 3.5). This holds on every day, by construction, rather
       than by a Friday-only suppression: the offer is simply never built.
    6. Reads exactly three of its own - `Sources.calendar` for tomorrow,
       `Sources.weekly_note` for THIS week's plan of record, and
       `Sources.vault_note` for any other vault path. The last two are a real
       split, not a redundancy: `run.plan` emits this week's note under `vault`
       and every other note under `vault_notes`. Reading the wrong one of the
       two had the wrap reporting "no weekly note" over a 17,703-character file
       that was sitting in the payload (#131). Everything else it reports comes
       from a `Ledger` or `Pulse` the caller already built and owns; this module
       never queries either directly.

WHY IT EXISTS
    It owns no source and no query, same as `daydag.brief`. Tomorrow's window
    and every vault path come from `daydag.recipes`; the notes-gap cross
    reference from `daydag.ledger`; the moved block from `daydag.pulse`; the
    header template from `daydag.voice`; and everything it renders with -
    the push shape, the degrade path, the citation renderer, the
    missing-vs-downed note split, the weekly-note scan - from `daydag.push`,
    where the brief and the week-ahead take theirs from too.

    What is left is ASSEMBLY, same as the brief: a header plus three sections -
    what closed today (the ticked side of the same note the brief reads for its
    open items), what moved (the pulse's own block, reused verbatim), and
    tomorrow's first meeting plus any prep gap, cross-referenced against the
    ledger's notes gaps.

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

from datetime import date, timedelta
from typing import TYPE_CHECKING

from daydag import recipes, voice
from daydag.push import (
    Push,
    Reader,
    Section,
    Sources,
    aware,
    claim,
    closed_red_items,
    first_meeting_line,
    missing_note_section,
    read_vault_note,
    shipping_lines,
)

if TYPE_CHECKING:
    from datetime import datetime

    from daydag.ledger import Ledger
    from daydag.pulse import Pulse

__all__ = ["assemble"]

#: Friday, 0-indexed from Monday - `date.weekday()`'s own convention.
_FRIDAY = 4

#: How far ahead the wrap looks for the planning outcome: the week that
#: `weekly-planning` writes on Fridays is the one starting the next Monday.
_NEXT_WEEK = timedelta(days=7)


def assemble(
    *,
    now: datetime,
    sources: Sources,
    ledger: Ledger | None = None,
    pulse: Pulse | None = None,
) -> Push:
    """Build the EOD wrap for ``now``'s local day.

    ``ledger`` and ``pulse`` are optional because they are *state the caller
    owns*, not sources: a run with no pulse has no moved block, and that is
    silence rather than a failure - the same contract
    :func:`daydag.brief.assemble` states.
    """
    aware(now, "today's close and tomorrow's first meeting are local wall-clock questions")
    day = now.astimezone(recipes.PACIFIC).date()
    read = Reader()
    sections: list[Section] = []

    # -- what closed today: the weekly note's own ticked red items --------
    note_path = recipes.weekly_note(day)
    note, missing_note = read_vault_note(
        read, lambda: sources.weekly_note(note_path), label="the weekly note"
    )
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

    # -- what moved: the pulse's own block, reused verbatim ----------------
    moved_lines = shipping_lines(read, pulse)
    if moved_lines and pulse is not None:
        # Counted from `items()`, not from the rendered lines: the block also
        # carries stale-mirror and unparsed-watchlist notices, and a failure to
        # read is not a thing that moved
        # (test_the_moved_count_counts_movement_not_failure_notices).
        count = len(read("the pulse", pulse.items, []))
        sections.append(Section(f"moved ({count})", tuple(moved_lines)))

    # -- tomorrow's first meeting, plus any prep gap -----------------------
    (window,) = recipes.loop_windows("eod", day)
    # `list` inside the lambda, not outside it - see `brief.assemble` for why.
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

    header = voice.render(
        voice.Push.EOD_WRAP,
        {
            "closed": "?" if "the weekly note" in read.unreachable else len(closed),
            "moved": "?" if "the pulse" in read.unreachable else len(moved_lines),
        },
    )
    return Push(
        day=day,
        header=header,
        sections=tuple(sections),
        unreachable=tuple(read.unreachable),
    )


def _friday_outcome(read: Reader, sources: Sources, day: date) -> list[Section]:
    """Whether the two files `weekly-planning` writes on Fridays landed.

    A statement of fact either way, never a question (contract 5): a line
    ending in "?" here would read as the offer SPEC 3.5 rules out. A file that
    could not be read asserts neither "landed" nor "not yet" - it degrades via
    `read.unreachable` and says nothing here at all.
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
