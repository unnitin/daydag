"""Evidence that an open item moved, read from the live sources (#134).

The vault records what he had time to write down. On a day with seventeen
meetings that is nothing, and he said so on 2026-09-15:

    dont just look at weekly note, actually look at calendar, slack and email
    to see how much things have moved, i dont always get the time to move
    things in obsidian

So `eod_wrap` derived "what closed today" from `brief.closed_red_items` - the
checkboxes he ticks himself - and reported `0 closed` on a full day. This
module is the other half: what the live sources say happened, proposed to him
for confirmation and never written as fact.
"""

from datetime import UTC, datetime

import pytest

from daydag.movement import Evidence, Movement, OpenItem, detect, open_items

NOW = datetime(2026, 9, 15, 17, 0, tzinfo=UTC)

WEEKLY_NOTE = """# 0914-0918

## Priorities

- [ ] 🔴 schedule the drokit walkthrough with the partner team *(mine)*
- [ ] 🔴 compute consolidation plan *(tracking: VP-Data)*
- [x] 🔴 send the pod update *(mine)*
"""

STATE = """# State

## Chase list

- vp-data · fruits metadata list for the finance reconciliation · asked-on 2026-09-10 · status open
- gov-lead · define project expectations and coverage timelines · asked-on 2026-09-11 · status open

## Done

- ~~vp-ai · wave 1 retro writeup~~ ✓ closed 2026-09-12
"""


def _slack(text, *, user="U_VPDATA", ts="1789000000.0001"):
    return {"text": text, "user": user, "ts": ts, "permalink": f"https://example.com/p{ts}"}


# -- reading the open items --------------------------------------------------


def test_open_items_come_from_both_the_chase_list_and_the_weekly_note():
    items = open_items(state=STATE, note=WEEKLY_NOTE, note_path="Weekly Notes/0914-0918.md")

    texts = [item.text for item in items]
    assert any("fruits metadata list" in t for t in texts), "the chase list was not read"
    assert any("drokit walkthrough" in t for t in texts), (
        "the weekly note's red items were not read"
    )


def test_an_item_he_has_already_closed_is_not_open():
    """A struck chase row and a ticked red item are both his answer already."""
    items = open_items(state=STATE, note=WEEKLY_NOTE, note_path="p.md")

    texts = " ".join(item.text for item in items)
    assert "wave 1 retro" not in texts, "a struck chase row came back as open"
    assert "send the pod update" not in texts, "a ticked red item came back as open"


def test_every_open_item_says_where_it_was_read_from():
    """House rule 1. A proposal about an item has to be able to cite the item."""
    items = open_items(state=STATE, note=WEEKLY_NOTE, note_path="Weekly Notes/0914-0918.md")

    assert all(item.source for item in items)
    assert {"DayDAG/State.md", "Weekly Notes/0914-0918.md"} == {item.source for item in items}


# -- detection ---------------------------------------------------------------


def test_a_meeting_that_now_exists_is_evidence_the_ask_was_scheduled():
    """His own example, 2026-09-15: "drokit meeting w/ chris has been
    scheduled, you can confirm that yourself through calendar"."""
    items = open_items(state="", note=WEEKLY_NOTE, note_path="p.md")
    calendar = [
        {
            "id": "abc",
            "summary": "drokit walkthrough - partner team",
            "start": "2026-09-18T10:00:00-07:00",
            "htmlLink": "https://example.com/event/abc",
        }
    ]

    found = detect(open_items=items, calendar=calendar, slack=[], gmail=[], now=NOW)

    (row,) = [m for m in found if "drokit" in m.item.text]
    assert row.proposed == "scheduled"
    assert row.evidence[0].source == "calendar"
    assert "drokit walkthrough - partner team" in row.evidence[0].quote


def test_a_message_naming_the_ask_is_evidence_it_moved():
    items = open_items(state=STATE, note="", note_path="p.md")
    slack = [_slack("fruits metadata list is in the workhorse, sending it to finance now")]

    found = detect(open_items=items, calendar=[], slack=slack, gmail=[], now=NOW)

    (row,) = [m for m in found if "fruits" in m.item.text]
    assert row.proposed == "discussed"
    assert row.evidence[0].permalink, "house rule 1: no permalink on the evidence"
    assert "sending it to finance now" in row.evidence[0].quote


def test_an_unrelated_message_is_not_evidence_of_anything():
    """The expensive failure is a false positive: it proposes closing an item
    that is still open, and he learns to stop reading the proposals."""
    items = open_items(state=STATE, note=WEEKLY_NOTE, note_path="p.md")

    found = detect(
        open_items=items,
        calendar=[{"id": "z", "summary": "DE Standup", "start": "2026-09-15T09:15:00-07:00"}],
        slack=[_slack("anyone got a minute to look at the staging deploy")],
        gmail=[{"id": "m", "subject": "lunch?", "snippet": "sushi"}],
        now=NOW,
    )

    assert found == [], f"matched on nothing: {found}"


def test_one_word_in_common_is_not_a_match():
    """Overlap has to be distinctive. "the plan" appears in every message he
    has ever received."""
    items = [OpenItem(key="k", text="compute consolidation plan", owner="vp-data", source="s")]

    found = detect(
        open_items=items,
        calendar=[],
        slack=[_slack("what is the plan for friday")],
        gmail=[],
        now=NOW,
    )

    assert found == []


def test_the_same_item_collects_every_piece_of_evidence_not_the_first():
    items = [
        OpenItem(
            key="k",
            text="fruits metadata list for the finance reconciliation",
            owner="",
            source="s",
        )
    ]
    slack = [
        _slack("fruits metadata list is ready", ts="1789000000.0001"),
        _slack("sent the fruits metadata list to finance", ts="1789000000.0002"),
    ]

    (row,) = detect(open_items=items, calendar=[], slack=slack, gmail=[], now=NOW)

    assert len(row.evidence) == 2, "only the first piece of evidence was kept"


# -- the critical rule (#18), which is the whole point -----------------------


@pytest.mark.guardrail
def test_nothing_this_module_proposes_is_ever_a_closure():
    """#18: "repo/Jira evidence marks a loop *evidence of movement* and
    surfaces it for confirmation - it never auto-closes it. Merged is not the
    same as what was asked for." A scheduled meeting is not a held one, and a
    reply is not an answer."""
    items = open_items(state=STATE, note=WEEKLY_NOTE, note_path="p.md")
    slack = [_slack("fruits metadata list is done, closed, shipped, delivered")]
    calendar = [{"id": "a", "summary": "drokit walkthrough", "start": "2026-09-18T10:00:00-07:00"}]

    found = detect(open_items=items, calendar=calendar, slack=slack, gmail=[], now=NOW)

    assert found, "nothing was detected, so this proves nothing"
    forbidden = {"closed", "done", "complete", "resolved"}
    assert not [m for m in found if m.proposed in forbidden], [m.proposed for m in found]
    assert all(m.proposed in Movement.PROPOSALS for m in found)


@pytest.mark.guardrail
def test_the_module_has_no_write_path():
    """Ingestion's contract 6, held to for the same reason: a detector that can
    write is a detector whose false positives are permanent."""
    import ast
    from pathlib import Path

    import daydag.movement as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert not {"write_text", "update_state", "open", "record"} & set(calls), sorted(set(calls))


# -- pure and total ----------------------------------------------------------


def test_detect_is_total_over_junk():
    """Ingestion's contract 2. A connector hands back whatever it hands back,
    and a detector that raises takes the whole wrap down with it."""
    items = open_items(state=STATE, note=WEEKLY_NOTE, note_path="p.md")

    assert (
        detect(
            open_items=items,
            calendar=[{}, {"summary": None}, "not a mapping"],
            slack=[{}, {"text": None}, 7],
            gmail=[None, {"subject": 3}],
            now=NOW,
        )
        == []
    )


def test_open_items_is_total_over_an_empty_vault():
    assert open_items(state="", note="", note_path="") == []


def test_detecting_twice_returns_the_same_rows():
    """Keyed on the item, so running the loop twice in one evening cannot
    propose the same movement twice."""
    items = open_items(state=STATE, note="", note_path="p.md")
    slack = [_slack("fruits metadata list is with finance now")]

    once = detect(open_items=items, calendar=[], slack=slack, gmail=[], now=NOW)
    twice = detect(open_items=items, calendar=[], slack=slack, gmail=[], now=NOW)

    assert once == twice


def test_evidence_carries_its_own_source_name_and_nothing_invented():
    items = open_items(state=STATE, note="", note_path="p.md")
    gmail = [
        {
            "id": "m1",
            "subject": "fruits metadata list for the finance reconciliation",
            "snippet": "attaching the table",
            "permalink": "https://example.com/mail/m1",
        }
    ]

    (row,) = detect(open_items=items, calendar=[], slack=[], gmail=gmail, now=NOW)

    assert isinstance(row.evidence[0], Evidence)
    assert row.evidence[0].source == "gmail"
    assert row.evidence[0].permalink == "https://example.com/mail/m1"
