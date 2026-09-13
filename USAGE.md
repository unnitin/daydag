# Using DayDAG

## The one thing to understand

**Python cannot call an MCP connector. The agent can.** So a loop runs in two
halves with the fetch in between:

```
plan(loop)              ->  the bounded queries        [python]
                            the agent runs them        [MCP]
render(loop, payloads)  ->  the push                   [python]
```

That is why there is no `daydag run` that does everything, and why a plain
`crontab` entry cannot work — it would produce a brief with every source empty
and four "couldn't check" lines. **The thing that runs a loop is an agent
session**, not a script.

## Day to day

Open Claude Code in this repo and say what you want:

> run my morning

The `daily-loops` skill does the three steps for you: gets the plan, runs each
query over the connectors, shapes the results, renders the push. You read it in
the terminal. Nothing is sent anywhere.

That is the whole interface. `SKILL.md` maps the other phrasings ("wrap up",
"week ahead").

## What actually works today

| Ask | Status |
|---|---|
| "run my morning" | **works.** Run against real data repeatedly; the notes-gap section matches hand-checked ground truth. |
| "week ahead" | **works.** Run against a real week (68 events); flags clashes and reads the plan of record. |
| "wrap up" | **runs**, including the Friday planning-outcome section, which never rendered before [#95](https://github.com/unnitin/daydag/issues/95). Exercised against real payloads rather than a live Friday. |
| "prep me for X" | **works.** Names a meeting or a person, searches 7 days, and asks which one when the name matches several. |
| "ingest", "chase", "what shipped" | **run**, newly wired in [#95](https://github.com/unnitin/daydag/issues/95). `chase` reads `State.md`; `ship` needs a Pulse built from the mirrors. |

All seven loops the skill advertises are now reachable. Two — morning and
week-ahead — have been checked against real data end to end; the rest have run,
which is not the same thing.

## One-time setup

```sh
uv venv && uv pip install -e ".[dev]"
cp .env.example .env        # then fill in the real ids
```

`.env` is gitignored and holds every real identifier. The repo is public;
nothing real belongs in it. `pytest tests/test_config.py -k every_reference` checks that every `${VAR}`
the repo references actually resolves against your `.env`.

You also need the connectors reachable from Claude Code — Slack, Gmail,
Calendar, Notion. The vault and `gh` are direct.

## Memory: pass `--log`, or it forgets

The differentiator — *"these meetings produced no notes"* — only works across
days if runs share an event log. Without `--log` each run starts blank, reports
nothing, and **says nothing about it**, because an empty section is omitted
rather than labelled.

```sh
--log ~/.local/state/daydag/events.db
```

Outside the vault, deliberately: the vault is plaintext on every synced device,
and iCloud corrupts a SQLite WAL touched from two machines. Anything sensitive
goes to the log, never the vault.

`--write-state` additionally writes the chase list and notes-gaps back to
`DayDAG/State.md` in the vault. Hand-edit it freely — it is an input,
re-read before every loop, and your edits win over derived state.

## Running the halves by hand

Only needed for debugging or a backfill:

```sh
python -m daydag.run plan morning                    # queries, as JSON
echo "$PAYLOADS" | python -m daydag.run render morning --log ~/.local/state/daydag/events.db
```

`render` reads the payloads from stdin as one JSON object keyed by source. Two
encodings bite, and both fail quietly in the direction of saying *less*:

- **`vault: null`** means the weekly note does not exist. `""` means it exists
  and is empty, and renders nothing at all. Omitting the key means you could
  not reach the vault.
- **`notes_attached`** on a calendar record is the notes-gap signal. Set it
  from `usp=meet_tnfm_calendar` in an attachment's `fileUrl` — not the
  attachment title, which is localized, and not "has an attachment", which
  catches series-level recordings.

`SKILL.md` carries the full shaping contract. Get it wrong and the loop
degrades rather than lying, but it degrades silently.

## Prepping a meeting you name

```sh
python -m daydag.run plan  prep --for "finance x data"
echo "$PAYLOADS" | python -m daydag.run render prep --for "finance x data"
```

Match a title phrase, or a person as they appear on the invite. Searches the
next **7 days** - a bi-weekly 1:1 that last ran on Monday is not found until its
next instance comes inside the window.

**It asks rather than guessing when a name matches more than one meeting**, and
that is the normal case rather than the edge:

```
prep: "ruwen" matches 4 meetings - which one?
  Wed 16 Sep 11:30  D&T Program Review
  Thu 17 Sep 13:00  Ruwen / Nitin 1-1
  Thu 17 Sep 14:00  Pod Steering (AIM)
  Fri 18 Sep 10:00  CMG Label Partner's Summit
```

Narrow it with words from the title - `"ruwen / nitin"` - rather than hoping it
picks right. Prep for the wrong meeting is worse than none: you read it, trust
it, and walk into the other one cold.

Naming a meeting skips the "is this worth interrupting you" gate, so a standup
you ask about is a standup you get prepped.
## The soak (running now)

The morning brief is inside its five-day gate - BUILD's M2-5 - which was set
before seven loops got built on top of it and never actually run. Five working
days, every requested edit logged, passing when the count trends down and
nothing is still being asked twice.

Each morning, after reading the brief:

```sh
LOG=~/.local/state/daydag/events.db
python -m daydag.soak shipped --log $LOG
python -m daydag.soak note "put the clashes above the meeting list" --log $LOG
python -m daydag.soak report  --log $LOG
```

```
soak: 2/5 days - 3 more working day(s) to run
  2026-09-14  1 edit
  2026-09-15  1 edit
  ⚠ asked twice, fold it into the skill: drop the emoji from the overnight lines
```

Say it the way you said it - the journal spots a repeat by matching the text,
and a tidied paraphrase reads as a new request. A repeat is the useful signal,
not the count: SPEC §8 folds anything asked twice into the skill. Once you have,
`soak folded "<the edit>"`, or the gate stays shut.

The journal lives in the same event log as everything else, marked private, so
it never reaches the vault - an edit quotes the brief, and the brief carries
meeting titles and names.
## People: what it knows about who

Two config values used to carry everything the system knew about people, and
both were unset - so `has_external` and `has_leadership` always said no, and
meetings with outside parties got no prep at all. Now there is a directory:

```sh
LOG=~/.local/state/daydag/events.db
python -m daydag.people add vp-data --log $LOG --email a@b.com --slack-id U... \
    --name "..." --title "VP Data Engineering" --dm D... --group C... --leadership
python -m daydag.people show vp-data --log $LOG
python -m daydag.people list         --log $LOG
```

It **learns from meetings that have happened**: the morning, wrap and prep loops
add the people in each seeded meeting once it is over, count how often you meet
them, and remember when. It never invents a title from a meeting. **What you
state outranks what it infers** - correct an entry and no later run un-learns
it, and a correction made after someone was already observed folds the observed
entry into the role you named.

Today `prep` reads leadership from here (unioned with `PREP_LEADERSHIP`); the
morning brief and week-ahead still read the `.env` list until #119.

The Slack roster and the per-person DM/group ids that used to be `.env` keys
live here now. `.env` keeps only the principal's id, because that has to be
known before any lookup can run. Everything in the directory stays in the event
log - outside the vault, outside this public repo.

## What it will not do

- **Drafts, not sends.** Autonomous output goes to one place: the principal's
  own Slack DM. Anything addressed to anyone else is a draft that waits for an explicit
  go, per message.
- **Jira is read-only by construction** — the token carries `read:jira-work`,
  so there is no transition, comment or assignment path at all.
- **Vault writes are additive or proposed diffs.** Never a wholesale rewrite.
- **Nothing runs on a schedule.** Every run today is one you asked for.

## Honest status

The foundations are solid and heavily tested. The wiring is where the bugs are:
every defect found by actually running the loops was a case of a module being
carefully right while the runner collapsed it — the plan ignoring its own loop
([#94](https://github.com/unnitin/daydag/issues/94)), the overnight window
unbounded above ([#96](https://github.com/unnitin/daydag/issues/96)), a
three-state vault contract the runner could only express two of
([#97](https://github.com/unnitin/daydag/issues/97)). None were visible to
~1000 passing tests.

Treat output as a draft to check, not a report to trust, until a loop has run
against live data on days you can verify yourself.
