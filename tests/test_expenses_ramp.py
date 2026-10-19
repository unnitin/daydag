"""Ramp, the sink since 2026-10-18 - shapes taken from the connector's real answers that day."""

from datetime import date
from decimal import Decimal

import pytest

from daydag import expenses_ramp as ramp

GL = {"category_uuid": "cat-gl", "category_name": "Category", "category_type": "GL_ACCOUNT"}
#: What .env holds in production; synthetic here, the names are the contract.
IDS = {
    "RAMP_TC_GL": "cat-gl",
    "RAMP_TC_DEPARTMENT": "cat-dept",
    "RAMP_TC_DEPARTMENT_OPTION": "opt-dept",
    "RAMP_TC_DIVISION": "cat-div",
    "RAMP_TC_DIVISION_OPTION": "opt-div",
    "RAMP_TC_BUSINESS_UNIT": "cat-bu",
    "RAMP_TC_BUSINESS_UNIT_OPTION": "opt-bu",
    "RAMP_GL_GROUND": "opt-ground",
    "RAMP_GL_MEALS": "opt-meals",
    "RAMP_GL_LODGING": "opt-lodging",
    "RAMP_GL_AIRFARE": "opt-airfare",
}


def item(
    id_,
    amount,
    merchant,
    day,
    status="PENDING",
    trip="t-1",
    gl="607103 - Travel Expense - Ground Transportation",
    memo="m",
):
    return {
        "id": id_,
        "amount_cents": int(Decimal(amount) * 100),
        "amount_decimal": amount,
        "currency": "USD",
        "memo": memo,
        "merchant_name": merchant,
        "transaction_date": day,
        "submitted_date": "2026-10-18T22:05:03.897369" if status != "DRAFT" else None,
        "status": status,
        "fund": None,
        "tracking_category_selections": [
            {
                **GL,
                "category_input_type": "SINGLE_CHOICE",
                "option_selection": {"option_uuid": "o", "option_name": gl},
            }
        ],
        "custom_record_values": [],
        "trip_uuid": trip,
        "attendee_user_uuids": [],
    }


def file_line(**over):
    base = {
        "file": "/x/slip.jpg",
        "merchant": "Linden",
        "amount": "152.39",
        "date": "2026-10-06",
        "category": "Meals",
        "memo": "drinks w/ team",
    }
    base.update(over)
    return base


def test_ramp_items_read_amount_date_status_trip_and_gl():
    payload = {"reimbursements": [item("a", "97.93", "Uber", "2026-10-08")], "total_count": 1}
    (one,) = ramp.ramp_items_in(payload)
    assert (one.amount, one.purchased_on) == (Decimal("97.93"), date(2026, 10, 8))
    assert (one.status, one.trip_uuid) == ("PENDING", "t-1")
    assert one.travel_gl and one.link.endswith("/a")


def test_check_matches_on_amount_and_shows_the_hotel_gap():
    """The hotel Ramp read as $1,203.77 against the $1,177.95 paid: both sides list it,
    the gap says 25.82."""
    expected = ramp.expected_from_file(
        [
            {
                "date": "2026-10-08",
                "merchant": "Uber",
                "amount": "97.93",
                "category": "Rideshare & Taxis",
            },
            {
                "date": "2026-10-08",
                "merchant": "TRB Hollywood Volume",
                "amount": "1177.95",
                "category": "Lodging",
            },
            {"date": "2026-10-05", "merchant": "Intelsat", "amount": "8.00", "category": "Office"},
        ]
    )
    items = ramp.ramp_items_in(
        [
            item("u", "97.93", "Uber", "2026-10-08"),
            item(
                "h",
                "1203.77",
                "Hotel Tonnelle",
                "2026-10-08",
                gl="607102 - Travel Expense - Lodging",
            ),
            item("w", "8.00", "Intelsat", "2026-10-05", trip="", gl="604104 - Office Supplies"),
            item("d", "1177.95", "TRB Hollywood Volume", "2026-10-08", status="DRAFT"),
        ]
    )
    result = ramp.ramp_check(expected, items)
    # the expected folio line matches the DRAFT by amount (flagged "draft");
    # drafts are not counted as pending
    assert {e.merchant for e, _ in result.matched} == {"Uber", "Intelsat", "TRB Hollywood Volume"}
    assert result.total_ramp() == Decimal("97.93") + Decimal("1203.77") + Decimal("8.00")
    assert result.total_expected() == Decimal("97.93") + Decimal("1177.95") + Decimal("8.00")
    text = result.render()
    assert "gap $+25.82" in text
    assert "in ramp, not expected: oct 8 $1203.77 Hotel Tonnelle" in text
    assert "⚠ no trip, gl 604104 - Office Supplies" in text
    assert "drafts not counted $1177.95" in text
    assert "$1177.95 TRB Hollywood Volume -> draft ⚠ draft" in text


def test_staging_prints_four_steps_with_explicit_amount_and_coding():
    files = ramp.files_from_payload([file_line()])
    (s,) = ramp.stage_files(files, trip_uuid="trip-1", identities=IDS)
    steps = s.steps()
    assert [st.get("tool") for st in steps] == [
        ramp.RAMP_TOOL["upload_link"],
        None,
        ramp.RAMP_TOOL["create"],
        ramp.RAMP_TOOL["edit"],
    ]
    assert steps[1]["shell"].startswith('curl -sS -F "file=@/x/slip.jpg"')
    create = steps[2]["args"]
    assert (create["amount"], create["merchant"], create["transaction_date"]) == (
        "152.39",
        "Linden",
        "2026-10-06",
    )
    edit = steps[3]["args"]
    assert edit["trip_uuid"] == "trip-1" and edit["memo"] == "drinks w/ team"
    gl = [sel for sel in edit["tracking_category_selections"] if sel["category_uuid"] == "cat-gl"]
    assert gl == [{"category_uuid": "cat-gl", "option_selection": "opt-meals"}]
    assert len(edit["tracking_category_selections"]) == 4
    assert steps[3]["note"] == ""
    assert "submit" not in str(steps).lower()


def test_unknown_category_still_stages_and_says_so():
    files = ramp.files_from_payload([file_line(file="/x/wifi.pdf", category="Wi-Fi")])
    (s,) = ramp.stage_files(files, identities=IDS)
    edit = s.steps()[3]
    assert "no travel GL known for category 'Wi-Fi'" in edit["note"]
    selections = edit["args"]["tracking_category_selections"]
    assert all(sel["category_uuid"] != "cat-gl" for sel in selections)
    assert len(selections) == 3


def test_missing_env_names_are_named_never_guessed():
    files = ramp.files_from_payload([file_line()])
    (s,) = ramp.stage_files(files, identities={"RAMP_TC_GL": "cat-gl"})
    edit = s.steps()[3]
    assert edit["args"]["tracking_category_selections"] == []
    assert "RAMP_GL_MEALS" in edit["note"] and "RAMP_TC_DEPARTMENT" in edit["note"]


def test_a_file_line_without_an_amount_is_refused():
    with pytest.raises(ValueError):
        ramp.files_from_payload([{"file": "/x/a.jpg", "date": "2026-10-05"}])
