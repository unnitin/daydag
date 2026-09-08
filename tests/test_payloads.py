"""The payload readers, tested away from any one source.

These were private helpers inside `smoke` until review asked why they lived
there. Tested here on their own because that is the actual argument for moving
them: a boundary reader that only ever runs through one caller's checks is
only ever tested through that caller's vocabulary, and the next edge to need
one writes its own copy rather than trusting an untested private.
"""

from __future__ import annotations

from daydag.payloads import (
    error_text,
    first_value,
    flatten,
    has,
    has_all,
    measure,
    records,
)

# --------------------------------------------------------------------------
# records: None and [] are different answers
# --------------------------------------------------------------------------


def test_a_bare_list_is_already_the_records():
    assert records([{"id": 1}]) == [{"id": 1}]


def test_the_first_known_key_holding_a_list_wins():
    assert records({"events": [1, 2], "data": [3]}) == [1, 2]


def test_a_payload_with_no_record_list_is_none_not_empty():
    """The distinction the caller branches on, so it has to survive here."""
    assert records({"status": "ok"}) is None
    assert records("a string") is None
    assert records(None) is None


def test_an_empty_list_stays_an_empty_list():
    assert records({"items": []}) == []


def test_a_known_key_holding_something_that_is_not_a_list_is_skipped():
    """`{"data": {...}}` is not a page of records, and reading it as one is how
    a single object gets counted as a result set."""
    assert records({"data": {"id": 1}}) is None


# --------------------------------------------------------------------------
# error_text: the shape clients actually use
# --------------------------------------------------------------------------


def test_a_nested_error_object_is_flattened_not_missed():
    assert "401" in error_text({"error": {"code": 401, "message": "denied"}})


def test_a_list_of_error_messages_is_joined():
    assert error_text({"errorMessages": ["bad jql", "no project"]}) == "bad jql no project"


def test_a_payload_carrying_no_error_says_so_with_an_empty_string():
    assert error_text({"items": []}) == ""
    assert error_text("not a mapping") == ""


def test_a_falsy_error_field_is_not_an_error():
    """`{"errors": []}` is a successful call that reported no errors."""
    assert error_text({"errors": []}) == ""
    assert error_text({"error": None}) == ""


def test_message_alone_is_not_an_error():
    """Deliberate: a `message` key is ordinary in a successful payload."""
    assert error_text({"message": "created"}) == ""


# --------------------------------------------------------------------------
# has / has_all: the distinction that let metadata-only results through
# --------------------------------------------------------------------------


def test_has_is_any_and_has_all_is_every():
    record = {"id": "1", "subject": ""}
    assert has(record, "id", "subject")
    assert not has_all(record, "id", "subject")


def test_neither_accepts_something_that_is_not_a_record():
    assert not has("string", "id")
    assert not has_all(None, "id")


def test_an_empty_value_does_not_count_as_carried():
    assert not has({"id": ""}, "id")


# --------------------------------------------------------------------------
# flatten, measure, first_value
# --------------------------------------------------------------------------


def test_flatten_reaches_every_leaf():
    assert flatten({"a": [1, {"b": 2}], "c": "x"}) == ["1", "2", "x"]


def test_measure_reads_a_string_as_itself_not_as_its_repr():
    """`repr` on a string adds quotes and escapes, which is not the size that
    came back over the wire."""
    assert measure("x" * 10) == 10
    assert measure({"a": 1}) == len(repr({"a": 1}))


def test_first_value_handles_every_row_shape_a_driver_returns():
    assert first_value({"1": 1}) == 1
    assert first_value([7, 8]) == 7
    assert first_value(5) == 5
    assert first_value({}) is None
    assert first_value([]) is None
