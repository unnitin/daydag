"""Call notes: which calls he was in, and what came out of all of them (#168).

Every name, address and id below is synthetic. The shapes are real: the note
bodies follow the Gemini PLAIN_TEXT layout measured on 2026-09-25 (header
boilerplate, "Quick Notes" prose, "Suggested next steps" as `[Owner] Title:
text`, then the footer), and the calendar rows carry the per-event
`response_status` the ledger already reads.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from daydag import call_notes
from daydag.call_notes import (
    ATTENDED,
    NOT_ATTENDED,
    UNCONFIRMED,
    Principal,
    attendance,
    next_steps,
    spoke,
)
from daydag.state import EventLog

PT = call_notes.recipes.PACIFIC

PRINCIPAL = Principal(
    names=("Alex Rivera", "Alex"),
    reports=("Sam Okafor", "Jo Lindqvist"),
)


def body(title: str, prose: list[str], steps: list[str]) -> str:
    """A note body in Gemini's PLAIN_TEXT layout, wrapped the way it wraps."""
    return (
        f"Notes from “{title}”\n\n"
        "These notes have been sent to invited guests in your organization.\n\n"
        "Open meeting notes\n\n"
        "The content was auto-generated on September 25, 2026, 9:40 AM PDT and may\n"
        "contain errors.\n\n"
        "Quick Notes\n\n"
        "Status and blockers\n\n" + "\n".join(prose) + "\n\n"
        "Suggested next steps\n\n" + "\n\n".join(steps) + "\n\n"
        "Meeting records Document Notes by Gemini\n\n"
        "Is the content of this email helpful?\n"
        "Not Useful Email Useful Email\n"
    )


def event(eid, summary, start, end, rsvp):
    return {
        "id": eid,
        "summary": summary,
        "start": datetime.fromisoformat(f"2026-09-25T{start}:00-07:00"),
        "end": datetime.fromisoformat(f"2026-09-25T{end}:00-07:00"),
        "attendees": ["alex.rivera@example.com", "sam.okafor@example.com"],
        "response_status": rsvp,
        "permalink": f"https://calendar.example.com/{eid}",
    }


def mail(mid, title, arrived, text):
    return {
        "id": mid,
        "subject": f"Notes: “{title}” Sep 25, 2026",
        "date": arrived,
        "body": text,
        "permalink": f"https://mail.example.com/#all/{mid}",
    }


STANDUP = body(
    "Pod Standup",
    [
        "Casey directed the app team to merge PRs and join testing at 11.",
        "The staging push started from production data with hot swap",
        "enabled for weekend evaluation.",
    ],
    [
        "[Alex Rivera] Run Data Checks: Execute data quality checks to\n"
        "determine the scope of the artist issue.",
        "[Sam Okafor] Push Staging: Initiate the data push to staging.",
        "[Casey Moreno] Refactor Tickets: Break down the backfill ticket.",
    ],
)

ENG_SYNC = body(
    "Eng Sync",
    [
        "Robin reviewed the draft data model, identifying key entities.",
        "Alex noted that deals represent long-term agreements providing robustness",
        "against time compared to individual contracts.",
    ],
    ["[Robin Hale] Consolidate Notes: Organize discussion points and open tickets."],
)

DEMO_PREP = body(
    "Demo Prep",
    ["Robin walked through the revenue spine grain options for the demo."],
    [
        "[Alex Rivera] Review Deck: Look over the demo deck before Monday.",
        "[Robin Hale] Revenue Spine Grain: Write up the grain options for the revenue spine.",
    ],
)

ONE_ON_ONE = body(
    "Alex / Sam - 1:1",
    ["Sam raised the promotion case for a senior engineer and the salary band."],
    ["[Sam Okafor] Draft Promotion Case: Write up the promotion packet and salary ask."],
)

EVENTS = [
    event("e1", "Pod Standup", "08:45", "09:15", "needsAction"),
    event("e2", "Eng Sync", "09:15", "09:45", "tentative"),
    event("e3", "Demo Prep", "11:00", "11:45", "declined"),
    event("e4", "Alex / Sam - 1:1", "12:00", "12:30", "accepted"),
]

NOTES = [
    mail("m1", "Pod Standup", "2026-09-25T16:42:42Z", STANDUP),
    mail("m2", "Eng Sync", "2026-09-25T17:13:04Z", ENG_SYNC),
    mail("m3", "Demo Prep", "2026-09-25T18:40:44Z", DEMO_PREP),
    mail("m4", "Alex / Sam - 1:1", "2026-09-25T19:43:22Z", ONE_ON_ONE),
]

DECISIONS = [
    "**2026-09-25 · is the revenue spine grain ISRC-grained, or movement-grained?** · "
    "owners: you + Sam · status: open",
]

EOD = datetime(2026, 9, 25, 16, 30, tzinfo=PT)


# --------------------------------------------------------------------------
# the principal, from identities
# --------------------------------------------------------------------------


def test_names_come_from_the_configured_addresses_not_from_the_code():
    """The repo is public: the principal's and his reports' names are derived
    from `.env` addresses at runtime, never written into the module."""
    found = Principal.from_identities(
        {
            "EMAIL_PRINCIPAL": "alex.rivera@example.com",
            "EMAIL_VP_DATA": "sam.okafor@example.com",
            "EMAIL_VP_AI": "jo.lindqvist@example.com",
        }
    )

    assert found.names == ("Alex Rivera", "Alex")
    assert set(found.reports) == {"Sam Okafor", "Jo Lindqvist"}
    assert found.is_him("Alex Rivera") and found.is_him("alex")
    assert not found.is_him("Alexis Moreno")
    assert found.is_report("Sam Okafor") and not found.is_report("Casey Moreno")


def test_a_middle_name_in_the_note_still_matches_the_address():
    """Gemini writes the display name, which can carry a middle name the
    address does not - first and last token is what has to agree."""
    assert PRINCIPAL.is_report("Sam Tobi Okafor")


# --------------------------------------------------------------------------
# attendance: rsvp first, the note's own evidence second
# --------------------------------------------------------------------------


def test_accepted_is_attended_and_declined_is_not():
    assert attendance("accepted", "", PRINCIPAL).status == ATTENDED
    assert attendance("declined", "", PRINCIPAL).status == NOT_ATTENDED


@pytest.mark.parametrize("rsvp", ["needsAction", "tentative", None])
def test_an_unanswered_invite_is_unconfirmed_not_attended(rsvp):
    """~70% of invites are never answered, so RSVP alone is not attendance."""
    verdict = attendance(rsvp, STANDUP, PRINCIPAL)

    assert verdict.status == UNCONFIRMED
    assert "don't quote you" in verdict.reason


def test_a_tentative_call_the_notes_quote_him_in_is_attended_and_says_why():
    """The 2026-09-25 case: tentative on the standup, and the notes quote him.
    RSVP alone under-counts; the quote is the evidence, and it is shown."""
    verdict = attendance("tentative", ENG_SYNC, PRINCIPAL)

    assert verdict.status == ATTENDED
    assert "tentative" in verdict.reason
    assert "Alex noted that deals represent long-term agreements" in verdict.reason


def test_being_named_as_an_owner_is_not_speaking():
    """`[Alex Rivera] Run Data Checks` is the summariser assigning him work,
    which it does whether or not he was there - it proves nothing."""
    assert spoke(STANDUP, PRINCIPAL) is None


def test_speech_is_read_across_gemini_line_wraps():
    assert spoke(ENG_SYNC, PRINCIPAL).startswith("Alex noted that deals")


def test_someone_else_with_his_first_name_as_a_prefix_is_not_him():
    text = body("x", ["Alexis noted the pipeline is green."], [])
    assert spoke(text, PRINCIPAL) is None


# --------------------------------------------------------------------------
# parsing next steps
# --------------------------------------------------------------------------


def test_next_steps_are_owner_and_text_with_wraps_joined():
    steps = next_steps(STANDUP)

    assert steps[0] == (
        "Alex Rivera",
        "Run Data Checks: Execute data quality checks to determine the scope of the artist issue.",
    )
    assert [owner for owner, _ in steps] == ["Alex Rivera", "Sam Okafor", "Casey Moreno"]


def test_a_body_with_no_next_steps_section_yields_none_and_does_not_raise():
    assert next_steps("just some text") == []
    assert next_steps("") == []


# --------------------------------------------------------------------------
# the join: note -> calendar row -> rsvp
# --------------------------------------------------------------------------


def test_every_note_joins_its_calendar_row_including_declined_ones():
    """The meeting ledger disqualifies a declined event on purpose (its
    contract 2), so the join cannot be the main ledger alone - a declined
    call's notes still arrive, and he wants them labelled, not dropped."""
    joined = call_notes.join(NOTES, EVENTS, PRINCIPAL)

    by_title = {n.title: n.verdict.status for n in joined}
    assert by_title == {
        "Pod Standup": UNCONFIRMED,
        "Eng Sync": ATTENDED,
        "Demo Prep": NOT_ATTENDED,
        "Alex / Sam - 1:1": ATTENDED,
    }


def test_a_note_with_no_calendar_row_is_unconfirmed_and_says_so():
    (note,) = call_notes.join(NOTES[:1], [], PRINCIPAL)

    assert note.verdict.status == UNCONFIRMED
    assert "no calendar entry" in note.verdict.reason


def test_a_mail_that_is_not_a_gemini_note_is_not_joined():
    joined = call_notes.join([{"id": "x", "subject": "Re: lunch", "body": "hi"}], EVENTS, PRINCIPAL)
    assert joined == []


# --------------------------------------------------------------------------
# the EOD section: ranked, labelled, every line cited
# --------------------------------------------------------------------------


def _eod_text():
    sections = call_notes.priorities(
        NOTES, EVENTS, PRINCIPAL, decisions=DECISIONS, day=EOD.date(), tz=PT
    )
    return "\n\n".join(s.render() for s in sections if s.lines)


def test_priorities_rank_his_items_then_decisions_then_reports_then_the_rest():
    text = _eod_text()

    order = [
        text.index("Run Data Checks"),  # his
        text.index("Revenue Spine Grain"),  # touches an open decision
        text.index("Push Staging"),  # a report's
        text.index("Refactor Tickets"),  # the rest
    ]
    assert order == sorted(order), text


def test_his_item_from_a_call_he_may_have_missed_is_called_out():
    """The done-when of #168: the standup item assigned to him, from a call he
    was not confirmed in, is flagged - it is the one he has not heard."""
    line = next(line for line in _eod_text().splitlines() if "Run Data Checks" in line)

    assert "not sure you were in it" in line
    assert "you may not have heard this one" in line


def test_observations_from_a_declined_call_carry_the_marker():
    line = next(line for line in _eod_text().splitlines() if "Review Deck" in line)
    assert "(from Demo Prep - you weren't in it)" in line


def test_an_attended_call_carries_no_provenance_warning():
    line = next(line for line in _eod_text().splitlines() if "Consolidate Notes" in line)
    assert "weren't in it" not in line and "not sure" not in line


def test_every_priority_line_carries_the_note_permalink():
    from daydag.brief import unsourced_claims

    text = _eod_text()
    assert not unsourced_claims(text), unsourced_claims(text)
    assert "https://mail.example.com/#all/m1" in text


def test_the_attendance_roll_lists_every_call_with_its_reason():
    text = _eod_text()

    assert "Pod Standup - unconfirmed" in text
    assert "Demo Prep - not attended (declined)" in text
    assert "Eng Sync - attended" in text


def test_personnel_content_is_quoted_like_anything_else():
    """#180 changed house rule 7 (the principal, 2026-09-25: "i think we
    should treat sensitive items the same"). The comp step is quoted in full,
    with its permalink, like every other line - it was withheld under #177."""
    text = _eod_text()

    assert "Draft Promotion Case: Write up the promotion packet and salary ask." in text
    assert "personnel/comp" not in text


def test_notes_from_another_day_are_left_out_of_todays_priorities():
    yesterday = mail("m9", "Pod Standup", "2026-09-24T16:42:42Z", STANDUP)
    sections = call_notes.priorities(
        [yesterday], [], PRINCIPAL, decisions=[], day=EOD.date(), tz=PT
    )
    assert not any(s.lines for s in sections)


def test_a_note_with_no_body_is_named_not_silently_dropped():
    bare = {"id": "m5", "subject": "Notes: “Pod Standup” Sep 25, 2026", "date": NOTES[0]["date"]}
    text = "\n".join(
        s.render()
        for s in call_notes.priorities(
            [bare], EVENTS, PRINCIPAL, decisions=[], day=EOD.date(), tz=PT
        )
    )
    assert "Pod Standup" in text and "no body" in text


# --------------------------------------------------------------------------
# the sweep: idempotent on the gmail message id
# --------------------------------------------------------------------------


def test_a_second_sweep_over_the_same_notes_reports_nothing_new(tmp_path):
    log = EventLog.open(tmp_path / "events.db")

    first = call_notes.sweep(NOTES, EVENTS, PRINCIPAL, log=log, tz=PT)
    second = call_notes.sweep(NOTES, EVENTS, PRINCIPAL, log=log, tz=PT)

    assert "4 new" in first
    assert "nothing new" in second and "4 already seen" in second


def test_the_seen_set_lives_in_the_event_log_not_in_the_vault(tmp_path):
    log = EventLog.open(tmp_path / "events.db")
    call_notes.sweep(NOTES, EVENTS, PRINCIPAL, log=log, tz=PT)

    assert {row["message_id"] for row in log.recorded(call_notes.INGESTED)} == {
        "m1",
        "m2",
        "m3",
        "m4",
    }


def test_a_note_fetched_without_its_body_is_not_burned(tmp_path):
    """Metadata-only search results must not mark a note seen - the next sweep
    that does fetch the body would then skip it forever."""
    log = EventLog.open(tmp_path / "events.db")
    bare = {k: v for k, v in NOTES[0].items() if k != "body"}

    call_notes.sweep([bare], EVENTS, PRINCIPAL, log=log, tz=PT)
    again = call_notes.sweep(NOTES[:1], EVENTS, PRINCIPAL, log=log, tz=PT)

    assert "1 new" in again


def test_the_digest_is_one_line_per_note_with_attendance(tmp_path):
    """#17's line shape, riding on the same pass."""
    text = call_notes.sweep(NOTES, EVENTS, PRINCIPAL, log=EventLog.open(tmp_path / "e.db"), tz=PT)

    line = next(line for line in text.splitlines() if "Pod Standup" in line and "logged" in line)
    assert "1 commitment (yours)" in line
    assert "2 asks" in line
    assert "unconfirmed" in line
    assert "anything wrong, tell me and i'll fix" in text


def test_the_sweep_without_a_log_still_renders(tmp_path):
    text = call_notes.sweep(NOTES, EVENTS, PRINCIPAL, log=None, tz=PT)
    assert "4 new" in text and "can't remember" in text


def test_a_sensitive_title_is_recorded_private(tmp_path):
    log = EventLog.open(tmp_path / "events.db")
    note = mail("m7", "Exit interview - Casey", "2026-09-25T19:43:22Z", STANDUP)
    call_notes.sweep([note], [], PRINCIPAL, log=log, tz=PT)

    (row,) = log._db.execute(
        "SELECT sensitivity FROM events WHERE kind = ?", (call_notes.INGESTED,)
    )
    assert row == ("private",)


def test_arrival_as_a_utc_string_is_read_as_an_instant():
    (note,) = call_notes.join(NOTES[:1], EVENTS, PRINCIPAL)
    assert note.arrived == datetime(2026, 9, 25, 16, 42, 42, tzinfo=UTC)


# --------------------------------------------------------------------------
# shapes met on the real 2026-09-25 notes
# --------------------------------------------------------------------------


def test_a_step_with_several_owners_counts_for_each():
    """Gemini writes `[Jo Lindqvist, Casey Moreno] Audit ...` - one step, two
    owners. Read as one name it matched nobody, so a report's ask fell to
    "everything else"."""
    assert PRINCIPAL.is_report("Casey Moreno, Jo Lindqvist")
    assert PRINCIPAL.is_him("Casey Moreno and Alex Rivera")


def test_the_first_sentence_he_speaks_is_the_attendance_evidence_whatever_it_says():
    """#180: a comp sentence is no longer passed over for a later one - the
    first time the notes quote him is the evidence, and it is quoted."""
    text = body(
        "x",
        [
            "Alex suggested capping the contractor's direct compensation at a lower band.",
            "Alex noted the roadmap needs a three-month cut.",
        ],
        [],
    )
    verdict = attendance("tentative", text, PRINCIPAL)

    assert verdict.status == ATTENDED
    assert "direct compensation" in verdict.reason


def test_sensitive_speech_proves_attendance_and_is_quoted():
    text = body("x", ["Alex suggested capping the salary band for the role."], [])
    verdict = attendance("needsAction", text, PRINCIPAL)

    assert verdict.status == ATTENDED
    assert "salary band" in verdict.reason and "not quoted" not in verdict.reason


def test_steps_from_a_sensitive_note_are_quoted_whole():
    """#180 removed #177's title-only rendering: a step from a comp
    conversation reads the same as any other, in the DM and in State.md."""
    note = mail(
        "m8",
        "Alex / Sam - 1:1",
        "2026-09-25T19:43:22Z",
        body(
            "Alex / Sam - 1:1",
            ["Alex suggested capping the contractor's compensation."],
            [
                "[Alex Rivera] Discuss Contracting: Consult finance about changes to the"
                " contracting structure for Casey.",
            ],
        ),
    )
    sections = call_notes.priorities([note], EVENTS, PRINCIPAL, decisions=[], day=EOD.date(), tz=PT)
    text = "\n".join(s.render() for s in sections)

    assert "Discuss Contracting" in text
    assert "contracting structure for Casey" in text
    assert "detail not quoted" not in text


def test_a_step_that_names_him_is_tagged_and_leads_its_bucket():
    """ "[Casey Moreno] Notify Alex: ..." is not his to do, but it is coming
    to him - it sorts first in its bucket and says so."""
    note = mail(
        "m6",
        "Pod Standup",
        "2026-09-25T16:42:42Z",
        body(
            "Pod Standup",
            ["Casey walked the release plan."],
            [
                "[Casey Moreno] Post Change Log: Post the change log.",
                "[Casey Moreno] Notify Alex: Notify Alex about the front end deploy.",
            ],
        ),
    )
    sections = call_notes.priorities([note], EVENTS, PRINCIPAL, decisions=[], day=EOD.date(), tz=PT)
    rest = next(s for s in sections if "everything else" in s.heading)

    assert "Notify Alex" in rest.lines[0] and "names you" in rest.lines[0]


def test_a_sweep_that_read_no_bodies_does_not_claim_nothing_new(tmp_path):
    """The 9/25 replay: eleven notes, no bodies, and the header said "nothing
    new since the last sweep" - true of nothing. Nothing was read."""
    bare = [{k: v for k, v in n.items() if k != "body"} for n in NOTES]
    text = call_notes.sweep(bare, EVENTS, PRINCIPAL, log=EventLog.open(tmp_path / "e.db"), tz=PT)

    assert "nothing new since" not in text
    assert "nothing ingested" in text and "no body fetched" in text
