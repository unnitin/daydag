"""Changing one existing line in an Obsidian note, safely (#6).

USING IT
    note = Vault(root).note("Weekly Notes/0908-0912.md")
    snap = note.read()                      # raises if evicted or outside root
    edit = note.plan_tick(snap, line_no)    # PURE: reads nothing, writes nothing
    edit.diff()                             # unified diff, for Proposals/
    note.apply(edit)                        # CAS against snap.digest

CONTRACTS
    1. Touch only what changed; leave the rest BYTE-FOR-BYTE. An edit is a byte
       splice over the bytes that were read. Nothing here parses a note into a
       model and prints it back out.
    2. A write is a compare-and-swap against the sha256 of what was read. A
       mismatch raises `ConflictError` carrying both digests and the current
       line - surface the conflict, never resolve it.
    3. An evicted iCloud placeholder is REFUSED, not read as empty. This is the
       one failure that would make the agent delete content it thought was
       absent. The message carries the `brctl download` remediation.
    4. Planning is pure. That is what lets one code path serve both execution
       contexts - apply locally, or render `diff()` into `DayDAG/Proposals/`
       for a human when there is no vault on the runtime at all (#24).
    5. Writes are atomic: temp file in the same directory, fsync, `os.replace`.
       A crash mid-write leaves the original note untouched.

WHY IT EXISTS
    The spike's premise was that the Obsidian connector is create/append-only,
    so the agent could never tick a checkbox in place. That premise is wrong
    for this context: the vault is on local disk under `iCloud~md~obsidian` and
    is POSIX read/write. Permissions are a non-issue.

    What is left once permissions stop being interesting is three real hazards
    - wholesale rewrite, sync racing the write, and dataless placeholders - and
    this module exists to hold all three in one place.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import os
import re
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

#: A markdown task line: leading indent, bullet, box. Tolerant per
#: reference/vault-recipes - title, owner tag, body and link are all optional.
_CHECKBOX = re.compile(r"^(?P<lead>[ \t]*[-*+] \[)(?P<box>[ xX])(?P<rest>\].*)$")


class VaultError(RuntimeError):
    """Base for every refusal in this module."""


class OutsideVaultError(VaultError):
    """A path resolved outside the vault root.

    Not hypothetical: a previous write landed ``claude-write-test.md`` in a
    ``Weekly Notes/`` folder at the vault's *parent*, because the vault prefix
    was dropped. Every path goes through :meth:`Vault.note`, which resolves
    symlinks before comparing.
    """


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

    ``name`` is the vault-relative path, and it is what a rendered diff shows -
    a proposal is read on another machine, so an absolute path there is both a
    leak and a patch nobody else can apply.
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
        """Lines split on ``\\n`` only - the same model the splice uses.

        Deliberately not ``str.splitlines()``. That also breaks on U+2028,
        U+0085, form feed and friends, which arrive routinely in text pasted
        from Slack or Google Docs. One such character mid-line and the line
        numbers planning sees stop matching the byte spans the write uses, so a
        tick lands on an unrelated line - and the compare-and-swap cannot catch
        it, because the file never changed. Splitting on ``\\n`` also keeps a
        CRLF note's ``\\r`` inside the line, so it survives a rewrite.
        """
        return [self.data[start:end].decode("utf-8") for start, end in _line_spans(self.data)]


@dataclass(frozen=True)
class NoteEdit:
    """One planned byte splice, and the snapshot it is valid against.

    Planning is pure. Nothing here has touched the disk, which is what lets the
    same object either be applied (local) or rendered as a proposal (remote).
    """

    snapshot: NoteSnapshot
    byte_span: tuple[int, int]
    replacement: bytes
    lineno: int
    before: str
    after: str

    @property
    def is_noop(self) -> bool:
        start, end = self.byte_span
        return self.snapshot.data[start:end] == self.replacement

    def result(self) -> bytes:
        """The full file bytes after the splice - originals either side."""
        start, end = self.byte_span
        return self.snapshot.data[:start] + self.replacement + self.snapshot.data[end:]

    def diff(self) -> str:
        """A unified diff of this edit - the remote context's deliverable.

        ``a/``/``b/`` prefixes and a trailing newline, so ``git apply -p1``
        takes it rather than rejecting a corrupt patch. Paths are vault-
        relative: this file is written for a human on another machine.
        """
        name = self.snapshot.name or self.snapshot.path.name
        return "".join(
            difflib.unified_diff(
                _patch_lines(self.snapshot.data),
                _patch_lines(self.result()),
                fromfile=f"a/{name}",
                tofile=f"b/{name}",
                n=2,
            )
        )


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

    def plan_line_edit(self, snapshot: NoteSnapshot, lineno: int, new_line: str) -> NoteEdit:
        """Replace line ``lineno`` (1-based); every other byte is untouched."""
        _reject_newlines(new_line)
        spans = _line_spans(snapshot.data)
        if not 1 <= lineno <= len(spans):
            raise LookupError(f"{snapshot.path.name} has no line {lineno}")
        start, end = spans[lineno - 1]
        return NoteEdit(
            snapshot=snapshot,
            byte_span=(start, end),
            replacement=new_line.encode("utf-8"),
            lineno=lineno,
            before=snapshot.data[start:end].decode("utf-8"),
            after=new_line,
        )

    def plan_tick(self, snapshot: NoteSnapshot, *, contains: str) -> NoteEdit:
        """Tick the one checkbox line containing ``contains``.

        Ticked **in place**. reference/vault-recipes records that the weekly
        note's own preamble says closed items are struck and moved to Done, and
        that in practice every closed item is ``- [x]`` where it sits with
        ``# Done this week`` left empty. Invariant 6: follow the vault.

        Ambiguity is an error rather than a first-match guess (invariant 5) -
        two near-identical standup lines is exactly the shape that recurs.
        """
        matches = [
            (n, line)
            for n, line in enumerate(snapshot.lines, 1)
            if contains in line and _CHECKBOX.match(line)
        ]
        if not matches:
            raise LookupError(f"no checkbox line in {snapshot.path.name} contains {contains!r}")
        if len(matches) > 1:
            at = ", ".join(str(n) for n, _ in matches)
            raise LookupError(
                f"{contains!r} matches {len(matches)} lines in {snapshot.path.name} "
                f"(lines {at}) - narrow it rather than picking one"
            )
        lineno, line = matches[0]
        match = _CHECKBOX.match(line)
        assert match is not None  # guaranteed by the filter above
        return self.plan_line_edit(snapshot, lineno, f"{match['lead']}x{match['rest']}")

    def plan_append(self, snapshot: NoteSnapshot, text: str) -> NoteEdit:
        """Append a line at the end - the purely additive case.

        Splices at EOF, so the whole existing body is carried through as its
        original bytes. A missing trailing newline is repaired here, since
        appending to such a file would otherwise silently join two lines.
        """
        _reject_newlines(text)
        data = snapshot.data
        prefix = b"" if (not data or data.endswith(b"\n")) else b"\n"
        end = len(data)
        return NoteEdit(
            snapshot=snapshot,
            byte_span=(end, end),
            replacement=prefix + text.encode("utf-8") + b"\n",
            lineno=len(snapshot.lines) + 1,
            before="",
            after=text,
        )

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
        # Checked before the no-op shortcut on purpose: "already ticked" must
        # not report success against a file the agent never re-read.
        if edit.is_noop:
            return
        _atomic_write(self.path, edit.result())


class Vault:
    """The vault root. Every note path is resolved through here."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def note(self, relative: str | Path) -> VaultNote:
        """A note inside the vault, or :class:`OutsideVaultError`."""
        candidate = Path(relative)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        root = self.root.resolve()
        if resolved != root and root not in resolved.parents:
            raise OutsideVaultError(
                f"{relative} resolves outside the vault root - refusing. "
                "A dropped vault prefix is how a note lands in the wrong tree."
            )
        # The relative name comes from the RESOLVED pair, so a symlinked root
        # (macOS /var -> /private/var) still yields a vault-relative name
        # rather than silently falling back to an absolute one.
        return VaultNote(resolved, resolved.relative_to(root).as_posix())


# -- helpers ---------------------------------------------------------------


def _reject_newlines(text: str) -> None:
    """One planned line must stay one line.

    Splicing several lines into a span the planner recorded as one desyncs
    every line number derived from the same snapshot - the same class of bug as
    a mismatched line model, and equally invisible to the compare-and-swap.
    Callers that want more lines plan more edits, or append.
    """
    if "\n" in text:
        raise ValueError("a replacement line may not contain a newline - plan one edit per line")


def _looks_like_placeholder(data: bytes) -> bool:
    head = data[:_SNIFF]
    return head.startswith(_PLACEHOLDER_MAGIC) and _PLACEHOLDER_MARKER in head


def _patch_lines(data: bytes) -> list[str]:
    """Lines with their newline kept - what ``difflib`` wants for a real patch.

    A final line with no newline carries git's ``\\ No newline at end of file``
    marker inline. ``difflib`` only prefixes the first physical line of each
    entry, so the marker lands unprefixed on its own line, which is exactly the
    format ``git apply`` expects. Without it the ``-`` and ``+`` hunk lines fuse
    into one and the patch is rejected as corrupt.
    """
    lines = [
        data[start:end].decode("utf-8") + ("\n" if end < len(data) else "")
        for start, end in _line_spans(data)
    ]
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n\\ No newline at end of file\n"
    return lines


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
    "OutsideVaultError",
    "Vault",
    "VaultError",
    "VaultNote",
]
