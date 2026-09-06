"""The scanner is the thing standing between a public repo and a leak.

These describe what it must catch and what it must not flag.
"""

import pytest
from scripts.scan_secrets import scan_text


@pytest.mark.guardrail
@pytest.mark.parametrize(
    "text,label",
    [
        ("dm target U0ZZ9XXQ4T2", "slack user id"),
        ("in:<#C0ZZ8XXT55K>", "slack channel id"),
        ("DM Sponsor D0ZZUXX992Q", "slack dm id"),
        ("doc 1zzQQ4xKmT7pLdW2rNvB8sYhJ0cEfGaXu9iRkPoZtVeM", "google file id"),
        ("user 4fa17c9d-2b83-4e61-9c07-115de3a9b7f2", "uuid (notion/atlassian)"),
        ("profile 7314802955617423", "databricks workspace id"),
        ("mail first.last@createmusicgroup.com", "internal email"),
        ("/Users/someuser/Library/x", "home path"),
        ("token xoxb-2321-4432-aabbccddeeff", "slack token"),
        ("gho_16C7e42F292c6912E7710c838347Ae178B4a", "github token"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private key"),
    ],
)
def test_catches_real_identifiers(text, label):
    found = scan_text(text)
    assert found, f"failed to catch {label} in {text!r}"
    assert any(f[1] == label for f in found), f"caught wrong type: {found}"


@pytest.mark.guardrail
@pytest.mark.parametrize(
    "text",
    [
        "SLACK_USER_PRINCIPAL=U000000000",  # .env.example placeholder
        "channel ${SLACK_CH_POD_DISCOVERY}",  # redacted reference
        "from:<@USERID> in:<#CHANNELID>",  # literal doc placeholders
        "notes from gemini-notes@google.com",  # public Google system address
        "reply to first.last@example.com",
        "id 00000000-0000-0000-0000-000000000000",
        "see $HOME/.local/state/daydag",
        "PR #412 merged tue",  # a bare number is not a workspace id
    ],
)
def test_does_not_flag_safe_text(text):
    assert scan_text(text) == [], f"false positive on {text!r}"


def test_reports_line_numbers():
    ((lineno, label, value),) = scan_text("clean\nleak U0ZZ9XXQ4T2\n")
    assert (lineno, label, value) == (2, "slack user id", "U0ZZ9XXQ4T2")


@pytest.mark.guardrail
def test_catches_truncated_ids_that_still_leak_a_prefix():
    """Docs abbreviate ids with an ellipsis; the prefix alone is enough to leak."""
    found = scan_text("VP Expectations `1zzQQ4xKmT7pLdW2…`")
    assert any(f[1] == "truncated google file id" for f in found), found
