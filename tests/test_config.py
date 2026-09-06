"""Config is the only path to a real identifier. It must fail loudly, never silently."""

import pytest

from daydag.config import ConfigError, Identities


def test_loads_values_from_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SLACK_USER_PRINCIPAL=nitin-slack-id\n# a comment\n\nVAULT_ROOT=/vault\n")
    ids = Identities.from_file(env)
    assert ids["SLACK_USER_PRINCIPAL"] == "nitin-slack-id"
    assert ids["VAULT_ROOT"] == "/vault"


def test_missing_key_raises_naming_the_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SLACK_USER_PRINCIPAL=nitin-slack-id\n")
    with pytest.raises(ConfigError, match="SLACK_CH_POD_DISCOVERY"):
        Identities.from_file(env)["SLACK_CH_POD_DISCOVERY"]


def test_missing_file_points_at_the_example(tmp_path):
    with pytest.raises(ConfigError, match=r"\.env\.example"):
        Identities.from_file(tmp_path / "nope.env")


def test_expands_references_in_text(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SLACK_USER_PRINCIPAL=nitin-slack-id\n")
    ids = Identities.from_file(env)
    assert ids.expand("dm ${SLACK_USER_PRINCIPAL} now") == "dm nitin-slack-id now"


def test_expand_rejects_unknown_reference(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SLACK_USER_PRINCIPAL=nitin-slack-id\n")
    with pytest.raises(ConfigError, match="NOPE"):
        Identities.from_file(env).expand("hello ${NOPE}")


@pytest.mark.guardrail
def test_never_echoes_a_value_in_an_error(tmp_path):
    """An exception that prints the ID defeats the point of holding it in .env."""
    env = tmp_path / ".env"
    env.write_text("SLACK_USER_PRINCIPAL=nitin-slack-id\n")
    ids = Identities.from_file(env)
    with pytest.raises(ConfigError) as exc:
        ids["MISSING"]
    assert "nitin-slack-id" not in str(exc.value)


def test_example_file_covers_every_key_used_in_repo():
    """.env.example is the schema. A key in .env with no example entry is undocumented."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    example = Identities.from_file(root / ".env.example")
    real = root / ".env"
    if not real.exists():
        pytest.skip("no local .env")
    missing = set(Identities.from_file(real)) - set(example)
    assert not missing, f".env has keys absent from .env.example: {sorted(missing)}"
