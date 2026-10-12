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
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

import heavy_morning as hm
from daydag import brief, budget, run
from daydag.brief import NOT_SHOWN, UNSOURCED, Brief, Section, claim
from daydag.state import EventLog, StateNotWritable
from daydag.voice import voice_violations

ROOT = Path(__file__).resolve().parents[1]


def _fit(tmp_path, now=hm.LATE, **sizes):
    assembled = hm.brief_at(tmp_path, now, **sizes)
    return assembled, budget.fit_morning(assembled, now=now)


# --------------------------------------------------------------------------
# the fixture is the problem, reproduced
# --------------------------------------------------------------------------


def test_the_heavy_fixture_overflows_like_the_real_day_and_holds_no_real_identifier(tmp_path):
    scan = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "scan_secrets.py"),
            str(ROOT / "tests" / "heavy_morning.py"),
        ],
        capture_output=True,
        text=True,
    )
    assert scan.returncode == 0, scan.stdout + scan.stderr
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
    "markup", ["**bold and more words", "~~struck and more", '*"open quote then more']
)
def test_clip_line_keeps_the_citation_and_closes_markup(markup):
    citation = "Create Music Group/Weekly Notes/1005-1009.md#L3"
    line = f"- {markup} " + "x" * 300 + f" ({citation})"

    clipped = brief.clip_line(line, 120)
    body, tail = brief.split_citation(clipped)

    assert tail == f" ({citation})", "the citation was cut"
    assert len(body) - 2 <= 120
    for marker in ("**", "~~", "`"):
        assert body.count(marker) % 2 == 0, f"{marker} left open: {body}"


def test_a_short_line_is_left_alone():
    line = claim("short", "https://x.example.com/c")
    assert brief.clip_line(line, 120) == line


# --------------------------------------------------------------------------
# the day's meetings, packed
# --------------------------------------------------------------------------


def test_packed_meeting_rows_name_every_meeting_and_cite_the_day():
    events = hm.events(hm.DAY)
    rows = brief.packed_meeting_rows(events, hm.DAY)
    text = "\n".join(line for line, _ in rows)

    assert sum(len(members) for _, members in rows) == len(events), "a meeting went missing"
    for line, members in rows:
        assert line.endswith(f"({brief.day_link(hm.DAY)})")
        if len(members) > 1 and "which one are you in?" not in line:
            # a packed run of clash-free meetings, never a pile-up
            assert not line.startswith(f"- {brief.WARN}")
    piles = [members for line, members in rows if "which one are you in?" in line]
    assert [len(p) for p in piles] == [10, 2, 2, 2, 2], "the clusters are the week-ahead's clusters"
    assert rows[-1][1][0]["id"] == "evt-allday", "all-day comes last"
    assert "all day Office closed" in text


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
    ranked = budget._ranked(lines)

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
    fitted = run.render("morning", **kwargs)
    whole = run.render("morning", **kwargs, full=True)

    assert fitted == whole
    assert budget.THREAD_MARK not in fitted
    assert not any(line.startswith("- +") for line in fitted.splitlines()), "nothing was cut"
    assert "https://calendar.example.com/e1" in fitted, "a light day keeps each meeting's own link"


@pytest.mark.parametrize("now", [hm.EARLY, hm.LATE], ids=["06:45", "13:43"])
def test_a_heavy_morning_fits_one_message(tmp_path, now):
    _, fitted = _fit(tmp_path, now)
    head, thread = budget.split_parts(fitted.text())

    assert len(head) <= budget.PUSH_BUDGET
    assert len(thread) <= budget.PUSH_BUDGET
    assert max(len(head), len(thread)) < budget.SLACK_TEXT_LIMIT


def test_every_cut_is_counted_and_headings_keep_their_totals(tmp_path):
    _, fitted = _fit(tmp_path)

    assert "owed to you (115)" in fitted.head
    assert "meetings (25)" in fitted.head
    for cut in fitted.cuts:
        assert 0 <= cut.shown <= cut.total
        if cut.counted:
            assert cut.home, f"{cut.section} cut {cut.counted} rows with nowhere to find them"
    owed = next(c for c in fitted.cuts if c.section == "owed")
    assert f"+{owed.counted} more in the chase list" in fitted.head


def test_overflow_lines_cite_their_home(tmp_path):
    _, fitted = _fit(tmp_path)
    head = fitted.head

    assert "more in the chase list" in head and "more in watch items" in head
    for line in head.splitlines():
        if "more in the chase list" in line or "more in watch items" in line:
            assert line.endswith(f"({budget.STATE_PATH})")
        if "🔴 in the note" in line:
            assert line.endswith(".md)")
        if NOT_SHOWN in line:
            assert line.split(f", {NOT_SHOWN}")[0].split()[-1] in {"overnight", "shipping"}
    assert "listed again tomorrow" in head


def test_every_thread_promise_has_its_reply(tmp_path):
    (tmp_path / "few").mkdir()
    (tmp_path / "many").mkdir()
    few = budget.fit_morning(hm.brief_at(tmp_path / "few", mentions=3, notes=0), now=hm.LATE)
    many = budget.fit_morning(hm.brief_at(tmp_path / "many"), now=hm.LATE)

    assert budget.THREAD_MARK not in few.text(), "three mentions fit; nothing goes to a thread"
    assert budget.dangling(many) == []
    assert "overnight, the rest (13)" in many.thread
    assert all(
        "example.slack.com" in line or "example.com" in line
        for line in many.thread.splitlines()[1:]
    )


@pytest.mark.parametrize("now", [hm.EARLY, hm.LATE], ids=["06:45", "13:43"])
def test_head_and_thread_are_sourced_and_in_voice(tmp_path, now):
    _, fitted = _fit(tmp_path, now)
    head, thread = budget.split_parts(fitted.text())

    assert brief.unsourced_claims(head) == []
    assert brief.unsourced_claims(thread) == []
    assert voice_violations(head) == []
    assert voice_violations(thread) == []


def test_shrink_order_cuts_watch_first_and_meetings_last(tmp_path):
    assembled = hm.brief_at(tmp_path, hm.LATE, meetings=40)
    fitted = budget.fit_morning(assembled, now=hm.LATE)
    cuts = {cut.section: cut for cut in fitted.cuts}

    assert len(fitted.head) <= budget.PUSH_BUDGET
    assert cuts["watch"].shown <= budget.CAPS["watch"]
    if cuts["gaps"].shown < budget.CAPS["gaps"]:
        assert cuts["watch"].shown == 0, "no-notes lost a row while watch still had one"
    if cuts["meetings"].counted:
        assert all(cuts[key].shown == 0 for key in ("watch", "gaps")), "meetings went before watch"


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
    )
    dead = ("calendar", "slack", "gmail", "the chase list", "the meeting ledger")
    assembled = Brief(
        hm.DAY,
        "morning. wed oct 7 - ? meetings",
        sections,
        unreachable=dead,
        events=tuple(hm.events(hm.DAY, count=60)),
    )
    refusal = "- couldn't update State.md: " + "x" * 300

    floor = budget.fit_morning(assembled, now=hm.LATE, late=[refusal[:200]], budget=0)

    assert len(floor.head) < 2_000
    for name in dead:
        assert f"- couldn't check {name}" in floor.head
    assert floor.head.splitlines()[2].startswith("⚠ no weekly note")
    assert brief.unsourced_claims(floor.head) == []


@pytest.mark.parametrize("owed", [0, 1, 60, 300])
@pytest.mark.parametrize("meetings", [0, 24, 60])
def test_the_budget_holds_at_any_scale(tmp_path, owed, meetings):
    assembled = hm.brief_at(tmp_path, hm.LATE, owed=owed, meetings=meetings)
    fitted = budget.fit_morning(assembled, now=hm.LATE)
    head, thread = budget.split_parts(fitted.text())

    assert len(head) <= budget.PUSH_BUDGET
    assert len(thread) <= budget.PUSH_BUDGET
    assert brief.unsourced_claims(head) == []


def test_the_budget_is_deterministic(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = budget.fit_morning(hm.brief_at(tmp_path / "a"), now=hm.LATE).text()
    again = budget.fit_morning(hm.brief_at(tmp_path / "a"), now=hm.LATE).text()
    shuffled = budget.fit_morning(hm.brief_at(tmp_path / "b", shuffle=3), now=hm.LATE).text()

    assert first == again == shuffled


# --------------------------------------------------------------------------
# the runner: --full, gave-up said once, the State.md refusal, the CLI
# --------------------------------------------------------------------------


def _seed_gave_up(tmp_path, log, count=5):
    """Meetings 13 days back that never produced a note: tomorrow's gave-up rows."""
    then = hm.EARLY - timedelta(days=13)
    old = [
        {
            "id": f"old{i}",
            "summary": f"Retired series {i}",
            "start": then.replace(hour=9 + i).isoformat(),
            "end": then.replace(hour=9 + i, minute=30).isoformat(),
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
        payloads=hm.payloads(now.astimezone(hm.PT).date()),
        state=kwargs.pop("state", None) or hm.folder(tmp_path),
        **kwargs,
    )


def test_full_prints_todays_render_and_records_nothing(tmp_path):
    log = tmp_path / "events.db"
    _seed_gave_up(tmp_path, log)
    full = _heavy_render(tmp_path / "now", hm.EARLY, log=log, full=True)

    assert len(full) > budget.PUSH_BUDGET * 5, "--full is the unfitted render"
    assert budget.THREAD_MARK not in full
    assert "gave up on notes (5)" in full, "the gave-up rows are in the full text"
    assert list(EventLog.open(log).recorded(run.GAVE_UP)) == [], "--full recorded them as said"


def test_gave_up_is_recorded_only_for_the_titles_the_dm_names(tmp_path):
    """Said once: a title the budget counted instead is named the next morning."""
    log = tmp_path / "events.db"
    _seed_gave_up(tmp_path, log)

    first = _heavy_render(tmp_path / "d1", hm.EARLY, log=log)
    named = {f"Retired series {i}" for i in range(5) if f"Retired series {i} (" in first}
    recorded = {p["id"] for p in EventLog.open(log).recorded(run.GAVE_UP)}

    assert named, "the gave-up section did not reach the DM"
    assert recorded == {f"old{i}" for i in range(5) if f"Retired series {i}" in named}

    second = _heavy_render(tmp_path / "d2", hm.EARLY + timedelta(days=1), log=log)
    rest = {f"Retired series {i}" for i in range(5)} - named
    assert all(f"{title} (" in second for title in rest), "a counted title was never named"
    third = _heavy_render(tmp_path / "d3", hm.EARLY + timedelta(days=2), log=log)
    assert "gave up on notes" not in third


def test_a_state_md_refusal_still_fits(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise StateNotWritable("the file changed under the writer; " + "detail " * 60)

    monkeypatch.setattr(run, "_project", refuse)
    text = _heavy_render(tmp_path, hm.LATE, log=tmp_path / "events.db", write_state=True)
    head, _ = budget.split_parts(text)
    refusal = next(
        line for line in head.splitlines() if line.startswith("- couldn't update State.md")
    )

    assert len(head) <= budget.PUSH_BUDGET
    assert len(refusal) <= 200
    assert brief.unsourced_claims(head) == []


def test_main_prints_head_then_thread_and_a_summary(tmp_path, monkeypatch, capsys):
    """The CLI on the real clock: the payload's meetings are another day's, but
    the heavy State.md alone overflows, so the fit still has to act."""
    vault = tmp_path / "vault"
    (tmp_path / ".env").write_text(
        f"SLACK_USER_PRINCIPAL=UPRINCIPAL1\nEMAIL_PRINCIPAL=principal@example.com\nVAULT_ROOT={vault}\n",
        encoding="utf-8",
    )
    hm.folder(vault)
    payloads = tmp_path / "payloads.json"
    payloads.write_text(json.dumps(hm.payloads()), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert run.main(["render", "morning", "--payloads", str(payloads)]) == 0
    out = capsys.readouterr()
    head, _ = budget.split_parts(out.out.rstrip("\n"))

    assert len(head) <= budget.PUSH_BUDGET
    assert out.err.startswith("morning push: "), out.err
    assert budget.STATE_PATH in out.err, "the summary names where the cut rows live"

    assert run.main(["render", "morning", "--payloads", str(payloads), "--full"]) == 0
    assert len(capsys.readouterr().out) > budget.PUSH_BUDGET * 2
