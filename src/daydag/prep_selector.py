"""Naming the meeting to prep for, and refusing to guess between two.

USING IT
    found = select(rows, "finance x data", now=now, until=until, principal=me, tz=tz)
    found.one          # -> Row | None, only when exactly one matched
    found.candidates   # -> every match, in time order
    found.render()     # -> the line to show when `one` is None

CONTRACTS
    1. `one` is set ONLY when exactly one meeting matched. Two matches is not a
       tie to break, it is a question to ask - see WHY IT EXISTS.
    2. A selector matches by TOKENS - against the title, or against an
       attendee's address local-part and display name - and every token must
       be present. One token is enough on its own; "bo finch" needs both. No
       substring path: "fin" does not find Finance, because partial-word
       matching is the fuzziness KNOWN LIMIT argues against.
    3. The principal never matches. He is on every meeting.
    4. Only meetings that have not started, and only before ``until`` - the
       bound the caller computed from the same day arithmetic as its fetch.
    5. A selector with no word in it raises rather than matching everything.
       The guard is on the TOKENS, because an empty token set is a subset of
       every title - "---" passed a guard on the string and matched the week.

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

    So the display name matters. Plenty of real addresses are a bare first
    name (`jonathan@`, `alex@`), and the surname exists only in google's
    `displayName` - shaped as the address alone, "jonathan strauss" cannot
    match and bare "jonathan" matches a different Jonathan. The name reaches
    here as `Row.attendee_names`, split from the address once in
    `Ledger.seed_day` (`ledger.attendee_parts`). It is deliberately NOT folded
    into the address string: that was tried, and every other consumer of
    `Row.attendees` compares bare emails, so it broke `has_external`,
    `has_leadership`, note matching and this module's own principal skip at
    once.

    A genuine nickname sharing no token with either ("Bill" against
    `william.smith@` with no display name) still will not match. The honest fix
    is an alias in `.env`, not fuzzier matching here - fuzzier matching buys
    back the wrong-meeting failure this module exists to prevent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, tzinfo

from daydag import recipes
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
    tz: tzinfo = recipes.PACIFIC

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
    "fin" find Finance, which is the fuzziness this module refuses.
    """
    if wanted <= _tokens(row.summary):
        return True
    names = list(row.attendee_names) + [""] * (len(row.attendees) - len(row.attendee_names))
    for address, name in zip(row.attendees, names, strict=False):
        # Contract 3, comparing like with like: `Row.attendees` are bare EMAILS
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
    tz: tzinfo = recipes.PACIFIC,
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
