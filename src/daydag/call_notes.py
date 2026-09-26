"""Call notes: which calls he was in, and what came out of all of them (#168).

USING IT
    principal = Principal.from_identities(identities)   # names from .env addresses

    call_notes.sweep(gmail, events, principal, log=log, tz=tz)     # the ingest push
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
    4. Personnel / comp / M&A text (`state.classify_sensitivity`) is never
       quoted, even in the DM (house rule 7): the line says such an item
       exists and links the note. Nothing in this module writes the vault.
    5. Pure over what it is handed, like `ingestion`: no connector, no vault
       read. `run` fetches, reads `Decisions.md`, and passes text in.

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
from daydag.state import EventLog, classify_sensitivity

__all__ = [
    "ATTENDED",
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

    A sentence `classify_sensitivity` calls private is passed over for a
    later one, because this is what the attendance roll QUOTES. If every one
    is private, the first is still returned - it is still evidence - and
    `attendance` declines to quote it.
    """
    said = _speech(body, principal)
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
        if classify_sensitivity(said) == "private":
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
            if _touches(sentence, headlines):
                items.append(_Item(note, "", sentence, _DECISION))
    # Stable: note order is kept inside a bucket, except that a step naming
    # him leads - it is coming to him even when it is not his to do.
    items.sort(key=lambda i: (i.rank, not i.names_him))
    return items


def _line(item: _Item) -> str:
    note = item.note
    if classify_sensitivity(item.text) == "private":
        # House rule 7: DM only, minimally quoted - so not quoted at all.
        body = "a personnel/comp item - not quoted here, open the note"
    elif note.sensitive:
        # The step reads innocuously but the note around it is about pay or
        # people: its title only (Gemini's "Title: detail" shape).
        title = item.text.split(":", 1)[0] if ":" in item.text else ""
        body = (
            f"{title} - detail not quoted, open the note"
            if title
            else ("an item from a personnel/comp conversation - open the note")
        )
    else:
        body = short(item.text)
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


def sweep(
    notes: Sequence[Any],
    events: Iterable[Any],
    principal: Principal,
    *,
    log: EventLog | None,
    tz: tzinfo = recipes.PACIFIC,
) -> str:
    """The ingest push: new notes only, each with attendance; marks them seen.

    Safe to run every ~30 minutes (contract 2). Returns the push text.
    """
    from daydag.brief import render_push

    records = [r for r in notes if isinstance(r, Mapping)]
    joined = join(records, events, principal)
    seen = _seen(log)
    fresh = [n for n in joined if n.has_body and n.message_id and n.message_id not in seen]
    already = [n for n in joined if n.message_id and n.message_id in seen]
    handled = {id(n) for n in fresh + already}

    # Unplaced: not a Gemini note, or one fetched without a body. Named by
    # id, never dropped - the old contract of this loop, kept.
    unplaced: list[str] = []
    for n, record in enumerate(records):
        mid = _field(record, "id", "messageId", "message_id") or str(record.get("subject") or n)
        if title_from_gemini_subject(str(record.get("subject", ""))) is None:
            unplaced.append(f"- {mid} - not a gemini note")
    for note in joined:
        if id(note) in handled:
            continue
        if not note.has_body:
            unplaced.append(
                f"- {note.message_id or note.title} ({note.title}) - no body fetched,"
                " fetch it in PLAIN_TEXT"
            )
        elif not note.message_id:
            unplaced.append(f"- {note.title} - no message id, can't dedupe it")

    sections: list[Section] = []
    if fresh:
        sections.append(
            Section(
                "logged - anything wrong, tell me and i'll fix",
                tuple(_digest(n, principal, tz) for n in fresh),
            )
        )
        mine = [
            _Item(n, owner, text, _MINE)
            for n in fresh
            for owner, text in next_steps(n.body)
            if principal.is_him(owner)
        ]
        sections.append(Section(f"assigned to you ({len(mine)})", tuple(_line(i) for i in mine)))
    if unplaced:
        sections.append(
            Section(f"unplaced ({len(unplaced)}) - these need you, not a guess", tuple(unplaced))
        )

    if log is not None:
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
    elif unplaced:
        # Not "nothing new": nothing was READ. A metadata-only fetch of eleven
        # notes said "nothing new since the last sweep" on the 9/25 replay.
        header = "ingest: nothing ingested - see unplaced"
    else:
        header = "ingest: nothing new landed"
    if already:
        header += f" ({len(already)} already seen)"
    unreachable = (
        ()
        if log is not None
        else ("the seen-set - no --log, so I can't remember what's been reported",)
    )
    return render_push(header, sections, unreachable)
