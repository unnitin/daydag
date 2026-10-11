"""Review distribution - metric 2 of the team's "How we work" doc.

Plan: `docs/how-we-work-skills.md` §4 D2. The doc asks *who is reviewing*, so
unlike coverage this report does name reviewers - by people-directory role
token, never by GitHub login, which is what keeps it safe to mirror into a
vault file. The rest is the three §2 rules a machine can see: risk tier,
stale open PRs, and drafts left past a week.
"""

from datetime import UTC, date, datetime

import pytest

from daydag import team_practices as tp

REPO = "CreateMusicGroup/cmg-sdp-transform"
WEEK_OF = date(2026, 9, 14)
NOW = datetime(2026, 9, 18, 17, 0, tzinfo=UTC)  # a friday

#: Logins are opaque here on purpose - the mapping is the unit under test, and
#: a real one has no business in a public repo.
ROLES = {
    "login-a": "vp-data",
    "login-b": "eng-sr",
    "login-c": "dataeng-1",
    "review-bot": "bot",
}


def reviewed(number=1, *, by=(), paths=("docs/x.md",), draft=False, updated=None, **extra):
    """One record in the shape `gh pr list --json ...,reviews,files` returns.

    ``by`` is a list of (login, days_ago) or plain logins.
    """
    reviews = []
    for entry in by:
        login, ago = entry if isinstance(entry, tuple) else (entry, 0)
        reviews.append(
            {
                "author": {"login": login},
                "submittedAt": _stamp(ago),
                "state": "APPROVED",
            }
        )
    record = {
        "number": number,
        "headRefName": "chore/x",
        "title": "t",
        "body": "",
        "mergedAt": "2026-09-16T10:00:00Z",
        "isDraft": draft,
        "createdAt": _stamp(30),
        "updatedAt": updated or _stamp(0),
        "reviews": reviews,
        "files": [{"path": path} for path in paths],
        **extra,
    }
    return tp.PullRequest.from_record(record, repo=REPO)


def _stamp(days_ago):
    from datetime import timedelta

    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------
# reviewers, by role token
# --------------------------------------------------------------------------


def test_reviewers_are_counted_by_role_token():
    prs = [
        reviewed(1, by=["login-a", "login-b"]),
        reviewed(2, by=["login-a"]),
        reviewed(3, by=["login-a", "review-bot"]),
    ]
    report = tp.reviews(prs, [], repo=REPO, week_of=WEEK_OF, roles=ROLES, now=NOW)
    assert report.counts == (("vp-data", 3), ("eng-sr", 1))
    assert report.bot_reviewed == 1


@pytest.mark.guardrail
def test_a_github_login_never_reaches_the_render():
    """The doc asks who reviews; a public repo is not where logins belong."""
    report = tp.reviews(
        [reviewed(1, by=["login-a"])], [], repo=REPO, week_of=WEEK_OF, roles=ROLES, now=NOW
    )
    text = report.render()
    assert "login-a" not in text
    assert "vp-data" in text


def test_a_reviewer_with_no_directory_entry_is_counted_but_not_named():
    """§7's gap, surfaced rather than papered over: the directory has no login field yet."""
    report = tp.reviews(
        [reviewed(1, by=["someone-unmapped"])],
        [],
        repo=REPO,
        week_of=WEEK_OF,
        roles=ROLES,
        now=NOW,
    )
    assert report.unmapped == 1
    text = report.render()
    assert "someone-unmapped" not in text
    assert "1 reviewer has no directory entry" in text


def test_a_self_review_is_not_a_review():
    prs = [reviewed(1, by=["login-a"], author={"login": "login-a"})]
    report = tp.reviews(prs, [], repo=REPO, week_of=WEEK_OF, roles=ROLES, now=NOW)
    assert report.counts == ()


def test_the_same_reviewer_twice_on_one_pr_counts_once():
    prs = [reviewed(1, by=["login-a", "login-a"])]
    report = tp.reviews(prs, [], repo=REPO, week_of=WEEK_OF, roles=ROLES, now=NOW)
    assert report.counts == (("vp-data", 1),)


def test_concentration_is_the_top_reviewers_share_of_what_merged():
    prs = [reviewed(n, by=["login-a"]) for n in (1, 2, 3)] + [reviewed(4, by=["login-b"])]
    report = tp.reviews(prs, [], repo=REPO, week_of=WEEK_OF, roles=ROLES, now=NOW)
    assert report.concentration == ("vp-data", 75)
    assert "🔴 vp-data reviewed 75%" in report.render()


def test_no_reviews_at_all_is_not_a_crash():
    report = tp.reviews([], [], repo=REPO, week_of=WEEK_OF, roles=ROLES, now=NOW)
    assert report.concentration is None
    assert "nothing merged" in report.render()


# --------------------------------------------------------------------------
# risk tier
# --------------------------------------------------------------------------


RULES = tp.RiskRules(human_required=("orchestration/*", "*schema*"), bot_enough=("docs/*", "*.md"))


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (("orchestration/run_all.yml",), "human"),
        (("nodes/a/schema.yml",), "human"),
        (("docs/tables.md",), "bot"),
        (("README.md",), "bot"),
        (("docs/tables.md", "orchestration/run_all.yml"), "human"),
        (("some/unlisted/file.py",), "human"),
        ((), "human"),
    ],
)
def test_risk_tier_puts_anything_unsure_in_the_first_bucket(paths, expected):
    """The doc's own tie-break. An unlisted path is not a safe path."""
    assert tp.risk_tier(paths, RULES) == expected


def test_a_human_required_pr_merged_on_bot_review_alone_is_reported():
    prs = [
        reviewed(1, by=["review-bot"], paths=("orchestration/run_all.yml",)),
        reviewed(2, by=["review-bot"], paths=("docs/x.md",)),
        reviewed(3, by=["review-bot", "login-a"], paths=("orchestration/run_all.yml",)),
    ]
    report = tp.reviews(prs, [], repo=REPO, week_of=WEEK_OF, roles=ROLES, rules=RULES, now=NOW)
    assert report.human_required_on_bot_only == (1,)
    assert "#1" in report.render()


def test_risk_rules_load_from_the_shipped_file():
    rules = tp.load_risk_rules()
    assert "orchestration/*" in rules.human_required
    assert "docs/*" in rules.bot_enough
    assert tp.risk_tier(("orchestration/run_all.yml",), rules) == "human"


# --------------------------------------------------------------------------
# open PRs: the two rules with a clock
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2026, 9, 14), date(2026, 9, 16), 2),  # mon -> wed
        (date(2026, 9, 18), date(2026, 9, 21), 1),  # fri -> mon, the weekend is not idle time
        (date(2026, 9, 19), date(2026, 9, 20), 0),  # sat -> sun
        (date(2026, 9, 16), date(2026, 9, 16), 0),
    ],
)
def test_business_days_skip_the_weekend(start, end, expected):
    assert tp.business_days(start, end) == expected


def test_an_open_pr_idle_two_business_days_is_stale():
    """SPEC 3.7's clock, applied to a review instead of an ask."""
    openers = [
        reviewed(10, by=[("login-a", 5)], updated=_stamp(5)),
        reviewed(11, by=[("login-a", 1)], updated=_stamp(1)),
    ]
    report = tp.reviews([], openers, repo=REPO, week_of=WEEK_OF, roles=ROLES, now=NOW)
    assert [number for number, _ in report.stale] == [10]
    assert "#10" in report.render()


def test_a_bot_review_does_not_reset_the_stale_clock():
    """A bot approving is not the human review the doc asks for."""
    openers = [reviewed(12, by=[("review-bot", 0)], updated=_stamp(0))]
    report = tp.reviews([], openers, repo=REPO, week_of=WEEK_OF, roles=ROLES, now=NOW)
    assert [number for number, _ in report.stale] == [12]


def test_drafts_past_a_week_are_named_with_their_age():
    openers = [reviewed(20, draft=True), reviewed(21, draft=True, **{"createdAt": _stamp(2)})]
    report = tp.reviews([], openers, repo=REPO, week_of=WEEK_OF, roles=ROLES, now=NOW)
    assert report.old_drafts == ((20, 30),)
    assert "#20 30d" in report.render()


def test_a_draft_is_not_also_reported_as_stale():
    openers = [reviewed(20, draft=True)]
    report = tp.reviews([], openers, repo=REPO, week_of=WEEK_OF, roles=ROLES, now=NOW)
    assert report.stale == ()


# --------------------------------------------------------------------------
# the event log
# --------------------------------------------------------------------------


def test_rows_are_one_per_role_token_per_week():
    prs = [reviewed(1, by=["login-a", "login-b"]), reviewed(2, by=["login-a"])]
    rows = tp.reviews(prs, [], repo=REPO, week_of=WEEK_OF, roles=ROLES, now=NOW).rows()
    assert rows == [
        {"repo": REPO, "week_of": "2026-09-14", "reviewer": "vp-data", "reviewed": 2},
        {"repo": REPO, "week_of": "2026-09-14", "reviewer": "eng-sr", "reviewed": 1},
    ]
