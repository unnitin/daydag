"""Naming the meeting to prep for, and refusing to guess between two.

USING IT
    found = select(rows, "finance x data", now=now, principal=me)
    found.one          # -> Row | None, only when exactly one matched
    found.candidates   # -> every match, in time order
    found.render()     # -> the line to show when `one` is None

CONTRACTS
    1. `one` is set ONLY when exactly one meeting matched. Two matches is not a
       tie to break, it is a question to ask - see WHY IT EXISTS.
    2. A selector matches a meeting's TITLE loosely, or an attendee by name
       tokens, where every token must be present. One token is enough on its
       own; "bo finch" needs both.
    3. The principal never matches. He is on every meeting.
    4. Only meetings that have not started, and only inside `HORIZON_DAYS`.
    5. An empty selector raises rather than matching everything.

WHY IT EXISTS
    `run._prep` preps the NEXT qualifying meeting, because a prep ping is the
    only push allowed to interrupt and is worth exactly as much as its timing.
    *"Prep me for the Finance call"* is a different question and had nowhere to
    go - SPEC §3.8 lists it, the runner had no selector.

    The hard part is not the matching, it is the ambiguity, which is the common
    case rather than the edge: a 1:1 that runs twice a week appears twice in any
    horizon worth searching, and a person is in several meetings. Prep for the
    WRONG meeting is worse than no prep at all - he reads it, trusts it, and
    walks into the other one cold. So two matches surface as two (invariant 5).

KNOWN LIMIT
    Matching is tokens, not identity. It does not know that "Wren" and
    `wren.alder@` are the same person as "Wren Alder" in a title - only that
    the token `wren` appears in each.

    This makes the SHAPING load-bearing. Plenty of real addresses are a bare
    first name (`jonathan@`, `alex@`), so the surname exists only in google's
    `displayName` and an attendee shaped as the address alone cannot be
    distinguished from a different Jonathan. `SKILL.md` requires the
    `"Full Name <addr>"` form for exactly this, and `_person_tokens` reads
    both.

    A genuine nickname sharing no token with either ("Bill" against
    `william.smith@` with no display name) still will not match. The honest fix
    is an alias in `.env`, not fuzzier matching here - fuzzier matching buys
    back the wrong-meeting failure this module exists to prevent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from daydag.ledger import Row

__all__ = ["HORIZON_DAYS", "Selection", "select"]

#: How far ahead a named prep will look. A week, because that is the span the
#: evidence a prep is built from actually covers - two weeks out, the Slack and
#: mail it would quote has not been written yet.
HORIZON_DAYS = 7

_TOKENS = re.compile(r"[^a-z0-9]+")


def _tokens(text: str) -> set[str]:
    """The words in a string, however it was punctuated."""
    return {part for part in _TOKENS.split(str(text).casefold()) if part}


def _person_tokens(attendee: str) -> set[str]:
    """The name tokens in an attendee, with the domain thrown away.

    `wren.alder@example.com` is `{wren, alder}`, so a first name finds the
    person without anyone storing a display name - and `example`/`com` are NOT
    matchable, because every colleague shares them and a selector that hit the
    domain would match the entire invite list.

    A display name rather than an address just tokenises whole.
    """
    return _tokens(str(attendee).split("@", 1)[0])


@dataclass(frozen=True)
class Selection:
    """What a selector matched, and what to say when that is not one thing."""

    candidates: tuple[Row, ...]
    selector: str

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
        lines.extend(f"  {row.start:%a %d %b %H:%M}  {row.summary}" for row in self.candidates)
        return "\n".join(lines)


def _matches(row: Row, wanted: set[str], phrase: str, principal: str) -> bool:
    """Whether one meeting answers to this selector.

    Title first and loosely, because "finance x data" is how he refers to the
    meeting and is not anybody's name. Then attendees, where EVERY token has to
    land: one token is enough to name a person, but a two-token selector that
    matched on either half would pick the wrong Bo.
    """
    if phrase and phrase in str(row.summary).casefold():
        return True
    if wanted <= _tokens(row.summary):
        return True
    for attendee in row.attendees or ():
        # Contract 3, and it has to compare like with like: `attendees` are
        # EMAILS, so the principal has to arrive as one. Passing a Slack id
        # here matches nothing and silently disables this skip - which is what
        # the first wiring of this did, while the tests passed because their
        # fixture principal happened to be email-shaped.
        if principal and str(attendee).casefold() == principal.casefold():
            continue
        if wanted and wanted <= _person_tokens(attendee):
            return True
    return False


def select(
    rows: Iterable[Row],
    selector: str,
    *,
    now: datetime,
    principal: str = "",
    horizon_days: int = HORIZON_DAYS,
) -> Selection:
    """Every upcoming meeting answering to ``selector``, in time order.

    Raises `ValueError` on an empty selector: `""` is a substring of every
    title, so matching everything and taking the first would look exactly like
    a selector that works.
    """
    phrase = " ".join(str(selector).casefold().split())
    if not phrase:
        raise ValueError("a prep selector cannot be empty - name a meeting or a person")
    wanted = _tokens(selector)

    horizon = now + timedelta(days=horizon_days)
    found = [
        row
        for row in rows
        if now <= row.start <= horizon and _matches(row, wanted, phrase, principal)
    ]
    return Selection(tuple(sorted(found, key=lambda r: r.start)), phrase)


def windows_for(now: datetime, *, horizon_days: int = HORIZON_DAYS) -> Sequence[object]:
    """Day windows a named prep must fetch - one per day across the horizon.

    Separate from `recipes.calendar_days` only to keep the horizon in one
    place; the bound itself is the #2 audit's, one request per day and never a
    wide one.
    """
    from daydag import recipes

    day = now.astimezone(recipes.PACIFIC).date()
    return recipes.calendar_days(day, day + timedelta(days=horizon_days))
