"""What the LIVE sources say moved, for him to confirm (#134, #167).

USING IT
    from daydag.movement import detect, open_items, unclaimed

    items = open_items(state=state_md, note=weekly_note, note_path=path)
    rows = detect(
        open_items=items, calendar=cal, slack=msgs, gmail=mail,
        slack_sent=his_msgs, gmail_sent=his_mail, slack_sweep=channels_and_dms,
        jira=moved_tickets, github=merged_reviewed_closed, now=now,
    )
    rows[0].proposed        # one of Movement.PROPOSALS - never "closed"
    rows[0].proposed in Movement.CLOSING   # "answered" / "sent": looks closed
    rows[0].evidence[0].quote, rows[0].evidence[0].permalink
    unclaimed(rows, jira=moved_tickets, github=merged_reviewed_closed)

CONTRACTS
    1. PURE and TOTAL. No source of its own, no clock of its own, no write
       path, and nothing it is handed can make it raise - a connector returns
       whatever it returns, and a detector that raises takes the whole wrap
       down with it. `daydag.ingestion` is held to the same two for the same
       reason.
    2. Nothing it returns is a CLOSURE. `Movement.PROPOSALS` is the whole
       vocabulary and no member asserts an item is done. This is #18's
       critical rule - "evidence of movement … surfaces it for confirmation,
       it never auto-closes" - lifted out of the chaser, because it is a
       property of the system and not of one loop.
    3. Every piece of evidence is VERBATIM and carries its own permalink where
       the source gave one (house rule 1). Nothing is paraphrased, because the
       row exists to be judged by him, not believed.
    4. Matching needs TWO distinctive terms in common. One is "the plan",
       which appears in every message he has ever received. The two failure
       directions are not equal: a miss costs a proposal he would have waved
       through, a false positive costs his trust in every row under it.
    5. Keyed on the item, so the same evening's second run proposes the same
       rows rather than a second copy of them.
    6. IDS BEAT WORDS. An item that names a gmail thread, a Slack permalink, a
       ticket key or a repo is matched on that id, including ids in his
       sub-bullets - an id in common is the same thread, not a fuzzy match,
       so reading sub-bullets for ids cannot manufacture the false positives
       reading them for WORDS would.

WHY IT EXISTS
    Every loop that reported status read it out of the vault, and the vault
    records what he had time to write down. On 2026-09-15, a day with
    seventeen meetings, that was nothing:

        dont just look at weekly note, actually look at calendar, slack and
        email to see how much things have moved, i dont always get the time to
        move things in obsidian

    `eod_wrap` derived "what closed today" from `brief.closed_red_items` - the
    checkboxes he ticks himself - and so reported `0 closed` on a full day, a
    fact about his bookkeeping dressed as a fact about his week.

WHO SAID IT, AND WHY IT MATTERS (#167)
    Authorship comes from the payload KEY, never from resolving an author:
    `slack_sent` and `gmail_sent` are fetched as his by construction
    (`from:<@principal>`, `in:sent`). That is what licenses the two CLOSING
    proposals - `answered` (he replied on the thread the ask came from) and
    `sent` (a link he dropped in the conversation of the person he owes it
    to). Both are claims about what HE did, which is exactly what the
    evidence shows; neither says the loop is done, and the wrap renders them
    under "looks closed - confirm", never under "closed today".

KNOWN LIMIT
    It does not know who ELSE spoke. Resolving a Slack author to a role token
    needs the people directory, which is why someone else's message is
    "discussed" rather than "the owner replied" - the weaker claim is the one
    the evidence supports. `sent` reads the counterpart from the item's own
    head (`Name → you`), so a chase row written without an arrow cannot earn
    it. Jira has no before/after here: a ticket in the window is one whose
    status or assignee CHANGED, and the quote says where it is now.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from daydag.board import keys_in
from daydag.brief import red_items
from daydag.state import _BULLET, _HEADING, read_section

#: Words that carry no identity. Everything he writes is about a plan, a team,
#: a list or the data, so overlap on one of these is overlap on nothing.
_STOPWORDS = frozenset(
    """
    about after also back been before both call come data does down each else
    even ever from give have here into just keep know like make many more most
    much must need next only over plan same somesuch take team than that them
    then there these they thing this time very want week well were what when
    where which while will with work would your
    """.split()
)

#: Four characters, because "the", "for" and "and" are noise and "PR", "ML"
#: and "AI" are ambiguous enough to behave like noise.
_MIN_TERM = 4

#: Two distinctive terms in common. Contract 4 is the reasoning.
_MIN_OVERLAP = 2

_WORD = re.compile(r"[a-z0-9][a-z0-9'-]*")

#: Checkbox syntax and the weekly note's ownership tags. `[^)]*` after the
#: keyword because they are written freehand: `(mine)`, `(mine w/ Gov-Lead)`,
#: `(tracking: VP-Data)`.
_TAGS = re.compile(r"\[[ xX]\]|\((?:mine|x-team|tracking)[^)]*\)", re.IGNORECASE)

#: Emphasis, stripped as CHARACTERS and never as a span.
#:
#: Deleting `*...*` as a span was greedy: on `**Deal Modeler** cutover *(mine)*`
#: it ate from the first asterisk to the last and returned the empty set, so a
#: bold-titled red item could never reach `_MIN_OVERLAP` and never produced a
#: proposal - silently. The live weekly note bolds every item title, which is
#: `weekly-planning`'s house format, so the detector was blind to precisely the
#: items it exists to track. It also ran over Slack evidence, where `*bold*` is
#: Slack's own markup.
_EMPHASIS = re.compile(r"[*_`~]+")

#: A bare date or number. `2026-09-10` survives `_WORD` as one token and is
#: shared by every item filed on the same day.
_NUMERIC = re.compile(r"^[\d-]+$")

#: The chase row's own bookkeeping. `- owner · ask · asked-on DATE · status
#: open` puts `asked-on`, `status` and `open` in EVERY row, which is two shared
#: terms before a single word of the ask is read - so one message saying
#: "status on that, still open" matched all four live chase items at once.
#: Stripped from the matching text; `OpenItem.text` keeps the whole row for
#: display, because what he reads should be the row he wrote.
_BOOKKEEPING = re.compile(
    r"·\s*(?:asked-on|due|last-activity|status|waiting since|snoozed-until)\b[^·]*",
    re.IGNORECASE,
)


def _terms(text: str) -> frozenset[str]:
    """The distinctive words in ``text``, lowercased.

    Tags come off before emphasis, so `*(mine w/ Gov-Lead)*` loses the tag
    while its parens are still there to bound it and the orphaned asterisks go
    with the emphasis pass.
    """
    cleaned = _BOOKKEEPING.sub(" ", str(text))
    cleaned = _EMPHASIS.sub("", _TAGS.sub(" ", cleaned)).casefold()
    return frozenset(
        word
        for word in _WORD.findall(cleaned)
        if len(word) >= _MIN_TERM and word not in _STOPWORDS and not _NUMERIC.match(word)
    )


@dataclass(frozen=True)
class OpenItem:
    """One thing that is open, and where it was read from.

    `source` is a vault path so a proposal about the item can cite the item -
    house rule 1 applies to the claim "this is open" exactly as it applies to
    the evidence against it.
    """

    key: str
    text: str
    owner: str
    source: str
    #: Ids the item names anywhere in its block, sub-bullets included:
    #: `gmail:<thread>`, `slack:<channel>:<ts>`, `slackdm:<channel>`,
    #: `jira:<KEY>`, `repo:<owner/name>`, `pr:<owner/name>#<n>`. Contract 6.
    anchors: frozenset[str] = field(default_factory=frozenset)
    #: Names on the OTHER side of an ask he owes (`Name → you`), lowercased
    #: words. Empty when he does not owe it, which is what keeps `sent` off.
    counterparts: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Evidence:
    """One verbatim thing a live source said, and where to go and read it."""

    source: str
    quote: str
    permalink: str
    at: str
    #: The repo or Jira project a board/repo record belongs to, so the wrap
    #: can squash a busy repo to one line (SPEC 3.7 rule 1). Empty elsewhere.
    group: str = ""


@dataclass(frozen=True)
class Movement:
    """An open item, what was found, and what is being PROPOSED about it."""

    #: The whole vocabulary. Contract 2: neither member says an item is done.
    #: A scheduled meeting is not a held one and a mention is not an answer.
    #:
    #: Ordered strongest first: when an item has evidence of several kinds,
    #: the strongest names the row and its evidence is quoted first, so the
    #: label and the quote beside it always agree.
    PROPOSALS = (
        "answered",
        "sent",
        "merged",
        "ticket-moved",
        "reviewed",
        "scheduled",
        "discussed",
    )
    #: The two that LOOK like closure - he replied on the ask's own thread, or
    #: dropped a link where the person he owes it would see it. Rendered under
    #: "looks closed - confirm". Still proposals: a reply is not an answer he
    #: has accepted, and a link is not proof it was the thing asked for.
    CLOSING = ("answered", "sent")

    item: OpenItem
    evidence: tuple[Evidence, ...]
    proposed: str


#: A loop he has deliberately paused. `State.md` documents the convention
#: ("To pause instead, write `status: snoozed-until YYYY-MM-DD`"), and parked
#: is the chase list's own third state. Re-proposing either is re-asking a
#: question he has already answered.
_PAUSED = re.compile(r"snoozed-until|status:?\s*(?:parked|snoozed)", re.IGNORECASE)


def _key(text: str) -> str:
    """The identity of an item, for deduping it across the two stores.

    Built from `_terms` rather than from the raw line, because the same loop is
    written differently in each: `- eddie · fruits metadata list · asked-on …`
    in the chase list and `- [ ] 🔴 fruits metadata list` in the weekly note.
    Keying on the raw head made those two different items and the wrap asked
    him to confirm one thing twice.

    Two rows still survive when the wordings genuinely differ - an owner named
    in one and not the other is a real difference in the words. That is the
    cheap failure of the two: he can see a duplicate.
    """
    terms = _terms(text)
    return " ".join(sorted(terms)) if terms else " ".join(str(text).split())[:60].casefold()


def open_items(*, state: str, note: str, note_path: str) -> list[OpenItem]:
    """Everything open, from the chase list and the week's red items.

    Both stores, because he keeps open loops in both and neither one is
    complete. A struck chase row and a ticked red item are each his own answer
    already and are not returned - re-proposing something he has crossed off
    is the loudest possible way to prove his edits do not win.

    Top-level bullets only. An indented one is his comment on the item above
    it, which is the file's documented convention and not a second loop: the
    live State.md has 4 chase items under 22 bullets, and reading all 22 as
    open would propose movement against his own annotations.
    """
    items: list[OpenItem] = []
    blocks = _blocks(str(state or ""), "Chase list")
    for body in read_section(str(state or ""), "Chase list", top_level=True):
        if "~~" in body or _PAUSED.search(body):
            continue
        owner = body.split("·")[0] if "·" in body else ""
        items.append(
            OpenItem(
                key=_key(body),
                text=body,
                owner=re.sub(r"[*_`]", "", owner).strip(),
                source="DayDAG/State.md",
                anchors=_anchors(blocks.get(body, body), head=body),
                counterparts=_counterparts(body),
            )
        )
    # `red_items` already skips the ticked side and the triage legend, and
    # reads the note's own layout rather than a template (invariant 6).
    for _lineno, text in red_items(str(note or "")):
        items.append(
            OpenItem(
                key=_key(text),
                text=text,
                owner="",
                source=note_path,
                anchors=_anchors(text, head=text),
            )
        )
    return _deduped(items)


def _blocks(text: str, name: str) -> dict[str, str]:
    """Each top-level bullet under ``name``, mapped to itself plus its sub-bullets.

    Same section rule as `state.read_section` - a nested heading does not end
    the section - and its own regexes, imported rather than copied. The block
    is read for IDS only (contract 6); its words never reach `_terms`.
    """
    wanted = name.casefold()
    collecting, level_of, current = False, 0, ""
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            level = len(heading["hashes"])
            if heading["name"].casefold() == wanted:
                collecting, level_of = True, level
            elif collecting and level <= level_of:
                collecting = False
            continue
        if not collecting:
            continue
        bullet = _BULLET.match(line)
        if bullet and not line[:1].isspace():
            current = bullet["body"]
            out.setdefault(current, [current])
        elif current and line[:1].isspace():
            out[current].append(line)
    return {head: "\n".join(lines) for head, lines in out.items()}


#: A gmail thread or message id: sixteen lowercase hex digits, standing alone.
_GMAIL_ID = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{16}(?![0-9A-Za-z])")
#: A Slack permalink: the conversation, and `p` + the ts with its dot removed.
_SLACK_LINK = re.compile(r"archives/(?P<channel>[CDG][A-Z0-9]+)/p(?P<sec>\d{10})(?P<frac>\d{6})")
#: `owner/name`, optionally as a github url, optionally with `/pull/N`. Loose
#: on purpose: an anchor only ever matches a record that names the SAME repo,
#: so a spurious one (`w/ seth`) costs nothing.
_REPO = re.compile(
    r"(?:github\.com/)?(?P<slug>[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9])"
    r"(?:/(?:pull|issues)/(?P<number>\d+))?"
)


def _anchors(block: str, *, head: str = "") -> frozenset[str]:
    """Every id ``block`` names, typed so two kinds of id can never collide.

    A whole DM (`slackdm:`) is an anchor only when the row's ``head`` links
    it - that is the conversation the ask came from, so his next message
    there is plausibly the reply. A DM linked in a sub-bullet is CONTEXT: on
    the real 2026-09-25 file the sponsor's-question row cited VP-Data's DM,
    and "sure will join back" there was proposed as the answer.
    """
    text = str(block or "")
    found: set[str] = {f"gmail:{gid}" for gid in _GMAIL_ID.findall(text)}
    for link in _SLACK_LINK.finditer(text):
        found.add(f"slack:{link['channel']}:{link['sec']}.{link['frac']}")
    for link in _SLACK_LINK.finditer(str(head or "")):
        if link["channel"].startswith("D"):
            found.add(f"slackdm:{link['channel']}")
    found |= {f"jira:{key}" for key in keys_in(text)}
    for repo in _REPO.finditer(text):
        slug = repo["slug"].casefold()
        found.add(f"repo:{slug}")
        if repo["number"]:
            found.add(f"pr:{slug}#{repo['number']}")
    return frozenset(found)


#: Who "you" is in his own shorthand for an ask he owes.
_HIM = frozenset({"you", "me", "mine"})
_NAME_WORD = re.compile(r"[a-z][a-z'-]+")


def _counterparts(head: str) -> frozenset[str]:
    """The names he owes this to, from `Name → you` - or nothing.

    Only the arrow form: it is the one place the row itself says who is
    waiting on whom. `vp-data · ask` names an owner, and the owner of an ask
    he is chasing is not someone he owes a delivery to.
    """
    first = _EMPHASIS.sub("", str(head).split("·")[0])
    if "→" not in first:
        return frozenset()
    *others, target = first.split("→")
    if target.strip().casefold() not in _HIM:
        return frozenset()
    words: set[str] = set()
    for part in others:
        for word in _NAME_WORD.findall(part.casefold()):
            stem = word.split("-")[0]
            if len(stem) >= 3 and stem not in _HIM:
                words.add(stem)
    return frozenset(words)


def _deduped(items: list[OpenItem]) -> list[OpenItem]:
    """One row per loop, first store wins.

    He tracks the same loop in both stores routinely - a red item in the week's
    priorities and a chase row for the person who owes it - and reading both is
    deliberate, because neither is complete. Without this the wrap prints the
    same thing twice under `looks moved - confirm` and counts it twice in the
    header.

    Keyed on `_key`, which is the flattened head of the row, so the two
    wordings have to actually agree. They often will not, and a duplicate is
    the cheap failure here: he can see it.
    """
    seen: set[str] = set()
    out: list[OpenItem] = []
    for item in items:
        if item.key in seen:
            continue
        seen.add(item.key)
        out.append(item)
    return out


def _records(raw: Any) -> list[Mapping[str, Any]]:
    """Every mapping in ``raw``, and nothing else. Contract 1's totality: the
    payload is whatever a connector handed back, including `None` and a bare
    string where a list was promised."""
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, Mapping)):
        return []
    return [record for record in raw if isinstance(record, Mapping)]


def _text(record: Mapping[str, Any], *fields: str) -> str:
    return " ".join(str(record.get(f) or "") for f in fields).strip()


@dataclass(frozen=True)
class _Candidate:
    """One live record, reduced once to everything any rule reads off it."""

    evidence: Evidence
    terms: frozenset[str]
    #: Ids this record IS, in the same typed form as `OpenItem.anchors`.
    ids: frozenset[str] = frozenset()
    #: Fetched as HIS (`slack_sent`, `gmail_sent`) - by the payload key, never
    #: by resolving an author.
    his: bool = False
    #: Lowercased words naming the conversation or the recipients - who
    #: would see it. `sent` matches an item's counterparts against these.
    audience: frozenset[str] = frozenset()
    #: Carries a link or an attachment - the shape of a delivery.
    delivers: bool = False
    #: Fixed proposal for board/repo records, whose meaning is their kind.
    fixed: str = ""


#: Per word-matched source: the field QUOTED, the fields matched on, where the
#: link is, and where the timestamp is. Quote and match are separate columns
#: because they answer different questions - a Gemini note matches on its
#: subject and its snippet together, but quoting the two concatenated produces
#: a sentence that appears nowhere in the message he is being sent to go and
#: read, which is contract 3 and house rule 1 broken where they are
#: load-bearing.
_SHAPES: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], str]] = {
    "calendar": ("summary", ("summary",), ("htmlLink", "permalink"), "start"),
    "slack": ("text", ("text",), ("permalink",), "ts"),
    "gmail": ("subject", ("subject", "snippet"), ("permalink", "link"), "date"),
}

_URL = re.compile(r"https?://", re.IGNORECASE)


def _link(record: Mapping[str, Any], fields: Sequence[str]) -> str:
    return next((str(record[f]) for f in fields if record.get(f)), "")


def _slack_ids(record: Mapping[str, Any]) -> frozenset[str]:
    """The thread a message sits in and the conversation it was posted to."""
    channel = str(record.get("channel") or record.get("channel_id") or "")
    if not channel:
        found = _SLACK_LINK.search(str(record.get("permalink") or ""))
        channel = found["channel"] if found else ""
    if not channel:
        return frozenset()
    ids = {f"slack:{channel}:{record.get(f)}" for f in ("ts", "thread_ts") if record.get(f)}
    if channel.startswith("D"):
        ids.add(f"slackdm:{channel}")
    return frozenset(ids)


def _words(*values: Any) -> frozenset[str]:
    text = " ".join(
        " ".join(map(str, v)) if isinstance(v, list | tuple) else str(v or "") for v in values
    )
    return frozenset(re.findall(r"[a-z]{3,}", text.casefold()))


#: Slack markup that names a person, a channel or a link - never the subject.
#: `<@U123|Seth Jensen>` and a pasted URL's path segments matched open items on
#: the real 2026-09-25 evening.
_SLACK_MARKUP = re.compile(r"<[@#!][^>]*>|<https?://[^>]*>|https?://\S+")


def _subject_terms(text: str, participants: frozenset[str]) -> frozenset[str]:
    """What a message is ABOUT: its words, minus markup and minus the names of
    the people in the conversation, who are in every message in it."""
    return frozenset(_terms(_SLACK_MARKUP.sub(" ", text)) - participants)


#: A `Name, Name` conversation label or recipient list, as name words.
_PERSON = re.compile(r"[a-z][a-z'-]{2,}")


def _people(*values: Any) -> frozenset[str]:
    text = " ".join(
        " ".join(map(str, v)) if isinstance(v, list | tuple) else str(v or "") for v in values
    )
    return frozenset(_PERSON.findall(text.casefold().replace(".", " ")))


def _slack(records: Any, *, his: bool) -> list[_Candidate]:
    out: list[_Candidate] = []
    for record in _records(records):
        quote, link = _text(record, "text"), _link(record, ("permalink",))
        if not quote or not link:
            continue
        participants = _people(record.get("channel_name"))
        out.append(
            _Candidate(
                Evidence("slack", quote, link, str(record.get("ts") or "")),
                _subject_terms(quote, participants),
                ids=_slack_ids(record),
                his=his,
                audience=_words(record.get("channel_name"), quote),
                delivers=bool(_URL.search(quote) or record.get("files")),
            )
        )
    return out


#: A Gmail emoji reaction lands in Sent on the thread. It acknowledges; it
#: answers nothing - on 2026-09-25 a 👍 was proposed as a reply.
_REACTION = re.compile(r"reacted via Gmail", re.IGNORECASE)


def _gmail_sent(records: Any) -> list[_Candidate]:
    """His own mail. Quoted by its SNIPPET - his words, verbatim - because the
    subject of a reply is the other person's subject with `Re:` on it."""
    out: list[_Candidate] = []
    for record in _records(records):
        link = _link(record, ("permalink", "viewUrl", "link"))
        quote = html.unescape(_text(record, "snippet") or _text(record, "subject"))
        if not quote or not link or _REACTION.search(quote):
            continue
        ids = {f"gmail:{record[f]}" for f in ("threadId", "id") if record.get(f)}
        recipients = _people(record.get("to"), record.get("toRecipients"))
        out.append(
            _Candidate(
                Evidence("gmail", quote, link, str(record.get("date") or "")),
                _subject_terms(html.unescape(_text(record, "subject", "snippet")), recipients),
                ids=frozenset(ids),
                his=True,
                audience=_words(record.get("to"), record.get("toRecipients")),
                delivers=bool(record.get("attachments") or _URL.search(quote)),
            )
        )
    return out


def _field(record: Mapping[str, Any], name: str) -> Any:
    """A Jira field, flat or under `fields` - the agent may hand back either."""
    fields = record.get("fields")
    if isinstance(fields, Mapping) and name in fields:
        return fields[name]
    return record.get(name)


def _named(value: Any, key: str = "name") -> str:
    return str(value.get(key) or "") if isinstance(value, Mapping) else str(value or "")


def _jira(records: Any) -> list[_Candidate]:
    """Tickets whose status or assignee changed in the window - by key only.

    Keys are the spine (SPEC 3.7): a ticket matches the item that names it,
    never one that happens to share two words with its summary.
    """
    out: list[_Candidate] = []
    for record in _records(records):
        key = str(record.get("key") or "")
        link = _link(record, ("permalink", "webUrl", "url"))
        if not key or not link:
            continue
        status = _named(_field(record, "status"))
        assignee = _named(_field(record, "assignee"), "displayName")
        quote = " ".join(part for part in (key, _named(_field(record, "summary"))) if part)
        quote += f" - now {status}" if status else ""
        quote += f", {assignee}" if assignee else ""
        out.append(
            _Candidate(
                Evidence("jira", quote, link, _named(_field(record, "updated")), key.split("-")[0]),
                frozenset(),
                ids=frozenset({f"jira:{key}"}),
                fixed="ticket-moved",
            )
        )
    return out


#: A GitHub hit's `kind` (the plan step that fetched it) -> what it proposes.
#: A closed ISSUE is a ticket that moved; nothing here says "closed".
_GITHUB_KINDS = {
    "merged": "merged",
    "approved": "reviewed",
    "changes_requested": "reviewed",
    "issue_closed": "ticket-moved",
}


def _github(records: Any) -> list[_Candidate]:
    """Merged, reviewed and closed on watched repos - by repo or PR id only."""
    out: list[_Candidate] = []
    for record in _records(records):
        link = str(record.get("url") or "")
        proposal = _GITHUB_KINDS.get(str(record.get("kind") or ""))
        found = _REPO.search(link)
        if not link or proposal is None or found is None:
            continue
        repo = _named(record.get("repository"), "nameWithOwner") or found["slug"]
        slug = repo.casefold()
        number = str(record.get("number") or found["number"] or "")
        ids = {f"repo:{slug}"} | ({f"pr:{slug}#{number}"} if number else set())
        quote = f"{repo}#{number} {_text(record, 'title')}".strip()
        out.append(
            _Candidate(
                Evidence("github", quote, link, str(record.get("closedAt") or ""), repo),
                frozenset(),
                ids=frozenset(ids),
                fixed=proposal,
            )
        )
    return out


def _candidates(
    *,
    calendar: Any,
    slack: Any,
    gmail: Any,
    slack_sent: Any,
    gmail_sent: Any,
    slack_sweep: Any,
    jira: Any,
    github: Any,
) -> list[_Candidate]:
    """Every live record, reduced once.

    One pass over each payload rather than one per open item: the terms are
    the expensive part and they do not depend on which item is being matched.

    A record with no permalink is DROPPED, which is what `run._prep` already
    does to a linkless message and for the same reason. The alternative was
    keeping it and letting the wrap cite the vault path the item came from
    instead, which renders a Slack quote as though `DayDAG/State.md` said it -
    a citation that points at the wrong document is worse than no row.
    """
    out: list[_Candidate] = []
    for source, payload in (("calendar", calendar), ("gmail", gmail)):
        quote_field, match_fields, link_fields, at_field = _SHAPES[source]
        for record in _records(payload):
            quote, link = _text(record, quote_field), _link(record, link_fields)
            if not quote or not link:
                continue
            out.append(
                _Candidate(
                    Evidence(source, quote, link, str(record.get(at_field) or "")),
                    _terms(_text(record, *match_fields)),
                )
            )
    out += _slack(slack, his=False)
    out += _slack(slack_sweep, his=False)
    out += _slack(slack_sent, his=True)
    out += _gmail_sent(gmail_sent)
    out += _jira(jira)
    out += _github(github)
    return out


#: A bold title at the head of a row - `weekly-planning`'s house format, and
#: how he writes chase rows. Optional checkbox and triage mark before it.
_BOLD_TITLE = re.compile(r"^\s*(?:\[[ xX]\]\s*)?(?:🔴\s*)?\*\*(?P<title>.+?)\*\*")


def _identity(text: str) -> str:
    """The words that say WHAT an item is - what words get matched on.

    The bold title when the row has one, since the rest is his annotation (the
    same reason sub-bullets are never matched on); then the owner field off
    the front, since `nitin+seth · ...` names who, not what. On the real
    2026-09-25 evening, matching the whole row proposed a 32-term red item as
    discussed because an unrelated message shared `draft` and `roadmap` with
    its description, and a chase row because a DM said `nitin` and `seth`.
    """
    found = _BOLD_TITLE.search(str(text))
    head = found["title"] if found else str(text)
    return head.split("·", 1)[1] if "·" in head else head


def _proposal(item: OpenItem, wanted: frozenset[str], candidate: _Candidate) -> str:
    """What ``candidate`` proposes about ``item``, or ``""`` for nothing.

    Ids first (contract 6), then his deliveries, then words (contract 4).
    """
    shared = item.anchors & candidate.ids
    if candidate.fixed:
        return candidate.fixed if shared else ""
    if shared:
        return "answered" if candidate.his else "discussed"
    if (
        candidate.his
        and candidate.delivers
        and item.counterparts
        and item.counterparts & candidate.audience
    ):
        return "sent"
    if len(wanted & candidate.terms) >= _MIN_OVERLAP:
        return "scheduled" if candidate.evidence.source == "calendar" else "discussed"
    return ""


def detect(
    *,
    open_items: Sequence[OpenItem],
    calendar: Any = (),
    slack: Any = (),
    gmail: Any = (),
    slack_sent: Any = (),
    gmail_sent: Any = (),
    slack_sweep: Any = (),
    jira: Any = (),
    github: Any = (),
    now: datetime | None = None,
) -> list[Movement]:
    """One row per open item with evidence against it, in item order.

    The row's label is the strongest proposal any of its evidence supports
    (`Movement.PROPOSALS` order), and that evidence leads the tuple.

    ``now`` is accepted and unused: the windows are the caller's, bounded by
    the recipe that fetched them, and a module that re-derived "today" here
    would disagree with the payload it was handed. It stays in the signature
    because every consumer has one and leaving it out invites a caller to
    filter by date itself, which is the duplication this exists to stop.

    Args:
        open_items: from :func:`open_items`. Anything that is not an
            ``OpenItem`` is skipped rather than coerced - contract 1.
        slack_sent, gmail_sent: HIS messages and mail, fetched as his.
        slack_sweep: watched channels and DMs to him, whoever wrote them.
        jira, github: the evening's board and repo movement (`run.plan eod`).
    """
    candidates = _candidates(
        calendar=calendar,
        slack=slack,
        gmail=gmail,
        slack_sent=slack_sent,
        gmail_sent=gmail_sent,
        slack_sweep=slack_sweep,
        jira=jira,
        github=github,
    )
    rank = {name: index for index, name in enumerate(Movement.PROPOSALS)}
    rows: list[Movement] = []
    for item in open_items:
        if not isinstance(item, OpenItem):
            continue
        wanted = _terms(_identity(item.text))
        hits: list[tuple[str, Evidence]] = []
        seen: set[str] = set()
        for candidate in candidates:
            proposal = _proposal(item, wanted, candidate)
            # One message fetched twice - mentioned AND in a watched channel -
            # is one piece of evidence, not two.
            if proposal and candidate.evidence.permalink not in seen:
                seen.add(candidate.evidence.permalink)
                hits.append((proposal, candidate.evidence))
        if not hits:
            continue
        hits.sort(key=lambda hit: rank[hit[0]])  # stable: source order within a rank
        rows.append(Movement(item=item, evidence=tuple(e for _, e in hits), proposed=hits[0][0]))
    return rows


def unclaimed(rows: Sequence[Movement], *, jira: Any = (), github: Any = ()) -> list[Evidence]:
    """Board and repo movement no open item claimed - Jira first, then GitHub.

    The wrap's "moved" block. A merged PR on a watched repo is worth one line
    whether or not a chase row names it; it is still evidence, never closure,
    and it is not duplicated when a row already carries it.
    """
    claimed = {
        evidence.permalink for row in rows if isinstance(row, Movement) for evidence in row.evidence
    }
    return [
        candidate.evidence
        for candidate in (*_jira(jira), *_github(github))
        if candidate.evidence.permalink not in claimed
    ]
