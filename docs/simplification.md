# Simplification plan - same capabilities, half the code

Written 2026-09-18 from a full read of `src/daydag/` (25 modules, 12,685 lines)
and `tests/` (38 files, 16,254 lines, 1,208 tests of which 291 are guardrails).
Every claim below that says "verified" was checked against the code by hand;
the rest comes from four module surveys and is cited by file and line so it
can be re-checked before each PR.

The plan is a stack of eight PRs. Each is green on its own, each carries its
own doc edits, and the stack can stop after any of them and leave the repo
better than it found it.

## 1. What the read found

**Half the source is prose.** 5,277 of 12,685 lines are code; 3,979 are
docstrings and 1,376 are comments (42%). Most of the prose narrates an
incident that a named test already pins - `run.py:448-467` retells the
wrong-day calendar story that `test_run.py:382,406` asserts; `state.py:19-56`
lists seven contracts that `test_state_append.py` and `test_state_store.py`
each cover. A smaller part holds calibration facts nothing else records
(the 18h/6h/10d note-arrival windows in `ledger.py:64-78`, the rejected
sensitivity tokens in `state.py:990-995`). The first kind can be a line and a
test name; the second belongs in `reference/`.

**A third of the modules have no production caller.** Verified by grep over
`src/` and `scripts/`:

| module | lines | reached by |
|---|---|---|
| `board.py` | 974 | its own tests. BUILD.md already says M2-4 "not built" |
| `vault.py` | 467 | its own tests. The M1-3 spike behind decision #24 |
| `delivery.py` | 355 | its own tests and `test_guardrails.py`. No message has ever gone through it - the skill has the agent post via MCP by hand |
| `evalset.py` | 207 | three vocabulary constants (`ingestion.py:84`); the rest is a test helper living in `src/` |
| `smoke.run()` | ~300 of 579 | nothing. `run.py` builds its smoke-shaped rows by hand and imports only the constants |
| `runlog` projection half | ~123 | nothing. SPEC §4/§7's "run log to State.md" was never wired; `update_state` has no run-log parameter |
| `state.DecisionQueue` | ~70 | nothing. `Decisions.md` is written by hand and read by `closure` |
| `week_ahead` Monday prep queue | ~140 | nothing. `run.py:905` renders the push and discards the object that carries it |
| `recipes.gh_*` builders | ~110 | nothing in `src/` |
| `ledger` backfill path, `prep.Schedule`/`due_at`, `people` CLI-only fields | ~150 | tests only |

`soak.py` looked dormant to the survey but is not: the journal holds entries
for 9/15-9/18 and the M2-5 gate is at day 4 of 5. It stays.

**Two things the CLI cannot do that the code can.** Verified: `run.main`
calls `render` with no `pulse` and no `state` (`run.py:1085-1097`), so
`python -m daydag.run render ship` always prints "couldn't check - no pulse
was built" and the shipping sections of the morning, wrap and week-ahead
never render on a real run (#138 describes the symptom). And `State.md` is
written with a plain `write_text` (`state.py:876`) while the atomic replace,
iCloud-placeholder refusal and digest check all sit unused in `vault.py`.

**The same thing is written several times.**

- `week_ahead.py:174-218` re-spells six of `brief`'s helpers (`_local`,
  `_clock`, `_instant`, `_link`, `_day_label`, `_claim`, `_line_from_state`).
- Three push classes with the same four fields and three error classes with
  the same meaning (`Brief`/`Wrap`/`WeekAhead`); the weekly-note read, the
  pulse block and the State.md read each appear three times (`brief.py:622,
  649, 666` · `eod_wrap.py:191, 212` · `week_ahead.py:438, 479, 542`).
- The calendar window is computed once in `run._calendar_windows` and then
  re-derived by every consumer to filter what it was handed (#109, #111).
- Two watchlist parsers over one file (`pulse.read_watchlist:542-586`,
  `board.read_board_watchlist:858-908`) and two copies of the monotonic
  evidence rule with an eight-line comment duplicated verbatim
  (`pulse.py:936-942` ≡ `board.py:795-801`).
- One observation row converted five times between `smoke.Result`,
  `runlog.Run.observe`, `Run.row`, `RunRow.payload` and `RunRow.from_payload`;
  two `_one_line` wrappers over one `voice.clipped`.
- Two State.md parsers in one file (`read_section` and `StateDoc`), three
  link strippers, two event-log decoders with divergent corruption policy,
  two meeting qualifiers that differ in two cases (#110), and attendees
  parsed once by the ledger and again by the people directory.
- `registry.Registry.load` and `manifests._validate_block` police the same
  six keys.

**The docs overlap and three claims are stale.** README, USAGE, BUILD,
ARCHITECTURE, SPEC and the skill each carry a module list, a "what works"
table or the guardrail list. README calls `delivery` "the one autonomous
send" (nothing calls it); SPEC §4 and §7 say the run log is written to
State.md (it is not); BUILD says the soak "has not run" while USAGE heads a
section "The soak (running now)".

## 2. What does not change

Every capability that works today keeps working, from the same command, with
the same output shape. The parity check in §4 is what proves it.

| capability | command | after the stack |
|---|---|---|
| morning brief | `run plan/render morning` | same |
| EOD wrap | `run plan/render eod` | same |
| Sunday week-ahead | `run plan/render week-ahead` | same, plus the Monday prep queue it computes and today discards (PR 3 renders it) |
| prep, named or next | `run plan/render prep [--for]` | same |
| ingest | `run plan/render ingest` | same |
| chase, with closure reads | `run plan/render chase` | same |
| ship | `run render ship` | **works from the CLI** once `--mirrors` exists (PR 3); today it always degrades |
| people directory | `people show/list/add` | same command, argparse |
| manifests check | `manifests` | same command (preflight depends on it) |
| soak journal | `soak shipped/note/folded/report` | same |
| State.md append-only, byte-for-byte round trip | `--write-state` | same, and the write becomes atomic |
| one autonomous channel, drafts never sends | structural | same tests, same subjects |

The house rules are untouched: one writer per artifact, evidence or silence,
surface don't resolve, sensitivity never reaches the vault, the event log
outside the vault, Jira read-only, bounded queries, id-scoped Slack search.

## 3. Target shape - 25 modules to 17

| after | from | holds |
|---|---|---|
| `config.py` | `config` | `.env` identities, `${VAR}` resolution, timezone, plus `path_from`/`host_from` so three env readers become one |
| `connectors.py` | `recipes` + `payloads` | what to ask each connector and how to read what comes back. `gh_*` builders gone; `GH_WRITE_VERBS` stays as the tripwire subject |
| `push.py` | `brief` primitives + `voice` | `Section`, `claim`, `render_push`, `Reader`, red items, overlap clusters, `unsourced_claims`, `clipped`, `WARN`, one `Push`, one `PushError` |
| `loops.py` | `brief` body + `eod_wrap` + `week_ahead` + `run._chase/_ingest/_prep` | one `Loop` descriptor (windows, needs_ledger, assemble) and the seven bodies sharing prologue helpers |
| `run.py` | `run` | plan, render, the payload adapter. No loop bodies |
| `prep.py` | `prep` + `prep_selector` | qualification, selection, the timed-ping window (`Schedule`, kept for #25), the ping |
| `closure.py` | `closure` | plan the reads, judge, render. Reads the vault through `StateDoc` |
| `statedoc.py` | `state` (folder half) | `StateDoc`/`Block`/`StateFolder`/`update_state`; one parser, `Block.body`/`link`/`struck`, `StateDoc.blocks_in`; writes through `vault` |
| `eventlog.py` | `state` (log half) | `EventLog`, one decoder, `record_fetch`/`last_fetch` |
| `vault.py` | `vault` | how bytes reach the vault safely: atomic write, placeholder refusal, compare-and-swap line edits - the one write path, used by `statedoc` now and by #16's write-back later |
| `meetings.py` | `ledger` + `people` | rows, one `qualifies(for_week=)`, notes gaps, the directory folded from the same attendee parse |
| `ingestion.py` | `ingestion` + `evalset` vocabularies | the classifier and its three vocabularies. `load_items`/`score` move to `tests/evalset.py` |
| `evidence.py` | `pulse` | mirrors, the join, one watchlist parser, one `apply_evidence`. `board` retired until M2-4 wires it |
| `observe.py` | `smoke` + `runlog` + `soak` | one `Row`, one renderer, the probe table, the run row, the soak journal |
| `registry.py` | `registry` + `manifests` | one validate step, `main` kept |
| `delivery.py` | `delivery` | `Transport`, `deliver_push`, `deliver_prep_ping`, `draft` - the shape the guardrails inspect, minus its run-log plumbing |
| `cli.py` | four `__main__` blocks | one argparse tree; `python -m daydag.run` etc. stay as three-line shims (#117, #126) |

Estimated after: **6,000-7,000 source lines** (code ~4,200, prose ~2,300),
tests **~11,000 lines / ~950 tests**, guardrails **~270**. The guardrails
that go are the ones whose subject goes (board's 29, vault's line-splicing
subset, the duplicated copies in `test_guardrails.py`); every other
guardrail keeps its test, re-pointed at the new module.

## 4. How parity is proven

1. **Tag first.** `pre-simplification` on `main` before PR 1 merges, so any
   retired module is one `git show` away.
2. **Golden renders.** PR 1 adds `tests/golden/<loop>.txt`: the rendered
   push for each of the seven loops over the existing fixture payloads
   (`tests/test_plan_feeds_render.py` already builds payloads from each
   plan's own keys). PRs 2-5 must reproduce them byte-for-byte, with a
   whitelist for the one deliberate change per PR (e.g. the ship section
   appearing once `--mirrors` exists). PR 7 deletes the goldens or keeps
   them, the user's call.
3. **Guardrail ledger.** Each PR's description lists guardrail count before
   and after and names every removed one with the deleted subject it
   covered. A guardrail whose subject survives is never removed.
4. **Tripwires re-pointed, not dropped.** `test_guardrails.py` scans
   `src/daydag/*.py` by name and pins `merges_since_cursor` as its
   self-check symbol; `test_ingestion.py:419-460` asserts the classifier
   imports neither the vault nor the store; `test_board.py:258-274` ASTs
   `board.py`; `test_docstring_examples.py` binds every `USING IT` block.
   Each rename updates the scanner in the same PR.
5. **Preflight unchanged.** `scripts/preflight.sh` keeps running the
   manifests check, the secrets scan, the docs check and the guardrail
   subset on every push.

## 5. The stack

Base: `fix/closure-check` (#142), so `closure` is in the tree the stack
reshapes. Branches are `chore/simplify-NN-<name>` - the `chore/` prefix is
what gives a stacked PR CI (CONTRIBUTING, "Branches").

| PR | branch | change | src lines | risk |
|---|---|---|---|---|
| 0 | `chore/simplify-00-plan` | this document | 0 | none |
| 1 | `chore/simplify-01-retire` | tag `pre-simplification`; delete `board.py`, the `gh_*` builders, the runlog projection half, `DecisionQueue`, the ledger backfill path, `prep.Schedule`/`due_at`, the Monday prep chain, `evalset`'s test-only half (to `tests/`), `people`'s unread fields; add the golden renders; fix the three stale doc claims | −2,300 (−1,500 tests) | low - deletions of unreached code, no behaviour change |
| 2 | `chore/simplify-02-state` | `state.py` → `statedoc.py` + `eventlog.py`; one State.md reader (`Block`), one log decoder; State.md written through `vault`'s atomic writer and read through its placeholder check; `closure` reads the vault through `StateDoc`; one `line_from_state` | −300 | medium - the round-trip and sensitivity guardrails are the safety net |
| 3 | `chore/simplify-03-loops` | `push.py` + `loops.py`; `run.py` keeps plan/render only; `prep` absorbs `prep_selector`; `main` gains `--mirrors` so `ship` works; window arithmetic lives once (#109, #111) | −1,000 | medium - `test_plan_feeds_render` parametrises over every loop |
| 4 | `chore/simplify-04-sources` | `meetings.py` (ledger + people, one qualifier, #110); `evidence.py` (pulse, one watchlist parser, one evidence rule); `connectors.py` (recipes + payloads, `run` adopts `records()`); `config.path_from`/`host_from` | −700 | medium - mirror tests run real `git clone --mirror`; keep them |
| 5 | `chore/simplify-05-observe` | `observe.py` (smoke + runlog + soak, one `Row`); `registry` absorbs `manifests`; `delivery` trimmed to the guardrail shape; `cli.py` argparse (#117, #126); the ~220 duplicated guardrail lines deleted, scanners re-pointed | −900 (−600 tests) | medium - the module-scoped "no function takes a Draft and a Transport" assertion must move with `draft` |
| 6 | `chore/simplify-06-prose` | docstring pass: a contract a test pins becomes one line and the test's name; calibration facts to `reference/meeting-ledger.md`, `reference/sensitivity.md`, `reference/state-incidents.md`; connector numbers already in `reference/connector-audit.md` become pointers; `USING IT` blocks kept | −1,600 prose | low - `test_docstring_examples` catches a broken example |
| 7 | `chore/simplify-07-docs` | README absorbs USAGE; SPEC absorbs BUILD's status table; ARCHITECTURE keeps ownership, invariants and seams; SKILL.md loses the documentation-about-DayDAG its own README says it should not carry; `build_docs.REQUIRED_DOCS` updated | −700 doc lines | low |

Order matters: 1 is pure deletion and the biggest single win, so it goes
first and alone; 2 before 3 because the loops read the state through the
new parser; 4 and 5 are independent of each other but both sit on 3's
`push`/`loops` seam; 6 and 7 are prose and come last so they describe the
shape that exists.

## 6. Decisions needed before PR 1

Each has a recommendation. Say the word and the PR follows it; say
otherwise and the PR follows that.

- **D1 `board.py`** - retire until M2-4 wires a `sprint` command
  (recommended: it is unreached, BUILD.md already lists it as not built, and
  `connectors.jira_search` keeps the bound). Alternative: merge into
  `evidence.py` now, −450 instead of −974.
- **D2 `vault.py`** - decided 2026-09-18: wire it. `vault` stays as the one
  write path (atomic write, placeholder refusal, compare-and-swap line edits);
  `statedoc` writes State.md through it in PR 2, and #16's write-back uses the
  line edits later.
- **D3 `delivery.py`** - keep, trimmed to the shape the guardrails inspect
  (recommended: it is the only executable statement of "one autonomous
  channel" and the AST tripwire alone is weaker). Alternative: delete with
  its 25 tests and 8 guardrail sections.
- **D4 Monday prep queue** - decided 2026-09-18: keep and render it. SPEC
  §3.6 rule 2 asks for it, the code works, and only the last line (discarding
  the object) was missing. PR 3 renders it as a section of the week-ahead push.
- **D5 `BUILD.md` and `USAGE.md`** - fold into README and SPEC (recommended).
  Alternative: keep both and only fix the stale claims.

## 7. What was verified by hand, and what was not

Verified in this session: the code/prose split; every "no caller" claim in
§1's table; `run.main` passes no pulse or state; the week-ahead object is
discarded; `smoke.run()` and the runlog projection have no caller;
`update_state` writes with `write_text` and `vault.py` holds the atomic
path; `docs/api.md` is gitignored; the soak journal is live.

From the surveys, not yet re-checked: the exact line ranges of the
duplications in §1 (the functions were confirmed, the ranges were not
re-read), the per-PR line estimates (they are estimates), and the claim
that `test_guardrails.py` duplicates ~220 lines of module tests. Each PR
re-checks what it touches.
