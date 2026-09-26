"""Call notes: which calls he was in, and what came out of all of them (#168).

USING IT
    principal = Principal.from_identities(identities)   # names from .env addresses

    call_notes.sweep(gmail, events, principal, log=log,
                     decisions=open_decision_texts, tz=tz)          # the ingest push
    log.chase_items(kinds={call_notes.CHASED})   # what it filed, for State.md
    call_notes.priorities(gmail, events, principal,
                          decisions=open_decision_texts, day=day, tz=tz)  # EOD sections

    call_notes.join(gmail, events, principal)   # -> [Note], each with a Verdict
    attendance("tentative", body, principal)    # -> Verdict(status, rsvp, reason)

    A gmail record is one Gemini note fetched in PLAIN_TEXT:
        {"id": "<message id>", "subject": 'Notes: "<title>" <date>',
         "date": "<the mail's own timestamp>", "body": "<plaintext body>",
         "permalink": "<viewUrl>"}
    `plaintextBody` / `viewUrl` - the connector's own names - are read too.

CONTRACTS - break one and the guarantee is gone
    1. Attendance is a VERDICT with a reason, never a bare flag.
         accepted                    -> attended
         declined                    -> not attended
         needsAction / tentative / ? -> unconfirmed
       Any of the three is upgraded to attended when the note's own prose
       quotes him speaking ("<his name> noted that ..."), and the reason
       carries that sentence. RSVP alone under-counts: on 2026-09-25 he was
       tentative on a standup whose notes quote him twice. Being named as the
       OWNER of a next step is not speech - the summariser assigns owners
       whether or not they were there.
    2. The sweep is IDEMPOTENT on the Gmail message id. A note already
       ingested is not re-reported; the seen-set is the `note_ingested` kind
       in the event log, outside the vault. A note that arrived WITHOUT its
       body is not marked seen - a metadata-only fetch must not burn it.
    3. Every priority line carries the note's permalink, and every line from
       a call he did not attend or is not confirmed in says so on the line.
    4. Personnel / comp / M&A text is quoted like anything else, in the DM
       and in `State.md` alike (#180 - the principal's decision of
       2026-09-25, which changed house rule 7). `classify_sensitivity` still
       MARKS every record, and `state.withholds_private()` - one switch for
       the vault and the DM - restores #177's minimal quoting here (a
       private line withheld, a step from a private note shown by title, a
       private sentence never the attendance quote) while `_visible` keeps
       the same items out of `State.md`.
    5. Pure over what it is handed, like `ingestion`: no connector, no vault
       read or write. `run` fetches, reads `Decisions.md`, passes text in,
       and projects what `sweep` recorded through `StateFolder.update_state`.
    6. Every ACTIONABLE `[Owner]` next step of a fresh note - assigned to
       him, touches an open decision, asks to his reports - is recorded as a
       `CHASED` row, once per (message id, item text), with owner, the step's
       title as the ask, the full step as the verbatim quote, permalink,
       asked-on, attendance marker and status open. Prose and section
       headings are never filed. "Everything else" is DM-only. A missed DM
       no longer means a missed item (#180).
    7. An unplaced note is named in ONE sweep's push (`UNPLACED` rows in the
       log), not every 30 minutes; the EOD roll names it again if it is
       still unread.

WHY IT EXISTS
    The principal, 2026-09-25: "call notes need to be pulled automatically
    very frequently, also need to add context of what calls I attend (marked
    yes) and not -- look at notes from all calls but clarify if some
    observations are coming from calls I have not attended". Asked twice
    before in other words (9/16, 9/25); on 9/25 the EOD plan had no step that
    read the day's notes and eight were read by hand to answer him.

    The join cannot be the meeting ledger alone. `ledger.qualifies` drops a
    declined event by design (its contract 2 - a declined meeting cannot be a
    notes gap), yet a declined call's notes still arrive, and those are
    exactly the ones he wants labelled "you weren't in it". So the join runs a
    private `Ledger` seeded with every event, declined included, and reads
    the RSVP back off the original record - the ledger's own title-first,
    window-bounded, ambiguity-surfacing matcher, not a second one.

KNOWN LIMIT
    - `State.md`'s own dedupe matches a row's head - owner, step title and
      the call it came from - so the same titled step for the same owner from
      two notes of the same call is ONE row there (a daily standup's repeat
      of yesterday's ask reads as already filed); the log holds both.
    - Speech detection is a verb list after his name. It will miss "per
      Alex, ..." and similar; a miss leaves the call unconfirmed, which is the
      honest answer, never a wrong "attended".
    - "Touches an open decision" is shared content words with the decision's
      bold headline (two or more). Precision over recall: a long decision
      body would match everything, so only the headline is read.
    - The digest (#17) counts commitments and asks from next steps. Decisions
      in body prose and correction handling are not built yet - TODO(#17).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Any

from daydag import recipes
from daydag.brief import Section, claim, short
from daydag.ingestion import classify
from daydag.ledger import Ledger, Match, title_from_gemini_subject
from daydag.state import EventLog, classify_sensitivity, withholds_private

__all__ = [
    "ATTENDED",
    "CHASED",
    "INGESTED",
    "NOT_ATTENDED",
    "UNCONFIRMED",
    "Note",
    "Principal",
    "Verdict",
    "attendance",
    "join",
    "next_steps",
    "priorities",
    "spoke",
    "sweep",
]

ATTENDED = "attended"
NOT_ATTENDED = "not attended"
UNCONFIRMED = "unconfirmed"

#: The event-log kind that is the sweep's seen-set (contract 2).
INGESTED = "note_ingested"

#: The event-log kind an actionable item is filed under (contract 6). In
#: `state.VAULT_BOUND`, so it cannot be recorded without a sensitivity mark.
CHASED = "call_note_item"

#: The event-log kind that remembers an unplaced note was already named, so
#: the 30-minute sweep names it once. Never projected to the vault.
UNPLACED = "note_unplaced"

#: Directory keys for his direct reports, per CLAUDE.md's ownership map.
REPORT_KEYS = ("EMAIL_VP_DATA", "EMAIL_VP_AI", "EMAIL_ENABLEMENT_LEAD")

#: Verbs Gemini uses when it reports someone speaking, measured on real
#: 2026-09 notes ("noted", "highlighted", "stated", "directed", ...). Up to two
#: words may sit between the name and the verb ("Alex also noted").
_SPEECH = (
    "said|says|noted|notes|stated|states|asked|asks|explained|highlighted|suggested|"
    "mentioned|emphasized|emphasised|proposed|raised|requested|confirmed|agreed|"
    "questioned|shared|clarified|pointed|recommended|argued|directed|instructed|"
    "expressed|added|advised|stressed|inquired|observed|reported|presented|outlined|"
    "described|indicated|urged|cautioned|challenged|approved|decided|acknowledged|"
    "committed|offered|volunteered|reminded|summarized|summarised|thanked|welcomed"
)

#: Where the prose starts and stops in a Gemini PLAIN_TEXT body.
_PROSE_START = re.compile(r"contain errors\.\s*$", re.MULTILINE)
_STEPS_HEADING = re.compile(r"^\s*Suggested next steps\s*$", re.MULTILINE | re.IGNORECASE)
_FOOTER = re.compile(
    r"^\s*(?:Meeting records|Is the content of this email helpful|You have received this)",
    re.MULTILINE,
)
#: `[Owner] Title: text`, one per paragraph.
_STEP = re.compile(r"^\[(?P<owner>[^\]]+)\]\s*(?P<text>.+)$", re.DOTALL)

#: Words too common to count as "touching" a decision.
_STOP = frozenset(
    """a an and are as at be been but by can could did do does for from had has have how
    in into is it its may more most must no not of on or our out over should so some
    than that the their them then there these they this those to under up was we were
    what when where which while who why will with would yes you your about after also
    any before between both each few just like made make many much only other same such
    very via ask asked open status owners owner raised question ones read full""".split()
)
_WORD = re.compile(r"[a-z0-9]+")
_BOLD = re.compile(r"\*\*(?P<head>.+?)\*\*")
_DATE_LEAD = re.compile(r"^\s*\d{4}-\d{2}-\d{2}[^·]*·\s*")


# --------------------------------------------------------------------------
# who he is, derived at runtime
# --------------------------------------------------------------------------


def name_from_address(address: str) -> str:
    """``first.last@x`` -> ``First Last``. ``""`` for nothing usable."""
    local = str(address or "").split("@", 1)[0]
    parts = [p for p in re.split(r"[._-]+", local) if p.isalpha()]
    return " ".join(p.capitalize() for p in parts)


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"\s+", name.strip().casefold()) if t]


def _same_person(owner: str, full: str) -> bool:
    """First AND last token agree, or the owner is the bare first name.

    Gemini writes display names, which may carry a middle name the address
    does not ("Sam Tobi Okafor" vs sam.okafor@) - so first and last, not the
    whole string.
    """
    have, want = _tokens(owner), _tokens(full)
    if not have or not want:
        return False
    if len(have) == 1:
        return have[0] == want[0]
    return have[0] == want[0] and have[-1] == want[-1]


@dataclass(frozen=True)
class Principal:
    """The names the notes will use for him, and for his direct reports.

    Derived from the `.env` addresses at runtime: the repo is public, so no
    name is ever written into this module.
    """

    #: Full name first, then first name - the two forms Gemini writes.
    names: tuple[str, ...]
    reports: tuple[str, ...] = ()

    @classmethod
    def from_identities(cls, identities: Mapping[str, str]) -> Principal:
        full = name_from_address(identities.get("EMAIL_PRINCIPAL", ""))
        names = tuple(dict.fromkeys(n for n in (full, full.split(" ")[0] if full else "") if n))
        reports = tuple(
            name
            for name in (name_from_address(identities.get(key, "")) for key in REPORT_KEYS)
            if name
        )
        return cls(names=names, reports=reports)

    @property
    def full(self) -> str:
        return self.names[0] if self.names else ""

    def is_him(self, owner: str) -> bool:
        return bool(self.full) and any(_same_person(o, self.full) for o in _owners(owner))

    def is_report(self, owner: str) -> bool:
        return any(_same_person(o, name) for o in _owners(owner) for name in self.reports)

    def named_in(self, text: str) -> bool:
        """Whether ``text`` names him at all - "Notify Alex: ..."."""
        if not self.names:
            return False
        names = "|".join(re.escape(n) for n in self.names)
        return re.search(rf"\b(?:{names})\b", text) is not None


def _owners(owner: str) -> list[str]:
    """``[A, B]`` and ``[A and B]`` are one step with two owners (real notes
    write both); each is checked on its own."""
    return [o for o in re.split(r"\s*,\s*|\s+and\s+", owner.strip()) if o]


# --------------------------------------------------------------------------
# reading one note body
# --------------------------------------------------------------------------


def _prose(body: str) -> str:
    """The note's prose: after the header boilerplate, before next steps."""
    start = _PROSE_START.search(body)
    text = body[start.end() :] if start else body
    for stop in (_STEPS_HEADING, _FOOTER):
        found = stop.search(text)
        if found:
            text = text[: found.start()]
    return text


#: A real sentence ends like one; a section heading does not.
_SENTENCE_END = re.compile(r"[.!?][^\w\s]*$")


def _title(text: str) -> str:
    """Gemini's step title - "Run Data Checks" from "Run Data Checks: Execute
    ..." - or ``""`` when the step has no ``Title:`` lead."""
    head, sep, _ = text.partition(":")
    return head.strip() if sep and 0 < len(head.strip()) <= 80 else ""


def _sentences(text: str) -> list[str]:
    """Paragraphs unwrapped, then split into sentences. Gemini hard-wraps at
    ~75 columns, so a sentence routinely spans two lines."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        joined = " ".join(line.strip() for line in para.splitlines() if line.strip())
        out += [s.strip() for s in re.split(r"(?<=[.!?])\s+", joined) if s.strip()]
    return out


def _speech(body: str, principal: Principal) -> list[str]:
    """Every prose sentence that reports him speaking, in order."""
    if not principal.names:
        return []
    names = "|".join(re.escape(n) for n in principal.names)
    pattern = re.compile(rf"\b(?:{names})\b(?:\s+\w+){{0,2}}?\s+(?:{_SPEECH})\b")
    return [s for s in _sentences(_prose(body or "")) if pattern.search(s)]


def spoke(body: str, principal: Principal) -> str | None:
    """The first prose sentence that reports him speaking, or ``None``.

    Whatever it says, by default (#180). With house rule 7 on
    (`state.withholds_private()`) a sentence `classify_sensitivity` calls
    private is passed over for a later one, because this is what the
    attendance roll QUOTES; if every one is private the first is still
    returned - it is still evidence - and `attendance` declines to quote it.
    """
    said = _speech(body, principal)
    if withholds_private():
        for sentence in said:
            if classify_sensitivity(sentence) != "private":
                return sentence
    return said[0] if said else None


def next_steps(body: str) -> list[tuple[str, str]]:
    """``[(owner, text)]`` from the note's "Suggested next steps", in order."""
    body = body or ""
    heading = _STEPS_HEADING.search(body)
    if heading is None:
        return []
    tail = body[heading.end() :]
    footer = _FOOTER.search(tail)
    if footer:
        tail = tail[: footer.start()]
    steps: list[tuple[str, str]] = []
    for para in re.split(r"\n\s*\n", tail):
        joined = " ".join(line.strip() for line in para.splitlines() if line.strip())
        found = _STEP.match(joined)
        if found:
            steps.append((found["owner"].strip(), found["text"].strip()))
    return steps


# --------------------------------------------------------------------------
# attendance
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """Whether he was in the call, and the evidence for saying so."""

    status: str
    rsvp: str
    reason: str


def attendance(rsvp: str | None, body: str, principal: Principal) -> Verdict:
    """Contract 1: RSVP first, upgraded by the note quoting him."""
    said = spoke(body, principal)
    label = rsvp or "no rsvp"
    if said:
        if rsvp == "accepted":
            return Verdict(ATTENDED, label, "accepted")
        if withholds_private() and classify_sensitivity(said) == "private":
            return Verdict(
                ATTENDED,
                label,
                f"rsvp {label}, but the notes quote you (personnel/comp - not quoted)",
            )
        return Verdict(ATTENDED, label, f'rsvp {label}, but the notes quote you: "{short(said)}"')
    if rsvp == "accepted":
        return Verdict(ATTENDED, label, "accepted")
    if rsvp == "declined":
        return Verdict(NOT_ATTENDED, label, "declined")
    return Verdict(UNCONFIRMED, label, f"rsvp {label} and the notes don't quote you")


# --------------------------------------------------------------------------
# the join
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Note:
    """One Gemini note, joined to its calendar row (or not)."""

    message_id: str
    title: str
    arrived: datetime | None
    body: str
    permalink: str
    event: Mapping[str, Any] | None
    verdict: Verdict

    @property
    def has_body(self) -> bool:
        return bool(self.body.strip())

    @property
    def sensitive(self) -> bool:
        """The note as a whole carries personnel / comp / M&A text."""
        return classify_sensitivity(self.title, self.body) == "private"

    def local_day(self, tz: tzinfo) -> date | None:
        start = self.event.get("start") if self.event else None
        when = start if isinstance(start, datetime) else self.arrived
        if not isinstance(when, datetime):
            return None
        return (when.astimezone(tz) if when.tzinfo else when).date()

    def marker(self) -> str:
        """The provenance tag a line from this call carries (contract 3)."""
        if self.verdict.status == NOT_ATTENDED:
            return f"(from {self.title} - you weren't in it)"
        if self.verdict.status == UNCONFIRMED:
            return f"(from {self.title} - not sure you were in it)"
        return f"(from {self.title})"


def _instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, Mapping):
        value = value.get("dateTime") or value.get("date_time")
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _field(record: Mapping[str, Any], *names: str) -> str:
    for name in names:
        if record.get(name):
            return str(record[name])
    return ""


def _permalink(record: Mapping[str, Any], message_id: str) -> str:
    link = _field(record, "permalink", "viewUrl", "view_url")
    if link:
        return link
    return f"https://mail.google.com/mail/#all/{message_id}" if message_id else ""


def join(notes: Iterable[Any], events: Iterable[Any], principal: Principal) -> list[Note]:
    """Each Gemini note with its calendar row and attendance verdict.

    A private ledger, seeded with every event including declined ones - see
    WHY IT EXISTS for why the main ledger cannot answer this. A record that is
    not a Gemini note (no parseable subject) is skipped; `sweep` names it.
    """
    originals: dict[tuple[str, datetime], Mapping[str, Any]] = {}
    ledger = Ledger()
    for raw in events:
        if not isinstance(raw, Mapping) or not raw.get("id") or not raw.get("summary"):
            continue
        start, end = _instant(raw.get("start")), _instant(raw.get("end"))
        if start is None or end is None:
            continue
        # The copy qualifies regardless of RSVP; the original keeps it.
        seedable = {**raw, "start": start, "end": end, "response_status": "needsAction"}
        ledger.seed_day([seedable])
        originals[(str(raw["id"]), start)] = raw

    joined: list[Note] = []
    for record in notes:
        if not isinstance(record, Mapping):
            continue
        title = title_from_gemini_subject(str(record.get("subject", "")))
        if title is None:
            continue
        message_id = _field(record, "id", "messageId", "message_id")
        arrived = _instant(record.get("date") or record.get("arrived"))
        body = _field(record, "body", "plaintextBody", "plaintext_body")
        row = None
        if arrived is not None:
            row = ledger.offer_note(
                Match(title=title, arrived=arrived, attendees=[], source="gemini")
            )
        event = originals.get(row.key) if row is not None else None
        if event is None:
            verdict = attendance(None, body, principal)
            if verdict.status != ATTENDED:
                verdict = Verdict(UNCONFIRMED, "no rsvp", "no calendar entry matched this note")
        else:
            verdict = attendance(event.get("response_status"), body, principal)
        joined.append(
            Note(
                message_id=message_id,
                title=title,
                arrived=arrived,
                body=body,
                permalink=_permalink(record, message_id),
                event=event,
                verdict=verdict,
            )
        )
    return joined


# --------------------------------------------------------------------------
# ranking: his > an open decision > his reports > the rest
# --------------------------------------------------------------------------

_MINE, _DECISION, _REPORTS, _REST = range(4)
_BUCKETS = (
    "assigned to you",
    "touches an open decision",
    "asks to your reports",
    "everything else",
)


def _content(text: str) -> set[str]:
    words = {w.rstrip("s") if len(w) > 4 else w for w in _WORD.findall(text.casefold())}
    # Digits never count: every dated headline carries "2026".
    return {w for w in words if len(w) >= 4 and w not in _STOP and not w.isdigit()}


def _headline(decision: str) -> str:
    """The bold headline of a `Decisions.md` entry, date lead stripped."""
    found = _BOLD.search(decision)
    head = found["head"] if found else decision.split("·")[0]
    return _DATE_LEAD.sub("", head)


def _touches(text: str, headlines: Sequence[set[str]]) -> bool:
    words = _content(text)
    return any(len(words & head) >= 2 for head in headlines)


@dataclass(frozen=True)
class _Item:
    note: Note
    owner: str
    text: str
    rank: int
    #: The text names him without him owning it - "Notify Alex: ...".
    names_him: bool = False


def _items(notes: Sequence[Note], principal: Principal, decisions: Sequence[str]) -> list[_Item]:
    headlines = [h for h in (_content(_headline(d)) for d in decisions) if h]
    items: list[_Item] = []
    for note in notes:
        for owner, text in next_steps(note.body):
            if principal.is_him(owner):
                rank = _MINE
            elif _touches(text, headlines):
                rank = _DECISION
            elif principal.is_report(owner):
                rank = _REPORTS
            else:
                rank = _REST
            items.append(_Item(note, owner, text, rank, rank != _MINE and principal.named_in(text)))
        # Prose reaches the list only where it touches an open decision -
        # the rest of it is status, and the full note is one click away.
        for sentence in _sentences(_prose(note.body)):
            # A Quick Notes section heading is its own paragraph with no
            # closing punctuation, and it is not a finding. On the 9/25 replay
            # "Revenue data platform data model and architecture" was filed
            # as a chase row because it shared words with a decision.
            if not _SENTENCE_END.search(sentence):
                continue
            if _touches(sentence, headlines):
                items.append(_Item(note, "", sentence, _DECISION))
    # Stable: note order is kept inside a bucket, except that a step naming
    # him leads - it is coming to him even when it is not his to do.
    items.sort(key=lambda i: (i.rank, not i.names_him))
    return items


def _line(item: _Item) -> str:
    """One push line: the step as the note wrote it, which `State.md` files as
    its title plus the verbatim quote - the same words (#180).

    With house rule 7 on (`state.withholds_private()`) the pre-#180 minimal
    quoting applies instead - and the vault withholds the row as well, so the
    two still agree.
    """
    note = item.note
    body = short(item.text)
    if withholds_private():
        if classify_sensitivity(item.text) == "private":
            body = "a personnel/comp item - not quoted here, open the note"
        elif note.sensitive:
            # The step reads innocuously but the note around it is about pay
            # or people: its title only (Gemini's "Title: detail" shape).
            title = _title(item.text)
            body = (
                f"{title} - detail not quoted, open the note"
                if title
                else "an item from a personnel/comp conversation - open the note"
            )
    who = f"[{item.owner}] " if item.owner else ""
    heard = " - names you" if item.names_him else ""
    if item.rank == _MINE and note.verdict.status != ATTENDED:
        heard = " - ⚠ you may not have heard this one"
    return claim(f"{who}{body} {note.marker()}{heard}", note.permalink or None)


def _roll_line(note: Note) -> str:
    tick = "✓ " if note.verdict.status == ATTENDED else ""
    return claim(
        f"{tick}{note.title} - {note.verdict.status} ({note.verdict.reason})",
        note.permalink or None,
    )


def priorities(
    notes: Iterable[Any],
    events: Iterable[Any],
    principal: Principal,
    *,
    decisions: Sequence[str],
    day: date,
    tz: tzinfo = recipes.PACIFIC,
) -> list[Section]:
    """The EOD sections: today's attendance roll, then ranked priorities.

    Only notes for ``day`` - by the joined meeting's start, or the mail's
    arrival when nothing joined. Empty sections are returned empty and
    vanish in `render_push` (silence is information).
    """
    today = [n for n in join(notes, events, principal) if n.local_day(tz) == day]
    if not today:
        return []
    today.sort(key=lambda n: n.arrived or datetime.max.replace(tzinfo=tz))
    counts = Counter(n.verdict.status for n in today)
    roll = Section(
        f"today's calls ({len(today)}) - {counts[ATTENDED]} attended, "
        f"{counts[NOT_ATTENDED]} not, {counts[UNCONFIRMED]} unconfirmed",
        tuple(
            _roll_line(n)
            if n.has_body
            else claim(f"{n.title} - no body fetched, couldn't read it", n.permalink or None)
            for n in today
        ),
    )
    items = _items([n for n in today if n.has_body], principal, decisions)
    sections = [roll]
    for rank, name in enumerate(_BUCKETS):
        lines = tuple(_line(i) for i in items if i.rank == rank)
        sections.append(Section(f"priorities from today's calls - {name} ({len(lines)})", lines))
    return sections


# --------------------------------------------------------------------------
# the sweep: every ~30 minutes, idempotent
# --------------------------------------------------------------------------


def _reported(log: EventLog | None) -> set[str]:
    """Unplaced notes already named in a sweep's push - named once (#181)."""
    if log is None:
        return set()
    return {
        str(row.get("key"))
        for row in log.recorded(UNPLACED)
        if isinstance(row, Mapping) and row.get("key")
    }


def _seen(log: EventLog | None) -> set[str]:
    if log is None:
        return set()
    return {
        str(row.get("message_id"))
        for row in log.recorded(INGESTED)
        if isinstance(row, Mapping) and row.get("message_id")
    }


def _digest(note: Note, principal: Principal, tz: tzinfo) -> str:
    """#17's line: "logged from the 9:15am <title>: 1 commitment (yours), 2 asks -> ..."."""
    labels: Counter[str] = Counter()
    owners: Counter[str] = Counter()
    for n, (owner, text) in enumerate(next_steps(note.body)):
        mine = principal.is_him(owner)
        label = classify(
            {
                "item_id": f"{note.message_id}:{n}",
                "section": "next_steps",
                "owner_form": "principal"
                if mine
                else ("the_group" if owner.casefold() == "the group" else "named_person"),
                "modality": "imperative",
                "mentions_principal": mine,
                "grounded_in_body": True,
                "text": text,
            }
        )
        labels[label or "unplaced"] += 1
        if label == "assigned_ask":
            owners[owner] += 1
    start = note.event.get("start") if note.event else None
    when = ""
    if isinstance(start, datetime):
        local = start.astimezone(tz)
        when = local.strftime("%I:%M%p").lstrip("0").lower().replace(":00", "") + " "
    parts = []
    if labels["principal_commitment"]:
        c = labels["principal_commitment"]
        parts.append(f"{c} commitment{'' if c == 1 else 's'} (yours)")
    if labels["assigned_ask"]:
        a = labels["assigned_ask"]
        top = ", ".join(f"{o}" + (f" x{k}" if k > 1 else "") for o, k in owners.most_common(3))
        more = len(owners) - 3
        parts.append(
            f"{a} ask{'' if a == 1 else 's'} → {top}" + (f" +{more} more" if more > 0 else "")
        )
    if labels["new_workstream"]:
        parts.append(f"{labels['new_workstream']} new workstream")
    if labels["unplaced"]:
        parts.append(f"{labels['unplaced']} unplaced")
    summary = ", ".join(parts) or "no next steps"
    return claim(
        f"logged from the {when}{note.title} ({note.verdict.status}): {summary}",
        note.permalink or None,
    )


def _key(item: _Item) -> str:
    """The dedupe key: the note's message id and the item's own text."""
    digest = hashlib.sha256(item.text.encode("utf-8")).hexdigest()[:12]
    return f"call:{item.note.message_id}:{digest}"


def _chase_payload(item: _Item, tz: tzinfo) -> dict[str, Any]:
    """The `CHASED` row for ``item``: `state.ChaseItem`'s fields, plus the
    provenance `state._render_chase` prints as the attendance marker."""
    note = item.note
    day = note.local_day(tz)
    # The ask line is the step's title and the quote is the whole step, so a
    # row reads cleanly and never says anything twice (the 9/25 replay had a
    # "..."-clipped ask repeated in full by the quote underneath).
    ask = _title(item.text) or short(item.text)
    marker = note.marker()
    return {
        "key": _key(item),
        "owner": item.owner,
        "ask": ask,
        # The row's own head prefix: a bare title sits inside other titles
        # ("Test Plan" in "Create Test Plan"), which would read as filed.
        "needle": f"{item.owner} · {ask} · {marker}",
        "quote": item.text,
        "permalink": note.permalink,
        "asked_on": day.isoformat() if day else "",
        "status": "open",
        "marker": marker,
        "attendance": note.verdict.status,
        "message_id": note.message_id,
        "note_title": note.title,
        "why": _BUCKETS[item.rank],
    }


def _file(items: Sequence[_Item], log: EventLog, tz: tzinfo) -> None:
    """Record every actionable item not already recorded (contract 6).

    Deduped on its key against the log itself, not only through the note
    seen-set: the two are separate rows, and a pruned seen-set row must not
    double every item it covered.
    """
    have = {item.key for item in log.chase_items(kinds={CHASED})}
    for item in items:
        payload = _chase_payload(item, tz)
        if payload["key"] in have:
            continue
        have.add(payload["key"])
        log.record(
            CHASED,
            # The MARK, kept so the rule can be switched back (#180). The
            # whole note counts: a step can read innocuously while the call
            # around it was about pay.
            sensitivity=classify_sensitivity(item.note.title, item.note.body, item.text),
            **payload,
        )


def sweep(
    notes: Sequence[Any],
    events: Iterable[Any],
    principal: Principal,
    *,
    log: EventLog | None,
    decisions: Sequence[str] = (),
    tz: tzinfo = recipes.PACIFIC,
) -> str:
    """The ingest push: new notes only, each with attendance; marks them seen
    and records their actionable items for the chase list (contract 6).

    Safe to run every ~30 minutes (contract 2). ``decisions`` are the open
    decisions' texts, as `priorities` takes them. Returns the push text.
    """
    from daydag.brief import render_push

    records = [r for r in notes if isinstance(r, Mapping)]
    joined = join(records, events, principal)
    seen = _seen(log)
    fresh = [n for n in joined if n.has_body and n.message_id and n.message_id not in seen]
    already = [n for n in joined if n.message_id and n.message_id in seen]
    # Filed and DM'd: owned next steps only. Prose that touches a decision
    # still ranks in the EOD, but a status sentence has nobody to chase.
    actionable = [i for i in _items(fresh, principal, decisions) if i.rank != _REST and i.owner]
    handled = {id(n) for n in fresh + already}

    # Unplaced: not a Gemini note, or one fetched without a body. Named by
    # id, never dropped - the old contract of this loop, kept - but named
    # ONCE: at a 30-minute cadence the same block every sweep is noise. The
    # EOD's attendance roll names a note still without a body again.
    unplaced: list[tuple[str, str]] = []
    for n, record in enumerate(records):
        mid = _field(record, "id", "messageId", "message_id") or str(record.get("subject") or n)
        if title_from_gemini_subject(str(record.get("subject", ""))) is None:
            unplaced.append((mid, f"- {mid} - not a gemini note"))
    for note in joined:
        if id(note) in handled:
            continue
        if not note.has_body:
            unplaced.append(
                (
                    note.message_id or note.title,
                    f"- {note.message_id or note.title} ({note.title}) - no body fetched,"
                    " fetch it in PLAIN_TEXT",
                )
            )
        elif not note.message_id:
            unplaced.append((note.title, f"- {note.title} - no message id, can't dedupe it"))
    reported = _reported(log)
    repeat = [line for key, line in unplaced if key in reported]
    unplaced_new = [(key, line) for key, line in unplaced if key not in reported]

    sections: list[Section] = []
    if fresh:
        sections.append(
            Section(
                "logged - anything wrong, tell me and i'll fix",
                tuple(_digest(n, principal, tz) for n in fresh),
            )
        )
        # The three buckets that are filed for the chase list, line for line
        # what `_file` records - "everything else" stays out of both.
        for rank in (_MINE, _DECISION, _REPORTS):
            lines = tuple(_line(i) for i in actionable if i.rank == rank)
            sections.append(Section(f"{_BUCKETS[rank]} ({len(lines)})", lines))
    if unplaced_new:
        sections.append(
            Section(
                f"unplaced ({len(unplaced_new)}) - these need you, not a guess",
                tuple(line for _, line in unplaced_new),
            )
        )

    if log is not None:
        _file(actionable, log, tz)
        for key, _ in unplaced_new:
            log.record(UNPLACED, key=key)
        for note in fresh:
            log.record(
                INGESTED,
                sensitivity=classify_sensitivity(note.title),
                message_id=note.message_id,
                title=note.title,
                attendance=note.verdict.status,
            )

    if fresh:
        header = f"ingest: {len(fresh)} new note{'' if len(fresh) == 1 else 's'}"
    elif already:
        header = "ingest: nothing new since the last sweep"
    elif unplaced_new:
        # Not "nothing new": nothing was READ. A metadata-only fetch of eleven
        # notes said "nothing new since the last sweep" on the 9/25 replay.
        header = "ingest: nothing ingested - see unplaced"
    else:
        header = "ingest: nothing new landed"
    if already:
        header += f" ({len(already)} already seen)"
    if repeat:
        header += f" ({len(repeat)} unplaced already reported)"
    unreachable = (
        ()
        if log is not None
        else ("the seen-set - no --log, so I can't remember what's been reported",)
    )
    return render_push(header, sections, unreachable)
