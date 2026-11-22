"""Replacing a note in the Obsidian vault, safely (#6).

USING IT
    note = VaultNote(path, "DayDAG/State.md")   # the file, and its vault-relative name
    snap = note.read()                          # raises if evicted or not UTF-8
    note.apply(note.plan_rewrite(snap, text))   # whole file, CAS against snap.digest
    note.create(text)                           # first run only; refuses an evicted note

CONTRACTS
    1. A write is a compare-and-swap against the sha256 of what was read. A
       mismatch raises `ConflictError` carrying both digests and the note's
       first line as it now stands - surface the conflict, never resolve it.
    2. An evicted iCloud placeholder is REFUSED, not read as empty. This is the
       one failure that would make the agent delete content it thought was
       absent. The message carries the `brctl download` remediation.
    3. Writes are atomic: temp file in the same directory, fsync, `os.replace`.
       A crash mid-write leaves the original note untouched.
    4. `plan_rewrite` replaces the WHOLE file, so "touch only what changed,
       leave the rest byte-for-byte" (house rule 3) is the CALLER's to hold,
       and both callers do: `state.update_state` refuses a file its parse
       cannot reproduce and only appends to it, and `style.append_rows`
       inserts rows into the lines it read. Planning is pure - it reads
       nothing and writes nothing.

WHY IT EXISTS
    The spike's premise was that the Obsidian connector is create/append-only,
    so the agent could never tick a checkbox in place. That premise is wrong
    for this context: the vault is on local disk under `iCloud~md~obsidian` and
    is POSIX read/write. Permissions are a non-issue.

    What is left once permissions stop being interesting is three real hazards
    - wholesale rewrite, sync racing the write, and dataless placeholders. This
    module holds the last two and the atomic write; the first is contract 4.

KNOWN LIMIT
    Nothing here checks that a path is inside the vault: each caller builds
    its own (`StateFolder` joins literal names onto its root; `style` is
    handed a file), and the refusal of a path that walks out of the vault is
    `recipes.vault_path`'s. A symlink is followed on read; the write replaces
    the link rather than writing through it. The line-edit planner - tick one
    checkbox, replace or append one line, render the edit as a diff for
    `Proposals/` - and the `Vault` root check it resolved paths through had
    no caller and were retired on 2026-11-21. Write-back to the weekly note
    (#16) is the agent editing the note directly.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

#: Both plist flavours an iCloud placeholder can arrive as.
_PLACEHOLDER_MAGIC = (b"bplist00", b"<?xml")

#: Present in the placeholder's payload whichever flavour it is.
_PLACEHOLDER_MARKER = b"NSURLUbiquitousItem"

#: How much of a file to look at before deciding it is a placeholder.
_SNIFF = 4096


class VaultError(RuntimeError):
    """Base for every refusal in this module."""


class NotMaterialisedError(VaultError):
    """The note is an iCloud placeholder - present in the listing, not on disk."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.remediation = f'brctl download "{path}"'
        super().__init__(
            f"{path.name} is an iCloud placeholder, not content. "
            f"Materialise it first: {self.remediation}"
        )


class NoteFormatError(VaultError):
    """The bytes on disk are not decodable UTF-8 text."""


class ConflictError(VaultError):
    """The note changed on disk between the read and the write.

    Carries what is needed to show the collision rather than settle it: both
    digests and the line as it now stands.
    """

    def __init__(
        self,
        path: Path,
        expected_digest: str,
        actual_digest: str | None,
        detail: str,
    ) -> None:
        self.path = path
        self.expected_digest = expected_digest
        self.actual_digest = actual_digest
        super().__init__(
            f"{path.name} changed since it was read - not writing. "
            f"read {expected_digest[:12]}, now {(actual_digest or 'gone')[:12]}. {detail}"
        )


@dataclass(frozen=True)
class NoteSnapshot:
    """The bytes of a note at one instant, plus what identifies that instant.

    ``digest`` is the authority: an edit on another device that happens to
    preserve both size and mtime still changes the sha256.

    ``name`` is the vault-relative path the caller named the note by. Nothing
    reads it today: the proposal diff that printed it was retired with the
    line-edit planner.
    """

    path: Path
    data: bytes
    mtime_ns: int
    name: str = ""

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def text(self) -> str:
        return self.data.decode("utf-8")

    @property
    def lines(self) -> list[str]:
        """Lines split on ``\\n`` only. What `apply` quotes from in a conflict.

        Deliberately not ``str.splitlines()``. That also breaks on U+2028,
        U+0085, form feed and friends, which arrive routinely in text pasted
        from Slack or Google Docs, and would cut the quoted line short at one.
        Splitting on ``\\n`` also keeps a CRLF note's ``\\r`` inside the line.
        (Why it is one model with `_line_spans`: the retired line-edit planner
        numbered lines here and spliced bytes there, and had the two disagreed
        an edit landed on the wrong line, where the compare-and-swap could not
        see it.)
        """
        return [self.data[start:end].decode("utf-8") for start, end in _line_spans(self.data)]


@dataclass(frozen=True)
class NoteEdit:
    """One planned replacement of a byte span, and the snapshot it is valid against.

    Planning is pure: nothing here has touched the disk. `plan_rewrite` is the
    only planner, so today the span is the whole file and ``lineno`` is 1.
    """

    snapshot: NoteSnapshot
    byte_span: tuple[int, int]
    replacement: bytes
    lineno: int

    @property
    def is_noop(self) -> bool:
        start, end = self.byte_span
        return self.snapshot.data[start:end] == self.replacement

    def result(self) -> bytes:
        """The full file bytes after the splice - originals either side."""
        start, end = self.byte_span
        return self.snapshot.data[:start] + self.replacement + self.snapshot.data[end:]


class VaultNote:
    """A single note. Read it, plan an edit against that read, apply the edit.

    The three steps are separate on purpose: the snapshot is the compare-and-
    swap token, so a caller cannot accidentally write against a stale view by
    skipping a step.
    """

    def __init__(self, path: Path, name: str | None = None) -> None:
        self.path = path
        #: Vault-relative, for anything a human reads. Never the absolute path.
        self.name = name or path.name

    # -- read ------------------------------------------------------------

    def read(self) -> NoteSnapshot:
        """Snapshot the note, refusing placeholders and undecodable bytes."""
        sidecar = self.path.parent / f".{self.path.name}.icloud"
        if not self.path.exists():
            if sidecar.exists():
                raise NotMaterialisedError(self.path)
            raise FileNotFoundError(self.path)

        stat_result = self.path.stat()
        data = self.path.read_bytes()
        if _looks_like_placeholder(data) or (sidecar.exists() and not data):
            raise NotMaterialisedError(self.path)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NoteFormatError(f"{self.path.name} is not UTF-8 text: {exc}") from exc
        return NoteSnapshot(self.path, data, stat_result.st_mtime_ns, self.name)

    # -- plan ------------------------------------------------------------

    def plan_rewrite(self, snapshot: NoteSnapshot, text: str) -> NoteEdit:
        """The whole note replaced by ``text`` - contract 4, never a shortcut.

        For a caller that renders the full file from a read it has already
        proven it reproduces. Applying it is a compare-and-swap against
        ``snapshot`` and atomic.
        """
        return NoteEdit(
            snapshot=snapshot,
            byte_span=(0, len(snapshot.data)),
            replacement=text.encode("utf-8"),
            lineno=1,
        )

    def create(self, text: str) -> None:
        """Write a note that does not exist yet, atomically.

        Raises :class:`NotMaterialisedError` when an evicted copy sits beside
        it - a missing file next to its `.icloud` sidecar is not a first run,
        and writing there would sync an empty file over the real one - and
        :class:`ConflictError` when the note already exists.
        """
        if (self.path.parent / f".{self.path.name}.icloud").exists():
            raise NotMaterialisedError(self.path)
        if self.path.exists():
            current = hashlib.sha256(self.path.read_bytes()).hexdigest()
            raise ConflictError(
                self.path, "nothing", current, "it exists; read it and plan an edit"
            )
        _atomic_write(self.path, text.encode("utf-8"))

    # -- apply -----------------------------------------------------------

    def apply(self, edit: NoteEdit) -> None:
        """Write the edit, refusing if the note moved under us.

        The compare-and-swap window narrows the race but cannot close it: no
        POSIX call can hold off the iCloud daemon. What it does guarantee is
        that a change the agent could have observed is never overwritten
        silently - the remaining window is microseconds, and the failure is
        loud when it loses.

        Raises :class:`ConflictError` if the note changed or vanished, and -
        because the check is a real re-read - anything else the read can refuse
        on: :class:`NotMaterialisedError` if iCloud evicted the note between
        plan and apply, :class:`NoteFormatError` if it came back non-text.
        Catch :class:`VaultError` for all of them.
        """
        try:
            current = self.read()
        except FileNotFoundError as exc:
            raise ConflictError(
                self.path, edit.snapshot.digest, None, "the note is gone from disk"
            ) from exc
        if current.digest != edit.snapshot.digest:
            raise ConflictError(
                self.path,
                edit.snapshot.digest,
                current.digest,
                f"on disk now: {_line_at(current, edit.lineno)!r}",
            )
        # Checked before the no-op shortcut on purpose: an edit that changes
        # nothing must not report success against a file that was never re-read.
        if edit.is_noop:
            return
        _atomic_write(self.path, edit.result())


# -- helpers ---------------------------------------------------------------


def _looks_like_placeholder(data: bytes) -> bool:
    head = data[:_SNIFF]
    return head.startswith(_PLACEHOLDER_MAGIC) and _PLACEHOLDER_MARKER in head


def _line_spans(data: bytes) -> list[tuple[int, int]]:
    """Byte ``(start, end)`` per line, ``end`` excluding the newline."""
    spans: list[tuple[int, int]] = []
    start = 0
    while start <= len(data):
        newline = data.find(b"\n", start)
        if newline == -1:
            if start < len(data):
                spans.append((start, len(data)))
            break
        spans.append((start, newline))
        start = newline + 1
    return spans


def _line_at(snapshot: NoteSnapshot, lineno: int) -> str:
    lines = snapshot.lines
    return lines[lineno - 1] if 1 <= lineno <= len(lines) else "<line no longer exists>"


def _atomic_write(path: Path, data: bytes) -> None:
    """Temp file in the same directory, fsync, ``os.replace``.

    Same directory because ``os.replace`` is only atomic within a filesystem.
    Dot-prefixed because Obsidian skips dotfiles, so a temp file that outlives
    a crash never shows up as a note.
    """
    handle, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".daydag-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        with contextlib.suppress(OSError):
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    """Best effort - the rename is durable on macOS without it, not on all FSs."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


__all__ = [
    "ConflictError",
    "NotMaterialisedError",
    "NoteEdit",
    "NoteFormatError",
    "NoteSnapshot",
    "VaultError",
    "VaultNote",
]
