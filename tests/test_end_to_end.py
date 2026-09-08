"""The whole morning, walked end to end.

The unit tests each hold one seam. This file holds the walk between them: a
hand-edited `Watchlist.md` is read, the mirrors it names are provisioned and
fetched against a controlled clock, the pulse renders the shipping block, and
`State.md` is written from what came out. Nothing here reaches the network -
the origins are local repos from `conftest.py` and the clock is hand-wound.

It exists because both failure modes it asserts are *composition* failures. An
item with no permalink and a stale mirror with no date each look fine in
isolation, and only read as a lie once they are inside a brief someone is
deciding from.
"""

import re
import shutil
from datetime import UTC, datetime

import pytest

from daydag.pulse import Item, MirrorStore, Pulse, WatchedRepo, read_watchlist
from daydag.state import EventLog, StateFolder

FRIDAY = datetime(2026, 9, 4, 6, 40, tzinfo=UTC)
MONDAY = datetime(2026, 9, 7, 6, 40, tzinfo=UTC)

#: An item line, split into what it claims and what backs the claim.
ITEM_LINE = re.compile(r"^- (?P<claim>.+) \((?P<source>[^()]*)\)$")


@pytest.fixture
def clock():
    """A hand-wound clock. Time a test cannot control is a test that flakes."""

    class Clock:
        now = FRIDAY

        def __call__(self):
            return self.now

        def set(self, when):
            self.now = when

    return Clock()


@pytest.fixture
def morning(tmp_path, make_origin, clock):
    """One morning's worth of wiring: vault folder, event log, mirror store.

    `url_for` is the seam that keeps this offline - in production it resolves to
    github.com, here to a path on tmpfs. A repo with no origin resolves to a
    path that does not exist, which is exactly what an unreachable repo looks
    like to git.
    """
    origins: dict[str, object] = {}
    folder = StateFolder.create(tmp_path / "DayDAG")
    log = EventLog.open(":memory:")

    def url_for(repo):
        return str(origins.get(repo.slug, tmp_path / "origins" / f"{repo.name}-absent"))

    class Morning:
        def __init__(self):
            self.folder = folder
            self.log = log
            self.origins = origins
            self.store = MirrorStore(tmp_path / "mirrors", url_for=url_for, log=log, clock=clock)

        def add_repo(self, name, messages=("base",)):
            origins[f"ExampleOrg/{name}"] = make_origin(name, messages)
            return WatchedRepo("ExampleOrg", name)

        def watchlist(self, *names):
            body = "# Watchlist\n\n## repos\n" + "".join(f"- ExampleOrg/{n}\n" for n in names)
            folder.watchlist_path.write_text(body, encoding="utf-8")
            return read_watchlist(folder.watchlist_path)

        def run(self, watchlist, cursors=None):
            """One pulse pass: sync, render, hand back the advanced cursors.

            The cursors are read *after* rendering on purpose - that is the
            real ordering, and it is the property `test_pulse` pins separately:
            a cursor advances only once the caller has the result.
            """
            report = self.store.sync(watchlist, cursors=cursors or {})
            block = Pulse.from_sync(report).render()
            return block, {m.label: m.cursor for m in report.mirrors}

        def ship(self, block):
            """Put the block where a human reads it, the way a loop would."""
            watch = [{"what": line.removeprefix("- ")} for line in block.splitlines()]
            folder.write_state(watch=watch)
            return folder.read_state()

    return Morning()


def test_the_whole_morning_walks_from_watchlist_to_state_file(morning, add_commit):
    """Watchlist -> clone -> fetch -> pulse -> State.md, with no step mocked."""
    morning.add_repo("service-a")
    morning.add_repo("service-b")
    watchlist = morning.watchlist("service-a", "service-b")

    first, cursors = morning.run(watchlist)
    assert first == "", "first sight reported its own history as news"

    add_commit(morning.origins["ExampleOrg/service-a"], "Merge PR #412")
    block, _ = morning.run(watchlist, cursors)

    assert "Merge PR #412" in block
    assert "Merge PR #412" in morning.ship(block)


@pytest.mark.guardrail
def test_a_broken_mirror_degrades_and_the_rest_still_ships(morning, add_commit, clock):
    """Guardrail 6, and #60: the degrade line is *dated*.

    Two repos, one whose origin disappears over the weekend. The brief must ship
    the healthy repo's landing, name the broken one, and say when the broken one
    was last read cleanly - twenty minutes stale and four days stale mean
    different things about whether to trust the rest of the block, and an
    undated "as of last run" cannot tell them apart.
    """
    morning.add_repo("service-a")
    morning.add_repo("service-b")
    watchlist = morning.watchlist("service-a", "service-b")
    morning.run(watchlist)  # first sight clones
    _, cursors = morning.run(watchlist)  # a clean fetch on the Friday

    shutil.rmtree(morning.origins["ExampleOrg/service-b"])  # origin gone by Monday
    add_commit(morning.origins["ExampleOrg/service-a"], "Merge PR #413")

    clock.set(MONDAY)
    block, _ = morning.run(watchlist, cursors)

    assert "Merge PR #413" in block, "one dead source suppressed a healthy one"
    assert "ExampleOrg/service-b" in block, "the failure is unattributed"
    assert "could not fetch" in block
    assert "2026-09-04 06:40 UTC" in block, "staleness is admitted but not dated"
    assert "as of last run" not in block, "the undated wording survived"
    assert "no updates" not in block
    assert "2026-09-04 06:40 UTC" in morning.ship(block), (
        "the date was lost on the way to the vault"
    )


@pytest.mark.guardrail
def test_every_claim_that_reaches_the_morning_carries_a_source(morning, add_commit):
    """Invariant 3 across the walk, not just at the dataclass.

    Two halves, and the second is the one #59 is about. Every item line the walk
    produces must have something inside its brackets - `- title ()` reads as a
    formatting glitch rather than as a claim nothing can back - and an item that
    has no source cannot be constructed in the first place, so no code path can
    put one there.
    """
    morning.add_repo("service-a")
    watchlist = morning.watchlist("service-a")
    _, cursors = morning.run(watchlist)
    add_commit(morning.origins["ExampleOrg/service-a"], "Merge PR #412")

    block, _ = morning.run(watchlist, cursors)

    claims = [ITEM_LINE.match(line) for line in block.splitlines()]
    assert any(claims), "the walk produced no item line to check"
    for line, claim in zip(block.splitlines(), claims, strict=True):
        assert claim is not None, f"a line that is not a sourced claim: {line!r}"
        assert claim["source"].strip(), f"unsourced claim reached the brief: {line!r}"
    assert "()" not in morning.ship(block)

    with pytest.raises(ValueError, match="permalink"):
        Item(title="someone said it landed", permalink="")
