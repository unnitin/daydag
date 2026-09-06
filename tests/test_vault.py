"""Vault write-back: changing one existing line in a note, safely (#6).

The spike question is not "can we write" - the vault is POSIX-writable. It is
"what happens when iCloud has a different idea", plus the ARCHITECTURE
invariant that a note is never rewritten wholesale.

Every test runs against tmp_path. The real vault is never touched.
"""

import os
import stat
import subprocess

import pytest

from daydag.vault import (
    ConflictError,
    NoteFormatError,
    NotMaterialisedError,
    OutsideVaultError,
    Vault,
)

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


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "Create Music Group"
    (root / "Weekly Notes").mkdir(parents=True)
    (root / "Weekly Notes" / "0817-0821.md").write_text(NOTE, encoding="utf-8")
    return Vault(root)


@pytest.fixture
def note(vault):
    return vault.note("Weekly Notes/0817-0821.md")


# -- 1. targeted line edits, not wholesale rewrites -----------------------


def test_reads_a_note_with_a_content_digest(note):
    snapshot = note.read()
    assert snapshot.text == NOTE
    assert len(snapshot.digest) == 64
    assert snapshot.mtime_ns > 0


def test_ticking_a_checkbox_changes_exactly_that_line(note):
    snapshot = note.read()
    edit = note.plan_tick(snapshot, contains="Compute consolidation")
    note.apply(edit)

    after = note.read().lines
    assert after[4] == "- [x] **Compute consolidation** *(mine)* - land the plan"
    assert len(after) == len(snapshot.lines)


@pytest.mark.guardrail
def test_every_byte_outside_the_edited_line_survives(note):
    """ARCHITECTURE invariant: never rewrite a note wholesale."""
    before = note.read()
    edit = note.plan_tick(before, contains="Compute consolidation")
    note.apply(edit)
    after = note.read()

    start, end = edit.byte_span
    # the untouched regions are the ORIGINAL bytes, spliced - not re-serialised
    assert after.data == before.data[:start] + edit.replacement + before.data[end:]
    # nothing was reflowed: same line count, exactly one line differs
    differing = [
        i for i, (a, b) in enumerate(zip(before.lines, after.lines, strict=True)) if a != b
    ]
    assert differing == [edit.lineno - 1]


@pytest.mark.guardrail
def test_ticking_does_not_move_the_item_to_done(note):
    """reference/vault-recipes: closed items are ticked in place. Practice wins."""
    note.apply(note.plan_tick(note.read(), contains="Compute consolidation"))
    text = note.read().text
    done = text.index("# Done this week")
    assert "Compute consolidation" in text[:done]
    assert "Compute consolidation" not in text[done:]


def test_an_already_ticked_item_is_a_no_op_edit(note):
    snapshot = note.read()
    edit = note.plan_tick(snapshot, contains="Wave 2 scope")
    assert edit.is_noop
    note.apply(edit)
    assert note.read().digest == snapshot.digest


def test_a_line_that_matches_nothing_is_an_error(note):
    with pytest.raises(LookupError):
        note.plan_tick(note.read(), contains="no such item")


def test_an_ambiguous_match_is_surfaced_not_guessed(vault):
    """Invariant 5: surface, don't resolve."""
    path = vault.root / "Weekly Notes" / "dupe.md"
    path.write_text("- [ ] standup\n- [ ] standup\n", encoding="utf-8")
    dupe = vault.note("Weekly Notes/dupe.md")
    with pytest.raises(LookupError, match="2 lines"):
        dupe.plan_tick(dupe.read(), contains="standup")


def test_append_is_additive_and_leaves_the_body_byte_identical(note):
    before = note.read()
    note.apply(note.plan_append(before, "- [ ] **New ask** *(mine)*"))
    after = note.read()
    assert after.data.startswith(before.data)
    assert after.lines[-1] == "- [ ] **New ask** *(mine)*"


def test_append_fixes_a_missing_trailing_newline(vault):
    path = vault.root / "Weekly Notes" / "nonl.md"
    path.write_bytes(b"- [ ] last line, no newline")
    n = vault.note("Weekly Notes/nonl.md")
    n.apply(n.plan_append(n.read(), "- [ ] appended"))
    assert n.read().text == "- [ ] last line, no newline\n- [ ] appended\n"


def test_an_edit_renders_as_a_unified_diff_for_the_remote_context(note):
    """Remote execution cannot edit in place; the same plan becomes a proposal."""
    diff = note.plan_tick(note.read(), contains="Compute consolidation").diff()
    assert "-- [ ] **Compute consolidation**" in diff
    assert "+- [x] **Compute consolidation**" in diff
    assert "Weekly Notes/0817-0821.md" in diff
    assert note.read().text == NOTE, "rendering a diff must not write anything"


# -- 2. iCloud: compare-and-swap on the read ------------------------------


@pytest.mark.guardrail
def test_refuses_to_write_when_the_file_changed_since_it_was_read(note):
    """The iCloud failure mode: another device wrote between read and write."""
    snapshot = note.read()
    edit = note.plan_tick(snapshot, contains="Compute consolidation")

    other_device = NOTE.replace("land the plan", "land the plan (edited on the phone)")
    note.path.write_text(other_device, encoding="utf-8")

    with pytest.raises(ConflictError) as excinfo:
        note.apply(edit)
    assert note.read().text == other_device, "the other device's write was clobbered"
    assert "edited on the phone" in str(excinfo.value), "conflict must show what is on disk"


@pytest.mark.guardrail
def test_a_same_length_change_is_still_caught(note):
    """mtime alone is not enough; the digest is the authority."""
    snapshot = note.read()
    edit = note.plan_tick(snapshot, contains="Compute consolidation")
    swapped = NOTE.replace("land the plan", "land the PLAN")
    note.path.write_bytes(swapped.encode("utf-8"))
    os.utime(note.path, ns=(snapshot.mtime_ns, snapshot.mtime_ns))

    with pytest.raises(ConflictError):
        note.apply(edit)


def test_a_note_deleted_between_read_and_write_is_a_conflict(note):
    edit = note.plan_tick(note.read(), contains="Compute consolidation")
    note.path.unlink()
    with pytest.raises(ConflictError):
        note.apply(edit)


def test_the_conflict_carries_both_digests_for_surfacing(note):
    snapshot = note.read()
    edit = note.plan_tick(snapshot, contains="Compute consolidation")
    note.path.write_text(NOTE + "- [ ] added elsewhere\n", encoding="utf-8")
    with pytest.raises(ConflictError) as excinfo:
        note.apply(edit)
    assert excinfo.value.expected_digest == snapshot.digest
    assert excinfo.value.actual_digest != snapshot.digest


def test_a_fresh_read_after_a_conflict_lets_the_edit_be_replanned(note):
    snapshot = note.read()
    edit = note.plan_tick(snapshot, contains="Compute consolidation")
    note.path.write_text(NOTE + "- [ ] added elsewhere\n", encoding="utf-8")
    with pytest.raises(ConflictError):
        note.apply(edit)
    note.apply(note.plan_tick(note.read(), contains="Compute consolidation"))
    text = note.read().text
    assert "- [x] **Compute consolidation**" in text
    assert "- [ ] added elsewhere" in text


# -- 3. iCloud: evicted placeholders --------------------------------------


@pytest.mark.guardrail
def test_an_evicted_note_is_not_read_as_empty(vault):
    """A dataless placeholder must raise, never look like an empty note."""
    (vault.root / "Weekly Notes" / ".0901-0905.md.icloud").write_bytes(
        b"bplist00\xd1\x01\x02_\x10\x13NSURLUbiquitousItem"
    )
    evicted = vault.note("Weekly Notes/0901-0905.md")
    with pytest.raises(NotMaterialisedError) as excinfo:
        evicted.read()
    assert "brctl download" in str(excinfo.value)


@pytest.mark.guardrail
def test_a_stub_left_in_place_of_the_note_is_not_read_as_content(vault):
    """Some evictions leave the real name in place holding plist bytes."""
    path = vault.root / "Weekly Notes" / "stub.md"
    path.write_bytes(b"bplist00\xd1\x01\x02_\x10\x13NSURLUbiquitousItemIsDownloading")
    with pytest.raises(NotMaterialisedError):
        vault.note("Weekly Notes/stub.md").read()


def test_a_genuinely_empty_note_reads_as_empty(vault):
    path = vault.root / "Weekly Notes" / "empty.md"
    path.write_text("", encoding="utf-8")
    assert vault.note("Weekly Notes/empty.md").read().text == ""


def test_undecodable_bytes_raise_rather_than_being_dropped(vault):
    path = vault.root / "Weekly Notes" / "bin.md"
    path.write_bytes(b"# ok\n\xff\xfe not utf-8\n")
    with pytest.raises(NoteFormatError):
        vault.note("Weekly Notes/bin.md").read()


def test_a_note_that_does_not_exist_at_all_is_a_plain_missing_file(vault):
    with pytest.raises(FileNotFoundError):
        vault.note("Weekly Notes/never.md").read()


# -- 4. atomic writes ------------------------------------------------------


@pytest.mark.guardrail
def test_a_failed_write_leaves_the_original_intact(note, monkeypatch):
    """Temp + os.replace: a crash mid-write cannot corrupt the note."""
    edit = note.plan_tick(note.read(), contains="Compute consolidation")

    def boom(*_args, **_kwargs):
        raise OSError("iCloud yanked the volume")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="yanked"):
        note.apply(edit)
    assert note.read().text == NOTE


def test_a_failed_write_leaves_no_temp_file_behind(note, monkeypatch):
    edit = note.plan_tick(note.read(), contains="Compute consolidation")

    def boom(*_args, **_kwargs):
        raise OSError("nope")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        note.apply(edit)
    assert [p.name for p in note.path.parent.iterdir()] == ["0817-0821.md"]


def test_the_write_preserves_file_mode(note):
    note.path.chmod(0o640)
    note.apply(note.plan_tick(note.read(), contains="Compute consolidation"))
    assert stat.S_IMODE(note.path.stat().st_mode) == 0o640


# -- 5. staying inside the vault ------------------------------------------


@pytest.mark.guardrail
def test_a_path_escaping_the_vault_is_refused(vault):
    """The decoy: `Weekly Notes/` also exists at the vault's parent (#6)."""
    (vault.root.parent / "Weekly Notes").mkdir()
    (vault.root.parent / "Weekly Notes" / "claude-write-test.md").write_text("x")
    with pytest.raises(OutsideVaultError):
        vault.note("../Weekly Notes/claude-write-test.md")


@pytest.mark.guardrail
def test_an_absolute_path_outside_the_vault_is_refused(vault, tmp_path):
    with pytest.raises(OutsideVaultError):
        vault.note(tmp_path / "elsewhere.md")


def test_an_absolute_path_inside_the_vault_is_accepted(vault):
    inside = vault.root / "Weekly Notes" / "0817-0821.md"
    assert vault.note(inside).read().text == NOTE


@pytest.mark.guardrail
def test_a_symlink_pointing_out_of_the_vault_is_refused(vault, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    (vault.root / "Weekly Notes" / "link.md").symlink_to(outside)
    with pytest.raises(OutsideVaultError):
        vault.note("Weekly Notes/link.md")


# -- 6. one line model: planning and splicing must agree ------------------


@pytest.mark.guardrail
def test_an_exotic_line_separator_does_not_shift_line_numbers(vault):
    """U+2028 arrives routinely in text pasted from Slack or Google Docs.

    ``str.splitlines()`` breaks on it; a byte splice on ``\\n`` does not. If the
    two disagree the tick lands on an unrelated line - a silent corruption the
    compare-and-swap cannot catch, because the file itself never changed.
    """
    path = vault.root / "Weekly Notes" / "pasted.md"
    original = (
        "# notes\n"
        "quote: hello\N{LINE SEPARATOR}world\n"  # U+2028 mid-line
        "- [ ] **Compute** land it\n"
        "- [ ] **Other** thing\n"
    )
    path.write_text(original, encoding="utf-8")
    n = vault.note("Weekly Notes/pasted.md")
    n.apply(n.plan_tick(n.read(), contains="Compute"))
    assert n.read().text == original.replace("- [ ] **Compute**", "- [x] **Compute**")


@pytest.mark.guardrail
def test_a_crlf_note_keeps_its_carriage_returns(vault):
    """Same root cause, reached by an ordinary Windows-authored note."""
    path = vault.root / "Weekly Notes" / "crlf.md"
    path.write_bytes(b"# n\r\n- [ ] **Compute** land it\r\n- [ ] tail\r\n")
    n = vault.note("Weekly Notes/crlf.md")
    n.apply(n.plan_tick(n.read(), contains="Compute"))
    assert n.read().data == b"# n\r\n- [x] **Compute** land it\r\n- [ ] tail\r\n"


# -- 7. the proposal a remote runtime emits -------------------------------


@pytest.mark.guardrail
def test_a_diff_never_carries_an_absolute_path(note):
    """Proposals/ is read on another machine; a local path leaks and breaks."""
    diff = note.plan_tick(note.read(), contains="Compute consolidation").diff()
    assert "Weekly Notes/0817-0821.md" in diff
    assert str(note.path.parent.parent) not in diff
    assert "/private/" not in diff


def test_a_diff_is_a_well_formed_patch(note):
    diff = note.plan_tick(note.read(), contains="Compute consolidation").diff()
    assert diff.startswith("--- a/Weekly Notes/0817-0821.md\n")
    assert "+++ b/Weekly Notes/0817-0821.md\n" in diff
    assert diff.endswith("\n")


def test_eviction_between_plan_and_apply_says_so(note):
    """apply() re-reads, so every refusal read() can raise surfaces here too."""
    edit = note.plan_tick(note.read(), contains="Compute consolidation")
    note.path.write_bytes(b"bplist00\xd1\x01\x02_\x10\x13NSURLUbiquitousItemIsDownloading")
    with pytest.raises(NotMaterialisedError):
        note.apply(edit)


@pytest.mark.parametrize("tail", ["\n", ""], ids=["trailing-newline", "no-trailing-newline"])
def test_the_diff_of_any_note_applies_with_git_apply(vault, tmp_path, tail):
    """The proposal has to survive the round trip, not just look like a patch.

    A note whose last line has no newline is the case that fuses two hunk lines
    unless the `\\ No newline at end of file` marker is emitted.
    """
    body = "# w\n\n- [ ] **Compute** land it\n- [x] **Other** done" + tail
    (vault.root / "Weekly Notes" / "patchme.md").write_text(body, encoding="utf-8")
    n = vault.note("Weekly Notes/patchme.md")
    patch = tmp_path / "p.diff"
    patch.write_text(n.plan_tick(n.read(), contains="Compute").diff(), encoding="utf-8")

    result = subprocess.run(
        ["git", "apply", "-p1", str(patch)],
        cwd=vault.root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert n.read().text == body.replace("- [ ] **Compute**", "- [x] **Compute**")


# -- 8. the planner's own invariants --------------------------------------


@pytest.mark.guardrail
def test_a_no_op_edit_still_checks_the_file_did_not_move(note):
    """Otherwise "already ticked" reports success against a file never re-read."""
    edit = note.plan_tick(note.read(), contains="Wave 2 scope")
    assert edit.is_noop
    note.path.write_text("wholesale replacement from the phone\n", encoding="utf-8")
    with pytest.raises(ConflictError):
        note.apply(edit)


@pytest.mark.guardrail
def test_a_replacement_containing_a_newline_is_refused(note):
    """A multi-line splice into a one-line span desyncs every later line number."""
    snapshot = note.read()
    with pytest.raises(ValueError, match="newline"):
        note.plan_line_edit(snapshot, 5, "- [x] one\n- [ ] two")
    with pytest.raises(ValueError, match="newline"):
        note.plan_append(snapshot, "- [ ] one\n- [ ] two")
