"""Call-note items reach the State.md chase list, and read the same there as
in the DM (#180).

The principal, 2026-09-25: "i think we should treat sensitive items the same"
/ "i think wire everything consistently for now, we can change later". A
missed DM used to mean a missed item - the sweep marks a note seen and nothing
else remembered what was in it.

Synthetic throughout - the repo is public. Every vault is a tmp_path copy.
"""

from __future__ import annotations

import pytest
from tests.test_call_notes import (
    DECISIONS,
    EVENTS,
    NOTES,
    PRINCIPAL,
    PT,
    STANDUP,
    body,
    mail,
)
from tests.test_call_notes_run import SWEEP, _payloads

from daydag import call_notes, run, state
from daydag.config import Identities
from daydag.state import VAULT_BOUND, EventLog, StateFolder


@pytest.fixture
def folder(tmp_path) -> StateFolder:
    return StateFolder.create(tmp_path / "vault" / "DayDAG")


@pytest.fixture
def identities(tmp_path):
    """The runner tests' identities: a temp vault, the principal and one report."""
    env = tmp_path / ".env"
    env.write_text(
        "SLACK_USER_PRINCIPAL=UPRINCIPAL1\n"
        "EMAIL_PRINCIPAL=alex.rivera@example.com\n"
        "EMAIL_VP_DATA=sam.okafor@example.com\n"
        f"VAULT_ROOT={tmp_path / 'vault'}\n",
        encoding="utf-8",
    )
    (tmp_path / "vault" / "Weekly Notes").mkdir(parents=True)
    return Identities.from_file(env)


def _sweep(log, notes=NOTES, decisions=DECISIONS):
    return call_notes.sweep(notes, EVENTS, PRINCIPAL, log=log, decisions=decisions, tz=PT)


def _filed(log: EventLog):
    return log.chase_items(kinds={call_notes.CHASED})


# --------------------------------------------------------------------------
# what gets recorded
# --------------------------------------------------------------------------


@pytest.mark.guardrail
def test_the_chase_kind_is_vault_bound_so_it_must_carry_a_sensitivity_mark():
    assert call_notes.CHASED in VAULT_BOUND


def test_actionable_items_are_recorded_and_the_rest_are_not(tmp_path):
    """Assigned to him, touches an open decision, asks to his reports. The
    'everything else' bucket stays in the DM only - it is status, not a loop."""
    log = EventLog.open(tmp_path / "e.db")
    _sweep(log)

    asks = " | ".join(item.ask for item in _filed(log))
    assert "Run Data Checks" in asks  # his
    assert "Push Staging" in asks  # a report's
    assert "Revenue Spine Grain" in asks  # touches an open decision
    assert "Draft Promotion Case" in asks  # a report's, from the 1:1
    assert "Refactor Tickets" not in asks  # everything else
    assert "Consolidate Notes" not in asks


def test_each_item_carries_owner_quote_permalink_asked_on_attendance_and_status(tmp_path):
    log = EventLog.open(tmp_path / "e.db")
    _sweep(log)

    item = next(i for i in _filed(log) if "Run Data Checks" in i.ask)
    assert item.owner == "Alex Rivera"
    assert item.quote.startswith("Run Data Checks: Execute data quality checks to determine")
    assert item.permalink == "https://mail.example.com/#all/m1"
    assert item.asked_on == "2026-09-25"
    assert item.status == "open"
    assert item["attendance"] == call_notes.UNCONFIRMED
    assert item["marker"] == "(from Pod Standup - not sure you were in it)"
    assert item["message_id"] == "m1"


def test_the_dedupe_key_is_the_message_id_and_the_item_text(tmp_path):
    """Two notes carrying the same step are two items in the log; one note
    swept twice is one."""
    log = EventLog.open(tmp_path / "e.db")
    again = mail("m9", "Pod Standup", "2026-09-25T20:00:00Z", STANDUP)

    _sweep(log, notes=[NOTES[0]])
    _sweep(log, notes=[NOTES[0]])
    _sweep(log, notes=[again])

    keys = [i.key for i in _filed(log) if "Run Data Checks" in i.ask]
    assert len(keys) == 2 and len(set(keys)) == 2
    assert all(k.startswith("call:") for k in keys)


def test_a_log_whose_seen_set_was_lost_does_not_record_an_item_twice(tmp_path):
    """The seen-set and the items live in one log, but they are separate rows:
    a hand-pruned `note_ingested` row must not double every item."""
    log = EventLog.open(tmp_path / "e.db")
    _sweep(log, notes=[NOTES[0]])
    log._db.execute("DELETE FROM events WHERE kind = ?", (call_notes.INGESTED,))
    log._db.commit()
    _sweep(log, notes=[NOTES[0]])

    assert len([i for i in _filed(log) if "Run Data Checks" in i.ask]) == 1


def test_every_item_is_recorded_with_its_sensitivity_mark(tmp_path):
    """The mark is kept on every record so house rule 7 can be switched back
    without re-deriving anything - even though, today, it withholds nothing."""
    log = EventLog.open(tmp_path / "e.db")
    _sweep(log)

    marks = {i.ask.split(":")[0]: i.sensitivity for i in _filed(log)}
    assert marks["Draft Promotion Case"] == "private"
    assert marks["Push Staging"] == "normal"


# --------------------------------------------------------------------------
# into State.md - append-only, his edits win
# --------------------------------------------------------------------------


def test_items_are_appended_to_the_chase_list_through_the_one_writer(tmp_path, folder):
    log = EventLog.open(tmp_path / "e.db")
    _sweep(log)
    folder.update_state(chase=_filed(log))

    text = folder.read_state()
    chase = text.split("## Chase list", 1)[1].split("## Watch items", 1)[0]
    assert "Run Data Checks" in chase
    assert "(from Pod Standup - not sure you were in it)" in chase
    assert "asked-on 2026-09-25" in chase and "status open" in chase
    assert "[source](https://mail.example.com/#all/m1)" in chase


def test_two_sweeps_append_each_item_exactly_once(tmp_path, folder):
    """The done-when of #180, on synthetic notes: two sweeps, one row each."""
    log = EventLog.open(tmp_path / "e.db")
    for _ in range(2):
        _sweep(log)
        folder.update_state(chase=_filed(log))

    assert folder.read_state().count("Run Data Checks: Execute") == 2  # head + quote
    assert folder.read_state().count("- Alex Rivera · Run Data Checks") == 1


def test_a_hand_edited_row_is_left_alone_and_not_refiled(tmp_path, folder):
    log = EventLog.open(tmp_path / "e.db")
    _sweep(log, notes=[NOTES[0]])
    folder.update_state(chase=_filed(log))

    edited = folder.read_state().replace(
        "- Alex Rivera · Run Data Checks", "- ~~Alex Rivera · Run Data Checks"
    )
    edited = edited.replace("status open\n", "status open~~ ✓\n\t- done, sent to Sam\n", 1)
    folder.state_path.write_text(edited, encoding="utf-8")

    _sweep(log, notes=[NOTES[0]])
    folder.update_state(chase=_filed(log))

    assert folder.read_state() == edited


# --------------------------------------------------------------------------
# the DM and the vault read the same (strict mode removed)
# --------------------------------------------------------------------------


def test_the_dm_shows_every_item_it_files(tmp_path):
    log = EventLog.open(tmp_path / "e.db")
    text = _sweep(log)

    for item in _filed(log):
        assert item.ask in text, item.ask


@pytest.mark.guardrail
def test_a_sensitive_item_reads_the_same_in_the_dm_and_in_state_md(tmp_path, folder):
    """The deliberate change: a comp step is quoted in the DM and filed in
    State.md, word for word the same."""
    log = EventLog.open(tmp_path / "e.db")
    text = _sweep(log, notes=[NOTES[3]])
    folder.update_state(chase=_filed(log))

    (item,) = _filed(log)
    assert "Draft Promotion Case: Write up the promotion packet and salary ask." in text
    assert item.ask in text and item.ask in folder.read_state()
    assert "personnel/comp" not in text


def test_a_step_from_a_sensitive_note_is_quoted_whole_not_title_only(tmp_path):
    note = mail(
        "m8",
        "Alex / Sam - 1:1",
        "2026-09-25T19:43:22Z",
        body(
            "Alex / Sam - 1:1",
            ["Alex suggested capping the contractor's compensation."],
            ["[Alex Rivera] Discuss Contracting: Consult finance about the contract."],
        ),
    )
    text = _sweep(EventLog.open(tmp_path / "e.db"), notes=[note])

    assert "Consult finance about the contract" in text
    assert "detail not quoted" not in text


def test_sensitive_speech_is_quoted_as_attendance_evidence():
    evidence = body("x", ["Alex suggested capping the salary band for the role."], [])
    verdict = call_notes.attendance("needsAction", evidence, PRINCIPAL)

    assert verdict.status == call_notes.ATTENDED
    assert "salary band" in verdict.reason


# --------------------------------------------------------------------------
# the runner: render ingest --write-state
# --------------------------------------------------------------------------


def test_ingest_with_write_state_files_the_items_once_across_two_sweeps(identities, tmp_path):
    log = tmp_path / "events.db"
    for _ in range(2):
        text = run.render(
            "ingest",
            now=SWEEP,
            identities=identities,
            payloads=_payloads(),
            log=log,
            write_state=True,
        )
    written = (tmp_path / "vault" / "DayDAG" / "State.md").read_text(encoding="utf-8")

    assert written.count("- Alex Rivera · Run Data Checks") == 1
    assert "Push Staging" in written
    assert "nothing new" in text  # the second sweep


def test_ingest_says_how_many_it_filed(identities, tmp_path):
    text = run.render(
        "ingest",
        now=SWEEP,
        identities=identities,
        payloads=_payloads(),
        log=tmp_path / "events.db",
        write_state=True,
    )
    # his two (Run Data Checks, Review Deck) and his report's one (Push Staging)
    assert "filed 3 to the State.md chase list" in text, text


def test_ingest_without_write_state_leaves_the_vault_alone(identities, tmp_path):
    run.render(
        "ingest", now=SWEEP, identities=identities, payloads=_payloads(), log=tmp_path / "e.db"
    )
    state_md = tmp_path / "vault" / "DayDAG" / "State.md"
    assert not state_md.exists() or "Run Data Checks" not in state_md.read_text(encoding="utf-8")


def test_ingest_ranks_against_open_decisions_in_the_vault(identities, tmp_path):
    folder = StateFolder.create(tmp_path / "vault" / "DayDAG")
    folder.decisions_path.write_text(
        "# Pending decisions\n\n"
        "- **2026-09-25 · revenue spine grain - ISRC or movement?** · open\n",
        encoding="utf-8",
    )
    run.render(
        "ingest",
        now=SWEEP,
        identities=identities,
        payloads=_payloads(),
        log=tmp_path / "e.db",
        write_state=True,
    )
    assert "Revenue Spine Grain" in folder.read_state()


def test_ingest_files_only_call_note_items_not_other_producers(identities, tmp_path):
    """Scope (#180): ingest projects its own kind. A `loop_opened` row the log
    carries is still the morning/EOD projection's to file."""
    log = tmp_path / "events.db"
    EventLog.open(log).record(
        "loop_opened", sensitivity="normal", key="CDI-1", ask="compute consolidation", day=0
    )
    run.render(
        "ingest", now=SWEEP, identities=identities, payloads=_payloads(), log=log, write_state=True
    )
    assert "compute consolidation" not in (tmp_path / "vault" / "DayDAG" / "State.md").read_text(
        encoding="utf-8"
    )


@pytest.mark.guardrail
def test_rule_7_is_off_by_the_principals_decision_until_he_tightens_it():
    """The switch's default IS the decision (#180, 2026-09-25). Flipping it is
    a guardrail change and comes with the docs that state the rule."""
    assert state.WITHHOLD_PRIVATE_FROM_VAULT is False
