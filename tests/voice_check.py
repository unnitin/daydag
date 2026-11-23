"""The two scanners the suite runs over rendered text: house voice, and evidence or silence.

USING IT
    from voice_check import unsourced_claims, voice_violations

    voice_violations(text)      # -> list[str]; empty means it reads as him
    unsourced_claims(text)      # -> bullets with no citation and no admission; must be empty
    SAMPLE_DATA[kind.value]     # data that renders one `voice.Push` realistically

CONTRACTS
    1. Hyphens, never em or en dashes, and none of the phrases in `_CORPORATE`.
    2. Emoji only from SANCTIONED_EMOJI. The warning sign is plain U+26A0, NOT
       the emoji-presentation U+26A0 U+FE0F - invisible in most editors, and
       visible in the vault, which is the one place it matters. `voice.WARN`
       is that glyph; `brief.WARN` and `week_ahead.WARN` are literal copies,
       and `test_voice.py` pins all three.
    3. Evidence or silence (guardrail 3). A `- ` bullet passes
       `unsourced_claims` when it carries a citation or one of ADMISSIONS. A
       citation is `brief._EVIDENCE`, imported and not spelt again, so the
       scanner and `brief.split_citation` read one rule; `UNSOURCED` and
       `NOT_SHOWN` are the live wording for the same reason.

WHY IT EXISTS
    Both scanners lived in `src/daydag` (`voice`, `brief`) and nothing there
    called them: no loop runs either on its own output before it prints. The
    suite was their only caller, running them over what the live renderers
    return, so they sit with the tests, the way `evalset.py` does. Imported
    as `voice_check` (tests/ is on the pytest path).

    The voice rules are derived from what he actually writes rather than from
    a style guide, which is the only reason they are testable at all. A drift
    is a bug, not a matter of taste.

    Not `daydag.style`. That module's phrase and emoji checks read the rows he
    keeps in `DayDAG/Style.md` and run at build time (`python -m daydag.style
    check`); these are the fixed house rules, and only the suite runs them.

KNOWN LIMIT
    `unsourced_claims` is a floor under the rendering, not a proof of
    provenance: a claim whose own text contains a parenthesised `#` or `.md`
    reads as sourced.
"""

from __future__ import annotations

import re

from daydag.brief import _EVIDENCE, NOT_SHOWN, UNSOURCED

__all__ = [
    "ADMISSIONS",
    "SAMPLE_DATA",
    "SANCTIONED_EMOJI",
    "unsourced_claims",
    "voice_violations",
]

# --------------------------------------------------------------------------
# the house voice (moved from `daydag.voice`)
# --------------------------------------------------------------------------

#: The only emoji allowed in agent output. Note the plain U+26A0.
SANCTIONED_EMOJI = frozenset({"🔴", "🟡", "🟢", "✓", "⭐", "⚠"})

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
    "eod_wrap": {"closed": 2, "moved": 3, "confirm": 1},
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


# --------------------------------------------------------------------------
# evidence or silence (moved from `daydag.brief`)
# --------------------------------------------------------------------------

#: Phrases that count as admitting an absence. A line carrying one of these is
#: making a claim *about* a gap, so it needs no permalink - the gap is the fact.
ADMISSIONS = (
    UNSOURCED,
    "couldn't check",
    "couldn't source",
    "could not fetch",
    "could not read",
    "no note found",
    "not watched",
    "not understood",
    NOT_SHOWN,
    # The State.md writer declining (`StateNotWritable`): a failed write, said
    # once at the foot of the push, is a gap and not an assertion.
    "couldn't update",
)


def unsourced_claims(text: str) -> list[str]:
    """Every bullet in ``text`` that asserts something without evidence.

    The mechanical form of guardrail 3, applied to a rendered brief. A bullet
    passes if it carries a citation or if it admits it has none; headings and
    the greeting are not claims and are ignored.

    Known limit, stated rather than hidden: a claim whose own text happens to
    contain a parenthesised ``#`` or ``.md`` reads as sourced. The check is a
    floor under the rendering, not a proof of provenance.
    """
    unsourced: list[str] = []
    for line in text.splitlines():
        if not line.startswith("- "):
            continue
        if _EVIDENCE.search(line):
            continue
        if any(admission in line for admission in ADMISSIONS):
            continue
        unsourced.append(line)
    return unsourced
