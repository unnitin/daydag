"""What the LIVE sources say moved, for him to confirm (#134).

USING IT
    from daydag.movement import detect, open_items

    items = open_items(state=state_md, note=weekly_note, note_path=path)
    rows = detect(open_items=items, calendar=cal, slack=msgs, gmail=mail, now=now)
    rows[0].proposed        # "scheduled" | "discussed" - never "closed"
    rows[0].evidence[0].quote, rows[0].evidence[0].permalink

CONTRACTS
    1. PURE and TOTAL. No source of its own, no clock of its own, no write
       path, and nothing it is handed can make it raise - a connector returns
       whatever it returns, and a detector that raises takes the whole wrap
       down with it. `daydag.ingestion` is held to the same two for the same
       reason.
    2. Nothing it returns is a CLOSURE. `Movement.PROPOSALS` is the whole
       vocabulary and neither member asserts an item is done. This is #18's
       critical rule - "evidence of movement … surfaces it for confirmation,
       it never auto-closes" - lifted out of the chaser, because it is a
       property of the system and not of one loop.
    3. Every piece of evidence is VERBATIM and carries its own permalink where
       the source gave one (house rule 1). Nothing is paraphrased, because the
       row exists to be judged by him, not believed.
    4. Matching needs TWO distinctive terms in common. One is "the plan",
       which appears in every message he has ever received. The two failure
       directions are not equal: a miss costs a proposal he would have waved
       through, a false positive costs his trust in every row under it.
    5. Keyed on the item, so the same evening's second run proposes the same
       rows rather than a second copy of them.

WHY IT EXISTS
    Every loop that reported status read it out of the vault, and the vault
    records what he had time to write down. On 2026-09-15, a day with
    seventeen meetings, that was nothing:

        dont just look at weekly note, actually look at calendar, slack and
        email to see how much things have moved, i dont always get the time to
        move things in obsidian

    `eod_wrap` derived "what closed today" from `brief.closed_red_items` - the
    checkboxes he ticks himself - and so reported `0 closed` on a full day, a
    fact about his bookkeeping dressed as a fact about his week.

KNOWN LIMIT
    It does not know who spoke. Resolving a Slack author to a role token needs
    the people directory, which is why the proposal is "discussed" rather than
    "the owner replied" - the weaker claim is the one the evidence supports.
    Repo and Jira evidence is #18's half and is not read here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from daydag.brief import red_items
from daydag.state import read_section

#: Words that carry no identity. Everything he writes is about a plan, a team,
#: a list or the data, so overlap on one of these is overlap on nothing.
_STOPWORDS = frozenset(
    """
    about after also back been before both call come data does down each else
    even ever from give have here into just keep know like make many more most
    much must need next only over plan same somesuch take team than that them
    then there these they thing this time very want week well were what when
    where which while will with work would your
    """.split()
)

#: Four characters, because "the", "for" and "and" are noise and "PR", "ML"
#: and "AI" are ambiguous enough to behave like noise.
_MIN_TERM = 4

#: Two distinctive terms in common. Contract 4 is the reasoning.
_MIN_OVERLAP = 2

_WORD = re.compile(r"[a-z0-9][a-z0-9'-]*")

#: Checkbox syntax and the weekly note's ownership tags. `[^)]*` after the
#: keyword because they are written freehand: `(mine)`, `(mine w/ Gov-Lead)`,
#: `(tracking: VP-Data)`.
_TAGS = re.compile(r"\[[ xX]\]|\((?:mine|x-team|tracking)[^)]*\)", re.IGNORECASE)

#: Emphasis, stripped as CHARACTERS and never as a span.
#:
#: Deleting `*...*` as a span was greedy: on `**Deal Modeler** cutover *(mine)*`
#: it ate from the first asterisk to the last and returned the empty set, so a
#: bold-titled red item could never reach `_MIN_OVERLAP` and never produced a
#: proposal - silently. The live weekly note bolds every item title, which is
#: `weekly-planning`'s house format, so the detector was blind to precisely the
#: items it exists to track. It also ran over Slack evidence, where `*bold*` is
#: Slack's own markup.
_EMPHASIS = re.compile(r"[*_`~]+")

#: A bare date or number. `2026-09-10` survives `_WORD` as one token and is
#: shared by every item filed on the same day.
_NUMERIC = re.compile(r"^[\d-]+$")

#: The chase row's own bookkeeping. `- owner · ask · asked-on DATE · status
#: open` puts `asked-on`, `status` and `open` in EVERY row, which is two shared
#: terms before a single word of the ask is read - so one message saying
#: "status on that, still open" matched all four live chase items at once.
#: Stripped from the matching text; `OpenItem.text` keeps the whole row for
#: display, because what he reads should be the row he wrote.
_BOOKKEEPING = re.compile(
    r"·\s*(?:asked-on|due|last-activity|status|waiting since|snoozed-until)\b[^·]*",
    re.IGNORECASE,
)


def _terms(text: str) -> frozenset[str]:
    """The distinctive words in ``text``, lowercased.

    Tags come off before emphasis, so `*(mine w/ Gov-Lead)*` loses the tag
    while its parens are still there to bound it and the orphaned asterisks go
    with the emphasis pass.
    """
    cleaned = _BOOKKEEPING.sub(" ", str(text))
    cleaned = _EMPHASIS.sub("", _TAGS.sub(" ", cleaned)).casefold()
    return frozenset(
        word
        for word in _WORD.findall(cleaned)
        if len(word) >= _MIN_TERM and word not in _STOPWORDS and not _NUMERIC.match(word)
    )


@dataclass(frozen=True)
class OpenItem:
    """One thing that is open, and where it was read from.

    `source` is a vault path so a proposal about the item can cite the item -
    house rule 1 applies to the claim "this is open" exactly as it applies to
    the evidence against it.
    """

    key: str
    text: str
    owner: str
    source: str


@dataclass(frozen=True)
class Evidence:
    """One verbatim thing a live source said, and where to go and read it."""

    source: str
    quote: str
    permalink: str
    at: str


@dataclass(frozen=True)
class Movement:
    """An open item, what was found, and what is being PROPOSED about it."""

    #: The whole vocabulary. Contract 2: neither member says an item is done.
    #: A scheduled meeting is not a held one and a mention is not an answer.
    PROPOSALS = ("scheduled", "discussed")

    item: OpenItem
    evidence: tuple[Evidence, ...]
    proposed: str


#: A loop he has deliberately paused. `State.md` documents the convention
#: ("To pause instead, write `status: snoozed-until YYYY-MM-DD`"), and parked
#: is the chase list's own third state. Re-proposing either is re-asking a
#: question he has already answered.
_PAUSED = re.compile(r"snoozed-until|status:?\s*(?:parked|snoozed)", re.IGNORECASE)


def _key(text: str) -> str:
    """The identity of an item, for deduping it across the two stores.

    Built from `_terms` rather than from the raw line, because the same loop is
    written differently in each: `- eddie · fruits metadata list · asked-on …`
    in the chase list and `- [ ] 🔴 fruits metadata list` in the weekly note.
    Keying on the raw head made those two different items and the wrap asked
    him to confirm one thing twice.

    Two rows still survive when the wordings genuinely differ - an owner named
    in one and not the other is a real difference in the words. That is the
    cheap failure of the two: he can see a duplicate.
    """
    terms = _terms(text)
    return " ".join(sorted(terms)) if terms else " ".join(str(text).split())[:60].casefold()


def open_items(*, state: str, note: str, note_path: str) -> list[OpenItem]:
    """Everything open, from the chase list and the week's red items.

    Both stores, because he keeps open loops in both and neither one is
    complete. A struck chase row and a ticked red item are each his own answer
    already and are not returned - re-proposing something he has crossed off
    is the loudest possible way to prove his edits do not win.

    Top-level bullets only. An indented one is his comment on the item above
    it, which is the file's documented convention and not a second loop: the
    live State.md has 4 chase items under 22 bullets, and reading all 22 as
    open would propose movement against his own annotations.
    """
    items: list[OpenItem] = []
    for body in read_section(str(state or ""), "Chase list", top_level=True):
        if "~~" in body or _PAUSED.search(body):
            continue
        owner = body.split("·")[0] if "·" in body else ""
        items.append(
            OpenItem(
                key=_key(body),
                text=body,
                owner=re.sub(r"[*_`]", "", owner).strip(),
                source="DayDAG/State.md",
            )
        )
    # `red_items` already skips the ticked side and the triage legend, and
    # reads the note's own layout rather than a template (invariant 6).
    for _lineno, text in red_items(str(note or "")):
        items.append(OpenItem(key=_key(text), text=text, owner="", source=note_path))
    return _deduped(items)


def _deduped(items: list[OpenItem]) -> list[OpenItem]:
    """One row per loop, first store wins.

    He tracks the same loop in both stores routinely - a red item in the week's
    priorities and a chase row for the person who owes it - and reading both is
    deliberate, because neither is complete. Without this the wrap prints the
    same thing twice under `looks moved - confirm` and counts it twice in the
    header.

    Keyed on `_key`, which is the flattened head of the row, so the two
    wordings have to actually agree. They often will not, and a duplicate is
    the cheap failure here: he can see it.
    """
    seen: set[str] = set()
    out: list[OpenItem] = []
    for item in items:
        if item.key in seen:
            continue
        seen.add(item.key)
        out.append(item)
    return out


def _records(raw: Any) -> list[Mapping[str, Any]]:
    """Every mapping in ``raw``, and nothing else. Contract 1's totality: the
    payload is whatever a connector handed back, including `None` and a bare
    string where a list was promised."""
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, Mapping)):
        return []
    return [record for record in raw if isinstance(record, Mapping)]


def _text(record: Mapping[str, Any], *fields: str) -> str:
    return " ".join(str(record.get(f) or "") for f in fields).strip()


#: Per source: the field QUOTED, the fields matched on, where the link is, and
#: where the timestamp is. Quote and match are separate columns because they
#: answer different questions - a Gemini note matches on its subject and its
#: snippet together, but quoting the two concatenated produces a sentence that
#: appears nowhere in the message he is being sent to go and read, which is
#: contract 3 and house rule 1 broken in the one place they are load-bearing.
_SHAPES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...], str], ...] = (
    ("calendar", "summary", ("summary",), ("htmlLink", "permalink"), "start"),
    ("slack", "text", ("text",), ("permalink",), "ts"),
    ("gmail", "subject", ("subject", "snippet"), ("permalink", "link"), "date"),
)


def _candidates(
    calendar: Any, slack: Any, gmail: Any
) -> list[tuple[str, Evidence, frozenset[str]]]:
    """Every live record, reduced to (source, evidence, its terms).

    One pass over each payload rather than one per open item: the terms are
    the expensive part and they do not depend on which item is being matched.

    A record with no permalink is DROPPED, which is what `run._prep` already
    does to a linkless message and for the same reason. The alternative was
    keeping it and letting the wrap cite the vault path the item came from
    instead, which renders a Slack quote as though `DayDAG/State.md` said it -
    a citation that points at the wrong document is worse than no row.
    """
    out: list[tuple[str, Evidence, frozenset[str]]] = []
    for payload, (source, quote_field, match_fields, link_fields, at_field) in zip(
        (calendar, slack, gmail), _SHAPES, strict=True
    ):
        for record in _records(payload):
            quote = _text(record, quote_field)
            link = next((str(record[f]) for f in link_fields if record.get(f)), "")
            if not quote or not link:
                continue
            out.append(
                (
                    source,
                    Evidence(source, quote, link, str(record.get(at_field) or "")),
                    _terms(_text(record, *match_fields)),
                )
            )
    return out


def detect(
    *,
    open_items: Sequence[OpenItem],
    calendar: Any = (),
    slack: Any = (),
    gmail: Any = (),
    now: datetime | None = None,
) -> list[Movement]:
    """One row per open item with evidence against it, in item order.

    ``now`` is accepted and unused: the windows are the caller's, bounded by
    the recipe that fetched them, and a module that re-derived "today" here
    would disagree with the payload it was handed. It stays in the signature
    because every consumer has one and leaving it out invites a caller to
    filter by date itself, which is the duplication this exists to stop.

    Args:
        open_items: from :func:`open_items`. Anything that is not an
            ``OpenItem`` is skipped rather than coerced - contract 1.
    """
    candidates = _candidates(calendar, slack, gmail)
    rows: list[Movement] = []
    for item in open_items:
        if not isinstance(item, OpenItem):
            continue
        wanted = _terms(item.text)
        hits = [
            (source, evidence)
            for source, evidence, terms in candidates
            if len(wanted & terms) >= _MIN_OVERLAP
        ]
        if not hits:
            continue
        # Calendar wins the label: a room that now exists is a stronger and
        # more checkable statement than a mention, and it is the one he named
        # ("you can confirm that yourself through calendar").
        proposed = "scheduled" if any(source == "calendar" for source, _ in hits) else "discussed"
        rows.append(Movement(item=item, evidence=tuple(e for _, e in hits), proposed=proposed))
    return rows
