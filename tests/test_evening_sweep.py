"""The evening sweep: what `plan eod` asks for so the wrap can see what moved (#141, #167).

The evening loops used to fetch the MORNING brief's payloads - Slack messages
that @-mention him and Gemini notes. Neither can carry the evidence the wrap
needs: his own reply to the Sponsor on an email thread, a doc he sent in a DM,
a merged PR, a ticket that changed column. These tests pin the widened sweep
as recipes and as plan steps. Every one is bounded, because an unbounded query
here is not a degraded read, it is an overflow that returns nothing at all.

Synthetic ids throughout - this repo is public (CONTRIBUTING).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from daydag import recipes, run
from daydag.recipes import RecipeError
from daydag.state import StateFolder

PT = ZoneInfo("America/Los_Angeles")
DAY = date(2026, 9, 25)
FRIDAY_EVENING = datetime(2026, 9, 25, 16, 30, tzinfo=PT)
PRINCIPAL = "UPRINCIPAL1"
CHANNEL = "CPODCHANNEL"
REPOS = ("ExampleOrg/service-a", "ExampleOrg/service-b")

WATCHLIST = """# Watchlist

## repos

- ExampleOrg/service-a · the service · active
- ExampleOrg/service-b · branch:dev · the other one

## jira

- CDI · live board
- TDE · data platform

## channels

- CPODCHANNEL  #pod-channel
- CLEADCHANNEL  #leads
- not a channel line
"""


# --------------------------------------------------------------------------
# recipes
# --------------------------------------------------------------------------


def test_his_own_slack_messages_are_one_day_scoped_by_id():
    query = recipes.slack_sent_on(DAY, principal=PRINCIPAL)

    assert f"from:<@{PRINCIPAL}>" in query
    assert "after:2026-09-24" in query and "before:2026-09-26" in query
    assert "sort:timestamp" in query


@pytest.mark.guardrail
def test_a_display_name_is_refused_not_searched():
    """`from:@someone` silently matches nothing - the brief then reports a
    silence it never checked."""
    with pytest.raises(RecipeError):
        recipes.slack_sent_on(DAY, principal="nitin")


def test_channel_traffic_is_one_channel_one_day():
    query = recipes.slack_channel_on(DAY, channel=CHANNEL)

    assert f"in:<#{CHANNEL}>" in query
    assert "after:2026-09-24" in query and "before:2026-09-26" in query


def test_dms_to_him_exclude_his_own_side():
    """His own side is `slack_sent_on`. Fetching it twice would put every
    message he sent into both payloads and count it as two pieces of evidence."""
    query = recipes.slack_dms_on(DAY, principal=PRINCIPAL)

    assert f"-from:<@{PRINCIPAL}>" in query
    assert "after:2026-09-24" in query and "before:2026-09-26" in query


def test_his_sent_mail_is_one_day_and_no_address():
    """`in:sent` rather than `from:<address>` - the address is personal data
    and this repo is public, and the mailbox already knows whose it is."""
    query = recipes.gmail_sent_on(DAY)

    assert query == "in:sent after:2026/09/25 before:2026/09/26"


def test_jira_movement_is_status_or_assignee_changes_in_the_window():
    params = recipes.jira_moved_on("CDI", DAY)

    jql = params["jql"]
    assert jql.startswith('project = "CDI" AND (')
    assert 'status CHANGED DURING ("2026-09-25", "2026-09-26")' in jql
    assert 'assignee CHANGED DURING ("2026-09-25", "2026-09-26")' in jql


@pytest.mark.guardrail
def test_jira_movement_is_bounded_in_fields_and_count():
    """Measured 2026-09-25: three projects, nine fields, one day came back at
    81,582 characters and overflowed. One project per call, five fields, and a
    page cap under the size that overflowed."""
    params = recipes.jira_moved_on("CDI", DAY)

    assert params["maxResults"] <= recipes.JIRA_MOVED_MAX_RESULTS <= 20
    assert "*" not in "".join(params["fields"])
    assert set(params["fields"]) <= set(recipes.JIRA_FIELDS)


def test_jira_movement_refuses_a_key_that_is_not_one():
    """The watchlist is hand-edited; a key is concatenated into JQL."""
    with pytest.raises(RecipeError):
        recipes.jira_moved_on('CDI" OR project is not EMPTY', DAY)


def test_github_merges_are_one_search_across_every_watched_repo():
    argv = recipes.gh_merged_on(REPOS, DAY)

    assert argv[:3] == ["gh", "search", "prs"]
    assert "--merged-at=2026-09-25" in argv
    assert argv.count("--repo") == 2
    assert "--limit" in argv and int(argv[argv.index("--limit") + 1]) <= recipes.GH_LIMIT_CAP


def test_github_review_state_and_closed_issues_are_their_own_searches():
    approved = recipes.gh_reviewed_on(REPOS, DAY, review="approved")
    changes = recipes.gh_reviewed_on(REPOS, DAY, review="changes_requested")
    closed = recipes.gh_closed_issues_on(REPOS, DAY)

    assert "--review=approved" in approved and "--updated=2026-09-25" in approved
    assert "--review=changes_requested" in changes
    assert closed[:3] == ["gh", "search", "issues"] and "--closed=2026-09-25" in closed


def test_a_review_state_that_is_not_one_is_refused():
    with pytest.raises(RecipeError):
        recipes.gh_reviewed_on(REPOS, DAY, review="approved; rm -rf")


@pytest.mark.guardrail
def test_no_github_movement_recipe_is_a_write():
    """SPEC section 4: GitHub is read-only, and guardrail 1 keeps it that way."""
    for argv in (
        recipes.gh_merged_on(REPOS, DAY),
        recipes.gh_reviewed_on(REPOS, DAY, review="approved"),
        recipes.gh_closed_issues_on(REPOS, DAY),
    ):
        assert not recipes.GH_WRITE_VERBS & set(argv), argv


def test_github_with_no_repos_is_refused_rather_than_searching_everything():
    """`gh search prs --merged-at=DAY` with no `--repo` searches all of GitHub."""
    with pytest.raises(RecipeError):
        recipes.gh_merged_on([], DAY)


def test_watched_channels_are_read_from_the_channels_block_only():
    assert recipes.watched_channels(WATCHLIST) == ["CPODCHANNEL", "CLEADCHANNEL"]


def test_no_channels_block_is_no_channels():
    assert recipes.watched_channels("# Watchlist\n\n## repos\n\n- a/b\n") == []


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault" / "Create Music Group"
    folder = StateFolder.create(root / "DayDAG")
    folder.watchlist_path.write_text(WATCHLIST, encoding="utf-8")
    return root


@pytest.fixture
def ids(vault):
    return {"SLACK_USER_PRINCIPAL": PRINCIPAL, "VAULT_ROOT": str(vault)}


def _steps(ids, loop="eod"):
    return [step.to_dict() for step in run.plan(loop, now=FRIDAY_EVENING, identities=ids).steps]


def _by_source(steps, source):
    return [step for step in steps if step["source"] == source]


def test_plan_eod_asks_for_his_own_slack_and_mail(ids):
    steps = _steps(ids)

    (sent,) = _by_source(steps, "slack_sent")
    assert sent["detail"]["query"] == recipes.slack_sent_on(DAY, principal=PRINCIPAL)
    (mail,) = _by_source(steps, "gmail_sent")
    assert mail["detail"]["query"] == recipes.gmail_sent_on(DAY)


def test_plan_eod_sweeps_each_watched_channel_and_his_dms(ids):
    """#141: the mention query never sees "sent the fruits list to finance"
    posted in a channel, or a DM - a DM does not @-mention him."""
    sweep = _by_source(_steps(ids), "slack_sweep")

    queries = [step["detail"]["query"] for step in sweep]
    assert recipes.slack_channel_on(DAY, channel="CPODCHANNEL") in queries
    assert recipes.slack_channel_on(DAY, channel="CLEADCHANNEL") in queries
    dms = [step for step in sweep if step["detail"].get("channel_types") == "im,mpim"]
    assert dms and dms[0]["detail"]["query"] == recipes.slack_dms_on(DAY, principal=PRINCIPAL)
    assert all(step["detail"]["max_pages"] <= 3 for step in sweep)


def test_plan_eod_asks_jira_once_per_watched_project(ids):
    jira = _by_source(_steps(ids), "jira")

    jqls = [step["detail"]["jql"] for step in jira]
    assert len(jqls) == 2
    assert any('project = "CDI"' in jql for jql in jqls)
    assert any('project = "TDE"' in jql for jql in jqls)


def test_plan_eod_falls_back_to_the_data_team_board_without_a_watchlist(tmp_path):
    ids = {"SLACK_USER_PRINCIPAL": PRINCIPAL, "VAULT_ROOT": str(tmp_path / "nowhere")}

    (jira,) = _by_source(_steps(ids), "jira")

    assert 'project = "CDI"' in jira["detail"]["jql"]


def test_plan_eod_asks_github_for_every_watched_repo(ids):
    github = _by_source(_steps(ids), "github")

    kinds = sorted(step["detail"]["kind"] for step in github)
    assert kinds == ["approved", "changes_requested", "issue_closed", "merged"]
    for step in github:
        argv = step["detail"]["argv"]
        assert "ExampleOrg/service-a" in argv and "ExampleOrg/service-b" in argv


def test_plan_eod_without_watched_repos_says_so_and_asks_nothing(tmp_path):
    ids = {"SLACK_USER_PRINCIPAL": PRINCIPAL, "VAULT_ROOT": str(tmp_path / "nowhere")}

    github = _by_source(_steps(ids), "github")

    assert len(github) == 1 and "argv" not in github[0]["detail"]
    assert "leave `github` out" in github[0]["how"]


def test_the_morning_plan_is_unchanged(ids):
    """#141's own line: this is an evening shape, not a replacement."""
    sources = {step["source"] for step in _steps(ids, loop="morning")}

    assert not sources & {"slack_sent", "slack_sweep", "gmail_sent", "jira", "github"}


# --------------------------------------------------------------------------
# render: what the wrap does with the widened payloads
# --------------------------------------------------------------------------

EVENING_KEYS = ("slack_sent", "gmail_sent", "slack_sweep", "jira", "github")

STATE = """# State

## Chase list

- **Sponsor → Owner-A · the youtube question that gates the roadshow** 🔴
\t- source: gmail thread `1a0d000000000f44`
- **Partner-B → you · two actions out of the 1:1**
\t- the repo is `ExampleOrg/service-a`
"""


def _payloads(**overrides):
    base = {"calendar": [], "slack": [], "gmail": [], "vault": None, "vault_notes": {}}
    base.update({key: [] for key in EVENING_KEYS})
    base.update(overrides)
    return base


def _render(ids, vault, payloads):
    (vault / "DayDAG" / "State.md").write_text(STATE, encoding="utf-8")
    return run.render("eod", now=FRIDAY_EVENING, identities=ids, payloads=payloads)


def test_each_evening_source_left_out_is_one_couldnt_check_line(ids, vault):
    payloads = _payloads()
    for key in EVENING_KEYS:
        del payloads[key]

    text = _render(ids, vault, payloads)

    for name in ("his slack messages", "his sent mail", "slack channels + dms", "jira", "github"):
        assert f"- couldn't check {name}" in text, text


def test_a_source_that_came_back_the_wrong_shape_is_unchecked_too(ids, vault):
    text = _render(ids, vault, _payloads(jira={"error": "overflow"}))

    assert "- couldn't check jira" in text, text


def test_every_evening_source_ran_and_found_nothing_says_nothing(ids, vault):
    text = _render(ids, vault, _payloads())

    assert "couldn't check" not in text.replace("couldn't check the weekly note", ""), text
    assert "looks closed" not in text and "board + repos" not in text, text


def test_the_0925_shape_renders_both_as_looks_closed_and_the_merge_as_moved(ids, vault):
    """#167's done-when, on synthetic stand-ins: his email reply on the
    sponsor's thread and the link he dropped in the partner's DM both render
    as proposed-closed with their permalinks; a merged PR on a watched repo
    renders as moved with its link. Nothing adds to `closed`."""
    payloads = _payloads(
        gmail_sent=[
            {
                "threadId": "1a0d000000000f44",
                "subject": "Re: Performance Domain",
                "snippet": "catching up w/ the engineer. 2 factors influence the deviation",
                "permalink": "https://mail.example.com/#all/1a0d000000000f44",
            }
        ],
        slack_sent=[
            {
                "text": "yo, finished some table setting - https://example.com/doc",
                "ts": "1790380022.100000",
                "channel": "DPARTNER01",
                "channel_name": "DM with Partner-B Park, Principal",
                "permalink": "https://example.slack.com/archives/DPARTNER01/p1790380022100000",
            }
        ],
        github=[
            {
                "kind": "merged",
                "number": 9,
                "title": "fix the thing",
                "url": "https://github.com/ExampleOrg/service-b/pull/9",
                "repository": {"nameWithOwner": "ExampleOrg/service-b"},
            }
        ],
    )

    text = _render(ids, vault, payloads)

    assert text.startswith("wrap: 0 closed, 1 moved, 2 to confirm"), text
    assert "looks closed - confirm (2)" in text, text
    assert "(https://mail.example.com/#all/1a0d000000000f44) · proposed: answered" in text, text
    assert (
        "(https://example.slack.com/archives/DPARTNER01/p1790380022100000) · proposed: sent" in text
    )
    assert "(https://github.com/ExampleOrg/service-b/pull/9)" in text, text
