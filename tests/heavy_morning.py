"""A synthetic morning the size of the real 7 Oct one, for the push budget (#185).

The real render that day was 59,510 characters against a Slack message of about
5,000, so every brief since 21 Sep was rewritten by hand. This builds a morning
of about the same SIZE and shape - 24 meetings with a 10-meeting pile-up and
four two-way clashes (the real day had 8 and five), 115 chase rows (111 with
the link on a sub-bullet, 3 struck), 43 watch rows (7 struck), an 11-item red
list with one 700-character item, 8 Slack mentions and 9 Gemini notes
overnight - and none of its content. Every name is a
role token, every link points at example.com, and every id is a placeholder the
secret scanner lets through.

Imported by the tests as `heavy_morning` (tests/ is on the pytest path).
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from daydag import brief, recipes, run
from daydag.brief import Brief
from daydag.ledger import Ledger
from daydag.state import StateFolder

PT = recipes.PACIFIC

#: A Wednesday, like 7 Oct: the brief at 06:45 and the late one at 13:43.
DAY = date(2026, 10, 7)
EARLY = datetime(2026, 10, 7, 6, 45, tzinfo=PT)
LATE = datetime(2026, 10, 7, 13, 43, tzinfo=PT)

IDENTITIES = {"SLACK_USER_PRINCIPAL": "UPRINCIPAL1", "EMAIL_PRINCIPAL": "principal@example.com"}

_ROLES = (
    "vp-data",
    "vp-ai",
    "cto",
    "sponsor",
    "gov-lead",
    "eng-2",
    "eng-4",
    "dataeng-1",
    "product-1",
)
_TOPICS = (
    "statement ingestion backfill",
    "compute consolidation plan",
    "revenue domain pilot scope",
    "artist bridge reprocessing",
    "staging data refresh",
    "dashboard access review",
    "contract import validation",
    "forecast model retrain",
    "catalog schema change",
    "release calendar sync",
)
_WORDS = (
    "the",
    "team",
    "said",
    "they",
    "would",
    "share",
    "the",
    "draft",
    "by",
    "friday",
    "and",
    "loop",
    "in",
    "finance",
    "once",
    "numbers",
    "reconcile",
    "against",
    "last",
    "quarter",
    "pending",
    "review",
    "from",
    "the",
    "pod",
    "after",
    "the",
    "steering",
    "call",
    "today",
)


def _sentence(rng: random.Random, length: int) -> str:
    words: list[str] = []
    while len(" ".join(words)) < length:
        words.append(rng.choice(_WORDS))
    return " ".join(words)[:length].rstrip()


def _calendar_link(rng: random.Random) -> str:
    # 150-ish characters, like Google's event links; never starts with 1.
    token = "".join(
        rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz") for _ in range(96)
    )
    return f"https://calendar.example.com/calendar/event?eid=E{token}&ctz=America/Los_Angeles"


def _slack_link(rng: random.Random) -> str:
    channel, ts = rng.randint(10, 99), rng.randint(10**9, 10**10 - 1)
    return f"https://example.slack.com/archives/CEXAMPLE{channel}/p{ts}"


def _mail_link(rng: random.Random) -> str:
    return f"https://mail.example.com/mail/u/0/#inbox/M{rng.randint(10**9, 10**10 - 1)}"


def _at(hour: int, minute: int = 0, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=PT)


def events(day: date = DAY, *, count: int = 24, seed: int = 7) -> list[dict[str, Any]]:
    """``count`` meetings: eight chained from 9:15 that run into the first noon
    pair (one 10-meeting pile-up), four more two-way clashes, the rest
    clash-free; three declined, one all-day. Datetimes, as the runner's
    payload adapter hands them to the brief."""
    rng = random.Random(seed)  # noqa: S311 - a fixed seed, not a secret
    slots: list[tuple[datetime, datetime]] = []
    # the pile-up: eight from 9:15, each overlapping the next, the last ending 12:05
    for i in range(8):
        start = _at(9, 15, day) + timedelta(minutes=20 * i)
        slots.append((start, start + timedelta(minutes=30)))
    # five clashing pairs from noon; the noon pair runs into the pile-up
    for hour in (12, 13, 14, 15, 16):
        slots.append((_at(hour, 0, day), _at(hour, 30, day)))
        slots.append((_at(hour, 15, day), _at(hour, 45, day)))
    # the rest, clash-free, early and late
    singles = [(_at(7, 0, day), _at(7, 15, day)), (_at(8, 0, day), _at(8, 15, day))]
    hour = 17
    while len(slots) + len(singles) < count:
        singles.append((_at(hour, 0, day), _at(hour, 30, day)))
        hour += 1
        if hour > 22:
            hour = 6
    slots = (slots + singles)[:count]
    out: list[dict[str, Any]] = []
    for index, (start, end) in enumerate(slots):
        topic = _TOPICS[index % len(_TOPICS)]
        title = f"{topic.title()} | {rng.choice(_ROLES)} x principal"[: rng.randint(10, 60)]
        out.append(
            {
                "id": f"evt{index:03d}",
                "summary": title,
                "start": start,
                "end": end,
                "permalink": _calendar_link(rng),
                "response_status": "declined" if index in (3, 11, 19) else "accepted",
                "attendees": ["principal@example.com", f"{rng.choice(_ROLES)}@example.com"],
            }
        )
    out.append(
        {
            "id": "evt-allday",
            "summary": "Office closed for the team offsite",
            "attendees": ["principal@example.com", "vp-data@example.com", "cto@example.com"],
            "start": day,
            "end": day + timedelta(days=1),
            "permalink": _calendar_link(rng),
        }
    )
    return out


#: Chase and watch rows run 40 to 520 characters in the live file, most short.
_ROW_LENGTHS = (40, 60, 80, 100, 120, 160, 200, 300, 520)


def _state_row(rng: random.Random, index: int, *, linked_inline: bool, struck: bool) -> list[str]:
    role, topic = rng.choice(_ROLES), _TOPICS[index % len(_TOPICS)]
    said = _sentence(rng, rng.choice(_ROW_LENGTHS))
    text = f'{role} · {topic} · "{said}"'
    if struck:
        text = f"~~{text}~~ ✓"
    if rng.random() < 0.2:
        text = f"⭐ {text}"
    if linked_inline:
        return [f"- {text} · [slack]({_slack_link(rng)})"]
    # the live file's shape: the citation on a sub-bullet, which a top-level read drops (#184)
    return [f"- {text}", f"  - [slack]({_slack_link(rng)})"]


def state_md(*, owed: int = 115, watch: int = 43, seed: int = 11) -> str:
    """State.md with ``owed`` chase rows (all but four linked on a sub-bullet,
    three struck) and ``watch`` watch rows (seven struck, a third unlinked)."""
    rng = random.Random(seed)  # noqa: S311 - a fixed seed, not a secret
    lines = ["# State", "", "## Chase list", ""]
    for i in range(owed):
        lines += _state_row(
            rng, i, linked_inline=i in (owed - 1, owed - 5, 7, 40), struck=i in (2, 30, 60)
        )
    lines += ["", "## Watch items", ""]
    for i in range(watch):
        lines += _state_row(rng, i, linked_inline=i % 3 != 0, struck=i % 6 == 1)
    return "\n".join(lines) + "\n"


def weekly_note(*, red: int = 11, seed: int = 13) -> str:
    """A weekly note whose priorities carry ``red`` open 🔴 items, one of 700 characters."""
    rng = random.Random(seed)  # noqa: S311 - a fixed seed, not a secret
    lines = ["# Priorities", ""]
    for i in range(red):
        length = 700 if i == 4 else rng.randint(60, 220)
        lines.append(f"- 🔴 {_TOPICS[i % len(_TOPICS)]}: {_sentence(rng, length)} *(mine)*")
    lines += ["- 🟡 something that can wait", "- 🟢 cross-teaming check-in"]
    return "\n".join(lines) + "\n"


def slack(*, count: int = 8, seed: int = 17) -> list[dict[str, Any]]:
    """Overnight mentions. No timestamp, so the brief keeps every one."""
    rng = random.Random(seed)  # noqa: S311 - a fixed seed, not a secret
    return [
        {
            "who": rng.choice(_ROLES),
            "text": _sentence(rng, rng.randint(80, 330)),
            "permalink": _slack_link(rng),
        }
        for _ in range(count)
    ]


def gmail(*, count: int = 9, seed: int = 19) -> list[dict[str, Any]]:
    """Gemini notes that landed overnight."""
    rng = random.Random(seed)  # noqa: S311 - a fixed seed, not a secret
    return [
        {
            "subject": f'Notes: "{_TOPICS[i % len(_TOPICS)].title()} sync" Oct 6, 2026',
            "permalink": _mail_link(rng),
        }
        for i in range(count)
    ]


def payloads(
    day: date = DAY, *, meetings: int = 24, mentions: int = 8, notes: int = 9, red: int = 11
) -> dict[str, Any]:
    """What the agent hands `run.render("morning")`: JSON-shaped, ISO instants."""

    def iso(value: Any) -> Any:
        return value.isoformat() if isinstance(value, (datetime, date)) else value

    calendar = [
        {key: iso(value) for key, value in event.items()} for event in events(day, count=meetings)
    ]
    return {
        "calendar": calendar,
        "slack": slack(count=mentions),
        "gmail": gmail(count=notes),
        "vault": weekly_note(red=red),
    }


def folder(root: Path, *, owed: int = 115, watch: int = 43) -> StateFolder:
    """A DayDAG folder whose State.md carries the heavy chase and watch lists."""
    made = StateFolder.create(root / "DayDAG")
    made.state_path.write_text(state_md(owed=owed, watch=watch), encoding="utf-8")
    return made


def brief_at(
    root: Path,
    now: datetime = LATE,
    *,
    owed: int = 115,
    watch: int = 43,
    meetings: int = 24,
    shuffle: int | None = None,
) -> Brief:
    """The assembled heavy Brief, for testing `daydag.budget` without the runner -
    its payloads read through the runner's own adapter, as a real morning's are."""
    day = now.astimezone(PT).date()
    data = payloads(day, meetings=meetings)
    if shuffle is not None:
        random.Random(shuffle).shuffle(data["calendar"])  # noqa: S311
    return brief.assemble(
        now=now.astimezone(UTC),
        sources=run._Payloads(data, weekly_path=recipes.weekly_note(day)),
        identities=IDENTITIES,
        state=folder(root, owed=owed, watch=watch),
        ledger=Ledger(),
        pulse=None,
    )
