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
       and the alternative is worse. A naive ``now`` is REFUSED (`BriefError`)
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

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, NamedTuple, Protocol

from daydag import recipes
from daydag.config import resolve_reference
from daydag.ledger import DISQUALIFYING_RESPONSES, Ledger, title_from_gemini_subject
from daydag.pulse import Pulse
from daydag.state import StateFolder, is_struck, link_only, read_rows, row_owner, split_link
from daydag.voice import Push, clipped, render

__all__ = [
    "ADMISSIONS",
    "NOT_SHOWN",
    "QUOTE_CAP",
    "UNSOURCED",
    "WARN",
    "Brief",
    "BriefError",
    "MeetingRow",
    "Reader",
    "Section",
    "Sources",
    "assemble",
    "claim",
    "clip_line",
    "closed_red_items",
    "day_link",
    "first_meeting_line",
    "packed_meeting_rows",
    "read_vault_note",
    "red_items",
    "render_push",
    "split_citation",
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

#: Said by a morning overflow line whose rows went to the thread under the DM
#: (`daydag.budget`). An admission, not a claim: the rows are one tap away in
#: the reply under the DM.
NOT_SHOWN = "not shown here"

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
    NOT_SHOWN,
    # The State.md writer declining (`StateNotWritable`): a failed write, said
    # once at the foot of the push, is a gap and not an assertion.
    "couldn't update",
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
    """A heading and its lines. An empty one never renders.

    ``key`` names the section for `daydag.budget`, which ranks and caps the
    morning push by it; it never renders, and every other push leaves it empty.
    """

    heading: str
    lines: tuple[str, ...] = ()
    key: str = ""

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
    #: The day's calendar records, for `daydag.budget`'s packed meeting rows.
    events: tuple[Mapping[str, Any], ...] = ()

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


def _meeting_lines(events: Sequence[Mapping[str, Any]], day: date) -> list[str]:
    ordered = sorted(events, key=_instant)
    lines = [
        claim(f"{_clock(event.get('start'))} {event.get('summary', 'untitled')}", _link(event))
        for event in ordered
    ]
    lines += _overlap_flags(ordered, day)
    return lines


def _firm(event: Mapping[str, Any]) -> bool:
    """He said yes to it, or it is his own: a clash between two of these is his
    call. A payload that carries no response is read as a yes, so a missing
    field shows a clash rather than hiding one."""
    status = event.get("response_status")
    return status in (None, "accepted") or bool(event.get("organizer_is_self"))


def _overlap_flags(ordered: Sequence[Mapping[str, Any]], day: date) -> list[str]:
    """Duplicate slots, surfaced and left unresolved (invariant 5).

    Every earlier meeting against every later one, not just adjacent pairs: a
    long block hides the collisions past its immediate successor, so a 9-12 hold
    with an 11:00 invite inside it went unflagged - which is exactly the case
    the flag exists for. Quadratic on a handful of meetings a day.

    Half-open comparison, so 9:00-10:00 and 10:00-11:00 are back-to-back rather
    than a collision. Choosing between two real invites is his call; the agent's
    job is to make sure he sees there are two.

    Only a clash between two meetings he accepted or owns gets its own line,
    citing the later one - each meeting's own line above already cites it.
    One with an invite he has not answered is counted on a single line: on
    10/1 six of six flags were that kind, each carrying two calendar URLs (#189).
    """
    flags: list[str] = []
    unanswered = 0
    for earlier, later in _clashes(ordered):
        if not (_firm(earlier) and _firm(later)):
            unanswered += 1
            continue
        flags.append(
            claim(
                f"{WARN} {earlier.get('summary') or 'untitled'} and"
                f" {later.get('summary') or 'untitled'} overlap"
                f" - which one are you in?",
                _link(later),
            )
        )
    if unanswered:
        flags.append(_unanswered_line(unanswered, day))
    return flags


def _clashes(
    ordered: Sequence[Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Every (earlier, later) pair that overlaps, half-open, in time order."""
    pairs = []
    for index, later in enumerate(ordered):
        start = _local(later.get("start"))
        if start is None:
            continue
        for earlier in ordered[:index]:
            end = _local(earlier.get("end"))
            if end is not None and start < end:
                pairs.append((earlier, later))
    return pairs


def _unanswered_line(count: int, day: date) -> str:
    plural = "s" if count != 1 else ""
    return claim(
        f"{WARN} {count} more overlap{plural} w/ invites you haven't accepted", day_link(day)
    )


def _link(record: Mapping[str, Any]) -> str | None:
    value = record.get("permalink")
    return str(value) if value else None


def overlap_clusters(events: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    """Groups of meetings that pile up on each other, in time order.

    The other rendering of the rule `_overlap_flags` applies pairwise: a single
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
    can still fall INSIDE another's span - the same asymmetry `_overlap_flags`
    has, where only the earlier of a pair needs an end. A meeting with no start
    cannot be placed and is left out; both detectors agree on that too.
    """
    spans = sorted(
        (
            (start, _local(event.get("end")) or start, event)
            for event in events
            if (start := _local(event.get("start"))) is not None
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
# the line, cut to size: `daydag.budget` shortens the morning with these
# --------------------------------------------------------------------------

#: One citation group at the end of a line: `` (https://...)``, `` (path#L4)``,
#: `` (DayDAG/State.md)`` or the admission `` (couldn't source this one)``.
_TAIL_GROUP = re.compile(r" \((?P<inner>[^()]*)\)$")

#: Markup that must not be left open by a cut, checked in this order so a
#: two-character marker is not read as two single ones. `_` is left alone:
#: snake_case in a title would read as an open italic.
_MARKERS = ("**", "~~", "`", "*", '"')


def split_citation(line: str) -> tuple[str, str]:
    """A push line split into what it says and the citations that follow it.

    ``claim`` writes ``- text (link) (link)`` or ``- text (couldn't source this
    one)``; the tail is every trailing parenthesised group that is evidence (a
    URL, ``path#anchor`` or ``.md`` path - the same rule as `unsourced_claims`)
    or the unsourced admission. A quote that ends in its own parenthesis is
    closed by a quote mark first, so it stays in the body.
    """
    body, tail = line, ""
    while True:
        found = _TAIL_GROUP.search(body)
        if found is None:
            break
        inner = found["inner"]
        if inner != UNSOURCED and not _EVIDENCE.search(f"({inner})"):
            break
        tail = body[found.start() :] + tail
        body = body[: found.start()]
    return body, tail


def _closers(text: str) -> str:
    """What closes the markup ``text`` leaves open, innermost first.

    Inside a code span nothing is markup until its backtick closes, so a ``*``
    or a quote in code never earns a closer. A marker already open is closed
    where it next appears, closing anything opened after it on the way.
    """
    opened: list[str] = []
    index = 0
    while index < len(text):
        if opened and opened[-1] == "`":
            if text[index] == "`":
                opened.pop()
            index += 1
            continue
        for marker in _MARKERS:
            if text.startswith(marker, index):
                if marker in opened:
                    while opened.pop() != marker:
                        pass
                else:
                    opened.append(marker)
                index += len(marker)
                break
        else:
            index += 1
    return "".join(reversed(opened))


#: A link inside what a line says: a markdown or Slack link, or a bare URL.
_BODY_LINK = re.compile(r"\]\(|https?://")


def clip_line(line: str, cap: int) -> str:
    """``line`` with what it says clipped to ``cap`` characters, every link whole.

    The budget is spent on the words, never a link: a clipped permalink is a
    broken one, and an uncited line fails guardrail 3. So a line whose words
    carry a link of their own is left whole, and the citation is never cut.
    Markup the cut leaves open (bold, strike, code, a quote) is closed inside
    the cap, so a struck item cannot strike the rest of the push.
    """
    body, tail = split_citation(line)
    prefix = "- " if body.startswith("- ") else ""
    text = " ".join(body[len(prefix) :].split())
    if len(text) <= cap or _BODY_LINK.search(text):
        return line
    room = cap
    while True:
        cut = text if len(text) <= room else text[: max(0, room - 3)].rstrip() + "..."
        closers = _closers(cut)
        if len(cut) + len(closers) <= cap or room <= 3:
            return prefix + cut + closers + tail
        room -= len(cut) + len(closers) - cap


def day_link(day: date) -> str:
    """The calendar's day view for ``day``: one link that cites a whole row of
    meetings, where each meeting's own link costs 150 characters."""
    return f"https://calendar.google.com/calendar/r/day/{day.year}/{day.month}/{day.day}"


#: A meeting's title in a packed row, and how long a row of meetings may run
#: before the next starts. Sized so a heavy day's 24 meetings take about ten
#: lines, the shape of the hand-written briefs he accepted.
TITLE_CAP = 60
PACK_WIDTH = 150


def _is_all_day(event: Mapping[str, Any]) -> bool:
    """A date, or a midnight start running a day or more.

    The second shape is how a bare ``2026-10-07`` arrives once the payload
    adapter has parsed it: as midnight. Read as a meeting, it overlaps every
    real one and folds the whole day into a single "which one are you in?".
    """
    start, end = event.get("start"), event.get("end")
    if not isinstance(start, datetime):
        return True
    if (start.hour, start.minute, start.second) != (0, 0, 0) or not isinstance(end, datetime):
        return False
    try:
        return end - start >= timedelta(days=1)
    except TypeError:
        return False


def _packed_title(event: Mapping[str, Any]) -> str:
    title = clipped(event.get("summary") or "untitled", TITLE_CAP, ellipsis="...")
    when = "all day" if _is_all_day(event) else _clock(event.get("start"))
    return f"{when} {title}"


class MeetingRow(NamedTuple):
    """One packed line and the meetings in it."""

    line: str
    members: tuple[Mapping[str, Any], ...]


def packed_meeting_rows(events: Sequence[Mapping[str, Any]], day: date) -> list[MeetingRow]:
    """The day's meetings in about a tenth of the space, each row citing the day.

    Every meeting is in a row, in time order. A pile-up of meetings he
    accepted or owns - the same `overlap_clusters` the week-ahead uses, so the
    two never disagree on what a clash is - is ONE line ending "which one are
    you in?", where the pairwise flags spent a line per pair. An invite he has
    not answered never joins one: it packs like any other meeting, and its
    clashes are counted on one line, the rule `_overlap_flags` applies (#189).
    Meetings that clash with nothing share ``·``-joined rows up to
    `PACK_WIDTH`. All-day and undated entries never join a pile-up and come
    last. Each row carries its meetings, so the budget can cut whole rows and
    count every meeting it did not show.
    """
    link = day_link(day)
    rows: list[MeetingRow] = []
    run: list[Mapping[str, Any]] = []

    def flush() -> None:
        if run:
            text = " · ".join(_packed_title(e) for e in run)
            rows.append(MeetingRow(claim(text, link), tuple(run)))
            run.clear()

    def add(event: Mapping[str, Any]) -> None:
        width = sum(len(_packed_title(e)) + 3 for e in run) + len(_packed_title(event))
        if run and width > PACK_WIDTH:
            flush()
        run.append(event)

    timed = sorted((e for e in events if not _is_all_day(e)), key=_instant)
    units = overlap_clusters([e for e in timed if _firm(e)])
    units += [[event] for event in timed if not _firm(event)]
    for cluster in sorted(units, key=lambda unit: _instant(unit[0])):
        if len(cluster) > 1:
            flush()
            names = " · ".join(_packed_title(event) for event in cluster)
            line = claim(f"{WARN} {names} - which one are you in?", link)
            rows.append(MeetingRow(line, tuple(cluster)))
            continue
        add(cluster[0])
    flush()
    unanswered = sum(1 for a, b in _clashes(timed) if not (_firm(a) and _firm(b)))
    if unanswered:
        rows.append(MeetingRow(_unanswered_line(unanswered, day), ()))
    for event in events:
        if _is_all_day(event):
            add(event)
    flush()
    return rows


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
    owns: Callable[[str], bool] | None = None,
    who: Callable[[str], str | None] | None = None,
    checks: Mapping[tuple[str, str], RowCheck] | None = None,
) -> Brief:
    """Build the morning brief for ``now``'s local day.

    ``state``, ``ledger`` and ``pulse`` are optional because they are *state the
    caller owns*, not sources: a run without a pulse has no shipping block, and
    that is silence rather than a failure. A source that raises is the other
    case, and it lands in :attr:`Brief.unreachable`.

    ``owns`` says whether a chase row's owner is him, so his own steps read as
    owed BY him (#186); ``who`` names a Slack user id when a mention carries no
    name (#190). The runner builds both. Without ``owns`` only a ``X → you``
    row is his; without ``who`` an unnamed sender is "someone". ``checks``
    maps a chase row - (head line, citation) - to what reading its thread
    established this run (#187). Given, every row it does not name renders
    "couldn't verify": shown and not read is not open. Not given (no State.md
    to check), rows render as they are.
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
                key="no-note",
            )
        )

    # What he reads leaves out the invites he declined - he already answered
    # "which one are you in?" (#189). The ledger still gets every event: a
    # decline is how it retracts a row seeded earlier in the day (#169).
    shown = [e for e in events if e.get("response_status") not in DISQUALIFYING_RESPONSES]
    if shown:
        sections.append(
            Section(f"meetings ({len(shown)})", tuple(_meeting_lines(shown, day)), key="meetings")
        )

    red = red_items(note)
    if red:
        sections.append(
            Section(
                f"top of the note ({recipes.week_label(day)})",
                tuple(claim(text, f"{note_path}#L{lineno}") for lineno, text in red),
                key="note",
            )
        )

    # -- chase and watch, read out of the file he corrects by hand --------
    if state is not None:
        written = read("the chase list", state.read_state, "")
        owed_rows, owed_by_rows = chase_rows(written, owns)

        def mark(head: str, subs: tuple[str, ...]) -> RowCheck | None:
            if checks is None:
                return None
            return checks.get((head, row_citation(head, subs) or ""), RowCheck("unchecked"))

        chase = [_line_from_state(h, s, mark(h, s)) for h, s in owed_rows]
        owed_by = [_line_from_state(h, s, mark(h, s)) for h, s in owed_by_rows]
        watch = [
            _line_from_state(head, subs)
            for head, subs in read_rows(written, "Watch items")
            if not is_struck(head)
        ]
        if chase:
            sections.append(Section(f"owed to you ({len(chase)})", tuple(chase), key="owed"))
        if owed_by:
            sections.append(Section(f"owed by you ({len(owed_by)})", tuple(owed_by), key="owed-by"))

    # -- the overnight delta ----------------------------------------------
    overnight = _overnight_lines(now, principal, sources, read, who)
    if overnight:
        sections.append(Section(f"overnight ({len(overnight)})", tuple(overnight), key="overnight"))

    # -- shipping, only when it has something to say ----------------------
    if pulse is not None:
        block = read("shipping", pulse.render, "")
        if block.strip():
            sections.append(Section("shipping", tuple(block.splitlines()), key="shipping"))

    if state is not None and watch:
        sections.append(Section("watch", tuple(watch), key="watch"))

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
                    key="gaps",
                )
            )

    header = render(
        Push.MORNING_BRIEF,
        {
            "day": _day_label(day),
            # Not `len(events)` when the read failed: zero events and an unread
            # calendar are the same number and not the same fact, and the header
            # is not a `- ` line, so `unsourced_claims` never sees it assert one.
            "count": "?" if "calendar" in read.unreachable else len(shown),
        },
    )
    return Brief(
        day=day,
        header=header,
        sections=tuple(sections),
        unreachable=tuple(read.unreachable),
        events=tuple(shown),
    )


def _day_label(day: date) -> str:
    return f"{day:%a %b} {day.day}".lower()


#: "Boris → you": the file's own way of saying the actions are his.
_TO_HIM = re.compile(r"→\s*you\b", re.IGNORECASE)


def _owned_by_him(owner: str, owns: Callable[[str], bool] | None) -> bool:
    """Whether a chase row's owner column names him - alone, in a list, joined
    with ``+`` or ``&`` as the hand-written rows do, or as ``X → you``."""
    if _TO_HIM.search(owner):
        return True
    return owns is not None and owns(re.sub(r"\s*[+&]\s*", ", ", owner))


@dataclass(frozen=True)
class RowCheck:
    """What this run established about a chase row (#187): from its thread,
    ``answered`` (with the closing message), ``open`` or ``unchecked``; from
    his sent messages or the messages to him, ``closing`` or ``discussed``
    (with the evidence) - proposals for him to confirm, never a closure."""

    status: str
    quote: str = ""
    permalink: str = ""


#: How a row reads when something was found about it (#187), strongest first:
#: the owner answered in the ask's thread; then, from the live sources, a
#: reply or delivery that looks like closure; then a mention that is only talk.
#: How much of the evidence a marked row quotes, so the row is still named.
_EVIDENCE_QUOTE = 60

_EVIDENCED = {
    "answered": "✓ looks answered, strike it?",
    "closing": "looks closed, confirm?",
    "discussed": "discussed since:",
}


def row_citation(body: str, subs: Sequence[str] = ()) -> str | None:
    """The link a State.md row is cited by: the one on its head line or, when
    the head has none, a sub-bullet that is a link and nothing else - the
    shape the writer used until #184. His prose comments are never read."""
    link = split_link(body)[1]
    return link or next((url for url in map(link_only, subs) if url), None)


def _line_from_state(body: str, subs: Sequence[str] = (), check: RowCheck | None = None) -> str:
    """A State.md row as a claim, marked by what this run read of its thread.

    Open is a verdict, not a default (#187): a row whose reply was read and
    found carries the closing quote for him to strike; one that could not be
    read says so. A row read and still open is shown as it is.
    """
    text, link = split_link(body)[0], row_citation(body, subs)
    # The mark LEADS the line: a fitted morning clips a long row's words from
    # the end, and a mark at the end was the first thing it cut.
    if check is not None and check.status in _EVIDENCED:
        evidence = check.permalink if check.permalink != link else None
        # The evidence leads, held short: a fitted morning clips a row's words
        # from the end, and the quote is what makes the mark worth reading.
        said = ""
        if check.quote:
            said = f' "{clipped(check.quote, _EVIDENCE_QUOTE, ellipsis="...")}" -'
        return claim(f"{_EVIDENCED[check.status]}{said} {text}", link, evidence)
    # A row with no link already says it cannot be sourced; checked or not,
    # there is nothing more to say about it.
    if check is None or check.status == "open" or link is None:
        return claim(text, link)
    return claim(f"couldn't verify: {text}", link)


def chase_rows(
    written: str, owns: Callable[[str], bool] | None
) -> tuple[list[tuple[str, tuple[str, ...]]], list[tuple[str, tuple[str, ...]]]]:
    """The chase list's live rows, split into what is owed TO him and what he
    owes: struck rows out (#188), his own rows by their owner column (#186).
    One reading, shared by the brief and the morning plan that checks the rows
    the brief will show (#187)."""
    owed: list[tuple[str, tuple[str, ...]]] = []
    owed_by: list[tuple[str, tuple[str, ...]]] = []
    for head, subs in read_rows(written, "Chase list"):
        if not is_struck(head):
            (owed_by if _owned_by_him(row_owner(head), owns) else owed).append((head, subs))
    return owed, owed_by


#: Public names for the two helpers `run` and `week_ahead` were re-spelling -
#: one bullet-from-State.md rule and one quote budget, held here.
line_from_state = _line_from_state
short = _short


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


#: Where a Slack record may carry its sender's name. `who` is the payload
#: contract's; the rest are what agents and the connector have handed over.
_SENDER_FIELDS = ("who", "from", "user_name", "username", "sender", "author")


def _sender(message: Mapping[str, Any], who: Callable[[str], str | None] | None) -> str:
    """The sender's name, or the directory's name for their Slack id, or
    "someone". Every overnight line read "someone" on 10/1 while each record
    carried the name under a field nothing read (#190)."""
    for field in _SENDER_FIELDS:
        if message.get(field):
            return str(message[field])
    user = message.get("user")
    if user and who is not None:
        named = who(str(user))
        if named:
            return named
    return "someone"


def _overnight_lines(
    now: datetime,
    principal: str,
    sources: Sources,
    read: Reader,
    who: Callable[[str], str | None] | None = None,
) -> list[str]:
    """Slack since 6pm yesterday, plus the Gemini notes that landed with it.

    Two steps on purpose, for both halves. Slack search resolves to whole days
    and Gmail's ``after:`` is a date, so each query over-fetches and the real
    cutoff is applied here. Dropping that second step is a whole extra *workday*
    in a 6:45am DM - and worse, the same notes re-reported every morning they
    stay inside the ``after:`` day, while the query still looks correct.
    """
    window = recipes.slack_overnight(now, mentioning=principal)
    lines: list[str] = []
    mentions = read("slack", lambda: list(sources.slack(window.query)), [])
    for message in mentions:
        if not _is_overnight(message, window):
            continue
        # His own message matches a search for his id - a DM he sent with a
        # link in it - and is not a mention of him (#183).
        if str(message.get("user") or "") == principal:
            continue
        # `or ""`: a file-only Slack message carries `"text": null`, and str(None)
        # is the word None, quoted as if he had said it.
        lines.append(
            claim(_sender(message, who), _link(message), quote=str(message.get("text") or ""))
        )

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
        lines.append(claim(who, _link(mail), quote=title or subject))
    # Nothing from slack on a working night that DID have meetings is how a
    # malformed call reads - "No results found", no error - and it renders as
    # a quiet night (#183). Said, not shown as silence.
    landed = any(line.startswith("- notes landed") for line in lines)
    working = now.astimezone(recipes.PACIFIC).weekday() < 5
    if not mentions and landed and working and "slack" not in read.unreachable:
        read.unreachable.append(
            "slack for sure - no mentions came back on a night notes landed, and a"
            " malformed search reads the same; re-run it if that looks wrong"
        )
    return lines
