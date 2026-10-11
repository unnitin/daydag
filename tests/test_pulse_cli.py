"""The pulse reaches the CLI, and reads on from where it left off (#138).

`render` always accepted a pulse; `main` never built one, so `ship` degraded
on every real run and the shipping sections never rendered. And the cursor
each mirror advanced was returned and thrown away, so even a built pulse
started at first sight and reported a quiet day forever.
"""

import io
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from daydag import run
from daydag.config import Identities
from daydag.state import EventLog

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
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, env=git_env, capture_output=True, text=True
    ).stdout.strip()


# -- the cursor store ---------------------------------------------------------


def test_a_cursor_round_trips_and_first_sight_is_none(tmp_path):
    log = EventLog.open(tmp_path / "events.db")

    assert log.last_cursor("org/repo") is None
    log.record_cursor("org/repo", "abc123")
    log.record_cursor("org/repo", "def456")

    assert log.last_cursor("org/repo") == "def456", "the newest cursor wins"
    assert log.last_cursor("org/other") is None


def test_a_torn_cursor_row_is_skipped_not_raised(tmp_path):
    """The log's torn-row rule (`_rows`), for the one read that bypasses it."""
    log = EventLog.open(tmp_path / "events.db")
    log.record_cursor("org/repo", "abc123")
    log._db.execute(
        "INSERT INTO events (kind, sensitivity, payload) VALUES ('mirror_cursor', 'normal', '{')"
    )

    assert log.last_cursor("org/repo") == "abc123"


# -- the CLI path -------------------------------------------------------------


def test_build_pulse_reads_on_from_the_stored_cursor(identities, fake_repo, git_env, tmp_path):
    """Run one: first sight, nothing reported, cursor stored at the tip.
    Run two, after a new landing: exactly that landing, and the cursor moves."""
    log = EventLog.open(tmp_path / "events.db")
    url_for = lambda _repo: str(fake_repo)  # noqa: E731

    pulse, report = run.build_pulse(identities, log, url_for=url_for)
    assert "#413" not in pulse.render(), "first sight is not news"
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


def test_render_ship_with_a_built_pulse_is_not_the_degrade_line(identities, fake_repo, tmp_path):
    log = EventLog.open(tmp_path / "events.db")
    pulse, _ = run.build_pulse(identities, log, url_for=lambda _r: str(fake_repo))

    text = run.render("ship", now=FRIDAY, identities=identities, payloads={}, pulse=pulse)

    assert "couldn't check" not in text


def test_build_pulse_without_a_vault_is_a_run_error(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        f"SLACK_USER_PRINCIPAL=UPRINCIPAL1\nMIRROR_DIR={tmp_path / 'mirrors'}\n", encoding="utf-8"
    )

    with pytest.raises(run.RunError, match="Watchlist"):
        run.build_pulse(Identities.from_file(env), None)


def _env(tmp_path):
    (tmp_path / ".env").write_text(
        "SLACK_USER_PRINCIPAL=UPRINCIPAL1\nEMAIL_PRINCIPAL=principal@x.com\n"
        f"VAULT_ROOT={tmp_path / 'vault'}\n",
        encoding="utf-8",
    )


def test_main_accepts_mirrors_and_stores_cursors_after_the_render(monkeypatch, tmp_path, capsys):
    """`--mirrors` builds the pulse and, with `--log`, persists what it read to."""
    monkeypatch.chdir(tmp_path)
    _env(tmp_path)
    seen: dict = {}

    class FakePulse:
        def render(self):
            return "- Merge PR #9 (https://example.com/c/9)"

    class FakeMirror:
        label, cursor, stale, read = "ExampleOrg/svc", "abc123", False, "read"

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


def test_a_stale_mirror_does_not_store_its_cursor(monkeypatch, tmp_path, capsys):
    """A stale mirror contributed no items, so its cursor did not earn a move."""
    monkeypatch.chdir(tmp_path)
    _env(tmp_path)

    class FakePulse:
        def render(self):
            return ""

    class FakeMirror:
        # Stale mirrors are skipped by `Pulse.items`, so never read.
        label, cursor, stale, read = "ExampleOrg/svc", "abc123", True, None

    class FakeReport:
        mirrors = (FakeMirror(),)

    monkeypatch.setattr(run, "build_pulse", lambda *_a, **_k: (FakePulse(), FakeReport()))
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))

    run.main(["render", "ship", "--mirrors", "--log", str(tmp_path / "events.db")])

    assert EventLog.open(tmp_path / "events.db").last_cursor("ExampleOrg/svc") is None


def test_a_pulse_that_cannot_be_built_degrades_to_one_line(monkeypatch, tmp_path, capsys):
    """Guardrail 6: a mirror sync that fails is one line, not a dead push."""
    monkeypatch.chdir(tmp_path)
    _env(tmp_path)

    def broken(*_a, **_k):
        raise run.RunError("no vault is configured")

    monkeypatch.setattr(run, "build_pulse", broken)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))

    assert run.main(["render", "ship", "--mirrors"]) == 0
    assert "couldn't check" in capsys.readouterr().out


def test_main_without_mirrors_still_degrades_to_one_line(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "SLACK_USER_PRINCIPAL=UPRINCIPAL1\nEMAIL_PRINCIPAL=principal@x.com\n", encoding="utf-8"
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))

    assert run.main(["render", "ship"]) == 0
    assert "couldn't check" in capsys.readouterr().out


# -- what a cursor is allowed to be ---------------------------------------------


def _watch(tmp_path, line):
    (tmp_path / "vault" / "DayDAG" / "Watchlist.md").write_text(
        f"# Watchlist\n\n## repos\n\n- {line}\n\n## jira\n", encoding="utf-8"
    )


def test_a_wiki_mirror_reads_on_from_its_own_stored_cursor(identities, tmp_path, monkeypatch):
    """A repo marked `wiki` is two mirrors. `store_cursors` stored the wiki's
    cursor under its own label every run, and `build_pulse` only looked up the
    repos' - so the wiki started at first sight every run and never reported a
    landing."""
    _watch(tmp_path, "ExampleOrg/svc · wiki")
    log = EventLog.open(tmp_path / "events.db")
    log.record_cursor("ExampleOrg/svc", "aaa")
    log.record_cursor("ExampleOrg/svc.wiki", "bbb")
    seen: dict = {}

    def capture(self, watched, cursors=None):
        seen.update(cursors or {})
        return run.SyncReport()

    monkeypatch.setattr(run.MirrorStore, "sync", capture)
    run.build_pulse(identities, log)

    assert seen == {"ExampleOrg/svc": "aaa", "ExampleOrg/svc.wiki": "bbb"}


def test_a_cursor_that_stopped_resolving_starts_over_rather_than_failing_forever(
    identities, fake_repo, tmp_path
):
    """An upstream force-push leaves the stored sha unreachable. The read
    fails, which is one "could not read" line - but storing that same sha back
    made it fail on every run after. It goes back to first sight instead."""
    log = EventLog.open(tmp_path / "events.db")
    url_for = lambda _repo: str(fake_repo)  # noqa: E731
    log.record_cursor("ExampleOrg/svc", "0" * 39 + "1")

    pulse, report = run.build_pulse(identities, log, url_for=url_for)
    assert "could not read" in pulse.render()
    run.store_cursors(log, report)

    pulse, report = run.build_pulse(identities, log, url_for=url_for)
    assert "could not read" not in pulse.render(), "the dead cursor came back"


def test_a_mirror_nobody_read_stores_no_cursor(identities, fake_repo, git_env, tmp_path):
    """`--mirrors` on a loop that never lists the pulse's items leaves each
    mirror holding its starting value - for a `branch:` row, the branch NAME.
    Stored, `dev` came back as a cursor, `dev..tip` was empty, and every
    landing since was lost. Only a cursor a read produced is stored."""
    subprocess.run(["git", "branch", "dev"], cwd=fake_repo, env=git_env, check=True)
    _watch(tmp_path, "ExampleOrg/svc · branch: dev")
    log = EventLog.open(tmp_path / "events.db")

    _pulse, report = run.build_pulse(identities, log, url_for=lambda _r: str(fake_repo))
    run.store_cursors(log, report)

    assert log.last_cursor("ExampleOrg/svc") is None
