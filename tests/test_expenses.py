"""Receipts into Rippling - the sources, the one sink, and what never happens.

Three things are worth pinning as guardrails: a charge summary is never offered
as a receipt, a forward can only ever address the Rippling inbox, and nothing
already in the log is re-sent on its own. The rest is parsing, checked against
the layout his mailbox actually carries. Every place, name, zip and card digit
here is invented; only the shape is real.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from daydag import expenses as ex
from daydag.brief import unsourced_claims
from daydag.state import EventLog

LINK = "https://mail.google.com/mail/#all/thread-f:1|msg-f:{}"

PICKUP = "12 Harbour Rd, Northfield, XA 10001, USA"
DROP = "7 Ridge Ln, Southbank, XA 20002, USA"


def uber_body(kind="ride", *, stamp="Oct 8, 2026 2:08 PM", total="97.93", route=True):
    """The plaintext the Gmail connector hands back for an Uber mail, by kind."""
    day, clock = stamp.rsplit(" ", 2)[0], " ".join(stamp.rsplit(" ", 2)[1:])
    lines = [f"| {day} {clock} |", f"| {day} , {clock} |"]
    if kind == "summary":
        lines += [
            "| This is your charge summary This document acknowledges your trip completion. |",
            f"| Total | ${total} |",
            "| This is not a payment receipt. It is a charge summary to acknowledge the trip. |",
        ]
        return "\n".join(lines)
    greeting = "Thanks for tipping, Sam" if kind == "tip" else "Thanks for riding, Sam"
    lines += [
        "| Tip |" if kind == "tip" else "",
        f"| {greeting} We hope you enjoyed your ride this afternoon. | |",
        f"| Total | ${total} |",
        "| Trip fare | $84.30 |",
        "| Payments |",
        f"| Apple Pay American Express ••••0000 | ${total} |",
        "| Trip details |",
        "| UberX |",
    ]
    if route:
        lines += ["| 2:13 PM |", f"| {PICKUP} |", "| 2:48 PM |", f"| {DROP} |"]
        lines += [f"| {PICKUP} |", f"| {DROP} |"]  # uber repeats the block
    return "\n".join(lines)


def message(
    mid, body=None, *, date="2026-10-09T08:08:23Z", subject="[Business] Your trip", snippet=None
):
    out = {"id": mid, "date": date, "subject": subject, "viewUrl": LINK.format(mid), "sender": "x"}
    if body is not None:
        out["plaintextBody"] = body
    if snippet is not None:
        out["snippet"] = snippet
    return out


# --------------------------------------------------------------------------
# uber: the one source parsed against real mail
# --------------------------------------------------------------------------


@pytest.mark.guardrail
def test_a_charge_summary_is_never_a_receipt():
    """Uber mails the summary first, under the same subject. Forwarding it files nothing."""
    harvest = ex.receipts_in("uber", [message("s1", uber_body("summary"))])
    assert harvest.receipts == ()
    assert [s.reason for s in harvest.skipped] == ["charge summary, not a receipt"]
    assert harvest.skipped[0].message_id == "s1"


def test_an_uber_receipt_carries_amount_date_card_and_route():
    (receipt,) = ex.receipts_in("uber", [message("r1", uber_body())]).receipts
    assert receipt.amount == Decimal("97.93")
    assert receipt.purchased_on == date(2026, 10, 8)
    assert receipt.key == "uber:2026-10-08T14:08"
    assert receipt.merchant == "Uber"
    assert receipt.category == "Rideshare & Taxis"
    assert receipt.paid_with == "Apple Pay American Express ••••0000"
    assert receipt.memo == "uber Northfield -> Southbank"
    assert receipt.permalink == LINK.format("r1")
    assert receipt.supersedes == ()


def test_the_newest_mail_for_a_trip_wins_and_names_what_it_supersedes():
    """A tip restates the whole receipt with a higher total; one trip, one receipt."""
    ride = message("r1", uber_body("ride", total="19.94"), date="2026-06-24T08:09:24Z")
    tip = message("t1", uber_body("tip", total="24.94"), date="2026-06-24T08:09:40Z")
    summary = message("s1", uber_body("summary", total="19.94"), date="2026-06-24T07:00:00Z")
    harvest = ex.receipts_in("uber", [tip, summary, ride])
    (receipt,) = harvest.receipts
    assert receipt.message_id == "t1"
    assert receipt.amount == Decimal("24.94")
    assert receipt.supersedes == ("r1",)


def test_a_snippet_alone_gives_amount_and_date_and_a_plainer_memo():
    """`search_threads` hands back snippets; the body fetch is optional."""
    snippet = (
        "Oct 8, 2026 2:08 PM Oct 8, 2026 , 2:08 PM Thanks for riding, Sam We hope you "
        "enjoyed your ride this afternoon. Total $97.93 $4.55 Uber One credits earned"
    )
    (receipt,) = ex.receipts_in("uber", [message("r1", snippet=snippet)]).receipts
    assert receipt.amount == Decimal("97.93")
    assert receipt.purchased_on == date(2026, 10, 8)
    assert receipt.memo == "uber"
    assert receipt.paid_with == ""


def test_a_ride_inside_one_city_names_the_streets():
    body = uber_body(route=False) + (
        "\n| 2:13 PM |\n| 100 Elm St, Westvale, XA 30003, USA |"
        "\n| 2:20 PM |\n| 200 N Oak Ave, Westvale Heights, XA 30004, USA |"
    )
    (receipt,) = ex.receipts_in("uber", [message("r1", body)]).receipts
    assert receipt.memo == "uber Elm St -> N Oak Ave"


def test_a_receipt_with_no_total_is_skipped_with_a_reason():
    body = "| Oct 8, 2026 2:08 PM |\n| Thanks for riding, Sam |\n| Trip fare | $84.30 |"
    harvest = ex.receipts_in("uber", [message("r1", body)])
    assert harvest.receipts == ()
    assert harvest.skipped[0].reason == "no total line"


def test_search_results_are_read_as_they_arrive():
    """The raw `search_threads` answer, threads and all, needs no reshaping."""
    payload = {"threads": [{"messages": [message("r1", uber_body())]}]}
    assert len(ex.receipts_in("uber", payload).receipts) == 1


# --------------------------------------------------------------------------
# everything else: the generic reader, labelled as such
# --------------------------------------------------------------------------


def test_the_generic_source_reads_a_total_and_the_message_date():
    body = "Your order from Sweetgreen\nSubtotal $21.00\nTotal $23.45\nThanks!"
    msg = message("d1", body, date="2026-10-07T01:15:00Z", subject="Order Confirmation")
    (receipt,) = ex.receipts_in("doordash", [msg]).receipts
    assert receipt.amount == Decimal("23.45")
    assert receipt.purchased_on == date(2026, 10, 7)
    assert receipt.merchant == "DoorDash"
    assert receipt.category == "Meals"
    assert receipt.memo == "Order Confirmation"
    assert ex.SOURCES["doordash"].verified is False


def test_an_unknown_source_names_the_known_ones():
    with pytest.raises(ex.ExpenseError, match="uber"):
        ex.receipts_in("lyft", [])


# --------------------------------------------------------------------------
# the plan: bounded queries that already exclude what was filed
# --------------------------------------------------------------------------


def test_plan_queries_are_bounded_and_exclude_the_expensed_label():
    plan = ex.plan(date(2026, 10, 1), date(2026, 10, 15))
    uber = plan["uber"]["query"]
    assert uber.startswith("from:noreply@uber.com")
    assert 'subject:"trip with Uber"' in uber
    assert "after:2026/10/01" in uber
    assert "before:2026/10/16" in uber  # gmail's before: is exclusive
    assert f"-label:{ex.EXPENSED_LABEL}" in uber
    assert plan["doordash"]["verified"] is False


@pytest.mark.guardrail
def test_a_window_wider_than_the_cap_is_refused():
    with pytest.raises(ex.ExpenseError, match="days"):
        ex.plan(date(2026, 1, 1), date(2026, 10, 15))
    with pytest.raises(ex.ExpenseError, match="before"):
        ex.plan(date(2026, 10, 15), date(2026, 10, 1))


# --------------------------------------------------------------------------
# the sink: one recipient, after his go, logged once
# --------------------------------------------------------------------------


def receipt(**over):
    mid = over.get("message_id", "m1")
    base = dict(
        source="uber",
        key="uber:2026-10-05T10:45",
        merchant="Uber",
        amount=Decimal("11.98"),
        purchased_on=date(2026, 10, 5),
        message_id=mid,
        permalink=LINK.format(mid),
        memo="uber Elm St -> Oak Ave",
        category="Rideshare & Taxis",
    )
    return ex.Receipt(**{**base, **over})


@pytest.mark.guardrail
def test_a_forward_addresses_only_the_rippling_inbox():
    """House rule 2 as a type: there is no field a caller could point elsewhere."""
    forward = ex.forward_spec(receipt(), report="Trip to LA")
    call = forward.call()
    assert call["tool"] == ex.GMAIL_FORWARD_TOOL
    assert call["args"]["to"] == [ex.RIPPLING_RECEIPTS_INBOX]
    assert call["args"]["messageId"] == "m1"
    assert not {"cc", "bcc"} & set(call["args"])
    assert not {"to", "cc", "bcc"} & {f.name for f in ex.Forward.__dataclass_fields__.values()}
    note = call["args"]["forwardText"]
    assert "report: Trip to LA" in note
    assert "category: Rideshare & Taxis" in note
    assert "vendor: Uber" in note and "amount: $11.98" in note and "date: 2026-10-05" in note


@pytest.mark.guardrail
def test_a_receipt_without_a_permalink_is_refused():
    with pytest.raises(ex.ExpenseError, match="permalink"):
        receipt(permalink="")


def test_review_numbers_the_new_and_sets_the_filed_aside(tmp_path):
    log = EventLog.open(tmp_path / "events.db")
    filed = receipt(key="uber:2026-10-05T10:45", message_id="old")
    fresh = receipt(key="uber:2026-10-06T08:45", message_id="new", purchased_on=date(2026, 10, 6))
    ex.mark_forwarded(log, [filed], report="Trip to LA", now=datetime(2026, 10, 7, tzinfo=UTC))

    batch = ex.review(
        {"uber": ex.Harvest((filed, fresh), ())}, filed=ex.forwarded_rows(log), report="Trip to LA"
    )
    assert [(r.n, r.status, r.receipt.message_id) for r in batch.rows] == [
        (1, ex.FILED, "old"),
        (2, ex.NEW, "new"),
    ]
    assert [r.n for r in batch.offered()] == [2]


@pytest.mark.guardrail
def test_a_trip_that_changed_after_filing_is_flagged_and_never_in_go_all(tmp_path):
    """The tip arrived after the forward. Say so; re-send only on a named go."""
    log = EventLog.open(tmp_path / "events.db")
    before = receipt(message_id="ride", amount=Decimal("19.94"))
    ex.mark_forwarded(log, [before], report="", now=datetime(2026, 10, 7, tzinfo=UTC))
    after = receipt(message_id="tip", amount=Decimal("24.94"), supersedes=("ride",))

    batch = ex.review({"uber": ex.Harvest((after,), ())}, filed=ex.forwarded_rows(log))
    (row,) = batch.rows
    assert row.status == ex.CHANGED
    assert row.filed_amount == "19.94"
    assert ex.go(batch, "all") == []
    (forward,) = ex.go(batch, [1])
    assert forward.message_id == "tip"
    assert "19.94" in batch.render()


def test_go_refuses_a_row_that_is_not_there_or_already_filed(tmp_path):
    log = EventLog.open(tmp_path / "events.db")
    filed = receipt()
    ex.mark_forwarded(log, [filed], report="", now=datetime(2026, 10, 7, tzinfo=UTC))
    batch = ex.review({"uber": ex.Harvest((filed,), ())}, filed=ex.forwarded_rows(log))
    with pytest.raises(ex.ExpenseError, match="no row 4"):
        ex.go(batch, [4])
    with pytest.raises(ex.ExpenseError, match="already filed"):
        ex.go(batch, [1])


def test_mark_records_one_private_row_per_forward(tmp_path):
    log = EventLog.open(tmp_path / "events.db")
    now = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
    count = ex.mark_forwarded(
        log, [receipt(), receipt(key="k2", message_id="m2")], report="R", now=now
    )
    assert count == 2
    rows = log.recorded(ex.FORWARDED)
    assert [r["message_id"] for r in rows] == ["m1", "m2"]
    assert rows[0]["amount"] == "11.98"
    assert rows[0]["report"] == "R"
    assert rows[0]["at"] == now.isoformat()
    assert ex.forwarded_rows(log)["k2"]["message_id"] == "m2"


# --------------------------------------------------------------------------
# the render: every row sourced, the report named or proposed
# --------------------------------------------------------------------------


def test_every_rendered_row_carries_its_permalink():
    harvest = ex.Harvest(
        (receipt(), receipt(key="k2", message_id="m2", purchased_on=date(2026, 10, 8))),
        (ex.Skipped("s1", "charge summary, not a receipt", LINK.format("s1")),),
    )
    batch = ex.review({"uber": harvest})
    text = batch.render()
    assert unsourced_claims(text) == []
    assert LINK.format("m1") in text and LINK.format("m2") in text
    assert "- 1. oct 5 · Uber · $11.98 · uber Elm St -> Oak Ave · Rideshare & Taxis (" in text
    assert "skipped 1: charge summary, not a receipt" in text
    assert ex.RIPPLING_RECEIPTS_INBOX in text
    assert "trip oct 5-8" in text  # proposed from the dates
    assert batch.proposed is True


def test_his_report_name_wins_over_the_proposal():
    batch = ex.review({"uber": ex.Harvest((receipt(),), ())}, report="Trip to LA")
    assert batch.report == "Trip to LA"
    assert batch.proposed is False
    assert "Trip to LA" in batch.render()


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        ([date(2026, 10, 5), date(2026, 10, 8)], "trip oct 5-8"),
        ([date(2026, 10, 5)], "trip oct 5"),
        ([date(2026, 9, 29), date(2026, 10, 2)], "trip sep 29 - oct 2"),
        ([], ""),
    ],
)
def test_the_proposed_report_name_reads_the_dates(days, expected):
    assert ex.propose_report(days) == expected


def test_a_batch_round_trips_through_json():
    batch = ex.review({"uber": ex.Harvest((receipt(supersedes=("x",)),), ())}, report="R")
    again = ex.Batch.from_json(batch.to_json())
    assert again == batch
    assert again.rows[0].receipt.amount == Decimal("11.98")


# --------------------------------------------------------------------------
# amex: a statement reconciles, it does not file
# --------------------------------------------------------------------------

AMEX_CSV = """Date,Description,Card Member,Account #,Amount,Reference,Category
10/06/2026,RIDESHARE *TRIP,J DOE,-00000,11.98,'100000001',Transportation
10/07/2026,EXAMPLE HOTEL WESTVALE,J DOE,-00000,264.08,'100000002',Travel
10/09/2026,AMEX PAYMENT,J DOE,-00000,-500.00,'100000003',Payment
"""


def test_amex_rows_are_read_by_header_not_position():
    charges = ex.charges_from_csv(AMEX_CSV)
    assert [c.amount for c in charges] == [Decimal("11.98"), Decimal("264.08"), Decimal("-500.00")]
    assert charges[0].posted_on == date(2026, 10, 6)
    assert charges[1].description == "EXAMPLE HOTEL WESTVALE"


def test_a_csv_without_the_needed_columns_says_which():
    with pytest.raises(ex.ExpenseError, match="amount"):
        ex.charges_from_csv("Date,Description\n10/06/2026,UBER\n")


def test_reconcile_matches_on_amount_within_days_and_lists_the_rest():
    charges = ex.charges_from_csv(AMEX_CSV)
    found = [receipt(), receipt(key="k2", message_id="m2", amount=Decimal("8.50"))]
    result = ex.reconcile(found, charges, days=3)
    assert [(r.message_id, c.description) for r, c in result.matched] == [("m1", "RIDESHARE *TRIP")]
    assert [r.message_id for r in result.receipts_without_charge] == ["m2"]
    assert [c.description for c in result.charges_without_receipt] == ["EXAMPLE HOTEL WESTVALE"]
    text = result.render()
    assert "EXAMPLE HOTEL WESTVALE" in text and "no receipt" in text
    assert "AMEX PAYMENT" not in text  # a credit is not a charge to receipt


# --------------------------------------------------------------------------
# the report: the batch as he will submit it, day by day
# --------------------------------------------------------------------------


def test_by_day_groups_orders_and_sums():
    rows = [
        receipt(
            key="uber:2026-10-06T08:45",
            message_id="b",
            amount=Decimal("12.99"),
            purchased_on=date(2026, 10, 6),
        ),
        receipt(key="uber:2026-10-05T17:54", message_id="a2", amount=Decimal("34.98")),
        receipt(key="uber:2026-10-05T10:45", message_id="a1", amount=Decimal("11.98")),
        receipt(
            key="doordash:x",
            message_id="c",
            amount=Decimal("23.45"),
            category="Meals",
            merchant="DoorDash",
        ),
    ]
    report = ex.by_day(ex.review({"uber": ex.Harvest(tuple(rows), ())}, report="Trip to LA"))
    assert [g.day for g in report.days] == [date(2026, 10, 5), date(2026, 10, 6)]
    assert [r.receipt.message_id for r in report.days[0].rows] == ["c", "a1", "a2"]
    assert report.days[0].subtotal == Decimal("70.41")
    assert report.total == Decimal("83.40")
    assert report.by_category() == {
        "Meals": Decimal("23.45"),
        "Rideshare & Taxis": Decimal("59.95"),
    }
    text = report.render()
    assert text.startswith(
        'expense report "Trip to LA" - 4 expenses, $83.40 over 2 days (oct 5 - oct 6)'
    )
    assert "oct 5 - 3 expenses, $70.41" in text
    assert "- 10:45 am · Uber · $11.98" in text
    assert "- - · DoorDash · $23.45" in text  # no time on a generic receipt
    assert "by category: Meals $23.45, Rideshare & Taxis $59.95" in text
    assert unsourced_claims(text) == []


def test_the_report_marks_what_rippling_already_has(tmp_path):
    log = EventLog.open(tmp_path / "events.db")
    filed = receipt()
    ex.mark_forwarded(log, [filed], report="R", now=datetime(2026, 10, 7, tzinfo=UTC))
    fresh = receipt(key="uber:2026-10-05T17:54", message_id="m2", amount=Decimal("34.98"))
    batch = ex.review(
        {"uber": ex.Harvest((filed, fresh), ())}, filed=ex.forwarded_rows(log), report="R"
    )
    text = ex.by_day(batch).render()
    assert "✓ in rippling" in text
    assert "in rippling already: 1 of 2" in text


def test_an_empty_batch_renders_as_such():
    assert (
        ex.by_day(ex.review({}, report="R")).render() == 'expense report "R" - nothing in the batch'
    )


def test_the_report_command_prints_and_writes(tmp_path, capsys):
    batch = ex.review({"uber": ex.Harvest((receipt(),), ())}, report="Trip to LA")
    batch_path = tmp_path / "batch.json"
    batch_path.write_text(batch.to_json(), encoding="utf-8")
    out = tmp_path / "report.md"
    assert ex.main(["report", str(batch_path), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith(
        'expense report "Trip to LA" - 1 expense, $11.98'
    )
    assert "10:45 am" in capsys.readouterr().out


# --------------------------------------------------------------------------
# the trip: the frame from the calendar, and where each receipt stands in it
# --------------------------------------------------------------------------

CAL = "https://www.google.com/calendar/event?eid={}"
IN_TOWN = "5 Main St, Westvale, XA 30003, USA"


def event(summary, start, end=None, *, location="", description="", all_day=False, eid="e"):
    """One event in the shape `list_events` hands back."""
    key = "date" if all_day else "dateTime"
    return {
        "summary": summary,
        "start": {key: start},
        "end": {key: end or start},
        "location": location,
        "description": description,
        "htmlLink": CAL.format(eid),
    }


def calendar():
    hotel = "Example Hotel Westvale 100 Elm St   Westvale, Xania, USA"
    return {
        "events": [
            event(
                "Flight to Westvale (XA 123)",
                "2026-10-05T06:20:00-07:00",
                "2026-10-05T09:06:00-07:00",
                location="Northfield NFD",
                eid="f1",
            ),
            event(
                "Example Hotel Westvale - Confirmation number: 777",
                "2026-10-05",
                "2026-10-09",
                all_day=True,
                location=hotel,
                description=(
                    "Confirmation number: 777\n\n\nStarts:2026-10-05, Ends: 2026-10-08 \n"
                    f"{hotel} 30003 Tel: 1"
                ),
                eid="h1",
            ),
            event(
                "Stay at Example Hotel Westvale", "2026-10-05", "2026-10-08", all_day=True, eid="h2"
            ),
            event(
                "Dinner in Westvale",
                "2026-10-05T18:30:00-07:00",
                "2026-10-05T20:30:00-07:00",
                location="The Bistro (300 Pine St, Westvale, XA, United States)",
                eid="d1",
            ),
            event("Standup", "2026-10-06T09:00:00-07:00", location="Room 2-Red (10)", eid="s1"),
            event("Someone Out of Office", "2026-10-01", "2026-10-17", all_day=True, eid="o1"),
            event(
                "Flight to Northfield (XA 124)",
                "2026-10-08T10:55:00-07:00",
                "2026-10-08T13:40:00-07:00",
                location="Westvale WVL",
                eid="f2",
            ),
        ]
    }


def westvale_trip(**over):
    base = dict(
        name="Trip to Westvale",
        start=date(2026, 10, 5),
        end=date(2026, 10, 8),
        flights=(
            ex.Flight(
                date(2026, 10, 5),
                "6:20 am",
                "9:06 am",
                "NFD",
                "Westvale",
                "XA 123",
                CAL.format("f1"),
            ),
        ),
        stay=ex.Stay(
            "Example Hotel",
            "100 Elm St, Westvale, XA 30003, USA",
            date(2026, 10, 5),
            date(2026, 10, 8),
            "777",
            CAL.format("h1"),
        ),
    )
    return ex.Trip(**{**base, **over})


def test_the_trip_is_read_off_the_calendar():
    trip = ex.trip_from_calendar(calendar())
    assert trip.name == "Trip to Westvale"
    assert (trip.start, trip.end) == (date(2026, 10, 5), date(2026, 10, 8))
    assert [(f.day, f.origin, f.to_city, f.number, f.departs) for f in trip.flights] == [
        (date(2026, 10, 5), "NFD", "Westvale", "XA 123", "6:20 am"),
        (date(2026, 10, 8), "WVL", "Northfield", "XA 124", "10:55 am"),
    ]
    assert trip.airports() == {"NFD", "WVL"}
    assert trip.stay.name == "Example Hotel Westvale"
    # the description line with the zip, minus the hotel's own name and phone
    assert trip.stay.address == "100 Elm St   Westvale, Xania, USA 30003"
    assert (trip.stay.check_in, trip.stay.check_out) == (date(2026, 10, 5), date(2026, 10, 8))
    assert trip.stay.confirmation == "777"
    assert trip.stay.permalink == CAL.format("h1")
    assert trip.area() == "300"
    assert [(a.day, a.title) for a in trip.anchors] == [(date(2026, 10, 5), "Dinner in Westvale")]


def test_a_calendar_with_no_flight_and_no_stay_is_no_trip():
    only = {"events": [event("Standup", "2026-10-06T09:00:00-07:00")]}
    assert ex.trip_from_calendar(only) is None


def test_his_name_wins_and_the_trip_names_the_report():
    trip = ex.trip_from_calendar(calendar(), name="Trip to LA")
    batch = ex.review({"uber": ex.Harvest((receipt(stops=(IN_TOWN,)),), ())}, trip=trip)
    assert batch.report == "Trip to LA"
    assert batch.proposed is False


@pytest.mark.parametrize(
    ("over", "expected"),
    [
        (
            {"stops": (IN_TOWN, "9 Oak Ave, Westvale, XA 30004, USA")},
            (ex.NEW, "at the destination"),
        ),
        (
            {
                "stops": (
                    "1 Harbour Rd, Northfield, XA 10001, USA",
                    "Gate 1, Northfield Airport (NFD), Northfield, XA 10002, USA",
                ),
                "airports": ("NFD",),
            },
            (ex.NEW, "airport leg (NFD)"),
        ),
        ({"stops": ("100 Elm St, Westvale Heights, XA 31000, USA",)}, (ex.NEW, "at the hotel")),
        ({"state": "XA"}, (ex.NEW, "in XA")),
        ({"state": "ZZ"}, (ex.UNPLACED, "placed in ZZ, the trip is in XA")),
        (
            {"stops": ("1 Far Rd, Elsewhere, ZZ 99999, USA",)},
            (ex.UNPLACED, "placed in Elsewhere, the trip is in XA"),
        ),
        ({}, (ex.UNPLACED, "no place on the receipt - fetch the body, or go by number")),
        ({"purchased_on": date(2026, 10, 12)}, (ex.OUTSIDE, "dated outside oct 5 - oct 8")),
    ],
)
def test_place_puts_a_receipt_where_the_trip_says(over, expected):
    assert ex.place(receipt(**over), westvale_trip()) == expected


@pytest.mark.guardrail
def test_with_a_trip_go_all_sends_only_what_is_placed_inside():
    """In the window is not enough: a ride the trip cannot place waits for a go by number."""
    trip = westvale_trip()
    inside = receipt(key="k1", message_id="m1", stops=(IN_TOWN,))
    nowhere = receipt(key="k2", message_id="m2", purchased_on=date(2026, 10, 6))
    outside = receipt(key="k3", message_id="m3", purchased_on=date(2026, 10, 12))
    batch = ex.review({"uber": ex.Harvest((inside, nowhere, outside), ())}, trip=trip)
    assert [(r.n, r.status) for r in batch.rows] == [(1, ex.NEW), (2, ex.UNPLACED), (3, ex.OUTSIDE)]
    assert [f.message_id for f in ex.go(batch, "all")] == ["m1"]
    assert [f.message_id for f in ex.go(batch, [2])] == ["m2"]
    text = batch.render()
    assert "1 new, 1 in the window but not placed, 1 outside the trip" in text
    assert 'trip "Trip to Westvale" oct 5 - oct 8' in text
    assert "where? no place on the receipt" in text
    assert "- outside the trip: 3. oct 12" in text
    assert unsourced_claims(text) == []
    assert ex.Batch.from_json(batch.to_json()) == batch


def test_a_stop_that_matches_a_calendar_event_names_it_in_the_memo():
    trip = ex.trip_from_calendar(calendar())
    ride = receipt(
        stops=("1 Elm St, Westvale, XA 30003, USA", "300-2 Pine St, Westvale, XA 30003, USA"),
        memo="uber Elm St -> Pine St",
    )
    batch = ex.review({"uber": ex.Harvest((ride,), ())}, trip=trip)
    assert batch.rows[0].receipt.memo == "uber Elm St -> Pine St (Dinner in Westvale)"


def test_the_report_opens_with_the_trip_and_drops_what_is_outside():
    trip = westvale_trip()
    inside = receipt(key="k1", message_id="m1", stops=(IN_TOWN,))
    outside = receipt(key="k3", message_id="m3", purchased_on=date(2026, 10, 12))
    batch = ex.review({"uber": ex.Harvest((inside, outside), ())}, trip=trip)
    text = ex.by_day(batch).render()
    assert 'expense report "Trip to Westvale" - 1 expense, $11.98' in text
    assert "- flight oct 5 6:20 am NFD -> Westvale XA 123, lands 9:06 am (" in text
    assert (
        "- stay Example Hotel, 100 Elm St, Westvale, XA 30003, USA, oct 5 to oct 8, "
        "confirmation 777 (" in text
    )
    assert "oct 12" not in text


def test_the_uber_reader_keeps_the_stops_airports_and_state():
    body = uber_body() + (
        "\n| XA Driver Benefits | $0.82 |\n| NFD Airport Surcharge | $4.50 |"
        "\n| Gate 1, Northfield Airport (NFD), Northfield, XA 10002, USA |"
        "\n| SeaTac Airport Pickup Fee | $6.50 |"
    )
    (r,) = ex.receipts_in("uber", [message("r1", body)]).receipts
    assert r.stops == (PICKUP, DROP)
    assert r.airports == ("NFD", "SEA")
    assert r.state == "XA"


def test_the_trip_command_writes_the_frame_and_plan_takes_its_window(tmp_path, capsys):
    cal = tmp_path / "calendar.json"
    cal.write_text(json.dumps([calendar()]), encoding="utf-8")
    out = tmp_path / "trip.json"
    assert ex.main(["trip", str(cal), "--out", str(out), "--name", "Trip to LA"]) == 0
    assert 'trip "Trip to LA" - oct 5 to oct 8' in capsys.readouterr().out
    assert ex.main(["plan", "--trip", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert "after:2026/10/05" in printed["uber"]["query"]
    assert "before:2026/10/09" in printed["uber"]["query"]


# --------------------------------------------------------------------------
# the confirmation: rippling's reply is the only proof a forward arrived
# --------------------------------------------------------------------------

DRAFT_LINK = (
    "https://link.rippling.com/spendApp?path=/spend-management/expense/abc123/&isDeepLink=true"
)


def rippling_reply(thread="t1", *, amount="11.98", vendor="Uber", day="Oct 5, 2026", mid="c1"):
    body = (
        "| Draft reimbursement created Rippling scanned your receipt and created a draft "
        "reimbursement for you. |\n| |\n"
        f"| | ${amount} | []({DRAFT_LINK}) |\n| {vendor} |\n| Purchase date | Category |\n"
        f"| {day} | Rideshare & Taxis |\n| View draft reimbursement []({DRAFT_LINK}) |"
    )
    return {
        "id": mid,
        "threadId": thread,
        "sender": "receipts@rippling.com",
        "subject": "Fwd: [Business] Your trip",
        "date": "2026-10-16T06:44:28Z",
        "plaintextBody": body,
        "viewUrl": LINK.format(mid),
    }


def test_a_rippling_reply_is_read_into_a_draft():
    (draft,) = ex.drafts_in([rippling_reply()])
    assert draft.thread_id == "t1"
    assert draft.amount == Decimal("11.98")
    assert draft.vendor == "Uber"
    assert draft.purchased_on == date(2026, 10, 5)
    assert draft.category == "Rideshare & Taxis"
    assert draft.link == DRAFT_LINK


def test_the_snippet_of_a_reply_is_enough():
    snippet = (
        "Draft reimbursement created Rippling scanned your receipt and created a draft "
        "reimbursement for you. $34.98 Uber Purchase date Category Oct 5, 2026 "
        "Rideshare &amp; Taxis View draft reimbursement © 2026"
    )
    msg = {"id": "c2", "threadId": "t2", "sender": "receipts@rippling.com", "snippet": snippet}
    (draft,) = ex.drafts_in([msg])
    assert (draft.amount, draft.category, draft.link) == (Decimal("34.98"), "Rideshare & Taxis", "")


def test_other_mail_from_rippling_is_not_a_draft():
    msg = {
        "id": "x",
        "threadId": "t9",
        "sender": "no-reply@rippling.com",
        "snippet": "You just got paid!",
    }
    assert ex.drafts_in([msg]) == []


@pytest.mark.guardrail
def test_check_confirms_only_what_rippling_answered(tmp_path):
    """Sent is not arrived: a forwarded row with no reply stays unconfirmed."""
    log = EventLog.open(tmp_path / "events.db")
    answered = receipt(key="k1", message_id="m1", thread_id="t1")
    silent = receipt(key="k2", message_id="m2", thread_id="t2", amount=Decimal("34.98"))
    fresh = receipt(key="k3", message_id="m3", thread_id="t3", purchased_on=date(2026, 10, 6))
    ex.mark_forwarded(log, [answered, silent], report="R", now=datetime(2026, 10, 16, tzinfo=UTC))
    batch = ex.review(
        {"uber": ex.Harvest((answered, silent, fresh), ())}, filed=ex.forwarded_rows(log)
    )
    stray = rippling_reply("t8", amount="5.00", vendor="Cafe", mid="c8")
    result = ex.check(batch, ex.drafts_in([rippling_reply("t1"), stray]))
    assert [(row.n, d.thread_id) for row, d in result.confirmed] == [(1, "t1")]
    assert [row.n for row in result.unconfirmed] == [2]
    assert [d.vendor for d in result.unmatched] == ["Cafe"]
    text = result.render()
    assert "1 of 2 forwarded confirmed" in text
    assert (
        f"- ✓ 1. oct 5 Uber $11.98 -> draft reimbursement, Rideshare & Taxis ({DRAFT_LINK})" in text
    )
    assert "- ? 2. oct 5 Uber $34.98 - forwarded, no reply from rippling yet (" in text
    assert "- draft with no row in this batch: oct 5 Cafe $5.00 (" in text
    assert unsourced_claims(text) == []


def test_check_falls_back_to_amount_and_date_when_the_thread_differs(tmp_path):
    log = EventLog.open(tmp_path / "events.db")
    sent = receipt(key="k1", message_id="m1", thread_id="old-thread")
    ex.mark_forwarded(log, [sent], report="R", now=datetime(2026, 10, 16, tzinfo=UTC))
    batch = ex.review({"uber": ex.Harvest((sent,), ())}, filed=ex.forwarded_rows(log))
    result = ex.check(batch, ex.drafts_in([rippling_reply("new-thread")]))
    assert len(result.confirmed) == 1 and result.unconfirmed == ()


def test_the_check_command_reads_both_files(tmp_path, capsys):
    log = EventLog.open(tmp_path / "events.db")
    sent = receipt(thread_id="t1")
    ex.mark_forwarded(log, [sent], report="R", now=datetime(2026, 10, 16, tzinfo=UTC))
    batch = ex.review({"uber": ex.Harvest((sent,), ())}, filed=ex.forwarded_rows(log))
    (tmp_path / "batch.json").write_text(batch.to_json(), encoding="utf-8")
    (tmp_path / "replies.json").write_text(
        json.dumps({"threads": [{"messages": [rippling_reply()]}]})
    )
    assert ex.main(["check", str(tmp_path / "batch.json"), str(tmp_path / "replies.json")]) == 0
    assert "1 of 1 forwarded confirmed" in capsys.readouterr().out
