# DayDAG

A persistent agent that watches the day between the weekly rituals - builds a
morning brief, notices meetings that produced no notes, tracks open loops, reads
repo state, and delivers it all to one Slack DM.

It writes drafts, not sends. It surfaces discrepancies rather than resolving
them. And it owns almost nothing: five weekly routines already produce the
plan, the progress report and the pod update, so DayDAG reads their output
instead of re-deriving it.

## Where to start

| Document | What it answers |
|---|---|
| this file | how to run it, what works today, the modules, the gotchas |
| [SPEC.md](SPEC.md) | what the agent does - the loops, the sources, the guardrails |
| [ARCHITECTURE.md](ARCHITECTURE.md) | how the pieces fit, and **who owns each artifact** |
| [CONTRIBUTING.md](CONTRIBUTING.md) | tests first, review before pushing, how to merge |
| [docs/simplification.md](docs/simplification.md) | the 2026-09 consolidation: what was folded into what, and why |

Read ARCHITECTURE's ownership table before changing anything. One writer per
artifact is the load-bearing rule; the rest is plumbing. Tickets and milestones
live in the GitHub tracker, not in a document.

## The one thing to understand

**Python cannot call an MCP connector. The agent can.** So a loop runs in two
halves with the fetch in between:

```
plan(loop)              ->  the bounded queries        [python]
                            the agent runs them        [MCP]
render(loop, payloads)  ->  the push                   [python]
```

That is why there is no `daydag run` that does everything, and why a plain
`crontab` entry cannot work - it would produce a brief with every source empty
and four "couldn't check" lines. **The thing that runs a loop is an agent
session**, not a script.

## Day to day

Open Claude Code in this repo and say what you want:

> run my morning

The `daily-loops` skill does the three steps: gets the plan, runs each query
over the connectors, shapes the results, renders the push. You read it in the
terminal. Nothing is sent anywhere. `SKILL.md` maps the other phrasings ("wrap
up", "week ahead", "chase", "what shipped").

## What actually works today

| Ask | Status |
|---|---|
| "run my morning" | **works.** Run against real data repeatedly; the notes-gap section matches hand-checked ground truth. |
| "week ahead" | **works.** Run against a real week (68 events); flags clashes, reads the plan of record, pre-builds Monday's prep queue. |
| "wrap up" | **runs**, including the Friday planning-outcome section. Exercised against real payloads rather than a live Friday. |
| "prep me for X" | **works.** Names a meeting or a person, searches 7 days, and asks which one when the name matches several. |
| "chase" | **works.** Reads the chase list, the owed-by-you list and the pending decisions out of `State.md`, and reads the reply under every ask before calling one open. |
| "ingest", "what shipped" | **run.** `ship` renders from the mirrors with `render ship --mirrors --log <db>`, which also gives the morning, wrap and week-ahead their shipping section. |

Two loops have been checked against real data end to end; the rest have run,
which is not the same thing. "Runs" is not "verified", and the gap between
them is where every defect found so far has lived.

**Not built:** the chaser's clocks and nudge drafts, write-back into the weekly
note and Workstreams (#16), the ingestion digest (#17), most on-demand
commands (#22), the API halves of the pulse - Jira board deltas and PR
review/CI state, retired at tag `pre-simplification` until #145 - and
scheduling (#25). Nothing runs on a schedule; every run is one you asked for.

**The soak** (#13): the morning brief is inside a five-day gate - every edit
asked for is journalled, and the gate opens when the count trends down and
nothing is still asked twice. `python -m daydag.soak report --log <db>`.

## One-time setup

```sh
uv venv && uv pip install -e ".[dev]"
uv run pre-commit install          # installs the pre-commit AND pre-push hooks
cp .env.example .env               # then fill in the real ids
bash scripts/preflight.sh          # everything CI runs, in a throwaway venv
```

`.env` is gitignored and holds every real identifier. The repo is public;
nothing real belongs in it. `pytest tests/test_config.py -k every_reference`
checks that every `${VAR}` the repo references resolves against your `.env`.
You also need the connectors reachable from Claude Code - Slack, Gmail,
Calendar, Notion. The vault and `gh` are direct.

## Memory: pass `--log`, or it forgets

The differentiator - *"these meetings produced no notes"* - only works across
days if runs share an event log. Without `--log` each run starts blank, reports
nothing, and **says nothing about it**, because an empty section is omitted
rather than labelled.

```sh
--log ~/.local/state/daydag/events.db
```

Outside the vault, deliberately: the vault is plaintext on every synced device,
and iCloud corrupts a SQLite WAL touched from two machines. Anything sensitive
goes to the log, never the vault.

`--write-state` additionally appends what the run learned - chase items,
notes gaps, and the run's own log line - to `DayDAG/State.md` in the vault,
atomically. Hand-edit that file freely: it is an input, re-read before every
loop, and your edits win over derived state.

## Running the halves by hand

```sh
python -m daydag.run plan morning                    # queries, as JSON
echo "$PAYLOADS" | python -m daydag.run render morning --log ~/.local/state/daydag/events.db
python -m daydag.run render ship --mirrors --log ~/.local/state/daydag/events.db < /dev/null
```

`render` reads the payloads from stdin as one JSON object keyed by source. Two
encodings bite, and both fail quietly in the direction of saying *less*:

- **`vault: null`** means the weekly note does not exist. `""` means it exists
  and is empty, and renders nothing at all. Omitting the key means you could
  not reach the vault.
- **`notes_attached`** on a calendar record is the notes-gap signal. Set it
  from `usp=meet_tnfm_calendar` in an attachment's `fileUrl` - not the
  attachment title, which is localized, and not "has an attachment", which
  catches series-level recordings.

`SKILL.md` carries the full shaping contract. Get it wrong and the loop
degrades rather than lying, but it degrades silently.

**Prepping a meeting you name** - `plan prep --for "finance x data"`, then
`render prep --for ...`. A title phrase, or a person as they appear on the
invite, over the next 7 days. When the name matches several meetings it asks
which one rather than guessing: prep for the wrong meeting is worse than none.

**People** - `python -m daydag.people show vp-data --log <db>` (`add`, `set`,
`list`). The directory learns from meetings that have happened and never
invents a title; what you state outranks what it infers. Slack ids, addresses
and DM channels live there, in the event log, never in this repo.

All four commands share one parser: `python -m daydag <run|people|soak|registry>`.

## The modules

| Module | What it is |
|---|---|
| `config` | Identifiers resolved from a local `.env`. This repo is public; nothing real is committed |
| `registry` | The ownership table as startup checks, read off every `.claude/skills/*/SKILL.md` - two writers to one artifact is an error, not a convention |
| `statedoc` | The `DayDAG/` vault folder: State.md as a document, the sensitivity gate, the one State.md reader every push shares |
| `eventlog` | The SQLite event log outside the vault: every transition, the meeting ledger, the sensitive partition |
| `vault` | How bytes reach the vault safely: atomic writes, placeholder refusal, an iCloud-aware compare-and-swap for line edits |
| `ledger` | Calendar-driven meeting rows, so a meeting that produced **no** notes is visible; one verdict on what counts as a meeting, and the one attendee and name-token parse |
| `people` | Who someone is - ids, title, DM, groups, how often met - with stated facts outranking inferred ones |
| `pulse` | Repo state from local git mirrors, read on from a stored cursor |
| `closure` | Open is a verdict, not a default: the reply under every ask is read before the ask is reported open |
| `recipes` | The connector edge: SPEC §4's prose as literal, bounded queries, and the readers for what comes back |
| `voice` | The house voice as assertions, and the templates for every push |
| `push` | The push kernel every loop renders with: one `Push`, one `Reader`, one citation rule, one weekly-note scanner, one overlap rule |
| `brief` | SPEC §3.1's morning brief, assembled from the kernel |
| `eod_wrap` | SPEC §3.5's EOD wrap: what closed, what moved, tomorrow's first meeting |
| `week_ahead` | SPEC §3.6's Sunday week-ahead: a read of Friday's plan, plus Monday's prep queue |
| `prep` | Meeting prep pings: the one interrupt, 30 min out, qualified by the ledger - and naming the meeting to prep for without guessing between two |
| `ingestion` | SPEC §3.3's classifier: five labels, biased to precision, no write path |
| `observe` | What ran and what it reached: the pre-flight over injected probes, and one row per run in the event log |
| `delivery` | The shape of the one autonomous send - the principal's DM - and the threaded prep reply. Not yet wired to a transport: the skill posts by hand until the loops are scheduled (#25) |
| `soak` | The five-day gate's journal: every edit he asks for, and whether a repeat is still outstanding |
| `run` | The two-phase contract that makes a loop runnable: plan the fetch, assemble the push |
| `cli` | The one command line behind `daydag.run`, `people`, `soak` and `registry` |

## What it will not do

- **Drafts, not sends.** Autonomous output goes to one place: the principal's
  own Slack DM. Anything addressed to anyone else is a draft that waits for an
  explicit go, per message.
- **Jira is read-only by construction** - the token carries `read:jira-work`,
  so there is no transition, comment or assignment path at all.
- **Vault writes are additive or proposed diffs.** Never a wholesale rewrite.
- **Nothing runs on a schedule.** Every run today is one you asked for.

## Working on it

Two rules worth knowing before your first PR:

**Tests come first.** Write the failing test, then the code, and land both in
one PR - a red branch cannot be pushed.

**Review happens locally.** Run `/code-review` in your session before opening a
PR. It has found a real defect on essentially every run: dead code the docs
claimed was wired in, tests that assert nothing, a guardrail that failed open.
An LLM reviewer in CI was tried and removed; CONTRIBUTING records why.

## Two things that shaped this repo

**It is public, and it is about a real workplace.** Every identifier lives in a
gitignored `.env` and is referenced as `${VAR}`; people are role tokens
(`VP-Data`, `VP-AI`). `scripts/scan_secrets.py` blocks a real one from being
committed, and it runs on every commit and every push. It catches identifiers -
it does not catch a name, which is the author's judgement.

**The defects here are seam defects.** Nearly every real bug found so far passed
its own unit tests and was wrong about the world or about a neighbour: the
ledger silently dropped 61% of meetings, the state writer filtered one list and
not its twin, a parser the docs described was never called. That is why the
`tests/test_end_to_end_*.py` suites and `tests/golden/` exist - one walk per
path across the modules, and every push held byte-for-byte - and why guardrails
are marked and gated separately. Treat output as a draft to check, not a report
to trust, until a loop has run against live data on days you can verify.
