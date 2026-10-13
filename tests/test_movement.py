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


def test_his_sub_bullets_are_comments_on_an_item_not_more_items():
    """The file's own convention: "expect comments from me in sub-bullets"
    (2026-09-15). Reading them as open loops proposed movement against his own
    annotations - the live State.md has 4 chase items under 22 bullets."""
    state = (
        "# State\n\n## Chase list\n\n"
        "- vp-data · fruits metadata list · status open\n"
        "\t- he has this working in the metadata workhorse already\n"
        '\t- his words: *"I will have the list by tomorrow"*\n'
        "\t- [slack · thread](https://example.com/p1)\n"
    )

    items = open_items(state=state, note="", note_path="p.md")

    assert [item.text for item in items] == ["vp-data · fruits metadata list · status open"]


# --------------------------------------------------------------------------
# what the review of this branch found. Every one of these was a way the
# detector was blind or noisy on the REAL files while the suite stayed green.
# --------------------------------------------------------------------------


def test_a_bold_item_title_survives_the_noise_stripping():
    """The weekly note bolds every item title - it is `weekly-planning`'s house
    format - and the tag `*(mine)*` follows it. Deleting `*...*` as a span ate
    everything between the first asterisk and the last, so a bold-titled red
    item reduced to the empty set and could never match anything. Silently: no
    row, no warning, and a green suite, because the fixtures used a bare
    trailing tag with no bold title before it.
    """
    from daydag.movement import _terms

    bold = "- [ ] 🔴 **Deal Modeler cutover date** *(mine w/ Gov-Lead)*"

    assert {"modeler", "cutover"} <= _terms(bold), sorted(_terms(bold))


def test_the_chase_rows_own_bookkeeping_is_not_a_distinctive_term():
    """`- owner · ask · asked-on DATE · status open` puts `asked-on`, `status`
    and `open` in every row, which is two shared terms before a word of the ask
    is read. One message saying "status on that, still open" matched all four
    live chase items at once - the false-positive class contract 4 calls the
    expensive one."""
    items = open_items(state=STATE, note="", note_path="p.md")
    slack = [_slack("quick status on the luminate contract - still open, will chase legal")]

    assert detect(open_items=items, calendar=[], slack=slack, gmail=[], now=NOW) == []


def test_evidence_with_no_permalink_is_dropped_not_cited_to_the_item():
    """House rule 1. The wrap used to fall back to the item's own vault path,
    so a Slack quote rendered as though `DayDAG/State.md` had said it. A
    citation pointing at the wrong document is worse than no row - `run._prep`
    already drops a linkless message for the same reason."""
    items = open_items(state=STATE, note="", note_path="p.md")
    linkless = {"text": "fruits metadata list is with finance now", "ts": "1789000000.1"}

    assert detect(open_items=items, calendar=[], slack=[linkless], gmail=[], now=NOW) == []


def test_a_loop_tracked_in_both_stores_is_one_row_not_two():
    """He keeps the same loop in both - a red item for the week and a chase row
    for whoever owes it - and reading both is deliberate. Two rows means the
    wrap asks him to confirm the same thing twice and counts it twice."""
    same = "fruits metadata list for the finance reconciliation"
    items = open_items(
        state=f"# State\n\n## Chase list\n\n- {same}\n",
        note=f"# w\n\n- [ ] 🔴 {same}\n",
        note_path="p.md",
    )

    assert len(items) == 1, [item.text for item in items]


def test_an_item_he_has_snoozed_is_not_re_proposed():
    """State.md documents the convention: "To pause instead, write
    `status: snoozed-until YYYY-MM-DD`". Re-proposing it re-asks a question he
    has already answered."""
    state = (
        "# State\n\n## Chase list\n\n"
        "- vp-data · fruits metadata list · status: snoozed-until 2026-10-01\n"
    )

    assert open_items(state=state, note="", note_path="p.md") == []


def test_a_gemini_note_is_quoted_by_its_subject_not_by_subject_plus_snippet():
    """Contract 3. Concatenating the two produced a sentence appearing nowhere
    in the message he is being sent to go and read. Matching on both is fine;
    quoting the join is not."""
    items = open_items(state=STATE, note="", note_path="p.md")
    gmail = [
        {
            "subject": 'Notes: "Finance x Data" 2026-09-15',
            "snippet": "the fruits metadata list is with finance; reconciliation next week",
            "permalink": "https://example.com/mail/m1",
        }
    ]

    (row,) = detect(open_items=items, calendar=[], slack=[], gmail=gmail, now=NOW)

    assert row.evidence[0].quote == 'Notes: "Finance x Data" 2026-09-15'
    assert "reconciliation" not in row.evidence[0].quote


# --------------------------------------------------------------------------
# #167 - his own side of the day, the board and the repos
#
# The 2026-09-25 wrap rendered `0 closed, 0 moved` on a day he answered the
# Sponsor's question by email and sent a doc the chase list was waiting on.
# Neither could be seen: the evening fetched @-mentions and Gemini notes. The
# fixtures below are synthetic stand-ins for that day's two items.
# --------------------------------------------------------------------------

SPONSOR_THREAD = "1a0d000000000f44"

STATE_0925 = f"""# State

## Chase list

- **Sponsor → Owner-A · the youtube question that gates the performance roadshow** 🔴
\t- sponsor replied 05:45: *"do we understand what's driving the still material 6% difference"*
\t- source: gmail thread `{SPONSOR_THREAD}` (`Performance Domain Ready for the Business`)
\t- asked-on 2026-09-25 · status open
- **Partner-B → you · two actions out of the 9/24 1:1** (note landed 9/24 23:34)
\t- send partner-b the data platform roadmap + walkthrough materials for feedback
\t- the repo is `ExampleOrg/roadmap-repo` (PR #1 merged)
- **vp-data · compute consolidation ticket** · CDI-812 · status open
- **eng-4 · wave 2 cutover** · [slack](https://x.slack.com/archives/C0POD0001/p1790000000000100)
"""


def _sent_mail(thread=SPONSOR_THREAD, subject="Re: Performance Domain Ready for the Business"):
    return {
        "id": "1a0d00000000d405",
        "threadId": thread,
        "subject": subject,
        "snippet": "Was just catching up w/ the engineer. 2 factors influence the deviation",
        "to": ["sponsor@example.com"],
        "date": "2026-09-25T23:45:30Z",
        "permalink": f"https://mail.example.com/#all/{thread}",
    }


def _sent_dm(
    text, *, channel="DPARTNER01", name="DM with Partner-B Park, Principal", ts="1790380022.1"
):
    return {
        "text": text,
        "ts": ts,
        "channel": channel,
        "channel_name": name,
        "permalink": f"https://example.slack.com/archives/{channel}/p{ts.replace('.', '')}",
    }


def _items():
    return open_items(state=STATE_0925, note="", note_path="p.md")


def _row(rows, fragment):
    matches = [row for row in rows if fragment in row.item.text]
    assert len(matches) == 1, [(row.item.text, row.proposed) for row in rows]
    return matches[0]


def _pr(number=2, repo="ExampleOrg/roadmap-repo", title="Apply styling and Overview/Roadmap UI"):
    return {
        "kind": "merged",
        "number": number,
        "title": title,
        "url": f"https://github.com/{repo}/pull/{number}",
        "repository": {"nameWithOwner": repo},
        "closedAt": "2026-09-25T16:27:16Z",
    }


def _ticket(key="CDI-812", status="In Review"):
    return {
        "key": key,
        "fields": {
            "summary": "compute engine consolidation",
            "status": {"name": status},
            "assignee": {"displayName": "Eng Four"},
        },
        "webUrl": f"https://example.atlassian.net/browse/{key}",
    }


def test_his_reply_on_the_asks_own_email_thread_proposes_answered():
    """The item names the thread in a sub-bullet, which is where he keeps
    sources. An id in common is not a fuzzy match - it is the same thread."""
    rows = detect(open_items=_items(), gmail_sent=[_sent_mail()], now=NOW)

    row = _row(rows, "youtube question")
    assert row.proposed == "answered"
    assert row.evidence[0].source == "gmail"
    assert row.evidence[0].permalink.endswith(SPONSOR_THREAD)
    assert row.proposed in Movement.CLOSING


def test_a_reply_on_some_other_thread_is_not_an_answer():
    other = _sent_mail(thread="1a0dffffffffffff", subject="Re: lunch")

    rows = detect(open_items=_items(), gmail_sent=[other], now=NOW)

    assert not [row for row in rows if "youtube" in row.item.text]


def test_a_link_he_sent_in_the_dm_of_the_person_owed_proposes_sent():
    """`Partner-B → you` - the ask is his. A link dropped in that person's DM
    is the delivery, and it names nothing the item's head says, which is why
    term overlap alone never saw it."""
    dm = _sent_dm("yo, finished some table setting for the platform - https://example.com/doc")

    rows = detect(open_items=_items(), slack_sent=[dm], now=NOW)

    row = _row(rows, "two actions")
    assert row.proposed == "sent"
    assert "table setting" in row.evidence[0].quote


def test_a_dm_with_no_link_in_it_is_not_a_delivery():
    dm = _sent_dm("can still talk for a bit if needed")

    rows = detect(open_items=_items(), slack_sent=[dm], now=NOW)

    assert not [row for row in rows if "two actions" in row.item.text]


def test_a_link_to_someone_else_is_not_a_delivery_of_this():
    dm = _sent_dm("here you go https://example.com/x", name="DM with Someone Else, Principal")

    rows = detect(open_items=_items(), slack_sent=[dm], now=NOW)

    assert not [row for row in rows if "two actions" in row.item.text]


def test_a_link_he_sends_on_an_ask_he_does_not_owe_is_not_his_delivery():
    """`Sponsor → Owner-A`: he is not the one who owes it. A link he posts to
    the sponsor is not the delivery of someone else's answer."""
    dm = _sent_dm("fyi https://example.com/x", name="DM with Sponsor Name, Principal")

    rows = detect(open_items=_items(), slack_sent=[dm], now=NOW)

    assert not [row for row in rows if row.proposed == "sent"]


def _thread_reply(text):
    return {
        "text": text,
        "ts": "1790000500.0002",
        "thread_ts": "1790000000.000100",
        "channel": "C0POD0001",
        "permalink": "https://example.slack.com/archives/C0POD0001/p1790000500000200",
    }


def test_his_reply_in_the_thread_an_ask_came_from_proposes_answered():
    rows = detect(open_items=_items(), slack_sent=[_thread_reply("on it - lmk")], now=NOW)

    assert _row(rows, "wave 2 cutover").proposed == "answered"


def test_someone_elses_reply_in_that_thread_is_discussion_not_an_answer():
    rows = detect(open_items=_items(), slack_sweep=[_thread_reply("moved to tue")], now=NOW)

    assert _row(rows, "wave 2 cutover").proposed == "discussed"


def test_channel_traffic_that_never_mentions_him_is_evidence():
    """#141's done-when: `movement.detect` fires on a channel message that
    never mentions him."""
    msg = _slack("sent the compute consolidation plan to finance", ts="1790000900.1")

    rows = detect(open_items=_items(), slack_sweep=[msg], now=NOW)

    assert _row(rows, "compute consolidation").proposed == "discussed"


def test_a_ticket_the_item_names_that_moved_proposes_ticket_moved():
    rows = detect(open_items=_items(), jira=[_ticket()], now=NOW)

    row = _row(rows, "compute consolidation")
    assert row.proposed == "ticket-moved"
    assert row.evidence[0].permalink == "https://example.atlassian.net/browse/CDI-812"
    assert "In Review" in row.evidence[0].quote


def test_a_merged_pr_on_a_repo_the_item_names_proposes_merged_not_closed():
    """#18: merged is evidence of movement, never closure."""
    rows = detect(open_items=_items(), github=[_pr()], now=NOW)

    row = _row(rows, "two actions")
    assert row.proposed == "merged"
    assert row.proposed not in Movement.CLOSING
    assert row.evidence[0].permalink == "https://github.com/ExampleOrg/roadmap-repo/pull/2"


def test_closing_evidence_leads_when_an_item_has_both():
    """He delivered AND a PR merged. The stronger claim leads the row, and its
    evidence is the one quoted first - the label and the quote must agree."""
    dm = _sent_dm("roadmap walkthrough here https://example.com/doc")

    rows = detect(open_items=_items(), github=[_pr()], slack_sent=[dm], now=NOW)

    row = _row(rows, "two actions")
    assert row.proposed == "sent"
    assert row.evidence[0].source == "slack"
    assert len(row.evidence) == 2


def test_board_and_repo_movement_no_item_claims_is_returned_separately():
    """The done-when's second half: a merged PR on a watched repo renders as
    moved with its link, whether or not a chase item names it."""
    from daydag.movement import unclaimed

    pr = _pr(number=9, repo="ExampleOrg/service-a", title="fix the thing")
    ticket = _ticket(key="CDI-900", status="Done")
    rows = detect(open_items=_items(), github=[pr], jira=[ticket], now=NOW)

    loose = unclaimed(rows, jira=[ticket], github=[pr])

    assert [e.permalink for e in loose] == [
        "https://example.atlassian.net/browse/CDI-900",
        "https://github.com/ExampleOrg/service-a/pull/9",
    ]


def test_evidence_an_item_already_claimed_is_not_also_unclaimed():
    from daydag.movement import unclaimed

    rows = detect(open_items=_items(), github=[_pr()], now=NOW)

    assert unclaimed(rows, jira=[], github=[_pr()]) == []


@pytest.mark.guardrail
def test_no_source_can_propose_a_closure_word():
    """#18 again, over every source this module now reads. `answered` and
    `sent` are claims about what HE did - neither says the loop is done."""
    found = detect(
        open_items=_items(),
        gmail_sent=[_sent_mail()],
        slack_sent=[_sent_dm("done https://example.com/doc")],
        jira=[_ticket(status="Done")],
        github=[_pr()],
        now=NOW,
    )

    assert len(found) >= 3
    assert all(m.proposed in Movement.PROPOSALS for m in found)
    assert not {"closed", "done", "complete", "resolved"} & set(Movement.PROPOSALS)


def test_the_new_sources_are_total_over_junk():
    assert (
        detect(
            open_items=_items(),
            slack_sent=[None, {"text": 3}, "x"],
            gmail_sent={"not": "a list"},
            slack_sweep=7,
            jira=[{"key": None}, {"fields": "x"}],
            github=[{"url": None}, []],
            now=NOW,
        )
        == []
    )


# --------------------------------------------------------------------------
# what the replay of the real 2026-09-25 evening found. Each of these was a
# wrong row on real data while every test above was green.
# --------------------------------------------------------------------------


def test_a_dm_cited_as_context_is_not_the_thread_the_ask_came_from():
    """The sponsor's-question row cited VP-Data's DM in a sub-bullet ("seth's
    10:18 DM calls it the most urgent item"). He then wrote "sure will join
    back" in that DM, and the row was proposed `answered` - quoting a message
    about something else. A DM counts as the ask's own conversation only when
    the row's HEAD links it."""
    state = (
        "# State\n\n## Chase list\n\n"
        "- **Sponsor → Owner-A · the youtube question**\n"
        "\t- coupling: the DM calls it urgent - "
        "https://x.slack.com/archives/DCONTEXT01/p1790356734421279\n"
        "- **roland · does this time still work?** - "
        "[slack](https://x.slack.com/archives/DASKED0001/p1789755681441679)\n"
    )
    items = open_items(state=state, note="", note_path="p.md")
    context = _sent_dm("sure will join back", channel="DCONTEXT01", name="DM with Other")
    asked = _sent_dm("yes still works", channel="DASKED0001", name="DM with Roland R")

    rows = detect(open_items=items, slack_sent=[context, asked], now=NOW)

    assert [(row.item.text[:12], row.proposed) for row in rows] == [("**roland · d", "answered")]


def test_a_gmail_reaction_is_not_a_reply():
    """ "👍 ... reacted via Gmail" lands in sent mail on the thread, and is not
    an answer to anything."""
    reaction = _sent_mail()
    reaction["snippet"] = "👍 Principal reacted via Gmail On Fri, Sep 25 someone wrote:"

    rows = detect(open_items=_items(), gmail_sent=[reaction], now=NOW)

    assert not [row for row in rows if "youtube" in row.item.text]


def test_the_names_of_the_people_in_the_room_are_not_what_it_was_about():
    """Real false positive: "nitin+seth · establish a process..." matched a
    DM on the two words `nitin` and `seth`. The owner field is who, not what,
    and the conversation's participants are in every message in it."""
    state = "# State\n\n## Chase list\n\n- ownera+ownerb · establish a lineage process\n"
    items = open_items(state=state, note="", note_path="p.md")
    msg = _sent_dm("ownera ownerb can we talk tomorrow", name="DM with Ownera Person, Ownerb")

    assert detect(open_items=items, slack_sweep=[msg], now=NOW) == []


def test_mentions_and_urls_are_not_subject_matter():
    """`<@U123|Seth Jensen>` and a pasted link's path segments are not words
    he wrote about the item."""
    items = [OpenItem(key="k", text="claude artifact jensen review", owner="", source="s")]
    msg = _slack("<@UPEER00001|Seth Jensen> see <https://claude.ai/artifact/abc|here>")

    assert detect(open_items=items, slack_sweep=[msg], now=NOW) == []


def test_a_long_weekly_note_item_matches_on_its_title_not_its_description():
    """Real false positive: a 32-term red item ("Will 1:1 Wed - bring three
    things *(mine)* - (a) the progress email ... draft ... roadmap ...")
    matched an unrelated message on `draft` + `roadmap`, both in the
    description. The bold title is the item; the rest is his annotation."""
    note = (
        "# 0921-0925\n\n## Priorities\n\n"
        "- [ ] 🔴 **Pin the revenue-domain design date** *(mine)* - carry the "
        "draft roadmap and the finance review into the steering room\n"
    )
    items = open_items(state="", note=note, note_path="p.md")

    unrelated = _slack("can you share the draft roadmap for the platform please")
    related = _slack("revenue-domain design date is oct 9, pinned", ts="1789000000.0009")

    assert detect(open_items=items, slack_sweep=[unrelated], now=NOW) == []
    (row,) = detect(open_items=items, slack_sweep=[related], now=NOW)
    assert row.proposed == "discussed"
