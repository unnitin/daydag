"""The morning brief (SPEC 3.1) - the one loop every other loop composes from.

It owns no source and no query. Calendar windows, the Slack overnight cutoff,
the Gemini search and every vault path come from :mod:`daydag.recipes`; notes
gaps from :mod:`daydag.ledger`; the shipping block from :mod:`daydag.pulse`;
chase and watch from ``DayDAG/State.md``; the register from :mod:`daydag.voice`.
What is left here is assembly - and assembly is where this project's defects
have actually lived, because every module was right on its own.

Four rules are structural rather than stylistic, and each has a test:

* **Silence is information** (SPEC 3.7 rule 3). An empty section is omitted, not
  labelled. "no updates" is a line that trains the reader to skim, and a brief
  people skim is a brief that stops being read.
* **Evidence or silence** (guardrail 3). Every claim renders with a permalink or
  a file path, or says out loud that it could not be sourced.
  :func:`unsourced_claims` makes that mechanical instead of advisory.
* **Degrade, never stall** (guardrail 6). A source that raises costs one
  "couldn't check X" line; the brief still ships. Every source is *injected*,
  which is what makes that path testable at all - a client constructed in here
  would be a source nobody can make fail on purpose.
* **The weekly note may not exist.** CLAUDE.md records a gap in the series, so
  the first real run will meet one. A missing note leads the brief.

The reason sources arrive as an object of four methods rather than as data: the
brief has to be able to observe *how* it asked - one calendar day at a time, an
id-scoped Slack query - and those are the two failures that come back as a
plausible-looking empty result rather than as an error.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from daydag import recipes
from daydag.config import resolve_reference
from daydag.ledger import Ledger, title_from_gemini_subject
from daydag.pulse import Pulse
from daydag.state import StateFolder, read_section, split_link
from daydag.voice import Push, clipped, render

__all__ = [
    "ADMISSIONS",
    "QUOTE_CAP",
    "UNSOURCED",
    "WARN",
    "Brief",
    "BriefError",
    "Reader",
    "Section",
    "Sources",
    "assemble",
    "claim",
    "closed_red_items",
    "first_meeting_line",
    "read_vault_note",
    "red_items",
    "render_push",
    "unsourced_claims",
]


class BriefError(RuntimeError):
    """The brief was asked for something it cannot honestly produce.

    Raised only for a caller mistake - a naive clock, an unresolved identity -
    never for a source that failed. A failed source degrades to a line; a wrong
    parameter would silently produce a brief that reads fine and is not true.
    """


#: The sanctioned warning glyph: plain U+26A0, not its emoji-presentation twin.
#: Written as an escape because the difference is invisible in most editors and
#: `voice.voice_violations` fails the whole brief over it.
WARN = "⚠"

#: What a claim says when it has no evidence. Guardrail 3's second half: "if the
#: agent can't source it, it says so instead of asserting".
UNSOURCED = "couldn't source this one"

#: Phrases that count as admitting an absence. A line carrying one of these is
#: making a claim *about* a gap, so it needs no permalink - the gap is the fact.
ADMISSIONS = (
    UNSOURCED,
    "couldn't check",
    "couldn't source",
    "could not fetch",
    "could not read",
    "no note found",
    "not watched",
    "not understood",
)

#: A parenthesised citation: a URL, a `path#sha`, or a vault note path. Matched
#: on the rendered line rather than on an object, because a permalink held on
#: something nobody prints is not a citation.
_EVIDENCE = re.compile(r"\([^()]*(?://|#|\.md)[^()]*\)")

#: Longest verbatim quote carried inline. The permalink is the full record, so
#: trimming loses nothing that cannot be clicked - but an untrimmed 400-char
#: Slack message turns a scannable brief into a wall.
QUOTE_CAP = 160


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------


class Sources(Protocol):
    """The four reads the brief performs. Every one may raise.

    Deliberately narrow, and deliberately taking the *query* rather than the
    parameters behind it: the queries come from :mod:`daydag.recipes`, so an
    adapter cannot quietly ask a different question than the one the recipe
    tests cover.
    """

    def calendar(self, window: recipes.DayWindow) -> Iterable[Mapping[str, Any]]:
        """Events in one local day."""

    def weekly_note(self, path: str) -> str:
        """The note's text. ``FileNotFoundError`` means nobody wrote it."""

    def slack(self, query: str) -> Iterable[Mapping[str, Any]]:
        """Messages matching an overnight query: ``ts``, ``text``, ``permalink``."""

    def gmail(self, query: str) -> Iterable[Mapping[str, Any]]:
        """Mail matching a query: ``subject``, ``permalink``, optional ``who``."""


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _short(text: str) -> str:
    """A quote, clipped to the brief's own budget. See `voice.clipped`."""
    return clipped(text, QUOTE_CAP, ellipsis="...")


def claim(text: str, *permalinks: str | None, quote: str | None = None) -> str:
    """One push line, with its citation or with an admission that it has none.

    Public - and used outside this module - because "evidence or silence" is
    not a brief-specific rule (guardrail 3). The EOD wrap cites a closed red
    item, or tomorrow's first meeting, exactly the same way the brief cites an
    overnight message: text, then a permalink or a path in parens, or the
    admission that there is none.
    """
    body = f'{text}: "{_short(quote)}"' if quote else text
    links = [str(link) for link in permalinks if link]
    if not links:
        return f"- {body} ({UNSOURCED})"
    return "- " + body + "".join(f" ({link})" for link in links)


def unsourced_claims(text: str) -> list[str]:
    """Every bullet in ``text`` that asserts something without evidence.

    The mechanical form of guardrail 3, applied to a rendered brief. A bullet
    passes if it carries a citation or if it admits it has none; headings and
    the greeting are not claims and are ignored.

    Known limit, stated rather than hidden: a claim whose own text happens to
    contain a parenthesised ``#`` or ``.md`` reads as sourced. The check is a
    floor under the rendering, not a proof of provenance.
    """
    unsourced: list[str] = []
    for line in text.splitlines():
        if not line.startswith("- "):
            continue
        if _EVIDENCE.search(line):
            continue
        if any(admission in line for admission in ADMISSIONS):
            continue
        unsourced.append(line)
    return unsourced


@dataclass(frozen=True)
class Section:
    """A heading and its lines. An empty one never renders."""

    heading: str
    lines: tuple[str, ...] = ()

    def render(self) -> str:
        return "\n".join([self.heading, *self.lines])


def render_push(header: str, sections: Sequence[Section], unreachable: Sequence[str]) -> str:
    """Assemble one push: a header, its non-empty sections, then dead sources.

    Shared by :meth:`Brief.render` and the EOD wrap's own render - both pushes
    are this same shape, and it is not brief-specific: a header line, sections
    that vanish when empty (silence is information, SPEC 3.7 rule 3), and one
    line per source that could not be reached (guardrail 6), in that order.
    """
    blocks = [header]
    blocks += [section.render() for section in sections if section.lines]
    if unreachable:
        blocks.append("\n".join(f"- couldn't check {name}" for name in unreachable))
    return "\n\n".join(blocks)


@dataclass(frozen=True)
class Brief:
    """One morning's assembled brief."""

    day: date
    header: str
    sections: tuple[Section, ...]
    #: Display names of sources that could not be read this run.
    unreachable: tuple[str, ...] = ()

    def render(self) -> str:
        return render_push(self.header, self.sections, self.unreachable)


# --------------------------------------------------------------------------
# the weekly note - the note's own layout wins over any template
# --------------------------------------------------------------------------

RED = "🔴"
_OTHER_TIERS = ("🟡", "🟢")

#: Any ATX heading, used to scope priority by section (see red_items).
_HEADING_ANY = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<name>.+?)\s*$")

#: A list item, with or without a checkbox, under any heading at all. Guardrail
#: 2: the layout evolves (Section A-D became Priorities around 0622), so nothing
#: here keys off a heading name.
_ITEM = re.compile(r"^\s*[-*]\s+(?:\[(?P<tick>[ xX])\]\s*)?(?P<body>\S.*?)\s*$")


def _scan_red_items(note: str, *, ticked: bool) -> list[tuple[int, str]]:
    """The shared scan behind :func:`red_items` and :func:`closed_red_items`.

    Both are the same walk over the same note, differing only in which side of
    the checkbox they keep - open items for the brief's "top of the note",
    ticked ones for the EOD wrap's "what closed today" (SPEC 3.5). Extracted
    rather than duplicated: the heading-scoping rules below are the fiddly,
    previously-wrong part (see the inline comments), and a second copy is a
    second place for the same bug to come back in.

    Three rules, each of them the vault's practice rather than its documentation
    (invariant 6):

    * any list item carrying 🔴, under any heading - the note's own layout wins;
    * a checkbox's tick decides open vs. closed *in place*, even though the
      note's header documents a strike-and-move rule it does not follow;
    * a line naming 🟡 or 🟢 as well is the triage legend, not a priority. Notes
      carry one, and without this the legend leads every brief.
    """
    found: list[tuple[int, str]] = []
    under_red = False
    red_level = 0
    for lineno, line in enumerate((note or "").splitlines(), 1):
        heading = _HEADING_ANY.match(line)
        if heading:
            # The vault's real layout scopes priority by HEADING - `## 🔴 High -
            # needs my hand this week` - and leaves the items beneath it
            # unmarked. Requiring the emoji in the item body returned nothing
            # against a real note and silently dropped the brief's lead section;
            # it only looked right because the fixtures repeated the emoji on
            # every item. A heading naming several tiers is the triage legend.
            tiers = [t for t in (RED, *_OTHER_TIERS) if t in heading["name"]]
            level = len(heading["hashes"])
            if tiers == [RED]:
                under_red, red_level = True, level
            elif under_red and level <= red_level:
                # Only a heading at the SAME level or shallower ends the
                # section. Clearing on any heading meant a `### Ingestion`
                # nested inside `## 🔴 High` silently dropped every item under
                # it - the same failure this block was written to fix, one
                # level down, and no fixture nests a heading.
                under_red = False
            continue
        item = _ITEM.match(line)
        if not item:
            continue
        is_ticked = bool((item["tick"] or " ").strip())
        if is_ticked != ticked:
            continue
        if any(tier in item["body"] for tier in _OTHER_TIERS):
            continue
        # Red by its heading, or marked in the body for notes that do that.
        if under_red or RED in item["body"]:
            found.append((lineno, item["body"]))
    return found


def red_items(note: str) -> list[tuple[int, str]]:
    """Open 🔴 items in a weekly note, as ``(line number, text)``.

    Three rules, each of them the vault's practice rather than its documentation
    (invariant 6):

    * any list item carrying 🔴, under any heading - the note's own layout wins;
    * ticked items are closed *in place*, so ``- [x]`` is skipped even though
      the note's header documents a strike-and-move rule it does not follow;
    * a line naming 🟡 or 🟢 as well is the triage legend, not a priority. Notes
      carry one, and without this the legend leads every brief.
    """
    return _scan_red_items(note, ticked=False)


def closed_red_items(note: str) -> list[tuple[int, str]]:
    """Closed 🔴 items in a weekly note, as ``(line number, text)``.

    The mirror of :func:`red_items`: same heading-scoped legend rules, but
    keeps the ticked side of the checkbox instead of the open one. This is the
    EOD wrap's "what closed today" (SPEC 3.5) - a red item struck in place is,
    per invariant 6, ready to move to Done in Workstreams even though nothing
    here touches that file.
    """
    return _scan_red_items(note, ticked=True)


# --------------------------------------------------------------------------
# State.md - read, because a hand edit wins over anything derived
#
# The parse itself lives in `daydag.state`, beside the writer: the week-ahead
# loop (SPEC 3.6) reads the same hand-edited sections, and a second regex here
# would be a second set of bugs that agree only on the easy cases.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# the calendar day
# --------------------------------------------------------------------------


def _local(value: Any) -> datetime | None:
    """A calendar instant in the principal's zone.

    A NAIVE datetime is assumed to already be his local wall-clock time, not
    reinterpreted via the host's zone: `astimezone()` on a naive value adopts
    whatever TZ the runner has, so a 9am event printed as 2:00 under TZ=UTC and
    mis-sorted against everything else. `assemble` refuses a naive `now` for the
    same reason; event starts arrive from the connector and get the same care.
    """
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=recipes.PACIFIC)
    return value.astimezone(recipes.PACIFIC)


def _clock(value: Any) -> str:
    """``9:00``, in his register - 12-hour, no am/pm, like the note itself."""
    moment = _local(value)
    if moment is None:
        return "all day"
    return f"{moment.hour % 12 or 12}:{moment.minute:02d}"


def _instant(event: Mapping[str, Any]) -> float:
    """A sort key that is the real moment, not the way it was written.

    Sorting on ``str(start)`` looked fine and was wrong: a calendar may express
    a 9am PT meeting as ``16:00Z``, and the string form sorts by the digits
    while ignoring the offset. The 3pm then led the day, and because the overlap
    sweep trusts the order it invented a collision between two meetings six
    hours apart. All-day and undated entries sort last.
    """
    moment = _local(event.get("start"))
    return moment.timestamp() if moment else float("inf")


def _meeting_lines(events: Sequence[Mapping[str, Any]]) -> list[str]:
    ordered = sorted(events, key=_instant)
    lines = [
        claim(f"{_clock(event.get('start'))} {event.get('summary', 'untitled')}", _link(event))
        for event in ordered
    ]
    lines += _overlap_flags(ordered)
    return lines


def _overlap_flags(ordered: Sequence[Mapping[str, Any]]) -> list[str]:
    """Duplicate slots, surfaced and left unresolved (invariant 5).

    Every earlier meeting against every later one, not just adjacent pairs: a
    long block hides the collisions past its immediate successor, so a 9-12 hold
    with an 11:00 invite inside it went unflagged - which is exactly the case
    the flag exists for. Quadratic on a handful of meetings a day.

    Half-open comparison, so 9:00-10:00 and 10:00-11:00 are back-to-back rather
    than a collision. Choosing between two real invites is his call; the agent's
    job is to make sure he sees there are two.
    """
    flags: list[str] = []
    for index, later in enumerate(ordered):
        start = _local(later.get("start"))
        if start is None:
            continue
        for earlier in ordered[:index]:
            end = _local(earlier.get("end"))
            if end is None or start >= end:
                continue
            flags.append(
                claim(
                    f"{WARN} {earlier.get('summary') or 'untitled'} and"
                    f" {later.get('summary') or 'untitled'} overlap"
                    f" - which one are you in?",
                    _link(earlier),
                    _link(later),
                )
            )
    return flags


def _link(record: Mapping[str, Any]) -> str | None:
    value = record.get("permalink")
    return str(value) if value else None


def first_meeting_line(events: Iterable[Mapping[str, Any]]) -> tuple[str, str] | None:
    """The day's earliest event, as a claim line plus its raw summary.

    ``None`` with nothing to report - the EOD wrap omits its "tomorrow" section
    entirely rather than asserting an empty day, same as every other silent
    section here. Reused by the wrap for SPEC 3.5's "tomorrow's first meeting"
    rather than reimplemented, so the two never silently disagree on which
    meeting counts as first: the same instant-based order as the brief's own
    meeting lines (see :func:`_instant`), the same local clock, the same
    citation-or-admission rendering.

    The summary travels back alongside the rendered line because the wrap
    cross-references it against the meeting ledger's notes gaps, which key on
    the meeting's title, not on the rendered text.
    """
    ordered = sorted(events, key=_instant)
    if not ordered:
        return None
    first = ordered[0]
    summary = str(first.get("summary", "untitled"))
    return claim(f"{_clock(first.get('start'))} {summary}", _link(first)), summary


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------


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
        error=BriefError,
    )


class Reader:
    """Runs one read, and turns a failure into one line instead of an exception.

    Public because degrading a source to one "couldn't check X" line (guardrail
    6) is not a brief-specific mechanism - the EOD wrap reads its own, smaller
    set of sources through the same object rather than a second copy of this
    ten-line class.
    """

    def __init__(self) -> None:
        self.unreachable: list[str] = []

    def __call__(self, name: str, call, default):
        try:
            return call()
        except Exception:  # any failure degrades identically
            # The exception text is deliberately not carried into the brief: it
            # is source-controlled text, and the brief is read as the agent's
            # own words. The name of the source is the whole message.
            self.unreachable.append(name)
            return default


def read_vault_note(read: Reader, fetch: Callable[[], str], *, label: str) -> tuple[str, bool]:
    """One vault note, read through the degrade path - and a third state told apart.

    Returns ``(text, missing)``. Two calls into this function - the brief's
    weekly note and the EOD wrap's - both need the same three-way split, and
    conflating any two of them is the exact bug this exists to prevent:

    * read cleanly - ``missing`` is ``False``, ``text`` is the note.
    * never written (``FileNotFoundError``) - not a failure. The note is
      hand-written and CLAUDE.md records a gap in the series, so the first
      real run meets one. ``missing`` is ``True``, ``text`` is ``""``.
    * the source itself could not be reached (anything else) - recorded on
      ``read`` like every other degrade, exactly as if this were "calendar" or
      "slack". ``missing`` is ``False`` - an absent note is a fact, a downed
      source is a degrade, and the two must not read the same in a rendered
      push.
    """
    try:
        return fetch(), False
    except FileNotFoundError:
        return "", True
    except Exception:  # any other failure degrades like any other source
        read.unreachable.append(label)
        return "", False


def assemble(
    *,
    now: datetime,
    sources: Sources,
    identities: Mapping[str, str],
    state: StateFolder | None = None,
    ledger: Ledger | None = None,
    pulse: Pulse | None = None,
) -> Brief:
    """Build the morning brief for ``now``'s local day.

    ``state``, ``ledger`` and ``pulse`` are optional because they are *state the
    caller owns*, not sources: a run without a pulse has no shipping block, and
    that is silence rather than a failure. A source that raises is the other
    case, and it lands in :attr:`Brief.unreachable`.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise BriefError(
            "now must be timezone-aware: the 6pm overnight cutoff is a local "
            "wall-clock time, and a naive clock is silently hours wrong on a "
            "UTC runner"
        )
    principal = _principal(identities)
    day = now.astimezone(recipes.PACIFIC).date()
    read = Reader()
    sections: list[Section] = []
    watch: list[str] = []

    # -- calendar, one request per day ------------------------------------
    events: list[Mapping[str, Any]] = []
    for window in recipes.calendar_days(day, day):
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
            Section(
                f"{WARN} no weekly note for {recipes.week_label(day)}",
                (claim("can't triage today against the week's priorities", note_path),),
            )
        )

    if events:
        sections.append(Section(f"meetings ({len(events)})", tuple(_meeting_lines(events))))

    red = red_items(note)
    if red:
        sections.append(
            Section(
                f"top of the note ({recipes.week_label(day)})",
                tuple(claim(text, f"{note_path}#L{lineno}") for lineno, text in red),
            )
        )

    # -- chase and watch, read out of the file he corrects by hand --------
    if state is not None:
        written = read("the chase list", state.read_state, "")
        chase = [_line_from_state(body) for body in read_section(written, "Chase list")]
        watch = [_line_from_state(body) for body in read_section(written, "Watch items")]
        if chase:
            sections.append(Section(f"owed to you ({len(chase)})", tuple(chase)))

    # -- the overnight delta ----------------------------------------------
    overnight = _overnight_lines(now, principal, sources, read)
    if overnight:
        sections.append(Section(f"overnight ({len(overnight)})", tuple(overnight)))

    # -- shipping, only when it has something to say ----------------------
    if pulse is not None:
        block = read("shipping", pulse.render, "")
        if block.strip():
            sections.append(Section("shipping", tuple(block.splitlines())))

    if state is not None and watch:
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

    header = render(
        Push.MORNING_BRIEF,
        {
            "day": _day_label(day),
            # Not `len(events)` when the read failed: zero events and an unread
            # calendar are the same number and not the same fact, and the header
            # is not a `- ` line, so `unsourced_claims` never sees it assert one.
            "count": "?" if "calendar" in read.unreachable else len(events),
        },
    )
    return Brief(
        day=day,
        header=header,
        sections=tuple(sections),
        unreachable=tuple(read.unreachable),
    )


def _day_label(day: date) -> str:
    return f"{day:%a %b} {day.day}".lower()


def _line_from_state(body: str) -> str:
    text, link = split_link(body)
    return claim(text, link)


def _seed_and_gaps(ledger: Ledger, events: Sequence[Mapping[str, Any]], now: datetime) -> list[str]:
    """Seed today's rows, then report yesterday's meetings that produced nothing.

    Seeding first is the whole mechanism: a meeting with no row can never be
    surfaced as a gap, so the gap the agent reports tomorrow is created today.

    A payload missing ``attendees`` is refused rather than tolerated. The
    ledger's qualification rule reads that key, so an adapter that omits it
    seeds ZERO rows and the notes-gap section simply vanishes - no error, no
    "couldn't check" line, and tomorrow's gap is never created. A missing
    ``id`` or ``end`` already raises and is therefore surfaced; the asymmetry
    was the bug, not the strictness.
    """
    for event in events:
        missing = [key for key in ("id", "start", "end", "attendees") if key not in event]
        if missing:
            raise KeyError(
                f"calendar record is missing {', '.join(missing)}; "
                "the ledger cannot qualify it and the gap would vanish silently"
            )
    ledger.seed_day(events)
    return ledger.notes_gaps(as_of=now)


#: Timestamp fields an adapter may carry. Slack's is `ts`; Gmail's arrival time
#: is `internalDate`, which is what makes the mail half filterable at all -
#: `after:` is day-granular, so the query alone cannot mean "since 6pm".
_STAMP_FIELDS = ("ts", "internal_date", "internalDate")


def _is_overnight(record: Mapping[str, Any], min_ts: float) -> bool:
    """Whether a record landed after the cutoff.

    A record with no usable timestamp is **kept**. It cannot be placed relative
    to the cutoff, and the two errors are not symmetric: over-reporting is a
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
        return (stamp / 1000 if stamp > 1e11 else stamp) >= min_ts
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
        if not _is_overnight(message, window.min_ts):
            continue
        who = message.get("who") or message.get("from") or "someone"
        lines.append(claim(str(who), _link(message), quote=str(message.get("text", ""))))

    # `after` is the evening the window opens, not today: a note that landed
    # at 7pm yesterday is overnight mail, and today-only would miss all of it.
    opened_on = datetime.fromtimestamp(window.min_ts, tz=recipes.PACIFIC).date()
    query = recipes.gmail_gemini_notes(after=opened_on)
    for mail in read("gmail", lambda: list(sources.gmail(query)), []):
        if not _is_overnight(mail, window.min_ts):
            continue
        subject = str(mail.get("subject", ""))
        title = title_from_gemini_subject(subject)
        who = "notes landed" if title else str(mail.get("who") or mail.get("from") or "mail")
        lines.append(claim(who, _link(mail), quote=title or subject))
    return lines
