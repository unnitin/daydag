"""What the morning brief shows from State.md and the calendar - the five
content defects the 10/1 morning surfaced.

* #188 a struck row (`~~…~~ ✓`) is done and leaves the push;
* #186 his own steps are owed BY him, not to him;
* #184 a row's link on a link-only sub-bullet is its source - his prose
  comments under a row are never read as one;
* #189 a declined invite is not on his day, and a clash with an invite he
  has not answered is counted, not spelled out;
* #190 an overnight mention names its sender.
"""

from __future__ import annotations

from datetime import UTC, datetime

from daydag import brief, call_notes, run
from daydag.brief import UNSOURCED
from daydag.ledger import Ledger
from daydag.people import People
from daydag.state import EventLog, StateFolder
from test_brief import IDENTITIES, NOW, FakeSources, _assemble, _event, _message

STATE = """# State

## Chase list

- vp-data · the compute plan · [slack](https://x.example.com/1)
- ~~vp-ai · the rollout date~~ ✓ resolved
- ⭐ ~~**⚠ astro PR vs the nightly DAG**~~ ✓ **resolved 2026-09-18**
- vp-ai · the rollout date
\t- [source](https://x.example.com/2)
- eng-2 · the backfill · status open
\t- *"they said friday"*
\t- see https://x.example.com/3 for my note on it
- Alex Rivera · draft the entity diagram · [source](https://x.example.com/4)
- Alex Rivera, vp-ai · demo logistics
\t- [source](https://x.example.com/5)
- ⭐ Alex Rivera · the starred one · [source](https://x.example.com/8)
- **Alex Rivera** · the bold one · [source](https://x.example.com/9)
- alex+vp-data · document the sources · [source](https://x.example.com/10)
- **vp-ai → you** · two actions out of the 1:1 · [slack](https://x.example.com/11)
- vp-ai · moved to ~~friday~~ monday · [slack](https://x.example.com/12)
- vp-data · the second thread
\t- https://x.example.com/13 is the wrong thread, ignore it
\t- [first](https://x.example.com/14)
\t- [second](https://x.example.com/15)
- vp-data · no link before the heading

### new 2026-09-25 (morning loop)

\t- https://x.example.com/16
- eng-2 · the row after a heading · status open

## Owed by you

- send the deck to the sponsor · [slack](https://x.example.com/6)
- ~~already sent the numbers~~ ✓

## Watch items

- **swimlanes** · [doc](https://x.example.com/7)
- **~~swimlanes v0~~** ✓ closed
"""

ALEX = call_notes.Principal(names=("Alex Rivera", "Alex"))


def _brief(tmp_path, *, owns=None, events=(), messages=(), who=None, ledger=None):
    folder = StateFolder.create(tmp_path / "DayDAG")
    folder.state_path.write_text(STATE, encoding="utf-8")
    return _assemble(
        FakeSources(events=events, messages=messages),
        state=folder,
        owns=owns,
        who=who,
        ledger=ledger,
    )


def _section(assembled, key):
    return next((s for s in assembled.sections if s.key == key), None)


# --------------------------------------------------------------------------
# State.md rows
# --------------------------------------------------------------------------


def test_a_struck_row_leaves_the_push_and_its_live_twin_stays(tmp_path):
    text = _brief(tmp_path).render()

    assert text.count("the rollout date") == 1, "struck once, live once: only the live one"
    assert "astro PR" not in text and "already sent" not in text
    assert "swimlanes v0" not in text and "swimlanes" in text
    assert not any(line.startswith("- ~~") for line in text.splitlines())


def test_a_link_only_sub_bullet_is_the_rows_source_and_his_prose_is_not(tmp_path):
    owed = _section(_brief(tmp_path), "owed").lines

    assert "- vp-ai · the rollout date (https://x.example.com/2)" in owed
    backfill = next(line for line in owed if "the backfill" in line)
    assert backfill.endswith(f"({UNSOURCED})"), "a comment that mentions a link is not a source"


def test_his_own_steps_are_owed_by_him(tmp_path):
    assembled = _brief(tmp_path, owns=ALEX.is_him)
    owed, owed_by = _section(assembled, "owed"), _section(assembled, "owed-by")

    assert owed.heading == "owed to you (7)"
    assert not any("Alex Rivera" in line for line in owed.lines)
    assert owed_by.heading == "owed by you (6)"
    for step in ("draft the entity diagram", "the starred one", "the bold one"):
        assert any(step in line for line in owed_by.lines), step
    assert any("demo logistics" in line for line in owed_by.lines), "a step he shares is his"
    assert any("document the sources" in line for line in owed_by.lines), "alex+vp-data is his"
    assert any("two actions" in line for line in owed_by.lines), "'X → you' is his"
    assert not any("send the deck" in line for line in owed_by.lines), (
        "his hand-kept Owed by you section holds narrative; the brief reads only chase rows"
    )
    assert brief.unsourced_claims(assembled.render()) == []


def test_without_an_owner_check_only_an_arrow_to_him_moves(tmp_path):
    assembled = _brief(tmp_path)
    assert _section(assembled, "owed").heading == "owed to you (12)"
    assert _section(assembled, "owed-by").heading == "owed by you (1)"


def test_a_strike_inside_a_live_row_does_not_take_it_out(tmp_path):
    assert "moved to ~~friday~~ monday" in _brief(tmp_path).render()


def test_the_first_link_only_sub_bullet_wins_and_prose_starting_with_a_link_does_not(tmp_path):
    owed = _section(_brief(tmp_path), "owed").lines
    assert "- vp-data · the second thread (https://x.example.com/14)" in owed


def test_a_link_under_a_sub_heading_is_not_the_row_above_it(tmp_path):
    """A sub-bullet never attaches across a heading: the link-only bullet under
    `### new ...` belongs to no row, and the row before it stays as it was."""
    owed = _section(_brief(tmp_path), "owed").lines
    assert not any("x.example.com/16" in line for line in owed)
    assert f"- vp-data · no link before the heading ({UNSOURCED})" in owed
    assert any(line.startswith("- eng-2 · the row after a heading") for line in owed)


def test_a_trailing_citation_label_is_not_left_in_the_words(tmp_path):
    owed = _section(_brief(tmp_path), "owed").lines
    assert "- vp-data · the compute plan (https://x.example.com/1)" in owed


def test_what_the_writer_files_reads_back_sourced(tmp_path):
    """#184's done-when: the writer's rows through `render morning`, no
    line tagged unsourced."""
    vault = tmp_path / "vault"
    folder = StateFolder.create(vault / "DayDAG")
    folder.update_state(
        chase=[
            {
                "key": f"K{i}",
                "owner": "vp-ai",
                "ask": f"the step number {i}",
                "quote": "they said friday",
                "permalink": f"https://mail.example.com/#all/m{i}",
                "asked_on": "2026-09-07",
                "status": "open",
            }
            for i in range(3)
        ]
    )
    text = run.render(
        "morning",
        now=datetime(2026, 9, 7, 13, 40, tzinfo=UTC),
        identities={**IDENTITIES, "EMAIL_PRINCIPAL": "alex.rivera@example.com"},
        payloads={"calendar": [], "slack": [], "gmail": [], "vault": ""},
        state=folder,
    )
    owed = [line for line in text.splitlines() if "the step number" in line]

    assert len(owed) == 3
    assert not any(line.endswith(f"({UNSOURCED})") for line in owed)


# --------------------------------------------------------------------------
# the calendar
# --------------------------------------------------------------------------


def test_a_declined_invite_is_not_on_his_day(tmp_path):
    events = [
        _event("a", "Pod Standup", 9),
        _event("b", "Data Health Check", 9, 15, status="declined"),
    ]
    assembled = _brief(tmp_path, events=events)
    text = assembled.render()

    assert "Data Health Check" not in text
    assert "overlap" not in text, "he answered 'which one' when he declined"
    assert assembled.header.startswith("morning") and "1 meeting" in assembled.header
    assert [e["id"] for e in assembled.events] == ["a"]


def test_the_ledger_still_sees_a_decline_so_it_can_retract_the_row(tmp_path):
    """#169: a decline is how a row seeded earlier in the day goes away."""
    ledger = Ledger()
    ledger.seed_day([_event("b", "Data Health Check", 9)])
    assert [r.summary for r in ledger.open_rows()] == ["Data Health Check"]

    _brief(tmp_path, events=[_event("b", "Data Health Check", 9, status="declined")], ledger=ledger)

    assert ledger.open_rows() == []


def test_a_clash_he_must_choose_is_one_line_citing_the_later_meeting(tmp_path):
    events = [
        _event("a", "Pod Standup", 9, link="https://cal.example.com/a"),
        _event("b", "Vendor call", 9, 30, link="https://cal.example.com/b"),
    ]
    flags = [
        line for line in _brief(tmp_path, events=events).render().splitlines() if "overlap" in line
    ]

    assert flags == [
        "- ⚠ Pod Standup and Vendor call overlap - which one are you in? (https://cal.example.com/b)"
    ]


def test_clashes_with_unanswered_invites_are_counted_on_one_line(tmp_path):
    events = [
        _event("a", "Pod Standup", 9),
        _event("b", "Optional review", 9, 15, status="needsAction"),
        _event("c", "Maybe sync", 9, 30, status="tentative"),
    ]
    flags = [
        line for line in _brief(tmp_path, events=events).render().splitlines() if "overlap" in line
    ]

    assert len(flags) == 1
    assert flags[0].startswith("- ⚠ 3 more overlaps w/ invites you haven't accepted (https://")
    assert "calendar/r/day/2026/9/7" in flags[0]


def test_his_own_meeting_counts_as_his_answer(tmp_path):
    mine = {**_event("a", "Pod Standup", 9, status="needsAction"), "organizer_is_self": True}
    text = _brief(tmp_path, events=[mine, _event("b", "Vendor call", 9, 30)]).render()
    assert "Pod Standup and Vendor call overlap" in text


def test_one_unanswered_clash_is_one_overlap(tmp_path):
    events = [_event("a", "Pod Standup", 9), _event("b", "Maybe", 9, 15, status="tentative")]
    assert "⚠ 1 more overlap w/ invites" in _brief(tmp_path, events=events).render()


def test_a_payload_without_a_response_still_shows_its_clash(tmp_path):
    a, b = _event("a", "Pod Standup", 9), _event("b", "Vendor call", 9, 30)
    del a["response_status"], b["response_status"]
    text = _brief(tmp_path, events=[a, b]).render()

    assert "Pod Standup and Vendor call overlap" in text


# --------------------------------------------------------------------------
# overnight senders
# --------------------------------------------------------------------------


def _mention(**fields):
    message = _message("the numbers are in", NOW.replace(hour=5))
    del message["who"]
    return {**message, **fields}


def test_a_mention_names_its_sender_from_any_field_the_payload_uses(tmp_path):
    for field in ("who", "from", "user_name", "username", "sender", "author"):
        text = _brief(tmp_path / field, messages=[_mention(**{field: "VP-Data"})]).render()
        assert "- VP-Data: " in text, field


def test_the_contract_field_outranks_the_others(tmp_path):
    text = _brief(tmp_path, messages=[_mention(who="VP-Data", **{"from": "UOTHER0001"})]).render()
    assert "- VP-Data: " in text


def test_a_mention_with_only_an_id_is_named_by_the_directory(tmp_path):
    names = {"UVPDATA0001": "VP-Data"}
    text = _brief(tmp_path, messages=[_mention(user="UVPDATA0001")], who=names.get).render()
    assert "- VP-Data: " in text
    assert (
        "- someone: " in _brief(tmp_path / "x", messages=[_mention(user="UNOBODY00001")]).render()
    )


def test_the_runner_names_an_id_from_the_people_directory(tmp_path):
    log = tmp_path / "events.db"
    People(EventLog.open(log)).remember(
        "vp-data", slack_id="UVPDATA0001", display_name="Vp Data", email="vp.data@example.com"
    )
    message = {
        "ts": f"{datetime(2026, 9, 7, 12, 0, tzinfo=UTC).timestamp():.6f}",
        "user": "UVPDATA0001",
        "text": "the numbers are in",
        "permalink": "https://slack.example.com/p/9",
    }
    text = run.render(
        "morning",
        now=datetime(2026, 9, 7, 13, 40, tzinfo=UTC),
        identities={**IDENTITIES, "EMAIL_PRINCIPAL": "alex.rivera@example.com"},
        payloads={"calendar": [], "slack": [message], "gmail": [], "vault": ""},
        log=log,
    )
    assert "- Vp Data: " in text


def test_the_runner_reads_his_own_rows_as_owed_by_him(tmp_path):
    """`owns` is built from EMAIL_PRINCIPAL: a row filed under his name before
    #186 reads as owed by him on the next morning, with no change to the file."""
    folder = StateFolder.create(tmp_path / "DayDAG")
    folder.state_path.write_text(
        "# State\n\n## Chase list\n\n"
        "- Alex Rivera · draft the entity diagram · [source](https://x.example.com/4)\n"
        "- vp-ai · the rollout date · [source](https://x.example.com/2)\n",
        encoding="utf-8",
    )
    text = run.render(
        "morning",
        now=datetime(2026, 9, 7, 13, 40, tzinfo=UTC),
        identities={**IDENTITIES, "EMAIL_PRINCIPAL": "alex.rivera@example.com"},
        payloads={"calendar": [], "slack": [], "gmail": [], "vault": ""},
        state=folder,
    )
    assert "owed to you (1)" in text and "owed by you (1)" in text
    owed_by = text.split("owed by you (1)", 1)[1].split("\n\n", 1)[0]
    assert "draft the entity diagram" in owed_by
