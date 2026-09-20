"""The event log: the store outside the vault (ARCHITECTURE, "State: two stores").

Two stores with opposite requirements. The vault folder is human-editable
markdown, and `test_statedoc.py` describes it; this one is SQLite under
`~/.local/state/`, holding history, mirror fetch times, the chase-item rows
and anything sensitive. These tests pin what `EventLog` records and hands back:
`ChaseItem` as the one shape both sides are held to (#63), the last clean fetch
per mirror (#60), and the sensitivity gate failing CLOSED (#105) - a
vault-bound row without a sensitivity is refused rather than defaulted, and
`classify_sensitivity` reads house rule 7's vocabulary. Anything that then
crosses into `State.md` is asserted on the other side of the seam.
"""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest

from daydag.eventlog import ChaseItem, EventLog, SensitivityRequired, classify_sensitivity

#: A fixed offset, so an offset other than UTC is exercised without pulling in
#: a tz database or depending on the machine's own zone.
PACIFIC = timezone(timedelta(hours=-7))


# -- mirror fetch times (#60) ---------------------------------------------


def test_the_log_remembers_when_each_mirror_last_fetched_cleanly():
    """A stale mirror's line is dated from here, so the log is what carries it.

    Per repo, and the *latest* success wins - the log is append-only, so a repo
    that has fetched a hundred times has a hundred rows and only the newest one
    is the answer to "how old is what you are showing me".
    """
    log = EventLog.open(":memory:")
    log.record_fetch("ExampleOrg/service-a", at=datetime(2026, 9, 5, 6, 40, tzinfo=UTC))
    log.record_fetch("ExampleOrg/service-a", at=datetime(2026, 9, 7, 6, 40, tzinfo=UTC))
    log.record_fetch("ExampleOrg/service-b", at=datetime(2026, 9, 6, 6, 40, tzinfo=UTC))

    assert log.last_fetch("ExampleOrg/service-a") == datetime(2026, 9, 7, 6, 40, tzinfo=UTC)
    assert log.last_fetch("ExampleOrg/service-b") == datetime(2026, 9, 6, 6, 40, tzinfo=UTC)


def test_a_repo_that_has_never_fetched_has_no_last_fetch():
    """`None`, not `now()`. A default of "now" would date a mirror that has
    never once been read as if it were fresh - the exact lie #60 is about."""
    assert EventLog.open(":memory:").last_fetch("ExampleOrg/never-seen") is None


def test_an_unparseable_fetch_row_is_skipped_rather_than_crashing_the_pulse():
    """The log is a file on disk a human can also touch. A row that is not a
    timestamp must not take the 6:40am brief down with it."""
    log = EventLog.open(":memory:")
    log.record("mirror_fetched", repo="ExampleOrg/service-a", at="last tuesday")
    log.record_fetch("ExampleOrg/service-a", at=datetime(2026, 9, 7, 6, 40, tzinfo=UTC))

    assert log.last_fetch("ExampleOrg/service-a") == datetime(2026, 9, 7, 6, 40, tzinfo=UTC)


def test_a_naive_stamp_beside_an_aware_one_does_not_crash_the_comparison():
    """The failure mode this cost: one naive row and one aware row for the same
    repo made them incomparable, and `>` raised TypeError inside `ensure` -
    which catches only MirrorUnavailable, so the whole brief died on a stamp.

    A naive stamp is read as UTC rather than refused. The caller is a scheduled
    pre-step; a missing tzinfo must not stop a morning.
    """
    log = EventLog.open(":memory:")
    log.record_fetch("ExampleOrg/service-a", at=datetime(2026, 9, 5, 6, 40))  # naive
    log.record_fetch("ExampleOrg/service-a", at=datetime(2026, 9, 7, 6, 40, tzinfo=UTC))
    log.record("mirror_fetched", repo="ExampleOrg/service-a", at="2026-09-06T06:40:00")  # by hand

    assert log.last_fetch("ExampleOrg/service-a") == datetime(2026, 9, 7, 6, 40, tzinfo=UTC)


def test_a_stamp_in_another_offset_comes_back_as_utc():
    """Two mirrors dated in two offsets would otherwise render side by side in
    one block, both looking like local time and neither saying which."""
    log = EventLog.open(":memory:")
    log.record_fetch("ExampleOrg/service-a", at=datetime(2026, 9, 7, 6, 40, tzinfo=PACIFIC))

    fetched = log.last_fetch("ExampleOrg/service-a")

    assert fetched == datetime(2026, 9, 7, 13, 40, tzinfo=UTC)
    assert fetched.utcoffset() == timedelta(0)


# --------------------------------------------------------------------------
# the chase-item shape (#63): EventLog and write_state agree on one contract
# --------------------------------------------------------------------------


def test_chase_items_returns_the_one_shape_both_sides_are_held_to():
    """`EventLog.chase_items` builds `ChaseItem`, not a bare dict merge."""
    log = EventLog.open(":memory:")
    log.record(
        "loop_opened",
        sensitivity="normal",
        key="a",
        owner="seth",
        ask="compute consolidation",
        day=0,
    )

    (item,) = log.chase_items()

    assert isinstance(item, ChaseItem)
    # Both access styles work, so every existing caller - dict-style or
    # attribute-style - keeps working unchanged.
    assert item["owner"] == "seth" == item.get("owner") == item.owner
    assert item["ask"] == "compute consolidation"
    assert item.get("sensitivity") == "normal"


# --------------------------------------------------------------------------
# what review found after the first pass at #63
# --------------------------------------------------------------------------


def test_a_chase_item_keeps_fields_outside_its_own_schema():
    """`ChaseItem` contract 3 claims it is "a drop-in wherever a chase item was
    already a bare dict - every existing caller".

    `chase_items()` used to return `{**payload, ...}`, so a caller reading
    `item["day"]` got it. Whitelisting the nine named fields silently dropped
    every other key, which makes the drop-in claim false and turns an existing
    read into a `KeyError`. The nine are GUARANTEED to exist; they were never
    meant to be all there is.
    """
    log = EventLog(sqlite3.connect(":memory:"))
    log.record("loop_opened", sensitivity="normal", key="k1", owner="VP-Data", ask="ship it", day=5)

    item = log.chase_items()[0]

    assert item["day"] == 5, "a recorded field vanished on the way out"
    assert item["owner"] == "VP-Data"
    assert item["status"] == "open", "the guaranteed fields still get defaults"


def test_reading_a_chase_item_out_of_a_hand_written_row_never_raises():
    """`from_payload`'s docstring says "never raises" - a human can hand-edit
    this log, so a row whose payload is valid JSON but not an object must
    degrade, not take `update_state` down with an AttributeError."""
    log = EventLog(sqlite3.connect(":memory:"))
    log._db.execute(
        "INSERT INTO events (kind, sensitivity, payload) VALUES (?, ?, ?)",
        ("carry_forward", "normal", "null"),
    )
    log._db.commit()

    items = log.chase_items()

    assert all(not item.has_owner_or_ask for item in items if not item.get("owner"))


def test_the_logs_sensitivity_column_outranks_a_payload_that_claims_otherwise():
    """The column is the trusted fact; a payload key is not.

    `from_payload` read `payload.get("sensitivity", sensitivity)`, so a payload
    carrying `"normal"` outranked a column saying `"private"`. Unreachable
    through `record()`, which takes sensitivity keyword-only and consumes it -
    but this log is one a human hand-edits, which `from_payload`'s own
    docstring is built around, and a hand-written row leaked straight into
    plaintext `State.md`.
    """
    log = EventLog(sqlite3.connect(":memory:"))
    log._db.execute(
        "INSERT INTO events (kind, sensitivity, payload) VALUES (?,?,?)",
        (
            "carry_forward",
            "private",
            '{"owner":"o","ask":"SECRET","key":"k","sensitivity":"normal"}',
        ),
    )
    log._db.commit()

    (item,) = log.chase_items()

    assert item.get("sensitivity") == "private", "the payload outranked the column"


def test_a_caller_passing_a_plain_dict_still_gets_its_own_sensitivity_honoured():
    """The other call site has no column to trust - `update_state` is handed a
    dict whose own `sensitivity` is the only source there, so it must win."""
    assert (
        ChaseItem.from_payload({"owner": "o", "sensitivity": "private"}).get("sensitivity")
        == "private"
    )


# --------------------------------------------------------------------------
# guardrail 3 fails CLOSED (#105)
# --------------------------------------------------------------------------


def test_a_vault_bound_record_without_a_sensitivity_is_refused(tmp_path):
    """The gate filters what is MARKED private. Nothing marked automatically, so
    an unmarked comp item reached a plaintext State.md. Now the writer cannot
    forget: the omission raises instead of defaulting to normal."""
    log = EventLog.open(tmp_path / "events.db")

    with pytest.raises(SensitivityRequired):
        log.record("carry_forward", owner="VP-AI", ask="comp: 145k base plus equity", key="k1")
    with pytest.raises(SensitivityRequired):
        log.record("loop_opened", owner="VP-Data", ask="the compute plan", key="k2")


def test_a_kind_that_never_reaches_the_vault_still_defaults(tmp_path):
    """Run-log rows and remembered meetings are not projected; requiring the
    argument there would be ceremony with no gate behind it."""
    log = EventLog.open(tmp_path / "events.db")
    log.record("run", loop="morning")
    log.record("meeting", id="e1")

    assert len(log.recorded("run")) == 1


def test_classify_reads_house_rule_7_vocabulary_as_private():
    for text in (
        "comp: 145k base plus equity",
        "discussed her relocation package",
        "PIP conversation with the contractor",
        "term sheet from the acquirer",
        "headcount for Q4",
    ):
        assert classify_sensitivity(text) == "private", text


def test_classify_reads_a_dm_origin_as_private_whatever_the_words():
    assert classify_sensitivity("can you send the deck", origin="dm") == "private"
    assert classify_sensitivity("standup moved to 9:15", origin="mpim") == "private"


def test_classify_leaves_ordinary_work_normal():
    assert classify_sensitivity("the compute consolidation plan") == "normal"
    assert classify_sensitivity("cutover rehearsal for CDI-596", origin="channel") == "normal"


def test_a_misspelt_or_non_string_sensitivity_is_refused_too(tmp_path):
    """`_is_private` compares for equality, so "Private", "privat" and True
    would pass a None check and then render as visible - the same road #105's
    unmarked item took, one letter longer."""
    log = EventLog.open(tmp_path / "events.db")

    for bad in ("Private", "privat", True, "", "secret"):
        with pytest.raises(SensitivityRequired):
            log.record("carry_forward", sensitivity=bad, owner="x", ask="y", key="k")
    assert log.chase_items() == []


def test_the_refusal_is_not_a_value_error():
    """The house pattern wraps decoding in `except ValueError`; a refusal that
    handler could swallow is not a refusal."""
    assert not issubclass(SensitivityRequired, ValueError)


def test_classify_does_not_trip_on_a_teams_everyday_vocabulary():
    """Tokens dropped from the floor after false positives on real text: a
    team that writes code says `pip`, `raise` and `200k rows` every day."""
    for text in (
        "pip install failed on the runner",
        "raise the timeout to 30s",
        "backfill of 200k rows finished",
        "stock photos for the deck",
        "the compute consolidation plan",
    ):
        assert classify_sensitivity(text) == "normal", text


def test_classify_catches_inflections_and_the_escaped_ampersand():
    for text in (
        "two promotions to announce",
        "M&amp;A update from the bankers",
        "laid off the contractors",
        "stock options refresh",
        "exit interview notes",
    ):
        assert classify_sensitivity(text) == "private", text


def test_a_record_stored_whole_may_carry_the_logs_own_parameter_names():
    """A caller storing a connector's record hands it over as a mapping, so a
    key named `kind` or `sensitivity` in the RECORD is data, never an argument.
    Google's calendar records all carry `kind`, and `run._remember` splatted
    them into this signature and raised (found consolidating the seam tests)."""
    log = EventLog.open(":memory:")
    log.record("meeting", {"id": "e1", "kind": "calendar#event", "sensitivity": "n/a"})
    log.record("meeting", {"id": "e2"}, summary="keywords still work beside it")

    assert log.recorded("meeting") == [
        {"id": "e1", "kind": "calendar#event", "sensitivity": "n/a"},
        {"id": "e2", "summary": "keywords still work beside it"},
    ]


def test_a_key_given_both_in_the_payload_and_as_a_keyword_is_refused():
    """`{**payload, **fields}` let a keyword silently overwrite the record's own
    value, with no documented precedence. Refused instead, the way Python
    refuses `f(a=1, **{"a": 2})`; nothing is written."""
    log = EventLog.open(":memory:")

    with pytest.raises(TypeError, match="summary"):
        log.record("meeting", {"id": "e1", "summary": "from google"}, summary="from the caller")

    assert log.recorded("meeting") == []


def test_an_instant_at_any_depth_is_stored_as_its_isoformat():
    """`run._remember` pre-walked one level; google nests the instant as
    `{"dateTime": ...}`, and a datetime one level down raised TypeError out of
    `record` AFTER the push text was built - a dead push. The log owns its
    encoding: an instant at any depth is its isoformat, and a value json
    cannot take at all is its `str` rather than a raise."""
    log = EventLog.open(":memory:")
    when = datetime(2026, 9, 7, 15, 0, tzinfo=UTC)

    log.record("meeting", {"id": "e1", "start": {"dateTime": when}, "day": when.date()})
    log.record("meeting", {"id": "e2", "odd": {1, 2}})

    assert log.recorded("meeting") == [
        {"id": "e1", "start": {"dateTime": "2026-09-07T15:00:00+00:00"}, "day": "2026-09-07"},
        {"id": "e2", "odd": "{1, 2}"},
    ]
