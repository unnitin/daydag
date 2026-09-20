"""One command line behind the four entries (#117, #126)."""

from pathlib import Path

from daydag import cli, registry

REPO_SKILLS = Path(__file__).resolve().parents[1] / ".claude" / "skills"


def test_the_registry_subcommand_prints_the_shipped_ownership_table(capsys):
    assert cli.main(["registry", str(REPO_SKILLS)]) == 0
    out = capsys.readouterr().out
    assert "ownership (one writer per artifact)" in out
    assert "vault:DayDAG/State.md" in out


def test_a_flag_with_no_value_is_a_usage_error_not_a_file_named_none(tmp_path, monkeypatch):
    """The failure the hand-rolled parsers had: `--log` last wrote sqlite to `None`."""
    monkeypatch.chdir(tmp_path)

    assert cli.main(["soak", "report", "--log"]) == 2
    assert cli.main(["people", "list", "--log"]) == 2
    assert not (tmp_path / "None").exists()


def test_each_module_entry_hands_its_arguments_to_the_one_parser(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "main", lambda argv=None: seen.append(list(argv)) or 0)
    from daydag import people, run, soak

    run.main(["plan", "morning"])
    people.main(["list", "--log", "x.db"])
    soak.main(["report", "--log", "x.db"])
    registry.main([])

    assert seen == [
        ["run", "plan", "morning"],
        ["people", "list", "--log", "x.db"],
        ["soak", "report", "--log", "x.db"],
        ["registry"],
    ]


def test_a_modules_refusal_is_one_line_and_exit_one(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no .env here, so `run` refuses with ConfigError

    assert cli.main(["run", "plan", "morning"]) == 1
    err = capsys.readouterr().err
    assert "no identity file" in err and "Traceback" not in err


def test_the_package_itself_is_the_entry_point():
    """`python -m daydag ...` is what the CLI docstring promises; without a
    `__main__.py` the package refuses to run and the promise is false."""
    import subprocess
    import sys

    done = subprocess.run(
        [sys.executable, "-m", "daydag", "registry", str(REPO_SKILLS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "ownership (one writer per artifact)" in done.stdout
