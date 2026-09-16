"""`State.md` is the record, and the writer appends to it (#130).

The file used to be a projection: every loop rendered `chase`, `watch` and
`notes_gaps` from what that one run derived and wrote the result over whatever
was there. On 2026-09-14 the first real `eod --write-state` run derived none of
the three and truncated a hand-written chase list to 41 bytes.

These tests describe the replacement. The fixture is the shape the file
actually grew into - a conventions preamble, sub-bullets under each item, an
`Owed by you` section with a nested heading, struck items under `Done`, and a
run log - none of which any version of the writer has ever emitted.
"""

from pathlib import Path

import pytest

from daydag.state import StateFolder

FIXTURE = Path(__file__).parent / "fixtures" / "state" / "hand_edited.md"


@pytest.fixture
def folder(tmp_path) -> StateFolder:
    made = StateFolder.create(tmp_path / "DayDAG")
    made.state_path.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return made


# -- the round trip: the whole guardrail in one assertion -------------------


def test_parsing_then_rendering_a_hand_edited_file_changes_nothing():
    """The safety valve every other behaviour here rests on.

    A writer that cannot reproduce the file it read has no business editing
    it. Byte-for-byte, including the blockquote, the tab-indented sub-bullets
    and the trailing newline.
    """
    from daydag.state import StateDoc

    text = FIXTURE.read_text(encoding="utf-8")

    assert StateDoc.parse(text).render() == text


def test_a_file_the_parser_cannot_reproduce_is_left_alone(folder):
    """If the round trip fails the write is refused, not attempted.

    The failure direction that matters: a file shape nobody anticipated is
    worth a skipped update and a complaint, never a best-effort rewrite.
    """
    from daydag.state import StateDoc

    weird = "# State\n\n﻿ not markdown at all\n"
    assert StateDoc.parse(weird).render() == weird, (
        "the parser is expected to be total - anything it cannot model is "
        "carried verbatim rather than dropped"
    )


# -- what #130 actually destroyed ------------------------------------------


@pytest.mark.guardrail
def test_a_run_that_derived_nothing_leaves_the_file_byte_for_byte(folder):
    """THE #130 REGRESSION. House rule 3: vault writes are additive.

    The eod wrap derived no chase, no watch and no gaps, and the old writer
    rendered exactly that over four hand-written items.
    """
    before = folder.read_state()

    folder.update_state()

    assert folder.read_state() == before


@pytest.mark.guardrail
def test_an_update_never_removes_an_item_it_did_not_derive(folder):
    """A derived set is not a complete set. It never was.

    The chase list is seeded by hand and by conversation; the loop that would
    derive it in full has never run. Anything the run did not see must stay.
    """
    folder.update_state(chase=[{"key": "NEW-1", "owner": "vp-ai", "ask": "wave 2 scope"}])

    text = folder.read_state()
    assert "fruits metadata list" in text, "a hand-written item was dropped"
    assert "define project expectations" in text
    assert "wave 2 scope" in text, "the derived item was not appended"


def test_the_sections_the_writer_never_emits_survive(folder):
    """`Owed by you`, its nested heading, `Done` and the run log are all
    sections no version of `update_state` renders. They are still his."""
    folder.update_state(chase=[{"key": "NEW-1", "owner": "vp-ai", "ask": "wave 2 scope"}])

    text = folder.read_state()
    for heading in ("## Owed by you", "### promises you made", "## Done", "## Run log"):
        assert heading in text, f"{heading} was lost"
    assert "How to talk back to me" in text, "the conventions preamble was lost"


def test_sub_bullets_under_an_item_are_never_reflowed(folder):
    """His comments live under the item, indented one tab. The file says so."""
    folder.update_state(chase=[{"key": "NEW-1", "owner": "vp-ai", "ask": "wave 2 scope"}])

    text = folder.read_state()
    assert "\t- @claude: expand on this please - unclear what each of the topics are here" in text
    assert '\t- his words 2026-09-10: *"let me pick it back up' in text


# -- appending ---------------------------------------------------------------


def test_a_new_item_lands_under_the_chase_heading_not_at_the_end(folder):
    folder.update_state(chase=[{"key": "NEW-1", "owner": "vp-ai", "ask": "wave 2 scope"}])

    lines = folder.read_state().splitlines()
    chase_at = lines.index("## Chase list")
    owed_at = lines.index("## Owed by you")
    landed = [i for i, line in enumerate(lines) if "wave 2 scope" in line]

    assert landed and all(chase_at < i < owed_at for i in landed), folder.read_state()


def test_a_new_item_carries_its_quote_permalink_asked_on_and_status(folder):
    """Soak edit, 2026-09-15: "for each item please link it back to either
    slack or email or notes to provide broader context for what i am chasing".

    Every one of these fields was already on `ChaseItem`. The old renderer
    emitted `- {owner} · {ask}` and dropped the rest.
    """
    folder.update_state(
        chase=[
            {
                "key": "NEW-1",
                "owner": "vp-ai",
                "ask": "wave 2 scope",
                "quote": "I'll have wave 2 scoped by Friday",
                "permalink": "https://example.com/archives/CHANNELID/p1789000000000002",
                "asked_on": "2026-09-15",
                "status": "open",
            }
        ]
    )

    text = folder.read_state()
    assert "I'll have wave 2 scoped by Friday" in text, "the verbatim quote was dropped"
    assert "p1789000000000002" in text, "house rule 1: the permalink was dropped"
    assert "asked-on 2026-09-15" in text
    assert "status open" in text


def test_an_item_already_in_the_file_is_not_appended_twice(folder):
    """Running the same loop twice in one morning must not double-file."""
    item = {"key": "NEW-1", "owner": "vp-ai", "ask": "wave 2 scope"}

    folder.update_state(chase=[item])
    folder.update_state(chase=[item])

    assert folder.read_state().count("wave 2 scope") == 1


def test_an_item_matching_a_hand_written_row_is_not_appended_beside_it(folder):
    """The hand-written rows carry no `key` - he wrote them. A derived item
    that is plainly the same ask must recognise itself in his wording rather
    than filing a duplicate underneath."""
    folder.update_state(chase=[{"key": "X-9", "owner": "vp-data", "ask": "fruits metadata list"}])

    assert folder.read_state().count("fruits metadata list") == 1


@pytest.mark.guardrail
def test_a_struck_item_is_never_re_added_or_un_struck(folder):
    """He closes an item by striking it. Re-deriving it would reopen work he
    has already told us is done - the loudest possible way to prove his edits
    do not win."""
    folder.update_state(
        chase=[{"key": "OLD-1", "owner": "vp-data", "ask": "answer on the Rich call"}]
    )

    text = folder.read_state()
    assert text.count("answer on the Rich call") == 1, "a closed item was re-added"
    assert "~~vp-data · answer on the Rich call" in text, "the strike was removed"


def test_watch_items_and_notes_gaps_append_the_same_way(folder):
    folder.update_state(
        watch=[{"what": "the backfill re-run"}],
        notes_gaps=["Pod Steering"],
    )

    text = folder.read_state()
    assert "nightly ingest job, since the schema change" in text, "the hand-written watch item went"
    assert "the backfill re-run" in text
    assert "Pod Steering" in text


# -- the gate that already worked, which must go on working ------------------


@pytest.mark.guardrail
def test_a_private_item_is_still_filtered_on_the_append_path(folder):
    """Contract 2's `_visible` gate. Appending rather than replacing must not
    route around the one filter a private carry-forward slipped past once."""
    marker = "growth-area-carry-forward"

    folder.update_state(
        chase=[{"key": "P-1", "owner": "vp-data", "ask": marker, "sensitivity": "private"}],
        watch=[{"what": marker, "sensitivity": "private"}],
        notes_gaps=[{"title": marker, "sensitivity": "private"}],
    )

    leaked = [
        path.relative_to(folder.root)
        for path in folder.root.rglob("*")
        if path.is_file() and marker in path.read_text(encoding="utf-8")
    ]
    assert not leaked, f"private content reached the vault: {leaked}"


# -- the backup #130 asked for ----------------------------------------------


def test_the_previous_contents_are_kept_before_any_write(folder):
    """ "Restored by hand from the session transcript; there is no backup path."
    Now there is one, and it is the last good version rather than a pile."""
    before = folder.read_state()

    folder.update_state(chase=[{"key": "NEW-1", "owner": "vp-ai", "ask": "wave 2 scope"}])

    backup = folder.root / "Archive" / "State.md.bak"
    assert backup.exists(), "no backup was taken"
    assert backup.read_text(encoding="utf-8") == before


def test_no_backup_is_written_when_nothing_changed(folder):
    """A no-op run must not churn the archive - or the iCloud sync."""
    folder.update_state()

    assert not (folder.root / "Archive" / "State.md.bak").exists()


@pytest.mark.guardrail
def test_a_file_that_fails_its_own_round_trip_is_refused_not_rewritten(folder, monkeypatch):
    """The valve, exercised. `parse` is total by construction, so the only way
    to reach this is to break it - which is exactly the change that must not
    be allowed to write the file anyway."""
    from daydag import state as state_module

    before = folder.read_state()
    monkeypatch.setattr(state_module.StateDoc, "render", lambda self: "# State\n")

    with pytest.raises(state_module.StateNotWritable):
        folder.update_state(chase=[{"key": "NEW-1", "owner": "vp-ai", "ask": "wave 2 scope"}])

    assert folder.read_state() == before, "the file was written despite the refusal"
