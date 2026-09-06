"""Engineering pulse: git mirrors for history, API for state (SPEC 3.7).

The rules here are as load-bearing as the features - rule 1 ("state changes,
not activity") is what keeps this from reading as a productivity metric on a
named engineer.
"""

import pytest

from daydag.pulse import Mirror, Pulse, PulseError


def test_merges_since_last_run_uses_the_stored_cursor(fake_repo):
    m = Mirror.attach(fake_repo, cursor="HEAD~2")
    merged = m.merges_since_cursor()
    assert [c.title for c in merged] == ["Merge PR #412", "Merge PR #413"]


def test_cursor_advances_only_after_a_successful_read(fake_repo):
    m = Mirror.attach(fake_repo, cursor="HEAD~2")
    before = m.cursor
    with pytest.raises(PulseError):
        m.merges_since_cursor(_fail=True)
    assert m.cursor == before, "cursor advanced past commits that were never reported"


@pytest.mark.guardrail
def test_stale_mirror_reports_as_of_rather_than_asserting(fake_repo):
    """Guardrail 6. Reporting a stale mirror as current is how this lies."""
    m = Mirror.attach(fake_repo, cursor="HEAD~2")
    m.mark_fetch_failed()
    report = Pulse(mirrors=[m]).render()
    assert "as of" in report
    assert "no updates" not in report


@pytest.mark.guardrail
@pytest.mark.parametrize("forbidden", ["commits", "lines changed", "+/-", "contributions"])
def test_never_emits_activity_metrics(fake_repo, forbidden):
    """SPEC 3.7 rule 1: the unit is 'did the thing he's tracking move'."""
    report = Pulse(mirrors=[Mirror.attach(fake_repo, cursor="HEAD~2")]).render()
    assert forbidden not in report.lower()


def test_silence_produces_no_shipping_block(fake_repo):
    """SPEC 3.7 rule 3: a quiet day produces nothing, not 'no updates'."""
    m = Mirror.attach(fake_repo, cursor="HEAD")
    assert Pulse(mirrors=[m]).render() == ""


def test_joins_slack_pr_and_note_on_the_ticket_key():
    """The valuable part: resolve an ask to a ticket, a PR and a status."""
    p = Pulse(mirrors=[])
    p.observe_slack("jasmeet said he'd do DATA-812")
    p.observe_pr(title="DATA-812 consolidate compute engines", number=412, state="merged")
    joined = p.by_ticket("DATA-812")
    assert joined.pr_number == 412 and joined.mentioned_in_slack


@pytest.mark.guardrail
def test_evidence_marks_movement_but_never_closes_a_loop():
    """SPEC 3.7: merged is not the same as what was asked for."""
    p = Pulse(mirrors=[])
    p.observe_pr(title="DATA-812 consolidate", number=412, state="merged")
    loop = p.apply_evidence({"key": "DATA-812", "status": "open"})
    assert loop["status"] == "open"
    assert loop["evidence_of_movement"] is True


def test_every_reported_item_carries_a_permalink(fake_repo):
    p = Pulse(mirrors=[Mirror.attach(fake_repo, cursor="HEAD~2")])
    assert all(item.permalink for item in p.items())


@pytest.mark.guardrail
def test_a_mirror_reads_its_own_repo_whatever_the_environment_says(fake_repo, monkeypatch):
    """Guardrail 6 again: a mirror that reads the wrong repo reports confidently.

    git exports GIT_DIR into hooks, and a scheduled loop can inherit it from
    anywhere. Inherited, it overrides cwd and the mirror silently answers about
    a different repository.
    """
    monkeypatch.setenv("GIT_DIR", str(fake_repo.parent / "not-the-mirror"))
    monkeypatch.setenv("GIT_WORK_TREE", str(fake_repo.parent))
    m = Mirror.attach(fake_repo, cursor="HEAD~2")
    assert [c.title for c in m.merges_since_cursor()] == ["Merge PR #412", "Merge PR #413"]
