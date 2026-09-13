"""Who someone is, and how confident the system is that it knows.

USING IT
    directory = People(EventLog.open(path))
    directory.remember("vp-data", email=..., slack_id=..., source=STATED)
    directory.observe(row, principal=me)     # learn from a calendar Row
    directory.resolve("wren.alder@example.com")   # -> Person | None
    directory.resolve("U0AWREN") / resolve("wren alder")
    directory.leadership()                   # -> tuple[Person, ...]
    directory.all()

CONTRACTS
    1. PRECEDENCE, and it is the whole point: STATED > PROFILE > OBSERVED. What
       he told it is never overwritten by what a run inferred. Same source, the
       newer wins, or a correction could not itself be corrected.
    2. Facts ACCUMULATE. An update carrying only a slack id does not clear a
       title, and groups add rather than replace.
    3. `resolve` returns None for unknown. Unknown is a state a caller must
       handle, never a benign default - see WHY IT EXISTS.
    4. `observe` records co-attendance and nothing else. A meeting is evidence
       he was in a room with someone; it is not evidence of their job.
    5. Nothing here touches the vault or the repo. Slack ids, addresses and DM
       channels are what CONTRIBUTING keeps out of a public repo and CLAUDE.md
       keeps out of a synced one.

WHY IT EXISTS
    The system knew almost nothing about people: two config values,
    `PREP_LEADERSHIP` and `ORG_EMAIL_DOMAIN`, both unset, so `has_leadership`
    and `has_external` always answered False and two of prep's four reasons
    could never fire. Measured on real meetings - Luminate, YouTube, the label
    summit - every outside-party meeting got no prep at all, which is exactly
    where walking in cold costs most.

    The roster that did exist was PROSE in CLAUDE.md as `${SLACK_USER_*}`
    references. An agent reads that; no code does. It covered about fifteen
    people against the forty a single week's calendar holds, and it could not
    grow, because every addition was a commit to a public repo.

    So the knowledge moves to a store that is outside the repo, learns from the
    meetings that are already being read, and can be corrected in place.

KNOWN LIMIT
    Identity is emails, ids and name tokens - not a person. Two addresses are
    the same human only when something has said so, by carrying the same key.
    Merging on a matching display name would eventually merge two people who
    share one, and this store is read by things that decide whether to message
    someone.

    `observe` cannot tell a colleague from a candidate or a vendor. It records
    that a meeting happened. Whether that person is external is
    `Person.external`, which is derived from the address domain and is only as
    good as `ORG_EMAIL_DOMAIN`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from daydag.state import EventLog

__all__ = [
    "OBSERVED",
    "PROFILE",
    "STATED",
    "People",
    "Person",
]

#: Where a fact came from, lowest trust first. `remember` refuses to lower the
#: trust on a field: a run that infers something must not quietly replace what
#: he typed, which is CLAUDE.md's "a hand edit is an event and it wins".
OBSERVED = "observed"
PROFILE = "profile"
STATED = "stated"

_TRUST = {OBSERVED: 1, PROFILE: 2, STATED: 3}

#: The event kind every fact is appended under.
FACT = "person_fact"

#: Google books conference rooms as attendees on this domain. A room in the
#: directory would be "met" more often than any human.
_RESOURCE_DOMAIN = "resource.calendar.google.com"

_TOKENS = re.compile(r"[^a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return {part for part in _TOKENS.split(str(text).casefold()) if part}


def _name_tokens(handle: str) -> set[str]:
    """Name tokens in an address or display name, domain discarded.

    Same rule as `prep_selector._person_tokens`, and for the same reason: every
    colleague shares the domain, so matching it matches everybody.
    """
    return _tokens(str(handle).split("@", 1)[0])


@dataclass(frozen=True)
class Person:
    """One human, and the best-sourced value known for each field."""

    key: str
    emails: tuple[str, ...] = ()
    slack_id: str | None = None
    display_name: str | None = None
    title: str | None = None
    #: The DM channel between this person and the principal.
    dm: str | None = None
    #: Channels and group DMs they are tracked in.
    groups: tuple[str, ...] = ()
    leadership: bool = False
    #: Learned by `observe`, never stated.
    met: int = 0
    first_met: date | None = None
    last_met: date | None = None
    #: Per-field provenance, so precedence survives a reload.
    sources: Mapping[str, str] = field(default_factory=dict)

    @property
    def primary_email(self) -> str | None:
        """The address to address them at - the first one learned."""
        return self.emails[0] if self.emails else None

    def external(self, internal_domains: Iterable[str]) -> bool | None:
        """Whether they are outside the org, or None when that is unknowable.

        Three-valued on purpose. `False` means "checked, internal"; `None`
        means "no domain configured, or no address" - and the whole reason this
        module exists is that those two were the same answer before.
        """
        domains = {d.strip().casefold() for d in internal_domains if d.strip()}
        if not domains or not self.emails:
            return None
        return any(
            (domain := address.partition("@")[2].casefold()) and domain not in domains
            for address in self.emails
        )


def _is_resource(address: str) -> bool:
    return _RESOURCE_DOMAIN in str(address).casefold()


class People:
    """The directory, folded from the event log on construction."""

    def __init__(self, events: EventLog) -> None:
        self._events = events
        self._by_key: dict[str, Person] = {}
        self._seen_events: set[str] = set()
        for row in events.recorded(FACT):
            if isinstance(row, Mapping):
                self._apply(row)

    # -- writing -----------------------------------------------------------

    def remember(
        self,
        key: str,
        *,
        source: str = STATED,
        email: str | None = None,
        slack_id: str | None = None,
        display_name: str | None = None,
        title: str | None = None,
        dm: str | None = None,
        groups: Sequence[str] = (),
        leadership: bool | None = None,
    ) -> Person:
        """Record what is known about ``key``, at ``source``'s level of trust."""
        fact: dict[str, Any] = {"key": key, "source": source}
        for name, value in (
            ("email", email),
            ("slack_id", slack_id),
            ("display_name", display_name),
            ("title", title),
            ("dm", dm),
            ("leadership", leadership),
        ):
            if value is not None:
                fact[name] = value
        if groups:
            fact["groups"] = list(groups)
        self._events.record(FACT, sensitivity="private", **fact)
        return self._apply(fact)

    def observe(self, row: Any, *, principal: str = "") -> None:
        """Learn from a meeting: who was in it, and that it happened.

        Contract 4 - co-attendance only. The meeting is not evidence of anyone's
        job, so nothing here writes a title, and `OBSERVED` keeps whatever it
        does write below anything he states later.
        """
        event_id = str(getattr(row, "event_id", "") or "")
        day = getattr(getattr(row, "start", None), "date", lambda: None)()
        for attendee in getattr(row, "attendees", ()) or ():
            address = str(attendee).strip()
            if not address or _is_resource(address):
                continue  # a room is not a person
            if principal and address.casefold() == principal.casefold():
                continue  # he is in every meeting
            handle = _address_of(address)
            existing = self._lookup(handle)
            key = existing.key if existing else _slug(handle)
            marker = f"{key}@{event_id}"
            fact: dict[str, Any] = {
                "key": key,
                "source": OBSERVED,
                "email": handle,
                "met_on": day.isoformat() if day else None,
                "event_id": event_id,
            }
            name = _display_of(address)
            if name:
                fact["display_name"] = name
            if marker in self._seen_events:
                continue  # contract: the same meeting twice counts once
            self._events.record(FACT, sensitivity="private", **fact)
            self._apply(fact)

    # -- reading -----------------------------------------------------------

    def resolve(self, handle: str) -> Person | None:
        """The person this address, id or name refers to, or None."""
        return self._lookup(str(handle).strip())

    def all(self) -> tuple[Person, ...]:
        return tuple(self._by_key.values())

    def leadership(self) -> tuple[Person, ...]:
        return tuple(p for p in self._by_key.values() if p.leadership)

    # -- the fold ----------------------------------------------------------

    def _lookup(self, handle: str) -> Person | None:
        if not handle:
            return None
        lowered = handle.casefold()
        for person in self._by_key.values():
            if any(address.casefold() == lowered for address in person.emails):
                return person
            if person.slack_id and person.slack_id.casefold() == lowered:
                return person
        wanted = _name_tokens(handle)
        if not wanted:
            return None
        for person in self._by_key.values():
            known = set()
            for address in person.emails:
                known |= _name_tokens(address)
            if person.display_name:
                known |= _tokens(person.display_name)
            if wanted <= known:
                return person
        return None

    def _apply(self, fact: Mapping[str, Any]) -> Person:
        """Fold one recorded fact in, honouring precedence (contract 1)."""
        key = str(fact.get("key", "")) or "unknown"
        source = str(fact.get("source", OBSERVED))
        person = self._by_key.get(key) or Person(key=key)
        sources = dict(person.sources)
        changes: dict[str, Any] = {}

        for name in ("slack_id", "display_name", "title", "dm", "leadership"):
            if name not in fact or fact[name] is None:
                continue
            if _TRUST.get(source, 0) < _TRUST.get(sources.get(name, OBSERVED), 0):
                continue  # a lower-trust source never overwrites a higher one
            changes[name] = fact[name]
            sources[name] = source

        email = fact.get("email")
        if email and str(email).casefold() not in {e.casefold() for e in person.emails}:
            changes["emails"] = (*person.emails, str(email))

        groups = fact.get("groups") or ()
        if groups:
            merged = list(person.groups)
            merged.extend(g for g in groups if g not in merged)
            changes["groups"] = tuple(merged)

        met_on = fact.get("met_on")
        event_id = str(fact.get("event_id", "") or "")
        marker = f"{key}@{event_id}"
        if met_on and marker not in self._seen_events:
            self._seen_events.add(marker)
            when = date.fromisoformat(str(met_on))
            changes["met"] = person.met + 1
            changes["first_met"] = min(person.first_met or when, when)
            changes["last_met"] = max(person.last_met or when, when)

        updated = replace(person, sources=sources, **changes)
        self._by_key[key] = updated
        return updated


def _address_of(attendee: str) -> str:
    """The bare address out of `"Full Name <addr>"`, or the value unchanged."""
    if "<" in attendee and ">" in attendee:
        return attendee.split("<", 1)[1].split(">", 1)[0].strip()
    return attendee.strip()


def _display_of(attendee: str) -> str | None:
    """The display name out of `"Full Name <addr>"`, if there is one."""
    if "<" in attendee:
        name = attendee.split("<", 1)[0].strip()
        return name or None
    return None


def _slug(address: str) -> str:
    """A stable key for someone nobody has named yet."""
    return "-".join(sorted(_name_tokens(address))) or address.casefold()


def main(argv: list[str] | None = None) -> int:
    """`add` / `set` write a stated fact; `show` and `list` read.

    Hand entry is a first-class path, not a convenience. Most of what this
    store holds will be learned from meetings at `OBSERVED`, and the only way
    that stays trustworthy is if a correction is easy enough to actually make -
    it lands at `STATED` and outranks anything a later run infers.
    """
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    usage = (
        "usage: python -m daydag.people {add|set|show|list} [key] --log PATH\n"
        "       [--email A] [--slack-id U] [--name N] [--title T]\n"
        "       [--dm D] [--group C] [--leadership]"
    )
    if not args or args[0] not in {"add", "set", "show", "list"} or "--log" not in args:
        print(usage)
        return 2

    def opt(name: str) -> str | None:
        return args[args.index(name) + 1] if name in args[:-1] else None

    directory = People(EventLog.open(str(opt("--log"))))
    command = args[0]

    if command == "list":
        for person in sorted(directory.all(), key=lambda p: p.key):
            met = f"met {person.met}x" if person.met else "not met yet"
            print(
                f"  {person.key:22} {person.primary_email or '-':34} {person.title or '-':28} {met}"
            )
        return 0

    if command == "show":
        person = directory.resolve(args[1]) if len(args) > 1 else None
        if person is None:
            print(f"not in the directory: {args[1] if len(args) > 1 else ''}")
            return 1
        for name in (
            "key",
            "emails",
            "slack_id",
            "display_name",
            "title",
            "dm",
            "groups",
            "leadership",
            "met",
            "first_met",
            "last_met",
        ):
            print(f"  {name:14} {getattr(person, name)}")
        print(f"  {'sources':14} {dict(person.sources)}")
        return 0

    if len(args) < 2 or args[1].startswith("--"):
        print(usage)
        return 2
    groups = [args[i + 1] for i, a in enumerate(args) if a == "--group" and i + 1 < len(args)]
    directory.remember(
        args[1],
        source=STATED,
        email=opt("--email"),
        slack_id=opt("--slack-id"),
        display_name=opt("--name"),
        title=opt("--title"),
        dm=opt("--dm"),
        groups=groups,
        leadership=True if "--leadership" in args else None,
    )
    print(f"  remembered {args[1]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
