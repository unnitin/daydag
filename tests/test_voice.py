"""Voice and format fixtures (SPEC section 5, issue #8).

These are the regression fixtures for every format change afterwards. The voice
rules are unusually specific because they were derived from what Nitin actually
writes, not from a style guide - so they are testable, and a drift is a bug
rather than a matter of taste.

The sharpest one: the vault uses plain U+26A0 (the text-presentation warning
sign), not the emoji-presentation U+FE0F variant. The difference is invisible in
most editors and visible in the vault.
"""

import itertools

import pytest

from daydag.voice import (
    SAMPLE_DATA,
    SANCTIONED_EMOJI,
    Push,
    clipped,
    one_line,
    render,
    voice_violations,
)


@pytest.mark.guardrail
def test_sanctioned_emoji_are_the_text_presentation_forms():
    """SPEC section 5 calls out plain U+26A0, not the emoji-presentation variant."""
    assert "\u26a0" in SANCTIONED_EMOJI
    assert "\u26a0\ufe0f" not in SANCTIONED_EMOJI


@pytest.mark.guardrail
@pytest.mark.parametrize("text", ["\u26a0\ufe0f heads up", "\u2713\ufe0f done"])
def test_emoji_presentation_variants_are_actually_rejected(text):
    """The constant alone proves nothing - the checker has to catch the variant.

    This is the rule that is invisible in an editor and visible in the vault,
    so it has to fail the gate, not merely be documented in a frozenset.
    """
    assert voice_violations(text), f"U+FE0F variant slipped through: {text!r}"


@pytest.mark.parametrize(
    "text,reason",
    [
        ("Reviewed the deck — it looks good", "em dash"),
        ("Reviewed the deck \u2013 it looks good", "en dash"),
        ("Let me know if that works 🎉", "unsanctioned emoji"),
        ("Please advise at your earliest convenience.", "corporate register"),
    ],
)
def test_voice_violations_are_caught(text, reason):
    found = voice_violations(text)
    assert found, f"missed {reason} in {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "lmk if that works - i'll pick it up tomorrow",
        "def worth doing. keep me honest w/ the dates",
        "🔴 high · deal modeler staging is red",
        "iirc we parked this in aug. nw either way",
        "one flag: ⚠ the invite still lists a departed organiser",
    ],
)
def test_house_voice_passes_clean(text):
    assert voice_violations(text) == [], f"false positive on {text!r}"


def test_hyphen_is_not_flagged_as_a_dash_violation():
    """Hyphens are the house style; only em and en dashes are violations."""
    assert voice_violations("the write-back is half-done") == []


@pytest.mark.parametrize("kind", list(Push))
def test_every_push_type_has_sample_data(kind):
    """Every push needs a realistic render, or its voice fixture proves nothing."""
    assert kind.value in SAMPLE_DATA


@pytest.mark.parametrize("kind", list(Push))
def test_rendered_templates_obey_the_voice_rules(kind):
    """Checked on the *interpolated* form.

    Rendering with no data strips every placeholder, so a skeleton like
    'morning. - meetings' would pass while the real output was never examined.
    """
    out = render(kind, SAMPLE_DATA[kind.value])
    assert out.strip(), f"{kind} rendered empty"
    assert voice_violations(out) == [], f"{kind}: {voice_violations(out)}"


def test_empty_sections_are_omitted_not_labelled_empty():
    """SPEC 3.7 rule 3: a quiet day produces no block, never 'no updates'.

    Asserts on whole phrases - a substring check for "none" false-fires on
    ordinary words like "phone".
    """
    out = render(Push.MORNING_BRIEF, {"day": "tue", "count": 0}).lower()
    for phrase in ("no updates", "nothing to report", "no items", "n/a"):
        assert phrase not in out, f"empty section was labelled: {phrase!r}"


@pytest.mark.guardrail
def test_a_claim_without_a_permalink_is_a_violation():
    """Invariant 3: evidence or silence. The fixture enforces it structurally."""
    with pytest.raises(ValueError, match="permalink"):
        render(Push.NUDGE, {"quote": "can you pick this up?", "owner": "VP-Data"})


@pytest.mark.guardrail
@pytest.mark.parametrize("data", [{}, None, {"quote": "q"}, {"permalink": ""}])
def test_nudge_never_renders_without_a_permalink(data):
    """`and data` short-circuited on {} and None, rendering an unsourced nudge."""
    with pytest.raises(ValueError, match="permalink"):
        render(Push.NUDGE, data)


@pytest.mark.guardrail
def test_the_emoji_ranges_do_not_overlap():
    """A redundant subrange is how a filter drifts from what its author reads.

    `1F900-1F9FF` sat entirely inside `1F300-1FAFF` (CodeQL
    `py/overly-large-range`, medium). This character class IS a filter -
    `voice_violations` decides from it whether a glyph is sanctioned - so the
    set a reader computes by eye must be the set the engine matches.
    """
    import re as _re

    from daydag.voice import _EMOJI

    bounds = [(ord(lo), ord(hi)) for lo, hi in _re.findall(r"(.)-(.)", _EMOJI.pattern)]
    assert bounds, "could not parse the ranges out of the pattern"
    ordered = sorted(bounds)
    for (a, b), (c, d) in itertools.pairwise(ordered):
        assert b < c, f"ranges U+{a:04X}-U+{b:04X} and U+{c:04X}-U+{d:04X} overlap"


@pytest.mark.guardrail
@pytest.mark.parametrize("glyph", ["🎉", "🚀", "🙂", "🤝", "🧠", "✅"])
def test_unsanctioned_emoji_are_still_caught_after_narrowing(glyph):
    """🧠 (U+1F9E0) lived in the removed range; the outer one must still cover it."""
    assert voice_violations(f"nice work {glyph}")


@pytest.mark.guardrail
@pytest.mark.parametrize("glyph", sorted(SANCTIONED_EMOJI))
def test_every_sanctioned_glyph_still_passes(glyph):
    assert voice_violations(f"status {glyph} ok") == []


# --------------------------------------------------------------------------
# the shared text primitives
#
# `brief._short` and `smoke._one_line` each hand-rolled "collapse to one line,
# then clip" with their own cap and their own truncation rule. One idea, two
# implementations, and the pair drifts the next time either is tuned - which is
# the same note review left on the duplicated env-reference regex in #57.
# --------------------------------------------------------------------------


def test_one_line_collapses_every_kind_of_whitespace():
    assert one_line("a\n b\t\tc  \r\nd") == "a b c d"
    assert one_line("  padded  ") == "padded"
    assert one_line("") == ""


def test_one_line_takes_a_non_string_because_a_payload_is_not_always_one():
    assert one_line(404) == "404"
    assert one_line(None) == "None"


def test_clipped_leaves_text_inside_the_budget_alone():
    assert clipped("short", 40) == "short"


def test_clipped_never_exceeds_its_budget_ellipsis_included():
    """The ellipsis comes out of the budget, it is not added to it.

    Appending after clipping is how a limit gets quietly exceeded by three
    characters - and the caller that asked for 160 got 163 into a line it had
    already sized.
    """
    out = clipped("x" * 500, 40, ellipsis="...")
    assert len(out) == 40
    assert out.endswith("...")


def test_clipped_with_no_ellipsis_just_stops():
    out = clipped("y" * 500, 40)
    assert len(out) == 40
    assert "." not in out


def test_clipped_handles_a_budget_smaller_than_its_own_ellipsis():
    """A caller reserving room for a suffix can drive the budget to nothing."""
    assert clipped("z" * 50, 0) == ""
    assert len(clipped("z" * 50, 2, ellipsis="...")) <= 2
