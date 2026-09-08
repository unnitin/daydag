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
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
        chase: Iterable[dict[str, Any]] = (),
        watch: Iterable[dict[str, Any]] = (),
        notes_gaps: Iterable[str] = (),
    ) -> None:
        """Rewrite ``State.md`` wholesale. It is derived, so it is replaced.

        Sensitive ``chase`` and ``watch`` items are filtered here rather than at
        the call site: the vault is plaintext on every device, so this is the
        boundary that has to hold even when a caller forgets.

        ``notes_gaps`` is *not* filtered - it is a list of bare strings with no
        sensitivity tag to read, so a meeting whose own title is sensitive has
        to be withheld by its caller. Giving it the same shape as the other two
        is the fix; until then the gap is stated rather than assumed away.
        """
        lines = ["# State", "", "## Chase list", ""]
        for item in chase:
            if item.get("sensitivity") == "private":
                continue
            owner = item.get("owner", "?")
            ask = item.get("ask", "")
            lines.append(f"- {owner} · {ask}".rstrip(" ·"))
        lines += ["", "## Watch items", ""]
        for item in watch:
            # Watch items come from the same log as chase items and carry the
            # same tag; filtering one list and not the other leaked a private
            # carry-forward straight into a synced markdown file.
            if item.get("sensitivity") == "private":
                continue
            lines.append(f"- {item.get('what', '')}")
        gaps = list(notes_gaps)
        if gaps:
            lines += ["", "## Notes gaps", ""] + [f"- {g}" for g in gaps]
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

    def chase_items(self) -> list[dict[str, Any]]:
        """Chase entries, each tagged with the sensitivity that gates the vault."""
        return [
            {**payload, "sensitivity": sensitivity}
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
