"""Meeting prep pings: the one interrupt the system allows (SPEC 3.2, #20).

USING IT
    audience = Audience.from_identities(ids)
    reason = prep_worthy(row, audience)     # -> Reason | None; None means skip
    due_at(row)                             # 30 min before it starts
    sources(row, now, identities=ids, channels=chans, terms=words)
    point("what", "why now", quote=q, permalink=link, source="slack")
    build(row, reason, points)              # -> PrepPing
    may_interrupt(Push.PREP)                # the ONLY kind that may
    recipient(ids)                          # the one destination, guardrail 1

CONTRACTS
    1. Qualification is the LEDGER's, narrowed - never re-derived. `Ledger`
       already decides what is a meeting, and that rule was expensive: the #2
       audit found requiring an `accepted` response dropped 61% of real
       meetings silently, because ~70% of invites are never answered. This adds
       exactly one rule on top - 3.2's "skip standups". Response status, kind
       and attendee count are not re-examined, so nothing can drift.
    2. The inclusive/exclusive bias INVERTS across that seam, on purpose. The
       ledger errs wide: a false positive there costs one "no notes" line,
       a false negative is a meeting the system cannot see. Prep errs narrow: a
       false positive spends the INTERRUPT, the only thing allowed to arrive
       off-schedule. An interrupt that fires on a standup is how the channel
       gets muted, and a muted channel fails guardrail 1 more completely than
       never pinging at all.
    3. Verbatim quotes are EVIDENCE, not voice. House rule 1 says quote
       verbatim with a permalink; SPEC 5 bans em dashes. Someone else's Slack
       line may contain one, and rewriting it to pass a style check is exactly
       the paraphrase-from-memory the quoting rule exists to prevent. So the
       voice check has a defined scope: `PrepPing.authored_text` - what the
       agent composed, with quoted spans and their links excised.
    4. No I/O and NO SEND PATH. This builds the ping; delivery is somebody
       else's problem, and per guardrail 1 it has exactly one destination.
    5. An unset `ORG_EMAIL_DOMAIN` means nobody is declared external, not
       everybody. Defaulting the other way fires the interrupt on every meeting
       on the calendar, which mutes it inside a day.

WHY IT EXISTS
    Thirty minutes out, three to five talking points drawn from the last four
    weeks of that meeting's notes and the Slack around them - the
    `weekly-planning` skill's File 2 recipe, run just in time instead of on
    Friday.

KNOWN LIMIT
    `LOOKBACK_DAYS` is 28 and fixed. A meeting that recurs monthly gets one
    prior instance to draw on, which is thin; a weekly gets four.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum

from daydag.config import resolve_reference
from daydag.ledger import Row
from daydag.recipes import (
    PACIFIC,
    RecipeError,
    gmail_gemini_notes,
    is_user_id,
    meeting_prep,
    slack_search,
)
from daydag.voice import Push, render

__all__ = [
    "LOOKBACK_DAYS",
    "MAX_POINTS",
    "NO_MATERIAL",
    "PREP_LEAD",
    "UNSOURCED",
    "Audience",
    "Point",
    "PrepError",
    "PrepPing",
    "Reason",
    "Schedule",
    "SourcePlan",
    "build",
    "due_at",
    "may_interrupt",
    "point",
    "prep_worthy",
    "recipient",
    "sources",
]


class PrepError(RecipeError):
    """A prep ping was asked for something it must not invent.

    Subclasses :class:`~daydag.recipes.RecipeError` on purpose: prep's whole job
    is to turn a meeting into source queries, so a caller that already catches
    the recipe refusal catches this too rather than needing to know the seam is
    there.
    """


#: Section 3.2's lead time. Not configurable: it is what makes the ping the one
#: thing worth interrupting for, and a longer one is just an earlier brief.
PREP_LEAD = timedelta(minutes=30)

#: "the last ~4 weeks" of that meeting's notes, as days.
LOOKBACK_DAYS = 28

#: Section 3.2 says 3-5. Only the ceiling is enforced - see :func:`build`.
MAX_POINTS = 5

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
    """Why a meeting earned the interrupt. Recorded, not just computed.

    A ping that cannot say why it fired is unauditable, and the first question
    about a ping that should not have fired is which rule let it through.
    """

    ONE_ON_ONE = "1:1"
    STEERING = "steering/pod"
    LEADERSHIP = "leadership in the room"
    EXTERNAL = "external party"


#: Prep's single extra rule. Focus blocks, holds and OOO are already gone via
#: `ledger.NON_MEETING_KINDS`, and a solo block cannot clear the ledger's
#: two-attendee floor, so standups are all that is left to exclude.
_STANDUP = re.compile(r"\b(stand\s*-?\s*ups?|scrums?)\b", re.IGNORECASE)

#: Two first names and a slash is how half of his 1:1s are actually titled, so
#: the count matters as much as the words - hence both paths in `_shape`.
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
        outside party when the org domain is unset - fires the interrupt on
        every meeting on the calendar, which mutes it inside a day.
        """
        return cls(
            leadership=_csv(identities, LEADERSHIP_KEY),
            internal_domains=_csv(identities, ORG_DOMAIN_KEY),
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
    """Why this ledger row deserves a prep ping, or ``None``.

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
# timing
# ---------------------------------------------------------------------------


def due_at(row: Row) -> datetime:
    """When this instance's ping should fire."""
    return row.start - PREP_LEAD


class Schedule:
    """Which pings are due now, each meeting instance pinged exactly once.

    Keyed on ``Row.key`` - the event id *and* the start - because a weekly 1:1
    is a new row every week. Keying on the event id alone produces a recurring
    meeting that is prepped once and never again, and it looks entirely correct
    for the first seven days.
    """

    def __init__(self) -> None:
        self._pinged: set[tuple[str, datetime]] = set()

    def due(
        self, rows: Iterable[Row], now: datetime, audience: Audience
    ) -> list[tuple[Row, Reason]]:
        """Newly due pings, earliest meeting first. Marks them as fired."""
        if now.tzinfo is None:
            raise PrepError("now must be timezone-aware; the lead time is wall-clock")
        due: list[tuple[Row, Reason]] = []
        for row in rows:
            if row.start.tzinfo is None:
                # `ledger.seed_day` passes `event["start"]` through untouched, so
                # a calendar read that lost its offset arrives here. Compared
                # against an aware `now` that is a TypeError from inside the
                # loop, naming neither the meeting nor the cause. Raising rather
                # than skipping because the start comes from the same read for
                # every row: if one is naive the batch is not trustworthy, and a
                # silently skipped prep ping is invisible.
                raise PrepError(
                    f"{row.summary!r} has a timezone-naive start; the calendar read lost its offset"
                )
            if row.key in self._pinged:
                continue
            # After the meeting starts the ping is worthless, and a worthless
            # interrupt still spends the budget - so the window closes hard.
            if not (due_at(row) <= now < row.start):
                continue
            reason = prep_worthy(row, audience)
            if reason is None:
                continue
            self._pinged.add(row.key)
            due.append((row, reason))
        return sorted(due, key=lambda pair: pair[0].start)


# ---------------------------------------------------------------------------
# sources: the File 2 recipe, run just in time
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
    the drift only shows up as a ping that quietly omits a week.
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
        # back-to-back 1:1s - which for a prep ping means prepping the wrong one.
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
            # One unresolvable channel must not take the ping with it. Named,
            # because a channel silently dropped is a silence nobody checked.
            degraded.append(f"{channel}: {exc}")

    return SourcePlan(
        window=(start, end),
        gemini=gemini,
        slack=tuple(queries),
        # The MEETING's week, not `now`'s. They agree for a same-day, 30-min
        # ping - which is why this shipped looking right - and disagree the
        # first time `sources()` is built ahead of time, from a different
        # week: the week-ahead loop (SPEC 3.6) calls this on Sunday night for
        # Monday's meetings, and `meeting_prep(end)` there named Meeting
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
    this codebase. `Schedule.due` already refuses a naive `row.start` outright;
    this path can be reached without going through `Schedule` at all (the
    week-ahead loop calls `sources()` directly, well before the 30-minute
    window), so it degrades to the same wall-clock reading rather than raising.
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
        head = f"- {self.what.strip()} - {self.why_now.strip()}"
        if self.sourced:
            return f'{head}\n  "{self.quote}" {self.permalink}'
        return f"{head}\n  ({UNSOURCED})"

    def authored(self) -> str:
        """The line minus the borrowed words, for the voice check.

        The quote and its link were written by somebody else and copied
        verbatim; holding them to his voice would mean editing evidence.
        """
        head = f"- {self.what.strip()} - {self.why_now.strip()}"
        return head if self.sourced else f"{head}\n  ({UNSOURCED})"


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
    is; a candidate with no why-now is dropped here rather than padding the ping
    out to five, because a padded ping is one he skims.
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
# the ping
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrepPing:
    """One meeting's prep, ready to be handed to the DM."""

    meeting: str
    starts: datetime
    reason: Reason
    points: tuple[Point, ...] = ()
    #: Set when there were no points at all, and says so in his own words.
    gap: str | None = None

    def headline(self) -> str:
        """The one-line push. Depth is in the thread (principle 6)."""
        return render(Push.PREP_PING, {"meeting": self.meeting})

    def _body(self, *, authored: bool) -> str:
        if not self.points:
            return self.gap or NO_MATERIAL
        return "\n".join(p.authored() if authored else p.render() for p in self.points)

    def render(self) -> str:
        return f"{self.headline()}\n{self._body(authored=False)}"

    def detail(self) -> str:
        """Everything after the headline - the thread reply, not the interrupt.

        Exists so a delivery layer can post :meth:`headline` as the one
        top-level message the interrupt is allowed to be, and this as the
        threaded reply underneath it (principle 6), without reaching past this
        class into ``_body``.
        """
        return self._body(authored=False)

    def authored_text(self) -> str:
        """Everything the agent wrote, with borrowed words excised.

        This is the string that must pass `voice.voice_violations` clean. See
        the module docstring for why the full render is not that string.
        """
        return f"{self.headline()}\n{self._body(authored=True)}"


def build(row: Row, reason: Reason, points: Iterable[Point | None]) -> PrepPing:
    """Assemble a ping from candidate points.

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


# ---------------------------------------------------------------------------
# the one interrupt, and its one destination
# ---------------------------------------------------------------------------


def may_interrupt(kind: Push) -> bool:
    """Whether a push may arrive off the schedule. Exactly one may.

    ARCHITECTURE's interaction model batches every decision onto the next
    scheduled push, because an agent that needs an answer at an unpredictable
    moment is a pager and a pager gets muted. The prep ping is the single
    exception: it is time-boxed by definition, and after the meeting it is
    worth nothing.
    """
    return kind is Push.PREP_PING


def recipient(identities: Mapping[str, str]) -> str:
    """The only place a ping may go: the principal's own DM (guardrail 1).

    A function rather than a constant so the id stays in `.env`, and an error
    rather than a fallback so a misconfigured run pings nobody instead of
    pinging somebody else.

    The shape is checked as well as the presence. `slack_search` already refuses
    a non-id because a display name matches nothing *silently*; the one
    permitted destination deserves the same floor, and `.env` is hand-edited -
    an empty value, a display name, or a trailing inline comment glued onto the
    id all produced a DM addressed at a conversation that does not exist.
    """
    value = resolve_reference(
        "${SLACK_USER_PRINCIPAL}",
        identities,
        what="prep ping recipient",
        error=PrepError,
    )
    if not is_user_id(value):
        # Never echo the value: config.ConfigError holds the same line, because
        # an error that prints the id defeats keeping it out of the repo.
        raise PrepError(
            "SLACK_USER_PRINCIPAL is not a Slack user id. Fix it in .env - and "
            "note that everything after the `=` is the value, inline comment included."
        )
    return value
