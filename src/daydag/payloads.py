"""Reading a payload nobody has validated yet. `recipes` asks; this reads.

USING IT
    records(payload)            # -> list | None. None and [] are DIFFERENT
    has(record, "id", "user")   # any of these keys, truthy
    has_all(record, "id", "subject")    # every one of them
    error_text(payload)         # the error it carries, flattened, or ""
    measure(payload)            # rough size on the way back
    first_value(row)            # first value, however the driver shaped it
    flatten(value)              # every leaf as a string

CONTRACTS
    1. `records` returns None for "not this shape" and [] for "this shape,
       nothing in it". Keeping them apart is the whole reason it returns an
       optional - which of the two is a FAILURE depends on the source, and
       that judgement belongs to the caller.
    2. A WRONG SHAPE never raises - it returns a falsy answer, because a wrong
       shape is the routine case at this edge. Code that needs raise-to-degrade
       (`runlog._text`) does its own checking on purpose.

       Not the same as "nothing here raises", which was the earlier wording and
       was false: `flatten` recurses, so a cyclic or thousands-deep structure
       raises `RecursionError`, and `error_text` inherits that through it.
       `measure` propagates an exception from a broken `__repr__`. Neither is
       reachable from a JSON-decoded connector response - JSON cannot be
       cyclic - so the guard is the decode, not this module.
    3. Nothing here knows what a source MEANS. `records()` finds a list; it
       does not know an empty one is honest for calendar and a broken query
       for gmail.
    4. `has` uses truthiness, so an empty string does not count as carried.
       `has_all` exists because `any` on `("id", "subject")` let Gmail's
       metadata-only results through on the strength of the id.

WHY IT EXISTS
    This is the connector edge. Everything else in the package is handed data
    already shaped - `brief` takes a validated `Sequence[Mapping]`, `ledger`
    takes events, `pulse` parses git's stdout. Only code standing here sees an
    `error` key that might be a string, a list, or a dict of lists.

    It is a module rather than private helpers because a private helper is what
    makes the SECOND edge - a real client, an ingestion path - write its own
    copy. And a reader tested only through one caller is only ever tested
    through that caller's vocabulary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "ERROR_KEYS",
    "RECORD_KEYS",
    "error_text",
    "first_value",
    "flatten",
    "has",
    "has_all",
    "measure",
    "records",
]

#: Where a connector puts its error when it hands one back instead of raising.
#: The shape MCP and REST clients actually use, which a string-only reading
#: missed entirely: a `{"error": {"code": 401}}` fell through to the caller's
#: plausibility check and reported "no event list came back", never the 401.
#: "message" is deliberately absent: it is only an error when it sits under one
#: of these, and `flatten` already reads it there.
ERROR_KEYS = ("error", "errors", "errorMessages", "error_description")

#: Keys a connector puts its records under. Checked in order, first list wins.
RECORD_KEYS = (
    "events",
    "items",
    "messages",
    "threads",
    "issues",
    "repositories",
    "members",
    "results",
    "rows",
    "values",
    "data",
)


def flatten(value: Any) -> list[str]:
    """Every leaf in a nested structure, as strings, depth first."""
    if isinstance(value, Mapping):
        return [part for item in value.values() for part in flatten(item)]
    if isinstance(value, list | tuple):
        return [part for item in value for part in flatten(item)]
    return [str(value)]


def error_text(payload: Any) -> str:
    """The error a payload is carrying, flattened, or ``""`` if it carries none."""
    if not isinstance(payload, Mapping):
        return ""
    for key in ERROR_KEYS:
        if payload.get(key):
            return " ".join(flatten(payload[key]))
    return ""


def measure(payload: Any) -> int:
    """Roughly how much text this payload would occupy on the way back."""
    return len(payload if isinstance(payload, str) else repr(payload))


def records(payload: Any) -> list[Any] | None:
    """The record list inside a payload, or ``None`` if there is not one.

    ``None`` and ``[]`` are different answers, and keeping them apart is the
    whole reason this returns an optional rather than an empty list: no list at
    all means the call did not return this source's shape, an empty list means
    it did and matched nothing. Which of those is a failure depends on the
    source, so that call belongs to the caller and is not made here.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        for key in RECORD_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return None


def has(record: Any, *keys: str) -> bool:
    """Whether the record carries *any* of these, for keys that are alternatives."""
    return isinstance(record, Mapping) and any(record.get(key) for key in keys)


def has_all(record: Any, *keys: str) -> bool:
    """Whether the record carries *every* one of these.

    Separate from `has` because the difference is where two checks were wrong:
    `any` on `("id", "subject")` let Gmail's metadata-only search results
    through on the strength of the id, and the subject is the whole point.
    """
    return isinstance(record, Mapping) and all(record.get(key) for key in keys)


def first_value(row: Any) -> Any:
    """The first value in a row, however the driver shaped it."""
    if isinstance(row, Mapping):
        return next(iter(row.values()), None)
    if isinstance(row, list | tuple):
        return row[0] if row else None
    return row
