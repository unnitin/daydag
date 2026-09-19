"""The push kernel: what every loop renders with, held once.

USING IT
    read = Reader()                                  # degrade, never stall
    events = read("calendar", lambda: list(sources.calendar(window)), [])
    note, missing = read_vault_note(read, lambda: sources.weekly_note(path), label="the note")
    lines = meeting_lines(events)          # clock, title, permalink, overlaps flagged
    sections = (Section("meetings", tuple(lines)),)
    Push(day, header, sections, tuple(read.unreachable)).render()
    unsourced_claims(text)                           # guardrail 3, mechanical - must be empty

CONTRACTS
    1. Silence is information. An empty section is omitted, never labelled.
    2. Evidence or silence. `claim` renders a permalink or the admission that
       there is none; `unsourced_claims` checks the rendered text.
    3. Degrade, never stall. `Reader` turns a failing source into one named
       line; `read_vault_note` tells a note nobody wrote apart from a vault
       that could not be reached.
    4. A naive `now` is refused (`aware`), never read against the host zone.
       A naive EVENT start is read as the principal's wall clock (`local`).
    5. One shape. The brief, the wrap and the week-ahead are all a `Push`;
       one `PushError`; one `Sources` protocol; one State.md reader
       (`line_from_state`); one weekly-note scanner; one overlap rule.

WHY IT EXISTS
    Three loops each carried their own copy of the same eight helpers, the
    same push dataclass and the same error class, and every seam defect this
    repo has had came from two copies of one rule disagreeing.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from daydag import recipes
from daydag.ledger import Ledger
from daydag.pulse import Pulse
from daydag.statedoc import StateDoc, StateFolder
from daydag.voice import clipped

__all__ = [
    "ADMISSIONS",
    "QUOTE_CAP",
    "UNSOURCED",
    "WARN",
    "Push",
    "PushError",
    "Reader",
    "Section",
    "Sources",
    "aware",
    "claim",
    "clock",
    "closed_red_items",
    "day_label",
    "first_meeting_line",
    "instant",
    "line_from_state",
    "link",
    "local",
    "meeting_lines",
    "missing_note_section",
    "overlap_clusters",
    "overlap_flags",
    "read_vault_note",
    "red_items",
    "render_push",
    "seed_ledger",
    "shipping_lines",
    "short",
    "state_lines",
    "unsourced_claims",
]


class PushError(RuntimeError):
    """A push was asked for something it cannot honestly produce.

    Raised only for a caller mistake - a naive clock, an unresolved identity -
    never for a source that failed. A failed source degrades to a line; a wrong
    parameter would silently produce a push that reads fine and is not true.
    One class for the brief, the wrap and the week-ahead: a caller that catches
    it catches all three.
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


class Sources(Protocol):
    """The four reads the brief performs. Every one may raise.

    Deliberately narrow, and deliberately taking the *query* rather than the
    parameters behind it: the queries come from :mod:`daydag.recipes`, so an
    adapter cannot quietly ask a different question than the one the recipe
    tests cover.
    """

    def calendar(self, window: recipes.DayWindow) -> Iterable[Mapping[str, Any]]:
        """Events in one local day."""

    def vault_note(self, path: str) -> str:
        """Any vault note by path. `eod_wrap` reads next week's plan and prep
        through this, and it was never DECLARED here - so `run._Payloads`
        implemented only `weekly_note`, both reads raised, and the Friday
        "weekly-planning outcome" section silently never rendered on a real
        run. Both test doubles implement it, which is why the suite was green:
        the fake was more capable than the adapter it stood in for.
        """
        ...

    def weekly_note(self, path: str) -> str:
        """The note's text. ``FileNotFoundError`` means nobody wrote it."""

    def slack(self, query: str) -> Iterable[Mapping[str, Any]]:
        """Messages matching an overnight query: ``ts``, ``text``, ``permalink``."""

    def gmail(self, query: str) -> Iterable[Mapping[str, Any]]:
        """Mail matching a query: ``subject``, ``permalink``, optional ``who``."""


def short(text: str) -> str:
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
    body = f'{text}: "{short(quote)}"' if quote else text
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
class Push:
    """One assembled push: the brief, the wrap and the week-ahead are all this shape."""

    day: date
    header: str
    sections: tuple[Section, ...]
    #: Display names of sources that could not be read this run.
    unreachable: tuple[str, ...] = ()

    def render(self) -> str:
        return render_push(self.header, self.sections, self.unreachable)


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


def local(value: Any) -> datetime | None:
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


def clock(value: Any) -> str:
    """``9:00``, in his register - 12-hour, no am/pm, like the note itself."""
    moment = local(value)
    if moment is None:
        return "all day"
    return f"{moment.hour % 12 or 12}:{moment.minute:02d}"


def instant(event: Mapping[str, Any]) -> float:
    """A sort key that is the real moment, not the way it was written.

    Sorting on ``str(start)`` looked fine and was wrong: a calendar may express
    a 9am PT meeting as ``16:00Z``, and the string form sorts by the digits
    while ignoring the offset. The 3pm then led the day, and because the overlap
    sweep trusts the order it invented a collision between two meetings six
    hours apart. All-day and undated entries sort last.
    """
    moment = local(event.get("start"))
    return moment.timestamp() if moment else float("inf")


def meeting_lines(events: Sequence[Mapping[str, Any]]) -> list[str]:
    ordered = sorted(events, key=instant)
    lines = [
        claim(f"{clock(event.get('start'))} {event.get('summary', 'untitled')}", link(event))
        for event in ordered
    ]
    lines += overlap_flags(ordered)
    return lines


def overlap_flags(ordered: Sequence[Mapping[str, Any]]) -> list[str]:
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
        start = local(later.get("start"))
        if start is None:
            continue
        for earlier in ordered[:index]:
            end = local(earlier.get("end"))
            if end is None or start >= end:
                continue
            flags.append(
                claim(
                    f"{WARN} {earlier.get('summary') or 'untitled'} and"
                    f" {later.get('summary') or 'untitled'} overlap"
                    f" - which one are you in?",
                    link(earlier),
                    link(later),
                )
            )
    return flags


def link(record: Mapping[str, Any]) -> str | None:
    value = record.get("permalink")
    return str(value) if value else None


def overlap_clusters(events: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    """Groups of meetings that pile up on each other, in time order.

    The other rendering of the rule `overlap_flags` applies pairwise: a single
    day has a handful of collisions and every pair is worth a line, but across a
    week four meetings stacked at 11:00 are six near-identical pairs for ONE
    decision, so `week_ahead` wants the cluster. Same half-open comparison -
    9:00-10:00 and 10:00-11:00 are back-to-back, not a clash - held once here
    rather than spelt again in a second module.

    Linear after the sort: starts are ascending, so a meeting either overlaps
    the running end of the open cluster or opens a new one. Sorted on the two
    INSTANTS only - the first version sorted `(start, end, event)` tuples, so two
    invites at the same 11:00-12:00 fell through to comparing the event dicts
    and raised TypeError. That is the stacked-at-11:00 case this exists for, and
    it took the whole Sunday push down.

    A meeting with a start but no end is read as ending when it starts, so it
    can still fall INSIDE another's span - the same asymmetry `overlap_flags`
    has, where only the earlier of a pair needs an end. A meeting with no start
    cannot be placed and is left out; both detectors agree on that too.
    """
    spans = sorted(
        (
            (start, local(event.get("end")) or start, event)
            for event in events
            if (start := local(event.get("start"))) is not None
        ),
        key=lambda span: (span[0], span[1]),
    )
    clusters: list[list[Mapping[str, Any]]] = []
    reach: datetime | None = None
    for start, end, event in spans:
        if reach is not None and start < reach:
            clusters[-1].append(event)
            reach = max(reach, end)
        else:
            clusters.append([event])
            reach = end
    return clusters


def first_meeting_line(events: Iterable[Mapping[str, Any]]) -> tuple[str, str] | None:
    """The day's earliest event, as a claim line plus its raw summary.

    ``None`` with nothing to report - the EOD wrap omits its "tomorrow" section
    entirely rather than asserting an empty day, same as every other silent
    section here. Reused by the wrap for SPEC 3.5's "tomorrow's first meeting"
    rather than reimplemented, so the two never silently disagree on which
    meeting counts as first: the same instant-based order as the brief's own
    meeting lines (see :func:`instant`), the same local clock, the same
    citation-or-admission rendering.

    The summary travels back alongside the rendered line because the wrap
    cross-references it against the meeting ledger's notes gaps, which key on
    the meeting's title, not on the rendered text.
    """
    ordered = sorted(events, key=instant)
    if not ordered:
        return None
    first = ordered[0]
    summary = str(first.get("summary", "untitled"))
    return claim(f"{clock(first.get('start'))} {summary}", link(first)), summary


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
            # Named once, not once per failing call: seven calendar windows
            # that all fail are one dead source, not seven lines.
            if name not in self.unreachable:
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
        if label not in read.unreachable:
            read.unreachable.append(label)
        return "", False


def day_label(day: date) -> str:
    return f"{day:%a %b} {day.day}".lower()


def line_from_state(block: Any) -> str:
    """One State.md block as a push line: his wording, its permalink from
    anywhere in the block - the one rule the brief, the week-ahead and the
    chaser share."""
    return claim(block.body, block.link)


def aware(now: datetime, why: str) -> datetime:
    """``now``, or `PushError` if it carries no zone.

    Every push is a wall-clock question about HIS day, and a naive clock is
    silently hours wrong on a UTC runner. ``why`` names the question, so the
    refusal reads as the loop's own.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise PushError(f"now must be timezone-aware: {why}")
    return now


def missing_note_section(day: date, path: str, consequence: str) -> Section:
    """The section that leads a push when the weekly note was never written.

    A fact about the week, not a degrade: the note is hand-written and the
    series has had gaps, so a real run meets this. Three loops rendered it
    three ways; the consequence is the only part that differs.
    """
    return Section(
        f"{WARN} no weekly note for {recipes.week_label(day)}", (claim(consequence, path),)
    )


def state_lines(read: Reader, state: StateFolder | None) -> tuple[list[str], list[str]]:
    """The chase list and the watch items, read out of the file he corrects by hand.

    ``([], [])`` when no vault is configured. Struck lines are his audit
    trail, not open items.
    """
    if state is None:
        return [], []
    doc = StateDoc.parse(read("the chase list", state.read_state, ""))
    chase = [line_from_state(b) for b in doc.blocks_in("Chase list") if not b.struck]
    watch = [line_from_state(b) for b in doc.blocks_in("Watch items") if not b.struck]
    return chase, watch


def shipping_lines(read: Reader, pulse: Pulse | None) -> list[str]:
    """The pulse's own block as push lines, or nothing.

    ``None`` is silence, not a failure - a run without a pulse has no
    shipping section. A pulse that raises degrades like any other source.
    """
    if pulse is None:
        return []
    block = read("the pulse", pulse.render, "")
    return block.splitlines() if block.strip() else []


def seed_ledger(
    ledger: Ledger,
    events: Sequence[Mapping[str, Any]],
    *,
    required: Sequence[str] = ("id", "start", "end", "attendees"),
) -> None:
    """Seed ``ledger`` from calendar records, refusing one that cannot qualify.

    A payload missing a key the ledger reads seeds ZERO rows and says nothing:
    the notes-gap section simply vanishes, and tomorrow's gap is never
    created. A missing ``id`` or ``end`` already raised; the asymmetry was the
    bug, so every required key raises the same way.
    """
    for event in events:
        missing = [key for key in required if key not in event]
        if missing:
            raise KeyError(
                f"calendar record is missing {', '.join(missing)}; "
                "the ledger cannot qualify it and the gap would vanish silently"
            )
    ledger.seed_day(events)
