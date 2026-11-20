"""Bring every local copy of a watched repo up to date before a recap reads it.

USING IT
    python -m daydag.repos sync [--log <events.db>]

    The first step of every daily and weekly recap: the morning, the wrap, the
    week-ahead, `ship` / `sprint` / `status`, the Friday report and plan, the
    feedback scan, the progress deck and team practices. The scheduled wrappers
    (`scripts/morning.sh`, `scripts/progress-deck.sh`) run it before Claude
    starts, so a headless run needs no extra permission for it.

WHAT IT TOUCHES
    1. DayDAG's mirrors under ``MIRROR_DIR``: clone what `Watchlist.md` names
       and is missing, ``git fetch --prune`` the rest. This is the pulse's own
       pre-step (`MirrorStore.sync`) without the read, so no cursor moves and
       the next brief's shipping section still reports from where it left off.
    2. His working copies under ``WORKTREE_DIRS`` (optional, ``:``-separated):
       any direct child that is a clone of a repo in a watched org. It is
       fetched, then its checked-out branch is fast-forwarded to its upstream -
       and nothing else. No branch switch, no stash, no reset, no merge commit,
       no hooks. A dirty tree, a diverged branch, a detached HEAD, a branch
       with no upstream or a merge/rebase in progress is fetched and left as it
       is, with one line saying why.

WHY IT EXISTS
    Syncing was opt-in (`render --mirrors`) and the scheduled morning never
    opted in, so on 2026-11-20 every mirror still stood at 23 Sep and the one
    working copy of discovery-services was 1,438 commits behind. A recap that
    reads code reads it from here.

Never blocks (guardrail 6): a repo that cannot be fetched is one line, and the
exit code is 0 unless the configuration itself is missing.
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from daydag.config import ConfigError, Identities
from daydag.pulse import (
    MirrorStore,
    PulseError,
    SyncReport,
    _run_git,
    mirror_root,
    read_watchlist,
)
from daydag.state import EventLog, StateFolder

__all__ = ["CopyResult", "main", "refresh_copy", "sync", "working_copies"]

USAGE = "usage: python -m daydag.repos sync [--log <events.db>]"

#: The owner in a GitHub remote, ssh or https: `git@github.com:Org/x.git`,
#: `https://github.com/Org/x`.
_OWNER = re.compile(r"github\.com[:/](?P<owner>[^/]+)/")

#: Markers of an operation a fast-forward must not walk into.
_IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")


@dataclass(frozen=True)
class CopyResult:
    """What refreshing one working copy did, as one line for the run log."""

    path: Path
    branch: str
    outcome: str
    pulled: int = 0

    def line(self) -> str:
        where = f"{self.path} ({self.branch})" if self.branch else str(self.path)
        return f"{where}: {self.outcome}"


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "no detail")


def _owner_of(path: Path) -> str | None:
    result = _run_git(["remote", "get-url", "origin"], cwd=path)
    if result.returncode != 0:
        return None
    match = _OWNER.search(result.stdout.strip())
    return match["owner"].casefold() if match else None


def working_copies(roots: Iterable[Path], owners: Iterable[str]) -> list[Path]:
    """Direct children of ``roots`` that are clones of a repo in ``owners``.

    One level deep on purpose: a recursive walk of ~/Documents is slow and
    would find vendored repos inside node_modules.
    """
    wanted = {owner.casefold() for owner in owners}
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if (child / ".git").exists() and _owner_of(child) in wanted:
                found.append(child)
    return found


def refresh_copy(path: Path) -> CopyResult:
    """Fetch one working copy, and fast-forward its branch only when that is safe."""
    fetched = _run_git(["fetch", "--prune", "--quiet"], cwd=path)
    if fetched.returncode != 0:
        return CopyResult(path, "", f"couldn't fetch - {_first_line(fetched.stderr)}")
    head = _run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=path)
    if head.returncode != 0:
        return CopyResult(path, "", "fetched; detached HEAD, left as is")
    branch = head.stdout.strip()
    for marker in _IN_PROGRESS:
        where = _run_git(["rev-parse", "--git-path", marker], cwd=path).stdout.strip()
        if where and (path / where).exists():
            return CopyResult(path, branch, "fetched; a merge or rebase is in progress, left as is")
    if _run_git(["rev-parse", "--verify", "--quiet", "@{u}"], cwd=path).returncode != 0:
        return CopyResult(path, branch, "fetched; no upstream branch, left as is")
    counts = _run_git(["rev-list", "--left-right", "--count", "HEAD...@{u}"], cwd=path)
    ahead, behind = (int(n) for n in counts.stdout.split())
    if behind == 0:
        extra = f" ({ahead} local commits not pushed)" if ahead else ""
        return CopyResult(path, branch, f"up to date{extra}")
    if ahead:
        return CopyResult(
            path, branch, f"fetched; diverged ({ahead} ahead, {behind} behind), not pulled"
        )
    dirty = _run_git(["status", "--porcelain", "--untracked-files=no"], cwd=path)
    if dirty.stdout.strip():
        return CopyResult(
            path, branch, f"fetched; uncommitted changes, {behind} commits not pulled"
        )
    # Hooks off: an unattended pull must not run a repo's post-merge install.
    merged = _run_git(
        ["-c", "core.hooksPath=/dev/null", "merge", "--ff-only", "--quiet", "@{u}"], cwd=path
    )
    if merged.returncode != 0:
        return CopyResult(
            path, branch, f"fetched; fast-forward failed - {_first_line(merged.stderr)}"
        )
    return CopyResult(path, branch, f"fast-forwarded {behind} commits", pulled=behind)


def _mirror_lines(report: SyncReport) -> list[str]:
    cloned = set(report.cloned)
    lines = []
    for mirror in report.mirrors:
        if mirror.label in cloned:
            lines.append(f"{mirror.label}: cloned")
        elif mirror.stale:
            lines.append(f"{mirror.label}: couldn't fetch - still as of {mirror.as_of}")
        else:
            lines.append(f"{mirror.label}: fetched")
    lines += [f"{gone.slug}: couldn't clone - {gone.reason}" for gone in report.unavailable]
    lines += [f"watchlist: {line}" for line in report.unreadable]
    return lines


def sync(identities: Mapping[str, str], log: EventLog | None = None) -> str:
    """Refresh mirrors and working copies; return the run-log text.

    Raises:
        ConfigError: no ``VAULT_ROOT`` or no ``MIRROR_DIR``.
        PulseError: an unreadable ``Watchlist.md``.
    """
    started = time.monotonic()
    root = identities.get("VAULT_ROOT")
    if not root:
        raise ConfigError("VAULT_ROOT is not set, so there is no Watchlist.md to read repos from")
    watchlist = read_watchlist(StateFolder.create(Path(root) / "DayDAG").watchlist_path)
    report = MirrorStore(mirror_root(identities), log=log).sync(watchlist)
    mirror_lines = _mirror_lines(report)
    cloned = set(report.cloned)
    stale = sum(1 for m in report.mirrors if m.stale)
    fetched = sum(1 for m in report.mirrors if m.label not in cloned and not m.stale)
    failed = stale + len(report.unavailable)

    roots = [
        Path(os.path.expandvars(part.strip())).expanduser()
        for part in identities.get("WORKTREE_DIRS", "").split(":")
        if part.strip()
    ]
    owners = {repo.slug.split("/")[0] for repo in watchlist.repos}
    copies = [refresh_copy(path) for path in working_copies(roots, owners)]
    pulled = sum(1 for copy in copies if copy.pulled)

    head = f"repos: {fetched} fetched, {len(cloned)} cloned, {failed} failed"
    if roots:
        head += f" · working copies: {pulled} of {len(copies)} fast-forwarded"
    head += f" · {time.monotonic() - started:.0f}s"
    lines = [head, *(f"- {line}" for line in mirror_lines)]
    if copies:
        lines += ["working copies:", *(f"- {copy.line()}" for copy in copies)]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "sync":
        print(USAGE, file=sys.stderr)
        return 2
    log = None
    if "--log" in args:
        after = args[args.index("--log") + 1 :]
        if not after or after[0].startswith("--"):
            print("--log needs the path of the event log", file=sys.stderr)
            return 2
        log = EventLog.open(Path(after[0]).expanduser())
    try:
        print(sync(Identities.from_file(Path(".env")), log))
    except (ConfigError, PulseError) as broken:
        print(f"couldn't refresh repos: {broken}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
