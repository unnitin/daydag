# DayDAG

A persistent agent that watches the day between the weekly rituals — builds a
morning brief, notices meetings that produced no notes, tracks open loops, reads
repo and board state, and delivers it all to one Slack DM.

It writes drafts, not sends. It surfaces discrepancies rather than resolving
them. And it owns almost nothing: five weekly routines already produce the
plan, the progress report and the pod update, so DayDAG reads their output
instead of re-deriving it.

**Status: in development.** All eight loops the skill advertises now run. Two of
them - the morning brief and the Sunday week-ahead - have been checked end to
end against live connectors on days verified by hand; the other six run but
have not, and that gap is where every defect found so far has lived. The
morning brief's five-day soak (#13) passed on paper only: nearly every brief it
counted was rewritten by hand, because the render overflows a Slack message
(#185), so it re-runs once the render is what ships. Nothing runs on a
schedule. See [USAGE.md](USAGE.md) for the per-loop split and how to run one.

Only the morning brief runs on a schedule (USAGE.md, "The scheduled morning");
the other loops' schedule is #25. Not built yet, each an issue: the chaser's clocks and nudge
drafts (#18), write-back to the vault (#16), the ingestion digest (#17), most
on-demand commands (#22), the section 8 measurements (#27), and the pulse's PR
review and CI half (#145).

## Where to start

| Document | What it answers |
|---|---|
| [USAGE.md](USAGE.md) | **How to actually run it**, what works today, and the gotchas |
| [SPEC.md](SPEC.md) | What the agent does — the loops, the sources, the guardrails |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the pieces fit, and **who owns each artifact** |
| [Issues](https://github.com/unnitin/daydag/issues) | What is planned: the milestones (M2 to M5) and the open decisions (D-1 to D-4), one issue each |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Tests first, review before pushing, how to merge |

Read ARCHITECTURE's ownership table before changing anything. One writer per
artifact is the load-bearing rule; the rest is plumbing.

## The modules

| Module | What it is |
|---|---|
| `config` | Identifiers resolved from a local `.env`. This repo is public; nothing real is committed |
| `registry` | The ownership table as startup checks — two writers to one artifact is an error, not a convention |
| `manifests` | Reads the `daydag:` block out of every `.claude/skills/*/SKILL.md`, so those checks run over what ships |
| `state` | The `DayDAG/` vault folder and the SQLite event log. Two stores, opposite requirements |
| `ledger` | Calendar-driven meeting rows, so a meeting that produced **no** notes is visible |
| `pulse` | Repo state: local mirrors for history, API for review state |
| `board` | The watchlist's `jira` and `projects` blocks, and ticket keys in a line; the join and deltas are retired at tag `pre-retirement` until a loop needs them |
| `closure` | Open is a verdict, not a default: the reply under every ask is read before the ask is reported open |
| `vault` | Targeted line edits to a note, with an iCloud-aware compare-and-swap |
| `voice` | The house voice as assertions, and the templates for every push |
| `smoke` | One pre-flight pass over every source: reached, skipped, or never connected |
| `runlog` | One row per run in the event log, so a brief that never arrived can still be explained |
| `recipes` | SPEC §4's prose as literal, testable queries |
| `payloads` | Reading a connector's answer while it is still unvalidated - `recipes` asks, this reads |
| `brief` | SPEC §3.1's morning brief: the assembler every other loop composes from |
| `budget` | The morning push fitted to one Slack message: ranked, capped, every cut counted where he can find it, overflow in one threaded reply (#185) |
| `eod_wrap` | SPEC §3.5's EOD wrap: what closed, what moved, tomorrow's first meeting |
| `prep` | Meeting prep pings: the one interrupt, 30 min out, qualified by the ledger |
| `week_ahead` | SPEC §3.6's Sunday week-ahead: a read of Friday's plan, assembled into one push |
| `delivery` | The one autonomous send - the principal's DM - and the threaded prep reply |
| `prep_selector` | Naming the meeting to prep for, and refusing to guess between two |
| `prep_ahead` | Day-before prep for the calls his `## Prep rules` name - rows he edits, not code |
| `people` | Who someone is - ids, title, DM, groups, how often met - with stated facts outranking inferred ones |
| `run` | The two-phase contract that makes a loop runnable: plan the fetch, assemble the push |
| `soak` | The five-day gate's journal: every edit he asks for, and whether a repeat is still outstanding |
| `ingestion` | SPEC §3.3's classifier: five labels, biased to precision, no write path |
| `movement` | What the live sources say moved, proposed for confirmation - never a closure, no write path |
| `call_notes` | The ingest sweep (idempotent on the gmail message id) and the EOD calls section: attendance per note, provenance on every line, ranked priorities; actionable items recorded for the `State.md` chase list (#180) |
| `style` | What his feedback taught, as rows he edits in `DayDAG/Style.md`: scope by section, lifetime by `holds`, the checkable ones enforced at build |
| `team_practices` | The team's "How we work" metrics: ticket coverage today, reviews and ownership next. No author, anywhere |
| `expenses` | Receipts into Rippling Spend: sources in (Uber parsed against real mail, the rest by a generic rule), one forward to the receipts inbox after his go, labelled and logged so nothing is offered twice; Amex reconciles, never files |

## Working on it

```sh
uv venv && uv pip install -e ".[dev]"
uv run pre-commit install          # installs the pre-commit AND pre-push hooks
bash scripts/preflight.sh          # everything CI runs, in a throwaway venv
python3 scripts/build_docs.py      # check links, regenerate docs/api.md
```

Two rules worth knowing before your first PR:

**Tests come first.** Write the failing test, then the code, and land both in
one PR — a red branch cannot be pushed.

**Review happens locally.** Run `/code-review` in your session before opening a
PR. It has found a real defect on essentially every run: dead code the docs
claimed was wired in, tests that assert nothing, a guardrail that failed open.
An LLM reviewer in CI was tried and removed; CONTRIBUTING records why.

## Two things that shaped this repo

**It is public, and it is about a real workplace.** Every identifier lives in a
gitignored `.env` and is referenced as `${VAR}`; people are role tokens
(`VP-Data`, `VP-AI`). `scripts/scan_secrets.py` blocks a real one from being
committed, and it runs on every commit and every push. It catches identifiers —
it does not catch a name, which is the author's judgement.

**The defects here are seam defects.** Nearly every real bug found so far passed
its own unit tests and was wrong about the world or about a neighbour: the
ledger silently dropped 61% of meetings, the state writer filtered one list and
not its twin, a parser the docs described was never called. That is why
the `tests/test_end_to_end_*.py` suites exist - one per walk across the
modules, named for the path each takes - and why guardrails are marked and
gated separately.
