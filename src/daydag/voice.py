"""The template for every push, the warning glyph, and the one-line clip.

USING IT
    render(Push.MORNING_BRIEF, data)     # -> the text for one push kind
    one_line(text)                       # collapse whitespace to a single line
    clipped(text, 160, ellipsis="...")   # one line, never longer than 160
    WARN                                 # the sanctioned warning sign

CONTRACTS
    1. `WARN` is plain U+26A0, NOT the emoji-presentation U+26A0 U+FE0F -
       invisible in most editors, and visible in the vault, which is the one
       place it matters.
    2. A nudge with no permalink does not render: `render(Push.NUDGE, ...)`
       raises `ValueError` (invariant 3, evidence or silence).
    3. `clipped` spends the ellipsis FROM the budget, never on top of it.
       Appending after the cut is how a 160-char limit returns 163 into a line
       the caller had already sized.

WHY IT EXISTS
    One template per push kind, kept as plain strings so a diff on them is
    readable, plus the two things renderers share: the warning glyph and the
    collapse-and-clip.

    `one_line`/`clipped` live here because three modules had hand-rolled the
    same `" ".join(text.split())[:cap]` - `brief`, `runlog` and `smoke`, which
    has since been retired. The caps stayed with their callers: a quote and a
    run row are genuinely different budgets. Only the mechanism was shared.

KNOWN LIMIT
    The house voice is not enforced here. Hyphens never dashes, emoji only
    from the sanctioned set, no corporate register: those are assertions the
    SUITE runs over rendered text, with `voice_violations` in
    `tests/voice_check.py`. No loop calls it on its own push. (The rules his
    feedback adds are `daydag.style`'s, read from `DayDAG/Style.md`.)
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

#: The sanctioned warning sign, named once. `state` and `brief` each had their
#: own literal copy, and this is the one glyph in the sanctioned set with an
#: invisible wrong twin - the emoji-presentation U+26A0 U+FE0F. Three literals
#: is three chances to paste the wrong one.
WARN = "\u26a0"


def one_line(text: Any) -> str:
    """Any text as a single line, with runs of whitespace collapsed.

    Takes a non-string because the callers feed it whatever a connector or a
    note handed back, and `str()` at each call site is the step somebody skips.
    """
    return " ".join(str(text).split())


def clipped(text: Any, limit: int, *, ellipsis: str = "") -> str:
    """One line, no longer than ``limit``, ``ellipsis`` inside the budget.

    The ellipsis comes out of the budget rather than being appended after the
    cut: appending is how a 160-char limit quietly returns 163 into a line the
    caller had already sized. A ``limit`` too small for the ellipsis drops it
    rather than overshooting, and a ``limit`` of zero or less is the empty
    string - a caller reserving room for a suffix can reach both.

    The budget is the caller's, not this function's. `brief` clips a quote at
    one width and `runlog` a failure at another; what they share is the
    collapse-then-clip, and only that is here.
    """
    line = one_line(text)
    if limit <= 0:
        return ""
    if len(line) <= limit:
        return line
    if ellipsis and limit > len(ellipsis):
        return line[: limit - len(ellipsis)].rstrip() + ellipsis
    return line[:limit].rstrip()


class Push(Enum):
    """Every surface the agent renders. Each needs a template and a fixture."""

    MORNING_BRIEF = "morning_brief"
    EOD_WRAP = "eod_wrap"
    WEEK_AHEAD = "week_ahead"
    INGEST_DIGEST = "ingest_digest"
    PREP_PING = "prep_ping"
    NUDGE = "nudge"


# Templates are deliberately plain strings, not a template engine: they are
# fixtures first and output second, and a diff on them should be readable.
_TEMPLATES: dict[Push, str] = {
    Push.MORNING_BRIEF: "morning. {day} - {count} meetings",
    Push.EOD_WRAP: "wrap: {closed} closed, {moved} moved, {confirm} to confirm",
    Push.WEEK_AHEAD: "week ahead - shape of it below",
    Push.INGEST_DIGEST: "logged from {meeting}: {summary}. anything wrong, lmk",
    Push.PREP_PING: "{meeting} in 30 - talking points in thread",
    Push.NUDGE: "hey {owner} - following up on this: {quote} ({permalink})",
}


def render(kind: Push, data: dict[str, Any]) -> str:
    """Render one push.

    Empty sections are omitted entirely rather than labelled - SPEC 3.7 rule 3
    says a quiet day produces no block, because a line saying nothing happened
    is noise that trains the reader to skim.
    """
    if kind is Push.NUDGE and not (data or {}).get("permalink"):
        # Invariant 3: evidence or silence. Structural, not advisory - a nudge
        # that cannot be sourced must not render at all.
        raise ValueError("a nudge needs a permalink; quote alone is not evidence")

    body = _TEMPLATES[kind]
    filled = {k: v for k, v in (data or {}).items() if v not in (None, "", [], {})}
    for key, value in filled.items():
        body = body.replace("{" + key + "}", str(value))
    # Unfilled placeholders are dropped rather than rendered as literals.
    return re.sub(r"\s*\{[a-z_]+\}", "", body).strip()
