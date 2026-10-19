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

### Expenses

Each forwarded receipt becomes a draft reimbursement in Rippling Spend, with
vendor, amount, date and category filled by Rippling; you add the memo and
the report and press Submit. Say what you want:

> file my receipts for the LA trip          (or: what's not in rippling yet)

The `expenses` skill first builds the trip from your calendar (flights, the
hotel, the dinner that explains a ride), then plans the Gmail searches, runs
them, drops Uber's charge summaries and tip duplicates, places each receipt
against the trip, and prints a numbered list with a permalink per row. Only
what the trip places inside it goes with `go all`; the rest waits for a
number. Nothing is sent until you answer with a go:

> go 1            (the first time: one receipt, then check Spend > Receipts)
> go all          (the rest, once the first one has landed)

Then:

> did they land                                (Rippling's replies, with the draft links)
> report by day                                (the expense report, day by day)
> reconcile my amex against the LA trip        (needs the activity CSV)

For Amex, download the activity export first - Statements & Activity >
Download > CSV - and drop it anywhere outside the repo; give the path. It is
used to reconcile only: a statement line is never forwarded, Rippling needs
the receipt. Amex, DoorDash and the like send nothing to the work mailbox, so
a receipt that lives in another account has to reach Gmail first (a filter
that forwards it to work is the simplest route).

The forward is a connector write (`mcp__claude_ai_Gmail__forward`); Claude
Code asks for permission on it unless you allow it once in
`.claude/settings.local.json`. The `expensed` label cannot be applied from
here (the Gmail connector has no label-write scope), so the event log is the
"already filed" guard. The reasoning and what was verified about Rippling
live in `reference/rippling-expenses.md`.

## What actually works today

| Ask | Status |
|---|---|
| "run my morning" | **runs, but is rewritten before sending.** Run against real data repeatedly and the notes-gap section matches hand-checked ground truth, but the render is 23,000 to 59,000 characters against Slack's 5,000 per message, so every brief since 21 Sep was rewritten by hand (#185). |
| "week ahead" | **works.** Run against a real week (68 events); flags clashes and reads the plan of record. |
| "wrap up" | **runs**, including the Friday planning-outcome section, which never rendered before [#95](https://github.com/unnitin/daydag/issues/95). Exercised against real payloads rather than a live Friday. |
| "prep me for X" | **works.** Names a meeting or a person, searches 7 days, and asks which one when the name matches several. |
| "prep tomorrow's calls" / `prep-ahead` | **runs**, new in [#171](https://github.com/unnitin/daydag/issues/171). Evening, with the wrap: preps the next working day's calls that match `## Prep rules` in `DayDAG/Watchlist.md`. Checked against Monday 9/28's real calendar; not yet run end to end. |
| "ingest", "chase", "what shipped" | **run**, newly wired in [#95](https://github.com/unnitin/daydag/issues/95). `chase` reads `State.md`; `ship` needs a Pulse built from the mirrors: pass `--mirrors` (and `--log`, so the cursors persist). |
| "file my receipts" | **runs**, new 2026-10-15 (`expenses` skill). Uber parsed against his real mail; the forward to Rippling's receipts inbox is unverified until the first one is seen landing, so the first run sends one and checks. |

All eight loops the skill advertises are now reachable. Two — morning and
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
re-read before every loop, and your edits win over derived state. The write
is atomic and a compare-and-swap: an edit you make while a run is assembling
wins and the run says it could not write, and an iCloud-evicted `State.md` is
refused rather than replaced.

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
  not reach the vault. `vault` is this week's note only; any other note the
  plan names (next week's, for the week-ahead and the Friday wrap) goes under
  `vault_notes` keyed by path, `null` for one that does not exist.
- **`notes_attached`** on a calendar record is the notes-gap signal. Set it
  from `usp=meet_tnfm_calendar` in an attachment's `fileUrl` — not the
  attachment title, which is localized, and not "has an attachment", which
  catches series-level recordings.

`SKILL.md` carries the full shaping contract. Get it wrong and the loop
degrades rather than lying, but it degrades silently.

## The morning fits one message

`render morning` prints a DM of at most 4,500 characters (the pulse's own
"could not fetch" notices are never cut, so a long watchlist of dead mirrors
could pass it) and, on a heavy day, a
line `--- thread ---` followed by one reply holding the overnight, shipping and
gave-up rows the DM cut (#185). Post the two parts as they are - the skill says
how. What was cut, and where it lives, is one line in the event log
(`morning_fit`), also printed to stderr when stderr is a terminal. In a
scheduled run the fit prints nothing to stderr, so the summary cannot end up in
the post. (A pulse that fails to build under `--mirrors` still reports only on
stderr, with no line in the DM - a gap older than this budget.) The summary
reads:

```
morning push: 2,707 chars; cut owed 112 -> DayDAG/State.md; watch 41 -> DayDAG/State.md; ...
```

To see what a "+N more" line hides, render the same payloads unfitted in the
terminal. It writes nothing a later brief reads - no run-log row, no remembered
meetings, no State.md write, no cursors stored, no gave-up title marked as said
- so the next fitted run still names what it cut. Pass the same log, or the
remembered no-notes and gave-up rows are missing; shipping also needs
`--mirrors`, which still fetches:

```sh
python -m daydag.run render morning --payloads payloads.json \
    --log ~/.local/state/daydag/events.db --full
```

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

## Prepping tomorrow's important calls

Which calls, and how, is **rows you edit** in `DayDAG/Watchlist.md`, not code:

```markdown
## Prep rules

- title: Pod Steering · deck · day before
- attendee: cfo, sponsor · past-notes · day before
- attendee: ceo · past-notes+ideas · day before
```

`title:` words must all be in the meeting title; `attendee:` is a people
directory key, matched against required, non-declined invitees (an optional
invitee is not in the call). Recipes: `deck`, `past-notes`, `ideas`, joined
with `+`. Lead: `day before` or `N days before`, in working days, so Friday
evening preps Monday. Fields split on `·` only - titles carry `|`. A row that
does not parse is one warning line in the push; no section at all means the
seed rules above are used, and the push says so.

```sh
python -m daydag.run plan prep-ahead                                 # the calendar day
python -m daydag.run plan prep-ahead --calendar cal.json --log DB    # the reads per call
python -m daydag.run render prep-ahead --log DB < payloads.json
```

Payloads: `{"calendar": [...], "prep_ahead": {"<event id>": {"notes": [...],
"deck": [...], "slack": [...], "points": [...], "ideas": [...]}}}` - each read's
`put_under` names its key. `ideas` render only for a rule that asks for them,
labelled as suggestions; a deck that would not open is one line.
## The soak (passed on paper; re-runs after #185)

`soak report` reads "11/5 days - passed", and the arithmetic holds. What it
counted does not show the brief works, for three reasons (audit of 12 Oct):

- **The briefs it counted were not the render.** 9 of the 10 counted days were
  rewritten by hand, because the render overflows a Slack message (#185). The
  renderer's own output never drew a logged edit.
- **Logging stopped at the pass.** After 21 Sep he gave roughly 56 to 69 pieces
  of loop feedback in chat; 2 were logged. `soak shipped` is written by the
  agent, so a zero-edit day does not show the brief was read.
- **A repeat is matched by exact wording.** "Check the live source before
  reporting it open" came 14 to 16 times on 8 days and never registered as one.

So #13 stays open. The gate re-runs on the rendered brief once #185 lands:
log every reaction to a loop's output the day it is given, mark a day shipped
only when the render went out unedited, and fold a repeat when it is the same
idea, not only the same words. The commands are unchanged.

After reading a brief:

```sh
LOG=~/.local/state/daydag/events.db
python -m daydag.soak shipped --log $LOG
python -m daydag.soak note "put the clashes above the meeting list" --log $LOG
python -m daydag.soak report  --log $LOG
```

```
soak: 11/5 days - passed
  2026-09-15  13 edits
  2026-09-17  16 edits
  ...
  2026-10-07  0 edits
```

A repeat shows as `⚠ asked twice, fold it into the skill: <the edit>` under the days.

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
- **Only the morning runs on a schedule** (below). Every other run is one you
  asked for.

## The scheduled morning

The morning brief runs itself at 06:43 on weekdays: a launchd job on this Mac
starts one headless Claude Code run (`scripts/morning.sh`), which plans,
fetches over your claude.ai connectors, renders, and posts to your DM - the
message, and the thread reply when there is one. It can post only to your DM,
and logs the soak day as shipped only when the post is byte-for-byte what
render printed. Only the morning is scheduled (your call, 2026-10-12); the
other loops stay on demand.

```sh
scripts/install-morning.sh             # install or update the 06:43 weekday job
scripts/install-morning.sh --remove    # take it off
DAYDAG_POST=no scripts/morning.sh      # a dry run now: renders, posts nothing
```

Each run appends to `~/.local/state/daydag/runs/morning-<date>.log`, with the
payloads it fetched beside it. A Mac asleep at 06:43 runs it when it wakes.
It runs on this machine, not in the cloud, because it reads your vault and the
event log, which only this machine has.

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
