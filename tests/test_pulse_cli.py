"""The pulse reaches the CLI, and reads on from where it left off (#138).

`render` always accepted a pulse; `main` never built one, so `ship` degraded
on every real run and the shipping sections never rendered. And the cursor
each mirror advanced was returned and thrown away, so even a built pulse
started at first sight and reported a quiet day forever.
"""

from datetime import UTC, datetime

import pytest

from daydag import recipes, run
from daydag.config import Identities
from daydag.eventlog import EventLog

FRIDAY = datetime(2026, 9, 18, 17, 0, tzinfo=UTC)


@pytest.fixture
def identities(tmp_path, fake_repo):
    env = tmp_path / ".env"
    env.write_text(
        "SLACK_USER_PRINCIPAL=UPRINCIPAL1\nEMAIL_PRINCIPAL=principal@x.com\n"
        f"VAULT_ROOT={tmp_path / 'vault'}\nMIRROR_DIR={tmp_path / 'mirrors'}\n",
        encoding="utf-8",
    )
    daydag = tmp_path / "vault" / "DayDAG"
    daydag.mkdir(parents=True)
    (daydag / "Watchlist.md").write_text(
        "# Watchlist\n\n## repos\n\n- ExampleOrg/svc\n\n## jira\n", encoding="utf-8"
    )
    return Identities.from_file(env)


def _tip(repo, git_env):
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, env=git_env, capture_output=True, text=True
    ).stdout.strip()


# -- the windows every loop reads, held once ----------------------------------


def test_the_plan_and_the_consumer_ask_for_the_same_windows(identities):
    """#109: one function, called from both sides of the seam."""
    day = FRIDAY.astimezone(recipes.PACIFIC).date()
    for loop in run.LOOPS:
        planned = [
            s.detail["day"]
            for s in run.plan(loop, now=FRIDAY, identities=identities).steps
            if s.source == "calendar"
        ]
        expected = [str(w.day) for w in recipes.loop_windows(loop, day)]
        assert planned == expected, loop


def test_loop_windows_are_one_day_each_and_the_right_days():
    day = FRIDAY.astimezone(recipes.PACIFIC).date()
    assert [str(w.day) for w in recipes.loop_windows("morning", day)] == ["2026-09-18"]
    assert [str(w.day) for w in recipes.loop_windows("eod", day)] == ["2026-09-19"]
    week = [str(w.day) for w in recipes.loop_windows("week-ahead", day)]
    assert week[0] == "2026-09-21" and len(week) == 7
    assert len(recipes.loop_windows("prep", day, selector="finance")) == recipes.PREP_HORIZON_DAYS
    assert recipes.loop_windows("chase", day) == []


# -- the cursor store ---------------------------------------------------------


def test_a_cursor_round_trips_and_first_sight_is_none(tmp_path):
    log = EventLog.open(tmp_path / "events.db")

    assert log.last_cursor("org/repo") is None
    log.record_cursor("org/repo", "abc123")
    log.record_cursor("org/repo", "def456")

    assert log.last_cursor("org/repo") == "def456", "the newest cursor wins"
    assert log.last_cursor("org/other") is None


# -- the CLI path -------------------------------------------------------------


def test_build_pulse_reads_on_from_the_stored_cursor(identities, fake_repo, git_env, tmp_path):
    """Run one: first sight, nothing reported, cursor stored at the tip.
    Run two, after a new landing: exactly that landing, and the cursor moves."""
    import subprocess

    log = EventLog.open(tmp_path / "events.db")
    url_for = lambda _repo: str(fake_repo)  # noqa: E731

    pulse, report = run.build_pulse(identities, log, url_for=url_for)
    assert pulse.render() == "", "first sight is not news"
    run.store_cursors(log, report)
    assert log.last_cursor("ExampleOrg/svc") == _tip(fake_repo, git_env)

    (fake_repo / "f").write_text("414")
    subprocess.run(["git", "add", "-A"], cwd=fake_repo, env=git_env, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "Merge PR #414"], cwd=fake_repo, env=git_env, check=True
    )

    pulse, report = run.build_pulse(identities, log, url_for=url_for)
    text = pulse.render()
    assert "Merge PR #414" in text and "#413" not in text, text
    run.store_cursors(log, report)
    assert log.last_cursor("ExampleOrg/svc") == _tip(fake_repo, git_env)


def test_render_ship_with_a_built_pulse_is_the_shipping_block(identities, fake_repo, tmp_path):
    log = EventLog.open(tmp_path / "events.db")
    pulse, _ = run.build_pulse(identities, log, url_for=lambda _r: str(fake_repo))

    text = run.render("ship", now=FRIDAY, identities=identities, payloads={}, pulse=pulse)

    assert "couldn't check" not in text


def test_main_accepts_mirrors_and_stores_cursors_after_the_render(monkeypatch, tmp_path, capsys):
    """`--mirrors` builds the pulse and, with `--log`, persists what it read to."""
    import io
    import sys

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "SLACK_USER_PRINCIPAL=UPRINCIPAL1\nEMAIL_PRINCIPAL=principal@x.com\n"
        f"VAULT_ROOT={tmp_path / 'vault'}\n",
        encoding="utf-8",
    )
    seen: dict = {}

    class FakePulse:
        def render(self):
            return "- Merge PR #9 (https://example.com/c/9)"

        def items(self):
            return []

    class FakeMirror:
        label, cursor, stale = "ExampleOrg/svc", "abc123", False

    class FakeReport:
        mirrors = (FakeMirror(),)

    def fake_build(identities, log, **_):
        seen["log"] = log
        return FakePulse(), FakeReport()

    monkeypatch.setattr(run, "build_pulse", fake_build)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))

    code = run.main(["render", "ship", "--mirrors", "--log", str(tmp_path / "events.db")])

    assert code == 0
    assert "Merge PR #9" in capsys.readouterr().out
    assert seen["log"] is not None, "the pulse was built without the log the cursors live in"
    assert EventLog.open(tmp_path / "events.db").last_cursor("ExampleOrg/svc") == "abc123"


def test_main_without_mirrors_still_degrades_to_one_line(monkeypatch, tmp_path, capsys):
    import io
    import sys

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "SLACK_USER_PRINCIPAL=UPRINCIPAL1\nEMAIL_PRINCIPAL=principal@x.com\n", encoding="utf-8"
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))

    assert run.main(["render", "ship"]) == 0
    assert "couldn't check" in capsys.readouterr().out
