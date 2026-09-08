# DayDAG

A persistent agent that watches the day between the weekly rituals — builds a
morning brief, notices meetings that produced no notes, tracks open loops, reads
repo and board state, and delivers it all to one Slack DM.

It writes drafts, not sends. It surfaces discrepancies rather than resolving
them. And it owns almost nothing: five weekly routines already produce the
plan, the progress report and the pod update, so DayDAG reads their output
instead of re-deriving it.

**Status: in development.** The foundations are built and tested; the loops that
consume them are not wired up yet. Nothing runs on a schedule.

## Where to start

| Document | What it answers |
|---|---|
| [SPEC.md](SPEC.md) | What the agent does — the loops, the sources, the guardrails |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the pieces fit, and **who owns each artifact** |
| [BUILD.md](BUILD.md) | What gets built in what order, and why that order |
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
| `pulse` | Repo and board state: local mirrors for history, API for review state |
| `vault` | Targeted line edits to a note, with an iCloud-aware compare-and-swap |
| `voice` | The house voice as assertions, and the templates for every push |
| `smoke` | One pre-flight pass over every source: reached, skipped, or never connected |
| `runlog` | One row per run in the event log, so a brief that never arrived can still be explained |
| `recipes` | SPEC §4's prose as literal, testable queries |
| `payloads` | Reading a connector's answer while it is still unvalidated - `recipes` asks, this reads |
| `brief` | SPEC §3.1's morning brief: the assembler every other loop composes from |

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
`tests/test_end_to_end.py` exists and why guardrails are marked and gated
separately.
