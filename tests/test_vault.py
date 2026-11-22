"""Vault write-back: replacing a note, safely (#6).

The spike question is not "can we write" - the vault is POSIX-writable. It is
"what happens when iCloud has a different idea".

`plan_rewrite` is the one planner: the line-edit planner (tick, line edit,
append, the proposal diff) and the `Vault` root check were retired on
2026-11-21 with no caller, and their tests with them. The guardrails that
exercised the compare-and-swap, the placeholder refusal and the atomic write
THROUGH a tick are here on the rewrite, which is the write `State.md` and
`Style.md` actually take. The ones whose subject was the planner itself are
re-pointed at the live code that holds the property today:

* every byte outside the edit survives, an exotic line separator, a CRLF
  file -> `tests/test_state_append.py`, on `StateFolder.update_state`;
* closed means ticked in place -> `tests/test_brief.py`, on the reader;
* a relative or absolute path out of the vault is refused ->
  `tests/test_recipes.py`, on `recipes.vault_path`;
* a symlink out of the vault -> `tests/test_guardrails.py`, the write half
  only: nothing refuses the link, and the write does not go through it.

Two had no subject left and are gone with the code: the proposal diff never
carrying an absolute path (nothing renders a proposal), and a replacement line
refusing a newline (nothing plans by line number).

Every test runs against tmp_path. The real vault is never touched.
"""

import os
import stat

import pytest

from daydag.vault import ConflictError, NoteFormatError, NotMaterialisedError, VaultNote

NOTE = (
    "# Week of August 17-21, 2026\n"
    "\n"
    "# Priorities\n"
    "## \N{LARGE RED CIRCLE} High - needs my hand this week\n"
    "- [ ] **Compute consolidation** *(mine)* - land the plan\n"
    "- [x] **Wave 2 scope** *(tracking: VP-AI)* - shipped\n"
    "\n"
    "# Done this week\n"
    "<!-- nothing moves here; items are ticked in place -->\n"
)

#: What a caller would write back: the note it read, plus one line.
EDITED = NOTE + "- [ ] **New ask** *(mine)*\n"


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "Create Music Group"
    (root / "Weekly Notes").mkdir(parents=True)
    (root / "Weekly Notes" / "0817-0821.md").write_text(NOTE, encoding="utf-8")
    return root


def _note(root, relative):
    """A note the way the live callers build one: the path, and a name for
    it beside the path."""
    return VaultNote(root / relative, relative)


@pytest.fixture
def note(root):
    return _note(root, "Weekly Notes/0817-0821.md")


# -- 1. reading, and the one write ------------------------------------------


def test_reads_a_note_with_a_content_digest(note):
    snapshot = note.read()
    assert snapshot.text == NOTE
    assert len(snapshot.digest) == 64
    assert snapshot.mtime_ns > 0


def test_a_rewrite_to_the_same_text_is_a_no_op_edit(note):
    snapshot = note.read()
    edit = note.plan_rewrite(snapshot, NOTE)
    assert edit.is_noop
    note.apply(edit)
    assert note.read().digest == snapshot.digest


# -- 2. iCloud: compare-and-swap on the read ------------------------------


@pytest.mark.guardrail
def test_refuses_to_write_when_the_file_changed_since_it_was_read(note):
    """The iCloud failure mode: another device wrote between read and write."""
    snapshot = note.read()
    edit = note.plan_rewrite(snapshot, EDITED)

    # On the first line, which is the one a rewrite's conflict quotes.
    other_device = NOTE.replace("August 17-21, 2026", "August 17-21, 2026 (edited on the phone)")
    note.path.write_text(other_device, encoding="utf-8")

    with pytest.raises(ConflictError) as excinfo:
        note.apply(edit)
    assert note.read().text == other_device, "the other device's write was clobbered"
    assert "edited on the phone" in str(excinfo.value), "conflict must show what is on disk"


@pytest.mark.guardrail
def test_a_same_length_change_is_still_caught(note):
    """mtime alone is not enough; the digest is the authority."""
    snapshot = note.read()
    edit = note.plan_rewrite(snapshot, EDITED)
    swapped = NOTE.replace("land the plan", "land the PLAN")
    note.path.write_bytes(swapped.encode("utf-8"))
    os.utime(note.path, ns=(snapshot.mtime_ns, snapshot.mtime_ns))

    with pytest.raises(ConflictError):
        note.apply(edit)


def test_a_note_deleted_between_read_and_write_is_a_conflict(note):
    edit = note.plan_rewrite(note.read(), EDITED)
    note.path.unlink()
    with pytest.raises(ConflictError):
        note.apply(edit)


def test_the_conflict_carries_both_digests_for_surfacing(note):
    snapshot = note.read()
    edit = note.plan_rewrite(snapshot, EDITED)
    note.path.write_text(NOTE + "- [ ] added elsewhere\n", encoding="utf-8")
    with pytest.raises(ConflictError) as excinfo:
        note.apply(edit)
    assert excinfo.value.expected_digest == snapshot.digest
    assert excinfo.value.actual_digest != snapshot.digest


def test_a_fresh_read_after_a_conflict_lets_the_edit_be_replanned(note):
    snapshot = note.read()
    edit = note.plan_rewrite(snapshot, EDITED)
    note.path.write_text(NOTE + "- [ ] added elsewhere\n", encoding="utf-8")
    with pytest.raises(ConflictError):
        note.apply(edit)
    fresh = note.read()
    note.apply(note.plan_rewrite(fresh, fresh.text + "- [ ] **New ask** *(mine)*\n"))
    text = note.read().text
    assert "- [ ] **New ask** *(mine)*" in text
    assert "- [ ] added elsewhere" in text


# -- 3. iCloud: evicted placeholders --------------------------------------


@pytest.mark.guardrail
def test_an_evicted_note_is_not_read_as_empty(root):
    """A dataless placeholder must raise, never look like an empty note."""
    (root / "Weekly Notes" / ".0901-0905.md.icloud").write_bytes(
        b"bplist00\xd1\x01\x02_\x10\x13NSURLUbiquitousItem"
    )
    evicted = _note(root, "Weekly Notes/0901-0905.md")
    with pytest.raises(NotMaterialisedError) as excinfo:
        evicted.read()
    assert "brctl download" in str(excinfo.value)


@pytest.mark.guardrail
def test_a_stub_left_in_place_of_the_note_is_not_read_as_content(root):
    """Some evictions leave the real name in place holding plist bytes."""
    path = root / "Weekly Notes" / "stub.md"
    path.write_bytes(b"bplist00\xd1\x01\x02_\x10\x13NSURLUbiquitousItemIsDownloading")
    with pytest.raises(NotMaterialisedError):
        _note(root, "Weekly Notes/stub.md").read()


def test_a_genuinely_empty_note_reads_as_empty(root):
    path = root / "Weekly Notes" / "empty.md"
    path.write_text("", encoding="utf-8")
    assert _note(root, "Weekly Notes/empty.md").read().text == ""


def test_undecodable_bytes_raise_rather_than_being_dropped(root):
    path = root / "Weekly Notes" / "bin.md"
    path.write_bytes(b"# ok\n\xff\xfe not utf-8\n")
    with pytest.raises(NoteFormatError):
        _note(root, "Weekly Notes/bin.md").read()


def test_a_note_that_does_not_exist_at_all_is_a_plain_missing_file(root):
    with pytest.raises(FileNotFoundError):
        _note(root, "Weekly Notes/never.md").read()


def test_eviction_between_plan_and_apply_says_so(note):
    """apply() re-reads, so every refusal read() can raise surfaces here too."""
    edit = note.plan_rewrite(note.read(), EDITED)
    note.path.write_bytes(b"bplist00\xd1\x01\x02_\x10\x13NSURLUbiquitousItemIsDownloading")
    with pytest.raises(NotMaterialisedError):
        note.apply(edit)


# -- 4. atomic writes ------------------------------------------------------


@pytest.mark.guardrail
def test_a_failed_write_leaves_the_original_intact(note, monkeypatch):
    """Temp + os.replace: a crash mid-write cannot corrupt the note."""
    edit = note.plan_rewrite(note.read(), EDITED)

    def boom(*_args, **_kwargs):
        raise OSError("iCloud yanked the volume")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="yanked"):
        note.apply(edit)
    assert note.read().text == NOTE


def test_a_failed_write_leaves_no_temp_file_behind(note, monkeypatch):
    edit = note.plan_rewrite(note.read(), EDITED)

    def boom(*_args, **_kwargs):
        raise OSError("nope")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        note.apply(edit)
    assert [p.name for p in note.path.parent.iterdir()] == ["0817-0821.md"]


def test_the_write_preserves_file_mode(note):
    note.path.chmod(0o640)
    note.apply(note.plan_rewrite(note.read(), EDITED))
    assert stat.S_IMODE(note.path.stat().st_mode) == 0o640


# -- 5. an edit that changes nothing ---------------------------------------


@pytest.mark.guardrail
def test_a_no_op_edit_still_checks_the_file_did_not_move(note):
    """Otherwise "nothing to change" reports success against a file never
    re-read. `style.append_rows` handed only blank rows plans exactly this;
    `update_state` returns before planning when it derived nothing."""
    edit = note.plan_rewrite(note.read(), NOTE)
    assert edit.is_noop
    note.path.write_text("wholesale replacement from the phone\n", encoding="utf-8")
    with pytest.raises(ConflictError):
        note.apply(edit)


# -- a whole-file rewrite, for a caller that owns its own round trip ----------


def test_a_rewrite_is_still_a_compare_and_swap(root):
    """`State.md`'s writer renders the whole file from a parse it has already
    proven byte-for-byte. The rewrite is a compare-and-swap: a change on
    another device between read and write is refused, not lost."""
    note = _note(root, "DayDAG/State.md")
    note.path.parent.mkdir(parents=True, exist_ok=True)
    note.path.write_text("# State\n", encoding="utf-8")
    snap = note.read()
    edit = note.plan_rewrite(snap, "# State\n- new\n")

    note.path.write_text("# State\n- from his phone\n", encoding="utf-8")
    with pytest.raises(ConflictError):
        note.apply(edit)

    assert note.path.read_text(encoding="utf-8") == "# State\n- from his phone\n"


def test_a_rewrite_applies_atomically_when_nothing_moved(root):
    note = _note(root, "DayDAG/State.md")
    note.path.parent.mkdir(parents=True, exist_ok=True)
    note.path.write_text("# State\n", encoding="utf-8")

    note.apply(note.plan_rewrite(note.read(), "# State\n- new\n"))

    assert note.path.read_text(encoding="utf-8") == "# State\n- new\n"


def test_create_writes_a_note_that_does_not_exist(root):
    note = _note(root, "DayDAG/State.md")
    note.path.parent.mkdir(parents=True, exist_ok=True)

    note.create("# State\n")

    assert note.read().text == "# State\n"


def test_create_refuses_a_note_that_exists_or_is_evicted(root):
    """`create` is for a first run. An existing note is a conflict, and an
    evicted one is the content-deleting case contract 2 exists for."""
    note = _note(root, "DayDAG/State.md")
    note.path.parent.mkdir(parents=True, exist_ok=True)
    note.path.write_text("# mine\n", encoding="utf-8")
    with pytest.raises(ConflictError):
        note.create("# State\n")
    assert note.path.read_text(encoding="utf-8") == "# mine\n"

    note.path.unlink()
    (note.path.parent / ".State.md.icloud").write_bytes(b"bplist00")
    with pytest.raises(NotMaterialisedError):
        note.create("# State\n")
    assert not note.path.exists()
