"""The `DayDAG/` vault folder: State.md as a document, the sensitivity gate, the writer.

USING IT
    folder = StateFolder.create(root)                    # DayDAG/, idempotent
    folder.update_state(chase=..., watch=..., notes_gaps=...)   # APPENDS, atomically
    folder.add_decision("draft nudge to VP-Data? · status: open")  # APPENDS
    doc = StateDoc.parse(folder.read_state())
    for block in doc.blocks_in("Chase list"):            # nested headings included
        block.body, block.link, block.struck             # the line, its permalink, ~~done~~

CONTRACTS
    1. `parse` is TOTAL and `render` its exact inverse for ANY string, split on
       "\n" only. A writer that cannot reproduce the file it read refuses
       (`StateNotWritable`) and writes nothing (#130).
    2. `State.md` is the RECORD and the writer APPENDS. Every line a run did
       not derive comes back byte-for-byte; a derived item already present -
       under any heading, struck or open - is not filed twice.
    3. One gate. `chase`, `watch` and `notes_gaps` pass through `_visible`
       before anything renders, so `sensitivity == "private"` cannot be wired
       to two lists and forgotten on the third (#63, #61, #105).
    4. One reader. `Block` is the unit every consumer reads - the brief, the
       week-ahead and the chaser - and `Block.link` is found anywhere in the
       block, because his citation sits in the sub-bullet under the line.
    5. Writes go through `vault`: temp file, fsync, `os.replace`, and a read
       that refuses an iCloud placeholder rather than treating it as empty.
    6. `Decisions.md` is appended, never regenerated, and the answer is the
       line's own `status:` field (D-5, #62). A private line is refused at the
       append (#124) - house rule 7.

WHY IT EXISTS
    The folder is markdown a human corrects by hand, and a hand edit is an
    event that wins over anything derived. Everything sensitive lives in
    `eventlog`, outside the vault.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from daydag import vault
from daydag.recipes import has

# WARN is the sanctioned glyph (plain U+26A0, not its emoji-presentation twin);
# `daydag.voice` is the register authority, and a chase item that cannot be
# rendered in full still has to stay inside house voice.
from daydag.voice import WARN

README = """# DayDAG

Files in this folder are maintained by the DayDAG agent. Correct them freely -
a hand edit is an event, and it wins over anything the agent derived.

| File | Who writes it | Cadence |
|---|---|---|
| `State.md` | both | appended to, never rewritten. Your edits win; sub-bullets are never reflowed |
| `Decisions.md` | both | appended, never regenerated. Answer by writing next to a line |
| `Watchlist.md` | you | config: repos, Jira projects, channels |
| `Proposals/` | the agent | proposed diffs awaiting a yes |
| `Archive/` | the agent | pre-cutover snapshots |

History, metrics, the meeting ledger and anything sensitive live in the event
log outside this vault, not here.
"""

# --------------------------------------------------------------------------
# reading a projection back - State.md is hand-edited, so anything that reads
# it lives here beside the writer rather than being reinvented per caller.
# --------------------------------------------------------------------------

_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<name>.+?)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(?P<body>\S.*?)\s*$")
_MD_LINK = re.compile(r"\[(?P<label>[^\]]*)\]\((?P<url>[^)\s]+)\)")
#: `)` excluded so a hand-written "(see https://x)" does not capture the
#: bracket into the link and strand its opener in the text.
_BARE_URL = re.compile(r"<?(?P<url>https?://[^\s>)]+)>?")


def split_link(body: str) -> tuple[str, str | None]:
    """A hand-written line's text and the link in it, if there is one.

    Both forms appear in a file a human edits: a markdown link, and a URL pasted
    bare. The link is pulled out so the line can be re-rendered as a claim like
    any other - otherwise a bare URL would read as unsourced to a caller's own
    evidence check.
    """
    link = _MD_LINK.search(body)
    if link:
        return (body[: link.start()] + link["label"] + body[link.end() :]).strip(" -·"), link["url"]
    bare = _BARE_URL.search(body)
    if bare:
        # Drop a bracket left wrapping nothing. Excluding `)` from the URL kept
        # it out of the link but left "(see )" behind - half a fix reads worse
        # than none, because it looks deliberate.
        head, tail = body[: bare.start()], body[bare.end() :]
        if head.rstrip().endswith("(") and tail.lstrip().startswith(")"):
            head, tail = head.rstrip()[:-1], tail.lstrip()[1:]
        # Collapse the gap the removal leaves: "see  for detail" reads as a
        # typo in a brief, which spends the reader's trust on nothing.
        return re.sub(r"\s{2,}", " ", head + tail).strip(" -·"), bare["url"]
    return body.strip(), None


#: Every field a chase item carries beyond `sensitivity` (CLAUDE.md section 8's
#: schema, plus `key` - not in that schema, but what a nudge or a piece of repo
#: evidence keys back onto, and present on every payload the log has recorded).
_CHASE_FIELDS: tuple[str, ...] = (
    "key",
    "owner",
    "ask",
    "quote",
    "permalink",
    "asked_on",
    "last_activity",
    "status",
)


@dataclass(frozen=True)
class ChaseItem(Mapping[str, Any]):
    """The one chase-item shape `EventLog` and `update_state` are both held to.

    USING IT
        item = ChaseItem.from_payload(payload, sensitivity=sensitivity)
        item.owner; item["owner"]; item.get("owner")   # all three work
        item.has_owner_or_ask                          # False -> both missing

    CONTRACTS
        1. `key` is the only field every chase item is guaranteed to carry - a
           payload recorded with nothing else still builds one.
        2. Neither `owner` nor `ask` being present does not raise. It is read
           by `update_state` as `has_owner_or_ask is False`, which renders a
           named warning line instead of a bare bullet or a crash (guardrail
           6's "degrade visibly" - not the same failure as one of the two
           being present, which still renders).
        3. Mapping-shaped (`__getitem__`, `.get`, `dict(item)`) so it is a
           drop-in wherever a chase item was already a bare dict - every
           existing caller on either side of the log/vault seam.

    WHY IT EXISTS
        Issue #63: the log returned whatever a caller recorded and
        `update_state` assumed `owner` and `ask` were in it, so a key-only
        payload rendered as a bare `- ?` in `State.md`. One shape, read the
        same way on both sides of the log/vault seam, is what keeps the two
        from disagreeing again the next time either module changes.
    """

    key: str = "?"
    owner: str = ""
    ask: str = ""
    quote: str = ""
    permalink: str = ""
    asked_on: str = ""
    last_activity: str = ""
    status: str = "open"
    sensitivity: str = "normal"
    #: Everything else the caller recorded. Whitelisting the fields above
    #: dropped `day` - which `loop_opened` records on every call - and made
    #: contract 3's "drop-in" false: `item["day"]` became a KeyError. Carried
    #: apart, not merged, so a payload cannot overwrite a guaranteed field.
    extra: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(
        cls, payload: Mapping[str, Any], *, sensitivity: str | None = None
    ) -> ChaseItem:
        """Build one from whatever a caller recorded - a partial payload included.

        Never raises: a chase item is read out of a log a human can also
        write rows into by hand, and a partial one has to degrade to a named
        warning (`has_owner_or_ask`), not take the render down. Accepts
        another `ChaseItem` as ``payload`` too, since it is itself a mapping -
        re-normalizing one is a no-op.
        """
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, Mapping):
            # "Never raises" holds for a hand-written row too: valid JSON that
            # is not an object (`null`, a list, a scalar) took `update_state`
            # down with an AttributeError - undoing, one layer up, the torn-row
            # tolerance `eventlog.recorded` exists for.
            return cls(sensitivity=sensitivity)
        kwargs = {name: payload[name] for name in _CHASE_FIELDS if payload.get(name)}
        extra = {
            name: value
            for name, value in payload.items()
            if name not in _CHASE_FIELDS and name != "sensitivity"
        }
        # An explicit ``sensitivity`` is the LOG'S COLUMN and outranks anything
        # the payload claims - the column is the trusted fact, the payload is
        # whatever was written into the row. Omitted means there is no column to
        # trust (`update_state` is handed a bare dict), so the payload wins.
        resolved = sensitivity if sensitivity is not None else payload.get("sensitivity", "normal")
        return cls(sensitivity=str(resolved), extra=extra, **kwargs)

    @property
    def has_owner_or_ask(self) -> bool:
        """Whether there is anything real to render.

        `recipes.has` rather than a hand-rolled truthiness check - the same
        "at least one of these alternatives" rule that keeps a Gmail
        metadata-only result from passing as a match. An item with neither
        field is not half-missing data, it is no data.
        """
        return has(self, "owner", "ask")

    def __getitem__(self, key: str) -> Any:
        if key in _CHASE_FIELDS or key == "sensitivity":
            return getattr(self, key)
        return self.extra[key]

    def __iter__(self):
        return iter((*_CHASE_FIELDS, "sensitivity", *self.extra))

    def __len__(self) -> int:
        return len(_CHASE_FIELDS) + 1 + len(self.extra)


def _as_chase_item(raw: ChaseItem | Mapping[str, Any]) -> ChaseItem:
    """Coerce whatever a caller passed `update_state` into the one shape (#63)."""
    return raw if isinstance(raw, ChaseItem) else ChaseItem.from_payload(raw)


@dataclass(frozen=True)
class NotesGap:
    """One meeting with no note found - `notes_gaps`' shape (#61).

    CONTRACTS
        1. `title` is what `update_state` prints. A meeting's own title can be
           the sensitive fact - a comp conversation, an exit interview - so it
           carries `sensitivity` exactly like `chase` and `watch` already do.
        2. `from_value` also accepts a bare string, read as
           ``sensitivity="normal"`` - `ledger.notes_gaps()` still returns
           ``list[str]``, and no existing caller has to change to keep
           working.

    WHY IT EXISTS
        Issue #61 / guardrail GAP 3: `notes_gaps` was bare strings with no tag
        to read, so a sensitive meeting title could only be withheld by its
        caller pre-filtering. The boundary has to hold even when a caller
        forgets, which is why `chase` and `watch` are filtered inside
        `update_state` - and why this shape exists, so gaps can be too.
    """

    title: str
    sensitivity: str = "normal"

    @classmethod
    def from_value(cls, value: str | Mapping[str, Any] | NotesGap) -> NotesGap:
        """A `NotesGap` from one of its own, a bare string, or a dict.

        The already-a-`NotesGap` case is FIRST and is not a convenience: this
        class carries a `get()` but does not subclass `Mapping`, so without it
        an instance fell through to the bare-string branch, took the dataclass
        repr as its title and reset `sensitivity` to "normal" - laundering a
        private gap into a visible one. `ChaseItem` never had the bug because
        it does subclass `Mapping`: one coercion, two shapes, one guard.
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(
                title=str(value.get("title", "")),
                sensitivity=str(value.get("sensitivity", "normal")),
            )
        return cls(title=str(value))

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


def _is_private(item: Any) -> bool:
    """Whether ``item`` is tagged ``sensitivity: private``.

    The one place every vault-list filter reads from - `_visible` is what
    calls this, not `update_state`'s three loops individually, which is the
    difference between a check that can be forgotten on a third list and one
    that cannot.
    """
    return item.get("sensitivity") == "private"


def _visible(items: Iterable[Any]) -> list[Any]:
    """Every item in ``items`` that is not private.

    `update_state`'s one gate for `chase`, `watch` and `notes_gaps` alike. A
    private carry-forward once slipped past because `watch` had no version of
    the check while `chase` did, and before #61 `notes_gaps` had no field to
    check. One function applied three times makes "forgotten on the third
    list" a contradiction rather than a recurring incident.
    """
    return [item for item in items if not _is_private(item)]


# --------------------------------------------------------------------------
# State.md as a document - parsed, appended to, re-rendered (#130)
#
# The writer used to render the file from what one run derived. It is the
# record now, so everything here is built around one property: parse then
# render returns the input byte-for-byte, whatever is in it.
# --------------------------------------------------------------------------

#: A bullet at column zero - the start of a new item. Indented bullets are the
#: human's sub-bullets and belong to the item above them; `> - ...` inside the
#: conventions blockquote is not a bullet at all.
_TOP_BULLET = re.compile(r"^[-*]\s+\S")

#: The bullet marker only. `str.lstrip("-* ")` also eats the `**` opening a
#: bold owner token, so `**gov-lead**` read back as `gov-lead**`.
_MARKER = re.compile(r"^\s*[-*]\s+")

#: Strike marks, dropped from a body but read by `struck`.
_STRIKE = re.compile(r"~~")

#: Emphasis and strike markers, dropped before two lines are compared. He
#: writes `- **gov-lead** · ...`; the log records `gov-lead`.
_DECORATION = re.compile(r"[*_`~]+")


class PrivateDecision(Exception):
    """A decision line the sensitivity classifier marks private was refused
    at the append (#124). Not a `ValueError`, for the same reason as
    `SensitivityRequired`."""


class StateNotWritable(Exception):
    """`State.md` could not be reproduced from its own parse, so it was not
    written. Raised instead of writing a best-effort version of a file shape
    nobody anticipated - #130 is what a best-effort rewrite costs."""


#: The character classes an identifier runs on. `CDI-91` is one token, so
#: finding `CDI-9` inside it is not finding `CDI-9`.
_IDENT_TAIL = re.compile(r"[0-9a-z-]$")


def _found(needle: str, haystack: str) -> bool:
    """``needle`` in ``haystack``, at a token boundary if it ends like an id.

    Plain `in` is right for a phrase - he rewords "the compute plan" freely -
    and wrong for a key, because ticket keys are prefixes of each other and a
    false match drops a real ask silently.
    """
    if not _IDENT_TAIL.search(needle):
        return needle in haystack
    at = haystack.find(needle)
    while at != -1:
        after = haystack[at + len(needle) : at + len(needle) + 1]
        if not after or not _IDENT_TAIL.match(after):
            return True
        at = haystack.find(needle, at + 1)
    return False


def _flatten(text: str) -> str:
    """``text`` reduced to what two versions of the same item share.

    Decoration, the bullet marker and run of whitespace all vary between the
    way he writes a row and the way the log records it. What survives is the
    words.
    """
    return " ".join(_DECORATION.sub("", text).casefold().split()).strip(" -·")


@dataclass(frozen=True)
class Block:
    """One top-level bullet and every line indented under it, verbatim.

    CONTRACTS
        1. `lines` is exactly what was read, including trailing blank lines.
           Rendering a block is `"\\n".join(lines)` and nothing else - that is
           what makes his sub-bullets un-reflowable rather than merely
           un-reflowed.
        2. `matches` reads the FIRST line only. A sub-bullet quoting an ask is
           context, not a second copy of it.
    """

    lines: tuple[str, ...]

    @property
    def body(self) -> str:
        """The bullet line with its marker, strike marks and links stripped.

        What a push prints for this block. `split_link` drops the link and
        keeps the label, so `[slack · his DM](url)` reads as `slack · his DM`.
        """
        text, _ = split_link(_MARKER.sub("", self.head))
        return _STRIKE.sub("", text).strip(" -·")

    @property
    def link(self) -> str | None:
        """The first URL anywhere in the block - head line or sub-bullet.

        His citation sits UNDER the line (`\t- [slack](url)`), so a reader
        that looked at the head alone rendered every hand-written chase item
        as "couldn't source this one" while the link sat one line below.
        """
        for line in self.lines:
            _, link = split_link(line)
            if link:
                return link
        return None

    @property
    def struck(self) -> bool:
        """Whether he crossed the line off - `~~…~~ ✓` is the audit trail."""
        return _MARKER.sub("", self.head).startswith("~~")

    @property
    def head(self) -> str:
        """The bullet line itself - the first line with anything on it.

        Not `lines[0]`: `append` prepends a blank separator when the previous
        block does not end in one, so `lines[0]` was `""` for exactly those
        blocks and `matches` never saw the bullet - which filed the same ask
        twice inside one run.
        """
        return next((line for line in self.lines if line.strip()), "")

    def matches(self, *needles: str) -> bool:
        """Whether the bullet line contains any of ``needles``.

        Substring, after flattening, because he rewords a row when he files it
        by hand. The two failure directions are not equal: a missed match
        files a duplicate, which he can see and delete, while a false match
        silently drops a real ask.

        A needle ending identifier-shaped is matched at a BOUNDARY, not as a
        bare substring: ticket keys nest, so `CDI-9` read as already-filed
        inside an existing `CDI-91` line and dropped the ask.
        """
        flat = _flatten(self.head)
        return any(n and _found(_flatten(n), flat) for n in needles)

    def equals(self, text: str) -> bool:
        """Whether the bullet IS ``text``, rather than containing it.

        For the lists whose entries are short proper nouns - a watch item, a
        notes gap titled `1:1` or `Standup`. A substring test there is dropped
        by any line anywhere in the file that happens to contain the word, and
        the gap is then silently never reported.
        """
        return _flatten(self.head).strip(" -·") == _flatten(text).strip(" -·")


@dataclass(frozen=True)
class StateSection:
    """A heading and its blocks. ``heading`` is ``None`` for the preamble -
    everything above the first heading, which in practice is the title line
    and the conventions blockquote."""

    heading: str | None
    prologue: tuple[str, ...]
    blocks: tuple[Block, ...]

    @property
    def name(self) -> str:
        match = _HEADING.match(self.heading or "")
        return match["name"] if match else ""

    @property
    def lines(self) -> list[str]:
        out = [self.heading] if self.heading is not None else []
        out += list(self.prologue)
        for block in self.blocks:
            out += list(block.lines)
        return out


@dataclass
class StateDoc:
    """``State.md``, parsed into sections of verbatim blocks.

    USING IT
        doc = StateDoc.parse(path.read_text())
        doc.render() == path.read_text()        # always
        doc.contains("the compute plan")        # anywhere, any heading
        doc.append("Chase list", ["- vp-data · the compute plan", ""])

    CONTRACTS
        1. `parse` is TOTAL and `render` is its exact inverse, for ANY string.
           A line the parser has no model for is carried in whichever block or
           prologue it landed in, so it comes back unchanged. Nothing is
           dropped and nothing is normalised - not indentation, not blank
           runs, not the trailing newline.

           Split on `"\\n"` and nothing else. `str.splitlines()` also breaks
           on `\\r`, `\\x0b`, `\\x0c`, `\\x1c`, `\\x85`, `\\u2028` and
           `\\u2029`, which paste in from a browser or a Slack copy, while
           `render` rejoins with `\\n` - so a file carrying one did not survive
           its own parse. House rule 1 makes a verbatim quote mandatory, so one
           quoted line separator froze `State.md` permanently.
        2. `append` only ever adds lines. There is no method that removes or
           edits a block, which is the structural half of house rule 3: a
           caller cannot clobber what it did not derive, because no call does.
        3. `contains` searches EVERY section. An item he has struck under
           `Done` is still present, so re-deriving it appends nothing.

    KNOWN LIMIT
        A `- ` line inside a fenced code block reads as a new block. The file
        has never held one, the round trip is unaffected either way, and the
        only consequence is where an appended item lands relative to the
        fence.
    """

    sections: list[StateSection]

    @classmethod
    def parse(cls, text: str) -> StateDoc:
        sections: list[StateSection] = []
        heading: str | None = None
        prologue: list[str] = []
        blocks: list[Block] = []
        block: list[str] | None = None

        def close_block() -> None:
            nonlocal block
            if block is not None:
                blocks.append(Block(tuple(block)))
                block = None

        def close_section() -> None:
            nonlocal prologue, blocks
            close_block()
            if heading is not None or prologue or blocks:
                sections.append(StateSection(heading, tuple(prologue), tuple(blocks)))
            prologue, blocks = [], []

        for line in text.split("\n"):
            if _HEADING.match(line):
                close_section()
                heading = line
                continue
            if _TOP_BULLET.match(line):
                close_block()
                block = [line]
                continue
            if block is not None:
                block.append(line)
            else:
                prologue.append(line)
        close_section()
        return cls(sections)

    def render(self) -> str:
        """The document, byte for byte as it was parsed.

        `"\\n".join` is the exact inverse of `split("\\n")`, including the
        empty final element a trailing newline produces - which is why there
        is no `final_newline` flag to get wrong.
        """
        lines: list[str] = []
        for section in self.sections:
            lines += section.lines
        return "\n".join(lines)

    def section(self, name: str) -> StateSection | None:
        wanted = name.casefold()
        for section in self.sections:
            if section.name.casefold() == wanted:
                return section
        return None

    def blocks_in(self, name: str) -> list[Block]:
        """Every block under the heading ``name``, including nested headings.

        A `### promises you made` under `## Owed by you`, or a hand-added
        `### Snoozed` under `## Chase list`, belongs to the section above it:
        State.md is hand-edited and nesting is normal. The section ends at the
        next heading of the same or a higher level.
        """
        wanted = name.casefold()
        found: list[Block] = []
        level = 0
        collecting = False
        for section in self.sections:
            heading = section.heading or ""
            depth = len(heading) - len(heading.lstrip("#"))
            if section.name.casefold() == wanted:
                collecting, level = True, depth
            elif collecting and depth <= level:
                collecting = False
            if collecting:
                found.extend(section.blocks)
        return found

    def contains(self, *needles: str) -> bool:
        """Whether any block in any section is already about this.

        Any section on purpose (contract 3). A struck row under `Done` and an
        open one under `Chase list` both mean "he knows about it", and
        re-filing either is the same wrong answer.
        """
        return any(block.matches(*needles) for section in self.sections for block in section.blocks)

    def is_line(self, text: str, *, section: str) -> bool:
        """Whether a bullet in ``section`` IS ``text``, not merely contains it.

        Two narrowings from `contains`, both for the short-phrase lists:

        * whole line, because `contains("1:1")` is true of almost any file -
          "prep for the 1:1 with the CFO" is not a duplicate of the gap `1:1`;
        * one section, because a watch item named `1:1` is not the same fact as
          a notes gap named `1:1`, and cross-section suppression would drop
          whichever was written second.
        """
        found = self.section(section)
        return any(block.equals(text) for block in found.blocks) if found else False

    def append(self, name: str, lines: list[str]) -> None:
        """Add ``lines`` as a new block at the end of the section ``name``.

        The section is created if it is missing - after `Watch items` if that
        exists, so a generated section does not land under his run log.
        """
        section = self.section(name)
        if section is None:
            section = StateSection(f"## {name}", ("",), ())
            anchor = self.section("Watch items")
            at = self.sections.index(anchor) + 1 if anchor is not None else len(self.sections)
            self.sections.insert(at, section)
        tail = section.blocks[-1].lines[-1] if section.blocks else (section.prologue or ("",))[-1]
        # A blank line between items, but only if there is not one already:
        # blocks carry their own trailing blanks, so the common case needs
        # nothing and the end-of-file case needs one.
        body = lines if tail.strip() == "" else ["", *lines]
        at = self.sections.index(section)
        self.sections[at] = StateSection(
            section.heading, section.prologue, (*section.blocks, Block(tuple(body)))
        )


#: A fresh `State.md`. The conventions block is his, taken verbatim from how
#: he used the file on 2026-09-15 - it is the contract between him and the
#: writer, so a new install starts with it rather than learning it twice.
_EMPTY_STATE = """# State

> **How to talk back to me in this file.** Your words win over anything I
> derived, and I read this before every loop.
>
> - **Comments go in sub-bullets** under the item, indented one tab. I never
>   rewrite or reflow them.
> - **`@claude:` means act on it.** A bare sub-bullet is context I read and
>   leave alone.
> - **Cross something off by striking it** - `~~…~~ ✓`. Never delete the line;
>   the strike is the audit trail, and it is also what stops me re-deriving
>   the item tomorrow.

## Chase list

## Watch items
"""

#: Shortest needle worth matching on. Below this a substring test stops
#: identifying an item and starts colliding with unrelated ones - and a false
#: match silently drops a real ask, which is the expensive direction.
_MIN_ASK = 8
_MIN_KEY = 4


def _needles_for(item: ChaseItem) -> tuple[str, ...]:
    """What to look for in the file before filing ``item`` as new.

    The ask and the key, never the owner alone: he has four open items with
    one owner, and matching on the owner would read every one of them as
    already filed.

    Returns ``()`` when neither clears its floor; the caller falls back to the
    rendered bullet itself. Without that fallback `contains(*())` is vacuously
    False and the item is appended on EVERY run - four loops a day, four copies
    a day, unbounded, in the one file this whole change exists to protect.
    """
    return tuple(
        n
        for n, floor in ((item.ask, _MIN_ASK), (item.key, _MIN_KEY))
        if n and len(_flatten(n)) >= floor
    )


def _render_chase(item: ChaseItem) -> list[str]:
    """``item`` as the block that gets appended, plus its trailing blank.

    Every field `ChaseItem` carries, not the two the old renderer printed.
    House rule 1 wants the verbatim quote and the permalink on the row, and
    the soak's second edit asked for the link by name: "for each item please
    link it back to either slack or email or notes to provide broader context
    for what i am chasing".
    """
    if not item.has_owner_or_ask:
        return [f"- {WARN} chase item {item.key} has no owner or ask recorded", ""]
    head = " · ".join(
        part
        for part in (
            item.owner or "?",
            item.ask,
            f"asked-on {item.asked_on}" if item.asked_on else "",
            f"last-activity {item.last_activity}" if item.last_activity else "",
            f"status {item.status}" if item.status else "",
        )
        if part
    )
    lines = [f"- {head}"]
    if item.quote:
        lines.append(f'\t- *"{item.quote}"*')
    if item.permalink:
        lines.append(f"\t- [source]({item.permalink})")
    return [*lines, ""]


class StateFolder:
    """The ``DayDAG/`` folder: four files and two directories."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def state_path(self) -> Path:
        return self.root / "State.md"

    @property
    def decisions_path(self) -> Path:
        return self.root / "Decisions.md"

    @property
    def watchlist_path(self) -> Path:
        return self.root / "Watchlist.md"

    @classmethod
    def create(cls, root: Path) -> StateFolder:
        """Lay out the folder. Existing files are left exactly as they are."""
        folder = cls(Path(root))
        folder.root.mkdir(parents=True, exist_ok=True)
        (folder.root / "Proposals").mkdir(exist_ok=True)
        (folder.root / "Archive").mkdir(exist_ok=True)
        for path, body in (
            (folder.root / "README.md", README),
            (folder.state_path, _EMPTY_STATE),
            (folder.decisions_path, "# Pending decisions\n\n"),
            (folder.watchlist_path, "# Watchlist\n\n## repos\n\n## jira\n\n## channels\n"),
        ):
            if not path.exists():
                vault.atomic_write(path, body.encode("utf-8"))
        return folder

    # -- State.md is the record; the writer appends to it (#130) ----------

    def update_state(
        self,
        chase: Iterable[ChaseItem | Mapping[str, Any]] = (),
        watch: Iterable[Mapping[str, Any]] = (),
        notes_gaps: Iterable[str | Mapping[str, Any]] = (),
        run_lines: Iterable[str] = (),
    ) -> None:
        """Append anything derived that is not already in ``State.md``.

        ``run_lines`` are the run log's own projection lines (SPEC 7: one per
        run, free text withheld), appended under `## Run log` - the section
        he was writing by hand at the end of every run.

        Nothing is removed, reordered or reflowed. A run that derived nothing
        writes nothing at all - which is the whole of #130: on 2026-09-14 an
        `eod` run that derived nothing rendered exactly that over four
        hand-written chase items and six run-log lines.

        ``chase``, ``watch`` and ``notes_gaps`` all pass through ``_visible``
        first; appending rather than replacing must not route around that gate.
        ``chase`` is coerced to :class:`ChaseItem` and ``notes_gaps`` to
        :class:`NotesGap` whether a caller passes one already or a bare dict
        (#63, #61). An item with neither ``owner`` nor ``ask`` degrades to a
        named warning line rather than the ``- ?`` it used to render silently.

        Raises:
            StateNotWritable: the file could not be reproduced from its own
                parse. Nothing is written. A shape the parser cannot model is
                worth a skipped update and a complaint; it is not worth a
                best-effort rewrite of the file this method exists to protect.
        """
        before = self.read_state() if self.state_path.exists() else _EMPTY_STATE
        doc = StateDoc.parse(before)
        if doc.render() != before:
            raise StateNotWritable(
                f"{self.state_path} does not survive its own parse, so it was left alone. "
                "Nothing was written."
            )

        for item in _visible(_as_chase_item(raw) for raw in chase):
            lines = _render_chase(item)
            # The rendered bullet is the LAST-RESORT needle, for an ask too
            # short to identify and a degraded row that is only a warning: run
            # two renders the same line, so run two recognises it. Without it
            # `contains()` on an empty tuple is vacuously False and the row is
            # appended again every loop, forever.
            if doc.contains(*(_needles_for(item) or (lines[0],))):
                continue
            doc.append("Chase list", lines)
        for item in _visible(watch):
            what = str(item.get("what", ""))
            # `is_line` and not `contains` - see `is_line`. A watch item is a
            # short phrase, and a substring test is satisfied by any line
            # anywhere in the file, which drops the item silently.
            if not what or doc.is_line(what, section="Watch items"):
                continue
            doc.append("Watch items", [f"- {what}", ""])
        for gap in _visible(NotesGap.from_value(raw) for raw in notes_gaps):
            # Same as `watch`, and more exposed: a meeting title is routinely
            # `1:1` or `Standup`, which a substring test finds everywhere.
            if not gap.title or doc.is_line(gap.title, section="Notes gaps"):
                continue
            doc.append("Notes gaps", [f"- {gap.title}", ""])
        for line in run_lines:
            text = str(line).strip()
            if not text or doc.is_line(text, section="Run log"):
                continue
            doc.append("Run log", [f"- {text}"])

        after = doc.render()
        if after == before:
            # A no-op run must not churn the file, the archive, or the iCloud
            # sync that carries both to his other devices.
            return
        if self.state_path.exists():
            (self.root / "Archive").mkdir(exist_ok=True)
            vault.atomic_write(self.root / "Archive" / "State.md.bak", before.encode("utf-8"))
        vault.atomic_write(self.state_path, after.encode("utf-8"))

    def read_state(self) -> str:
        """State.md, refusing an iCloud placeholder rather than reading it as empty.

        Reading a placeholder as "" is the one failure that makes a writer
        delete content it thought was absent (`vault` contract 3).
        """
        return vault.read_materialised(self.state_path).decode("utf-8")

    def add_decision(self, line: str) -> None:
        """Append one pending decision to ``Decisions.md``, never regenerating it.

        The answer is written by hand on the same line as ``status: <answer>``
        (D-5, #62) - the line is his to edit, and `closure` reads the status
        back. Nothing here parses an answer, so a decision cannot self-answer
        and a hand edit cannot be overwritten.

        A line the classifier marks private is refused (#124): personnel, comp
        and M&A go to the DM only (house rule 7), and `Decisions.md` syncs to
        every device he owns.
        """
        if classify_sensitivity(line) == "private":
            raise PrivateDecision(
                "that decision reads as personnel / comp / M&A - it goes to the DM, "
                "not to a synced vault file (house rule 7)"
            )
        with self.decisions_path.open("a", encoding="utf-8") as handle:
            handle.write(f"- {line.strip()}\n")


#: Kinds `chase_items` projects into `State.md`. Recording one without an
#: explicit, well-formed sensitivity is refused - `eventlog` contract 2.
#: Meeting rows are not here: their titles reach `State.md` through the
#: ledger's `notes_gaps`, and `run._project` classifies each on the way out.
VAULT_BOUND = frozenset({"loop_opened", "carry_forward"})

#: House rule 7's categories, as the words that carry them. A FLOOR, not a
#: ceiling: matching any of these makes an item private; matching none proves
#: nothing, which is why a DM origin is decisive on its own - that is where
#: these conversations actually happen. Extend it, never narrow it.
_SENSITIVE_TERMS = re.compile(
    r"\b(?:"
    r"salar(?:y|ies)|comp(?:ensation)?|pay(?:\s*(?:band|rise|raise|cut|bump))|"
    r"equity|stock\s*(?:options?|grants?)|rsus?|options?\s*grants?|bonus(?:es)?|"
    r"pay\s*raise|offer\s*letters?|relocation|severance|"
    r"performance\s*(?:plan|review|improvement)|exit\s*interview|notice\s*period|"
    r"terminat(?:e|ed|ing|ion)|fir(?:e|ed|ing)\s+(?:him|her|them|someone)|let\s+go|"
    r"layoffs?|laid\s+off|resign(?:ation|ed|ing|s)?|headcount|"
    r"promot(?:e|ed|ing|ion|ions)|demot(?:e|ed|ing|ion|ions)|visa|immigration|"
    r"medical|leave\s+of\s+absence|acqui(?:re|red|ring|sition|sitions)|mergers?|"
    r"m(?:&|&amp;)a|due\s+diligence|term\s+sheets?|valuation|investors?|"
    r"board\s+(?:deck|meeting)"
    r")\b",
    re.IGNORECASE,
)
#: Case matters for one token: `PIP` is a performance plan, `pip` installs
#: packages. The floor above is case-insensitive, so this one is checked
#: separately, as written.
_SENSITIVE_ACRONYMS = re.compile(r"\bPIP\b")
#: Tokens deliberately NOT in the floor, with the false positive each caused
#: on real backfill text: lowercase `pip` (pip install), bare `raise` (Python), bare
#: `stock` (stock photos, in stock), `\d{2,3}k` (200k rows). A team that
#: writes code all day trips those on every render; the phrases that carry
#: the meaning - "pay raise", "stock options", "performance improvement" -
#: are kept instead.

#: Slack's names for a DM (`im`) and a group DM (`mpim`), plus the plain
#: words a shaper is likely to write instead. Compared casefolded.
_DM_ORIGINS = frozenset({"im", "mpim", "dm", "gdm", "group_dm", "group dm"})


def classify_sensitivity(*texts: Any, origin: str = "") -> str:
    """``"private"`` or ``"normal"`` for something about to be recorded.

    Two rules, either sufficient. ``origin`` naming a DM or group DM is
    private on its own: house rule 7's three categories - personnel, comp,
    M&A - are exactly the conversations that happen in DMs, and the one time
    an unmarked item was traced it had come from one. Any text carrying the
    vocabulary in `_SENSITIVE_TERMS` is private regardless of where it came
    from.

    Wrong-way-private costs a line missing from a vault file he can still read
    in his DM. Wrong-way-normal has already synced to every device by the time
    anyone notices. So when in doubt this says private, and a caller who knows
    better says ``"normal"`` explicitly.
    """
    if str(origin).strip().casefold() in _DM_ORIGINS:
        return "private"
    for text in texts:
        if text and (_SENSITIVE_TERMS.search(str(text)) or _SENSITIVE_ACRONYMS.search(str(text))):
            return "private"
    return "normal"


class SensitivityRequired(Exception):
    """A vault-bound record was written without a usable sensitivity.

    Deliberately NOT a `ValueError`: the house pattern wraps decoding in
    `except ValueError`, and a gate whose refusal can be swallowed by the
    handler around a `json.loads` is a gate with a hole in it.
    """


#: The only two marks `_is_private` reads. Anything else - "Private",
#: "privat", True - would pass a None check and then render as visible.
SENSITIVITIES = frozenset({"private", "normal"})
