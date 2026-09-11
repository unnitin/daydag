"""The two state stores, split by requirement.

ARCHITECTURE keeps these apart deliberately, because they carry opposite needs:

* The ``DayDAG/`` vault folder is markdown a human corrects by hand. It is the
  reason this is not a black box.
* The event log is SQLite outside the vault. Markdown cannot answer "median
  days to answer", and five scheduled loops appending to one iCloud-synced file
  with no locking is a lost update.

Within the folder, ``State.md`` and ``Decisions.md`` are also split on purpose.
State is a projection the agent rewrites every loop; Decisions is appended and
never regenerated, so an answer written in the margin cannot be overwritten
before it is read.

Two shapes hold that seam together, both added for #63 and #61. A chase entry
is a ``ChaseItem`` on BOTH sides of the log/vault boundary - the log returns
one, and ``write_state`` coerces whatever it is handed to one before
rendering, so the two cannot drift apart again the way they had. A notes gap
is a ``NotesGap``, which exists so it has a ``sensitivity`` field to filter on
at all; as a bare string it could not be withheld.

All three lists then pass through one ``_visible`` gate rather than three call
sites that happen to agree. That is deliberate: the filter was once wired to
``chase`` and not to its twin ``watch``, and a private carry-forward reached a
synced file.
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
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
| `State.md` | the agent | rewritten every loop - do not park anything here you need kept |
| `Decisions.md` | both | appended, never regenerated. Answer by writing next to a line |
| `Watchlist.md` | you | config: repos, Jira projects, channels |
| `Proposals/` | the agent | proposed diffs awaiting a yes |
| `Archive/` | the agent | pre-cutover snapshots |

History, metrics, the meeting ledger and anything sensitive live in the event
log outside this vault, not here.
"""

#: A pending decision line, e.g. "- [3] draft nudge to VP-Data? no"
_DECISION = re.compile(r"^- \[(?P<id>\d+)\]\s+(?P<text>.*?)\s*$", re.MULTILINE)

#: An answer a human may write beside a decision: a whole word, at one end of
#: the line or the other - a hand edit lands either before the text or after it.
#:
#: Both restrictions are there because the two failure directions are not equal.
#: A missed answer means the decision is asked again; a phantom one means it is
#: silently dropped and never seen. The substring form read "not", "now" and
#: "nothing" as a "no" nobody wrote, and read the word "parked" mid-sentence as
#: an answer - the words most likely to appear in a question about whether to
#: do something.
_ANSWER_WORDS = r"snooze|yes|no|parked"
_ANSWER = re.compile(
    rf"^\s*({_ANSWER_WORDS})\b|\b({_ANSWER_WORDS})[\s.,!?;:)\]]*$",
    re.IGNORECASE,
)


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
    """The one chase-item shape `EventLog` and `write_state` are both held to.

    USING IT
        item = ChaseItem.from_payload(payload, sensitivity=sensitivity)
        item.owner; item["owner"]; item.get("owner")   # all three work
        item.has_owner_or_ask                          # False -> both missing

    CONTRACTS
        1. `key` is the only field every chase item is guaranteed to carry - a
           payload recorded with nothing else still builds one.
        2. Neither `owner` nor `ask` being present does not raise. It is read
           by `write_state` as `has_owner_or_ask is False`, which renders a
           named warning line instead of a bare bullet or a crash (guardrail
           6's "degrade visibly" - not the same failure as one of the two
           being present, which still renders).
        3. Mapping-shaped (`__getitem__`, `.get`, `dict(item)`) so it is a
           drop-in wherever a chase item was already a bare dict - every
           existing caller on either side of the log/vault seam.

    WHY IT EXISTS
        Issue #63: `EventLog.chase_items()` returned whatever a caller
        recorded, and `write_state` assumed `owner` and `ask` would be in it.
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
    def from_payload(cls, payload: Mapping[str, Any], *, sensitivity: str = "normal") -> ChaseItem:
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
            # took `write_state` down with an AttributeError - the torn-row
            # tolerance `_rows` exists for, undone one layer up.
            return cls(sensitivity=sensitivity)
        kwargs = {name: payload[name] for name in _CHASE_FIELDS if payload.get(name)}
        extra = {
            name: value
            for name, value in payload.items()
            if name not in _CHASE_FIELDS and name != "sensitivity"
        }
        return cls(
            sensitivity=str(payload.get("sensitivity", sensitivity)),
            extra=extra,
            **kwargs,
        )

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
    """Coerce whatever a caller passed `write_state` into the one shape (#63)."""
    return raw if isinstance(raw, ChaseItem) else ChaseItem.from_payload(raw)


@dataclass(frozen=True)
class NotesGap:
    """One meeting with no note found - `notes_gaps`' shape (#61).

    CONTRACTS
        1. `title` is what `write_state` prints. A meeting's own title can be
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
        `watch` are filtered inside `write_state` to avoid: "the boundary has
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
    calls this, not `write_state`'s three loops individually, which is the
    difference between a check that can be forgotten on a third list and one
    that cannot.
    """
    return item.get("sensitivity") == "private"


def _visible(items: Iterable[Any]) -> list[Any]:
    """Every item in ``items`` that is not private.

    `write_state`'s one gate for `chase`, `watch` and `notes_gaps` alike - the
    filter that a private carry-forward once slipped past because `watch` had
    no version of it while `chase` already did (and, before #61, `notes_gaps`
    had no `sensitivity` field to check at all). One function, applied the
    same way three times, is what makes "forgotten on the third list" a
    contradiction rather than a recurring incident.
    """
    return [item for item in items if not _is_private(item)]


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
            (folder.state_path, "# State\n\n## Chase list\n\n## Watch items\n"),
            (folder.decisions_path, "# Pending decisions\n\n"),
            (folder.watchlist_path, "# Watchlist\n\n## repos\n\n## jira\n\n## channels\n"),
        ):
            if not path.exists():
                path.write_text(body, encoding="utf-8")
        return folder

    # -- State.md is a projection ----------------------------------------

    def write_state(
        self,
        chase: Iterable[ChaseItem | Mapping[str, Any]] = (),
        watch: Iterable[Mapping[str, Any]] = (),
        notes_gaps: Iterable[str | Mapping[str, Any]] = (),
    ) -> None:
        """Rewrite ``State.md`` wholesale. It is derived, so it is replaced.

        ``chase``, ``watch`` and ``notes_gaps`` all pass through ``_visible``
        before anything is rendered - one filter, applied the same way to all
        three, so ``sensitivity == "private"`` cannot be wired to two of them
        and forgotten on the third. That is exactly how a private
        carry-forward once reached plaintext ``State.md``: ``chase`` was
        filtered and ``watch`` was not.

        ``chase`` is coerced to :class:`ChaseItem` whether a caller passes one
        already or a bare dict such as ``{"owner": ..., "ask": ...}`` - both
        sides of the log/vault seam are held to the one shape now (#63). An
        item with neither ``owner`` nor ``ask`` degrades to a named warning
        line rather than the ``- ?`` it used to render silently.

        ``notes_gaps`` is coerced to :class:`NotesGap`, which gives a
        meeting's own title the same sensitivity channel ``chase`` and
        ``watch`` already had (#61) - a plain string is still accepted, read
        as ``sensitivity="normal"``.
        """
        lines = ["# State", "", "## Chase list", ""]
        for item in _visible(_as_chase_item(raw) for raw in chase):
            if not item.has_owner_or_ask:
                lines.append(f"- {WARN} chase item {item.key} has no owner or ask recorded")
                continue
            lines.append(f"- {item.owner or '?'} · {item.ask}".rstrip(" ·"))
        lines += ["", "## Watch items", ""]
        for item in _visible(watch):
            lines.append(f"- {item.get('what', '')}")
        gaps = _visible(NotesGap.from_value(gap) for gap in notes_gaps)
        if gaps:
            # A titleless gap gets a named warning, not a bare `- `. The chase
            # path one section up already degrades that way, and a blank bullet
            # is the same formatting-glitch-standing-in-for-data that #63 was
            # about. Reachable: a calendar-shaped record keyed `summary` rather
            # than `title` coerces to an empty title.
            lines += ["", "## Notes gaps", ""] + [
                f"- {gap.title}"
                if gap.title
                else f"- {WARN} a notes gap arrived with no title recorded"
                for gap in gaps
            ]
        self.state_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def read_state(self) -> str:
        return self.state_path.read_text(encoding="utf-8")


class DecisionQueue:
    """Pending decisions: appended, never regenerated.

    Decisions batch onto the next scheduled push rather than interrupting. An
    agent that needs an answer at an unpredictable moment is a pager, and a
    pager gets muted.
    """

    #: Renders before an unanswered item is parked and stops being re-asked.
    MAX_PUSHES = 3

    def __init__(self, folder: StateFolder) -> None:
        self._folder = folder
        self._pushes: dict[str, int] = {}

    def _read(self) -> str:
        return self._folder.decisions_path.read_text(encoding="utf-8")

    def add(self, text: str) -> str:
        """Append a decision and return its stable id.

        Ids are strings because they are quoted back by a human - "2 yes, 4 no"
        in a Slack reply, or written beside the line in Obsidian.
        """
        body = self._read()
        next_id = str(max((int(m["id"]) for m in _DECISION.finditer(body)), default=0) + 1)
        with self._folder.decisions_path.open("a", encoding="utf-8") as handle:
            handle.write(f"- [{next_id}] {text}\n")
        self._pushes[next_id] = 0
        return next_id

    def answer_for(self, item_id: str) -> str | None:
        """Whatever a human wrote after the decision, if anything.

        A hand edit is an event and wins over derived state, so this reads the
        file rather than any in-memory view.
        """
        for match in _DECISION.finditer(self._read()):
            if match["id"] != str(item_id):
                continue
            found = _ANSWER.search(match["text"].strip())
            if found is None:
                return None
            return (found.group(1) or found.group(2)).casefold()
        return None

    def status(self, item_id: str) -> str:
        """``answered``, ``parked`` or ``open``."""
        if self.answer_for(item_id):
            return "answered"
        if self._pushes.get(str(item_id), 0) >= self.MAX_PUSHES:
            return "parked"
        return "open"

    def render(self) -> str:
        """The numbered block that rides on the next push.

        Rendering counts as asking, which is what lets an unanswered item age
        out instead of being re-asked forever. Silence is an answer; the agent
        just says out loud that it read it that way.
        """
        lines = []
        for match in _DECISION.finditer(self._read()):
            item_id = match["id"]
            if self.answer_for(item_id):
                continue
            self._pushes[item_id] = self._pushes.get(item_id, 0) + 1
            if self._pushes[item_id] > self.MAX_PUSHES:
                continue
            lines.append(f"{item_id}. {match['text']}")
        return "\n".join(lines)


@dataclass
class _Event:
    kind: str
    payload: dict[str, Any]


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

    def record(self, kind: str, *, sensitivity: str = "normal", **payload: Any) -> None:
        self._db.execute(
            "INSERT INTO events (kind, sensitivity, payload) VALUES (?, ?, ?)",
            (kind, sensitivity, json.dumps(payload)),
        )
        self._db.commit()

    def _rows(self, kind: str | None = None) -> list[tuple[str, str, dict[str, Any]]]:
        """Decoded events, skipping any row whose payload will not parse.

        Skipped rather than raised. This feeds `chase_items`, which feeds
        `write_state` - so one torn or hand-repaired row would otherwise take
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
        `write_state` is held to the same shape on its side of the seam.
        """
        return [
            ChaseItem.from_payload(payload, sensitivity=sensitivity)
            for kind, sensitivity, payload in self._rows()
            if kind in {"loop_opened", "carry_forward"}
        ]

    def median_days_to_answer(self) -> float:
        """Section 8's chase-efficacy metric - the reason this store exists."""
        opened = {p["key"]: p["day"] for _, _, p in self._rows("loop_opened")}
        spans = [
            p["day"] - opened[p["key"]]
            for _, _, p in self._rows("loop_answered")
            if p["key"] in opened
        ]
        return statistics.median(spans) if spans else 0.0
