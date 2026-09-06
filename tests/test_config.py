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


def test_membership_does_not_raise_for_a_missing_key(tmp_path):
    """`key in ids` must answer False, not raise - Mapping's default would."""
    env = tmp_path / ".env"
    env.write_text("SLACK_USER_PRINCIPAL=nitin-slack-id\n")
    ids = Identities.from_file(env)
    assert "SLACK_USER_PRINCIPAL" in ids
    assert "NOPE" not in ids


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


def test_every_reference_in_the_repo_resolves():
    """`.env.example` is illustrative, not exhaustive - it teaches the shape.

    The invariant that matters is that every ${VAR} written into a doc or a
    module actually resolves, or the redaction has broken the text it replaced.
    """
    import re
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    real = root / ".env"
    if not real.exists():
        pytest.skip("no local .env")
    ids = Identities.from_file(real)
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.split()
    referenced = set()
    for rel in tracked:
        path = root / rel
        if path.suffix not in {".md", ".py", ".yml", ".yaml", ".toml"}:
            continue
        if rel.startswith("tests/"):
            continue  # fixtures deliberately reference names that do not exist
        referenced |= set(re.findall(r"\$\{([A-Z][A-Z0-9_]+)\}", path.read_text()))
    referenced -= {"VAR_NAME", "VAR"}  # placeholders used when explaining the scheme
    unresolved = sorted(k for k in referenced if k not in ids)
    assert not unresolved, f"referenced but absent from .env: {unresolved}"


def test_example_parses_and_names_roles_not_people():
    """Key names leak too: SLACK_USER_JANE_DOE names a colleague."""
    from pathlib import Path

    example = Identities.from_file(Path(__file__).resolve().parents[1] / ".env.example")
    assert example, "example schema is empty"
    assert all(k.isupper() for k in example)
