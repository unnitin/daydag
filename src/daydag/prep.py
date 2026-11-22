"""Meeting prep: which meeting, why it qualifies, and what to raise (SPEC 3.2, 3.8, #20).

USING IT
    audience = Audience.from_directory(people, ids)   # or Audience.from_identities(ids)
    reason = prep_worthy(row, audience)     # -> Reason | None; None means skip
    found = select(rows, "finance x data", now=now, until=until, principal=me, tz=tz)
    found.one                               # -> Row | None, only when exactly one matched
    found.candidates                        # -> every match, in time order
    found.render()                          # -> the line to show when `one` is None
    point("what", "why now", quote=q, permalink=link, source="slack")
    build(row, reason, points).render()     # -> the prep text, headline first
    sources(row, now, identities=ids, channels=chans, terms=words)   # -> SourcePlan

CONTRACTS
    1. Qualification is the LEDGER's, narrowed - never re-derived. `Ledger`
       already decides what is a meeting, and that rule was expensive: the #2
       audit found requiring an `accepted` response dropped 61% of real
       meetings silently, because ~70% of invites are never answered. This adds
       exactly one rule on top - 3.2's "skip standups". Response status, kind
       and attendee count are not re-examined, so nothing can drift.
    2. The inclusive/exclusive bias INVERTS across that seam, on purpose. The
       ledger errs wide: a false positive there costs one "no notes" line,
       a false negative is a meeting the system cannot see. `prep_worthy` errs
       narrow, because it decides what is prepped UNPROMPTED, and a prep nobody
       asked for on a standup is how a push gets muted. A meeting he NAMES
       skips the gate (`Reason.ASKED_FOR`): he asked.
    3. Verbatim quotes are EVIDENCE, not voice. House rule 1 says quote
       verbatim with a permalink; SPEC 5 bans em dashes. Someone else's Slack
       line may contain one, and rewriting it to pass a style check is exactly
       the paraphrase-from-memory the quoting rule exists to prevent. So
       `Point.render` prints the quote as it was written, on a line of its own
       beside its link, and the guardrail in `tests/test_prep.py` runs the
       voice check over every other line.
    4. No I/O and NO SEND PATH. This builds the prep text and the source
       queries. Nothing here fetches or posts.
    5. An unset `ORG_EMAIL_DOMAIN` means nobody is declared external, not
       everybody. Defaulting the other way qualifies every meeting on the
       calendar for a reason that is not true of it.
    6. `Selection.one` is set ONLY when exactly one meeting matched. Two
       matches is not a tie to break, it is a question to ask - see WHY IT
       EXISTS.
    7. A selector matches by TOKENS - against the title, or against an
       attendee's address local-part and display name - and every token must
       be present. One token is enough on its own; "bo finch" needs both. No
       substring path: "fin" does not find Finance, because partial-word
       matching is the fuzziness KNOWN LIMIT argues against.
    8. The principal never matches a selector. He is on every meeting.
    9. `select` returns only meetings that have not started, and only before
       ``until`` - the bound the caller computed from the same day arithmetic
       as its fetch.
    10. A selector with no word in it raises rather than matching everything.
       The guard is on the TOKENS, because an empty token set is a subset of
       every title - "---" passed a guard on the string and matched the week.

WHY IT EXISTS
    Two questions about one calendar. "What is next that is worth prepping?"
    is `prep_worthy`: `run`'s `prep` loop takes the next row it accepts.
    *"Prep me for the Finance call"* is `select` - SPEC 3.8 lists it, and the
    runner had no selector, so it had nowhere to go. Either way `build` turns
    the row and its candidate points into the text `run` prints: up to five
    talking points (`MAX_POINTS`), each with a reason to raise it today.
    `sources` is the `weekly-planning` skill's File 2 recipe - the last four
    weeks of that meeting's notes and the Slack around them - as queries for
    one meeting instead of a Friday's worth.

    The hard part of naming a meeting is not the matching, it is the
    ambiguity, which is the common case rather than the edge: a 1:1 that runs
    twice a week appears twice in any horizon worth searching, and a person is
    in several meetings. Prep for the WRONG meeting is worse than no prep at
    all - he reads it, trusts it, and walks into the other one cold. So two
    matches surface as two (invariant 5).

    SPEC 3.2 designed the first question as a ping thirty minutes before the
    meeting, the one push allowed to arrive off-schedule. Nothing was ever
    built to fire it, so the parts only a poller needs retired on 2026-11-21
    and are at tag `pre-retirement`: the due window and its once-per-instance
    schedule, the interrupt gate, the DM recipient, and `delivery`'s threaded
    reply. A tripwire in `tests/test_guardrails.py` fails when something that
    could fire a push on its own clock lands, and names the gate to restore.

KNOWN LIMIT
    `LOOKBACK_DAYS` is 28 and fixed. A meeting that recurs monthly gets one
    prior instance to draw on, which is thin; a weekly gets four.

    The headline is `voice`'s prep template: "<meeting> in 30 - talking points
    in thread", whatever the real lead is, and `PrepPing.render` prints the
    points directly beneath it rather than in a thread. A prep he asked for on
    Monday for a Thursday meeting says "in 30" too.

    `sources` has one caller, the week-ahead's Monday queue, and nothing reads
    the plan that queue builds. `run` does not call it: a prep's points come
    from the overnight Slack payload (`run._points`), because the two-phase
    plan cannot know the row before the fetch.

    Selector matching is tokens, not identity. It does not know that "Wren"
    and `wren.alder@` are the same person as "Wren Alder" in a title - only
    that the token `wren` appears in each.

    So the display name matters. Plenty of real addresses are a bare first
    name (`jonathan@`, `alex@`), and the surname exists only in google's
    `displayName` - shaped as the address alone, "jonathan strauss" cannot
    match and bare "jonathan" matches a different Jonathan. The name reaches
    here as `Row.attendee_names`, split from the address once in
    `Ledger.seed_day` (`ledger.attendee_parts`). It is deliberately NOT folded
    into the address string: that was tried, and every other consumer of
    `Row.attendees` compares bare emails, so it broke `has_external`,
    `has_leadership`, note matching and the selector's own principal skip at
    once.

    A genuine nickname sharing no token with either ("Bill" against
    `william.smith@` with no display name) still will not match. The honest fix
    is an alias in `.env`, not fuzzier matching here - fuzzier matching buys
    back the wrong-meeting failure `select` exists to prevent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from enum import Enum
from typing import Any

from daydag.ledger import Row
from daydag.recipes import (
    PACIFIC,
    RecipeError,
    gmail_gemini_notes,
    meeting_prep,
    slack_search,
)
from daydag.voice import Push, render

__all__ = [
    "HORIZON_DAYS",
    "LOOKBACK_DAYS",
    "MAX_POINTS",
    "NO_MATERIAL",
    "UNSOURCED",
    "Audience",
    "Point",
    "PrepError",
    "PrepPing",
    "Reason",
    "Selection",
    "SourcePlan",
    "build",
    "point",
    "prep_worthy",
    "select",
    "sources",
]


class PrepError(RecipeError):
    """A prep was asked for something it must not invent.

    Subclasses :class:`~daydag.recipes.RecipeError` on purpose: prep's whole job
    is to turn a meeting into source queries, so a caller that already catches
    the recipe refusal catches this too rather than needing to know the seam is
    there.
    """


#: "the last ~4 weeks" of that meeting's notes, as days.
LOOKBACK_DAYS = 28

#: Section 3.2 says 3-5. Only the ceiling is enforced - see :func:`build`.
MAX_POINTS = 5

#: How far ahead a named prep will look. A week, because that is the span the
#: evidence a prep is built from actually covers - two weeks out, the Slack and
#: mail it would quote has not been written yet.
HORIZON_DAYS = 7

#: What an unsourced point says instead of looking sourced. Invariant 3 is
#: "evidence or silence"; this is the third option the spec actually wants -
#: say out loud that the evidence is missing.
UNSOURCED = "couldn't source this one - no quote i can point at"

#: The honest degrade when four weeks turn up nothing. Guardrail 6, at the one
#: moment it is most tempting to write something plausible instead.
NO_MATERIAL = "nothing in the last 4 weeks i could pull from - going in cold. keep me honest"

#: Config keys. Written bare rather than as ``${VAR}`` references because a
#: reference in shipped source is checked against a real `.env`; these are the
#: names of keys, not uses of them. Both are documented in `.env.example`.
ORG_DOMAIN_KEY = "ORG_EMAIL_DOMAIN"
LEADERSHIP_KEY = "PREP_LEADERSHIP"


class Reason(Enum):
    """Why a meeting is being prepped. Recorded, not just computed.

    A prep that cannot say why it was built is unauditable, and the first
    question about one that should not have been is which rule let it through.
    """

    ONE_ON_ONE = "1:1"
    STEERING = "steering/pod"
    LEADERSHIP = "leadership in the room"
    EXTERNAL = "external party"
    #: He named this meeting. The others are rules deciding whether to prep
    #: unprompted; this one is not a rule at all, and recording it as its own
    #: reason keeps that distinction auditable. A prep that says "1:1" when it
    #: was actually built because he asked would make the rules look better
    #: than they are.
    ASKED_FOR = "you asked"


#: Prep's single extra rule. Focus blocks, holds and OOO are already gone via
#: `ledger.NON_MEETING_KINDS`, and a solo block cannot clear the ledger's
#: two-attendee floor, so standups are all that is left to exclude.
_STANDUP = re.compile(r"\b(stand\s*-?\s*ups?|scrums?)\b", re.IGNORECASE)

#: Two first names and a slash is how half of his 1:1s are actually titled, so
#: the count matters as much as the words - hence both paths in `prep_worthy`.
_ONE_ON_ONE = re.compile(r"(?:\b|^)(?:1\s*[:/-]\s*1|one[\s-]?on[\s-]?one|o3)\b", re.IGNORECASE)

#: Deliberately not including "review" or "sync": both appear on meetings that
#: qualify for a different reason, and a wrong `Reason` is a wrong audit trail.
_STEERING = re.compile(r"\b(steering|pod|leads\s+sync|staff\s+meeting)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Audience:
    """Who counts as leadership, and what counts as inside the org.

    Both are configuration rather than constants: the repo is public, and a
    hardcoded name or domain is the identifier CONTRIBUTING keeps in `.env`.
    """

    leadership: frozenset[str] = frozenset()
    internal_domains: frozenset[str] = frozenset()

    @classmethod
    def from_identities(cls, identities: Mapping[str, str]) -> Audience:
        """Read ``PREP_LEADERSHIP`` and ``ORG_EMAIL_DOMAIN``, both optional.

        Unset means *unknown*, and unknown resolves to "no one qualifies on
        this rule". The alternative default - treating every attendee as an
        outside party when the org domain is unset - qualifies every meeting
        on the calendar as external.

        Both were in fact unset, so `has_leadership` and `has_external` always
        answered False and two of the four reasons could never fire. Prefer
        `from_directory`, which takes leadership from the people store, where a
        person's seniority sits next to the rest of what is known about them
        and can be corrected without editing a CSV in `.env`.
        """
        return cls(
            leadership=_csv(identities, LEADERSHIP_KEY),
            internal_domains=_csv(identities, ORG_DOMAIN_KEY),
        )

    @classmethod
    def from_directory(cls, directory: Any, identities: Mapping[str, str]) -> Audience:
        """Leadership from the people store, domains still from `.env`.

        Split on purpose. "Who is senior" is a fact ABOUT A PERSON and belongs
        where the person is, learned and corrected over time. "Which domain is
        ours" is one value that decides inside-vs-outside for everybody, has no
        person attached, and is needed before any lookup can be trusted - so it
        stays configuration.

        UNIONED with the `PREP_LEADERSHIP` CSV, not either/or. The first version
        used the CSV only while the store was empty, so the first name added to
        the store silently dropped every configured leader - the CEO in .env
        stopped qualifying the moment the CTO was entered. And `brief` /
        `week_ahead` still read the CSV (#119), so it has to keep working.

        Internal domains: `ORG_EMAIL_DOMAIN`, or - when that is unset - the
        domain of `EMAIL_PRINCIPAL`. He works for the org; his address is its
        domain. Without this the external-party rule stayed dead for anyone who
        never set the org key, which was everyone.
        """
        from_store = frozenset(
            address.casefold() for person in directory.leadership() for address in person.emails
        )
        domains = _csv(identities, ORG_DOMAIN_KEY)
        if not domains:
            principal = str(identities.get("EMAIL_PRINCIPAL", "") or "")
            _, _, domain = principal.casefold().partition("@")
            domains = frozenset({domain}) if domain else frozenset()
        return cls(
            leadership=from_store | _csv(identities, LEADERSHIP_KEY), internal_domains=domains
        )

    def has_leadership(self, attendees: Iterable[str]) -> bool:
        return bool(self.leadership & {a.strip().casefold() for a in attendees})

    def has_external(self, attendees: Iterable[str]) -> bool:
        """Whether anyone is demonstrably from outside the org.

        An attendee with no ``@`` is not evidence of anything - some calendars
        hand back display names - so it is read as internal. Guessing the other
        way manufactures an `EXTERNAL` reason out of a formatting quirk.
        """
        if not self.internal_domains:
            return False
        for attendee in attendees:
            _, _, domain = attendee.strip().casefold().partition("@")
            if domain and domain not in self.internal_domains:
                return True
        return False


def _csv(identities: Mapping[str, str], key: str) -> frozenset[str]:
    if key not in identities:
        return frozenset()
    raw = str(identities[key])
    return frozenset(part.strip().casefold() for part in raw.split(",") if part.strip())


def prep_worthy(row: Row, audience: Audience) -> Reason | None:
    """Why this ledger row is worth prepping unprompted, or ``None``.

    Narrowing only. The row's existence already means the ledger accepted it as
    a meeting; this decides whether section 3.2 wants it prepped.
    """
    if _STANDUP.search(row.summary or ""):
        # A veto, not a tiebreak. Section 3.2 lists the skips flat, so a standup
        # with the sponsor in it is still a standup.
        return None
    if audience.has_external(row.attendees):
        return Reason.EXTERNAL
    if audience.has_leadership(row.attendees):
        return Reason.LEADERSHIP
    if _STEERING.search(row.summary or ""):
        return Reason.STEERING
    if _ONE_ON_ONE.search(row.summary or "") or len(row.attendees) == 2:
        return Reason.ONE_ON_ONE
    return None


# ---------------------------------------------------------------------------
# naming the meeting: the one he asked for, or a question when it is two
# ---------------------------------------------------------------------------

_TOKENS = re.compile(r"[^a-z0-9]+")


def _tokens(text: str) -> set[str]:
    """The words in a string, however it was punctuated."""
    return {part for part in _TOKENS.split(str(text).casefold()) if part}


def _person_tokens(address: str, name: str) -> set[str]:
    """The name tokens for one attendee: address local-part plus display name.

    `wren.alder@example.com` is `{wren, alder}`, so a first name finds the
    person without anyone storing a display name - and `example`/`com` are NOT
    matchable, because every colleague shares them and a selector that hit the
    domain would match the entire invite list. The display name adds the
    surname a bare-first-name address lacks (`jonathan@` + "Jonathan Strauss").
    """
    return _tokens(address.split("@", 1)[0]) | _tokens(name)


def _clock(moment: datetime, tz: tzinfo) -> str:
    """``mon 14 sep 9:00`` - his zone, his register, same as the brief.

    A row's start carries whatever offset the connector emitted; rendered raw,
    a 13:00 PT meeting delivered as ``20:00Z`` listed as 20:00 in the which-one
    prompt while the morning brief showed 1:00 for the same meeting. A naive
    start is read as already his wall-clock, the `brief._local` convention.
    """
    local = (moment if moment.tzinfo else moment.replace(tzinfo=tz)).astimezone(tz)
    return f"{local:%a %d %b}".lower() + f" {local.hour % 12 or 12}:{local.minute:02d}"


@dataclass(frozen=True)
class Selection:
    """What a selector matched, and what to say when that is not one thing."""

    candidates: tuple[Row, ...]
    selector: str
    tz: tzinfo = PACIFIC

    @property
    def one(self) -> Row | None:
        """The single match, or None when there are none or several."""
        return self.candidates[0] if len(self.candidates) == 1 else None

    def render(self) -> str:
        """The line to show when `one` is None - never a silent empty."""
        if not self.candidates:
            return (
                f"prep: nothing in the next {HORIZON_DAYS} days matches"
                f' "{self.selector}" - try a title word, or a name as it appears'
                " on the invite"
            )
        lines = [f'prep: "{self.selector}" matches {len(self.candidates)} meetings - which one?']
        lines.extend(f"  {_clock(row.start, self.tz)}  {row.summary}" for row in self.candidates)
        return "\n".join(lines)


def _matches(row: Row, wanted: set[str], principal: str) -> bool:
    """Whether one meeting answers to this selector.

    Title first, because "finance x data" is how he refers to the meeting and
    is not anybody's name. Then attendees, where EVERY token has to land: one
    token is enough to name a person, but a two-token selector that matched on
    either half would pick the wrong Bo. Tokens only - a substring path let
    "fin" find Finance, which is the fuzziness `select` refuses.
    """
    if wanted <= _tokens(row.summary):
        return True
    names = list(row.attendee_names) + [""] * (len(row.attendees) - len(row.attendee_names))
    for address, name in zip(row.attendees, names, strict=False):
        # Contract 8, comparing like with like: `Row.attendees` are bare EMAILS
        # (split from any display name in `Ledger.seed_day`), so the principal
        # has to arrive as one. A Slack id here matches nothing and silently
        # disables the skip - the first wiring did exactly that, and the tests
        # passed because their fixture principal happened to be email-shaped.
        if principal and address.casefold() == principal.casefold():
            continue
        if wanted <= _person_tokens(address, name):
            return True
    return False


def select(
    rows: Iterable[Row],
    selector: str,
    *,
    now: datetime,
    until: datetime,
    principal: str = "",
    tz: tzinfo = PACIFIC,
) -> Selection:
    """Every meeting in ``[now, until)`` answering to ``selector``, in time order.

    ``until`` is the caller's, not derived here: the plan fetched a specific
    set of day windows and the match bound has to be the END of the last one,
    or the two halves of one loop disagree about what "the next 7 days" means.
    A `now + 7 days` instant here fetched an eighth day it then discarded.

    Raises `ValueError` when the selector has no word in it. The guard is on
    the TOKENS: an empty token set is a subset of every title's, so "---" or a
    stray quote passed a check on the string and matched the whole week.
    """
    wanted = _tokens(selector)
    if not wanted:
        raise ValueError("a prep selector needs a word in it - a title word, or a name")
    phrase = " ".join(str(selector).split())

    found = [row for row in rows if now <= row.start < until and _matches(row, wanted, principal)]
    return Selection(tuple(sorted(found, key=lambda r: r.start)), phrase, tz)


# ---------------------------------------------------------------------------
# sources: the File 2 recipe, as queries for one meeting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourcePlan:
    """The queries to run for one meeting, and what could not be built."""

    window: tuple[date, date]
    gemini: str
    slack: tuple[str, ...]
    prep_note: str
    #: Named degradations, guardrail 6. Empty means every source was asked.
    degraded: tuple[str, ...] = ()


def sources(
    row: Row,
    now: datetime,
    *,
    identities: Mapping[str, str],
    channels: Sequence[str] = (),
    terms: Sequence[str] = (),
) -> SourcePlan:
    """The last ~4 weeks of this meeting, as literal queries.

    Every query comes from :mod:`daydag.recipes` rather than being written here
    - that module exists because loops that write their own queries drift, and
    the drift only shows up as a prep that quietly omits a week.

    Raises `PrepError` for a naive ``now``, and for a row `prep_worthy` turns
    down under the `.env` audience (`Audience.from_identities`).
    """
    if now.tzinfo is None:
        raise PrepError("now must be timezone-aware; the lookback is bounded by local days")
    audience = Audience.from_identities(identities)
    if prep_worthy(row, audience) is None:
        raise PrepError(f"{row.summary!r} does not qualify for a prep ping; nothing to source")

    end = now.astimezone(PACIFIC).date()
    start = end - timedelta(days=LOOKBACK_DAYS)
    degraded: list[str] = []

    try:
        # Subject, not body: the #2 audit found Gemini's subject is rigidly
        # `Notes: "<title>" <date>`, and body matching cannot separate two
        # back-to-back 1:1s - which for a prep means prepping the wrong one.
        gemini = gmail_gemini_notes(title=row.summary, after=start, before=end)
    except RecipeError as exc:
        degraded.append(f"meeting title is not searchable as a subject ({exc}); swept by date")
        gemini = gmail_gemini_notes(after=start, before=end)

    queries: list[str] = []
    for channel in channels:
        try:
            queries.append(
                slack_search(
                    channel=channel,
                    after=start,
                    before=end,
                    terms=terms,
                    identities=identities,
                )
            )
        except RecipeError as exc:
            # One unresolvable channel must not take the prep with it. Named,
            # because a channel silently dropped is a silence nobody checked.
            degraded.append(f"{channel}: {exc}")

    return SourcePlan(
        window=(start, end),
        gemini=gemini,
        slack=tuple(queries),
        # The MEETING's week, not `now`'s. They agree for a same-day prep -
        # which is why this shipped looking right - and disagree the first
        # time `sources()` is built ahead of time, from a different week: the
        # week-ahead loop (SPEC 3.6) calls this on Sunday night for Monday's
        # meetings, and `meeting_prep(end)` there named Meeting
        # Prep/<the closing week>.md - a real file, just the wrong one.
        prep_note=meeting_prep(_local_day(row.start)),
        degraded=tuple(degraded),
    )


def _local_day(start: datetime) -> date:
    """``start``'s calendar day in the principal's zone.

    A naive ``start`` is assumed to already be his local wall-clock time - the
    same contract `brief._local` and `week_ahead._local` use for an event's
    start - never reinterpreted via the host's zone: plain `.astimezone()` on a
    naive value adopts whatever zone the *runner* has, which is exactly the
    class of bug `slack_overnight` and `_as_of` were both fixed for elsewhere in
    this codebase. `Ledger.seed_day` passes the calendar's `start` through
    untouched, so a naive one can reach here (the week-ahead calls `sources()`
    the evening before the meeting); it is read as wall-clock rather than
    raising.
    """
    return (start if start.tzinfo else start.replace(tzinfo=PACIFIC)).astimezone(PACIFIC).date()


# ---------------------------------------------------------------------------
# points
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Point:
    """One talking point: what to raise, and why today."""

    what: str
    why_now: str
    quote: str | None = None
    permalink: str | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        if not (self.what or "").strip():
            raise PrepError("a talking point needs something to say")
        if not (self.why_now or "").strip():
            raise PrepError("a talking point needs a why-now; use point() to drop it instead")
        if bool(self.quote) != bool(self.permalink):
            # Invariant 3, structural rather than advisory: a quote nobody can
            # open is a paraphrase, and a bare link is not a quote.
            raise PrepError("a quote and its permalink travel together, or neither does")

    @property
    def sourced(self) -> bool:
        return bool(self.quote and self.permalink)

    def render(self) -> str:
        """The point, then its evidence on the next line - or the admission.

        The quote and its link were written by somebody else and are copied
        verbatim (contract 3): they get a line of their own, so what the agent
        composed and what it borrowed can be told apart.
        """
        head = f"- {self.what.strip()} - {self.why_now.strip()}"
        if self.sourced:
            return f'{head}\n  "{self.quote}" {self.permalink}'
        return f"{head}\n  ({UNSOURCED})"


def point(
    what: str,
    why_now: str | None = None,
    *,
    quote: str | None = None,
    permalink: str | None = None,
    source: str | None = None,
) -> Point | None:
    """A talking point, or ``None`` when there is no reason to raise it today.

    The forgiving front door to :class:`Point`. SPEC 3.2 is explicit that "sprint
    demo" is not a talking point and "sprint demo - force the teaser decision"
    is; a candidate with no why-now is dropped here rather than padding the prep
    out to five, because a padded prep is one he skims.
    """
    if not (why_now or "").strip():
        return None
    return Point(
        what=what,
        why_now=why_now or "",
        quote=quote,
        permalink=permalink,
        source=source,
    )


# ---------------------------------------------------------------------------
# the prep
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrepPing:
    """One meeting's prep, as the text `run` prints."""

    meeting: str
    starts: datetime
    reason: Reason
    points: tuple[Point, ...] = ()
    #: Set when there were no points at all, and says so in his own words.
    gap: str | None = None

    def headline(self) -> str:
        """The first line: `voice`'s prep template, with the meeting's title."""
        return render(Push.PREP_PING, {"meeting": self.meeting})

    def render(self) -> str:
        """The headline, then every point - or the gap line when there are none."""
        if self.points:
            body = "\n".join(p.render() for p in self.points)
        else:
            body = self.gap or NO_MATERIAL
        return f"{self.headline()}\n{body}"


def build(row: Row, reason: Reason, points: Iterable[Point | None]) -> PrepPing:
    """Assemble a prep from candidate points.

    Drops the candidates with no why-now, caps at :data:`MAX_POINTS`, and does
    **not** pad up to three: section 3.2's range is a shape, not a quota. Two
    points that both earn their place beat five where three are filler.
    """
    kept = tuple(p for p in points if p is not None)[:MAX_POINTS]
    return PrepPing(
        meeting=row.summary,
        starts=row.start,
        reason=reason,
        points=kept,
        # Not silence: a meeting that turned up nothing is itself worth one
        # line, and inventing points to fill it is the failure guardrail 6 buys
        # honest failure to avoid.
        gap=None if kept else NO_MATERIAL,
    )
