"""Day-before prep for the calls he named (#171).

His words, 2026-09-25: "to prepare for pod steering, look at any deck the day
before, for [the cfo] - look at past notes from the call the day before. for
[the ceo] - look at past notes the day before and suggest any ideas for
conversation / follow ups. in the future there could be more calls to prepare
for".

So the rules are ROWS he edits in the vault, not code: these tests pin that
adding a row changes behaviour, that a bad row is one line rather than a
crash, and that the evening run preps the next WORKING day.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from daydag import prep_ahead as pa
from daydag import run
from daydag.config import Identities
from daydag.people import Person
from daydag.state import EventLog
from daydag.voice import voice_violations

#: Friday 2026-09-25, 17:30 Pacific - when the evening run fires.
FRIDAY_EVENING = datetime(2026, 9, 26, 0, 30, tzinfo=UTC)
MONDAY = date(2026, 9, 28)

ROLES = {
    "cfo": Person(key="cfo", emails=("wren.cfo@x.com",), slack_id="UCFO0001"),
    "ceo": Person(key="ceo", emails=("jo@x.com",), slack_id="UCEO0001", dm="DCEO0001"),
    "vp-ai": Person(key="vp-ai", emails=("jo.other@x.com",), slack_id="UVPAI001"),
    # The real directory holds the sponsor with a dm and no address - the
    # shape that must degrade to a warning, not a crash or a silent no-match.
    "sponsor": Person(key="sponsor", dm="DSPON001"),
}


def _event(summary, *attendees, day="2026-09-28", at="12:30", **extra):
    people = [{"email": "principal@x.com", "self": True, "responseStatus": "accepted"}]
    for a in attendees:
        people.append(a if isinstance(a, dict) else {"email": a, "responseStatus": "accepted"})
    return {
        "id": f"{summary.strip().lower().replace(' ', '-')}-{day}",
        "summary": summary,
        "start": f"{day}T{at}:00-07:00",
        "end": f"{day}T{at[:2]}:59:00-07:00",
        "attendees": people,
        "response_status": "accepted",
        "permalink": f"https://cal/{summary.strip()[:8]}",
        **extra,
    }


# --------------------------------------------------------------------------
# the registry he edits
# --------------------------------------------------------------------------


def test_the_seed_section_parses_to_the_three_rules_he_asked_for():
    rules = pa.parse_rules(pa.SEED_SECTION)

    assert not rules.warnings, rules.warnings
    assert not rules.seeded, "a present section is his, not the fallback"
    by_recipe = {r.recipe: r for r in rules.rules}
    assert by_recipe[frozenset({"deck"})].title == ("pod", "steering")
    assert set(by_recipe[frozenset({"past-notes"})].attendees) == {"cfo", "sponsor"}
    assert by_recipe[frozenset({"past-notes", "ideas"})].attendees == ("ceo",)
    assert all(r.lead_days == 1 for r in rules.rules)


def test_a_watchlist_with_no_prep_section_falls_back_to_the_seed_and_says_so():
    rules = pa.parse_rules("# Watchlist\n\n## repos\n\n- a/b\n")

    assert rules.seeded
    assert len(rules.rules) == 3
    assert any("seed" in note for note in rules.notes)


def test_an_emptied_section_is_his_edit_and_wins_over_the_seed():
    """He deleted every row. Falling back to the seed would undo that."""
    rules = pa.parse_rules("## Prep rules\n\n(none for now)\n\n## repos\n- a/b\n")

    assert rules.rules == ()
    assert not rules.seeded


def test_a_malformed_row_is_one_warning_line_and_the_rest_still_load():
    text = (
        "## Prep rules\n"
        "- title: Pod Steering · deck\n"
        "- title: Board prep · slides · day before\n"  # unknown recipe
        "- deck · day before\n"  # no match
        "- attendee: ceo · past-notes · a week before\n"  # bad lead
        "- attendee: · past-notes\n"  # empty match
    )

    rules = pa.parse_rules(text)

    assert len(rules.rules) == 1
    assert len(rules.warnings) == 4, rules.warnings
    assert all(w.startswith("⚠ prep rule not understood") for w in rules.warnings)


def test_the_section_ends_at_the_next_heading_or_rule():
    text = "## Prep rules\n- title: x · deck\n---\n- title: y · deck\n## jira\n- CDI · live\n"

    rules = pa.parse_rules(text)

    assert [r.title for r in rules.rules] == [("x",)]
    assert not rules.warnings


def test_combined_match_lead_and_notes_fields():
    text = (
        "## Prep rules\n- title: roadmap · attendee: cfo · deck+past-notes · 2 days before · q4\n"
    )

    (rule,) = pa.parse_rules(text).rules

    assert rule.title == ("roadmap",)
    assert rule.attendees == ("cfo",)
    assert rule.recipe == frozenset({"deck", "past-notes"})
    assert rule.lead_days == 2


def test_the_prep_section_does_not_leak_into_the_repo_or_board_parsers(tmp_path):
    """Three parsers read one file. A prep row must never become a repo."""
    from daydag.board import read_board_watchlist
    from daydag.pulse import read_watchlist

    path = tmp_path / "Watchlist.md"
    path.write_text(
        "## repos\n- o/r\n\n" + pa.SEED_SECTION + "\n## jira\n- CDI · live\n", encoding="utf-8"
    )

    assert [r.name for r in read_watchlist(path).repos] == ["r"]
    assert not read_watchlist(path).unparsed
    assert [p.key for p in read_board_watchlist(path).projects] == ["CDI"]


# --------------------------------------------------------------------------
# next working day
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 9, 24), date(2026, 9, 25)),  # thu -> fri
        (date(2026, 9, 25), date(2026, 9, 28)),  # fri -> mon
        (date(2026, 9, 26), date(2026, 9, 28)),  # sat -> mon
        (date(2026, 9, 27), date(2026, 9, 28)),  # sun -> mon
    ],
)
def test_the_next_working_day_skips_the_weekend(today, expected):
    assert pa.next_working_day(today) == expected


def test_two_days_before_counts_working_days():
    assert pa.next_working_day(date(2026, 9, 24), 2) == date(2026, 9, 28)


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------


def _seed():
    return pa.parse_rules(pa.SEED_SECTION)


def test_the_seed_rules_match_the_calls_he_named_and_nothing_else():
    events = [
        _event("Pod Steering", "a@x.com", "b@x.com", at="09:00"),
        _event(" Status | CreateOS Labs", "wren.cfo@x.com", "eddie@x.com"),
        _event("Jo <> Principal", "jo@x.com", at="15:00"),
        _event("Jo Other <> Prin Connect", "jo.other@x.com", at="16:00"),
        _event("DE Standup", "a@x.com", "b@x.com", at="10:00"),
    ]

    matched, _ = pa.match(events, _seed(), ROLES, principal="principal@x.com")

    by_title = {m.title: m for m in matched}
    assert set(by_title) == {"Pod Steering", "Status | CreateOS Labs", "Jo <> Principal"}
    assert by_title["Pod Steering"].recipe == frozenset({"deck"})
    assert by_title["Status | CreateOS Labs"].recipe == frozenset({"past-notes"})
    assert by_title["Jo <> Principal"].recipe == frozenset({"past-notes", "ideas"})


def test_an_optional_or_declined_attendee_is_not_in_the_call():
    """Monday's sprint demo lists the cfo as optional. Prepping it as a cfo call
    spends his evening on a meeting the cfo is not coming to."""
    events = [
        _event("Sprint Demo", {"email": "wren.cfo@x.com", "optional": True}, "a@x.com"),
        _event(
            "Other",
            {"email": "wren.cfo@x.com", "responseStatus": "declined"},
            "a@x.com",
            at="14:00",
        ),
    ]

    matched, _ = pa.match(events, _seed(), ROLES, principal="principal@x.com")

    assert matched == []


def test_a_meeting_he_declined_is_not_prepped():
    event = _event("Pod Steering", "a@x.com", "b@x.com", response_status="declined")

    matched, _ = pa.match([event], _seed(), ROLES, principal="principal@x.com")

    assert matched == []


def test_two_rules_on_one_meeting_union_their_recipes():
    event = _event("Pod Steering", "wren.cfo@x.com", "b@x.com")

    (m,), _ = pa.match([event], _seed(), ROLES, principal="principal@x.com")

    assert m.recipe == frozenset({"deck", "past-notes"})
    assert len(m.rules) == 2


def test_a_role_with_no_address_is_a_warning_not_a_crash():
    rules = pa.parse_rules("## Prep rules\n- attendee: sponsor · past-notes\n")

    matched, warnings = pa.match(
        [_event("x", "a@x.com", "b@x.com")], rules, ROLES, principal="principal@x.com"
    )

    assert matched == []
    assert any("sponsor" in w and "no address" in w for w in warnings), warnings


def test_a_rule_with_one_matchable_role_does_not_warn_about_the_other():
    """The seed names the cfo twice (`cfo, sponsor`). Warning every evening that
    `sponsor` has no address is noise that teaches him to skip warnings."""
    _, warnings = pa.match(
        [_event("x", "a@x.com", "b@x.com")], _seed(), ROLES, principal="principal@x.com"
    )

    assert warnings == []


def test_notes_from_a_different_meeting_are_not_this_series(identities, log):
    """Measured 2026-09-25: gmail's subject search for "Status | CreateOS Labs"
    returned the notes of "Relaunching CreateOS labs (mandatory)"."""
    labs = _event(" Status | CreateOS Labs", "wren.cfo@x.com", "eddie@x.com")
    near_miss = {
        "subject": "Notes: “Relaunching CreateOS labs (mandatory)” Sep 10, 2026",
        "permalink": "https://mail/other",
    }

    text = _render(identities, log, [labs], {labs["id"]: {"notes": [near_miss]}})

    assert "https://mail/other" not in text
    assert "no notes from this series" in text


def test_adding_a_row_changes_what_matches_with_no_code_change():
    events = [_event("Revenue review", "a@x.com", "b@x.com")]
    before, _ = pa.match(events, _seed(), ROLES, principal="principal@x.com")

    edited = pa.parse_rules(pa.SEED_SECTION + "- title: revenue review · past-notes\n")
    after, _ = pa.match(events, edited, ROLES, principal="principal@x.com")

    assert before == []
    assert [m.title for m in after] == ["Revenue review"]


# --------------------------------------------------------------------------
# the plan: python fetches nothing, it names the reads
# --------------------------------------------------------------------------


@pytest.fixture
def identities(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        f"SLACK_USER_PRINCIPAL=UPRINCIPAL1\nEMAIL_PRINCIPAL=principal@x.com\n"
        f"VAULT_ROOT={tmp_path / 'vault'}\n",
        encoding="utf-8",
    )
    (tmp_path / "vault" / "Weekly Notes").mkdir(parents=True)
    return Identities.from_file(env)


@pytest.fixture
def log(tmp_path):
    from daydag.people import People

    path = tmp_path / "events.db"
    directory = People(EventLog.open(path))
    for role, person in ROLES.items():
        directory.remember(
            role,
            email=person.emails[0] if person.emails else None,
            slack_id=person.slack_id,
            dm=person.dm,
        )
    return path


def test_without_a_calendar_the_plan_asks_for_the_next_working_day_only(identities):
    built = run.plan("prep-ahead", now=FRIDAY_EVENING, identities=identities)

    days = [s.detail["day"] for s in built.steps if s.source == "calendar"]
    assert days == ["2026-09-28"], "friday evening preps monday, not saturday"
    assert {s.source for s in built.steps} == {"calendar"}


def test_with_the_calendar_the_plan_names_each_matched_meetings_reads(identities, log):
    calendar = [
        _event(
            "Pod Steering",
            "a@x.com",
            "b@x.com",
            at="09:00",
            attachments=[
                {"fileUrl": "https://docs.google.com/presentation/d/abc/edit", "title": "deck"},
                {"fileUrl": "https://docs.google.com/document/d/n?usp=meet_tnfm_calendar"},
                {"fileUrl": "https://drive.google.com/file/d/r", "mimeType": "video/mp4"},
            ],
        ),
        _event(" Status | CreateOS Labs", "wren.cfo@x.com", "eddie@x.com"),
        _event("Jo <> Principal", "jo@x.com", at="15:00"),
        _event("DE Standup", "a@x.com", "b@x.com", at="10:00"),
    ]

    built = run.plan(
        "prep-ahead", now=FRIDAY_EVENING, identities=identities, calendar=calendar, log=log
    )

    steps = [s.to_dict() for s in built.steps]
    for_event = {}
    for s in steps:
        for_event.setdefault(s["detail"].get("event_id"), []).append(s)
    assert "de-standup-2026-09-28" not in for_event, "an unmatched meeting got reads"

    steering = {s["source"]: s for s in for_event["pod-steering-2026-09-28"]}
    assert steering["drive"]["detail"]["urls"] == [
        "https://docs.google.com/presentation/d/abc/edit"
    ]
    assert "gmail" not in steering, "the deck rule does not ask for notes"

    labs = {s["source"]: s for s in for_event["status-|-createos-labs-2026-09-28"]}
    assert 'subject:"Status | CreateOS Labs"' in labs["gmail"]["detail"]["query"]
    assert "notion" in labs
    assert "from:<@UCFO0001>" in labs["slack"]["detail"]["query"]

    ceo = {s["source"]: s for s in for_event["jo-<>-principal-2026-09-28"]}
    assert "in:<#DCEO0001>" in ceo["slack"]["detail"]["query"], "the 1:1 dm, by id"
    assert "ideas" in ceo["prep_ahead"]["how"]


def test_no_deck_on_the_invite_plans_a_bounded_drive_search(identities, log):
    calendar = [_event("Pod Steering", "a@x.com", "b@x.com")]

    built = run.plan(
        "prep-ahead", now=FRIDAY_EVENING, identities=identities, calendar=calendar, log=log
    )

    (drive,) = [s for s in built.steps if s.source == "drive"]
    assert drive.detail["search"] == "Pod Steering"
    assert drive.detail["modified_after"]


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------


def _render(identities, log, calendar, per_meeting):
    return run.render(
        "prep-ahead",
        now=FRIDAY_EVENING,
        identities=identities,
        payloads={"calendar": calendar, "prep_ahead": per_meeting},
        log=log,
    )


def test_render_preps_the_matches_with_sourced_points_and_nothing_else(identities, log):
    labs = _event(" Status | CreateOS Labs", "wren.cfo@x.com", "eddie@x.com")
    standup = _event("DE Standup", "a@x.com", "b@x.com", at="10:00")
    text = _render(
        identities,
        log,
        [labs, standup],
        {
            labs["id"]: {
                "notes": [
                    {
                        "subject": "Notes: “Status | CreateOS Labs” Sep 21, 2026",
                        "permalink": "https://mail/n1",
                    }
                ],
                "points": [
                    {
                        "what": "labs pilot numbers",
                        "why_now": "cfo asked for them last time",
                        "quote": "bring the pilot numbers next month",
                        "permalink": "https://mail/n1",
                    }
                ],
            }
        },
    )

    assert "Status | CreateOS Labs" in text
    assert "DE Standup" not in text
    assert "labs pilot numbers" in text
    assert "https://mail/n1" in text
    assert "mon 9/28" in text


def test_ideas_are_labelled_suggestions_and_only_for_the_ideas_recipe(identities, log):
    ceo = _event("Jo <> Principal", "jo@x.com", at="15:00")
    labs = _event(" Status | CreateOS Labs", "wren.cfo@x.com", "eddie@x.com")
    idea = {
        "what": "ask about the label summit",
        "why_now": "he raised it",
        "permalink": "https://s/1",
    }
    text = _render(
        identities,
        log,
        [ceo, labs],
        {ceo["id"]: {"notes": [], "ideas": [idea]}, labs["id"]: {"notes": [], "ideas": [idea]}},
    )

    assert "ideas / follow-ups - suggestions" in text
    assert text.count("ask about the label summit") == 1, "ideas leaked into a non-ideas call"


def test_an_unreadable_deck_is_one_line_never_a_guess(identities, log):
    steering = _event("Pod Steering", "a@x.com", "b@x.com")
    text = _render(
        identities,
        log,
        [steering],
        {steering["id"]: {"deck": [{"url": "https://docs/d1", "error": "403"}]}},
    )

    assert "couldn't open the deck" in text
    assert "https://docs/d1" in text


def test_a_matched_meeting_with_no_payload_says_so(identities, log):
    steering = _event("Pod Steering", "a@x.com", "b@x.com")

    text = _render(identities, log, [steering], {})

    assert "Pod Steering" in text
    assert "couldn't read" in text


def test_no_matches_is_one_line_naming_the_day(identities, log):
    text = _render(identities, log, [_event("DE Standup", "a@x.com", "b@x.com")], {})

    assert "nothing on mon 9/28 matches a prep rule" in text


def test_more_than_five_points_are_capped(identities, log):
    labs = _event(" Status | CreateOS Labs", "wren.cfo@x.com", "eddie@x.com")
    points = [
        {"what": f"p{n}", "why_now": "because", "quote": f"q{n}", "permalink": f"https://s/{n}"}
        for n in range(8)
    ]

    text = _render(identities, log, [labs], {labs["id"]: {"notes": [], "points": points}})

    assert "p4" in text and "p5" not in text


def test_the_render_is_in_his_voice(identities, log):
    labs = _event(" Status | CreateOS Labs", "wren.cfo@x.com", "eddie@x.com")
    steering = _event("Pod Steering", "a@x.com", "b@x.com", at="09:00")

    text = _render(identities, log, [labs, steering], {labs["id"]: {"notes": []}})

    assert voice_violations(text) == []


def test_prep_ahead_does_not_persist_the_future_day_it_fetched(identities, log):
    """Same reason as a named prep: a meeting cancelled after the snapshot would
    otherwise be a permanent notes gap."""
    _render(identities, log, [_event("Pod Steering", "a@x.com", "b@x.com")], {})

    assert EventLog.open(log).recorded("meeting") == []


def test_rules_are_read_from_his_watchlist_and_his_edit_wins(identities, log, tmp_path):
    folder = tmp_path / "vault" / "DayDAG"
    folder.mkdir(parents=True)
    (folder / "Watchlist.md").write_text(
        "# Watchlist\n\n## Prep rules\n\n- title: revenue review · past-notes\n", encoding="utf-8"
    )
    review = _event("Revenue review", "a@x.com", "b@x.com")
    steering = _event("Pod Steering", "a@x.com", "b@x.com", at="09:00")

    text = _render(identities, log, [review, steering], {review["id"]: {"notes": []}})

    assert "Revenue review" in text
    assert "Pod Steering" not in text, "the seed rule survived his edit"
