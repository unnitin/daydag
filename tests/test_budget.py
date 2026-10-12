"""The morning push fits one Slack message, and nothing it cuts goes unsaid (#185).

The render ran 23,000 to 59,000 characters against a Slack message of about
5,000, so every brief from 21 Sep to 7 Oct was rewritten by hand and the soak
measured the rewrite. These tests pin the budget that replaces the rewrite:
a light morning is untouched; a heavy one fits; every row cut is counted
somewhere he can reach; citations survive every cut; and it is deterministic.
The heavy morning is synthetic, shaped like the real 7 Oct (`heavy_morning`).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import heavy_morning as hm
from daydag import brief, budget, recipes, run
from daydag.brief import NOT_SHOWN, UNSOURCED, Brief, Section, claim
from daydag.state import EventLog, StateNotWritable
from daydag.voice import voice_violations

ROOT = Path(__file__).resolve().parents[1]


def _fit(tmp_path, now=hm.LATE, **sizes):
    assembled = hm.brief_at(tmp_path, now, **sizes)
    return assembled, budget.fit_morning(assembled)


# --------------------------------------------------------------------------
# the fixture is the problem, reproduced
# --------------------------------------------------------------------------


def test_the_heavy_fixture_overflows_like_the_real_day(tmp_path):
    """CI's secret scan covers the fixture with every other tracked file."""
    assert len(hm.brief_at(tmp_path).render()) > 40_000, "the fixture no longer reproduces #185"


# --------------------------------------------------------------------------
# cutting a line without cutting its citation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "links", "quote"),
    [
        ("vp-data · the compute plan", (), None),
        ("vp-data · the compute plan", ("https://x.example.com/a", "path/note.md#L4"), None),
        ("eng-2", ("https://x.example.com/b",), "it lands (after review) friday"),
    ],
)
def test_split_citation_round_trips_claim(text, links, quote):
    line = claim(text, *links, quote=quote)
    body, tail = brief.split_citation(line)

    assert body + tail == line
    if links:
        assert tail == "".join(f" ({link})" for link in links)
    else:
        assert tail == f" ({UNSOURCED})"


@pytest.mark.parametrize(
    ("words", "closers"),
    [
        ("**bold and more words", "**"),
        ("~~struck and more", "~~"),
        ('*"open quote then more', '"*'),  # innermost first
        ("`a code span and more", "`"),  # cut inside code: closes the code, nothing in it
        ("**a *b** c", ""),  # the italic opened inside the bold closes with it
        ("**bold `x**y` more", "**"),  # ** inside code is code
        ("**bold ~~strike** more", ""),  # the closing ** closes the strike opened in it
    ],
)
def test_clip_line_keeps_the_citation_and_closes_only_what_is_still_open(words, closers):
    citation = " (Weekly Notes/1005-1009.md#L3)"
    line = f"- {words} " + "word " * 60 + citation[1:]
    body, tail = brief.split_citation(brief.clip_line(line, 120))

    assert tail == citation, "the citation was cut"
    assert len(body) - 2 <= 120
    assert body.rsplit("...", 1)[1] == closers, body


@pytest.mark.parametrize(
    "link",
    [
        "https://x.example.com/" + "p" * 80,
        "[the doc](https://x.example.com/" + "p" * 80 + ")",
        "<https://x.example.com/" + "p" * 80 + "|the doc>",
        "[the note](Weekly Notes/1005-1009.md)",
    ],
    ids=["bare", "markdown", "slack", "vault"],
)
def test_clip_line_leaves_a_line_whose_words_carry_a_link_whole(link):
    for line in (
        "- " + "word " * 18 + link + " and more words" * 10 + " (https://x.example.com/cite)",
        "- " + link + " and more words" * 20 + " (https://x.example.com/cite)",
    ):
        assert brief.clip_line(line, 120) == line


# --------------------------------------------------------------------------
# the day's meetings, packed
# --------------------------------------------------------------------------


def test_packed_meeting_rows_name_every_meeting_and_cite_the_day():
    events = hm.events(hm.DAY)
    rows = brief.packed_meeting_rows(events, hm.DAY)
    text = "\n".join(row.line for row in rows)

    assert sum(len(row.members) for row in rows) == len(events), "a meeting went missing"
    for line, members in rows:
        assert line.endswith(f"({brief.day_link(hm.DAY)})")
        if len(members) > 1 and "which one are you in?" not in line:
            # a packed run of clash-free meetings, never a pile-up
            assert not line.startswith(f"- {brief.WARN}")
    piles = [row.members for row in rows if "which one are you in?" in row.line]
    assert [len(p) for p in piles] == [10, 2, 2, 2, 2], "the clusters are the week-ahead's clusters"
    assert rows[-1][1][0]["id"] == "evt-allday", "all-day comes last"
    assert "all day Office closed" in text


@pytest.mark.parametrize(
    "start",
    ["2026-10-07", datetime(2026, 10, 7), datetime(2026, 10, 7, tzinfo=hm.PT)],
    ids=["date-string", "naive-midnight", "aware-midnight"],
)
def test_an_all_day_entry_never_joins_a_pile_up(start):
    timed = [event for event in hm.events(hm.DAY) if event["id"] != "evt-allday"]
    end = start if isinstance(start, str) else start + timedelta(days=1)
    allday = {"id": "allday", "summary": "Offsite", "start": start, "end": end}
    rows = brief.packed_meeting_rows([allday, *timed], hm.DAY)

    piles = [len(row.members) for row in rows if "which one are you in?" in row.line]
    assert piles == [10, 2, 2, 2, 2], "the all-day entry folded the day into one pile-up"
    assert rows[-1][1][-1]["id"] == "allday"
    assert "all day Offsite" in rows[-1][0]


def test_back_to_back_meetings_are_not_a_pile_up():
    day = hm.DAY
    events = [
        {"id": "a", "summary": "first", "start": hm._at(9), "end": hm._at(10)},
        {"id": "b", "summary": "second", "start": hm._at(10), "end": hm._at(11)},
    ]
    rows = brief.packed_meeting_rows(events, day)
    assert len(rows) == 1 and "which one are you in?" not in rows[0][0]


# --------------------------------------------------------------------------
# ranking
# --------------------------------------------------------------------------


def test_ranked_puts_open_linked_rows_first_newest_first_then_unlinked_then_struck():
    lines = [
        claim("old linked", "https://x.example.com/1"),
        claim("~~struck linked~~ ✓", "https://x.example.com/2"),
        claim("unlinked"),
        claim("⭐ ~~struck starred~~ ✓"),
        claim("new linked", "https://x.example.com/3"),
    ]
    ranked = [lines[i] for i in budget._ranked(lines)]

    assert [line.split(" (")[0] for line in ranked] == [
        "- new linked",
        "- old linked",
        "- unlinked",
        "- ~~struck linked~~ ✓",  # struck last; among struck rows, linked still first
        "- ⭐ ~~struck starred~~ ✓",
    ]


# --------------------------------------------------------------------------
# the fit
# --------------------------------------------------------------------------


def test_a_light_morning_is_byte_identical(tmp_path):
    """Contract 1: the budget acts only on a morning that would not fit."""
    payloads = {
        "calendar": [
            {
                "id": "e1",
                "summary": "Pod Steering",
                "start": "2026-10-07T09:00:00-07:00",
                "end": "2026-10-07T10:00:00-07:00",
                "attendees": ["a@example.com", "b@example.com"],
                "permalink": "https://calendar.example.com/e1",
            }
        ],
        "slack": [],
        "gmail": [],
        "vault": "",
    }
    kwargs = dict(
        now=hm.EARLY,
        identities=hm.IDENTITIES,
        payloads=payloads,
        state=hm.folder(tmp_path, owed=2, watch=1),
    )
    log = tmp_path / "events.db"
    fitted = run.render("morning", **kwargs, log=log)
    whole = run.render("morning", **kwargs, log=log, full=True)
    logged = EventLog.open(log).recorded(run.MORNING_FIT)

    assert fitted == whole
    assert [row["summary"].endswith("; fits whole") for row in logged] == [True], (
        "a morning that fits is still logged, once, and --full logs nothing"
    )
    assert budget.THREAD_MARK not in fitted
    assert not any(line.startswith("- +") for line in fitted.splitlines()), "nothing was cut"
    assert "https://calendar.example.com/e1" in fitted, "a light day keeps each meeting's own link"


@pytest.mark.parametrize(
    ("now", "owed", "meetings"),
    [(hm.EARLY, 115, 24), (hm.LATE, 115, 24), (hm.LATE, 0, 0), (hm.LATE, 300, 60)],
    ids=["06:45", "13:43", "no-owed-no-meetings", "heavier"],
)
def test_a_heavy_morning_fits_one_message_sourced_and_in_voice(tmp_path, now, owed, meetings):
    """Contract 2 at any scale, and both posts still pass the guardrails."""
    _, fitted = _fit(tmp_path, now, owed=owed, meetings=meetings)
    head, thread = budget.split_parts(fitted.text())

    assert max(len(head), len(thread)) <= budget.PUSH_BUDGET < budget.SLACK_TEXT_LIMIT
    for part in (head, thread):
        assert brief.unsourced_claims(part) == []
        assert voice_violations(part) == []


#: The words each section's overflow line counts its cut rows with, and where it says they are.
_COUNTED = {
    "note": ("more 🔴 in the note", f"({recipes.weekly_note(hm.DAY)})"),
    "owed": ("more in the chase list", f"({budget.STATE_PATH})"),
    "watch": ("more in watch items", f"({budget.STATE_PATH})"),
    "overnight": ("more overnight", f"{NOT_SHOWN} - in the thread"),
    "gaps": ("older w/ no note found", "listed again tomorrow"),
}


def test_every_cut_is_counted_where_it_lives_and_headings_keep_their_totals(tmp_path):
    assembled, fitted = _fit(tmp_path)
    head, thread = budget.split_parts(fitted.text())
    cuts = {cut.section: cut for cut in fitted.cuts}

    assert "owed to you (115)" in head and "meetings (25)" in head
    assert {key for key, cut in cuts.items() if cut.counted} == set(_COUNTED)
    assert _promised(fitted) == ("overnight",), "the DM promised a section it did not track"
    for key, (words, home) in _COUNTED.items():
        cut = cuts[key]
        assert cut.shown + cut.counted == cut.total
        line = next((x for x in head.splitlines() if x.startswith(f"- +{cut.counted} {words}")), "")
        assert line.endswith(home), f"{key}: {cut.counted} cut, a different count or home: {line}"
    overnight = thread.split("\n\n")[0].splitlines()
    assert overnight[0] == f"overnight, the rest ({cuts['overnight'].counted})"
    assert len(overnight) - 1 == cuts["overnight"].counted, "the reply lost overnight rows"
    said = next(s for s in assembled.sections if s.key == "overnight").lines
    assert overnight[1:] == list(said[cuts["overnight"].shown :]), "a reply row was changed"
    rows = sum(
        1 for section in assembled.sections for line in section.lines if line.startswith("- ")
    )
    assert rows >= sum(cut.total for key, cut in cuts.items() if key != "meetings")


#: How the DM says a section's rest is in the thread.
_PROMISE = {
    "overnight": f" overnight, {NOT_SHOWN} - in the thread",
    "shipping": f" shipping, {NOT_SHOWN} - in the thread",
    "gave-up": f" days, {NOT_SHOWN} - in the thread",
}


def _promised(fitted):
    """The sections the DM's own words say are in the thread."""
    head = fitted.head.splitlines()
    return tuple(
        key for key, words in _PROMISE.items() if any(line.endswith(words) for line in head)
    )


def dangling(fitted):
    """Every section the DM promises is in the thread whose heading the thread
    lacks; a heading counts only at the start of a line, so a row quoting it does not."""
    thread = fitted.thread.splitlines()
    return [
        key
        for key in _promised(fitted)
        if not any(line.startswith(f"{budget._THREADED[key]}, the rest") for line in thread)
    ]


def _block(head, heading):
    """The rows under the heading that starts with ``heading``, overflow line included."""
    lines = head.split("\n\n")
    block = next(part for part in lines if part.startswith(heading))
    return block.splitlines()[1:]


def test_the_qualifiers_and_the_order_match_the_rows(tmp_path):
    assembled, fitted = _fit(tmp_path)
    by_key = {section.key: section for section in assembled.sections}

    owed = _block(fitted.head, "owed to you")
    shown = [line for line in owed if not line.startswith("- +")]
    unlinked = sum(line.endswith(f"({UNSOURCED})") for line in by_key["owed"].lines)
    struck = sum("~~" in line for line in by_key["owed"].lines)
    unlinked -= sum(line.endswith(f"({UNSOURCED})") for line in shown)
    struck -= sum("~~" in line for line in shown)
    assert owed[-1].endswith(f", {unlinked} w/ no link yet, {struck} struck ({budget.STATE_PATH})")

    watch = _block(fitted.head, "watch")
    shown = [line for line in watch if not line.startswith("- +")]
    unlinked = sum(line.endswith(f"({UNSOURCED})") for line in by_key["watch"].lines)
    struck = sum("~~" in line for line in by_key["watch"].lines)
    unlinked -= sum(line.endswith(f"({UNSOURCED})") for line in shown)
    struck -= sum("~~" in line for line in shown)
    assert watch[-1].endswith(f", {unlinked} w/ no link yet, {struck} struck ({budget.STATE_PATH})")
    for line in _block(fitted.head, "overnight"):
        if not line.startswith("- +"):
            assert len(brief.split_citation(line)[0]) - 2 <= budget.ITEM_CAP, f"unclipped: {line}"

    gaps = [line for line in _block(fitted.head, "meetings w/ no notes") if "-  +" not in line]
    newest = list(reversed(by_key["gaps"].lines))[: len(gaps) - 1]
    assert gaps[:-1] == newest, "no-notes shows the newest first"

    overnight = [line for line in _block(fitted.head, "overnight") if not line.startswith("- +")]
    rows = [line for line in by_key["overnight"].lines if line.startswith("- ")]
    tails = [brief.split_citation(line)[1] for line in overnight]
    assert tails == [brief.split_citation(line)[1] for line in rows[: len(tails)]], (
        "overnight shows rows in source order: mentions, then notes"
    )


def _thread_rows(key, count):
    return [
        claim(f"{key} {i} " + "said " * 25, f"https://x.example.com/{key}{i}") for i in range(count)
    ]


def test_an_overfull_reply_keeps_every_heading_and_counts_what_it_drops():
    threaded = {key: _thread_rows(key, 30) for key in ("overnight", "shipping", "gave-up")}
    thread, named, gone = budget._thread(threaded, 3_000)
    blocks = dict(
        (block.split(", the rest")[0], block.splitlines()) for block in thread.split("\n\n")
    )

    assert len(thread) <= 3_000
    assert list(blocks) == ["overnight", "shipping", "gave up on notes"], "a promise lost its reply"
    kept = {}
    for heading, lines in blocks.items():
        rows = [line for line in lines[1:] if not line.startswith("- +")]
        counted = [line for line in lines[1:] if line.startswith("- +")]
        dropped = int(counted[0].split()[1].lstrip("+")) if counted else 0
        assert len(rows) + dropped == 30, f"{heading} lost rows without counting them"
        if rows:
            assert lines[0] == f"{heading}, the rest ({len(rows)})", "the heading over-counts"
        kept[heading] = len(rows)
    assert kept["overnight"] == 0, "overnight goes first: slack and gmail still have it"
    assert kept["gave up on notes"] == budget.CAPS["gave-up"], "gave-up keeps its floor"
    assert 0 < kept["shipping"] < 30, "shipping gives up rows before the gave-up floor"
    assert named == budget.CAPS["gave-up"], "exactly the titles the reply kept count as said"
    assert gone == {key: 30 - n for key, n in zip(threaded, kept.values(), strict=True)}
    assert "named in a later brief" in thread and "still in slack and gmail" in thread
    assert brief.unsourced_claims(thread) == []
    assert voice_violations(thread) == []


def test_shrink_order_cuts_watch_first_and_meetings_last(tmp_path):
    assembled = hm.brief_at(tmp_path, hm.LATE, meetings=40)
    fitted = budget.fit_morning(assembled)
    cuts = {cut.section: cut for cut in fitted.cuts}

    assert len(fitted.head) <= budget.PUSH_BUDGET
    assert cuts["watch"].shown <= budget.CAPS["watch"]
    if cuts["gaps"].shown < budget.CAPS["gaps"]:
        assert cuts["watch"].shown == 0, "no-notes lost a row while watch still had one"
    if cuts["meetings"].counted:
        assert all(cuts[key].shown == 0 for key in ("watch", "gaps")), "meetings went before watch"


@pytest.mark.parametrize(
    ("meetings", "now", "room"),
    [(60, hm.LATE, budget.PUSH_BUDGET), (40, hm.EARLY, 1_377)],
)
def test_the_fit_gives_back_every_row_that_still_fits(tmp_path, meetings, now, room):
    assembled = hm.brief_at(tmp_path, now, meetings=meetings)
    fitted = budget.fit_morning(assembled, budget=room)
    ceiling = {
        **budget.CAPS,
        "meetings": len(brief.packed_meeting_rows(assembled.events, assembled.day)),
    }
    short = [
        cut.section
        for cut in fitted.cuts
        if cut.counted and fitted.caps[cut.section] < ceiling[cut.section]
    ]

    assert short, "the fixture should make the fit shrink something"
    for key in short:
        grown = budget._compose(
            assembled, {**fitted.caps, key: fitted.caps[key] + 1}, gave_up=None, gave_up_days=11
        )
        assert len(grown.head) > room, f"{key} could have shown one more row"


def test_meetings_go_last_and_count_what_they_cut(tmp_path):
    assembled = hm.brief_at(tmp_path, hm.LATE, meetings=60)
    fitted = budget.fit_morning(assembled, budget=1_800)
    meetings = next(cut for cut in fitted.cuts if cut.section == "meetings")
    rows = brief.packed_meeting_rows(assembled.events, hm.DAY)
    kept = [row.line for row in rows if row.line in fitted.head]

    assert len(fitted.head) <= 1_800 and len(fitted.thread) <= 1_800
    assert meetings.counted, "the budget should force the meetings to give up rows"
    assert f"- +{meetings.counted} more today ({brief.day_link(hm.DAY)})" in fitted.head
    assert kept == [row.line for row in rows[: len(kept)]], (
        "a later meeting was kept over an earlier one"
    )
    alone = {key: 0 for key in fitted.caps}
    alone["meetings"] = fitted.caps["meetings"] + 1
    grown = budget._compose(assembled, alone, gave_up=None, gave_up_days=11)
    assert len(grown.head) > 1_800, "a meeting row was cut while every other section could give"


def test_the_floor_is_bounded_and_keeps_every_degrade_line():
    long = "word " * 600
    sections = (
        Section(
            "⚠ no weekly note for 1005-1009", (claim("can't triage", "note.md"),), key="no-note"
        ),
        Section(
            "owed to you (300)",
            tuple(claim(f"{i} {long}", f"https://x.example.com/{i}") for i in range(300)),
            key="owed",
        ),
        Section(
            "overnight (80)",
            tuple(claim(f"{i}", f"https://x.example.com/o{i}", quote=long) for i in range(80)),
            key="overnight",
        ),
        Section("watch", tuple(claim(f"{i} {long}") for i in range(90)), key="watch"),
        Section(
            "meetings w/ no notes (90)",
            tuple(f"- m{i} - no note found in gmail" for i in range(90)),
            key="gaps",
        ),
        Section(
            "meetings (61)", ("- the pairwise render, never shown when fitted",), key="meetings"
        ),
    )
    dead = ("calendar", "slack", "gmail", "the chase list", "the meeting ledger")
    assembled = Brief(
        hm.DAY,
        "morning. wed oct 7 - ? meetings",
        sections,
        unreachable=dead,
        events=tuple(hm.events(hm.DAY, count=60)),
    )
    floor = budget.fit_morning(assembled, budget=0)

    assert len(floor.head) < 2_000
    assert f"- +61 more today ({brief.day_link(hm.DAY)})" in floor.head, "a meeting went uncounted"
    for name in dead:
        assert f"- couldn't check {name}" in floor.head
    assert floor.head.splitlines()[2].startswith("⚠ no weekly note")
    assert brief.unsourced_claims(floor.head) == []


def test_the_budget_is_deterministic(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = budget.fit_morning(hm.brief_at(tmp_path / "a")).text()
    again = budget.fit_morning(hm.brief_at(tmp_path / "a")).text()
    shuffled = budget.fit_morning(hm.brief_at(tmp_path / "b", shuffle=3)).text()

    assert first == again == shuffled


# --------------------------------------------------------------------------
# the runner: --full, gave-up said once, the State.md refusal, the CLI
# --------------------------------------------------------------------------


def _seed_gave_up(tmp_path, log, count=5):
    """Meetings 13 days back that never produced a note: tomorrow's gave-up rows."""
    then = hm.EARLY - timedelta(days=13)
    midnight = then.replace(hour=0, minute=0)
    old = [
        {
            "id": f"old{i}",
            "summary": f"Retired series {i}",
            "start": (midnight + timedelta(minutes=20 * i)).isoformat(),
            "end": (midnight + timedelta(minutes=20 * i + 15)).isoformat(),
            "attendees": ["principal@example.com", "vp-data@example.com"],
            "permalink": f"https://calendar.example.com/old{i}",
        }
        for i in range(count)
    ]
    run.render(
        "morning",
        now=then,
        identities=hm.IDENTITIES,
        payloads={"calendar": old, "slack": [], "gmail": [], "vault": ""},
        state=hm.folder(tmp_path / "then", owed=0, watch=0),
        log=log,
    )


def _heavy_render(tmp_path, now, **kwargs):
    return run.render(
        "morning",
        now=now,
        identities=hm.IDENTITIES,
        payloads=hm.payloads(now.astimezone(hm.PT).date(), mentions=kwargs.pop("mentions", 8)),
        state=kwargs.pop("state", None) or hm.folder(tmp_path),
        **kwargs,
    )


def _row_counts(log):
    with sqlite3.connect(log) as db:
        query = "SELECT name FROM sqlite_master WHERE type = 'table'"
        tables = [name for (name,) in db.execute(query)]
        # table names come from the database itself, never from input
        return {
            table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]  # noqa: S608
            for table in tables
        }


def test_full_prints_todays_render_and_writes_nothing(tmp_path):
    """Read-only: the log, State.md and the gave-up record are as the fitted run left them.

    At 13:43, after the morning's fitted run: by then meetings have ended, so a
    --full that projected State.md or taught the directory would show here."""
    log = tmp_path / "events.db"
    folder = hm.folder(tmp_path / "now")
    _heavy_render(tmp_path / "now", hm.EARLY, log=log, write_state=True, state=folder)
    _seed_gave_up(tmp_path, log)
    before, state = _row_counts(log), folder.state_path.read_bytes()

    payloads = hm.payloads(hm.DAY)
    payloads["calendar"].append(
        {
            "id": "added-since",
            "summary": "Booked after the morning run",
            "start": hm._at(12, 50).isoformat(),
            "end": hm._at(13, 10).isoformat(),
            "attendees": ["principal@example.com", "cto@example.com"],
            "permalink": "https://calendar.example.com/added",
        }
    )
    full = run.render(
        "morning",
        now=hm.LATE,
        identities=hm.IDENTITIES,
        payloads=payloads,
        state=folder,
        log=log,
        full=True,
        write_state=True,
    )

    assert len(full) > budget.PUSH_BUDGET * 5, "--full is the unfitted render"
    assert budget.THREAD_MARK not in full
    assert "gave up on notes (5)" in full, "the gave-up rows are in the full text"
    assert "Booked after the morning run" in full
    assert _row_counts(log) == before, "--full wrote to the event log"
    assert folder.state_path.read_bytes() == state, "--full wrote State.md"
    fitted = _heavy_render(tmp_path / "now", hm.LATE, log=log, write_state=True, state=folder)
    assert "gave up on notes" in fitted, "--full used up tomorrow's gave-up rows"
    assert folder.state_path.read_bytes() != state, (
        "the fitted run should project, or this is vacuous"
    )


@pytest.mark.parametrize("mentions", [8, 20], ids=["7-oct-overnight", "heavy-overnight"])
def test_gave_up_is_said_once_and_every_title_is_said(tmp_path, mentions):
    """A title the posts name is recorded; one they only count is named on a later morning.
    A heavy overnight fills the reply first, and still the titles get said."""
    log = tmp_path / "events.db"
    count = 60
    _seed_gave_up(tmp_path, log, count=count)
    titles = {f"Retired series {i}": f"old{i}" for i in range(count)}

    said: set[str] = set()
    mornings = 0
    while len(said) < count and mornings < 8:
        text = _heavy_render(
            tmp_path / f"d{mornings}",
            hm.EARLY + timedelta(days=mornings),
            log=log,
            mentions=mentions,
        )
        head, thread = budget.split_parts(text)
        named = {title for title in titles if f"- {title} (" in text}
        recorded = {p["id"] for p in EventLog.open(log).recorded(run.GAVE_UP)}

        assert len(head) <= budget.PUSH_BUDGET and len(thread) <= budget.PUSH_BUDGET
        assert named, f"morning {mornings} named no gave-up title while {count - len(said)} wait"
        assert not named & said, "a title was said twice"
        said |= named
        assert recorded == {titles[title] for title in said}, "recorded is not what was said"
        if len(said) < count:
            assert "named in a later brief" in thread, "the rest were dropped without a word"
        mornings += 1

    assert len(said) == count, f"{count - len(said)} titles were never named"
    assert mornings > 1, "the fixture should hold some titles to a later morning"
    after = _heavy_render(tmp_path / "after", hm.EARLY + timedelta(days=mornings), log=log)
    assert "gave up on notes" not in after


def _refuse(*args, **kwargs):
    raise StateNotWritable(
        "the file changed under the writer; " + "detail " * 60 + "Nothing was written."
    )


def test_a_state_md_refusal_still_fits(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "_project", _refuse)
    text = _heavy_render(tmp_path, hm.LATE, log=tmp_path / "events.db", write_state=True)
    head, _ = budget.split_parts(text)
    refusal = next(
        line for line in head.splitlines() if line.startswith("- couldn't update State.md")
    )

    assert len(head) <= budget.PUSH_BUDGET
    assert refusal.startswith("- couldn't update State.md: the file changed")
    assert refusal.endswith("Nothing was written.") and "..." not in refusal, "the refusal was cut"
    assert brief.unsourced_claims(head) == []


def test_a_light_morning_keeps_the_refusal_whole(tmp_path, monkeypatch):
    """Contract 1 covers the footer too: a morning that fits is not edited."""
    monkeypatch.setattr(run, "_project", _refuse)
    text = run.render(
        "morning",
        now=hm.EARLY,
        identities=hm.IDENTITIES,
        payloads={"calendar": [], "slack": [], "gmail": [], "vault": ""},
        state=hm.folder(tmp_path, owed=1, watch=0),
        log=tmp_path / "events.db",
        write_state=True,
    )
    refusal = next(line for line in text.splitlines() if "couldn't update State.md" in line)

    assert "..." not in refusal and refusal.endswith("Nothing was written.")


def test_main_posts_only_the_push_and_logs_the_summary(tmp_path, monkeypatch, capsys):
    """The CLI on the real clock: the payload's meetings are another day's, but
    the heavy State.md alone overflows, so the fit still has to act. A run whose
    stderr is not a terminal - a scheduled one - prints nothing but the push."""
    vault = tmp_path / "vault"
    (tmp_path / ".env").write_text(
        f"SLACK_USER_PRINCIPAL=UPRINCIPAL1\nEMAIL_PRINCIPAL=principal@example.com\nVAULT_ROOT={vault}\n",
        encoding="utf-8",
    )
    hm.folder(vault)
    payloads = tmp_path / "payloads.json"
    payloads.write_text(json.dumps(hm.payloads()), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    log = tmp_path / "events.db"
    render = ["render", "morning", "--payloads", str(payloads), "--log", str(log)]

    assert run.main(render) == 0
    out = capsys.readouterr()
    head, _ = budget.split_parts(out.out.rstrip("\n"))
    logged = EventLog.open(log).recorded(run.MORNING_FIT)

    assert len(head) <= budget.PUSH_BUDGET
    assert out.err == "", "a scheduled run's stderr can be merged into the post"
    assert len(logged) == 1 and logged[0]["summary"].startswith("morning push: ")
    assert budget.STATE_PATH in logged[0]["summary"], "the summary names where cut rows live"

    assert out.out.count(budget.THREAD_MARK) == 1, "the CLI dropped the threaded reply"
    assert budget.split_parts(out.out.rstrip("\n"))[1].startswith("overnight, the rest")

    monkeypatch.setattr(sys.stderr, "isatty", lambda: True, raising=False)
    assert run.main(render) == 0
    assert capsys.readouterr().err.startswith("morning push: "), "a terminal sees the summary"

    before = _row_counts(log)
    assert run.main([*render, "--full"]) == 0
    assert len(capsys.readouterr().out) > budget.PUSH_BUDGET * 2
    assert _row_counts(log) == before, "--full wrote to the event log"


@pytest.mark.parametrize("full", [False, True], ids=["fitted", "full"])
def test_full_leaves_the_shipping_cursors_where_they_were(tmp_path, monkeypatch, capsys, full):
    """The cursor moves only when the pulse's landings were posted, which `--full` never is."""
    (tmp_path / ".env").write_text(
        "SLACK_USER_PRINCIPAL=UPRINCIPAL1\nEMAIL_PRINCIPAL=principal@example.com\n",
        encoding="utf-8",
    )
    payloads = tmp_path / "payloads.json"
    payloads.write_text(json.dumps(hm.payloads()), encoding="utf-8")
    stored = []
    monkeypatch.setattr(run, "build_pulse", lambda identities, log: (None, "report"))
    monkeypatch.setattr(run, "store_cursors", lambda log, report: stored.append(report))
    monkeypatch.chdir(tmp_path)
    argv = ["render", "morning", "--payloads", str(payloads), "--log", str(tmp_path / "e.db")]

    assert run.main([*argv, "--mirrors", *(["--full"] if full else [])]) == 0
    assert stored == ([] if full else ["report"])


# --------------------------------------------------------------------------
# what the second review found
# --------------------------------------------------------------------------


def test_every_section_the_brief_makes_is_one_the_budget_knows(tmp_path):
    """A section the budget does not know is shown whole and never cut, so a new
    one added to the brief without a budget rule could take the DM over."""
    import re

    made = set(re.findall(r'key="([^"]+)"', (ROOT / "src" / "daydag" / "brief.py").read_text()))
    assert made, "the brief's section keys moved"
    assert made <= set(budget._ORDER), made - set(budget._ORDER)
    assert {section.key for section in hm.brief_at(tmp_path).sections} <= set(budget._ORDER)


def test_a_pulse_blocks_own_degrade_lines_stay_in_the_dm(tmp_path):
    assembled = hm.brief_at(tmp_path, hm.EARLY)
    merges = tuple(claim(f"PR {i} merged", f"https://x.example.com/pr{i}") for i in range(6))
    notices = (
        "- svc-a: could not fetch, repo state as of oct 6",
        "- svc-b: could not read the mirror, nothing reported",
    )
    shipping = Section("shipping", ("svc-a", *merges, *notices), key="shipping")
    fitted = budget.fit_morning(
        Brief(
            assembled.day,
            assembled.header,
            (*assembled.sections, shipping),
            events=assembled.events,
        ),
    )
    cut = next(cut for cut in fitted.cuts if cut.section == "shipping")

    assert all(notice in fitted.head for notice in notices), "a stale repo was hidden"
    assert not any(notice in fitted.thread for notice in notices)
    assert cut.total == 6, "a notice was counted as a landing"
    assert dangling(fitted) == []
    if cut.counted:
        assert f"shipping, the rest ({cut.counted})" in fitted.thread


def test_the_summary_says_where_every_cut_row_went():
    fitted = budget.Fitted(
        head="h",
        thread="t",
        cuts=(
            budget.Cut("overnight", 35, 4, "the thread"),
            budget.Cut("owed", 115, 3, budget.STATE_PATH),
            budget.Cut("note", 3, 3, "x.md"),
        ),
        dropped={"overnight": 21},
    )
    assert fitted.summary() == (
        "morning push: 1 chars, thread 1; cut overnight 10 -> the thread,"
        " 21 -> still in slack and gmail; owed 112 -> DayDAG/State.md"
    )
    assert budget.Fitted(head="h").summary() == "morning push: 1 chars; fits whole"
    repacked = budget.Fitted(head="h", cuts=(budget.Cut("note", 3, 3, "x.md"),))
    assert repacked.summary().endswith("; fitted, nothing cut")


def _overflow(key):
    """The DM's real overflow line for ``key``, built by the budget itself."""
    rows = tuple(claim(f"{key} row {i}", f"https://x.example.com/{key}{i}") for i in range(5))
    section, _, rest = budget._capped(
        Section(key, rows, key=key),
        key=key,
        cap=1,
        note_path="note.md",
        gave_up_days=11,
    )
    assert rest, "the section should have sent rows on"
    return section.lines[-1]


@pytest.mark.parametrize("key", ["overnight", "shipping", "gave-up"])
def test_dangling_catches_every_kind_of_promise(key):
    heading = budget._THREADED[key]
    head = f"morning.\n\n{key}\n{_overflow(key)}"
    impostor = (
        f"overnight, the rest (1)\n- vp-data: {heading}, the rest of it (https://x.example.com/z)"
    )

    assert dangling(budget.Fitted(head=head)) == [key]
    assert dangling(budget.Fitted(head=head, thread=f"{heading}, the rest (4)\n- r")) == []
    if key != "overnight":
        assert dangling(budget.Fitted(head=head, thread=impostor)) == [key], (
            "a row quoting the heading is not the reply"
        )


def test_a_reply_one_row_over_counts_the_one_it_drops():
    rows = _thread_rows("overnight", 3)
    whole, _, none = budget._thread({"overnight": rows}, 10_000)
    thread, _, gone = budget._thread({"overnight": rows}, len(whole) - 1)
    fitted = budget.Fitted(
        head="h",
        thread=thread,
        cuts=(budget.Cut("overnight", 7, 4, "the thread"),),
        dropped=gone,
    )

    assert none == {} and gone == {"overnight": 1}
    assert f"- +1 more, {NOT_SHOWN} - still in slack and gmail" in thread
    assert "overnight 2 -> the thread, 1 -> still in slack and gmail" in fitted.summary()
