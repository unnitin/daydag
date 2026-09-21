"""Ticket coverage for the team's "How we work" commitments (SPEC-adjacent, see plan).

USING IT
    prs = [PullRequest.from_record(r, repo=REPO) for r in gh_json]
    report = coverage(prs, repo=REPO, window=(start, end))
    report.render()                     # the DM push
    report.rows()                       # what the event log stores
    classify(one_pr)                    # -> Reference.TDE / GITHUB / NONE

CONTRACTS
    1. Records are INJECTED, never fetched here. The caller runs `gh pr list
       --json number,title,body,headRefName,mergedAt,url` and hands the
       decoded list over. A client built into this module would be a source
       nobody can make fail on purpose, and the two failures that matter -
       an unauthorised token returning 403 and a window that quietly returns
       nothing - both arrive as a plausible empty list rather than an error.
    2. There is NO author. `PullRequest` does not parse one, so no render, no
       channel draft and no event-log row can leak one. The doc asks who is
       reviewing (`reviews` answers that, by role token); it does not ask for
       coverage per person, and a public repo is the wrong place to learn it.
    3. BOTH definitions, both labelled, neither blessed. The doc quotes
       "roughly 42%" and neither count reproduces it, so the report prints the
       TDE-keyed share against the target and the looser any-reference share
       beside it, saying which one the target applies to. Picking one is a
       decision, not a measurement - see the plan, §6 step 0.
    4. Jira CONFIRMS, it does not gate. `confirm_keys` marks keys that no
       ticket answers to, and if it raises, the coverage number is unchanged
       and one line says jira could not be checked (guardrail 6).
    5. Every uncovered PR renders with its own link (guardrail 3). A list of
       bare numbers is a list nobody can act on.

WHY IT EXISTS
    The doc commits the team to three tracked metrics and nothing computes any
    of them. This is the first: monthly ticket coverage, with the uncovered
    PRs listed so they can be back-filled. `docs/how-we-work-skills.md` is the
    full plan, including the two metrics that follow and the six helper skills
    that live in the team's own repo rather than here.

KNOWN LIMIT
    A PR whose only reference is in a review comment or a linked Jira
    remote-link reads as uncovered. Branch, title and body are what the doc
    itself names, and widening the search would make the number kinder than
    the practice it measures.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Container, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from daydag.brief import Section, claim, render_push

#: The share of merged PRs the doc asks to reach, against the TDE-keyed count.
TARGET_SHARE = 90

#: How many uncovered PRs the DM carries before it defers to the vault file.
#: The first real run listed 43 of them, which is a wall rather than a push;
#: the plan (§4 D1) puts the full list in `TeamPractices.md` for this reason.
UNCOVERED_IN_PUSH = 12

#: The human-editable mirror the full lists live in. Named here rather than
#: built from `VAULT_ROOT` because the push cites a path, it does not open one.
VAULT_MIRROR = "DayDAG/TeamPractices.md"

#: A Jira key for the data team's board. The one project `CLAUDE.md` records
#: as live; `DED`/`CING`/`DCTF` returned nothing in 14 days.
#:
#: No trailing `\b`: the repo carries `feature/TDE-653_other_thing`, and a word
#: boundary cannot sit between `3` and `_`, so the underscore form - a real
#: branch shape here - would have read as uncovered. `\d+` is greedy, so the
#: key still cannot be cut short.
_TDE_KEY = re.compile(r"\bTDE-\d+")

#: `fix/519-broken-join`, `docs/302-runbook` - a GitHub issue number carried as
#: the branch's first segment after the type.
_GH_BRANCH = re.compile(r"\A[a-z]+/\d+[-_]")

#: `#512`, `closes #44`, `Fixes 7`. The closing verbs are matched with or
#: without the hash because both forms are in the repo's history.
_GH_TEXT = re.compile(r"#\d+|\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s+#?\d+", re.IGNORECASE)

#: Branch prefixes an agent opened. The doc is explicit that these are not
#: exempt from the ticket rule, so they are counted in and flagged, never
#: filtered out.
AGENT_BRANCH_PREFIXES = ("claude/", "codex/")


class Reference(Enum):
    """What work item a merged PR points at, in the doc's own order of preference."""

    TDE = "tde"
    GITHUB = "github"
    NONE = "none"


@dataclass(frozen=True)
class PullRequest:
    """One merged PR, reduced to what the coverage question needs.

    Deliberately four fields and a link. See contract 2 for the field that is
    missing on purpose.
    """

    number: int
    repo: str
    branch: str
    title: str
    body: str
    url: str
    merged_at: datetime | None

    @classmethod
    def from_record(cls, record: dict[str, Any], *, repo: str) -> PullRequest:
        """Read one `gh pr list --json` row. Unknown keys are ignored, `author` among them."""
        number = int(record["number"])
        return cls(
            number=number,
            repo=repo,
            branch=str(record.get("headRefName") or ""),
            title=str(record.get("title") or ""),
            body=str(record.get("body") or ""),
            url=str(record.get("url") or f"https://github.com/{repo}/pull/{number}"),
            merged_at=_utc(record.get("mergedAt")),
        )

    @property
    def text(self) -> str:
        """Branch, title and body - the three places the doc says a key may live."""
        return f"{self.branch}\n{self.title}\n{self.body}"


def _utc(value: Any) -> datetime | None:
    """A GitHub timestamp as an aware UTC datetime, or None for an open PR.

    A machine wrote the stamp, so a naive one is read as UTC - the same
    provenance rule `pulse._as_of` follows, and the opposite of `brief._local`,
    where the naive value came from a human's calendar.
    """
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def keys(pr: PullRequest) -> tuple[str, ...]:
    """Every distinct TDE key the PR references, sorted, so a report is stable."""
    return tuple(sorted(set(_TDE_KEY.findall(pr.text))))


def is_agent_branch(pr: PullRequest) -> bool:
    return pr.branch.startswith(AGENT_BRANCH_PREFIXES)


def classify(pr: PullRequest) -> Reference:
    """Which work item this PR points at.

    TDE wins over a GitHub reference when both are present: the doc says
    "Jira is the system of record. Not GitHub issues, not both", so a PR
    carrying both is counted once, on the side the doc asks for.
    """
    if _TDE_KEY.search(pr.text):
        return Reference.TDE
    if _GH_BRANCH.match(pr.branch) or _GH_TEXT.search(f"{pr.title}\n{pr.body}"):
        return Reference.GITHUB
    return Reference.NONE


def _share(part: int, whole: int) -> int:
    """A whole percent. An empty window is 0%, not a division by zero."""
    return round(100 * part / whole) if whole else 0


def _month_day(day: date) -> str:
    return f"{day.strftime('%b').lower()} {day.day}"


@dataclass(frozen=True)
class Coverage:
    """One window's answer, both definitions, with what it could not confirm."""

    repo: str
    window: tuple[date, date]
    merged: int
    tde: int
    any_reference: int
    uncovered: tuple[int, ...]
    agent_branches: int
    agent_keyed: int
    missing_keys: tuple[tuple[str, int], ...]
    unreachable: tuple[str, ...]
    _prs: tuple[PullRequest, ...] = ()

    @property
    def tde_share(self) -> int:
        return _share(self.tde, self.merged)

    @property
    def any_share(self) -> int:
        return _share(self.any_reference, self.merged)

    @property
    def query_url(self) -> str:
        """The list the numbers came from - the citation every count carries."""
        return f"https://github.com/{self.repo}/pulls?q=is%3Apr+is%3Amerged"

    def rows(self) -> list[dict[str, Any]]:
        """One row per PR for the event log. The history a trend line is drawn from."""
        rows = []
        for pr in self._prs:
            found = keys(pr)
            rows.append(
                {
                    "number": pr.number,
                    "repo": pr.repo,
                    "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
                    "reference": classify(pr).value,
                    "key": found[0] if found else None,
                    "agent_branch": is_agent_branch(pr),
                }
            )
        return rows

    def render(self) -> str:
        """The DM push. House voice: lowercase, hyphens, sanctioned emoji only."""
        start, end = self.window
        header = f"ticket coverage - {self.repo.split('/')[-1]}"
        window_label = f"{_month_day(start)} → {_month_day(end)}"
        if not self.merged:
            return render_push(
                header,
                [Section(window_label, (f"- nothing merged in that window ({self.query_url})",))],
                self.unreachable,
            )

        glyph = "🟢" if self.tde_share >= TARGET_SHARE else "🔴"
        counts = [
            claim(
                f"{glyph} TDE-keyed: {self.tde} ({self.tde_share}%) · target {TARGET_SHARE}%",
                self.query_url,
            ),
            claim(
                f"🟡 any work-item ref: {self.any_reference} ({self.any_share}%) "
                "· the looser count, no target agreed",
                self.query_url,
            ),
        ]
        if self.agent_branches:
            counts.append(
                claim(
                    f"{self.agent_branches} from agent branches, {self.agent_keyed} of them keyed",
                    self.query_url,
                )
            )
        sections = [Section(f"{window_label}, {self.merged} merged", tuple(counts))]

        if self.uncovered:
            shown = self.uncovered[:UNCOVERED_IN_PUSH]
            lines = [
                claim(f"#{number}", f"https://github.com/{self.repo}/pull/{number}")
                for number in shown
            ]
            rest = len(self.uncovered) - len(shown)
            if rest:
                lines.append(f"- and {rest} more ({VAULT_MIRROR})")
            sections.append(Section(f"no reference at all ({len(self.uncovered)})", tuple(lines)))
        if self.missing_keys:
            sections.append(
                Section(
                    f"keys jira does not answer to ({len(self.missing_keys)})",
                    tuple(
                        claim(
                            f"{key} on #{number}",
                            f"https://github.com/{self.repo}/pull/{number}",
                        )
                        for key, number in self.missing_keys
                    ),
                )
            )
        return render_push(header, sections, self.unreachable)


def coverage(
    prs: Iterable[PullRequest],
    *,
    repo: str,
    window: tuple[date, date],
    confirm_keys: Callable[[Sequence[str]], Container[str]] | None = None,
) -> Coverage:
    """Classify a window of merged PRs, and confirm their keys if Jira answers.

    Args:
        prs: merged PRs, already fetched. Order is preserved in the report.
        repo: `owner/name`, used for the links every count and every PR cites.
        window: the closed date range the caller asked GitHub for. Reported as
            given - this does not re-filter, because a PR outside the window is
            a fetch bug and hiding it here would make it invisible.
        confirm_keys: takes every TDE key found, returns the ones that exist.
            Omitted, no confirmation is attempted and none is claimed. Raising
            costs one "couldn't check jira" line and nothing else.
    """
    ordered = tuple(prs)
    classified = [(pr, classify(pr)) for pr in ordered]
    agents = [pr for pr in ordered if is_agent_branch(pr)]

    missing: tuple[tuple[str, int], ...] = ()
    unreachable: tuple[str, ...] = ()
    found_keys = sorted({key for pr in ordered for key in keys(pr)})
    if confirm_keys is not None and found_keys:
        try:
            known = confirm_keys(found_keys)
        except Exception:  # any failure is the same one line, by contract 4
            unreachable = ("jira",)
        else:
            missing = tuple(
                (key, pr.number) for pr in ordered for key in keys(pr) if key not in known
            )

    return Coverage(
        repo=repo,
        window=window,
        merged=len(ordered),
        tde=sum(1 for _, ref in classified if ref is Reference.TDE),
        any_reference=sum(1 for _, ref in classified if ref is not Reference.NONE),
        uncovered=tuple(pr.number for pr, ref in classified if ref is Reference.NONE),
        agent_branches=len(agents),
        agent_keyed=sum(1 for pr in agents if classify(pr) is not Reference.NONE),
        missing_keys=missing,
        unreachable=unreachable,
        _prs=ordered,
    )
