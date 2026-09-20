"""What the system knows about a person, and where that knowledge came from.

Today it knows almost nothing: two config values, both unset, so `has_external`
and `has_leadership` always answer False and two of prep's four reasons cannot
fire. The roster in CLAUDE.md is prose - an agent reads it, no code does - and
it covers about fifteen people against the forty a single week's calendar holds.

This is the store that fixes that, and the thing it has to get right is not
lookup. It is PRECEDENCE. Most of what it learns is inferred from meetings, and
inference must never overwrite something he told it - CLAUDE.md is explicit that
a hand edit is an event and wins over derived state. A directory that quietly
un-learns a correction is worse than an empty one, because he stops checking.

Fixtures throughout. The repo is public; real identifiers live in the store,
which is outside it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from daydag.eventlog import EventLog
from daydag.ledger import Row, attendee_parts
from daydag.people import OBSERVED, PROFILE, STATED, People

MON = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)


@pytest.fixture
def directory(tmp_path):
    return People(EventLog.open(tmp_path / "events.db"))


def _row(summary: str, *attendees: str, days: int = 0, event_id: str = "") -> Row:
    """Split attendees the way `Ledger.seed_day` does, so these rows look like
    real ones - the first fixture put `"Name <addr>"` straight into
    `attendees` and hid that `observe` could never learn a name."""
    start = MON + timedelta(days=days)
    parts = [attendee_parts(a) for a in attendees]
    return Row(
        event_id=event_id or f"{summary}-{days}",
        start=start,
        end=start + timedelta(minutes=30),
        summary=summary,
        attendees=[addr for addr, _ in parts],
        attendee_names=[name for _, name in parts],
    )


# --------------------------------------------------------------------------
# knowing someone at all
# --------------------------------------------------------------------------


def test_a_person_stated_by_hand_is_found_by_email(directory):
    directory.remember("vp-data", email="wren.alder@example.com", source=STATED)

    found = directory.resolve("wren.alder@example.com")

    assert found is not None
    assert found.key == "vp-data"


def test_a_person_is_found_by_slack_id_too(directory):
    directory.remember("vp-data", email="wren.alder@example.com", slack_id="U0AWREN", source=STATED)

    assert directory.resolve("U0AWREN") is not None


def test_a_person_is_found_by_name(directory):
    directory.remember("ceo", email="jo@example.com", display_name="Jo Strauss", source=STATED)

    assert directory.resolve("jo strauss") is not None


def test_an_unknown_person_resolves_to_none_not_to_a_guess(directory):
    """`None` is the answer that makes a caller handle it. The current failure
    is the opposite - unknown reads as "no, not external" rather than "I do not
    know", so a vendor meeting silently gets no prep."""
    directory.remember("vp-data", email="wren.alder@example.com", source=STATED)

    assert directory.resolve("someone.else@example.com") is None


# --------------------------------------------------------------------------
# precedence - the part that matters
# --------------------------------------------------------------------------


def test_a_stated_fact_is_not_overwritten_by_an_observation(directory):
    """CLAUDE.md: a hand edit is an event and WINS over derived state."""
    directory.remember("ceo", email="jo@example.com", title="CEO", source=STATED)

    directory.remember("ceo", email="jo@example.com", title="Attendee", source=OBSERVED)

    assert directory.resolve("jo@example.com").title == "CEO"


def test_a_stated_fact_is_not_overwritten_by_a_profile_either(directory):
    """Slack titles are self-written and informal. If he has corrected one, the
    correction stands until he changes it."""
    directory.remember("ceo", email="jo@example.com", title="CEO", source=STATED)

    directory.remember("ceo", email="jo@example.com", title="CEO things", source=PROFILE)

    assert directory.resolve("jo@example.com").title == "CEO"


def test_a_profile_does_overwrite_an_observation(directory):
    directory.remember("x", email="a@example.com", title="guessed", source=OBSERVED)

    directory.remember("x", email="a@example.com", title="Staff Engineer", source=PROFILE)

    assert directory.resolve("a@example.com").title == "Staff Engineer"


def test_a_later_statement_replaces_an_earlier_one(directory):
    """Same source, so the newer wins - or a correction could never be
    corrected again."""
    directory.remember("x", email="a@example.com", title="VP Data", source=STATED)

    directory.remember("x", email="a@example.com", title="SVP Data", source=STATED)

    assert directory.resolve("a@example.com").title == "SVP Data"


def test_a_field_is_not_cleared_by_an_update_that_omits_it(directory):
    """Facts accumulate. Learning a slack id must not lose the title."""
    directory.remember("x", email="a@example.com", title="VP Data", source=STATED)

    directory.remember("x", email="a@example.com", slack_id="U0AX", source=PROFILE)

    person = directory.resolve("a@example.com")
    assert person.title == "VP Data"
    assert person.slack_id == "U0AX"


# --------------------------------------------------------------------------
# learning from meetings
# --------------------------------------------------------------------------


def test_a_meeting_teaches_the_directory_someone_new(directory):
    """The "builds over time" half. Every calendar row the ledger seeds carries
    attendees, so the directory discovers people without anyone entering them.
    """
    directory.observe(_row("Pod Steering", "me@example.com", "newcomer@example.com"))

    assert directory.resolve("newcomer@example.com") is not None


def test_meeting_counts_and_last_met_accumulate(directory):
    for day in (0, 3, 7):
        directory.observe(_row("1:1", "me@example.com", "wren@example.com", days=day))

    person = directory.resolve("wren@example.com")

    assert person.met == 3
    assert person.last_met == (MON + timedelta(days=7)).date()


def test_the_same_meeting_observed_twice_counts_once(directory):
    """Re-running a loop over the same day is normal - the ledger is seeded on
    every run - and must not inflate how often he meets someone."""
    row = _row("1:1", "me@example.com", "wren@example.com")
    directory.observe(row)
    directory.observe(row)

    assert directory.resolve("wren@example.com").met == 1


def test_observing_never_invents_a_title(directory):
    """A meeting says he was in a room with someone. It says nothing about
    their job, and a guessed designation would then outrank a real one later
    only by accident of ordering."""
    directory.observe(_row("Pod Steering", "me@example.com", "newcomer@example.com"))

    assert directory.resolve("newcomer@example.com").title is None


def test_a_conference_room_is_not_a_person(directory):
    """Google lists rooms as attendees. One in the directory would then be
    'met' more often than anybody."""
    directory.observe(
        _row(
            "Pod Steering",
            "me@example.com",
            "c_188@resource.calendar.google.com",
            "real@example.com",
        )
    )

    assert directory.resolve("c_188@resource.calendar.google.com") is None
    assert directory.resolve("real@example.com") is not None


def test_the_principal_is_not_added_to_his_own_directory(directory):
    """He is in every meeting. A row for him would be met-count noise and would
    match every name query."""
    directory.observe(
        _row("Pod Steering", "me@example.com", "other@example.com"), principal="me@example.com"
    )

    assert directory.resolve("me@example.com") is None


# --------------------------------------------------------------------------
# the channels he talks to them through
# --------------------------------------------------------------------------


def test_a_dm_and_groups_are_carried_per_person(directory):
    directory.remember(
        "vp-data",
        email="wren@example.com",
        dm="D0AWREN",
        groups=["G0ALEADS", "C0ADATA"],
        source=STATED,
    )

    person = directory.resolve("wren@example.com")

    assert person.dm == "D0AWREN"
    assert set(person.groups) == {"G0ALEADS", "C0ADATA"}


def test_groups_accumulate_rather_than_replace(directory):
    """Learning he is in one more channel must not forget the others."""
    directory.remember("x", email="a@example.com", groups=["C0AONE"], source=STATED)

    directory.remember("x", email="a@example.com", groups=["C0ATWO"], source=OBSERVED)

    assert set(directory.resolve("a@example.com").groups) == {"C0AONE", "C0ATWO"}


# --------------------------------------------------------------------------
# what the app asks it
# --------------------------------------------------------------------------


def test_leadership_is_a_query_not_a_config_list(directory):
    """`PREP_LEADERSHIP` was a CSV in .env that nobody set, so the rule never
    fired. Leadership is a property of a person, held where the person is."""
    directory.remember("ceo", email="jo@example.com", leadership=True, source=STATED)
    directory.remember("eng", email="dev@example.com", source=STATED)

    emails = {p.primary_email for p in directory.leadership()}

    assert emails == {"jo@example.com"}


def test_several_emails_resolve_to_one_person(directory):
    directory.remember("x", email="a@example.com", source=STATED)

    directory.remember("x", email="a@other.example", source=STATED)

    assert directory.resolve("a@example.com") is directory.resolve("a@other.example")


def test_everyone_is_listable_for_a_report(directory):
    directory.remember("a", email="a@example.com", source=STATED)
    directory.observe(_row("Sync", "me@example.com", "b@example.com"), principal="me@example.com")

    assert len(directory.all()) == 2


# --------------------------------------------------------------------------
# it stays out of the vault and out of the repo
# --------------------------------------------------------------------------


def test_the_directory_writes_nothing_to_the_vault(tmp_path):
    """Slack ids, emails and DM channels are exactly what CONTRIBUTING keeps
    out of a public repo, and CLAUDE.md keeps out of a synced vault."""
    vault = tmp_path / "vault"
    vault.mkdir()
    directory = People(EventLog.open(tmp_path / "events.db"))

    directory.remember("x", email="a@example.com", slack_id="U0AX", dm="D0AX", source=STATED)

    assert not any(p.is_file() for p in vault.rglob("*"))


def test_reloading_from_the_log_does_not_double_count_a_meeting(tmp_path):
    """The dedup that matters is on the FOLD, not on the call.

    `observe` skips a meeting already seen IN THIS PROCESS, which is the easy
    half. The log itself can still hold the same observation twice - two runs
    that both read the log before either wrote will each record it, and a
    backfill replayed over a live log does the same. Folding must then count it
    once, or the met-count climbs by one for every run that ever happened and
    "you meet Wren weekly" becomes "you meet Wren constantly".
    """
    log = tmp_path / "events.db"
    row = _row("1:1", "me@example.com", "wren@example.com")
    # BOTH opened before either writes - neither can see the other's record,
    # so the log genuinely ends up holding the observation twice.
    first = People(EventLog.open(log))
    second = People(EventLog.open(log))
    first.observe(row)
    second.observe(row)

    reloaded = People(EventLog.open(log))

    assert reloaded.resolve("wren@example.com").met == 1


def test_a_partial_name_does_not_resolve_to_the_wrong_person(directory):
    """Every token has to land. Matching on ANY of them makes "jo smith" find
    Jo Strauss, and this store is read by things that decide who to message.
    """
    directory.remember("ceo", email="jo@example.com", display_name="Jo Strauss", source=STATED)

    assert directory.resolve("jo smith") is None
    assert directory.resolve("jo strauss") is not None


# --------------------------------------------------------------------------
# wired: prep asks the directory who is senior
# --------------------------------------------------------------------------


def test_prep_takes_leadership_from_the_directory(directory):
    """`PREP_LEADERSHIP` was a CSV in `.env` that nobody set, so the rule never
    fired. Seniority is a fact about a person and belongs with the person."""
    from daydag.prep import Audience

    directory.remember("ceo", email="jo@example.com", leadership=True, source=STATED)

    audience = Audience.from_directory(directory, {})

    assert audience.has_leadership(["jo@example.com"])
    assert not audience.has_leadership(["someone@example.com"])


def test_an_empty_directory_falls_back_to_the_configured_list(directory):
    """A store still filling up must not silently switch the rule off for
    someone who had configured it the old way."""
    from daydag.prep import Audience

    audience = Audience.from_directory(directory, {"PREP_LEADERSHIP": "old@example.com"})

    assert audience.has_leadership(["old@example.com"])


# --------------------------------------------------------------------------
# what the review found
# --------------------------------------------------------------------------


def test_a_correction_after_an_observation_lands_on_the_same_person(directory):
    """Observation minted key `alder-wren` from the address; then he ran
    `people add vp-data --email wren.alder@... --dm D0AWREN --leadership`.
    Two entries, and resolve(address) kept returning the stub with no dm and
    no leadership - the precedence contract bypassed. Verified by running it."""
    directory.observe(
        _row("1:1", "me@example.com", "wren.alder@example.com"), principal="me@example.com"
    )

    directory.remember(
        "vp-data", email="wren.alder@example.com", dm="D0AWREN", leadership=True, source=STATED
    )

    found = directory.resolve("wren.alder@example.com")
    assert found.key == "vp-data" and found.dm == "D0AWREN" and found.leadership
    assert found.met == 1, "the observation was lost in the fold"
    assert len(directory.all()) == 1


def test_the_fold_survives_a_reload(tmp_path):
    log = tmp_path / "events.db"
    first = People(EventLog.open(log))
    first.observe(
        _row("1:1", "me@example.com", "wren.alder@example.com"), principal="me@example.com"
    )
    first.remember("vp-data", email="wren.alder@example.com", dm="D0AWREN", source=STATED)

    reloaded = People(EventLog.open(log))

    assert reloaded.resolve("wren.alder@example.com").key == "vp-data"
    assert len(reloaded.all()) == 1


def test_a_strangers_address_never_folds_into_a_known_person(directory):
    """`wren@vendorco.com` shares a token with Wren Alder. The first version
    resolved observations through the name fallback, appended the vendor's
    address to the VP and so pointed a stranger at her DM channel."""
    directory.remember("vp-data", email="wren.alder@example.com", dm="D0AWREN", leadership=True)

    directory.observe(
        _row("Vendor call", "me@example.com", "wren@vendorco.com"), principal="me@example.com"
    )

    vp = directory.resolve("wren.alder@example.com")
    assert vp.emails == ("wren.alder@example.com",)
    stranger = directory.resolve("wren@vendorco.com")
    assert stranger is not None and stranger.key != "vp-data" and stranger.dm is None


def test_an_address_query_never_falls_through_to_name_tokens(directory):
    directory.remember("vp-data", email="wren.alder@example.com", display_name="Wren Alder")

    assert directory.resolve("wren@vendorco.com") is None


def test_each_instance_of_a_recurring_meeting_counts(directory):
    """`Row.event_id` is google's SERIES id, so a marker on it alone counted a
    weekly 1:1 once and `met` stuck at 1 forever."""
    for days in (0, 7, 14):
        directory.observe(
            _row("1:1", "me@example.com", "wren@example.com", days=days, event_id="series-1"),
            principal="me@example.com",
        )

    person = directory.resolve("wren@example.com")
    assert person.met == 3
    assert person.last_met == (MON + timedelta(days=14)).date()


def test_resolve_finds_a_person_by_their_key(directory):
    """`people show vp-data` is the form every doc uses, and it printed
    "not in the directory"."""
    directory.remember("vp-data", email="wren.alder@example.com", display_name="Wren Alder")

    assert directory.resolve("vp-data") is not None


def test_a_meeting_teaches_a_display_name(directory):
    """`observe` parsed names out of `attendees`; `seed_day` puts them in
    `attendee_names`. So no real row ever taught a name."""
    directory.observe(
        _row("Summit", "me@example.com", "Jo Strauss <jo@example.com>"), principal="me@example.com"
    )

    assert directory.resolve("jo@example.com").display_name == "Jo Strauss"
    assert directory.resolve("jo strauss") is not None


def test_a_valueless_log_flag_is_refused_not_written_to_a_file_named_None(tmp_path, monkeypatch):
    from daydag.people import main

    monkeypatch.chdir(tmp_path)
    assert main(["add", "x", "--email", "a@example.com", "--log"]) == 2, (
        "a flag with no value is a usage error"
    )
    assert not (tmp_path / "None").exists()


def test_directory_leadership_is_unioned_with_the_configured_list(directory):
    """Either/or dropped every configured leader the moment one was stored."""
    from daydag.prep import Audience

    directory.remember("cto", email="cto@example.com", leadership=True)

    audience = Audience.from_directory(directory, {"PREP_LEADERSHIP": "ceo@example.com"})

    assert audience.has_leadership(["ceo@example.com"])
    assert audience.has_leadership(["cto@example.com"])


def test_the_org_domain_falls_back_to_the_principals_address(directory):
    """`ORG_EMAIL_DOMAIN` unset was the documented state, and it left the
    external-party rule dead. He works for the org; his address is its domain."""
    from daydag.prep import Audience

    audience = Audience.from_directory(directory, {"EMAIL_PRINCIPAL": "me@example.com"})

    assert audience.has_external(["rdinh@vendor.example"])
    assert not audience.has_external(["wren@example.com"])
