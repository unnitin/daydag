"""Voice and format fixtures (SPEC section 5, issue #8).

These are the regression fixtures for every format change afterwards. The voice
rules are unusually specific because they were derived from what Nitin actually
writes, not from a style guide - so they are testable, and a drift is a bug
rather than a matter of taste.

The sharpest one: the vault uses plain U+26A0 (the text-presentation warning
sign), not the emoji-presentation U+FE0F variant. The difference is invisible in
most editors and visible in the vault.
"""

import pytest

from daydag.voice import SAMPLE_DATA, SANCTIONED_EMOJI, Push, render, voice_violations


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
