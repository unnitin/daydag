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
       plaintext on every device it syncs to. This store holds the rows, the
       classifier that marks them and the one chase-item shape both sides of
       the seam read; `statedoc` holds the gate (`_visible`) that reads the
       mark on the way out.
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
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from daydag.recipes import has

__all__ = [
    "SENSITIVITIES",
    "VAULT_BOUND",
    "ChaseItem",
    "EventLog",
    "SensitivityRequired",
    "classify_sensitivity",
]


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


#: Kinds `chase_items` projects into `State.md`. Recording one without an
#: explicit, well-formed sensitivity is refused - contract 2 above.
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
