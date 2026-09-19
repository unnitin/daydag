"""Golden renders: every loop's push over one fixed world, held byte-for-byte.

The parity check for the simplification stack (docs/simplification.md, §4).
Captured on the tree that PR 1 leaves, before any module is merged or split,
so PRs 2-5 can reshape the code and prove the pushes did not move. A PR that
means to change a push regenerates the file for that loop and says so in its
description; a PR that does not mean to cannot change one by accident.

The world is built the way an agent following a plan builds it: one record per
source the plan names, keyed the way the step says to key it. Nothing here
knows which module reads what.

    UPDATE_GOLDENS=1 uv run pytest tests/test_golden.py     # regenerate
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from daydag import run
from daydag.config import Identities

GOLDEN = Path(__file__).parent / "golden"

#: Friday 10:00 PT, so `eod` looks at a Saturday, `week-ahead` at the week after,
#: and the Friday-only planning-outcome section of the wrap renders.
FRIDAY = datetime(2026, 9, 18, 17, 0, tzinfo=UTC)
PRINCIPAL = "UPRINCIPAL1"

WEEKLY_NOTE = """# 0914-0918

# Priorities

## 🔴 High

- [x] 🔴 land the ingestion backfill *(mine)*
- [ ] 🔴 compute consolidation plan *(mine w/ vp-data)*

## 🟡 Medium

- [ ] 🟡 amazon feed rebuild *(tracking: eng-4)*
"""

#: The hand-edited State.md shape (`tests/fixtures/state/hand_edited.md`), with
#: Slack-shaped permalinks so the chase loop has replies to read.
STATE = """# State

## Chase list

- vp-data · fruits metadata list · asked-on 2026-09-10 · status open
	- his words: *"on it"*
	- [slack · thread](https://x.slack.com/archives/CCHASE0001/p1789000000000000)
- gov-lead · define project expectations + coverage timelines · asked-on 2026-09-11 · status open
	- no link, this came from a hallway conversation

## Owed by you

- **gov-lead** · your read on his platform framing · waiting since 2026-09-14 06:00
	- [slack · his DM](https://x.slack.com/archives/DOWED00001/p1789390817581149?thread_ts=1789007120.252329&cid=DOWED00001)

### promises you made

- **to vp-ai** · *"will revert before eod today"* · said 2026-09-15 09:50 · ⚠ eod has passed
	- [slack](https://x.slack.com/archives/DPROMISE01/p1789488602000000)

## Watch items

- nightly ingest job, since the schema change

## Done

- ~~vp-data · answer on the Rich call · asked-on 2026-09-10~~ ✓ closed 2026-09-15

## Run log

- 2026-09-15T09:58 PT · morning brief (soak day 1/5) · reached: calendar, slack, gmail, vault
"""

DECISIONS = (
    "# Pending decisions\n\n"
    "- **2026-09-17 · shazam attribution** · options 1-4 · "
    "[slack](https://x.slack.com/archives/CDECIDE001/p1789667238635309) · status: open\n"
)


@pytest.fixture
def identities(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        f"SLACK_USER_PRINCIPAL={PRINCIPAL}\nEMAIL_PRINCIPAL=principal@x.com\n"
        f"VAULT_ROOT={tmp_path / 'vault'}\n"
        "ORG_EMAIL_DOMAIN=x.com\nPREP_LEADERSHIP=vp-ai@x.com\n",
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    (vault / "Weekly Notes").mkdir(parents=True)
    daydag = vault / "DayDAG"
    daydag.mkdir()
    (daydag / "State.md").write_text(STATE, encoding="utf-8")
    (daydag / "Decisions.md").write_text(DECISIONS, encoding="utf-8")
    (daydag / "Watchlist.md").write_text("# Watchlist\n", encoding="utf-8")
    return Identities.from_file(env)


def _meetings(day: str) -> list[dict]:
    """Two meetings on ``day``: a 1:1 and a steering room with an external party."""
    return [
        {
            "id": f"one-on-one-{day}",
            "summary": "Nitin / VP-Data 1:1",
            "start": f"{day}T09:00:00-07:00",
            "end": f"{day}T09:30:00-07:00",
            "attendees": [
                {"email": "principal@x.com", "displayName": "Nitin"},
                {"email": "vp-data@x.com", "displayName": "VP Data"},
            ],
            "response_status": "accepted",
            "organizer": "vp-data@x.com",
            "organizer_is_self": False,
            "permalink": f"https://example.com/cal/{day}/11",
        },
        {
            "id": f"steering-{day}",
            "summary": "Pod Steering",
            "start": f"{day}T11:00:00-07:00",
            "end": f"{day}T11:45:00-07:00",
            "attendees": [
                {"email": "principal@x.com", "displayName": "Nitin"},
                {"email": "vp-ai@x.com", "displayName": "VP AI"},
                {"email": "sponsor@vendor.example", "displayName": "Sponsor"},
            ],
            "response_status": "accepted",
            "organizer": "vp-ai@x.com",
            "organizer_is_self": False,
            "permalink": f"https://example.com/cal/{day}/steering",
        },
    ]


def payloads_for(plan: run.Plan) -> dict:
    """What an agent following ``plan`` hands back, one record per named step."""
    out: dict = {}
    first_day = ""
    for step in plan.steps:
        detail = step.detail
        if step.source == "calendar":
            day = str(detail.get("day", ""))
            first_day = first_day or day
            out.setdefault("calendar", []).extend(_meetings(day))
        elif step.source == "slack" and detail.get("key"):
            # A closure read: the owner answered the first ask, the rest are quiet.
            fetched = out.setdefault("closure", {})
            reply = (
                []
                if fetched
                else [
                    {
                        "user": "UVPDATA01",
                        "ts": str(float(detail["oldest"]) + 3600),
                        "text": "sent the list over, see the thread",
                        "permalink": "https://example.com/archives/CHANNELID/p1789400000000000",
                    }
                ]
            )
            fetched[detail["key"]] = reply
        elif step.source == "slack":
            since = float(detail.get("min_ts") or FRIDAY.timestamp() - 3600)
            out.setdefault("slack", []).append(
                {
                    "user": "UVPDATA01",
                    "ts": f"{since + 600:.6f}",
                    "text": f"<@{PRINCIPAL}> the compute consolidation doc is ready for a read",
                    "permalink": "https://example.com/archives/CHANNELID/p1789700000000000",
                    "channel": "CHANNELID",
                }
            )
        elif step.source == "gmail":
            out.setdefault("gmail", []).append(
                {
                    "id": "m1",
                    "subject": f'Notes: "Nitin / VP-Data 1:1" {first_day or "2026-09-18"}',
                    "date": f"{first_day or '2026-09-18'}T19:30:00Z",
                    "attendees": ["principal@x.com", "vp-data@x.com"],
                }
            )
        elif step.source == "vault":
            out["vault"] = WEEKLY_NOTE
        elif step.source == "vault_notes":
            out.setdefault("vault_notes", {}).update(
                {path: "# a note\n" for path in detail.get("paths", [])}
            )
        else:
            out.setdefault(step.source, [])
    return out


@pytest.mark.parametrize("loop", run.LOOPS)
def test_the_push_matches_its_golden(loop, identities):
    built = run.plan(loop, now=FRIDAY, identities=identities)
    text = run.render(loop, now=FRIDAY, identities=identities, payloads=payloads_for(built))
    path = GOLDEN / f"{loop}.txt"

    if os.environ.get("UPDATE_GOLDENS"):
        GOLDEN.mkdir(exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        pytest.skip(f"golden for {loop} rewritten")

    assert path.exists(), f"no golden for {loop} - run with UPDATE_GOLDENS=1 once"
    expected = path.read_text(encoding="utf-8").rstrip("\n")
    assert text.rstrip("\n") == expected, (
        f"{loop} rendered differently from tests/golden/{loop}.txt. If the change is "
        "meant, regenerate with UPDATE_GOLDENS=1 and say so in the PR."
    )
