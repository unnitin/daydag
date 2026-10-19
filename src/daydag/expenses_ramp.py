"""Ramp, the sink since 2026-10-18: the connector's answers read in the ledger's terms.

USING IT
    python -m daydag.expenses ramp-check batch.json ramp.json [--expected expected.json]
    #   -> his reimbursements in Ramp against what the trip should have produced:
    #      the batch rows (mail receipts) plus `expected.json`, the lines he filed
    #      from files - photographed slips, a folio, statement pages. ramp.json is
    #      the saved answer of `ramp_get_user_recent_reimbursements`
    #      (include_drafts=true, limit=null). Prints the pending total against the
    #      expected total, every match with its status, trip and GL, and both
    #      kinds of leftover.
    python -m daydag.expenses ramp-stage files.json [--trip-uuid UUID]
    #   -> for each local receipt file, the four steps that put it in Ramp as a
    #      draft with the right amount, merchant, date, memo, trip and coding:
    #      upload link -> curl from disk -> create from receipt -> edit. Printed,
    #      never run: the agent runs them after his numbered go.

    ramp_items_in(payload)                   # -> [RampItem]
    expected_from_batch(batch) + expected_from_file(payload)
    ramp_check(expected, items).render()
    stage_files(files, trip_uuid=...)        # -> [Staging], each .steps()

CONTRACTS
    1. Nothing here sends (expenses.py contract 1 holds): `stage_files` prints
       the calls and the curl line; the receipt bytes go from his disk to Ramp
       through the one-time link, never through the model. That is why files
       can be filed at all - a 1 MB folio as base64 in a tool call is not an
       option, a curl of the path is.
    2. Pass amount, merchant and date on create. Ramp's OCR read a $1,203.77
       folio total where $1,177.95 was paid and named the hotel "Hotel
       Tonnelle"; both fields are DRAFT-ONLY, so a wrong one survives
       submission. The create call takes them explicitly and skips OCR.
    3. Submission is its own go. Nothing here prints a submit call; the agent
       calls `ramp_submit_reimbursement` only on "submit 1 2 3" over a list he
       has read, because a submitted reimbursement reaches an approver and
       stops being editable in the ways that matter.
    4. Coding is data read from his own rows on 2026-10-18, not inferred, and
       it lives in .env under the names in `CODING_KEYS` / `GL_KEY_BY_CATEGORY`
       (the repo is public). A name that is not set is reported on the step
       and its selection left out; nothing here guesses a UUID. If Finance
       renames an account the UUID still resolves; if they retire one the
       edit fails loudly and .env gets re-read from a current row.

WHAT WAS VERIFIED (2026-10-18)
    - `ramp_edit_reimbursement` on a PENDING reimbursement accepts trip_uuid
      (16 rows moved under one trip) and rejects amount and merchant.
    - `ramp_request_receipt_upload_link` returns a single-use URL good for 30
      minutes; `curl -F file=@<path>` from the session answered 201 with a
      receipt_uuid; `ramp_create_reimbursement_from_receipt` with that uuid
      and explicit amount/merchant/date made a draft with the folio attached.
    - `ramp_duplicate_reimbursement` copies the fields but NOT the receipt.
    - Policy "General Expenses": receipt, memo and category required from $0.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = [
    "CODING_KEYS",
    "GL_CATEGORY_KEY",
    "GL_KEY_BY_CATEGORY",
    "RAMP_TOOL",
    "Expected",
    "FileReceipt",
    "RampCheck",
    "RampItem",
    "Staging",
    "expected_from_batch",
    "expected_from_file",
    "files_from_payload",
    "ramp_check",
    "ramp_items_in",
    "stage_files",
]

#: The connector tools the skill uses, named once.
RAMP_TOOL = {
    "recent": "mcp__claude_ai_Ramp__ramp_get_user_recent_reimbursements",
    "trips": "mcp__claude_ai_Ramp__ramp_get_user_trips",
    "upload_link": "mcp__claude_ai_Ramp__ramp_request_receipt_upload_link",
    "create": "mcp__claude_ai_Ramp__ramp_create_reimbursement_from_receipt",
    "edit": "mcp__claude_ai_Ramp__ramp_edit_reimbursement",
    "submit": "mcp__claude_ai_Ramp__ramp_submit_reimbursement",
}

#: Accounting coding, by the .env NAMES that hold it. Ramp returned these UUIDs
#: on his own rows on 2026-10-18 (CMG's NetSuite mapping: Department, Division,
#: Business Unit, and the GL "Category"); the repo is public, so the values live
#: in .env (see .env.example) and only the names are code. A missing name does
#: not invent a UUID: the step says which name to set and leaves that selection
#: out, so the edit call never carries a guess.
CODING_KEYS: dict[str, tuple[str, str]] = {
    "department": ("RAMP_TC_DEPARTMENT", "RAMP_TC_DEPARTMENT_OPTION"),
    "division": ("RAMP_TC_DIVISION", "RAMP_TC_DIVISION_OPTION"),
    "business_unit": ("RAMP_TC_BUSINESS_UNIT", "RAMP_TC_BUSINESS_UNIT_OPTION"),
}
GL_CATEGORY_KEY = "RAMP_TC_GL"

#: The travel GL for a receipt category, by the names the sources and Ramp use,
#: as the .env name that holds the option UUID.
GL_KEY_BY_CATEGORY: dict[str, str] = {
    "Rideshare & Taxis": "RAMP_GL_GROUND",
    "Taxi and Rideshare": "RAMP_GL_GROUND",
    "Ground Transportation": "RAMP_GL_GROUND",
    "Meals": "RAMP_GL_MEALS",
    "Restaurants": "RAMP_GL_MEALS",
    "Lodging": "RAMP_GL_LODGING",
    "Hotel": "RAMP_GL_LODGING",
    "Hotel & Lodging": "RAMP_GL_LODGING",
    "Airfare": "RAMP_GL_AIRFARE",
}
#: Every travel GL at CMG starts with this; anything else on a trip line is flagged.
TRAVEL_GL_PREFIX = "6071"

_MONEY = re.compile(r"-?[\d,]*\.?\d+")


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).replace("$", "").replace(",", "").strip()
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    for shape in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(str(value), shape).date()
        except ValueError:
            continue
    return None


def _day(day: date | None) -> str:
    return day.strftime("%b %-d").lower() if day else "undated"


# -- what Ramp holds ---------------------------------------------------------


@dataclass(frozen=True)
class RampItem:
    """One reimbursement as `ramp_get_user_recent_reimbursements` returns it."""

    id: str
    amount: Decimal
    merchant: str
    purchased_on: date | None
    status: str
    memo: str
    trip_uuid: str
    gl: str

    @property
    def link(self) -> str:
        return f"https://app.ramp.com/s/reimbursements/{self.id}"

    @property
    def travel_gl(self) -> bool:
        return self.gl.startswith(TRAVEL_GL_PREFIX)


def ramp_items_in(payload: Any) -> list[RampItem]:
    """Every reimbursement in a connector answer (or a bare list of them)."""
    if isinstance(payload, Mapping):
        rows = payload.get("reimbursements") or []
    else:
        rows = payload or []
    out: list[RampItem] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        amount = _decimal(row.get("amount_decimal") or row.get("amount"))
        if amount is None:
            continue
        gl = ""
        for sel in row.get("tracking_category_selections") or []:
            if isinstance(sel, Mapping) and sel.get("category_type") == "GL_ACCOUNT":
                opt = sel.get("option_selection") or {}
                gl = (
                    str(opt.get("option_name") or opt.get("name") or "")
                    if isinstance(opt, Mapping)
                    else ""
                )
        out.append(
            RampItem(
                id=str(row.get("id") or row.get("uuid") or ""),
                amount=amount,
                merchant=str(row.get("merchant_name") or ""),
                purchased_on=_date(row.get("transaction_date")),
                status=str(row.get("status") or ""),
                memo=str(row.get("memo") or ""),
                trip_uuid=str(row.get("trip_uuid") or ""),
                gl=gl,
            )
        )
    return out


# -- what the trip should have produced -------------------------------------


@dataclass(frozen=True)
class Expected:
    """One line the ledger says Ramp should hold."""

    purchased_on: date
    merchant: str
    amount: Decimal
    category: str = ""
    kind: str = "receipt"  # receipt | statement-line
    memo: str = ""
    permalink: str = ""


def expected_from_batch(batch: Any) -> list[Expected]:
    """The batch's rows (every status but outside-the-trip) as expected lines."""
    out: list[Expected] = []
    for row in getattr(batch, "rows", ()):
        if getattr(row, "status", "") == "outside":
            continue
        r = row.receipt
        out.append(
            Expected(
                purchased_on=r.purchased_on,
                merchant=r.merchant,
                amount=r.amount,
                category=r.category,
                kind="receipt",
                memo=r.memo,
                permalink=r.permalink,
            )
        )
    return out


def expected_from_file(payload: Any) -> list[Expected]:
    """`expected.json`: a list of {date, merchant, amount, category?, kind?, memo?}."""
    rows = payload.get("expected") if isinstance(payload, Mapping) else payload
    out: list[Expected] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        when = _date(row.get("date") or row.get("purchased_on"))
        amount = _decimal(row.get("amount"))
        if when is None or amount is None:
            raise ValueError(f"expected line needs a date and an amount: {dict(row)!r}")
        out.append(
            Expected(
                purchased_on=when,
                merchant=str(row.get("merchant") or ""),
                amount=amount,
                category=str(row.get("category") or ""),
                kind=str(row.get("kind") or "receipt"),
                memo=str(row.get("memo") or ""),
                permalink=str(row.get("permalink") or row.get("file") or ""),
            )
        )
    return out


# -- the check ---------------------------------------------------------------

PENDING_STATES = (
    "PENDING",
    "APPROVED",
    "AWAITING_PAYMENT",
    "AWAITING_OFF_RAMP_PAYMENT",
    "PROCESSING",
)


@dataclass(frozen=True)
class RampCheck:
    matched: tuple[tuple[Expected, RampItem], ...]
    expected_without_ramp: tuple[Expected, ...]
    ramp_without_expected: tuple[RampItem, ...]

    def total_ramp(self, *, states: Sequence[str] = PENDING_STATES) -> Decimal:
        items = [i for _, i in self.matched] + list(self.ramp_without_expected)
        return sum((i.amount for i in items if i.status in states), Decimal("0"))

    def total_expected(self) -> Decimal:
        rows = [e for e, _ in self.matched] + list(self.expected_without_ramp)
        return sum((e.amount for e in rows), Decimal("0"))

    def render(self) -> str:
        pending = self.total_ramp()
        drafts = self.total_ramp(states=("DRAFT",))
        expected = self.total_expected()
        lines = [
            f"ramp vs expected - {len(self.matched)} matched, "
            f"{len(self.expected_without_ramp)} expected w/o a ramp line, "
            f"{len(self.ramp_without_expected)} ramp lines w/o an expected",
            f"submitted or beyond in ramp ${pending:.2f} · expected ${expected:.2f} · "
            f"gap ${pending - expected:+.2f}"
            + (f" · drafts not counted ${drafts:.2f}" if drafts else ""),
            "",
        ]
        for e, i in sorted(self.matched, key=lambda p: (p[0].purchased_on, p[0].amount)):
            flags = []
            if not i.trip_uuid:
                flags.append("no trip")
            if i.gl and not i.travel_gl:
                flags.append(f"gl {i.gl}")
            if i.status not in PENDING_STATES:
                flags.append(i.status.lower())
            if i.purchased_on and i.purchased_on != e.purchased_on:
                flags.append(f"ramp date {_day(i.purchased_on)}")
            tail = f" ⚠ {', '.join(flags)}" if flags else ""
            lines.append(
                f"- ✓ {_day(e.purchased_on)} ${e.amount} {e.merchant} -> {i.status.lower()}"
                f"{tail} ({i.link})"
            )
        for e in sorted(self.expected_without_ramp, key=lambda e: (e.purchased_on, e.amount)):
            lines.append(
                f"- expected, not in ramp: {_day(e.purchased_on)} ${e.amount} {e.merchant} "
                f"({e.permalink or e.kind})"
            )
        for i in sorted(
            self.ramp_without_expected, key=lambda i: (i.purchased_on or date.min, i.amount)
        ):
            lines.append(
                f"- in ramp, not expected: {_day(i.purchased_on)} ${i.amount} {i.merchant} "
                f"{i.status.lower()} ({i.link}) - amount or date differs from the ledger, "
                "or it is another trip"
            )
        return "\n".join(lines)


def ramp_check(
    expected: Iterable[Expected], items: Iterable[RampItem], *, days: int = 3
) -> RampCheck:
    """Pair expected lines with Ramp items on amount, within ``days``. Amount is the join;
    a line whose amount Ramp read differently shows up on both leftover lists."""
    want = list(expected)
    have = list(items)
    candidates: list[tuple[int, int, int]] = []
    for i, e in enumerate(want):
        for j, item in enumerate(have):
            if item.amount != e.amount or item.purchased_on is None:
                continue
            gap = abs((item.purchased_on - e.purchased_on).days)
            if gap <= days:
                candidates.append((gap, i, j))
    used_e: set[int] = set()
    used_r: set[int] = set()
    matched: list[tuple[Expected, RampItem]] = []
    for _, i, j in sorted(candidates):
        if i in used_e or j in used_r:
            continue
        used_e.add(i)
        used_r.add(j)
        matched.append((want[i], have[j]))
    return RampCheck(
        tuple(matched),
        tuple(e for i, e in enumerate(want) if i not in used_e),
        tuple(r for j, r in enumerate(have) if j not in used_r),
    )


# -- files into ramp ---------------------------------------------------------


@dataclass(frozen=True)
class FileReceipt:
    """A receipt that exists as a file on his disk, with what he said it is."""

    path: str
    merchant: str
    amount: Decimal
    purchased_on: date
    category: str
    memo: str
    currency: str = "USD"


def files_from_payload(payload: Any) -> list[FileReceipt]:
    """`files.json`: a list of {file, merchant, amount, date, category, memo}."""
    rows = payload.get("files") if isinstance(payload, Mapping) else payload
    out: list[FileReceipt] = []
    for row in rows or []:
        when = _date(row.get("date"))
        amount = _decimal(row.get("amount"))
        if not row.get("file") or when is None or amount is None:
            raise ValueError(f"file line needs file, date and amount: {dict(row)!r}")
        out.append(
            FileReceipt(
                path=str(row["file"]),
                merchant=str(row.get("merchant") or ""),
                amount=amount,
                purchased_on=when,
                category=str(row.get("category") or ""),
                memo=str(row.get("memo") or ""),
                currency=str(row.get("currency") or "USD"),
            )
        )
    return out


def coding_for(
    category: str, identities: Mapping[str, str] | None
) -> tuple[list[dict[str, str]], list[str]]:
    """The tracking selections a trip line carries, resolved from .env.

    Returns the selections and the .env names that were missing. The fixed three
    (Department, Division, Business Unit) plus the travel GL for ``category``;
    a category with no travel GL adds the GL category's name to ``missing`` so
    the step says to pick one by hand.
    """
    ids: Mapping[str, str] = identities or {}
    selections: list[dict[str, str]] = []
    missing: list[str] = []
    gl_key = GL_KEY_BY_CATEGORY.get(category)
    if gl_key is None:
        missing.append(f"no travel GL known for category {category!r}")
    else:
        cat, opt = ids.get(GL_CATEGORY_KEY), ids.get(gl_key)
        if cat and opt:
            selections.append({"category_uuid": cat, "option_selection": opt})
        else:
            missing += [k for k, v in ((GL_CATEGORY_KEY, cat), (gl_key, opt)) if not v]
    for cat_key, opt_key in CODING_KEYS.values():
        cat, opt = ids.get(cat_key), ids.get(opt_key)
        if cat and opt:
            selections.append({"category_uuid": cat, "option_selection": opt})
        else:
            missing += [k for k, v in ((cat_key, cat), (opt_key, opt)) if not v]
    return selections, missing


@dataclass(frozen=True)
class Staging:
    """The four steps for one file. Placeholders are filled from the previous step's answer."""

    n: int
    receipt: FileReceipt
    trip_uuid: str = ""
    identities: Mapping[str, str] | None = None

    def steps(self) -> list[dict[str, Any]]:
        r = self.receipt
        why = f"receipt {self.n}: {r.merchant} ${r.amount} {r.purchased_on.isoformat()}"
        selections, missing = coding_for(r.category, self.identities)
        edit_args: dict[str, Any] = {
            "rationale": f"{why} - memo, trip and coding so it matches the other lines of the trip",
            "reimbursement_uuid": "<reimbursement_uuid from step 3>",
            "memo": r.memo,
            "tracking_category_selections": selections,
        }
        if self.trip_uuid:
            edit_args["trip_uuid"] = self.trip_uuid
        return [
            {
                "step": 1,
                "tool": RAMP_TOOL["upload_link"],
                "args": {
                    "rationale": f"{why} - one-time link to upload the receipt file from disk"
                },
            },
            {
                "step": 2,
                "shell": f"curl -sS -F \"file=@{r.path}\" '<upload_url from step 1>'",
                "note": "single use, 30 minutes; the 201 body carries receipt_uuid",
            },
            {
                "step": 3,
                "tool": RAMP_TOOL["create"],
                "args": {
                    "rationale": (
                        f"{why} - draft from the uploaded receipt, "
                        "amount/merchant/date given so OCR is skipped"
                    ),
                    "receipt_uuid": "<receipt_uuid from step 2>",
                    "amount": f"{r.amount:.2f}",
                    "currency_code": r.currency,
                    "transaction_date": r.purchased_on.isoformat(),
                    "merchant": r.merchant,
                },
            },
            {
                "step": 4,
                "tool": RAMP_TOOL["edit"],
                "args": edit_args,
                "note": (
                    ""
                    if not missing
                    else "coding left out, set in .env or pick by hand: " + "; ".join(missing)
                ),
            },
        ]


def stage_files(
    files: Sequence[FileReceipt],
    *,
    trip_uuid: str = "",
    identities: Mapping[str, str] | None = None,
) -> list[Staging]:
    return [Staging(n, f, trip_uuid, identities) for n, f in enumerate(files, start=1)]


def render_staging(stagings: Sequence[Staging]) -> str:
    head = [
        f"files into ramp - {len(stagings)} receipt(s), four steps each; "
        "nothing runs until he says go",
        "submit is a separate go: `submit 1 2` -> ramp_submit_reimbursement per uuid",
        "",
    ]
    body = json.dumps(
        [{"n": s.n, "file": s.receipt.path, "steps": s.steps()} for s in stagings], indent=2
    )
    return "\n".join(head) + body
