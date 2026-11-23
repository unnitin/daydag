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


# --------------------------------------------------------------------------
# names: the list is config on this machine, never in the repo
# --------------------------------------------------------------------------


def _names_file(tmp_path, body):
    path = tmp_path / "names.txt"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.guardrail
@pytest.mark.parametrize(
    "text",
    ["asked zaphod thu", "Zaphod's answer", "ZAPHOD / principal 1:1", "<@UPEER|Zaphod Quux>"],
)
def test_a_listed_name_is_caught_whatever_its_case(tmp_path, text):
    """The repo is public and a colleague's name is not ours to publish. The
    list of names cannot be in the repo either, so it is read from a file on
    this machine."""
    from scripts.scan_secrets import name_pattern

    names = name_pattern(_names_file(tmp_path, "# colleagues\nzaphod\n\nquux\n"))
    found = scan_text(text, names)
    assert ("name", "zaphod") in {(label, value.lower()) for _, label, value in found}, found


def test_a_name_is_a_whole_word_and_comments_are_not_names(tmp_path):
    from scripts.scan_secrets import name_pattern

    names = name_pattern(_names_file(tmp_path, "# colleagues\nzaphod\n"))
    assert scan_text("the zaphodian option, for colleagues", names) == []


def test_without_the_file_names_are_not_checked_and_ids_still_are(tmp_path):
    """CI and a fresh clone have no list. That must not fail the scan, and it
    must not switch the id checks off."""
    from scripts.scan_secrets import name_pattern

    assert name_pattern(tmp_path / "absent.txt") is None
    assert name_pattern(_names_file(tmp_path, "# nothing listed yet\n")) is None
    ((_, label, _),) = scan_text("zaphod said U0ZZ9XXQ4T2", None)
    assert label == "slack user id"
