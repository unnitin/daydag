"""The house voice as assertions, plus the template for every push.

USING IT
    voice_violations(text)      # -> list[str]; empty means it reads as Nitin
    render(Push.BRIEF, data)    # -> the text for one push kind
    one_line(text)              # collapse whitespace to a single line
    clipped(text, 160, ellipsis="...")   # one line, never longer than 160

CONTRACTS
    1. Hyphens, never em or en dashes.
    2. Emoji only from SANCTIONED_EMOJI. The warning sign is plain U+26A0, NOT
       the emoji-presentation U+26A0 U+FE0F - invisible in most editors, and
       visible in the vault, which is the one place it matters.
    3. `clipped` spends the ellipsis FROM the budget, never on top of it.
       Appending after the cut is how a 160-char limit returns 163 into a line
       the caller had already sized.

WHY IT EXISTS
    The rules are derived from what Nitin actually writes rather than from a
    style guide, which is the only reason they are testable at all. A drift is
    a bug, not a matter of taste.

    `one_line`/`clipped` live here because three modules had hand-rolled the
    same `" ".join(text.split())[:cap]` - `brief`, `smoke` and `runlog`. The
    caps stayed with their callers: a quote, a connector failure and a run row
    are genuinely different budgets. Only the mechanism was shared.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

#: The only emoji allowed in agent output. Note the plain U+26A0.
SANCTIONED_EMOJI = frozenset({"🔴", "🟡", "🟢", "✓", "⭐", "⚠"})

#: The sanctioned warning sign, named once. `state` and `brief` each had their
#: own literal copy, and this is the one glyph in the set with an invisible
#: wrong twin - the emoji-presentation U+26A0 U+FE0F. Three literals is three
#: chances to paste the wrong one.
WARN = "\u26a0"

#: Em dash and en dash. Hyphens are house style; these are not.
_DASHES = re.compile("[\u2014\u2013]")  # em dash, en dash

#: U+FE0F turns a sanctioned text-presentation glyph into its emoji twin.
#: Invisible in most editors, visible in the vault - so it is caught explicitly
#: rather than left to the pictographic range below.
_VARIATION_SELECTOR = "\ufe0f"

#: Anything pictographic that is not on the sanctioned list.
#:
#: The ranges must not overlap. `1F900-1F9FF` was here and sits entirely inside
#: `1F300-1FAFF`, which CodeQL flags as `py/overly-large-range`: a redundant
#: subrange in a character class is how a filter comes to match something other
#: than its author believes, and this class IS a filter - `voice_violations`
#: decides from it whether a glyph is sanctioned. Keep them disjoint so the set
#: a reader computes by eye is the set the engine matches.
_EMOJI = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\u2b00-\u2bff]")

#: Minimal data that renders each push into a realistic string, so the voice
#: fixtures exercise the interpolated form and not just the stripped skeleton.
SAMPLE_DATA: dict[str, dict[str, object]] = {
    "morning_brief": {"day": "tue", "count": 4},
    "eod_wrap": {"closed": 2, "moved": 3},
    "week_ahead": {},
    "ingest_digest": {"meeting": "the 2pm call", "summary": "2 commitments"},
    "prep_ping": {"meeting": "pod steering"},
    "nudge": {
        "owner": "VP-Data",
        "quote": "can you pick this up?",
        "permalink": "https://example.com/p/1",
    },
}

#: Register that reads as corporate rather than as him.
_CORPORATE = (
    "at your earliest convenience",
    "please advise",
    "kindly",
    "as per",
    "circle back",
    "reach out to",
    "touch base",
    "per my last",
)


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
    one width and `smoke` a failure at another; what they shared was the
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


def voice_violations(text: str) -> list[str]:
    """Every way ``text`` departs from the house voice.

    Returns a list rather than a bool so a caller can say *what* is wrong -
    a template failing this should name the rule, not just fail.
    """
    found: list[str] = []
    if _VARIATION_SELECTOR in text:
        found.append("emoji-presentation variant (U+FE0F) - use the plain glyph")
    if _DASHES.search(text):
        found.append("em/en dash - use a hyphen")
    for match in _EMOJI.finditer(text):
        char = match.group(0)
        if char not in SANCTIONED_EMOJI:
            found.append(f"unsanctioned emoji {char!r}")
    lowered = text.casefold()
    for phrase in _CORPORATE:
        if phrase in lowered:
            found.append(f"corporate register: {phrase!r}")
    return found


# Templates are deliberately plain strings, not a template engine: they are
# fixtures first and output second, and a diff on them should be readable.
_TEMPLATES: dict[Push, str] = {
    Push.MORNING_BRIEF: "morning. {day} - {count} meetings",
    Push.EOD_WRAP: "wrap: {closed} closed, {moved} moved",
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
