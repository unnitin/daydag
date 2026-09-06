"""Engineering pulse: git mirrors for history, API for state.

Split by what the data physically is. Git objects hold code and history and
nothing else - review age, CI verdicts and board columns are not in the repo at
any clone depth. So merges and code questions come from a local mirror against a
stored cursor, and everything social comes from the API.

The rules here matter more than the features. SPEC section 3.7 rule 1 is "state
changes, not activity": the unit is *did the thing he is tracking move*, never a
commit count on a named engineer. And repo evidence marks a loop as moved but
never closes it - merged is not the same as what was asked for.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Words that would turn this into a productivity metric. Asserted against.
FORBIDDEN_IN_OUTPUT = ("commits", "lines changed", "+/-", "contributions")


class PulseError(RuntimeError):
    """A source could not be read. Never silently swallowed into a clean report."""


@dataclass(frozen=True)
class Item:
    """One reported state change, with the link that makes it checkable."""

    title: str
    permalink: str
    kind: str = "merge"


@dataclass
class Joined:
    """A ticket key, and everything found under it across sources."""

    key: str
    pr_number: int | None = None
    pr_state: str | None = None
    mentioned_in_slack: bool = False


class Mirror:
    """A bare, push-disabled clone plus the cursor into it."""

    def __init__(self, path: Path, cursor: str) -> None:
        self.path = Path(path)
        self.cursor = cursor
        self._fetch_failed = False

    @classmethod
    def attach(cls, path: str | Path, cursor: str) -> Mirror:
        return cls(Path(path), cursor)

    @property
    def stale(self) -> bool:
        return self._fetch_failed

    def mark_fetch_failed(self) -> None:
        """Record that this mirror could not be refreshed.

        Reporting a stale mirror as current is the one way this component can
        lie convincingly, so the state is explicit rather than inferred.
        """
        self._fetch_failed = True

    def _git(self, *args: str) -> str:
        # shell=False with an argument list, so nothing here is shell-parsed:
        # a cursor value cannot break out into a second command. S607 (bare
        # "git") is accepted deliberately - pinning an absolute path would break
        # on the CI runner and on any machine with git somewhere else.
        result = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=self.path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise PulseError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def merges_since_cursor(self, _fail: bool = False) -> list[Item]:
        """Merges between the cursor and the tip, oldest first.

        The cursor advances only once the caller has the result. Advancing on
        the attempt would silently skip commits that were never reported - a
        gap nobody would ever notice.
        """
        if _fail:
            raise PulseError("simulated read failure")
        # --first-parent WITHOUT --merges. The ruleset permits squash and rebase
        # as well as merge commits, and a squashed PR lands as a single-parent
        # commit - so filtering on --merges would silently report nothing for
        # the most common workflow. Every first-parent commit on the default
        # branch is one landing.
        out = self._git("log", "--first-parent", "--format=%H%x1f%s", f"{self.cursor}..HEAD")
        items: list[Item] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            sha, _, subject = line.partition("\x1f")
            items.append(Item(title=subject, permalink=f"{self.path}#{sha[:7]}"))
        items.reverse()
        self.cursor = self._git("rev-parse", "HEAD").strip()
        return items


class Pulse:
    """Assembles the shipping block from mirrors and observed API state."""

    def __init__(self, mirrors: Iterable[Mirror] = ()) -> None:
        self._mirrors = list(mirrors)
        self._joined: dict[str, Joined] = {}
        self._items: list[Item] | None = None

    # -- the ticket-key join ---------------------------------------------

    @staticmethod
    def _keys(text: str) -> set[str]:
        import re

        return set(re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", text or ""))

    def observe_slack(self, text: str) -> None:
        for key in self._keys(text):
            self._joined.setdefault(key, Joined(key)).mentioned_in_slack = True

    def observe_pr(self, *, title: str, number: int, state: str) -> None:
        for key in self._keys(title):
            joined = self._joined.setdefault(key, Joined(key))
            joined.pr_number = number
            joined.pr_state = state

    def by_ticket(self, key: str) -> Joined:
        """Everything known about a ticket, across Slack, PRs and notes."""
        return self._joined.get(key, Joined(key))

    def apply_evidence(self, loop: dict[str, Any]) -> dict[str, Any]:
        """Mark a loop as moved. Never close it.

        SPEC 3.7: merged is not the same as what was asked for, so this
        surfaces for confirmation and leaves the status exactly as it found it.
        """
        joined = self.by_ticket(loop.get("key", ""))
        moved = joined.pr_state == "merged" or joined.pr_number is not None
        return {**loop, "evidence_of_movement": bool(moved)}

    # -- output ----------------------------------------------------------

    def items(self) -> list[Item]:
        if self._items is None:
            collected: list[Item] = []
            for mirror in self._mirrors:
                if mirror.stale:
                    continue
                collected.extend(mirror.merges_since_cursor())
            self._items = collected
        return self._items

    def render(self) -> str:
        """The shipping block, or an empty string.

        A quiet day produces nothing at all rather than "no updates" - silence
        is information, and a line saying nothing happened is noise that trains
        the reader to skim.
        """
        lines = [f"- {item.title} ({item.permalink})" for item in self.items()]
        stale = [m for m in self._mirrors if m.stale]
        for mirror in stale:
            lines.append(f"- {mirror.path.name}: could not fetch, repo state as of last run")
        return "\n".join(lines)
