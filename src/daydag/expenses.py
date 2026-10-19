"""Receipts into Rippling Spend: sources in, one sink out, nothing sent from here.

USING IT
    python -m daydag.expenses trip calendar.json --out trip.json [--name "Trip to LA"]
    #   -> the trip the calendar describes: dates, flights, the stay, the places
    #      he had reason to be; calendar.json is the list_events answers, day by day
    python -m daydag.expenses plan --trip trip.json        # or --since/--until by hand
    #   -> one Gmail query per source, bounded, already excluding `expensed`
    #   ... the agent runs each over the connector and saves the answers as
    #       {"uber": <search_threads result>, "doordash": ...} ...
    python -m daydag.expenses review payloads.json --log events.db --trip trip.json \
        --out <outside the repo>/batch.json
    #   -> the numbered list for his DM; batch.json holds the rows behind it.
    #      With a trip, a receipt is offered only when it is placed INSIDE it
    python -m daydag.expenses go batch.json 1 3        # the forward calls for those rows
    #   ... the agent makes each call, then labels the message `expensed` ...
    python -m daydag.expenses mark batch.json 1 3 --log events.db
    python -m daydag.expenses check batch.json confirmations.json --log events.db
    #   -> which forwards Rippling answered with "Draft reimbursement created",
    #      and the draft's link; confirmations.json is the search answer for
    #      from:receipts@rippling.com
    python -m daydag.expenses reconcile batch.json --amex activity.csv
    python -m daydag.expenses report batch.json [--out report.md]   # by day, with subtotals

    trip_from_calendar(events, name="Trip to LA")    # -> Trip, or None without a flight or stay
    receipts_in("uber", payload)                     # -> Harvest: receipts + skipped
    review({"uber": harvest}, filed=forwarded_rows(log), trip=trip)
    place(receipt, trip)                             # -> (NEW | UNPLACED | OUTSIDE, why)
    go(batch, [1, 3]) / go(batch, "all")             # -> [Forward], each one MCP call
    mark_forwarded(log, rows, report=..., now=...)
    by_day(batch).render()                           # the expense report, day by day
    drafts_in(payload)                               # -> [Draft], Rippling's confirmations
    check(batch, drafts).render()                    # forwarded rows against the drafts
    reconcile(receipts, charges_from_csv(text))

CONTRACTS - break one and the guarantee is gone
    1. Nothing here sends. `Forward.call()` is a dict the agent hands to the
       Gmail connector AFTER he says go; the package holds no mail client (the
       tripwire in tests/test_guardrails.py). `go` prints, `mark` logs.
    2. One recipient, fixed: `RIPPLING_RECEIPTS_INBOX`. `Forward` has no `to`,
       `cc` or `bcc` field a caller could set. Rippling matches the receipt to
       his account by the From header, so any other address is a send to
       someone else (house rule 2).
    3. A charge summary is never a receipt. Uber mails "This is not a payment
       receipt" first and the receipt hours later under the SAME subject;
       forwarding the summary files nothing and spends his go.
    4. One receipt per purchase. Uber's tip mail restates the whole receipt
       with a higher total: the newest mail for a trip wins and names what it
       supersedes. A trip already forwarded that has changed since is FLAGGED
       for a named re-send, and `go all` leaves it alone.
    5. Idempotent on the Gmail message id, two ways: the `expenses.forwarded`
       row in the event log, and the `expensed` label the plan's query
       excludes. Either alone has already failed in the ingest loop (#168).
    6. Every rendered row carries its permalink (invariant 3). A receipt with
       no permalink is refused at construction.
    7. The query window is capped at `MAX_WINDOW_DAYS`. Overflow at the
       connector is silent, and a year of Uber mail is several thousand rows.
    8. A forward is confirmed by Rippling's own reply, nothing else. Within a
       minute `receipts@rippling.com` answers in the SAME thread with "Draft
       reimbursement created" and the draft's link; `check` reads that. A
       forward with no reply is reported as unconfirmed, never assumed filed
       in Rippling - the log says it was sent, the reply says it arrived.
    9. With a trip, `go all` sends only what is PLACED inside it: dated within
       the window and at the destination, at the hotel, at one of its airports,
       or in its state. A receipt in the window with no place on it, or a place
       that does not fit, is listed and waits for a go by number. One outside
       the dates is listed and not offered. The trip is built from the calendar
       he already keeps, never from the receipts themselves.

WHY IT EXISTS
    He files reimbursements in Rippling Spend by hand, one report per trip
    ("Trip to LA", "Steerco in LA"), paid with a personal Amex and repaid
    through payroll. The receipts already sit in Gmail under a label he made
    ("uber receipts"). The work is finding the ones not yet filed, dropping
    the duplicates Uber sends, and getting each into Rippling with a memo and
    a category he does not have to retype.

    Rippling's one employee-side intake that needs no admin is its receipts
    inbox: mail forwarded there from the address on file lands in his Spend
    receipts, where he completes category, memo and report (see
    reference/rippling-expenses.md). So the sink is a Gmail forward, and an
    API sink stays an adapter with no implementation until Rippling publishes
    the endpoints and an admin issues a token - neither existed on 2026-10-15.

KNOWN LIMIT
    Uber is the one source parsed against real mail. DoorDash and anything
    else go through the generic reader, which takes a Total line and the
    message date, and says so. Amex has no mail in his work inbox at all; its
    adapter reads the activity CSV he downloads, and only to reconcile - a
    statement line is not a receipt Rippling will accept.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from daydag.config import Identities, timezone_for
from daydag.state import EventLog

__all__ = [
    "CHANGED",
    "EXPENSED_LABEL",
    "FILED",
    "FORWARDED",
    "GMAIL_FORWARD_TOOL",
    "MAX_WINDOW_DAYS",
    "NEW",
    "OUTSIDE",
    "RECEIPT_LAG_DAYS",
    "RIPPLING_RECEIPTS_INBOX",
    "SOURCES",
    "UNPLACED",
    "Anchor",
    "Batch",
    "Charge",
    "DayGroup",
    "Draft",
    "ExpenseError",
    "Flight",
    "Forward",
    "Harvest",
    "Receipt",
    "Reconciliation",
    "Report",
    "Row",
    "Skipped",
    "Source",
    "Stay",
    "Trip",
    "by_day",
    "charges_from_csv",
    "check",
    "drafts_in",
    "forward_spec",
    "forwarded_rows",
    "gmail_query",
    "go",
    "main",
    "mark_forwarded",
    "messages_in",
    "place",
    "plan",
    "propose_report",
    "receipts_in",
    "reconcile",
    "review",
    "trip_from_calendar",
]

#: Where a forwarded receipt lands. Since 2026-10-18 that is Ramp: a mail to
#: Ramp's reimbursements inbox becomes one draft reimbursement per receipt
#: (support.ramp.com, "Submitting reimbursements"); the account it files under
#: comes from the From header. The Rippling inbox it replaced is kept by name
#: so the log can say which system an older forward went to.
RECEIPTS_INBOX = "reimbursements@ramp.com"
RIPPLING_RECEIPTS_INBOX = RECEIPTS_INBOX

#: The Gmail label the agent applies after a forward. The plan excludes it, so
#: a receipt is offered once even when the log is unavailable.
EXPENSED_LABEL = "expensed"

#: Event-log kind for one forward. Never projected into the vault.
FORWARDED = "expenses.forwarded"

#: The connector call a `Forward` turns into. Named once, here.
GMAIL_FORWARD_TOOL = "mcp__claude_ai_Gmail__forward"

#: Days the search window runs past the trip: Uber mails the receipt hours
#: after the ride, so a last-day ride's receipt arrives the next morning.
#: Placement still bounds by purchase date.
RECEIPT_LAG_DAYS = 2

#: Widest window `plan` will build. A quarter, roughly: wide enough to catch a
#: trip he forgot, narrow enough that the search answer arrives.
MAX_WINDOW_DAYS = 93

#: Rippling category names, spelled as they appear on his own approved expenses.
RIDESHARE = "Rideshare & Taxis"
MEALS = "Meals"

NEW, FILED, CHANGED = "new", "filed", "changed"
#: Against a trip: in the window but nowhere the trip explains, or outside it.
UNPLACED, OUTSIDE = "unplaced", "outside"


class ExpenseError(RuntimeError):
    """A receipt, plan or batch that cannot be trusted to mean what it says."""


# -- sources -----------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """One place receipts come from, and the Rippling category they default to.

    ``verified`` says whether the reader was written against real mail from
    this sender. An unverified source still runs; the render says it is one.
    """

    name: str
    sender: str
    subject: str
    category: str
    merchant: str
    verified: bool


SOURCES: dict[str, Source] = {
    "uber": Source("uber", "noreply@uber.com", "trip with Uber", RIDESHARE, "Uber", True),
    "doordash": Source("doordash", "no-reply@doordash.com", "", MEALS, "DoorDash", False),
}


def gmail_query(source: Source, since: date, until: date) -> str:
    """The one Gmail search for ``source`` over ``[since, until]``, inclusive.

    Raises:
        ExpenseError: ``until`` before ``since``, or a window past the cap.
    """
    if until < since:
        raise ExpenseError(f"until {until} is before since {since}.")
    if (until - since).days > MAX_WINDOW_DAYS:
        raise ExpenseError(
            f"window of {(until - since).days} days is past the {MAX_WINDOW_DAYS}-day cap; "
            "run it in pieces - an overflowing search returns nothing, silently."
        )
    parts = [f"from:{source.sender}"]
    if source.subject:
        parts.append(f'subject:"{source.subject}"')
    parts.append(f"after:{since:%Y/%m/%d}")
    # Gmail's `before:` is exclusive, so the day after keeps `until` inside.
    parts.append(f"before:{until + timedelta(days=1):%Y/%m/%d}")
    parts.append(f"-label:{EXPENSED_LABEL}")
    return " ".join(parts)


def plan(
    since: date, until: date, sources: Iterable[str] | None = None
) -> dict[str, dict[str, Any]]:
    """What to fetch: one bounded query per source, plus what to do with the hits."""
    names = list(sources) if sources is not None else list(SOURCES)
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        source = _source(name)
        out[name] = {
            "query": gmail_query(source, since, until),
            "verified": source.verified,
            "then": (
                "save the search_threads answer as-is under this key; fetch a message "
                "PLAIN_TEXT only when its snippet has no Total line"
            ),
        }
    return out


def _source(name: str) -> Source:
    try:
        return SOURCES[name]
    except KeyError:
        known = ", ".join(sorted(SOURCES))
        raise ExpenseError(f"no source {name!r}; known: {known}.") from None


# -- receipts ----------------------------------------------------------------


@dataclass(frozen=True)
class Receipt:
    """One purchase, with the mail that proves it and what Rippling will ask for."""

    source: str
    key: str
    merchant: str
    amount: Decimal
    purchased_on: date
    message_id: str
    permalink: str
    memo: str
    category: str
    paid_with: str = ""
    received_at: str = ""
    supersedes: tuple[str, ...] = ()
    #: Where it happened, as the mail says it: the addresses on it, the
    #: airports it names, the state its fee lines name. Empty is "no idea".
    stops: tuple[str, ...] = ()
    airports: tuple[str, ...] = ()
    state: str = ""
    #: The Gmail thread. Rippling's confirmation lands in it, so it is the join.
    thread_id: str = ""

    def __post_init__(self) -> None:
        if not self.permalink:
            raise ExpenseError(
                f"receipt {self.key} has no permalink; a row he cannot open is a claim."
            )

    def payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "key": self.key,
            "merchant": self.merchant,
            "amount": str(self.amount),
            "purchased_on": self.purchased_on.isoformat(),
            "message_id": self.message_id,
            "permalink": self.permalink,
            "memo": self.memo,
            "category": self.category,
            "paid_with": self.paid_with,
            "received_at": self.received_at,
            "supersedes": list(self.supersedes),
            "stops": list(self.stops),
            "airports": list(self.airports),
            "state": self.state,
            "thread_id": self.thread_id,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Receipt:
        return cls(
            source=str(payload["source"]),
            key=str(payload["key"]),
            merchant=str(payload["merchant"]),
            amount=Decimal(str(payload["amount"])),
            purchased_on=date.fromisoformat(str(payload["purchased_on"])),
            message_id=str(payload["message_id"]),
            permalink=str(payload["permalink"]),
            memo=str(payload.get("memo") or ""),
            category=str(payload.get("category") or ""),
            paid_with=str(payload.get("paid_with") or ""),
            received_at=str(payload.get("received_at") or ""),
            supersedes=tuple(str(s) for s in payload.get("supersedes") or ()),
            stops=tuple(str(s) for s in payload.get("stops") or ()),
            airports=tuple(str(a) for a in payload.get("airports") or ()),
            state=str(payload.get("state") or ""),
            thread_id=str(payload.get("thread_id") or ""),
        )


@dataclass(frozen=True)
class Skipped:
    """A mail that was looked at and is not a receipt, and why."""

    message_id: str
    reason: str
    permalink: str = ""

    def payload(self) -> dict[str, str]:
        return {"message_id": self.message_id, "reason": self.reason, "permalink": self.permalink}


@dataclass(frozen=True)
class Harvest:
    """What one source yielded: the receipts worth offering, and the rest, named."""

    receipts: tuple[Receipt, ...]
    skipped: tuple[Skipped, ...]


def messages_in(payload: Any) -> list[Mapping[str, Any]]:
    """Every message in a connector answer, whatever shape it arrived in.

    Takes the raw `search_threads` result (threads holding messages), a thread,
    a list of messages, or one message. Anything else is no messages.
    """
    if isinstance(payload, Mapping):
        if "threads" in payload:
            return [m for thread in payload["threads"] or () for m in messages_in(thread)]
        if "messages" in payload:
            return [m for m in payload["messages"] or () if isinstance(m, Mapping)]
        return [payload] if "id" in payload else []
    if isinstance(payload, list):
        return [m for item in payload for m in messages_in(item)]
    return []


_STAMP = re.compile(r"([A-Z][a-z]{2} \d{1,2}, \d{4})\s*,?\s*(\d{1,2}:\d{2} [AP]M)")
_TOTAL = re.compile(r"\btotal\b[^$\d\n]{0,40}\$\s?([\d,]+\.\d{2})", re.IGNORECASE)
_CARD = re.compile(r"([A-Za-z][A-Za-z ]{2,40}?)\s*(•{4}\s?\d{4})")
_ADDRESS = re.compile(r"([^|\n]+?,\s*[^|\n]+?,\s*[A-Z]{2} \d{5}(?:-\d{4})?,\s*USA)")
#: An airport named on the receipt: "(BUR)" in an address, "BUR Airport
#: Surcharge" or "SeaTac Airport Pickup Fee" among the fee lines.
_AIRPORT_CODE = re.compile(r"\(([A-Z]{3})\)|\b([A-Z]{3}) Airport\b")
_AIRPORT_NAMES = {"seatac": "SEA"}
_STATE_FEE = re.compile(r"\b([A-Z]{2}) Driver (?:Benefits|Resource)")


#: Three capitals in parentheses that are not an airport.
_NOT_AIRPORTS = frozenset(
    {"PDT", "PST", "EDT", "EST", "CDT", "CST", "MDT", "MST", "UTC", "GMT", "USA", "LLC"}
)


def _airports(text: str) -> tuple[str, ...]:
    found = [a or b for a, b in _AIRPORT_CODE.findall(text) if (a or b) not in _NOT_AIRPORTS]
    low = text.lower()
    found += [code for name, code in _AIRPORT_NAMES.items() if f"{name} airport" in low]
    return tuple(dict.fromkeys(found))


def _state(text: str) -> str:
    match = _STATE_FEE.search(text)
    return match.group(1) if match else ""


def _field(message: Mapping[str, Any], *names: str) -> str:
    """The first of ``names`` the connector set. It spells its keys both ways."""
    for name in names:
        value = message.get(name)
        if value:
            return str(value)
    return ""


def _text(message: Mapping[str, Any]) -> str:
    """The body if the agent fetched it, else the search snippet. Both carry the total."""
    body = _field(message, "plaintextBody", "plaintext_body") or _field(message, "snippet")
    return html.unescape(body)


#: What a snippet runs into an address: a clock, a total, an earlier address's USA.
_STOP_JUNK = re.compile(r"\d{1,2}:\d{2} [AP]M|\$[\d,]+\.\d{2}|\bUSA\b")


def _clean_stop(address: str) -> str:
    """An address as matched, from the last clock, total or earlier address before it."""
    address = address.strip()
    cut = 0
    for match in _STOP_JUNK.finditer(address):
        if match.end() < len(address) - 3:  # the closing USA is the address's own
            cut = match.end()
    return address[cut:].strip(" ,")


def _amount(text: str) -> Decimal | None:
    match = _TOTAL.search(text)
    if match is None:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def _city(address: str) -> str:
    parts = [p.strip() for p in address.split(",")]
    return parts[-3] if len(parts) >= 3 else parts[0]


def _area(address: str) -> str:
    """The zip's first three digits: one area, whatever Uber calls the city.

    Anchored on the state code or the end of the address: a street number can
    have five digits too.
    """
    match = re.search(r"\b[A-Z]{2}\s+(\d{3})\d{2}(?:-\d{4})?\b", address) or re.search(
        r"\b(\d{3})\d{2}(?:-\d{4})?\s*(?:,\s*USA)?\s*$", address.strip()
    )
    return match.group(1) if match else _city(address)


def _place(address: str, *, same_city: bool) -> str:
    """The city, or the street when both ends are in one area ("Elm St -> Oak Ave")."""
    if not same_city:
        return _city(address)
    street = re.sub(r"^\d+[\w-]*\s+", "", address.split(",")[0].strip())
    return street or _city(address)


def _stamp(raw: str) -> datetime | None:
    """An ISO stamp as the connector writes it (`...Z` or an offset), or None."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _received(message: Mapping[str, Any]) -> datetime | None:
    return _stamp(str(message.get("date") or ""))


def _clock_minutes(clock: str) -> int:
    try:
        when = datetime.strptime(clock, "%I:%M %p")
    except ValueError:
        return -1
    return when.hour * 60 + when.minute


def _uber(message: Mapping[str, Any], source: Source) -> Receipt | Skipped:
    mid = str(message.get("id") or "")
    link = _field(message, "viewUrl", "view_url")
    if not link:
        return Skipped(mid, "no permalink on the message", "")
    text = _text(message)
    low = text.lower()
    if "not a payment receipt" in low or "charge summary" in low:
        return Skipped(mid, "charge summary, not a receipt", link)
    if "thanks for riding" not in low and "thanks for tipping" not in low:
        return Skipped(mid, "not the receipt uber sends after a ride", link)
    stamp = _STAMP.search(text)
    if stamp is None:
        return Skipped(mid, "no trip time", link)
    amount = _amount(text)
    if amount is None:
        return Skipped(mid, "no total line", link)
    when = datetime.strptime(f"{stamp.group(1)} {stamp.group(2)}", "%b %d, %Y %I:%M %p")
    card = _CARD.search(text)
    paid = f"{card.group(1).strip()} {card.group(2).replace(' ', '')}" if card else ""
    stops = list(dict.fromkeys(_clean_stop(a) for a in _ADDRESS.findall(text)))[:2]
    memo = "uber"
    if len(stops) == 2:
        same = _area(stops[0]) == _area(stops[1])
        memo = f"uber {_place(stops[0], same_city=same)} -> {_place(stops[1], same_city=same)}"
    return Receipt(
        source=source.name,
        key=f"uber:{when.isoformat(timespec='minutes')}",
        merchant=source.merchant,
        amount=amount,
        purchased_on=when.date(),
        message_id=mid,
        permalink=link,
        memo=memo,
        category=source.category,
        paid_with=paid,
        received_at=str(message.get("date") or ""),
        stops=tuple(stops),
        airports=_airports(text),
        state=_state(text),
        thread_id=_field(message, "threadId", "thread_id"),
    )


def _generic(message: Mapping[str, Any], source: Source) -> Receipt | Skipped:
    """A Total line and the message date. Honest about being no more than that."""
    mid = str(message.get("id") or "")
    link = _field(message, "viewUrl", "view_url")
    if not link:
        return Skipped(mid, "no permalink on the message", "")
    text = _text(message)
    amount = _amount(text)
    if amount is None:
        return Skipped(mid, "no total line", link)
    received = _received(message)
    if received is None:
        return Skipped(mid, "no message date", link)
    stops = tuple(dict.fromkeys(_clean_stop(a) for a in _ADDRESS.findall(text)))
    return Receipt(
        source=source.name,
        key=f"{source.name}:{mid}",
        merchant=source.merchant,
        amount=amount,
        purchased_on=received.astimezone(timezone_for()).date(),
        message_id=mid,
        permalink=link,
        memo=str(message.get("subject") or "")[:80],
        category=source.category,
        received_at=str(message.get("date") or ""),
        stops=stops,
        thread_id=_field(message, "threadId", "thread_id"),
    )


def receipts_in(source_name: str, payload: Any) -> Harvest:
    """Every receipt in a connector answer for one source, one per purchase.

    Where several mails describe the same purchase (Uber's ride receipt and the
    tip restatement that follows), the newest by message date is the receipt
    and the earlier ids are on its ``supersedes``.
    """
    source = _source(source_name)
    parse = _uber if source_name == "uber" else _generic
    versions: dict[str, list[Receipt]] = {}
    skipped: list[Skipped] = []
    for message in messages_in(payload):
        got = parse(message, source)
        if isinstance(got, Skipped):
            skipped.append(got)
        else:
            versions.setdefault(got.key, []).append(got)
    receipts: list[Receipt] = []
    for found in versions.values():
        found.sort(key=lambda r: _stamp(r.received_at) or datetime.min.replace(tzinfo=UTC))
        newest = found[-1]
        receipts.append(replace(newest, supersedes=tuple(r.message_id for r in found[:-1])))
    receipts.sort(key=lambda r: (r.purchased_on, r.key))
    return Harvest(tuple(receipts), tuple(skipped))


# -- the sink ----------------------------------------------------------------


@dataclass(frozen=True)
class Forward:
    """One Gmail forward, to the one address, made by the agent after his go.

    No recipient field on purpose (contract 2); ``to`` is a property.
    """

    message_id: str
    note: str

    @property
    def to(self) -> tuple[str, ...]:
        return (RIPPLING_RECEIPTS_INBOX,)

    def call(self) -> dict[str, Any]:
        """The connector call, as `{"tool": ..., "args": ...}`."""
        return {
            "tool": GMAIL_FORWARD_TOOL,
            "args": {
                "messageId": self.message_id,
                "to": list(self.to),
                "forwardText": self.note,
            },
        }


def forward_spec(receipt: Receipt, *, report: str = "") -> Forward:
    """The forward for one receipt, carrying what he would otherwise retype.

    The note is plain text at the top of the forwarded mail: the fields a
    Rippling expense asks for (vendor, amount, date, category, memo, report),
    each on its own line, so whatever Rippling's reader takes from the mail
    body finds them and he can paste the rest.
    """
    lines = [
        "receipt for ramp",
        f"vendor: {receipt.merchant}",
        f"amount: ${receipt.amount}",
        f"date: {receipt.purchased_on.isoformat()}",
        f"category: {receipt.category}",
        f"memo: {receipt.memo}",
    ]
    if report:
        lines.append(f"report: {report}")
    return Forward(message_id=receipt.message_id, note="\n".join(lines))


# -- the trip: what the calendar says, the frame the receipts are placed in --


@dataclass(frozen=True)
class Flight:
    """One flight event, as Gmail puts it on his calendar."""

    day: date
    departs: str
    arrives: str
    origin: str
    to_city: str
    number: str
    permalink: str

    def payload(self) -> dict[str, str]:
        return {
            "day": self.day.isoformat(),
            "departs": self.departs,
            "arrives": self.arrives,
            "origin": self.origin,
            "to_city": self.to_city,
            "number": self.number,
            "permalink": self.permalink,
        }


@dataclass(frozen=True)
class Stay:
    """Where he slept: the hotel block on the calendar."""

    name: str
    address: str
    check_in: date
    check_out: date
    confirmation: str
    permalink: str

    def payload(self) -> dict[str, str]:
        return {
            "name": self.name,
            "address": self.address,
            "check_in": self.check_in.isoformat(),
            "check_out": self.check_out.isoformat(),
            "confirmation": self.confirmation,
            "permalink": self.permalink,
        }


@dataclass(frozen=True)
class Anchor:
    """A place he had reason to be: a timed event with a street address."""

    day: date
    title: str
    address: str
    permalink: str

    def payload(self) -> dict[str, str]:
        return {
            "day": self.day.isoformat(),
            "title": self.title,
            "address": self.address,
            "permalink": self.permalink,
        }


#: State names as a calendar address spells them, to the code a receipt uses.
_STATES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
}


@dataclass(frozen=True)
class Trip:
    """The frame: dates, flights, the stay, and the places that explain a ride."""

    name: str
    start: date
    end: date
    flights: tuple[Flight, ...] = ()
    stay: Stay | None = None
    anchors: tuple[Anchor, ...] = ()

    def airports(self) -> frozenset[str]:
        return frozenset(f.origin for f in self.flights if f.origin)

    def area(self) -> str:
        """The zip prefix of the stay, where "at the destination" is decided."""
        if not self.stay or not re.search(r"\b\d{5}\b", self.stay.address):
            return ""
        return _area(self.stay.address)

    def state(self) -> str:
        """The stay's state as a two-letter code, from a code or a spelled-out name."""
        if not self.stay:
            return ""
        address = self.stay.address
        match = re.search(r"\b([A-Z]{2})\b[ ,]+\d{5}", address) or re.search(
            r",\s*([A-Z]{2})\b", address
        )
        if match:
            return match.group(1)
        # From the end: the state follows the city, and a city can be Washington.
        for part in (p.strip().lower() for p in reversed(address.split(","))):
            if part in _STATES:
                return _STATES[part]
        return ""

    def line(self) -> str:
        """One line for a header: name, dates, flights, stay."""
        bits = [f'trip "{self.name}" {_day(self.start)} - {_day(self.end)}']
        for f in self.flights:
            bits.append(f"{f.origin} -> {f.to_city} {f.number} {_day(f.day)} {f.departs}".strip())
        if self.stay:
            bits.append(
                f"stay {self.stay.name} ({_day(self.stay.check_in)} - {self.stay.check_out.day})"
            )
        return " · ".join(bits)

    def render(self) -> str:
        lines = [f'trip "{self.name}" - {_day(self.start)} to {_day(self.end)}']
        for f in self.flights:
            lines.append(
                f"- flight {_day(f.day)} {f.departs} {f.origin} -> {f.to_city} {f.number}, "
                f"lands {f.arrives} ({f.permalink})"
            )
        if self.stay:
            s = self.stay
            conf = f", confirmation {s.confirmation}" if s.confirmation else ""
            lines.append(
                f"- stay {s.name}, {s.address}, {_day(s.check_in)} to {_day(s.check_out)}"
                f"{conf} ({s.permalink})"
            )
        for a in self.anchors:
            lines.append(f"- {_day(a.day)} {a.title} at {a.address} ({a.permalink})")
        if not self.flights and not self.stay:
            lines.append("no flight or stay on the calendar - the dates are the only frame")
        lines.append(
            "flights and the stay the travel desk booked are theirs to pay, not yours to "
            "claim - only what you paid goes in the report"
        )
        return "\n".join(lines)

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "flights": [f.payload() for f in self.flights],
            "stay": self.stay.payload() if self.stay else None,
            "anchors": [a.payload() for a in self.anchors],
        }

    @classmethod
    def from_payload(cls, data: Mapping[str, Any]) -> Trip:
        stay = data.get("stay")
        return cls(
            name=str(data["name"]),
            start=date.fromisoformat(str(data["start"])),
            end=date.fromisoformat(str(data["end"])),
            flights=tuple(
                Flight(
                    day=date.fromisoformat(str(f["day"])),
                    departs=str(f.get("departs") or ""),
                    arrives=str(f.get("arrives") or ""),
                    origin=str(f.get("origin") or ""),
                    to_city=str(f.get("to_city") or ""),
                    number=str(f.get("number") or ""),
                    permalink=str(f.get("permalink") or ""),
                )
                for f in data.get("flights") or ()
            ),
            stay=Stay(
                name=str(stay["name"]),
                address=str(stay.get("address") or ""),
                check_in=date.fromisoformat(str(stay["check_in"])),
                check_out=date.fromisoformat(str(stay["check_out"])),
                confirmation=str(stay.get("confirmation") or ""),
                permalink=str(stay.get("permalink") or ""),
            )
            if stay
            else None,
            anchors=tuple(
                Anchor(
                    day=date.fromisoformat(str(a["day"])),
                    title=str(a.get("title") or ""),
                    address=str(a.get("address") or ""),
                    permalink=str(a.get("permalink") or ""),
                )
                for a in data.get("anchors") or ()
            ),
        )


_FLIGHT = re.compile(r"^Flight to (?P<city>.+?)\s*\((?P<number>[A-Z0-9]{2} ?\d{1,5})\)\s*$")
_IATA = re.compile(r"\b([A-Z]{3})\s*$")
_CONFIRMATION = re.compile(r"Confirmation number:\s*([A-Za-z0-9-]+)")
_ENDS = re.compile(r"Ends:\s*(\d{4}-\d{2}-\d{2})")
_STARTS = re.compile(r"Starts:\s*(\d{4}-\d{2}-\d{2})")
_STREET = re.compile(r"(?<![\w-])\d+[A-Za-z]?\s+[A-Z][a-z]")


def _address_line(description: str) -> str:
    """The description line that carries the stay's zip, never the confirmation line."""
    for line in description.splitlines():
        if _CONFIRMATION.search(line):
            continue
        if re.search(r"\b(?:USA|[A-Z]{2})\s+\d{5}\b|,\s*\d{5}\b", line):
            return line.strip()
    return ""


def _when(value: Any) -> tuple[date | None, str, bool]:
    """(local day, clock, all_day) from a calendar start or end object."""
    if not isinstance(value, Mapping):
        return None, "", False
    if value.get("date"):
        return date.fromisoformat(str(value["date"])[:10]), "", True
    raw = str(value.get("dateTime") or "")
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, "", False
    return when.date(), f"{when:%I:%M %p}".lstrip("0").lower(), False


def _events_in(payload: Any) -> list[Mapping[str, Any]]:
    """Every event in one or several `list_events` answers."""
    if isinstance(payload, Mapping):
        for key in ("events", "items"):
            if key in payload:
                return [e for e in payload[key] or () if isinstance(e, Mapping)]
        return [payload] if "summary" in payload else []
    if isinstance(payload, list):
        return [e for item in payload for e in _events_in(item)]
    return []


def _city_of(address: str) -> str:
    """The city: the part after the street, in a calendar address or a receipt's."""
    parts = [p.strip() for p in re.split(r",|\s{2,}", address) if p.strip()]
    for i, part in enumerate(parts[:-1]):
        if re.search(r"\b\d+\s+\w", part):
            nxt = parts[i + 1]
            if re.match(r"^[A-Z][A-Za-z.' -]*$", nxt):
                return nxt
    return ""


def trip_from_calendar(payload: Any, *, name: str = "") -> Trip | None:
    """The trip the calendar describes, or None when it holds no flight and no stay.

    Flights are the `Flight to <City> (<XX 123>)` events Gmail creates, with the
    origin airport at the end of their location. The stay is a hotel block: a
    summary with "Stay at" or "Hotel", or a description with a confirmation
    number; where several describe one stay, the one with an address and a
    confirmation wins. Anchors are the timed events in the window whose
    location carries a street address.
    """
    events = _events_in(payload)
    flights: list[Flight] = []
    stays: list[Stay] = []
    for e in events:
        summary = str(e.get("summary") or "").strip()
        link = str(e.get("htmlLink") or "")
        start_day, start_clock, all_day = _when(e.get("start"))
        end_day, end_clock, _ = _when(e.get("end"))
        if start_day is None:
            continue
        flight = _FLIGHT.match(summary)
        if flight:
            iata = _IATA.search(str(e.get("location") or ""))
            flights.append(
                Flight(
                    day=start_day,
                    departs=start_clock,
                    arrives=end_clock,
                    origin=iata.group(1) if iata else "",
                    to_city=flight.group("city").strip(),
                    number=flight.group("number"),
                    permalink=link,
                )
            )
            continue
        description = str(e.get("description") or "")
        is_stay = (
            summary.lower().startswith("stay at")
            or "hotel" in summary.lower()
            or _CONFIRMATION.search(description) is not None
        )
        if not (is_stay and all_day):
            continue
        location = str(e.get("location") or "").strip()
        with_zip = _address_line(description)
        address = location if re.search(r"\b\d{5}\b", location) else (with_zip or location)
        conf = _CONFIRMATION.search(description)
        starts, ends = _STARTS.search(description), _ENDS.search(description)
        check_in = date.fromisoformat(starts.group(1)) if starts else start_day
        if ends:
            check_out = date.fromisoformat(ends.group(1))
        else:
            check_out = (end_day - timedelta(days=1)) if end_day else start_day
        hotel = re.sub(r"\s*-\s*Confirmation number:.*$", "", summary)
        hotel = re.sub(r"^Stay at (?:Guest Relations at )?", "", hotel, flags=re.I).strip()
        # The desk's line opens with the hotel's name and ends with its phone;
        # the address is what is left between them.
        address = re.sub(r"\s*Tel:.*$", "", address).strip()
        if hotel and address.lower().startswith(hotel.lower()):
            address = address[len(hotel) :].lstrip(" ,-").strip()
        stays.append(Stay(hotel, address, check_in, check_out, conf.group(1) if conf else "", link))
    if not flights and not stays:
        return None
    stay = max(stays, key=lambda s: (bool(s.address), bool(s.confirmation)), default=None)
    days = [f.day for f in flights] + ([stay.check_in, stay.check_out] if stay else [])
    start, end = min(days), max(days)
    anchors: list[Anchor] = []
    for e in events:
        summary = str(e.get("summary") or "").strip()
        day, _, all_day = _when(e.get("start"))
        loc = str(e.get("location") or "")
        if day is None or all_day or not (start <= day <= end) or _FLIGHT.match(summary):
            continue
        if not _STREET.search(loc) or "hotel" in summary.lower():
            continue
        anchors.append(Anchor(day, summary, loc, str(e.get("htmlLink") or "")))
    anchors.sort(key=lambda a: (a.day, a.title))
    flights.sort(key=lambda f: (f.day, _clock_minutes(f.departs)))
    if not name:
        city = _city_of(stay.address) if stay and stay.address else ""
        city = city or (flights[0].to_city if flights else "")
        name = f"Trip to {city}" if city else "trip"
    return Trip(name, start, end, tuple(flights), stay, tuple(anchors))


def _street(text: str) -> str:
    """ "555-3 Mateo St" and "The Bistro (555 Mateo St, ...)" both give "mateo st"."""
    inner = re.search(r"\(([^)]*\d[^)]*)\)", text)
    head = (inner.group(1) if inner else text).split(",")[0].strip()
    return re.sub(r"^\d+[\w-]*\s+", "", head).strip().lower()


def place(receipt: Receipt, trip: Trip) -> tuple[str, str]:
    """Where a receipt stands against the trip: (NEW | UNPLACED | OUTSIDE, why)."""
    if not (trip.start <= receipt.purchased_on <= trip.end):
        return OUTSIDE, f"dated outside {_day(trip.start)} - {_day(trip.end)}"
    area, state, airports = trip.area(), trip.state(), trip.airports()
    if area and any(_area(s) == area for s in receipt.stops):
        return NEW, "at the destination"
    hit = [a for a in receipt.airports if a in airports]
    if hit:
        return NEW, f"airport leg ({hit[0]})"
    if trip.stay and receipt.stops:
        hotel = _street(trip.stay.address)
        if hotel and any(_street(s) == hotel for s in receipt.stops):
            return NEW, "at the hotel"
    if state and receipt.state == state:
        return NEW, f"in {state}"
    if receipt.stops or receipt.airports or receipt.state:
        where = receipt.state or ", ".join(receipt.airports) or _city(receipt.stops[0])
        return UNPLACED, f"placed in {where}, the trip is in {state or area or 'another place'}"
    return UNPLACED, "no place on the receipt - fetch the body, or go by number"


def _explain(receipt: Receipt, trip: Trip) -> str:
    """The memo, plus the calendar event a stop matches that day, when one does."""
    if not receipt.stops:
        return receipt.memo
    for anchor in trip.anchors:
        if anchor.day != receipt.purchased_on:
            continue
        target = _street(anchor.address)
        if target and any(_street(s) == target for s in receipt.stops):
            return f"{receipt.memo} ({anchor.title})"
    return receipt.memo


# -- the batch ---------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    """A receipt with its number in the list and where it stands with Rippling."""

    n: int
    receipt: Receipt
    status: str
    filed_amount: str = ""
    why: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "status": self.status,
            "filed_amount": self.filed_amount,
            "why": self.why,
            "receipt": self.receipt.payload(),
        }


@dataclass(frozen=True)
class Batch:
    """The list he sees, the rows behind it, and the report they go under."""

    rows: tuple[Row, ...]
    skipped: tuple[Skipped, ...]
    report: str
    proposed: bool
    unverified: tuple[str, ...] = field(default_factory=tuple)
    trip: Trip | None = None

    def offered(self) -> list[Row]:
        """What `go all` would send: the new rows, nothing changed or filed."""
        return [row for row in self.rows if row.status == NEW]

    def render(self) -> str:
        new = [r for r in self.rows if r.status == NEW]
        filed = [r for r in self.rows if r.status == FILED]
        changed = [r for r in self.rows if r.status == CHANGED]
        unplaced = [r for r in self.rows if r.status == UNPLACED]
        outside = [r for r in self.rows if r.status == OUTSIDE]
        head = f"receipts to file - {len(new)} new"
        if filed:
            head += f", {len(filed)} already filed"
        if changed:
            head += f", {len(changed)} changed since filing"
        if unplaced:
            head += f", {len(unplaced)} in the window but not placed"
        if outside:
            head += f", {len(outside)} outside the trip"
        lines = [head]
        if self.trip:
            lines.append(self.trip.line())
        lines.append("")
        for row in self.rows:
            if row.status in (FILED, OUTSIDE):
                continue
            r = row.receipt
            bits = [_day(r.purchased_on), r.merchant, f"${r.amount}", r.memo, r.category]
            if r.paid_with:
                bits.append(r.paid_with)
            if row.status == CHANGED:
                bits.append(f"changed since filing (was ${row.filed_amount}) - needs its own go")
            elif row.status == UNPLACED:
                bits.append(f"where? {row.why} - not in go all")
            elif self.trip and row.why:
                bits.append(row.why)
            lines.append(f"- {row.n}. {' · '.join(bits)} ({r.permalink})")
        for row in outside:
            r = row.receipt
            lines.append(
                f"- outside the trip: {row.n}. {_day(r.purchased_on)} {r.merchant} ${r.amount} "
                f"({r.permalink})"
            )
        for row in filed:
            r = row.receipt
            lines.append(
                f"- already filed: {_day(r.purchased_on)} {r.merchant} ${r.amount} ({r.permalink})"
            )
        reasons: dict[str, int] = {}
        for s in self.skipped:
            reasons[s.reason] = reasons.get(s.reason, 0) + 1
        for reason, count in reasons.items():
            lines.append(f"skipped {count}: {reason}")
        if self.unverified:
            lines.append(
                f"heads up: {', '.join(self.unverified)} read by the generic rule - "
                "a total line and the mail date - check those two before go"
            )
        if self.report:
            tail = " - proposed from the dates, name it with --report" if self.proposed else ""
            lines.append(f'report: "{self.report}"{tail}')
        lines.append(
            f'say "go 1 2 3" or "go all" to forward to {RIPPLING_RECEIPTS_INBOX} - '
            "nothing moves until you do"
        )
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "rows": [row.payload() for row in self.rows],
                "skipped": [s.payload() for s in self.skipped],
                "report": self.report,
                "proposed": self.proposed,
                "unverified": list(self.unverified),
                "trip": self.trip.payload() if self.trip else None,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> Batch:
        data = json.loads(text)
        rows = tuple(
            Row(
                n=int(row["n"]),
                receipt=Receipt.from_payload(row["receipt"]),
                status=str(row["status"]),
                filed_amount=str(row.get("filed_amount") or ""),
                why=str(row.get("why") or ""),
            )
            for row in data.get("rows") or ()
        )
        skipped = tuple(
            Skipped(str(s["message_id"]), str(s["reason"]), str(s.get("permalink") or ""))
            for s in data.get("skipped") or ()
        )
        return cls(
            rows=rows,
            skipped=skipped,
            report=str(data.get("report") or ""),
            proposed=bool(data.get("proposed")),
            unverified=tuple(str(u) for u in data.get("unverified") or ()),
            trip=Trip.from_payload(data["trip"]) if data.get("trip") else None,
        )


def _day(day: date) -> str:
    return f"{day:%b} {day.day}".lower()


def propose_report(days: Iterable[date]) -> str:
    """A report name from the dates, in his own shape ("Trip to LA" is his to type)."""
    ordered = sorted(set(days))
    if not ordered:
        return ""
    first, last = ordered[0], ordered[-1]
    if first == last:
        return f"trip {_day(first)}"
    if first.month == last.month and first.year == last.year:
        return f"trip {_day(first)}-{last.day}"
    return f"trip {_day(first)} - {_day(last)}"


def review(
    harvests: Mapping[str, Harvest],
    *,
    filed: Mapping[str, Mapping[str, Any]] | None = None,
    report: str = "",
    trip: Trip | None = None,
) -> Batch:
    """Number the receipts, set aside what the log already has, flag what changed.

    With a ``trip``, each receipt is placed against it first: only one placed
    inside is NEW; the rest are UNPLACED or OUTSIDE and wait for a go by number.
    The report name defaults to the trip's.
    """
    filed = filed or {}
    receipts = sorted(
        (r for harvest in harvests.values() for r in harvest.receipts),
        key=lambda r: (r.purchased_on, r.key),
    )
    rows: list[Row] = []
    for n, receipt in enumerate(receipts, 1):
        why = ""
        standing = NEW
        if trip is not None:
            standing, why = place(receipt, trip)
            receipt = replace(receipt, memo=_explain(receipt, trip))
        prior = filed.get(receipt.key)
        if prior is not None and str(prior.get("message_id")) == receipt.message_id:
            rows.append(Row(n, receipt, FILED, why=why))
        elif prior is not None:
            rows.append(
                Row(n, receipt, CHANGED, filed_amount=str(prior.get("amount") or ""), why=why)
            )
        else:
            rows.append(Row(n, receipt, standing, why=why))
    skipped = tuple(s for harvest in harvests.values() for s in harvest.skipped)
    unverified = tuple(name for name in harvests if name in SOURCES and not SOURCES[name].verified)
    name = report or (trip.name if trip else "")
    name = name or propose_report(r.purchased_on for r in receipts)
    proposed = not report and trip is None and bool(name)
    return Batch(tuple(rows), skipped, name, proposed, unverified, trip)


def go(batch: Batch, picks: Sequence[int] | str) -> list[Forward]:
    """The forwards for the rows he named. ``"all"`` is the new rows only.

    Raises:
        ExpenseError: a number not in the batch, or a row already filed.
    """
    if isinstance(picks, str):
        if picks != "all":
            raise ExpenseError(f"picks must be row numbers or 'all', not {picks!r}.")
        chosen = batch.offered()
    else:
        by_n = {row.n: row for row in batch.rows}
        chosen = []
        for n in dict.fromkeys(picks):
            row = by_n.get(n)
            if row is None:
                raise ExpenseError(f"no row {n} in this batch (rows 1-{len(batch.rows)}).")
            if row.status == FILED:
                raise ExpenseError(
                    f"row {n} is already filed ({row.receipt.permalink}); "
                    f"remove its `{EXPENSED_LABEL}` label and its log row to send again."
                )
            chosen.append(row)
    return [forward_spec(row.receipt, report=batch.report) for row in chosen]


# -- the report, day by day --------------------------------------------------


@dataclass(frozen=True)
class DayGroup:
    """One day of an expense report: its rows and what they add up to."""

    day: date
    rows: tuple[Row, ...]

    @property
    def subtotal(self) -> Decimal:
        return sum((row.receipt.amount for row in self.rows), Decimal("0"))


@dataclass(frozen=True)
class Report:
    """A batch as the expense report he is about to submit, grouped by day."""

    name: str
    days: tuple[DayGroup, ...]
    trip: Trip | None = None

    @property
    def rows(self) -> list[Row]:
        return [row for group in self.days for row in group.rows]

    @property
    def total(self) -> Decimal:
        return sum((group.subtotal for group in self.days), Decimal("0"))

    def by_category(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for row in self.rows:
            out[row.receipt.category] = (
                out.get(row.receipt.category, Decimal("0")) + row.receipt.amount
            )
        return out

    def render(self) -> str:
        rows = self.rows
        count = len(rows)
        if not self.days:
            return f'expense report "{self.name}" - nothing in the batch'
        first, last = self.days[0].day, self.days[-1].day
        span = _day(first) if first == last else f"{_day(first)} - {_day(last)}"
        name = self.name or "(unnamed)"
        head = (
            f'expense report "{name}" - {count} expense{"s" if count != 1 else ""}, '
            f"${self.total} over {len(self.days)} day{'s' if len(self.days) != 1 else ''} ({span})"
        )
        lines = [head]
        if self.trip is not None:
            lines += ["", self.trip.render()]
        for group in self.days:
            n = len(group.rows)
            lines += [
                "",
                f"{_day(group.day)} - {n} expense{'s' if n != 1 else ''}, ${group.subtotal}",
            ]
            for row in group.rows:
                r = row.receipt
                bits = [_clock(r) or "-", r.merchant, f"${r.amount}", r.memo, r.category]
                if r.paid_with:
                    bits.append(r.paid_with)
                if row.status == FILED:
                    bits.append("✓ in rippling")
                elif row.status == CHANGED:
                    bits.append(f"changed since filing (was ${row.filed_amount})")
                elif row.status == UNPLACED:
                    bits.append(f"where? {row.why}")
                lines.append(f"- {' · '.join(bits)} ({r.permalink})")
        cats = ", ".join(f"{c} ${a}" for c, a in sorted(self.by_category().items()))
        lines += ["", f"by category: {cats}"]
        filed = sum(1 for row in rows if row.status == FILED)
        lines.append(f"in rippling already: {filed} of {count}")
        return "\n".join(lines)


def _clock(receipt: Receipt) -> str:
    """The purchase time when the key carries one (uber does), as `10:45 am`."""
    _, _, stamp = receipt.key.partition(":")
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return ""
    return f"{when:%I:%M %p}".lstrip("0").lower()


def by_day(batch: Batch) -> Report:
    """Every row of the batch under its purchase day, days in order, rows by time.

    Rows the trip placed outside its dates are not part of the report.
    """
    groups: dict[date, list[Row]] = {}
    for row in batch.rows:
        if row.status == OUTSIDE:
            continue
        groups.setdefault(row.receipt.purchased_on, []).append(row)
    days = tuple(
        DayGroup(day, tuple(sorted(rows, key=lambda row: (row.receipt.key, row.n))))
        for day, rows in sorted(groups.items())
    )
    return Report(batch.report, days, batch.trip)


# -- the confirmation: what Rippling says it made of a forward ---------------


@dataclass(frozen=True)
class Draft:
    """Rippling's reply to a forward: the draft reimbursement it created."""

    thread_id: str
    message_id: str
    amount: Decimal
    vendor: str
    purchased_on: date | None
    category: str
    link: str
    permalink: str


RIPPLING_RECEIPTS_SENDER = "receipts@rippling.com"
#: Ramp answers a forward to `reimbursements@ramp.com` from this address, in
#: the forward's own thread, within seconds (verified 2026-10-18, 13 s):
#: "Reimbursement created for this receipt / You have 1 draft reimbursement:
#: / $11.98 at Uber / Draft reimbursement · Nitin Srivastava · 10/05/26 /
#: Review draft <https://app.ramp.com/s/reimbursements/<uuid>?...>". No
#: category in the reply; Ramp picks one when he opens the draft.
RAMP_REPLY_SENDER = "do-not-reply@ramp.com"
_RAMP_DRAFT_LINK = re.compile(r"(https://app\.ramp\.com/s/reimbursements/[0-9a-f-]+)")
_RAMP_DRAFT_FIELDS = re.compile(
    r"\$(?P<amount>[\d,]+\.\d{2}) at (?P<vendor>.+?)\s+Draft reimbursement(?: · .+?)? · "
    r"(?P<date>\d{2}/\d{2}/\d{2})"
)
_DRAFT_LINK = re.compile(
    r"(https://link\.rippling\.com/spendApp\?path=/spend-management/expense/[^\s)\]|]+)"
)
_DRAFT_FIELDS = re.compile(
    r"\$(?P<amount>[\d,]+\.\d{2})\s+(?P<vendor>.+?)\s+Purchase date\s+Category\s+"
    r"(?P<date>[A-Z][a-z]{2} \d{1,2}, \d{4})\s+(?P<category>.+?)\s+View draft reimbursement"
)


def drafts_in(payload: Any) -> list[Draft]:
    """Every "Draft reimbursement created" reply in a connector answer.

    The reply is from `receipts@rippling.com`, in the forward's own thread,
    and reads: `$11.98 Uber · Purchase date Oct 5, 2026 · Category Rideshare &
    Taxis · View draft reimbursement <link>`. Anything else from that sender
    is not a confirmation and is left alone.
    """
    drafts: list[Draft] = []
    for message in messages_in(payload):
        sender = str(message.get("sender") or "").lower()
        if RAMP_REPLY_SENDER in sender:
            ramp = _ramp_draft(message)
            if ramp is not None:
                drafts.append(ramp)
            continue
        if RIPPLING_RECEIPTS_SENDER not in sender:
            continue
        raw = _text(message)
        if "draft reimbursement created" not in raw.lower():
            continue
        link = _DRAFT_LINK.search(raw)
        flat = re.sub(r"\[\]\([^)]*\)", " ", raw)
        flat = re.sub(r"[|\s]+", " ", flat).strip()
        fields = _DRAFT_FIELDS.search(flat)
        if fields is None:
            continue
        try:
            amount = Decimal(fields.group("amount").replace(",", ""))
        except InvalidOperation:
            continue
        try:
            when: date | None = datetime.strptime(fields.group("date"), "%b %d, %Y").date()
        except ValueError:
            when = None
        drafts.append(
            Draft(
                thread_id=_field(message, "threadId", "thread_id"),
                message_id=str(message.get("id") or ""),
                amount=amount,
                vendor=fields.group("vendor").strip(),
                purchased_on=when,
                category=fields.group("category").strip(),
                link=link.group(1) if link else "",
                permalink=_field(message, "viewUrl", "view_url"),
            )
        )
    return drafts


def _ramp_draft(message: Mapping[str, Any]) -> Draft | None:
    """Ramp's "Reimbursement created for this receipt" reply, read into a Draft."""
    raw = _text(message)
    if "reimbursement created for this receipt" not in raw.lower():
        return None
    flat = re.sub(r"\[[^\]]*\]", " ", raw)
    flat = re.sub(r"\s+", " ", flat).strip()
    fields = _RAMP_DRAFT_FIELDS.search(flat)
    if fields is None:
        return None
    try:
        amount = Decimal(fields.group("amount").replace(",", ""))
    except InvalidOperation:
        return None
    try:
        when: date | None = datetime.strptime(fields.group("date"), "%m/%d/%y").date()
    except ValueError:
        when = None
    link = _RAMP_DRAFT_LINK.search(raw)
    return Draft(
        thread_id=_field(message, "threadId", "thread_id"),
        message_id=str(message.get("id") or ""),
        amount=amount,
        vendor=fields.group("vendor").strip(),
        purchased_on=when,
        category="",
        link=link.group(1) if link else "",
        permalink=_field(message, "viewUrl", "view_url"),
    )


@dataclass(frozen=True)
class Check:
    """Forwarded rows against the receipts inbox's confirmations.

    Ramp since 2026-10-18; Rippling before.
    """

    confirmed: tuple[tuple[Row, Draft], ...]
    unconfirmed: tuple[Row, ...]
    unmatched: tuple[Draft, ...]

    def render(self) -> str:
        total = len(self.confirmed) + len(self.unconfirmed)
        lines = [f"draft reimbursements - {len(self.confirmed)} of {total} forwarded confirmed"]
        for row, draft in self.confirmed:
            r = row.receipt
            lines.append(
                f"- ✓ {row.n}. {_day(r.purchased_on)} {r.merchant} ${r.amount} -> draft "
                f"reimbursement, {draft.category or 'no category'} "
                f"({draft.link or draft.permalink})"
            )
        for row in self.unconfirmed:
            r = row.receipt
            lines.append(
                f"- ? {row.n}. {_day(r.purchased_on)} {r.merchant} ${r.amount} - forwarded, "
                f"no reply from the receipts inbox yet ({r.permalink})"
            )
        for draft in self.unmatched:
            when = _day(draft.purchased_on) if draft.purchased_on else "undated"
            lines.append(
                f"- draft with no row in this batch: {when} {draft.vendor} ${draft.amount} "
                f"({draft.link or draft.permalink})"
            )
        if total == 0:
            lines.append("nothing in this batch has been forwarded")
        return "\n".join(lines)


def check(
    batch: Batch, drafts: Iterable[Draft], *, filed: Mapping[str, Any] | None = None
) -> Check:
    """Pair each forwarded row with Rippling's reply, by thread first.

    A row counts as forwarded when the batch says FILED or its key is in
    ``filed`` (`forwarded_rows(log)`): the batch file is written before the
    forwards go out, so the log is what knows. A row and a draft in the same
    Gmail thread are the same receipt. Failing that, the same amount on the
    same purchase date pairs them, which covers a forward that went out in a
    thread of its own. Nothing else does.
    """
    sent = filed or {}
    pending = list(drafts)
    confirmed: list[tuple[Row, Draft]] = []
    unconfirmed: list[Row] = []
    for row in batch.rows:
        if row.status != FILED and row.receipt.key not in sent:
            continue
        r = row.receipt
        match = next((d for d in pending if d.thread_id and d.thread_id == r.thread_id), None)
        if match is None:
            match = next(
                (d for d in pending if d.amount == r.amount and d.purchased_on == r.purchased_on),
                None,
            )
        if match is None:
            unconfirmed.append(row)
        else:
            pending.remove(match)
            confirmed.append((row, match))
    return Check(tuple(confirmed), tuple(unconfirmed), tuple(pending))


# -- the log -----------------------------------------------------------------


def mark_forwarded(
    log: EventLog, receipts: Iterable[Receipt], *, report: str, now: datetime
) -> int:
    """Record each forward once. Returns how many rows were written."""
    count = 0
    for receipt in receipts:
        log.record(
            FORWARDED,
            sensitivity="private",
            key=receipt.key,
            message_id=receipt.message_id,
            supersedes=list(receipt.supersedes),
            merchant=receipt.merchant,
            amount=str(receipt.amount),
            purchased_on=receipt.purchased_on.isoformat(),
            memo=receipt.memo,
            category=receipt.category,
            report=report,
            to=RECEIPTS_INBOX,
            at=now.isoformat(),
        )
        count += 1
    return count


def forwarded_rows(log: EventLog) -> dict[str, dict[str, Any]]:
    """The newest forward per purchase key. A row that will not read is skipped."""
    out: dict[str, dict[str, Any]] = {}
    for payload in log.recorded(FORWARDED):
        if isinstance(payload, Mapping) and payload.get("key"):
            out[str(payload["key"])] = dict(payload)
    return out


# -- amex: reconcile, never file ---------------------------------------------


@dataclass(frozen=True)
class Charge:
    """One line of the Amex activity export."""

    posted_on: date
    description: str
    amount: Decimal
    reference: str = ""


_DATE_SHAPES = ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y")


def _csv_date(raw: str) -> date | None:
    for shape in _DATE_SHAPES:
        try:
            return datetime.strptime(raw.strip(), shape).date()
        except ValueError:
            continue
    return None


def charges_from_csv(text: str) -> list[Charge]:
    """The export's rows, read by header name. Amex signs charges positive.

    Raises:
        ExpenseError: a header the reader needs is missing.
    """
    reader = csv.DictReader(io.StringIO(text))
    headers = {h.strip().lower(): h for h in reader.fieldnames or ()}
    for needed in ("date", "description", "amount"):
        if needed not in headers:
            raise ExpenseError(f"csv has no `{needed}` column; headers: {sorted(headers)}.")
    charges: list[Charge] = []
    for row in reader:
        posted = _csv_date(str(row.get(headers["date"]) or ""))
        raw_amount = str(row.get(headers["amount"]) or "").replace("$", "").replace(",", "").strip()
        try:
            amount = Decimal(raw_amount)
        except InvalidOperation:
            continue
        if posted is None:
            continue
        charges.append(
            Charge(
                posted_on=posted,
                description=str(row.get(headers["description"]) or "").strip(),
                amount=amount,
                reference=str(row.get(headers.get("reference", ""), "") or "").strip(" '"),
            )
        )
    return charges


@dataclass(frozen=True)
class Reconciliation:
    matched: tuple[tuple[Receipt, Charge], ...]
    receipts_without_charge: tuple[Receipt, ...]
    charges_without_receipt: tuple[Charge, ...]

    def render(self) -> str:
        lines = [
            f"amex vs receipts - {len(self.matched)} matched, "
            f"{len(self.receipts_without_charge)} receipts w/o a charge, "
            f"{len(self.charges_without_receipt)} charges w/o a receipt",
            "",
        ]
        for receipt, charge in self.matched:
            lines.append(
                f"- ✓ {_day(charge.posted_on)} ${charge.amount} {charge.description} "
                f"<- {receipt.merchant} {receipt.memo} ({receipt.permalink})"
            )
        for receipt in self.receipts_without_charge:
            lines.append(
                f"- receipt, no charge yet: {_day(receipt.purchased_on)} ${receipt.amount} "
                f"{receipt.merchant} ({receipt.permalink})"
            )
        for charge in self.charges_without_receipt:
            lines.append(
                f"- no receipt: {_day(charge.posted_on)} ${charge.amount} {charge.description}"
                " (amex statement line - photograph or find the receipt)"
            )
        return "\n".join(lines)


def reconcile(
    receipts: Iterable[Receipt], charges: Iterable[Charge], *, days: int = 3
) -> Reconciliation:
    """Pair receipts with statement charges on amount, within ``days``. Credits are ignored."""
    pending = [c for c in charges if c.amount > 0]
    found = list(receipts)
    candidates: list[tuple[int, int, int]] = []
    for i, receipt in enumerate(found):
        for j, charge in enumerate(pending):
            gap = abs((charge.posted_on - receipt.purchased_on).days)
            if charge.amount == receipt.amount and gap <= days:
                candidates.append((gap, i, j))
    used_r: set[int] = set()
    used_c: set[int] = set()
    matched: list[tuple[Receipt, Charge]] = []
    for _, i, j in sorted(candidates):
        if i in used_r or j in used_c:
            continue
        used_r.add(i)
        used_c.add(j)
        matched.append((found[i], pending[j]))
    return Reconciliation(
        tuple(matched),
        tuple(r for i, r in enumerate(found) if i not in used_r),
        tuple(c for j, c in enumerate(pending) if j not in used_c),
    )


# -- cli ---------------------------------------------------------------------


def _picks(raw: Sequence[str]) -> Sequence[int] | str:
    if list(raw) == ["all"]:
        return "all"
    try:
        return [int(x) for x in raw]
    except ValueError:
        raise ExpenseError(f"rows are numbers or 'all', not {list(raw)!r}.") from None


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m daydag.expenses trip|plan|review|go|mark|check|report|reconcile``."""
    parser = argparse.ArgumentParser(prog="python -m daydag.expenses")
    sub = parser.add_subparsers(dest="command", required=True)

    p_trip = sub.add_parser("trip", help="the trip the calendar describes")
    p_trip.add_argument("calendar", type=Path, help="list_events answers, day by day, as json")
    p_trip.add_argument("--name", default="")
    p_trip.add_argument("--out", type=Path, default=None)

    p_plan = sub.add_parser("plan", help="the Gmail queries to run")
    p_plan.add_argument("--since", type=date.fromisoformat, default=None)
    p_plan.add_argument("--until", type=date.fromisoformat, default=None)
    p_plan.add_argument("--trip", type=Path, default=None, help="take the window from trip.json")
    p_plan.add_argument("--source", action="append", default=None)

    p_review = sub.add_parser("review", help="the numbered list from the saved answers")
    p_review.add_argument("payloads", type=Path)
    p_review.add_argument("--log", type=Path, default=None)
    p_review.add_argument("--report", default="")
    p_review.add_argument("--trip", type=Path, default=None)
    p_review.add_argument(
        "--out", type=Path, required=True, help="outside the repo: it carries his addresses"
    )

    p_go = sub.add_parser("go", help="the forward calls for the rows he named")
    p_go.add_argument("batch", type=Path)
    p_go.add_argument("rows", nargs="+")

    p_mark = sub.add_parser("mark", help="log the rows that were forwarded")
    p_mark.add_argument("batch", type=Path)
    p_mark.add_argument("rows", nargs="+")
    p_mark.add_argument("--log", type=Path, required=True)

    p_check = sub.add_parser("check", help="forwarded rows against rippling's replies")
    p_check.add_argument("batch", type=Path)
    p_check.add_argument("confirmations", type=Path, help="rippling's replies, as searched")
    p_check.add_argument("--log", type=Path, default=None, help="which rows were forwarded")

    p_report = sub.add_parser("report", help="the batch as an expense report, by day")
    p_report.add_argument("batch", type=Path)
    p_report.add_argument("--out", type=Path, default=None)

    p_rec = sub.add_parser("reconcile", help="the batch against an amex activity csv")
    p_rec.add_argument("batch", type=Path)
    p_rec.add_argument("--amex", type=Path, required=True)
    p_rec.add_argument("--days", type=int, default=3)

    p_rc = sub.add_parser("ramp-check", help="what ramp holds against the ledger")
    p_rc.add_argument("batch", type=Path, help="batch.json, or '-' for none")
    p_rc.add_argument("ramp", type=Path, help="saved answer of ramp_get_user_recent_reimbursements")
    p_rc.add_argument("--expected", type=Path, default=None, help="lines filed from files")
    p_rc.add_argument("--days", type=int, default=3)

    p_rs = sub.add_parser("ramp-stage", help="the ramp calls that file local receipt files")
    p_rs.add_argument(
        "files", type=Path, help="files.json: file, merchant, amount, date, category, memo"
    )
    p_rs.add_argument("--trip-uuid", default="")

    args = parser.parse_args(argv)
    try:
        if args.command == "trip":
            events = json.loads(args.calendar.read_text(encoding="utf-8"))
            trip = trip_from_calendar(events, name=args.name)
            if trip is None:
                raise ExpenseError("no flight and no stay on the calendar in that window.")
            if args.out is not None:
                args.out.write_text(json.dumps(trip.payload(), indent=2), encoding="utf-8")
            print(trip.render())
        elif args.command == "plan":
            since, until = args.since, args.until
            if args.trip is not None:
                trip = Trip.from_payload(json.loads(args.trip.read_text(encoding="utf-8")))
                since = since or trip.start
                until = until or trip.end + timedelta(days=RECEIPT_LAG_DAYS)
            if since is None:
                raise ExpenseError("say --since, or --trip trip.json.")
            until = until or datetime.now().date()
            print(json.dumps(plan(since, until, args.source), indent=2))
        elif args.command == "review":
            payloads = json.loads(args.payloads.read_text(encoding="utf-8"))
            harvests = {name: receipts_in(name, payload) for name, payload in payloads.items()}
            filed = forwarded_rows(EventLog.open(args.log)) if args.log else {}
            trip = None
            if args.trip is not None:
                trip = Trip.from_payload(json.loads(args.trip.read_text(encoding="utf-8")))
            batch = review(harvests, filed=filed, report=args.report, trip=trip)
            args.out.write_text(batch.to_json(), encoding="utf-8")
            print(batch.render())
        elif args.command == "go":
            batch = Batch.from_json(args.batch.read_text(encoding="utf-8"))
            print(json.dumps([f.call() for f in go(batch, _picks(args.rows))], indent=2))
        elif args.command == "mark":
            batch = Batch.from_json(args.batch.read_text(encoding="utf-8"))
            picks = _picks(args.rows)
            if picks == "all":
                raise ExpenseError("mark takes the numbers that were sent, never 'all'.")
            by_n = {row.n: row for row in batch.rows}
            missing = [n for n in picks if n not in by_n]
            if missing:
                raise ExpenseError(f"no row {missing[0]} in this batch (rows 1-{len(batch.rows)}).")
            rows = [by_n[n] for n in dict.fromkeys(picks)]
            count = mark_forwarded(
                EventLog.open(args.log),
                (r.receipt for r in rows),
                report=batch.report,
                now=datetime.now().astimezone(),
            )
            print(f"logged {count} forward(s) under {FORWARDED}")
        elif args.command == "check":
            batch = Batch.from_json(args.batch.read_text(encoding="utf-8"))
            replies = json.loads(args.confirmations.read_text(encoding="utf-8"))
            filed = forwarded_rows(EventLog.open(args.log)) if args.log else {}
            print(check(batch, drafts_in(replies), filed=filed).render())
        elif args.command == "report":
            batch = Batch.from_json(args.batch.read_text(encoding="utf-8"))
            text = by_day(batch).render()
            if args.out is not None:
                args.out.write_text(text + "\n", encoding="utf-8")
            print(text)
        elif args.command == "reconcile":
            batch = Batch.from_json(args.batch.read_text(encoding="utf-8"))
            charges = charges_from_csv(args.amex.read_text(encoding="utf-8"))
            print(reconcile((r.receipt for r in batch.rows), charges, days=args.days).render())
        elif args.command == "ramp-check":
            from daydag import expenses_ramp as ramp

            expected: list[ramp.Expected] = []
            if str(args.batch) != "-":
                expected += ramp.expected_from_batch(
                    Batch.from_json(args.batch.read_text(encoding="utf-8"))
                )
            if args.expected is not None:
                expected += ramp.expected_from_file(
                    json.loads(args.expected.read_text(encoding="utf-8"))
                )
            items = ramp.ramp_items_in(json.loads(args.ramp.read_text(encoding="utf-8")))
            print(ramp.ramp_check(expected, items, days=args.days).render())
        elif args.command == "ramp-stage":
            from daydag import expenses_ramp as ramp

            files = ramp.files_from_payload(json.loads(args.files.read_text(encoding="utf-8")))
            env = Path(".env")
            identities = Identities.from_file(env) if env.exists() else None
            print(
                ramp.render_staging(
                    ramp.stage_files(files, trip_uuid=args.trip_uuid, identities=identities)
                )
            )
    except ExpenseError as exc:
        print(f"expenses: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
