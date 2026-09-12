"""Engineering pulse: git mirrors for history, the API for review state.

USING IT
    watchlist = read_watchlist(path)        # DayDAG/Watchlist.md
    store = MirrorStore(mirror_root(ids))
    report = store.sync(watchlist.repos)    # clone or fetch, push disabled

    pulse = Pulse.from_sync(report)
    pulse.observe_slack(message)            # ticket keys only, never echoed
    pulse.observe_pr(pr)
    pulse.apply_evidence()
    pulse.items()                           # -> [Item]; each one sourced
    pulse.render()

CONTRACTS
    1. State changes, NOT activity (SPEC 3.7 rule 1). The unit is "did the
       thing he is tracking move" - never a commit count against a named
       engineer.
    2. Repo evidence marks a loop MOVED, never CLOSED. Merged is not the same
       as what was asked for.
    3. A mirror is read-only by construction: push url is set to `no_push` at
       clone. There is no code path here that writes to a remote.
    4. A stale mirror contributes NO items and is reported as stale, carrying
       the time of the last fetch that actually worked. Silence is not health.
    5. Untrusted Slack text is parsed for ticket keys and nothing else. It is
       never echoed into output and never acted on.

WHY IT EXISTS
    Split by what the data physically IS. Git objects hold code and history and
    nothing else - review age, CI verdicts and board columns are not in the
    repo at any clone depth. So merges and code questions come from a local
    mirror against a stored cursor, and everything social comes from the API.

KNOWN LIMIT
    A mirror that has never been cloned is `MirrorUnavailable`, and
    `repo_is_absent` distinguishes "no such repo" from "the network failed".
    Calling both absent would delete a real problem from the report.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from daydag.config import ConfigError

#: Words that would turn this into a productivity metric. Asserted against.
FORBIDDEN_IN_OUTPUT = ("commits", "lines changed", "+/-", "contributions")

#: The push url every mirror gets. Not a valid remote, which is the point:
#: git resolves it as a local path, finds nothing, and refuses before it can
#: reach github. Invariant 4 enforced at the transport rather than in a prompt.
NO_PUSH = "no_push"


class PulseError(RuntimeError):
    """A source could not be read. Never silently swallowed into a clean report."""


#: Ceiling on any one git command. A clone of a large repo is minutes, not
#: hours; anything past this is git waiting on something that will never come.
GIT_TIMEOUT_SECONDS = 600

#: The exit code a timeout is reported as, so callers keep one failure path.
GIT_TIMED_OUT = 124


def _git_env() -> dict[str, str]:
    """Environment for every git call: no inherited repo, no prompt.

    ``GIT_DIR`` and friends override ``cwd``, and git exports them into its own
    hooks - so an unscrubbed environment means a mirror can confidently answer
    about a completely different repository, which turns guardrail 6's "repo
    state as of <ts>" into a quiet lie. ``GIT_TERMINAL_PROMPT=0`` is the other
    half: with output captured, a credential prompt on a private repo is an
    invisible wait, and the 6:40am pulse would hang instead of degrading.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(
    args: Sequence[str],
    cwd: Path | None = None,
    timeout: float = GIT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run one git command and hand back the result, failure included.

    shell=False with an argument list, so nothing here is shell-parsed: a cursor
    value or a watchlist line cannot break out into a second command. S607 (bare
    "git") is accepted deliberately - pinning an absolute path would break on
    the CI runner and on any machine with git somewhere else.

    A timeout comes back as a failed result rather than an exception, so a hung
    fetch degrades down the same path as any other failed one.
    """
    try:
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            env=_git_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=GIT_TIMED_OUT,
            stdout="",
            stderr=f"timed out after {timeout}s",
        )


@dataclass(frozen=True)
class Item:
    """One reported state change, with the link that makes it checkable.

    A permalink is required, and an empty one raises here rather than at the
    point it would be printed. Invariant 3 is "evidence or silence": an item
    built without a link used to render as ``- title ()``, which reads as a
    formatting glitch rather than as a claim nothing can back.

    Raise rather than render an explicitly-unsourced line, deliberately and
    consistently with the rest of the package - ``voice.render`` refuses to
    render a nudge with no permalink ("quote alone is not evidence"), and an
    item is what a nudge would be built from. An apologetic line still spends the
    reader's attention on a claim they can neither check nor act on, and a
    reader who sees one learns to skim the rest. Silence is the invariant's
    other half and it costs nothing. Refusing at construction also puts the
    failure at the source that dropped the link rather than three frames away in
    a renderer that only ever saw the symptom.
    """

    title: str
    permalink: str
    kind: str = "merge"

    def __post_init__(self) -> None:
        permalink = (self.permalink or "").strip()
        if not permalink:
            # Names the item. A raise nobody can trace back to a source is only
            # marginally better than the silent drop it replaced.
            raise ValueError(
                f"an item needs a permalink; a title alone is not evidence (title={self.title!r})"
            )
        # Stored stripped: whitespace would otherwise render inside the
        # brackets, and nothing upstream promises a trimmed string.
        object.__setattr__(self, "permalink", permalink)


@dataclass
class Joined:
    """A ticket key, and everything found under it across sources."""

    key: str
    pr_number: int | None = None
    pr_state: str | None = None
    mentioned_in_slack: bool = False


#: How a fetch time is written into a report. Absolute rather than relative
#: ("2 days ago"): a brief is read hours after it is built, and a relative age
#: computed at build time quietly drifts. UTC is stated, not assumed.
AS_OF_FORMAT = "%Y-%m-%d %H:%M %Z"

#: What the stale line says when no successful fetch was ever recorded. Not a
#: date and not silence: a mirror that has never once been read cleanly must not
#: be dated as if it were fresh, and omitting the clause reads as fresh too.
NO_FETCH_ON_RECORD = "an unknown time - no successful fetch on record"


def _utcnow() -> datetime:
    """The default clock. Injected everywhere it is used, so tests can wind it."""
    return datetime.now(UTC)


def _as_of(when: datetime | None) -> str:
    """Render a fetch time for the degrade line. Always carries a zone.

    `last_fetch` is a Protocol returning `datetime | None` with no timezone
    guarantee, so a store handing back a naive value is inside the contract.
    Rendered as-is, `%Z` is empty and the line said "as of 2026-09-04 06:40" -
    an unlabelled instant in the one sentence someone reads months later to
    decide whether a stale repo mattered. Stripping the trailing space made it
    tidy rather than answerable.

    A naive value is read as UTC, matching `state._as_utc` - the writer for
    this exact field, which assumes UTC when a stamp says nothing. Reading it
    as the principal's wall clock instead would render a naive 06:40 as
    "06:40 PDT", a seven-hour lie, and a confident wrong label is worse than
    the bare one it replaced.

    That is the opposite of `brief._local`, which reads a naive value as
    Pacific, and both are right: a calendar event's naive start is a human
    wall-clock time somebody typed, a fetch stamp is machine-generated by this
    process. Same syntax, different provenance.

    An already-aware value is rendered in its own zone, untouched - a fetch
    recorded in UTC reads as UTC, which is asserted in five places.
    """
    if when is None:
        return NO_FETCH_ON_RECORD
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.strftime(AS_OF_FORMAT).strip()


class Mirror:
    """A bare, push-disabled clone plus the cursor into it."""

    def __init__(
        self,
        path: Path,
        cursor: str,
        label: str | None = None,
        last_fetched_at: datetime | None = None,
    ) -> None:
        self.path = Path(path)
        self.cursor = cursor
        #: What a failure line calls this mirror. The owner belongs in it: two
        #: orgs can both have a `platform`, and "platform.git could not be read"
        #: names neither of them.
        self.label = label or Path(path).name
        self._fetch_failed = False
        #: When this mirror last fetched cleanly, from the event log. `None`
        #: means nothing is on record - never "now", which would date a mirror
        #: that has never been read successfully as if it had just been.
        self._last_fetched_at = last_fetched_at

    @classmethod
    def attach(
        cls, path: str | Path, cursor: str, last_fetched_at: datetime | None = None
    ) -> Mirror:
        return cls(Path(path), cursor, last_fetched_at=last_fetched_at)

    @property
    def stale(self) -> bool:
        return self._fetch_failed

    @property
    def last_fetched_at(self) -> datetime | None:
        return self._last_fetched_at

    @property
    def as_of(self) -> str:
        """The "as of <ts>" half of the degrade line (issue #32, #60)."""
        return _as_of(self._last_fetched_at)

    def mark_fetch_failed(self) -> None:
        """Record that this mirror could not be refreshed.

        Reporting a stale mirror as current is the one way this component can
        lie convincingly, so the state is explicit rather than inferred. The
        recorded fetch time is deliberately left alone: it dates the last run
        that *worked*, which is the whole question a reader has.
        """
        self._fetch_failed = True

    def mark_fetched(self, at: datetime) -> None:
        """Record a clean fetch at ``at``, which also clears staleness."""
        self._fetch_failed = False
        self._last_fetched_at = at

    def _git(self, *args: str) -> str:
        result = _run_git(args, cwd=self.path)
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


# -- what gets mirrored --------------------------------------------------


@dataclass(frozen=True)
class WatchedRepo:
    """One repo named in the ``repos`` block of ``DayDAG/Watchlist.md``."""

    owner: str
    name: str
    wiki: bool = False

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def mirror_subpath(self) -> Path:
        """Where this repo's mirror lives, relative to the mirror root.

        Keyed on owner AND name: repo names are only unique within an org, so a
        flat `<name>.git` would hand two orgs' `platform` repos the same
        directory - one silently overwriting the other, and its landings then
        reported under the wrong org's name.
        """
        return Path(self.owner, f"{self.name}.git")

    def wiki_repo(self) -> WatchedRepo:
        """The wiki as its own repo.

        GitHub serves a repo's wiki at ``<repo>.wiki.git``. Private wiki *pages*
        don't fetch anonymously, which is a documented gap; cloning the wiki over
        authenticated git gets them as plain markdown and closes it.
        """
        return WatchedRepo(self.owner, f"{self.name}.wiki")


def github_url(repo: WatchedRepo) -> str:
    """The clone url. Injectable, so tests clone local paths and never the network."""
    return f"https://github.com/{repo.owner}/{repo.name}.git"


class MirrorUnavailable(PulseError):
    """A mirror could not be provisioned.

    ``reason`` is the half that reaches the brief, so it says what is true of
    *this* failure - "could not clone" and "could not disable its push url" are
    different problems with different fixes, and reporting one as the other
    sends whoever reads it looking in the wrong place.
    """

    def __init__(self, slug: str, kind: str, reason: str, detail: str = "") -> None:
        super().__init__(f"{slug}: {reason}{f' - {detail}' if detail else ''}")
        self.slug = slug
        self.kind = kind
        self.reason = reason
        self.detail = detail

    @property
    def repo_is_absent(self) -> bool:
        """Whether git said the repo does not exist, as opposed to anything else.

        The distinction decides whether a missing wiki is normal or an outage,
        so it is read off git's own words rather than assumed from context: auth
        expiry, a refused connection and a rate limit all fail a clone too, and
        calling those "absent" would delete a real problem from the report.
        """
        return bool(_ABSENT_REPO.search(self.detail))


#: What git says when the thing it was asked to clone does not exist.
_ABSENT_REPO = re.compile(
    r"repository .*(not found|does not exist)|does not appear to be a git repository",
    re.IGNORECASE,
)


# -- reading the watchlist ------------------------------------------------

_HEADING = re.compile(r"^#{1,6}\s+(?P<name>.+?)\s*$")
#: `**jira**` on its own line. A hand-edited file labels sections both ways.
_BOLD_HEADING = re.compile(r"^\*\*(?P<name>[^*]+)\*\*:?$")
#: A horizontal rule, which ends whatever block was open.
_RULE = re.compile(r"-{3,}|\*{3,}|_{3,}")
_BULLET = re.compile(r"^[-*]\s+(?P<body>.+?)\s*$")
_MD_LINK = re.compile(r"^\[[^\]]*\]\((?P<url>[^)]+)\)(?P<rest>.*)$")
_GITHUB_URL = re.compile(r"^(?:https?://|git@)github\.com[/:](?P<path>.+)$")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: Markers a line may carry after a `·`, each in a field of its own.
_WIKI_MARKER = "wiki"

#: github.com paths whose first segment is the site, not an owner. Without this
#: `github.com/orgs/<org>/repositories` - a plausible paste - parses as the repo
#: `orgs/<org>` and puts a permanent failed-clone line in the brief.
_RESERVED_OWNERS = frozenset(
    {
        "orgs",
        "users",
        "settings",
        "search",
        "topics",
        "collections",
        "sponsors",
        "apps",
        "marketplace",
        "notifications",
        "explore",
        "about",
        "pricing",
        "login",
    }
)


def _slug_of(token: str) -> tuple[str, str] | None:
    """``owner``/``name`` from a slug or a github url, or ``None``."""
    token = token.strip().rstrip("/")
    url = _GITHUB_URL.match(token)
    parts = (url["path"] if url else token).split("/")
    # A pasted browser url carries a subpath (`/tree/main`); a bare slug must be
    # exactly two segments, or "a/b/c" would silently become "a/b".
    if len(parts) < 2 or (url is None and len(parts) != 2):
        return None
    owner, name = parts[0], parts[1].removesuffix(".git")
    if owner.casefold() in _RESERVED_OWNERS:
        return None
    if not _NAME.fullmatch(owner) or not _NAME.fullmatch(name):
        return None
    return owner, name


def _parse_repo_line(body: str) -> WatchedRepo | None:
    """One bullet body to a repo, or ``None`` if it does not name one."""
    link = _MD_LINK.match(body)
    if link:
        body = f"{link['url']} {link['rest']}"
    fields = [field_.strip().strip("()") for field_ in re.split(r"[·|]", body)]
    head = fields[0].split()
    if not head:
        return None
    slug = _slug_of(head[0])
    if slug is None:
        return None
    # A marker has to be a field of its own. Scanning the whole line for the
    # word would read "see the wiki for the runbook" as a marker and go cloning.
    markers = {field_.casefold() for field_ in fields[1:]}
    return WatchedRepo(*slug, wiki=_WIKI_MARKER in markers)


@dataclass(frozen=True)
class Watchlist:
    """The repos block, plus the lines that looked like repos and were not."""

    repos: list[WatchedRepo] = field(default_factory=list)
    #: Carried, not discarded: a slug with a typo silently stops being watched,
    #: and nothing else in the system would ever notice it had gone.
    unparsed: list[str] = field(default_factory=list)


def read_watchlist(path: str | Path) -> Watchlist:
    """Parse the ``repos`` block of ``Watchlist.md``.

    That file is config Nitin edits by hand, so it holds prose, trailing notes
    and markdown links alongside the slugs. A line that does not name a repo is
    skipped rather than raising - but one that *tried* to (it has a `/` or a
    github url in it) is kept as unparsed so the brief can say so.

    A missing or unreadable file raises ``PulseError`` rather than an ``OSError``
    from three frames down. It is a real failure - nothing is being watched - so
    it is not swallowed, but it stays inside the family the pulse degrades on:
    the vault is iCloud-synced, and an evicted placeholder is a documented
    hazard, not a reason for the 6:40am run to die on a traceback.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as unreadable:
        raise PulseError(f"could not read the watchlist at {path}: {unreadable}") from unreadable
    watchlist = Watchlist()
    in_repos = False
    for line in text.splitlines():
        stripped = line.strip()
        heading = _HEADING.match(stripped) or _BOLD_HEADING.match(stripped)
        if heading:
            # Exactly `repos`, not any heading starting with it: `## Reporting
            # cadence` in a hand-edited file would otherwise put its prose
            # bullets through the repo parser.
            in_repos = heading["name"].strip().casefold().rstrip(":") == "repos"
            continue
        if _RULE.fullmatch(stripped):
            # A horizontal rule ends the block. Without this a `---` divider
            # followed by a `**jira**` label leaks board rows into the clone
            # queue, and `ABC/board-4` becomes a repo DayDAG tries to fetch.
            in_repos = False
            continue
        bullet = _BULLET.match(stripped) if in_repos else None
        if bullet is None:
            continue
        body = bullet["body"].replace("`", "")
        repo = _parse_repo_line(body)
        if repo is not None:
            watchlist.repos.append(repo)
        elif "/" in body:
            watchlist.unparsed.append(body)
    return watchlist


def mirror_root(identities: Mapping[str, str]) -> Path:
    """Where mirrors live, from ``MIRROR_DIR``.

    Configured, never hardcoded: an absolute home path leaks a username into a
    public repo. Deliberately outside the vault - a few hundred MB of git
    objects in an iCloud-synced folder is a bad day.
    """
    if "MIRROR_DIR" not in identities:
        raise ConfigError("MIRROR_DIR is not set. Add it to .env; see .env.example.")
    resolved = os.path.expanduser(os.path.expandvars(identities["MIRROR_DIR"].strip()))
    # An empty value resolves to "." and an unset ${VAR} passes through as
    # literal text, so both would quietly clone hundreds of MB into the process
    # working directory or into a folder named after the variable.
    if not resolved or "$" in resolved:
        raise ConfigError("MIRROR_DIR is empty or names an unset variable. Fix it in .env.")
    return Path(resolved)


#: Cursor for a repo seen for the first time. First sight is not news: reporting
#: a fresh clone's whole history as "since the last run" would bury the day's
#: actual state changes under a backlog nobody asked about.
FIRST_SIGHT = "HEAD"


class FetchLog(Protocol):
    """The slice of the event log the pre-step needs (``state.EventLog``).

    A protocol rather than an import, because the two are separate surfaces that
    share only ``daydag.config`` by design. The pulse needs somewhere durable to
    put "this mirror last fetched cleanly at <ts>" - it does not need the store
    that answers "median days to answer" as well.
    """

    def record_fetch(self, repo: str, *, at: datetime) -> None: ...

    def last_fetch(self, repo: str) -> datetime | None: ...


@dataclass(frozen=True)
class Unavailable:
    """A watched repo with no usable mirror, and why."""

    slug: str
    reason: str


@dataclass
class SyncReport:
    """What one pre-step pass did, including everything it could not do."""

    mirrors: list[Mirror] = field(default_factory=list)
    cloned: list[str] = field(default_factory=list)
    #: Watched repos with no usable mirror - reported, never silently dropped.
    unavailable: list[Unavailable] = field(default_factory=list)
    #: Wikis that do not exist. Not a failure: GitHub creates a wiki lazily, so
    #: a repo whose wiki was never written has no `.wiki.git` to clone, and
    #: saying "could not clone" about it would put a permanent line in the brief.
    wikis_absent: list[str] = field(default_factory=list)
    #: Watchlist lines that look like repos but do not parse.
    unreadable: list[str] = field(default_factory=list)


class MirrorStore:
    """The mirror directory: clone on first sight, fetch every run after that.

    Every step is idempotent, because this runs three times a day against a
    directory a human can also touch. In particular the push url is re-disabled
    on every pass rather than only at clone time - enforcement that happens once
    is enforcement a single `git remote set-url` undoes forever.
    """

    #: Where a clone lands before it is a mirror. Named so it cannot collide
    #: with a repo directory, which is what makes cleanup safe: this path is
    #: always ours, so removing it can never take someone else's data with it.
    STAGING_PREFIX = ".incoming-"

    def __init__(
        self,
        root: str | Path,
        url_for: Callable[[WatchedRepo], str] = github_url,
        log: FetchLog | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.root = Path(root)
        self._url_for = url_for
        #: Optional. Without it the pre-step still runs; the stale line just has
        #: no date to carry, and says so rather than implying freshness.
        self._log = log
        #: Injected. A pre-step that reads the wall clock cannot be tested for
        #: the property that matters - that a *failed* fetch does not advance
        #: the date - because the assertion would have nothing to compare to.
        self._clock = clock

    def path_for(self, repo: WatchedRepo) -> Path:
        return self.root / repo.mirror_subpath

    def has(self, repo: WatchedRepo) -> bool:
        """Whether this repo is already provisioned.

        A directory without a ``HEAD`` file is not a mirror, so it is never read
        as one - and never deleted either. It gets reported and left alone.
        """
        return (self.path_for(repo) / "HEAD").is_file()

    def ensure(self, repo: WatchedRepo, cursor: str = FIRST_SIGHT) -> Mirror:
        """Provision the mirror if it is missing, and disable its push url.

        Raises ``MirrorUnavailable`` - the caller decides whether a repo it
        cannot provision is fatal. ``sync`` decides it is not.
        """
        path = self.path_for(repo)
        if not self.has(repo):
            self._clone(repo, path)
        self._disable_push(repo.slug, path)
        # Seeded from the log at attach time, because the mirror that needs a
        # date is by definition the one whose fetch is about to fail - so the
        # value can only come from a run that already finished.
        #
        # `label` is also the key the fetch stamp is filed under, and this is
        # the one place both are chosen - so the write key and the read key
        # cannot drift into disagreeing and quietly answering `None`.
        last = self._log.last_fetch(repo.slug) if self._log is not None else None
        return Mirror(path, cursor, label=repo.slug, last_fetched_at=last)

    def _clone(self, repo: WatchedRepo, path: Path) -> None:
        """Clone into staging, disable its push url there, then move it in.

        Two orderings matter. The guard goes on before the move, so there is no
        instant - not even an interrupted one - where a pushable mirror with a
        live credential sits at the path the agent reads.

        And the destination is never `rmtree`d. Cleaning up "a failed clone"
        that way deletes whatever is actually at that path: a restored backup,
        an iCloud copy, a directory that happens to share the name. A mirror is
        re-clonable, and those are not.
        """
        if path.exists():
            raise MirrorUnavailable(
                repo.slug, "occupied", "something that is not a mirror is in the way"
            )
        staging = path.with_name(f"{self.STAGING_PREFIX}{path.name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(staging, ignore_errors=True)  # leftover from an interrupted run
        # --mirror, not a checkout: log, diff, show and grep <rev> all work bare,
        # and a working tree only invites the agent to think it can edit.
        result = _run_git(["clone", "--mirror", self._url_for(repo), str(staging)])
        if result.returncode != 0:
            shutil.rmtree(staging, ignore_errors=True)
            raise MirrorUnavailable(
                repo.slug,
                "clone",
                "no mirror to read, could not clone it",
                result.stderr.strip(),
            )
        try:
            self._disable_push(repo.slug, staging)
        except MirrorUnavailable:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        staging.replace(path)

    def _disable_push(self, slug: str, path: Path) -> None:
        result = _run_git(["remote", "set-url", "--push", "origin", NO_PUSH], cwd=path)
        if result.returncode != 0:
            raise MirrorUnavailable(
                slug,
                "push-guard",
                "left unread, could not disable its push url",
                result.stderr.strip(),
            )

    def refresh(self, mirror: Mirror) -> bool:
        """``git fetch --prune`` one mirror. The pulse pre-step.

        A failed fetch marks the mirror stale instead of raising: the brief ships
        with one "repo state as of <ts>" line rather than stalling on a repo that
        happened to be unreachable at 6:40am.

        A success writes its time to the log, which is what dates that line on
        the next run that fails. Only a success writes - advancing the stamp on
        an attempt would date four-day-old objects as fetched this morning,
        which is precisely the convincing lie issue #60 is about.
        """
        result = _run_git(["fetch", "--prune"], cwd=mirror.path)
        if result.returncode != 0:
            mirror.mark_fetch_failed()
            return False
        self._remember_read(mirror)
        return True

    def _remember_read(self, mirror: Mirror) -> None:
        """Stamp a clean read of ``mirror`` - a fetch or a first-sight clone.

        A clone is a successful read of everything, so it stamps too. Without
        this a repo cloned yesterday whose origin is unreachable today reports
        "no successful fetch on record" - the strongest distrust signal there
        is - about objects known good one day ago.
        """
        at = self._clock()
        mirror.mark_fetched(at)
        if self._log is not None:
            self._log.record_fetch(mirror.label, at=at)

    @staticmethod
    def _targets(repos: Iterable[WatchedRepo]) -> list[tuple[WatchedRepo, bool]]:
        """Each watched repo, and the wiki repo of any marked ``wiki``.

        The flag says whether the target is optional. A wiki is: it may simply
        never have been written, which is normal rather than an outage.
        """
        targets: list[tuple[WatchedRepo, bool]] = []
        for repo in repos:
            targets.append((repo, False))
            if repo.wiki:
                targets.append((repo.wiki_repo(), True))
        return targets

    def sync(
        self,
        watched: Watchlist | Iterable[WatchedRepo],
        cursors: Mapping[str, str] | None = None,
    ) -> SyncReport:
        """Bring the mirror directory in line with the watchlist, then fetch.

        A repo that has dropped off the list is left exactly where it is.
        Deleting objects on a config edit is unrecoverable, and a line comes off
        that file for reasons ("not this quarter") that are not "destroy it".
        """
        watchlist = watched if isinstance(watched, Watchlist) else Watchlist(list(watched))
        cursors = cursors or {}
        report = SyncReport(unreadable=list(watchlist.unparsed))
        seen: set[Path] = set()
        for repo, optional in self._targets(watchlist.repos):
            path = self.path_for(repo)
            if path in seen:
                continue
            seen.add(path)
            newly = not self.has(repo)
            try:
                mirror = self.ensure(repo, cursors.get(repo.slug, FIRST_SIGHT))
            except MirrorUnavailable as unavailable:
                if optional and unavailable.repo_is_absent:
                    report.wikis_absent.append(repo.slug)
                else:
                    report.unavailable.append(Unavailable(repo.slug, unavailable.reason))
                continue
            if newly:
                report.cloned.append(repo.slug)
                # A clone is a clean read of the whole repo, so it dates the
                # mirror the same way a fetch does. Skipping it left a
                # day-old clone reporting "no successful fetch on record".
                self._remember_read(mirror)
            else:
                self.refresh(mirror)
            report.mirrors.append(mirror)
        return report


class Pulse:
    """Assembles the shipping block from mirrors and observed API state."""

    def __init__(
        self,
        mirrors: Iterable[Mirror] = (),
        unavailable: Iterable[Unavailable] = (),
        unreadable: Iterable[str] = (),
    ) -> None:
        self._mirrors = list(mirrors)
        self._unavailable = list(unavailable)
        self._unreadable = list(unreadable)
        self._joined: dict[str, Joined] = {}
        self._items: list[Item] | None = None

    @classmethod
    def from_sync(cls, report: SyncReport) -> Pulse:
        """Build the pulse from a pre-step pass, carrying its failures across.

        The wiring that matters: everything ``sync`` could not do reaches the
        report, so a repo that dropped out cannot go missing between the two.
        """
        return cls(
            mirrors=report.mirrors,
            unavailable=report.unavailable,
            unreadable=report.unreadable,
        )

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
        # OR, never overwrite. `pulse.apply_evidence` and this one are meant to
        # compose over the same loop, and each wrote the flag outright - so
        # whichever ran second erased the first, and a loop whose PR provably
        # merged went True -> False the moment the other source had nothing
        # that run. Evidence is monotonic: finding nothing is not finding
        # absence, and the chaser nudging work that demonstrably moved is the
        # cost of getting that backwards.
        return {
            **loop,
            "evidence_of_movement": bool(moved) or bool(loop.get("evidence_of_movement")),
        }

    # -- output ----------------------------------------------------------

    def items(self) -> list[Item]:
        if self._items is None:
            collected: list[Item] = []
            for mirror in self._mirrors:
                if mirror.stale:
                    continue
                try:
                    collected.extend(mirror.merges_since_cursor())
                except PulseError:
                    # One unreadable mirror is one line, not a dead brief: an
                    # unborn HEAD on a repo with nothing in it yet, or a cursor
                    # that stopped resolving after an upstream force-push, would
                    # otherwise take the whole shipping block down with it. The
                    # git message is deliberately not quoted here - it reaches
                    # the log, not a report that has to stay a report.
                    self._unavailable.append(
                        Unavailable(mirror.label, "could not read the mirror, nothing reported")
                    )
            self._items = collected
        return self._items

    def render(self) -> str:
        """The shipping block, or an empty string.

        A quiet day produces nothing at all rather than "no updates" - silence
        is information, and a line saying nothing happened is noise that trains
        the reader to skim.
        """
        lines = [f"- {item.title} ({item.permalink})" for item in self.items()]
        for mirror in self._mirrors:
            if mirror.stale:
                # Dated, per issue #32's "repo state as of <ts>". Undated
                # staleness is honest and unusable: twenty minutes and four days
                # both read as "could not fetch" and mean different things about
                # whether to trust the rest of the block.
                lines.append(f"- {mirror.label}: could not fetch, repo state as of {mirror.as_of}")
        for entry in self._unavailable:
            lines.append(f"- {entry.slug}: {entry.reason}")
        for line in self._unreadable:
            lines.append(f"- watchlist line not understood, so not watched: {line}")
        return "\n".join(lines)
