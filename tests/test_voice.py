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

from daydag.voice import SANCTIONED_EMOJI, Push, render, voice_violations


def test_sanctioned_emoji_are_the_text_presentation_forms():
    """SPEC section 5 calls out plain U+26A0, not the emoji-presentation variant."""
    assert "⚠" in SANCTIONED_EMOJI
    assert "⚠️" not in SANCTIONED_EMOJI


@pytest.mark.parametrize(
    "text,reason",
    [
        ("Reviewed the deck — it looks good", "em dash"),
        ("Reviewed the deck – it looks good", "en dash"),
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
def test_every_push_type_has_a_template(kind):
    out = render(kind, {})
    assert isinstance(out, str)


@pytest.mark.parametrize("kind", list(Push))
def test_rendered_templates_obey_the_voice_rules(kind):
    """A template that violates the voice is a violation shipped every day."""
    assert voice_violations(render(kind, {})) == []


def test_empty_sections_are_omitted_not_labelled_empty():
    """SPEC 3.7 rule 3: a quiet day produces no block, never 'no updates'."""
    out = render(Push.MORNING_BRIEF, {"shipping": [], "chase": []})
    assert "no updates" not in out.lower()
    assert "none" not in out.lower()


def test_a_claim_without_a_permalink_is_a_violation():
    """Invariant 3: evidence or silence. The fixture enforces it structurally."""
    with pytest.raises(ValueError, match="permalink"):
        render(Push.NUDGE, {"quote": "can you pick this up?", "owner": "VP-Data"})
