"""Open is a verdict, not a default: read the reply before reporting an ask open.

USING IT
    asks = asks_in(state_text, decisions_text)      # every unstruck ask the vault carries
    steps = closure_steps(asks)                     # what to read: after each ask, and its thread
    fetched = {step.key: messages, ...}             # what the agent read, keyed by the ask
    verdicts = judge(asks, fetched, principal="U…") # answered | open | unchecked, with evidence
    render_closure(verdicts, section="Chase list")  # three buckets, evidence on every line

CONTRACTS
    1. An ask whose conversation was NOT read is `unchecked`, never `open`.
       `None` for its messages means "not fetched"; `[]` means "fetched and
       empty". The two are different facts and render differently - the same
       distinction `run` draws for a whole source.
    2. `answered` means the party who OWES the reply posted in that conversation
       after the ask. Under `Owed by you`, `promises you made` and the pending
       decisions, that party is the principal. Under `Chase list` it is the
       owner: the named owner's id when the caller knows it, otherwise anyone
       who is not the principal.
    3. Direction comes from the SECTION the line sits under, never from its
       text. "will revert before eod" is a promise because it is filed as one.
    4. A verdict edits nothing. An answered item renders with the closing
       message's quote and permalink so he can strike it (principle 5: surface,
       don't resolve) - but it does not render under `open`, which is the whole
       point.
    5. A line with no Slack permalink cannot be checked and says so.

WHY IT EXISTS
    On 2026-09-18 three items were reported to him as open that were already
    closed in Slack: a promise answered three hours after it was made in the
    same DM, a recording that had been posted in the thread the day before, and
    a decision he had answered in the thread it was asked in. Every one had
    been matched on the text of the ASK, and the reply under it was never read.
    His words: "these are things you should already try before coming back to
    me for directions". This module is that try, made mechanical: the runner
    plans the reads, and an item cannot render as open until they happened.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from daydag import push
from daydag.statedoc import StateDoc

__all__ = [
    "Ask",
    "ReadStep",
    "Verdict",
    "asks_in",
    "closure_steps",
    "judge",
    "render_closure",
    "slack_permalink",
]

#: The State.md sections an ask can be filed under, and who owes the reply.
#: `Owed by you` carries `### promises you made` nested inside it; both wait on
#: the principal. The chase list waits on the named owner.
SECTIONS: Mapping[str, str] = {
    "Chase list": "owner",
    "Owed by you": "principal",
}

#: Where a Slack message's author id and stamp sit, depending on which
#: connector call produced the record. Checked in order, first present wins.
_USER_KEYS = ("user", "user_id", "from", "author")
_TS_KEYS = ("ts", "message_ts")

#: A Slack permalink, as `slack_read_thread` and the search tool print them:
#: `https://<ws>.slack.com/archives/<conversation>/p<16 digits>`, with an
#: optional `?thread_ts=…` when the message is a reply. The 16 digits are the
#: ts with its dot removed.
_PERMALINK = re.compile(
    r"https?://[\w.-]+\.slack\.com/archives/"
    r"(?P<conversation>[CDG][A-Z0-9]{6,})/p(?P<digits>\d{16})"
    r"(?:\?(?P<query>[^\s)>]*))?"
)
_THREAD_TS = re.compile(r"(?:^|&)thread_ts=(?P<ts>\d+\.\d+)")


@dataclass(frozen=True)
class Ask:
    """One unstruck line the vault is carrying, with who owes the reply."""

    text: str
    section: str
    waiting_on: str
    conversation: str = ""
    ts: str = ""
    thread_ts: str = ""
    #: The permalink as written, verbatim - it is the ask's identity across
    #: the plan and the payload, and the citation on every rendered line.
    permalink: str = ""

    @property
    def key(self) -> str:
        return self.permalink

    @property
    def checkable(self) -> bool:
        return bool(self.conversation and self.ts)


@dataclass(frozen=True)
class ReadStep:
    """One read the agent performs before an ask may be called open."""

    key: str
    conversation: str
    oldest: str
    thread_ts: str
    how: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "conversation": self.conversation,
            "oldest": self.oldest,
            "thread_ts": self.thread_ts,
            "how": self.how,
        }


@dataclass(frozen=True)
class Verdict:
    """What reading the conversation established about one ask."""

    ask: Ask
    status: str  # answered | open | unchecked
    reason: str = ""
    quote: str = ""
    permalink: str = ""
    by: str = ""


# ---------------------------------------------------------------------------
# reading the vault
# ---------------------------------------------------------------------------


def slack_permalink(text: str) -> tuple[str, str, str, str]:
    """``(conversation, ts, thread_ts, permalink)`` for the first Slack link in
    ``text``, or four empty strings.

    The FIRST one: a chase block's own permalink sits on the head line or its
    first sub-bullet, and a later sub-bullet may cite the meeting notes or a
    related thread. Reading the wrong conversation would report "open" against
    a channel the reply was never going to land in.
    """
    found = _PERMALINK.search(text)
    if not found:
        return "", "", "", ""
    digits = found["digits"]
    ts = f"{digits[:10]}.{digits[10:]}"
    thread = _THREAD_TS.search(found["query"] or "")
    return found["conversation"], ts, thread["ts"] if thread else "", found.group(0)


def asks_in(state_text: str, decisions_text: str = "") -> list[Ask]:
    """Every unstruck ask in ``State.md`` and every open pending decision.

    `StateDoc.blocks_in` is the reader: it walks nested headings (the promises
    sit under `### promises you made` inside `## Owed by you`) and `Block.link`
    finds the permalink in the sub-bullet where his citation lives.
    """
    asks: list[Ask] = []
    doc = StateDoc.parse(state_text)
    for section, waiting_on in SECTIONS.items():
        for block in doc.blocks_in(section):
            if block.struck or not block.head.strip():
                continue
            asks.append(_ask(block.body, section, waiting_on, "\n".join(block.lines)))
    for block in StateDoc.parse(decisions_text).blocks_in("Pending decisions"):
        if block.struck or "status: answered" in block.head.casefold():
            continue
        asks.append(_ask(block.body, "Pending decisions", "principal", "\n".join(block.lines)))
    return asks


def _ask(text: str, section: str, waiting_on: str, source: str) -> Ask:
    conversation, ts, thread_ts, link = slack_permalink(source)
    return Ask(
        text=text,
        section=section,
        waiting_on=waiting_on,
        conversation=conversation,
        ts=ts,
        thread_ts=thread_ts,
        permalink=link,
    )


# ---------------------------------------------------------------------------
# planning the reads
# ---------------------------------------------------------------------------


def closure_steps(asks: Iterable[Ask]) -> list[ReadStep]:
    """One read per checkable ask: the conversation after it, and its thread.

    The thread is not optional. A top-level read misses every reply, and a
    reply is exactly where an answer lands - all three of the 2026-09-18
    misses were replies.
    """
    steps: list[ReadStep] = []
    seen: set[str] = set()
    for ask in asks:
        if not ask.checkable or ask.key in seen:
            continue
        seen.add(ask.key)
        thread = ask.thread_ts or ask.ts
        steps.append(
            ReadStep(
                key=ask.key,
                conversation=ask.conversation,
                oldest=ask.ts,
                thread_ts=thread,
                how=(
                    f"read {ask.conversation} from ts {ask.ts} onward, AND read the thread"
                    f" under {thread} - a top-level read misses every reply. Pass every"
                    " message you read (user, ts, text, permalink) under this key;"
                    " pass [] if there were none. Leave the key out if you could not read."
                ),
            )
        )
    return steps


# ---------------------------------------------------------------------------
# judging what was read
# ---------------------------------------------------------------------------


def _first(record: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _after(ts: str, since: str) -> bool:
    try:
        return float(ts) > float(since)
    except ValueError:
        return False


def _responder(ask: Ask, user: str, *, principal: str, owners: Mapping[str, str]) -> bool:
    if ask.waiting_on == "principal":
        return user == principal
    owner_id = owners.get(ask.text.split("·")[0].strip().strip("*").casefold(), "")
    if owner_id:
        return user == owner_id
    return bool(user) and user != principal


def judge(
    asks: Iterable[Ask],
    fetched: Mapping[str, Any] | None,
    *,
    principal: str,
    owners: Mapping[str, str] | None = None,
) -> list[Verdict]:
    """A verdict per ask, from what was read.

    ``fetched`` maps an ask's key to the messages read for it. A key that is
    absent - or a ``fetched`` that is ``None`` because the agent never ran the
    closure reads at all - leaves that ask ``unchecked``. Nothing here infers
    absence from silence (contract 1).

    ``owners`` maps a casefolded owner token to a Slack user id, for chase
    items whose owner the caller can resolve; without it any non-principal
    reply counts as the owner's.
    """
    owners = {k.casefold(): v for k, v in (owners or {}).items()}
    verdicts: list[Verdict] = []
    for ask in asks:
        if not ask.checkable:
            verdicts.append(Verdict(ask, "unchecked", reason="no slack permalink to read from"))
            continue
        messages = None if fetched is None else fetched.get(ask.key)
        if messages is None:
            verdicts.append(Verdict(ask, "unchecked", reason="conversation not read this run"))
            continue
        closing = None
        for record in messages:
            if not isinstance(record, Mapping):
                continue
            user, ts = _first(record, _USER_KEYS), _first(record, _TS_KEYS)
            if _after(ts, ask.ts) and _responder(ask, user, principal=principal, owners=owners):
                if closing is None or _after(ts, _first(closing, _TS_KEYS)):
                    closing = record
        if closing is None:
            who = "you" if ask.waiting_on == "principal" else "them"
            verdicts.append(Verdict(ask, "open", reason=f"read the thread - nothing from {who}"))
            continue
        verdicts.append(
            Verdict(
                ask,
                "answered",
                quote=str(closing.get("text") or ""),
                permalink=str(closing.get("permalink") or ask.permalink),
                by=_first(closing, _USER_KEYS),
            )
        )
    return verdicts


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render_closure(verdicts: Iterable[Verdict], *, section: str) -> list[push.Section]:
    """The three buckets for one State.md section, evidence on every line.

    Empty buckets do not render (`render_push` drops them), so a section where
    everything was read and nothing was answered is one bucket, and a run that
    never did the reads is one bucket that says so.
    """
    chosen = [v for v in verdicts if v.ask.section == section]
    answered = [
        push.claim(f"{v.ask.text} - answered, strike?", v.permalink, quote=v.quote or None)
        for v in chosen
        if v.status == "answered"
    ]
    open_ = [
        push.claim(f"{v.ask.text} - {v.reason}", v.ask.permalink)
        for v in chosen
        if v.status == "open"
    ]
    unchecked = [
        push.claim(f"{v.ask.text} - unchecked: {v.reason}", v.ask.permalink or None)
        for v in chosen
        if v.status == "unchecked"
    ]
    label = section.casefold()
    return [
        push.Section(f"{label} - open ({len(open_)})", tuple(open_)),
        push.Section(f"{label} - answered, not yet struck ({len(answered)})", tuple(answered)),
        push.Section(f"{label} - couldn't verify ({len(unchecked)})", tuple(unchecked)),
    ]


@dataclass(frozen=True)
class Closure:
    """Everything one chase run established, for the runner to render."""

    verdicts: tuple[Verdict, ...] = field(default_factory=tuple)

    @property
    def open_count(self) -> int:
        return sum(1 for v in self.verdicts if v.status == "open")

    @property
    def read_nothing(self) -> bool:
        """True when not one ask was actually read - the run skipped the step."""
        return bool(self.verdicts) and all(v.status == "unchecked" for v in self.verdicts)
