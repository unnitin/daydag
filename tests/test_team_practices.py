"""Ticket coverage - metric 1 of the team's "How we work" doc.

The plan is `docs/how-we-work-skills.md` §4 D1. These pin the two things the
doc's own wording leaves a machine room to get wrong: which PRs count as
keyed, and what a public report is allowed to say about the people who wrote
them.
"""

from datetime import UTC, date, datetime

import pytest

from daydag import team_practices as tp
from daydag.brief import unsourced_claims
from daydag.pulse import FORBIDDEN_IN_OUTPUT

REPO = "CreateMusicGroup/cmg-sdp-transform"
WINDOW = (date(2026, 8, 20), date(2026, 9, 19))


def pr(number=1, *, branch="chore/x", title="t", body="", merged="2026-09-01T10:00:00Z", **extra):
    """One record in the shape `gh pr list --json` hands back."""
    record = {
        "number": number,
        "headRefName": branch,
        "title": title,
        "body": body,
        "mergedAt": merged,
        **extra,
    }
    return tp.PullRequest.from_record(record, repo=REPO)


# --------------------------------------------------------------------------
# the classifier, on the branch shapes the repo actually carries
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"branch": "feat/TDE-591-add-thing"}, tp.Reference.TDE),
        ({"branch": "feature/TDE-653_other_thing"}, tp.Reference.TDE),
        ({"branch": "chore/x", "title": "TDE-12 do the thing"}, tp.Reference.TDE),
        ({"branch": "chore/x", "body": "part of TDE-12"}, tp.Reference.TDE),
        ({"branch": "fix/519-broken-join"}, tp.Reference.GITHUB),
        ({"branch": "docs/302-runbook"}, tp.Reference.GITHUB),
        ({"branch": "chore/x", "body": "see #512"}, tp.Reference.GITHUB),
        ({"branch": "chore/x", "body": "closes #44"}, tp.Reference.GITHUB),
        ({"branch": "chore/x", "title": "Fixes #7"}, tp.Reference.GITHUB),
        ({"branch": "chore/registry-cutover-end"}, tp.Reference.NONE),
        ({"branch": "claude/issue-510-thing"}, tp.Reference.NONE),
    ],
)
def test_classify_reads_branch_title_and_body(kwargs, expected):
    assert tp.classify(pr(**kwargs)) is expected


def test_a_tde_key_outranks_a_github_reference():
    """Jira is the system of record, so a PR carrying both counts once, as TDE."""
    both = pr(branch="feat/TDE-591-x", body="closes #44")
    assert tp.classify(both) is tp.Reference.TDE


@pytest.mark.parametrize("branch", ["claude/issue-510-x", "codex/fix-thing"])
def test_agent_branches_are_flagged_separately(branch):
    """The doc: agent branches are not exempt. Flagged, not excluded."""
    assert tp.is_agent_branch(pr(branch=branch)) is True
    assert tp.is_agent_branch(pr(branch="feat/TDE-1-x")) is False


def test_keys_are_every_tde_reference_found():
    assert tp.keys(pr(branch="feat/TDE-591-x", body="supersedes TDE-12")) == ("TDE-12", "TDE-591")


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------


def a_mixed_window():
    return [
        pr(1, branch="feat/TDE-591-a"),
        pr(2, branch="fix/519-b"),
        pr(3, branch="chore/c"),
        pr(4, branch="claude/issue-510-d"),
    ]


def test_coverage_counts_both_definitions():
    report = tp.coverage(a_mixed_window(), repo=REPO, window=WINDOW)
    assert report.merged == 4
    assert report.tde == 1
    assert report.any_reference == 2
    assert report.uncovered == (3, 4)
    assert report.agent_branches == 1
    assert report.agent_keyed == 0


def test_shares_are_whole_percents_of_what_merged():
    report = tp.coverage(a_mixed_window(), repo=REPO, window=WINDOW)
    assert report.tde_share == 25
    assert report.any_share == 50


def test_an_empty_window_renders_without_dividing_by_zero():
    report = tp.coverage([], repo=REPO, window=WINDOW)
    assert report.tde_share == 0
    assert "nothing merged" in report.render()


def test_every_uncovered_pr_carries_its_link():
    text = tp.coverage(a_mixed_window(), repo=REPO, window=WINDOW).render()
    assert f"https://github.com/{REPO}/pull/3" in text
    assert unsourced_claims(text) == []


def test_jira_confirms_the_keys_it_can_find():
    report = tp.coverage(
        [pr(1, branch="feat/TDE-591-a"), pr(2, branch="feat/TDE-999-b")],
        repo=REPO,
        window=WINDOW,
        confirm_keys=lambda keys: {"TDE-591"},
    )
    assert report.missing_keys == (("TDE-999", 2),)
    assert "TDE-999" in report.render()


def test_jira_down_costs_one_line_and_the_report_still_ships():
    """Guardrail 6. The coverage number does not depend on Jira being up."""

    def boom(keys):
        raise RuntimeError("atlassian timed out")

    report = tp.coverage(
        [pr(1, branch="feat/TDE-591-a")], repo=REPO, window=WINDOW, confirm_keys=boom
    )
    text = report.render()
    assert report.unreachable == ("jira",)
    assert "couldn't check jira" in text
    assert "TDE-keyed" in text


# --------------------------------------------------------------------------
# what a public report may not say
# --------------------------------------------------------------------------


@pytest.mark.guardrail
def test_a_pull_request_has_no_author_field():
    """Structural, not a rule in prose: the report cannot leak what it never reads.

    The doc asks for coverage, not for coverage per person. A field that is
    never parsed cannot reach a render, a channel draft or the event log.
    """
    assert "author" not in tp.PullRequest.__dataclass_fields__


@pytest.mark.guardrail
def test_an_author_on_the_record_never_reaches_the_render():
    records = [pr(1, branch="chore/c", author={"login": "someone-cmg"})]
    text = tp.coverage(records, repo=REPO, window=WINDOW).render()
    assert "someone-cmg" not in text


@pytest.mark.guardrail
def test_the_render_carries_none_of_the_forbidden_vocabulary():
    """`pulse.FORBIDDEN_IN_OUTPUT` - a progress report is not a productivity metric."""
    text = tp.coverage(a_mixed_window(), repo=REPO, window=WINDOW).render()
    assert [word for word in FORBIDDEN_IN_OUTPUT if word in text] == []


def test_rows_for_the_event_log_carry_no_author_either():
    rows = tp.coverage(a_mixed_window(), repo=REPO, window=WINDOW).rows()
    assert len(rows) == 4
    assert all("author" not in row for row in rows)
    assert rows[0] == {
        "number": 1,
        "repo": REPO,
        "merged_at": "2026-09-01T10:00:00+00:00",
        "reference": "tde",
        "key": "TDE-591",
        "agent_branch": False,
    }


# --------------------------------------------------------------------------
# reading what the connector hands back
# --------------------------------------------------------------------------


def test_from_record_derives_the_url_when_the_query_did_not_ask_for_it():
    assert pr(7).url == f"https://github.com/{REPO}/pull/7"


def test_from_record_keeps_the_url_the_query_did_return():
    assert pr(7, url="https://github.example/x/pull/7").url == "https://github.example/x/pull/7"


def test_merged_at_is_read_as_utc():
    assert pr(1, merged="2026-09-01T10:00:00Z").merged_at == datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def test_an_open_pr_has_no_merge_stamp():
    assert pr(1, merged=None).merged_at is None


# --------------------------------------------------------------------------
# what the first real run against the team's repo found
# --------------------------------------------------------------------------


def test_a_long_uncovered_list_defers_to_the_vault_file():
    """43 uncovered PRs is a wall, not a push. The plan puts the full list in the vault."""
    many = [pr(n, branch="chore/x") for n in range(1, 44)]
    text = tp.coverage(many, repo=REPO, window=WINDOW).render()
    assert text.count(f"https://github.com/{REPO}/pull/") == tp.UNCOVERED_IN_PUSH
    assert f"and 31 more ({tp.VAULT_MIRROR})" in text
    assert "no reference at all (43)" in text
    assert unsourced_claims(text) == []


def test_the_push_has_no_blank_heading():
    """The counts sit under the window, not under an empty heading that renders as a gap."""
    text = tp.coverage(a_mixed_window(), repo=REPO, window=WINDOW).render()
    assert "\n\n\n" not in text
    assert text.splitlines()[0] == "ticket coverage - cmg-sdp-transform"
    assert text.splitlines()[2] == "aug 20 → sep 19, 4 merged"
