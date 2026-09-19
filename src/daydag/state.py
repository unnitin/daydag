"""The two state stores, split by opposite requirements.

USING IT
    folder = StateFolder.create(root)       # DayDAG/, idempotent
    folder.update_state(chase=..., watch=..., notes_gaps=...)  # APPENDS

    folder.add_decision("draft nudge to VP-Data? · status: open")  # APPENDS

    log = EventLog.open(path)               # SQLite, OUTSIDE the vault
    log.record("loop_opened", sensitivity=classify_sensitivity(ask, quote, origin=kind),
               **payload)                 # kind: the Slack conversation type
    log.chase_items()

CONTRACTS
    1. Anything sensitive goes to the EVENT LOG, never the vault. The vault is
       plaintext on every device it syncs to.
    2. `update_state` runs `chase`, `watch` AND `notes_gaps` through one
       `_visible` gate before anything is rendered - one filter, so
       `sensitivity == "private"` cannot be wired to two of the three lists and
       forgotten on the third. It was once wired to `chase` and not to its twin
       `watch`, and a private carry-forward reached a synced file (#63's
       sibling); `notes_gaps` had no sensitivity field to filter on at all
       until `NotesGap` gave it one (#61).
    3. One shape per list, held on BOTH sides of the log/vault seam. A chase
       entry is a `ChaseItem` - `EventLog.chase_items()` returns one and
       `update_state` coerces whatever it is handed before rendering, so the two
       cannot drift apart the way they had (#63). A notes gap is a `NotesGap`.
       Both coercions are idempotent and accept their own type first.
    4. `State.md` is the RECORD, and `update_state` APPENDS to it. It is
       parsed, added to, and re-rendered; every line the run did not derive
       comes back byte-for-byte. A derived item already in the file - under
       any heading, struck or open - is recognised and not filed twice. This
       was contract 4 the other way round until #130: the file was a
       projection rewritten wholesale, and on 2026-09-14 an `eod` run that
       derived nothing wrote nothing over four hand-written chase items.
       Nothing re-derives the list now, because nothing can.
    5. `Decisions.md` is APPENDED and never regenerated, so an answer written
       on the line cannot be overwritten before it is read. The answer IS the
       line's `status:` field (D-5, #62) and `closure` reads it; nothing here
       parses one, so nothing can read an answer he did not write.
    6. A VAULT-BOUND kind cannot be recorded without saying how sensitive it
       is. `record("loop_opened", ...)` with no `sensitivity`, or with anything
       other than exactly "private" or "normal", raises; the gate in (2)
       filters what is MARKED private, and a gate that depends on the writer
       remembering to mark - or spelling the mark the way `_is_private` reads
       it - is not a gate (#105). `classify_sensitivity` is the answer to
       pass: private for anything from a DM, or carrying personnel / comp /
       M&A vocabulary - house rule 7's three categories. Meeting titles reach
       the vault through `notes_gaps`, not through a recorded kind, so the
       runner classifies each title at projection time instead.

WHY IT EXISTS
    The folder is markdown a human corrects by hand, and that is the reason
    this is not a black box - a hand edit is an event and wins over anything
    derived. The event log is SQLite because markdown cannot answer "median
    days to answer", and because five scheduled loops appending to one
    iCloud-synced file with no locking is a lost update.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from daydag.payloads import has
from daydag.voice import WARN

#: The sanctioned warning glyph (plain U+26A0, not its emoji-presentation
#: twin) - `daydag.voice` is the register authority on this; a chase item that
#: cannot be rendered in full still has to stay inside house voice.

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


def read_section(text: str, name: str, *, top_level: bool = False) -> list[str]:
    """Bullet bodies under the heading called ``name``, whatever its level.

    Shared by every reader of ``State.md`` - the brief's chase/watch sections
    and the week-ahead's carrying-in section both walk the same hand-edited
    file, and a second regex here is a second set of bugs that agree only on
    the easy cases.

    Args:
        top_level: return only bullets at column zero. An indented bullet is
            HIS COMMENT on the item above it - "expect comments from me in
            sub-bullets" (2026-09-15) - and since #130 it is also the quote
            and the permalink `_render_chase` writes under each row. Without
            this, one appended chase item reads back as three, and the brief
            announced "owed to you (3)" with two of the three being a quote
            and a bare link. The live file's 4 chase items read back as 22
            bodies. Default off: nothing else has been audited for it.
    """
    wanted = name.casefold()
    collecting = False
    section_level = 0
    bodies: list[str] = []
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            level = len(heading["hashes"])
            if heading["name"].casefold() == wanted:
                collecting, section_level = True, level
            elif collecting and level <= section_level:
                # A hand-added `### Snoozed` under `## Chase list` must not end
                # the section. State.md is edited by a human; nesting is normal.
                collecting = False
            continue
        bullet = _BULLET.match(line)
        if collecting and bullet and not (top_level and line[:1].isspace()):
            bodies.append(bullet["body"])
    return bodies


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


def _as_utc(when: datetime) -> datetime:
    """``when`` as an aware UTC datetime, assuming UTC if it says nothing.

    One naive stamp beside one aware stamp is a ``TypeError`` on the comparison
    between them, so the log never holds both. Assuming rather than refusing is
    the right failure direction here: the caller is a scheduled pre-step, and a
    missing tzinfo is a cosmetic mistake that must not stop a brief.
    """
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)


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
        Issue #63: `EventLog.chase_items()` returned whatever a caller
        recorded, and `update_state` assumed `owner` and `ask` would be in it.
        A payload recorded with only `key` rendered as a bare `- ?` in
        `State.md` - a formatting glitch standing in for data nobody had
        agreed had to be there. One shape, read the same way on both sides of
        the seam, is what stops that disagreement from recurring the next
        time either module changes.
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
    #: Everything else the caller recorded. The nine above are GUARANTEED to
    #: exist; they were never meant to be all there is. Whitelisting them
    #: dropped `day` - which `loop_opened` records on every call - and made
    #: contract 3's "drop-in" false: an existing `item["day"]` became a
    #: KeyError. Carried, not merged into the fields, so a payload cannot
    #: overwrite a guaranteed one.
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
            # "Never raises" has to hold for a hand-written row too: valid JSON
            # that is not an object (`null`, a list, a scalar) reached here and
            # took `update_state` down with an AttributeError - the torn-row
            # tolerance `_rows` exists for, undone one layer up.
            return cls(sensitivity=sensitivity)
        kwargs = {name: payload[name] for name in _CHASE_FIELDS if payload.get(name)}
        extra = {
            name: value
            for name, value in payload.items()
            if name not in _CHASE_FIELDS and name != "sensitivity"
        }
        # An explicit ``sensitivity`` is the LOG'S COLUMN and outranks anything
        # the payload claims: the column is the trusted fact, the payload is
        # whatever was written into the row. Reading the payload first let a
        # hand-edited row carrying `"normal"` override a column saying
        # `"private"` and reach plaintext `State.md`. Omitted means there is no
        # column to trust - `update_state` is handed a bare dict whose own value
        # is the only source there - so the payload wins.
        resolved = sensitivity if sensitivity is not None else payload.get("sensitivity", "normal")
        return cls(sensitivity=str(resolved), extra=extra, **kwargs)

    @property
    def has_owner_or_ask(self) -> bool:
        """Whether there is anything real to render.

        `payloads.has` rather than a hand-rolled truthiness check - the same
        "at least one of these alternatives" rule that keeps a Gmail
        metadata-only result from passing as a match applies here: an item
        with neither field is not half-missing data, it is no data.
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
        Issue #61 / the guardrail file's GAP 3: `notes_gaps` was a list of
        bare strings with no sensitivity tag to read, so a meeting whose own
        title was sensitive had no way to be withheld - its caller had to
        pre-filter, which is exactly the failure direction `chase` and
        `watch` are filtered inside `update_state` to avoid: "the boundary has
        to hold even when a caller forgets."
    """

    title: str
    sensitivity: str = "normal"

    @classmethod
    def from_value(cls, value: str | Mapping[str, Any] | NotesGap) -> NotesGap:
        """A `NotesGap` from one of its own, a bare string, or a dict.

        The already-a-`NotesGap` case is FIRST and is not a convenience. This
        class carries a `get()` but does not subclass `Mapping`, so without it
        an instance fell through to the bare-string branch and became
        `cls(title=str(value))` - the dataclass repr as the title, and
        `sensitivity` reset to "normal". Handing the module its own type
        laundered a private gap into a visible one.

        `ChaseItem` never had the bug because it DOES subclass `Mapping`, which
        is the whole lesson: one coercion, two shapes, and the guard held on
        only one of them.
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

    `update_state`'s one gate for `chase`, `watch` and `notes_gaps` alike - the
    filter that a private carry-forward once slipped past because `watch` had
    no version of it while `chase` already did (and, before #61, `notes_gaps`
    had no `sensitivity` field to check at all). One function, applied the
    same way three times, is what makes "forgotten on the third list" a
    contradiction rather than a recurring incident.
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

#: Emphasis and strike markers, dropped before two lines are compared. He
#: writes `- **gov-lead** · ...`; the log records `gov-lead`.
_DECORATION = re.compile(r"[*_`~]+")


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
    def head(self) -> str:
        """The bullet line itself - the first line with anything on it.

        Not `lines[0]`: `append` prepends a blank separator when the previous
        block does not end in one, so `lines[0]` was `""` for exactly those
        blocks and `matches` never saw the bullet. Two chase items recorded on
        different days arrive in one `chase_items()` call, so the same ask was
        filed twice within a single run.
        """
        return next((line for line in self.lines if line.strip()), "")

    def matches(self, *needles: str) -> bool:
        """Whether the bullet line contains any of ``needles``.

        Substring, after flattening, because he rewords a row when he files it
        by hand. The two failure directions are not equal: a missed match
        files a duplicate, which he can see and delete, while a false match
        silently drops a real ask.

        A needle ending in something identifier-shaped is matched at a
        BOUNDARY rather than as a bare substring - ticket keys nest, and
        `CDI-9` inside an existing `CDI-91` line read as already-filed and
        dropped the ask.
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

           Split on `"\\n"` and nothing else. `str.splitlines()` also breaks on
           `\\r`, `\\x0b`, `\\x0c`, `\\x1c`, `\\x85`, `\\u2028` and `\\u2029`,
           all of which paste in from a browser or a Slack copy - and `render`
           rejoined with `\\n`, so a file carrying one did not survive its own
           parse and every loop then refused to write it. Worse, house rule 1
           makes a verbatim quote mandatory, so ONE quoted line separator
           written into `State.md` froze the file permanently and raised on
           every run afterwards.
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

    def contains(self, *needles: str) -> bool:
        """Whether any block in any section is already about this.

        Any section on purpose (contract 3). A struck row under `Done` and an
        open one under `Chase list` both mean "he knows about it", and
        re-filing either is the same wrong answer.
        """
        return any(block.matches(*needles) for section in self.sections for block in section.blocks)

    def is_line(self, text: str, *, section: str) -> bool:
        """Whether a bullet in ``section`` IS ``text``, not merely contains it.

        Two narrowings from `contains`, and both matter for the short-phrase
        lists:

        * whole line, because `contains("1:1")` is true of almost any file -
          "prep for the 1:1 with the CFO" is not a duplicate of the gap `1:1`;
        * one section, because `contains` searches all of them by design
          (contract 3) and a watch item named `1:1` is not the same fact as a
          notes gap named `1:1`. Cross-section suppression there would drop
          whichever of the two was written second.
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

    Returns ``()`` when neither clears its floor. The caller then falls back to
    the rendered bullet itself - without that, `contains(*())` is vacuously
    False and the item is appended on EVERY run: four loops a day, four copies
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
                path.write_text(body, encoding="utf-8")
        return folder

    # -- State.md is the record; the writer appends to it (#130) ----------

    def update_state(
        self,
        chase: Iterable[ChaseItem | Mapping[str, Any]] = (),
        watch: Iterable[Mapping[str, Any]] = (),
        notes_gaps: Iterable[str | Mapping[str, Any]] = (),
    ) -> None:
        """Append anything derived that is not already in ``State.md``.

        Nothing is removed, reordered or reflowed. A run that derived nothing
        writes nothing at all - which is the whole of #130: on 2026-09-14 an
        `eod` run that derived nothing rendered exactly that over four
        hand-written chase items and six run-log lines.

        ``chase``, ``watch`` and ``notes_gaps`` still pass through ``_visible``
        first - one filter, applied the same way to all three, so
        ``sensitivity == "private"`` cannot be wired to two of them and
        forgotten on the third. Appending rather than replacing must not route
        around the gate a private carry-forward once slipped past.

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
            # `is_line` and not `contains`: a watch item is a short phrase, and
            # a substring test is satisfied by any line anywhere in the file -
            # the run log, a sub-bullet, a struck row - which drops the item
            # silently.
            if not what or doc.is_line(what, section="Watch items"):
                continue
            doc.append("Watch items", [f"- {what}", ""])
        for gap in _visible(NotesGap.from_value(raw) for raw in notes_gaps):
            # Same as `watch`, and more exposed: a meeting title is routinely
            # `1:1` or `Standup`, which a substring test finds everywhere.
            if not gap.title or doc.is_line(gap.title, section="Notes gaps"):
                continue
            doc.append("Notes gaps", [f"- {gap.title}", ""])

        after = doc.render()
        if after == before:
            # A no-op run must not churn the file, the archive, or the iCloud
            # sync that carries both to his other devices.
            return
        if self.state_path.exists():
            (self.root / "Archive").mkdir(exist_ok=True)
            (self.root / "Archive" / "State.md.bak").write_text(before, encoding="utf-8")
        self.state_path.write_text(after, encoding="utf-8")

    def read_state(self) -> str:
        return self.state_path.read_text(encoding="utf-8")

    def add_decision(self, line: str) -> None:
        """Append one pending decision to ``Decisions.md``, never regenerating it.

        The answer is written by hand on the same line as ``status: <answer>``
        (D-5, #62) - the line is his to edit, and `closure` reads the status
        back. Nothing here parses an answer, so a decision cannot self-answer
        and a hand edit cannot be overwritten.
        """
        with self.decisions_path.open("a", encoding="utf-8") as handle:
            handle.write(f"- {line.strip()}\n")


@dataclass
class _Event:
    kind: str
    payload: dict[str, Any]


#: Kinds `chase_items` projects into `State.md`. Recording one without an
#: explicit, well-formed sensitivity is refused - see contract 7. Meeting
#: rows are not here: their titles reach `State.md` through the ledger's
#: `notes_gaps`, and `run._project` classifies each one on the way out.
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


class EventLog:
    """Append-mostly SQLite, outside the vault.

    Holds every transition, the meeting ledger, repo cursors, section 8 metrics,
    and the sensitive partition that must never reach a synced markdown file.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " kind TEXT NOT NULL,"
            " sensitivity TEXT NOT NULL DEFAULT 'normal',"
            " payload TEXT NOT NULL)"
        )
        self._db.commit()

    @classmethod
    def open(cls, path: str | Path) -> EventLog:
        return cls(sqlite3.connect(str(path)))

    def record(self, kind: str, *, sensitivity: str | None = None, **payload: Any) -> None:
        """Append one event.

        ``sensitivity`` may be omitted for a kind that never reaches the vault -
        a run-log row, a remembered meeting. For a kind in `VAULT_BOUND` it is
        REQUIRED and must be exactly "private" or "normal": the default was how
        an unmarked comp item reached a plaintext `State.md` (#105), and a
        misspelt mark would take the same road, since `_is_private` compares
        for equality. Pass `classify_sensitivity(...)` if you do not know.
        """
        if sensitivity is None and kind in VAULT_BOUND:
            raise SensitivityRequired(
                f"{kind!r} is projected into the vault: say sensitivity="
                '"private" or "normal" explicitly - classify_sensitivity() decides it'
            )
        if sensitivity is None:
            sensitivity = "normal"
        elif sensitivity not in SENSITIVITIES:
            raise SensitivityRequired(
                f"sensitivity={sensitivity!r} is not one of {sorted(SENSITIVITIES)}; "
                "the vault gate compares for equality, so a near miss renders as visible"
            )
        self._db.execute(
            "INSERT INTO events (kind, sensitivity, payload) VALUES (?, ?, ?)",
            (kind, sensitivity, json.dumps(payload)),
        )
        self._db.commit()

    def _rows(self, kind: str | None = None) -> list[tuple[str, str, dict[str, Any]]]:
        """Decoded events, skipping any row whose payload will not parse.

        Skipped rather than raised. This feeds `chase_items`, which feeds
        `update_state` - so one torn or hand-repaired row would otherwise take
        `State.md` down wholesale. The run log is now by far the highest-volume
        writer into this table, and a damaged row of *its* would have blanked
        the chase list. A row that cannot be decoded carries nothing a chase
        list could show anyway; `payloads` hands the raw text back for callers
        that want to say so.
        """
        sql = "SELECT kind, sensitivity, payload FROM events"
        args: tuple[Any, ...] = ()
        if kind is not None:
            sql += " WHERE kind = ?"
            args = (kind,)
        rows = []
        for k, s, p in self._db.execute(sql, args):
            try:
                rows.append((k, s, json.loads(p)))
            except ValueError:
                continue
        return rows

    def recorded(self, kind: str) -> list[Any]:
        """Every payload recorded under one kind, oldest first.

        The read side of `record`, and named for it - `record` writes, this
        reads back. It was `payloads`, which collided with the `daydag.payloads`
        module for a reader seeing both in one file, and the two mean different
        things: that module reads an unvalidated connector response, this reads
        a row this log wrote itself. `chase_items` predates this and
        folds the sensitivity column into each item, which is right for a chase
        entry and wrong for anything that has to round-trip.

        Ordered explicitly: a bare `SELECT` happens to come back in rowid order
        today, and "happens to" is not a thing a run log can be built on - the
        whole value of the log is which run came last.

        A row that will not decode comes back as its **raw text** rather than
        raising. One torn write or hand-repaired row would otherwise take the
        whole history down from inside this comprehension, before any caller
        could attribute the damage to a single row - and the moment a store gets
        damaged is the moment somebody is reading it. Callers already have to
        handle a payload that is not the shape they expect; this makes a
        corrupt one the same case rather than a fatal one.
        """
        rows = []
        for (payload,) in self._db.execute(
            "SELECT payload FROM events WHERE kind = ? ORDER BY id", (kind,)
        ):
            try:
                rows.append(json.loads(payload))
            except ValueError:
                rows.append(payload)
        return rows

    # -- mirror freshness ------------------------------------------------

    def record_fetch(self, repo: str, *, at: datetime) -> None:
        """Note that ``repo``'s mirror fetched cleanly at ``at``.

        Appended like everything else rather than upserted: the log is the
        history, and "when did this repo stop fetching" is a question only the
        rows can answer. ``last_fetch`` reads the newest back out.

        Normalised to UTC on the way in. A naive stamp is *assumed* UTC rather
        than refused, because refusing would take the pre-step down over a
        cosmetic detail - but it is not stored naive: one naive row beside one
        aware row makes them incomparable, and a mixed comparison is a
        ``TypeError`` three frames inside the 6:40am run.
        """
        self.record("mirror_fetched", repo=repo, at=_as_utc(at).isoformat())

    def last_fetch(self, repo: str) -> datetime | None:
        """When ``repo``'s mirror last fetched cleanly, or ``None``.

        ``None`` rather than "now": a mirror that has never once been read
        successfully must not be dated as if it were fresh, which is the exact
        lie issue #60 is about. The stale line says so in words instead.

        A row whose stamp does not parse is skipped rather than raised on, and a
        naive one is read as UTC. This file is on disk and a human can touch it;
        a bad value there must not take the 6:40am brief down.

        Filtered in SQL rather than in Python. Every mirror stamps this table
        three times a day forever, and this is read once per watched repo per
        run - decoding every event of every kind to answer it turns a constant
        into a scan that grows without bound.
        """
        rows = self._db.execute(
            "SELECT payload FROM events"
            " WHERE kind = 'mirror_fetched' AND json_extract(payload, '$.repo') = ?"
            " ORDER BY id DESC",
            (repo,),
        )
        newest: datetime | None = None
        for (payload,) in rows:
            try:
                # Newest by *stamp*, not by insertion order: the clock is
                # injected, so the two are not guaranteed to agree.
                stamp = _as_utc(datetime.fromisoformat(json.loads(payload).get("at", "")))
            except (ValueError, TypeError):
                continue
            if newest is None or stamp > newest:
                newest = stamp
        return newest

    def chase_items(self) -> list[ChaseItem]:
        """Chase entries as `ChaseItem` (#63), each tagged with the sensitivity
        that gates the vault.

        Was a bare dict merging the sensitivity column in - a payload recorded
        with only `key` rendered as `- ?` in `State.md`, a formatting glitch
        standing in for data nobody had agreed had to be there.
        `ChaseItem.from_payload` is where that agreement now lives, and
        `update_state` is held to the same shape on its side of the seam.
        """
        return [
            ChaseItem.from_payload(payload, sensitivity=sensitivity)
            for kind, sensitivity, payload in self._rows()
            if kind in {"loop_opened", "carry_forward"}
        ]
