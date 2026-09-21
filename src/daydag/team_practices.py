"""The team's "How we work" metrics: ticket coverage, and review distribution.

USING IT
    prs = [PullRequest.from_record(r, repo=REPO) for r in gh_json]

    coverage(prs, repo=REPO, window=(start, end)).render()      # metric 1
    reviews(merged, open_prs, repo=REPO, week_of=monday,        # metric 2
            roles=ROLES, now=now).render()

    classify(one_pr)                    # -> Reference.TDE / GITHUB / NONE
    risk_tier(paths, load_risk_rules()) # -> "human" / "bot"
    .rows()                             # what either report stores in the log

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
    6. A GITHUB LOGIN never renders. `reviews` names reviewers by
       people-directory role token, because the doc asks who is reviewing and
       this repo is public. A login with no token is counted and not named,
       and the report says how many - see #162, which is the directory field
       that makes the map buildable.

WHY IT EXISTS
    The doc commits the team to three tracked metrics and nothing computed any
    of them. Two are here: monthly ticket coverage with the uncovered PRs
    listed so they can be back-filled, and the weekly review distribution with
    the three §2 rules a machine can see - risk tier, stuck PRs, old drafts.
    `docs/how-we-work-skills.md` is the full plan, including the third metric
    and the six helper skills that live in the team's own repo rather than here.

KNOWN LIMIT
    A PR whose only reference is in a review comment or a linked Jira
    remote-link reads as uncovered. Branch, title and body are what the doc
    itself names, and widening the search would make the number kinder than
    the practice it measures.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Container, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from daydag.brief import Section, claim, render_push
from daydag.voice import WARN

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
class Review:
    """One review, reduced to what metric 2 asks and nothing that identifies a PR's author.

    `is_self` is the only trace the PR's author leaves anywhere in this module:
    it is computed while the record is being read and the login is dropped on
    the same line. The doc excludes a self-review from the count, which cannot
    be done without comparing the two - but it can be done without keeping one.
    """

    by: str
    at: datetime | None
    is_self: bool


@dataclass(frozen=True)
class PullRequest:
    """One PR, reduced to what the two metrics ask.

    See contract 2 for the field that is missing on purpose. Everything below
    `merged_at` is optional because the coverage query does not ask for it -
    `--json reviews,files,isDraft,createdAt,updatedAt` is metric 2's cost, and
    a coverage run should not pay it.
    """

    number: int
    repo: str
    branch: str
    title: str
    body: str
    url: str
    merged_at: datetime | None
    reviews: tuple[Review, ...] = ()
    paths: tuple[str, ...] = ()
    is_draft: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_record(cls, record: dict[str, Any], *, repo: str) -> PullRequest:
        """Read one `gh pr list --json` row. Unknown keys are ignored, `author` among them."""
        number = int(record["number"])
        author = str((record.get("author") or {}).get("login") or "")
        return cls(
            number=number,
            repo=repo,
            branch=str(record.get("headRefName") or ""),
            title=str(record.get("title") or ""),
            body=str(record.get("body") or ""),
            url=str(record.get("url") or f"https://github.com/{repo}/pull/{number}"),
            merged_at=_utc(record.get("mergedAt")),
            reviews=tuple(_reviews_of(record, author)),
            paths=tuple(
                str(entry.get("path") or "") for entry in (record.get("files") or []) if entry
            ),
            is_draft=bool(record.get("isDraft")),
            created_at=_utc(record.get("createdAt")),
            updated_at=_utc(record.get("updatedAt")),
        )

    @property
    def text(self) -> str:
        """Branch, title and body - the three places the doc says a key may live."""
        return f"{self.branch}\n{self.title}\n{self.body}"


def _reviews_of(record: dict[str, Any], author: str) -> list[Review]:
    """Every review on the record, each marked self or not. ``author`` goes no further."""
    out = []
    for entry in record.get("reviews") or []:
        by = str((entry.get("author") or {}).get("login") or "")
        out.append(
            Review(by=by, at=_utc(entry.get("submittedAt")), is_self=bool(by) and by == author)
        )
    return out


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


# --------------------------------------------------------------------------
# metric 2 - review distribution, and the §2 rules a machine can see
# --------------------------------------------------------------------------

#: Business days a PR may sit without human review before it is called stuck.
#: SPEC 3.7's open-loop clock, applied to a review instead of an ask - one
#: number for both, because they are the same question about a different queue.
STALE_AFTER_BUSINESS_DAYS = 2

#: Calendar days a draft may stay open. The doc says "drafts <= 1 week".
DRAFT_AFTER_DAYS = 7

#: The role token a mapped bot account carries. A login ending `[bot]` is one
#: too, which covers a GitHub App without anyone having to list it.
BOT_ROLE = "bot"

#: Shipped risk rules. `reference/` holds facts a run consults; this is one.
RISK_PATHS = Path(__file__).resolve().parents[2] / "reference" / "team-practices-risk-paths.yml"


@dataclass(frozen=True)
class RiskRules:
    """Which paths need a human, which the bot can carry. Both from a config file."""

    human_required: tuple[str, ...] = ()
    bot_enough: tuple[str, ...] = ()


def load_risk_rules(path: Path | str | None = None) -> RiskRules:
    """Read `reference/team-practices-risk-paths.yml`.

    Raises:
        FileNotFoundError: if the file is gone. Deliberately not a silent
            empty ruleset - `risk_tier` treats an unmatched path as human, so
            an empty ruleset would report every merged PR as under-reviewed
            and the report would read as a crisis rather than a missing file.
    """
    import yaml

    text = Path(path or RISK_PATHS).read_text(encoding="utf-8")
    loaded = yaml.safe_load(text) or {}
    return RiskRules(
        human_required=tuple(loaded.get("human_required") or ()),
        bot_enough=tuple(loaded.get("bot_enough") or ()),
    )


def risk_tier(paths: Sequence[str], rules: RiskRules) -> str:
    """``"human"`` or ``"bot"`` for the set of paths a PR touched.

    The doc's tie-break, applied twice: one human-required path makes the whole
    PR human-required, and a path in neither list is human-required too. A PR
    that touched nothing at all is human - an empty file list means the query
    did not ask for files, not that the change was harmless.
    """
    if not paths:
        return "human"
    if any(_matches(path, rules.human_required) for path in paths):
        return "human"
    return "bot" if all(_matches(path, rules.bot_enough) for path in paths) else "human"


def _matches(path: str, patterns: Sequence[str]) -> bool:
    """`fnmatch`, in which `*` crosses `/` - so `docs/*` covers any depth."""
    return any(fnmatch(path, pattern) for pattern in patterns)


def business_days(start: date, end: date) -> int:
    """Weekdays in ``(start, end]``. Zero when ``end`` is not after ``start``.

    Half-open at the start so a review this morning reads as zero days idle,
    and holidays are not modelled - a company calendar the agent cannot see
    would make the number look precise without making it truer.
    """
    days = 0
    day = start
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days += 1
    return days


def _is_bot(login: str, roles: Mapping[str, str]) -> bool:
    return roles.get(login) == BOT_ROLE or login.endswith("[bot]")


def _human_reviews(pr: PullRequest, roles: Mapping[str, str]) -> list[Review]:
    """Reviews that count: not the author's own, not the bot's."""
    return [r for r in pr.reviews if not r.is_self and not _is_bot(r.by, roles)]


@dataclass(frozen=True)
class Reviews:
    """One week's answer to "who is reviewing", plus the three checkable §2 rules."""

    repo: str
    week_of: date
    merged: int
    counts: tuple[tuple[str, int], ...]
    bot_reviewed: int
    unmapped: int
    human_required_on_bot_only: tuple[int, ...]
    stale: tuple[tuple[int, int], ...]
    old_drafts: tuple[tuple[int, int], ...]
    unreachable: tuple[str, ...] = ()

    @property
    def concentration(self) -> tuple[str, int] | None:
        """The top reviewer's share of what merged, or None if nothing did."""
        if not self.counts or not self.merged:
            return None
        token, count = self.counts[0]
        return token, _share(count, self.merged)

    @property
    def query_url(self) -> str:
        return f"https://github.com/{self.repo}/pulls?q=is%3Apr+is%3Amerged"

    def _pr_url(self, number: int) -> str:
        return f"https://github.com/{self.repo}/pull/{number}"

    def rows(self) -> list[dict[str, Any]]:
        """One row per reviewer per week - the trend the doc asks to watch."""
        return [
            {
                "repo": self.repo,
                "week_of": self.week_of.isoformat(),
                "reviewer": token,
                "reviewed": count,
            }
            for token, count in self.counts
        ]

    def render(self) -> str:
        header = f"reviews - week of {_month_day(self.week_of)}"
        sections: list[Section] = []

        if not self.merged:
            sections.append(
                Section(
                    _month_day(self.week_of),
                    (f"- nothing merged that week ({self.query_url})",),
                )
            )
        else:
            table = " · ".join(f"{token} {count}" for token, count in self.counts)
            table = table or "no human reviews"
            lines = [claim(f"{table} · bot {self.bot_reviewed} of {self.merged}", self.query_url)]
            top = self.concentration
            if top:
                token, share = top
                glyph = "🔴" if share >= 50 else "🟡"
                lines.append(
                    claim(
                        f"{glyph} {token} reviewed {share}% of what merged"
                        " - the one-person-on-leave risk the doc names",
                        self.query_url,
                    )
                )
            if self.unmapped:
                plural = "" if self.unmapped == 1 else "s"
                verb = "has" if self.unmapped == 1 else "have"
                lines.append(
                    f"- {self.unmapped} reviewer{plural} {verb} no directory entry"
                    " - couldn't name them (python -m daydag.people)"
                )
            sections.append(Section(f"{self.merged} merged", tuple(lines)))

        if self.human_required_on_bot_only:
            sections.append(
                Section(
                    f"{WARN} human-required, merged on bot review alone "
                    f"({len(self.human_required_on_bot_only)})",
                    tuple(
                        claim(f"#{number}", self._pr_url(number))
                        for number in self.human_required_on_bot_only
                    ),
                )
            )
        if self.stale:
            sections.append(
                Section(
                    f"stuck ≥ {STALE_AFTER_BUSINESS_DAYS} biz days ({len(self.stale)})",
                    tuple(
                        claim(f"#{number} {days}d idle", self._pr_url(number))
                        for number, days in self.stale
                    ),
                )
            )
        if self.old_drafts:
            sections.append(
                Section(
                    f"drafts past a week ({len(self.old_drafts)}) - finish or close",
                    tuple(
                        claim(f"#{number} {days}d", self._pr_url(number))
                        for number, days in self.old_drafts
                    ),
                )
            )
        return render_push(header, sections, self.unreachable)


def reviews(
    merged: Iterable[PullRequest],
    open_prs: Iterable[PullRequest],
    *,
    repo: str,
    week_of: date,
    roles: Mapping[str, str],
    now: datetime,
    rules: RiskRules | None = None,
) -> Reviews:
    """Who reviewed what, and which of the doc's §2 rules the week broke.

    Args:
        merged: PRs merged in the week, with `reviews` and `files` fetched.
        open_prs: every open PR, for the stale and draft clocks. Passed
            separately because they answer different questions over different
            windows - folding them into one list was how an earlier draft
            reported a merged PR as stuck.
        roles: `github login -> people-directory role token`. A login absent
            from it is counted and NOT named: the directory has no login field
            yet (plan §7), and guessing one in a public repo is the failure
            this whole module is shaped around.
        now: the clock, injected. Business days are read in whatever zone the
            caller's `now` carries.
        rules: risk paths. Defaults to the shipped `reference/` file.
    """
    rules = rules if rules is not None else load_risk_rules()
    merged_list = list(merged)
    counter: Counter[str] = Counter()
    unmapped: set[str] = set()
    bot_reviewed = 0
    on_bot_only: list[int] = []

    for pr in merged_list:
        if any(_is_bot(r.by, roles) for r in pr.reviews if not r.is_self):
            bot_reviewed += 1
        human = _human_reviews(pr, roles)
        for login in {r.by for r in human}:
            token = roles.get(login)
            if token:
                counter[token] += 1
            else:
                unmapped.add(login)
        if not human and risk_tier(pr.paths, rules) == "human":
            on_bot_only.append(pr.number)

    stale: list[tuple[int, int]] = []
    drafts: list[tuple[int, int]] = []
    for pr in open_prs:
        opened = pr.created_at or now
        if pr.is_draft:
            age = (now - opened).days
            if age > DRAFT_AFTER_DAYS:
                drafts.append((pr.number, age))
            continue
        touched = [r.at for r in _human_reviews(pr, roles) if r.at]
        since = max(touched) if touched else opened
        idle = business_days(since.date(), now.date())
        if idle >= STALE_AFTER_BUSINESS_DAYS:
            stale.append((pr.number, idle))

    return Reviews(
        repo=repo,
        week_of=week_of,
        merged=len(merged_list),
        counts=tuple(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))),
        bot_reviewed=bot_reviewed,
        unmapped=len(unmapped),
        human_required_on_bot_only=tuple(on_bot_only),
        stale=tuple(stale),
        old_drafts=tuple(drafts),
    )
