"""The event log: append-mostly SQLite, OUTSIDE the vault.

USING IT
    log = EventLog.open(path)
    log.record("loop_opened", sensitivity=classify_sensitivity(ask, quote, origin=kind), **payload)
    log.recorded("run")                     # every payload of one kind, oldest first
    log.chase_items()                       # ChaseItem rows, sensitivity from the column
    log.record_fetch("org/repo", at=now); log.last_fetch("org/repo")
    log.record_cursor("org/repo", sha); log.last_cursor("org/repo")   # the pulse reads on

CONTRACTS
    1. Anything sensitive lives here and never in the vault - the vault is
       plaintext on every device it syncs to. `statedoc` holds the gate
       (`_visible`) and the classifier; this store holds the rows.
    2. A VAULT-BOUND kind cannot be recorded without saying how sensitive it
       is, and the mark must be exactly "private" or "normal" (#105): the gate
       compares for equality, so a default or a near miss renders as visible.
    3. One decoder, `recorded`. A row that will not parse is skipped (or
       handed back raw when asked), never raised on - one torn row must not
       take the chase list or the run history down with it.

WHY SQLITE
    Markdown cannot answer "which run came last", and five loops appending to
    one iCloud-synced file with no locking is a lost update.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from daydag.statedoc import (
    SENSITIVITIES,
    VAULT_BOUND,
    ChaseItem,
    SensitivityRequired,
    classify_sensitivity,
)

__all__ = ["EventLog", "classify_sensitivity"]


def _as_utc(when: datetime) -> datetime:
    """``when`` as an aware UTC datetime, assuming UTC if it says nothing.

    One naive stamp beside one aware stamp is a ``TypeError`` on the comparison
    between them, so the log never holds both. Assuming rather than refusing is
    the right failure direction here: the caller is a scheduled pre-step, and a
    missing tzinfo is a cosmetic mistake that must not stop a brief.
    """
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)


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

    def recorded(self, kind: str | None = None, *, raw: bool = False) -> list[Any]:
        """Every payload of ``kind`` (or of every kind), oldest first.

        The one decoder. A row that will not parse is skipped, or handed back
        as its raw text when ``raw`` is set - so a caller that wants to say
        "one row is torn" can, and one torn row never takes the history down
        from inside a comprehension. Ordered explicitly: a bare ``SELECT``
        happens to come back in rowid order, and the run log is built on which
        run came last.
        """
        sql, args = "SELECT payload FROM events", ()
        if kind is not None:
            sql, args = sql + " WHERE kind = ?", (kind,)
        rows: list[Any] = []
        for (payload,) in self._db.execute(sql + " ORDER BY id", args):
            try:
                rows.append(json.loads(payload))
            except ValueError:
                if raw:
                    rows.append(payload)
        return rows

    # -- mirror cursors (#138) -------------------------------------------

    def record_cursor(self, repo: str, cursor: str) -> None:
        """Remember where ``repo``'s pulse read up to, so the next run reads on.

        Without this every run starts at first sight and reports a quiet day
        forever - the cursor was computed, returned, and thrown away.
        """
        self.record("mirror_cursor", repo=repo, cursor=cursor)

    def last_cursor(self, repo: str) -> str | None:
        """The newest stored cursor for ``repo``, or ``None`` on first sight."""
        rows = self._db.execute(
            "SELECT payload FROM events"
            " WHERE kind = 'mirror_cursor' AND json_extract(payload, '$.repo') = ?"
            " ORDER BY id DESC LIMIT 1",
            (repo,),
        )
        for (payload,) in rows:
            try:
                cursor = json.loads(payload).get("cursor")
            except ValueError:
                continue
            return str(cursor) if cursor else None
        return None

    # -- mirror freshness ------------------------------------------------

    def record_fetch(self, repo: str, *, at: datetime) -> None:
        """Note that ``repo``'s mirror fetched cleanly at ``at``.

        Appended like everything else rather than upserted: the log is the
        history, and "when did this repo stop fetching" is a question only the
        rows can answer. ``last_fetch`` reads the newest back out.

        Normalised through `_as_utc` on the way in, so the table never holds
        one naive row beside one aware one.
        """
        self.record("mirror_fetched", repo=repo, at=_as_utc(at).isoformat())

    def last_fetch(self, repo: str) -> datetime | None:
        """When ``repo``'s mirror last fetched cleanly, or ``None``.

        ``None`` rather than "now": a mirror that has never been read cleanly
        must not be dated as if it were fresh (#60). `pulse` renders the
        difference in words.

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
        """Chase entries as `ChaseItem` (#63), tagged with the LOG'S sensitivity
        column - the trusted fact, outranking anything the payload claims.

        Filtered in SQL (#115): the run log is the highest-volume writer, and
        decoding every one of its rows to find two kinds was a scan that grew
        without bound.
        """
        rows = self._db.execute(
            "SELECT sensitivity, payload FROM events"
            " WHERE kind IN ('loop_opened', 'carry_forward') ORDER BY id"
        )
        items: list[ChaseItem] = []
        for sensitivity, payload in rows:
            try:
                decoded = json.loads(payload)
            except ValueError:
                continue
            items.append(ChaseItem.from_payload(decoded, sensitivity=sensitivity))
        return items
