"""Source recipes: SPEC section 4's prose as literal, testable queries (issue #7).

Every loop needs the same handful of queries, and re-deriving them per loop is
how two loops end up asking slightly different questions of the same source.
These tests pin the shapes. None of them touch a connector - a recipe is a pure
function from parameters to a query string or a parameter dict, which is the
whole point: the thing that must be right is checkable offline.

Three of the shapes here contradict what SPEC section 4 originally said, and the
issue #2 connector audit is why. Each such test names the measurement.
"""

import itertools
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from daydag import recipes
from daydag.config import DEFAULT_TIMEZONE, ConfigError, Identities, timezone_for
from daydag.ledger import title_from_gemini_subject
from daydag.pulse import WatchedRepo
from daydag.recipes import RecipeError

PT = ZoneInfo("America/Los_Angeles")

# Synthetic ids. Real ones live in .env - this repo is public (CONTRIBUTING).
PRINCIPAL = "UPRINCIPAL1"
PEER = "UPEER00001"
CHANNEL = "CPODCHANNEL"


def _seconds(window):
    """Real elapsed time in a day window, which wall-clock arithmetic hides."""
    start = datetime.fromisoformat(window.time_min).astimezone(UTC)
    end = datetime.fromisoformat(window.time_max).astimezone(UTC)
    return (end - start).total_seconds()


# ---------------------------------------------------------------------------
# calendar - day-by-day, confirmed by measurement
# ---------------------------------------------------------------------------


@pytest.mark.guardrail
def test_a_multi_day_range_becomes_one_window_per_day():
    """A 5-day pull returned 156,681 chars and blew the output limit (#2 audit).

    Guardrail 6: the loop degrades honestly or it does not run. A query that
    overflows the connector's output limit does neither - it returns nothing
    useful while looking like it ran, so the brief silently loses a day.
    """
    windows = recipes.calendar_days(date(2026, 9, 7), date(2026, 9, 11))
    assert len(windows) == 5
    assert [w.day for w in windows] == [date(2026, 9, d) for d in (7, 8, 9, 10, 11)]


@pytest.mark.guardrail
def test_each_window_covers_exactly_its_own_local_day():
    """Half-open and contiguous: no event counted twice, none dropped between."""
    windows = recipes.calendar_days(date(2026, 9, 7), date(2026, 9, 9))
    for window in windows:
        start = datetime.fromisoformat(window.time_min)
        assert (start.hour, start.minute, start.second) == (0, 0, 0)
        assert start.date() == window.day
    for earlier, later in itertools.pairwise(windows):
        assert earlier.time_max == later.time_min


def test_windows_follow_dst_rather_than_adding_24_hours():
    """PT days are 23 and 25 hours twice a year. Naive arithmetic loses an hour.

    The lost hour is not hypothetical: a fixed +24h window on the fall-back day
    ends an hour early, dropping whatever sits in it.
    """
    fall_back = recipes.calendar_day(date(2026, 11, 1))
    spring_forward = recipes.calendar_day(date(2026, 3, 8))
    assert _seconds(fall_back) == 25 * 3600
    assert _seconds(spring_forward) == 23 * 3600


def test_window_is_rfc3339_with_an_explicit_offset():
    window = recipes.calendar_day(date(2026, 9, 8))
    assert window.time_min == "2026-09-08T00:00:00-07:00"
    assert window.time_max == "2026-09-09T00:00:00-07:00"
    assert window.params == {
        "time_min": "2026-09-08T00:00:00-07:00",
        "time_max": "2026-09-09T00:00:00-07:00",
    }


def test_one_day_range_is_one_window():
    assert len(recipes.calendar_days(date(2026, 9, 8), date(2026, 9, 8))) == 1


def test_a_backwards_range_is_refused():
    with pytest.raises(RecipeError, match="before"):
        recipes.calendar_days(date(2026, 9, 11), date(2026, 9, 7))


# ---------------------------------------------------------------------------
# slack - person-scoped, id-only, explicitly ordered
# ---------------------------------------------------------------------------


@pytest.mark.guardrail
@pytest.mark.parametrize("sender", ["@miko", "miko", "<@miko>", "Miko Smith"])
def test_a_display_name_is_refused_rather_than_silently_matching_nothing(sender):
    """`from:@name` returns zero results and no error - the worst failure mode.

    Guardrail 3: if the agent cannot source a claim it says so. An empty result
    set from a malformed scope is indistinguishable from "nobody said anything",
    so the brief would assert a silence it never actually checked.
    """
    with pytest.raises(RecipeError, match="id"):
        recipes.slack_search(sender=sender)


def test_person_scope_uses_the_angle_bracket_id_form():
    assert f"from:<@{PEER}>" in recipes.slack_search(sender=PEER)


def test_order_is_pinned_explicitly():
    """Slack's default is relevance, so chronology has to be asked for."""
    assert "sort:timestamp sort_dir:asc" in recipes.slack_search(sender=PEER)
    assert "sort:timestamp sort_dir:desc" in recipes.slack_search(sender=PEER, ascending=False)


def test_channel_scope_uses_the_channel_id():
    assert f"in:<#{CHANNEL}>" in recipes.slack_search(sender=PEER, channel=CHANNEL)


@pytest.mark.guardrail
def test_a_channel_name_is_refused():
    with pytest.raises(RecipeError, match="id"):
        recipes.slack_search(sender=PEER, channel="#pod-discovery")


@pytest.mark.guardrail
def test_an_unresolved_variable_reference_never_reaches_a_query():
    """`${SLACK_USER_VP_DATA}` as literal text is a query that matches nothing.

    Same class as the display-name failure and the same guardrail: it looks
    like a scoped search and is in fact a search for a dollar sign.
    """
    with pytest.raises(RecipeError, match=r"SLACK_USER_VP_DATA"):
        recipes.slack_search(sender="${SLACK_USER_VP_DATA}")


def test_a_variable_reference_resolves_against_identities(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"SLACK_USER_VP_DATA={PEER}\n")
    query = recipes.slack_search(
        sender="${SLACK_USER_VP_DATA}", identities=Identities.from_file(env)
    )
    assert f"from:<@{PEER}>" in query


def test_the_date_bounds_are_widened_because_slack_excludes_the_named_day():
    """`after:`/`before:` are exclusive of the date given. Callers mean inclusive.

    Erring wide is deliberate: an extra day of messages is trimmed by the
    caller, a missing day is invisible.
    """
    query = recipes.slack_search(sender=PEER, after=date(2026, 9, 5), before=date(2026, 9, 8))
    assert "after:2026-09-04" in query
    assert "before:2026-09-09" in query


def test_terms_are_quoted_so_a_phrase_stays_one_phrase():
    assert '"deal modeler"' in recipes.slack_search(sender=PEER, terms=["deal modeler"])


def test_overnight_window_carries_a_timestamp_cutoff():
    """Slack search resolves to whole days; "since 6pm yesterday" does not exist.

    So the recipe over-fetches by date and hands back the real cutoff for the
    caller to filter on. Without it section 3.1's overnight delta silently
    becomes a whole extra day of Slack in the morning brief.
    """
    window = recipes.slack_overnight(datetime(2026, 9, 8, 6, 45, tzinfo=PT), mentioning=PRINCIPAL)
    assert "after:2026-09-06" in window.query  # 9/7 inclusive -> 9/6 exclusive
    assert f"<@{PRINCIPAL}>" in window.query
    assert window.min_ts == datetime(2026, 9, 7, 18, 0, tzinfo=PT).timestamp()


# ---------------------------------------------------------------------------
# gmail - the gemini notes, by sender + label + subject
# ---------------------------------------------------------------------------


@pytest.mark.guardrail
def test_gemini_notes_are_scoped_by_sender_and_label():
    """Both, not either: `label:` alone would sweep in hand-labelled mail."""
    query = recipes.gmail_gemini_notes(after=date(2026, 9, 1), before=date(2026, 9, 5))
    assert "from:gemini-notes@google.com" in query
    assert 'label:"meeting notes"' in query


def test_the_end_day_is_included():
    """Gmail's `before:` is exclusive, so an inclusive range needs the next day."""
    query = recipes.gmail_gemini_notes(after=date(2026, 9, 1), before=date(2026, 9, 5))
    assert "after:2026/09/01" in query
    assert "before:2026/09/06" in query


def test_a_titled_lookup_searches_the_subject():
    """SPEC said the subject was useless and to match the body. It was wrong.

    All 201 notes in a 30-day window carry `Notes: "<title>" <date>` (#2 audit),
    which resolves the back-to-back-1:1 ambiguity that body matching cannot.
    """
    query = recipes.gmail_gemini_notes(title="AI Platform sync")
    assert 'subject:"AI Platform sync"' in query
    assert "from:gemini-notes@google.com" in query


def test_the_subject_parser_is_the_ledger_s_own():
    """One parser. A second implementation is a second set of bugs."""
    assert recipes.title_from_gemini_subject is title_from_gemini_subject


def test_a_real_subject_round_trips_into_a_lookup():
    title = recipes.title_from_gemini_subject("Notes: “Discovery sync” Sep 3, 2026")
    assert title == "Discovery sync"
    assert 'subject:"Discovery sync"' in recipes.gmail_gemini_notes(title=title)


def test_an_unparseable_subject_gives_no_title_and_so_no_titled_query():
    assert recipes.title_from_gemini_subject("Re: lunch?") is None
    with pytest.raises(RecipeError):
        recipes.gmail_gemini_notes(title=None)


def test_a_quote_in_a_title_cannot_break_out_of_the_subject_operator():
    with pytest.raises(RecipeError, match="quote"):
        recipes.gmail_gemini_notes(title='weird " title')


# ---------------------------------------------------------------------------
# vault - the Create Music Group prefix, and the decoy it exists to avoid
# ---------------------------------------------------------------------------


@pytest.mark.guardrail
def test_relative_paths_carry_the_prefix():
    """`Documents/Weekly Notes/claude-write-test.md` is a real file in the vault.

    It exists because a previous write missed this prefix and landed in a decoy
    folder outside the notes. Guardrail 2: a vault write goes where it is meant
    to or it does not happen.
    """
    assert (
        recipes.vault_relative("Weekly Notes", "0817-0821.md")
        == "Create Music Group/Weekly Notes/0817-0821.md"
    )


def test_the_prefix_is_not_doubled():
    assert (
        recipes.vault_relative("Create Music Group", "DayDAG", "State.md")
        == "Create Music Group/DayDAG/State.md"
    )


@pytest.mark.guardrail
@pytest.mark.parametrize("part", ["..", "../secrets", "/etc/passwd", "", "a/../../b"])
def test_a_path_that_leaves_the_vault_is_refused(part):
    with pytest.raises(RecipeError):
        recipes.vault_relative("Weekly Notes", part)


def test_weekly_note_names_a_monday_to_friday_range():
    """Mon-Fri, not Sun-Sat. `0817-0821.md` is "Week of August 17-21, 2026"."""
    assert recipes.weekly_note(date(2026, 9, 8)) == "Create Music Group/Weekly Notes/0907-0911.md"


def test_a_sunday_belongs_to_the_week_that_is_closing():
    """The section 3.6 trap: the Sunday loop runs inside the *old* week.

    So the closing note and the incoming note are two different files, and
    asking for "this week" on a Sunday evening gets the one just ended.
    """
    sunday = date(2026, 9, 6)
    assert recipes.week_label(sunday) == "0831-0904"
    assert recipes.next_week_label(sunday) == "0907-0911"


def test_meeting_prep_uses_the_same_plain_date_name():
    assert recipes.meeting_prep(date(2026, 9, 8)) == "Create Music Group/Meeting Prep/0907-0911.md"


def test_workstreams_looks_in_daydag_before_fact_base():
    """Custody transfers to DayDAG at the cut (#37); both paths are live states."""
    assert recipes.workstreams_paths() == (
        "Create Music Group/DayDAG/Workstreams.md",
        "Create Music Group/Fact Base/Workstreams.md",
    )


def test_local_path_adds_the_prefix_when_the_configured_root_stops_short():
    path = recipes.vault_path({"VAULT_ROOT": "/vault/Documents"}, "Fact Base", "Workstreams.md")
    assert str(path) == "/vault/Documents/Create Music Group/Fact Base/Workstreams.md"


def test_local_path_does_not_double_a_root_that_already_ends_in_the_prefix():
    root = "/vault/Documents/Create Music Group"
    path = recipes.vault_path({"VAULT_ROOT": root}, "Fact Base", "Workstreams.md")
    assert str(path) == f"{root}/Fact Base/Workstreams.md"


@pytest.mark.guardrail
@pytest.mark.parametrize("root", ["", "   ", "$VAULT_HOME/notes", "${NOPE}/notes"])
def test_an_unusable_vault_root_raises_rather_than_writing_somewhere_odd(root):
    with pytest.raises(RecipeError):
        recipes.vault_path({"VAULT_ROOT": root}, "DayDAG", "State.md")


def test_a_missing_vault_root_names_the_key():
    with pytest.raises(RecipeError, match="VAULT_ROOT"):
        recipes.vault_path({}, "DayDAG", "State.md")


# ---------------------------------------------------------------------------
# jira - bounded, because an unbounded one has already blown the limit
# ---------------------------------------------------------------------------


@pytest.mark.guardrail
def test_fields_are_explicit_and_bounded():
    """A 14-day, 4-project JQL returned 125,231 chars and overflowed (#2 audit).

    `fields=*all` is the cause: it ships every custom field on every issue.
    Same guardrail 6 argument as the calendar - an overflowing query is not a
    degraded read, it is a silent one.
    """
    params = recipes.jira_search(["CING"])
    assert "*all" not in params["fields"]
    assert "*navigable" not in params["fields"]
    assert set(params["fields"].split(",")) >= {"key", "summary", "status", "assignee", "updated"}
    assert 0 < params["maxResults"] <= recipes.JIRA_MAX_RESULTS_CAP


@pytest.mark.guardrail
@pytest.mark.parametrize("bad", ["*all", "*navigable", "key,*all"])
def test_asking_for_every_field_is_refused(bad):
    with pytest.raises(RecipeError, match=r"\*"):
        recipes.jira_search(["CING"], fields=bad.split(","))


@pytest.mark.guardrail
def test_page_size_above_the_cap_is_refused():
    with pytest.raises(RecipeError, match="maxResults"):
        recipes.jira_search(["CING"], max_results=recipes.JIRA_MAX_RESULTS_CAP + 1)


def test_paging_advances_by_the_page_size():
    first = recipes.jira_search(["CING"], max_results=50)
    second = recipes.jira_search(["CING"], max_results=50, page=1)
    assert first["startAt"] == 0
    assert second["startAt"] == 50
    assert first["jql"] == second["jql"]


@pytest.mark.guardrail
@pytest.mark.parametrize(
    "bad",
    ['CING" OR key != "x', "cing", "C ING", "CING;DROP", "", "TOOLONGAPROJECTKEY"],
)
def test_a_project_key_that_is_not_a_project_key_is_refused(bad):
    """JQL is assembled by concatenation, so the keys are the injection surface.

    They come from a hand-edited watchlist, which is untrusted input.
    """
    with pytest.raises(RecipeError):
        recipes.jira_search([bad])


def test_no_projects_is_refused_rather_than_querying_every_project():
    with pytest.raises(RecipeError, match="project"):
        recipes.jira_search([])


def test_jql_scopes_projects_window_and_order():
    jql = recipes.jira_jql(["CING", "PLAT"], updated_within_days=7)
    assert 'project in ("CING", "PLAT")' in jql
    assert "updated >= -7d" in jql
    assert jql.endswith("ORDER BY updated DESC")


def test_status_filter_is_quoted():
    jql = recipes.jira_jql(["CING"], statuses=["In Progress", "Blocked"])
    assert 'status in ("In Progress", "Blocked")' in jql


@pytest.mark.guardrail
def test_a_status_cannot_carry_a_quote_out_of_its_operator():
    with pytest.raises(RecipeError, match="quote"):
        recipes.jira_jql(["CING"], statuses=['done" OR key != "'])


# ---------------------------------------------------------------------------
# github - argv, read-only, and never assuming the default branch
# ---------------------------------------------------------------------------


def _gh_recipes():
    repo = WatchedRepo("CreateMusicGroup", "createos-discovery-services")
    return [
        recipes.gh_open_prs(repo),
        recipes.gh_pr_checks(repo, 412),
        recipes.gh_default_branch(repo),
        recipes.gh_recent_runs(repo, branch="develop"),
        recipes.gh_recent_runs(repo),
    ]


@pytest.mark.guardrail
def test_every_github_recipe_is_argv_never_a_shell_string():
    """A shell string built from a hand-edited watchlist is a command injection.

    argv with shell=False cannot be broken out of, which is the same reason
    `pulse._run_git` takes a list.
    """
    for argv in _gh_recipes():
        assert isinstance(argv, list)
        assert all(isinstance(token, str) for token in argv)
        assert argv[0] == "gh"


@pytest.mark.guardrail
def test_no_github_recipe_carries_a_write_verb():
    """Guardrail 1: read all, write nothing. GitHub is a read source (SPEC 4)."""
    for argv in _gh_recipes():
        assert not (set(argv) & recipes.GH_WRITE_VERBS), argv


@pytest.mark.guardrail
def test_the_module_exposes_no_send_or_write_surface():
    """A tripwire, not coverage: there is no send path here and must not be.

    Guardrail 1 - autonomous sends reach Nitin's DM only, and nothing in a
    query-building module has any business constructing one.
    """
    exported = [
        name
        for name in dir(recipes)
        if not name.startswith("_") and callable(getattr(recipes, name))
    ]
    forbidden = ("send", "post", "reply", "draft", "transition", "assign", "delete")
    assert [n for n in exported if any(word in n.lower() for word in forbidden)] == []


def test_open_prs_asks_for_explicit_fields_and_a_bounded_limit():
    argv = recipes.gh_open_prs(WatchedRepo("org", "repo"))
    assert "--json" in argv
    fields = argv[argv.index("--json") + 1].split(",")
    assert {"number", "title", "url", "reviewDecision", "statusCheckRollup"} <= set(fields)
    assert int(argv[argv.index("--limit") + 1]) <= recipes.GH_LIMIT_CAP


@pytest.mark.guardrail
def test_the_default_branch_is_resolved_never_assumed():
    """`createos-dsp-ingestion` defaults to `develop`, not `main`.

    A branch-health check hardcoding `main` reports nothing for it and looks
    green - guardrail 3's "if it cannot source it, it says so" inverted.
    """
    assert "main" not in recipes.gh_recent_runs(WatchedRepo("org", "repo"))
    assert "--branch" not in recipes.gh_recent_runs(WatchedRepo("org", "repo"))
    assert "defaultBranchRef" in recipes.gh_default_branch(WatchedRepo("org", "repo"))


def test_a_slug_string_works_as_well_as_a_watched_repo():
    assert recipes.gh_open_prs(WatchedRepo("org", "repo")) == recipes.gh_open_prs("org/repo")


@pytest.mark.parametrize("bad", ["not a slug", "org/repo/extra", "org", "", "-org/repo"])
def test_a_bad_slug_is_refused(bad):
    with pytest.raises(RecipeError):
        recipes.gh_open_prs(bad)


@pytest.mark.guardrail
@pytest.mark.parametrize("bad", ["--json", "-x", "a b", "a;rm -rf /"])
def test_a_branch_name_cannot_smuggle_a_flag_or_a_second_argument(bad):
    with pytest.raises(RecipeError):
        recipes.gh_recent_runs("org/repo", branch=bad)


@pytest.mark.parametrize("bad", [0, -1, "412; rm -rf /"])
def test_a_pr_number_must_be_a_positive_integer(bad):
    with pytest.raises(RecipeError):
        recipes.gh_pr_checks("org/repo", bad)


# --- regressions found by the prep-pings agent reviewing this module ---------


@pytest.mark.guardrail
def test_the_overnight_after_date_does_not_depend_on_the_callers_tzinfo():
    """The same instant must produce the same query, however it is expressed.

    `cutoff` is converted back to the caller's tzinfo, so a UTC-aware `now`
    rolled its .date() forward a day - 6pm PT is 01:00 UTC. `after:` is
    exclusive, so the window skipped the exact 6pm-to-midnight hours it exists
    to capture, while `min_ts` still claimed them. The two disagreed silently,
    which is the worst shape: a brief that looks complete and is not.
    """
    ids = {"SLACK_USER_PRINCIPAL": PRINCIPAL}
    instant = datetime(2026, 9, 7, 8, 0, tzinfo=recipes.PACIFIC)
    pacific = recipes.slack_overnight(
        now=instant, mentioning="${SLACK_USER_PRINCIPAL}", identities=ids
    )
    utc = recipes.slack_overnight(
        now=instant.astimezone(UTC), mentioning="${SLACK_USER_PRINCIPAL}", identities=ids
    )
    assert pacific.query == utc.query
    assert pacific.min_ts == utc.min_ts


def test_the_configured_timezone_is_actually_read():
    """`TIMEZONE` in .env was documented as read while nothing read it."""
    assert timezone_for({"TIMEZONE": "Europe/London"}).key == "Europe/London"
    assert timezone_for().key == DEFAULT_TIMEZONE


def test_an_unknown_timezone_is_refused_rather_than_silently_ignored():
    """A typo must not present as correct-looking wrong times."""
    with pytest.raises(ConfigError, match="not a known IANA zone"):
        timezone_for({"TIMEZONE": "Not/AZone"})
