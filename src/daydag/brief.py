"""The morning brief (SPEC 3.1) - the loop every other loop composes from.

USING IT
    sources = YourSources()     # `Sources` is a Protocol - implement
                                #   calendar/weekly_note/slack/gmail.
                                #   It is not instantiable itself.
    brief = assemble(now=..., identities=ids, sources=sources, ...)
    brief.render()
    unsourced_claims(text)      # -> claims with no permalink; must be empty
    red_items(note)             # -> [(level, text)] under a red heading

CONTRACTS
    1. Silence is information (SPEC 3.7 rule 3). An empty section is OMITTED,
       not labelled. "no updates" trains the reader to skim, and a brief people
       skim stops being read.
    2. Evidence or silence (guardrail 3). Every claim renders with a permalink
       or a file path, or says out loud that it could not be sourced.
       `unsourced_claims` makes that mechanical rather than advisory.
    3. Degrade, never stall (guardrail 6). A source that raises costs one
       "couldn't check X" line and the brief still ships.
    4. Sources are INJECTED, as an object of four methods - not as data. The
       brief has to observe HOW it asked (one calendar day at a time, an
       id-scoped Slack query), because those are the two failures that come
       back as a plausible empty result rather than an error. A client built in
       here would be a source nobody can make fail on purpose.
    5. The weekly note may NOT EXIST. CLAUDE.md records a gap in the series, so
       the first real run will meet one. A missing note leads the brief.
    6. Two answers to a naive datetime, on purpose. A naive EVENT START is
       read as Pacific (`_local`) - the connector hands back wall-clock time
       and the alternative is worse. A naive ``now`` is REFUSED (`PushError`)
       - the overnight cutoff is an hour of his day, and guessing which day is
       not a thing to do quietly.

       Never the host zone either way: `astimezone()` with no argument
       resolves to the machine's timezone, which passed locally and failed on
       UTC CI. Siblings differ by provenance and are right to - `pulse._as_of`
       reads a naive FETCH stamp as UTC, because a machine wrote it.

WHY IT EXISTS
    It owns no source and no query. Windows, cutoffs and vault paths come from
    `recipes`; notes gaps from `ledger`; shipping from `pulse`; chase and watch
    from `DayDAG/State.md`; the register from `voice`.

    What is left is ASSEMBLY - and assembly is where this project's defects
    have actually lived, because every module was right on its own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from daydag import recipes, voice
from daydag.config import resolve_reference
from daydag.ledger import Ledger, title_from_gemini_subject
from daydag.pulse import Pulse
from daydag.push import (
    Push,
    PushError,
    Reader,
    Section,
    Sources,
    aware,
    claim,
    day_label,
    link,
    meeting_lines,
    missing_note_section,
    read_vault_note,
    red_items,
    seed_ledger,
    shipping_lines,
    state_lines,
)
from daydag.statedoc import StateFolder

__all__ = ["assemble"]


def _principal(identities: Mapping[str, str]) -> str:
    """The one Slack id the brief needs, resolved or refused.

    Refused rather than passed through: ``${SLACK_USER_PRINCIPAL}`` as literal
    text is a syntactically valid query that matches nothing, and a brief built
    on it reports a quiet night it never actually looked at.
    """
    return resolve_reference(
        "${SLACK_USER_PRINCIPAL}",
        identities,
        what="the principal's Slack id",
        error=PushError,
    )


def assemble(
    *,
    now: datetime,
    sources: Sources,
    identities: Mapping[str, str],
    state: StateFolder | None = None,
    ledger: Ledger | None = None,
    pulse: Pulse | None = None,
) -> Push:
    """Build the morning brief for ``now``'s local day.

    ``state``, ``ledger`` and ``pulse`` are optional because they are *state the
    caller owns*, not sources: a run without a pulse has no shipping block, and
    that is silence rather than a failure. A source that raises is the other
    case, and it lands in :attr:`Brief.unreachable`.
    """
    aware(now, "the 6pm overnight cutoff is a local wall-clock time")
    principal = _principal(identities)
    day = now.astimezone(recipes.PACIFIC).date()
    read = Reader()
    sections: list[Section] = []

    # -- calendar, one request per day ------------------------------------
    events: list[Mapping[str, Any]] = []
    for window in recipes.loop_windows("morning", day):
        # `list` inside the lambda, not outside it: the protocol returns an
        # Iterable, and a paginated adapter is naturally a generator that raises
        # on iteration rather than on the call. Materialised outside, that
        # failure walks straight past the degrade path.
        events += read("calendar", lambda w=window: list(sources.calendar(w)), [])

    # -- the weekly note, whatever layout it happens to use ---------------
    note_path = recipes.weekly_note(day)
    note, missing_note = read_vault_note(
        read, lambda: sources.weekly_note(note_path), label="the weekly note"
    )

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

    # -- chase and watch, read out of the file he corrects by hand --------
    chase, watch = state_lines(read, state)
    if chase:
        sections.append(Section(f"owed to you ({len(chase)})", tuple(chase)))

    # -- the overnight delta ----------------------------------------------
    overnight = _overnight_lines(now, principal, sources, read)
    if overnight:
        sections.append(Section(f"overnight ({len(overnight)})", tuple(overnight)))

    # -- shipping, only when it has something to say ----------------------
    shipping = shipping_lines(read, pulse)
    if shipping:
        sections.append(Section("shipping", tuple(shipping)))

    if watch:
        sections.append(Section("watch", tuple(watch)))

    # -- notes gaps: calendar drives, notes attach ------------------------
    if ledger is not None:
        # Degraded like a source, because it consumes one: `seed_day` indexes
        # `id`, `start`, `end` and `summary` straight off a calendar record, and
        # `notes_gaps` compares `end` to an aware `now`. A connector that omits
        # a key or hands back a naive datetime would otherwise take the whole
        # brief down over the one section that reports an absence.
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
            # calendar are the same number and not the same fact, and the header
            # is not a `- ` line, so `unsourced_claims` never sees it assert one.
            "count": "?" if "calendar" in read.unreachable else len(events),
        },
    )
    return Push(
        day=day,
        header=header,
        sections=tuple(sections),
        unreachable=tuple(read.unreachable),
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
    """Whether a record landed inside the overnight window.

    Reads both bounds from the window they were computed with, so the filter
    and the definition cannot drift apart. Bounded at BOTH ends: the upper is not
    pedantry: ``now`` is a parameter, so a backfilled or replayed run has a
    ``now`` in the past while Slack's day-granular ``after:`` happily returns
    everything since. A 06:40 brief rendered a real message sent at 18:06 that
    evening as "overnight" - twelve hours of its own future - and said nothing.

    A record with no usable timestamp is **kept**. It cannot be placed relative
    to either cutoff, and the two errors are not symmetric: over-reporting is a
    line he skims past, under-reporting is a silence he has no way to notice.
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

    Two steps on purpose, for both halves. Slack search resolves to whole days
    and Gmail's ``after:`` is a date, so each query over-fetches and the real
    cutoff is applied here. Dropping that second step is a whole extra *workday*
    in a 6:45am DM - and worse, the same notes re-reported every morning they
    stay inside the ``after:`` day, while the query still looks correct.
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
    # at 7pm yesterday is overnight mail, and today-only would miss all of it.
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
