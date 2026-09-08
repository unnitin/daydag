# DayDAG — Build Plan

> Companion to [SPEC.md](SPEC.md). Scope: Phase 1 (skill) → Phase 2 (scheduled). CreateOS hosting is out of scope per §7.

## Shape of the build

Not a linear march through §3. The order below front-loads the two things that can invalidate the spec's design, then builds a walking skeleton (one working brief), then makes it self-updating, then fills in the remaining loops, then schedules.

**The two de-risking spikes, before anything else is built on top:**

1. **Where the agent runs, and therefore how it writes (M1-3 + M1-6).** The vault turns out to be on local disk and POSIX-writable (`~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Create Music Group/`), so a skill running on this Mac can edit the weekly note freely — the connector's append-only limit only binds if the agent runs somewhere else. That makes the execution-context decision a week-1 item rather than a deployment detail: local execution gives simple in-place write-back but needs the laptop awake at 6:45am; remote is dependable but pays for it in every vault write for the life of the project. Decide it before designing §3.3's write-back, because the two designs share almost nothing.
2. **Ingestion classifier precision (M3-1).** §3.3 is what keeps the agent's model of the world current without Nitin feeding it, and it is the only component whose failure is silent and compounding — a missed commitment doesn't announce itself. Build the eval set from ~20 past Gemini notes *first*, then the classifier against it.

Everything else is assembly.

## Milestones

### M0 — Prereqs (blocking, ~half a day, mostly not code)

| # | Ticket | Notes |
|---|---|---|
| M0-1 | Authorize Atlassian connector, verify Jira read | **Blocked on Nitin** — OAuth via claude.ai connector settings. Verify: project list, JQL, sprint state, issue history |
| M0-2 | Audit every other connector's read path | Calendar, Slack, Gmail, Obsidian, Notion, Granola, GitHub, Databricks. Confirm each returns what §4 assumes; record actual quirks |
| M0-3 | Connector smoke-test run | One invocation that hits all sources and reports reached/skipped. Doubles as the guardrail-6 degrade path and the run-log format |

### M1 — Foundations

| # | Ticket | Depends |
|---|---|---|
| M1-1 | Repo skeleton; commit SPEC.md; skill layout at `.claude/skills/daily-loops/` | — |
| M1-2 | `DayDAG/` vault folder — `README.md`, `State.md` schema hand-seeded w/ today's real open loops, `Decisions.md`, `Watchlist.md`, empty `Proposals/` | — |
| M1-3 | **Spike: vault write-back — local vs connector** | See above. Gates M3-3. Real risk is iCloud sync conflicts, not permissions |
| M1-4 | Source recipes reference — exact query per source | M0-2 |
| M1-5 | Voice + format fixtures: brief templates, 3 golden examples reviewed by Nitin | — |
| M1-6 | **Decision: execution context (local vs remote) + scheduling substrate** | Moved up from M5. Gates M3-3 |

`M1-4` is the boring one that saves the most time: calendar day-by-day, Slack `from:<@USER_ID>`, Gmail sender+body (not subject), vault paths with the `Create Music Group/` prefix, the saved JQL. These are all documented in §4 as prose and need to become literal queries.

### M2 — Walking skeleton: one working morning brief

| # | Ticket | Depends |
|---|---|---|
| M2-1 | §3.1 assembler: calendar + weekly-note 🔴 + overnight deltas + chase + watch | M1-2, M1-4 |
| M2-2 | Slack DM delivery + threaded prep replies | M2-1 |
| M2-3 | §3.7 engineering pulse, code side (merges, stuck PRs, CI) — invoke existing `morning-sync` | M1-2 |
| M2-4 | §3.7 board side: Jira deltas, sprint state, ticket-key join across Slack/PR/notes | M0-1, M2-3 |

**Gate: run it manually every morning for five days before building anything else.** Log every edit Nitin asks for — that count is the §8 "brief fitness" baseline, and format churn discovered here is free, whereas the same churn discovered after four more loops render into the same templates is not.

### M3 — Self-updating

| # | Ticket | Depends |
|---|---|---|
| M3-1 | Ingestion eval set: ~20 past Gemini/Granola notes, hand-labelled | — (start early) |
| M3-2 | §3.3 classifier: commitment / assigned ask / decision / new workstream / noise | M3-1, M1-3 |
| M3-3 | Write-back: weekly note, chase rows, Workstreams | M1-6, M1-3, M3-2 |
| M3-4 | Digest DM + correction handling ("anything wrong, tell me") | M3-2 |
| M3-5 | §3.4 chaser: clocks, evidence-of-movement from pulse, nudge drafts, snooze/re-raise/park | M2-4, M3-3 |

### M4 — Remaining loops

| # | Ticket | Depends |
|---|---|---|
| M4-1 | §3.5 EOD wrap | M2-1, M2-3 |
| M4-2 | §3.2 meeting prep pings (reuse weekly-planning File 2 recipe) | M1-4 |
| M4-3 | §3.6 Sunday week-ahead | M2-1, M2-3, M4-2 |
| M4-4 | §3.8 on-demand commands: sweep / prep / find / draft / status / ship / sprint / done / add / snooze | M3-3 |
| M4-5 | **Guardrail test checklist** — run before scheduling | all |

`M4-5` is not paperwork. Once M5 removes the human from the trigger, the guardrails are the only thing standing between a bug and a message sent as Nitin to someone else. Explicit cases: no autonomous send outside his own DM; vault writes additive or proposed; every claim carries quote + permalink; loops never auto-close on repo/Jira evidence; sensitive topics (personnel, comp, M&A) never reach a channel draft; someone else's Jira ticket never gets moved.

### M5 — Phase 2: scheduled

| # | Ticket | Depends |
|---|---|---|
| M5-2 | Schedule the five timed loops; pulse rides as a pre-step of three | M1-6, M4-5 |
| M5-3 | Run log to `DayDAG/State.md` + failure-honesty path | M0-3 |
| M5-4 | §8 measurement instrumentation + 4-week review | M5-2 |

### Open decisions — carried as tickets, not resolved up front

| # | Decision | Decide by |
|---|---|---|
| D-1 | Chase-clock defaults: global 2 business days vs per-owner (§9.2) | After 1 week of M3-5 |
| D-2 | Sunday week-ahead at 5:30pm vs Sunday morning (§9.3) | After 2 Sundays |
| D-3 | Pulse scope: workstream-linked repos/projects only, or everything (§9.4) | During M2-4 |
| D-4 | Two-way Slack: tolerate chat round-trip, minimal listener, or reopen platform (§9.5) | After 2 weeks of M5-2 |

Each of these is answered by data the build itself produces. Deciding them now would be guessing.

## Critical path

`M0-1 → M2-4 → M3-5` (Jira auth gates the pulse's board half, which gates the chaser's best evidence source) and `M1-6 → M1-3 → M3-3` (execution context gates the spike's scope, which gates all write-back). Everything else parallelises.

**First actions:** M0-1 and the `gh auth refresh -s project` grant together — ten minutes of clicking that unblocks the longest chain on the board. Then M1-6, then M1-3, then M1-2 seeded by hand. Not M2-1 yet, tempting as it is: it needs M1-2 and M1-4 underneath it.

## Rough sizing

M0 half a day · M1 1–2 days · M2 2 days + a 5-day soak · M3 3–4 days · M4 2 days · M5 1–2 days. Call it ~2 weeks of working time spread over 3 calendar weeks because of the soak gates.
