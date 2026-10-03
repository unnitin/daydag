# Simplification plan - same capabilities, less code

**Refreshed 2026-10-02 against `main` at `a1497ee`.** First written 2026-09-18,
built as a twelve-PR stack 09-18 to 09-20, parked 09-20, never merged. This
revision re-measures every number against today's `main`, re-checks every
"no caller" and every defect claim, and replaces the stack's plan with one that
can land. What the first stack did and measured is kept in §9, because it is the
best evidence for what this plan will do.

Every figure marked *measured* was produced on 2026-10-02 by the commands in §8
and reproduces the tag's published numbers exactly (§9), so the columns compare.

## 0. Why it was parked, and why that still holds

> "dont merge simplification, get the new functionality in first, use it then
> use the experience to cut / simplify" - decision, 2026-09-20

A plan derived from *reading* the code guesses at what is load-bearing; running
the new thing tells you. That held: since the park, twelve merges landed on `main`
(#142, #166, #172, #174, #176-#179, #181, `path/scheduled-loops` and two
docs branches), and the tree grew by four modules and 3,968 source lines. They are the experience the
decision asked for, and they change the plan in three ways:

1. **`board.py` is no longer unreached.** `movement` and the eod plan import two
   small things from it (§1), so D1 is re-opened.
2. **The new modules re-grew the duplication the stack removed.** `call_notes`,
   `movement` and `prep_ahead` each brought their own clock, link, token or
   section helpers, and the two markdown rule sets now each have a new user (§1).
   That is the strongest argument *for* the consolidation: it is what happens
   when the shared kernel does not exist yet.
3. **The stack cannot be rebased.** It folds `run.py` and `eod_wrap.py` into
   `loops.py`; `main` has since added 927 lines to `run.py` and 147 to
   `eod_wrap.py`. A rebase conflicts in 18 files and amounts to re-porting every
   feature merged since. §5 rebuilds instead, using the old branches as
   reference.

## 1. What the read found (measured on `main`, 2026-10-02)

| | tag (09-17) | `main` today |
|---|---|---|
| modules | 24 | 28 |
| source lines | 12,199 | 16,167 |
| of which code | 5,557 | 7,915 |
| of which docstrings + comments | 5,199 (43%) | 6,356 (39%) |
| test lines / files | 15,900 / 37 | 19,839 / 44 |
| tests / guardrails | - | 1,438 / 308 |

The biggest modules: `run.py` 1,761 · `state.py` 1,277 · `pulse.py` 997 ·
`recipes.py` 996 · `board.py` 974 · `call_notes.py` 884 · `brief.py` 812 ·
`movement.py` 793 · `prep_ahead.py` 736.

### Code with no production caller

Re-checked by import and name grep over `src/` and `scripts/`:

| what | lines | reached by, today |
|---|---|---|
| `board.py` | 974 | **two thin imports now.** `run._jira_steps` uses `read_board_watchlist` for the Jira project keys; `movement` uses `keys_in`, a one-line delegate to `Pulse._keys`. The remaining ~900 lines (the join, discrepancies, the projects-v2 note) are unreached |
| `vault.py` | 467 | its own tests. The atomic writer is still unused; `State.md` is written with `write_text` (`state.py:923`) |
| `delivery.py` | 355 | its own tests and the guardrails. Nothing in `src/` imports it |
| `smoke.run()` | ~300 of 579 | nothing. The constants and `Result` are used |
| `state.DecisionQueue` | ~70 | nothing |
| `week_ahead` Monday prep queue | ~140 | computed into `WeekAhead.monday_preps`, then discarded - `render` calls `.render()` and drops the object |
| `recipes.gh_open_prs`, `gh_pr_checks`, `gh_default_branch`, `gh_recent_runs` | ~60 | nothing. **Changed:** `gh_merged_on`, `gh_reviewed_on` and `gh_closed_issues_on` are now used by the evening's movement read and stay |
| `evalset.py` | 207 | three vocabularies for `ingestion`; the rest is a test helper in `src/` |

### Defects the stack fixed that are still on `main`

These are bugs, not tidiness, and §5 lands them first and separately.

- **The remembered-meeting `kind` collision (PR 11).** `run._remember` splats
  each calendar record into `EventLog.record(kind, ...)`. Reproduced today: a
  record with a `kind` key raises `TypeError: EventLog.record() got multiple
  values for argument 'kind'` from `render("morning"|"eod", log=...)`.
  `prep-ahead` is not affected (it remembers only today's meetings and fetches
  tomorrow). **Latent, not live:** the Google Calendar connector checked today
  omits `kind`, so it fires only for a source that passes it through - the raw
  Calendar API does, and the record the stack hit on 09-20 did.
- **#112, the week-ahead "no plan" check.** `_Payloads.weekly_note(path)`
  ignores `path` and returns the single `vault` payload, so the check for next
  week's note reads whatever note was handed in.
- **#138, `ship` from the CLI.** `run.main` never passes a pulse to `render`,
  so `python -m daydag.run render ship` always prints "couldn't check - no
  pulse was built".
- **`State.md` is written non-atomically** while `vault.py`'s atomic write and
  iCloud-placeholder refusal sit unused (D2 decided to wire them).

### The same thing, written several times

Carried over from 09-18 and still true, plus what the new modules added (marked
*new*):

- **Three Watchlist parsers** over one file: `pulse.read_watchlist`,
  `board.read_board_watchlist`, and *new* `recipes.watched_channels`.
- **Two markdown rule sets.** `pulse` and `state` each define `_HEADING` and
  `_BULLET`; `recipes._HEADING_LINE` is a third heading regex. `pulse`'s are
  applied to stripped lines, so its readers cannot tell a nested bullet from a
  top-level one; `state.read_section(top_level=True)` can. *New:* `prep_ahead`
  imports `pulse`'s set and `movement` imports `state`'s, so two modules
  written the same week read nesting differently.
- **Two `State.md` section walkers:** `state.read_section` and *new*
  `movement._blocks`, which re-implements the same section rule.
- **Time and day formatting, four ways, two visible formats.** `_clock` in
  `brief`, `week_ahead` (identical), `prep_selector` (tz-aware) and *new*
  `prep_ahead`; `_day_label` in `brief`, `week_ahead` and *new* `prep_ahead`.
  The morning shows `1:00` and `fri oct 2`; the day-before prep shows `1pm`
  and `fri 10/2`. That is a visible inconsistency in his DMs, not just code (D6).
- `_instant` ×3 (`brief`, `week_ahead`, *new* `call_notes`), `_link` ×3
  (`brief`, `week_ahead`, *new* `movement`), `_line_from_state` ×2.
- **Name tokens ×3:** `people` and `prep_selector` share one regex;
  *new* `call_notes._tokens` splits on whitespace - a different rule for the
  same question ("is this the same person").
- **Four push shapes and three error classes:** `Brief`/`BriefError`,
  `Wrap`/`WrapError`, `WeekAhead`/`WeekAheadError`, *new* `prep_ahead.DayBefore`.
  `call_notes` and `prep_ahead` already compose from `brief`'s `Section`,
  `claim` and `render_push` - the kernel exists, it is just named `brief`.
- The principal's id is shape-checked in three places (`brief`, `delivery`,
  `prep`); `_one_line` in `runlog` and `smoke`; two State.md parsers in
  `state.py` (`read_section` and `StateDoc`).

## 2. What does not change

Every capability that works today keeps working, from the same command, with the
same output shape. §4 is how that is proven.

| capability | command | after |
|---|---|---|
| morning brief | `run plan/render morning` | same |
| EOD wrap, incl. movement (#134) | `run plan/render eod` | same |
| Sunday week-ahead | `run plan/render week-ahead` | same, plus the Monday prep queue it computes today and discards (D4) |
| prep, named or next | `run plan/render prep [--for]` | same |
| day-before prep, scheduled | `run plan/render prep-ahead`, `scripts/scheduled_loop.sh prep-ahead` | same; the script's prompt is untouched |
| ingest, incl. call notes (#177, #181) | `run plan/render ingest` | same |
| chase, with closure reads | `run plan/render chase` | same |
| ship | `run render ship` | **works from the CLI** (#138); today it always degrades |
| people directory | `people show/list/add` | same |
| registry / manifests check | `manifests` | same command (preflight depends on it) |
| soak journal | `soak shipped/note/folded/report` | same |
| State.md append-only, byte-for-byte round trip | `--write-state` | same, and the write becomes atomic |
| one autonomous channel, drafts never sends | structural | same tests, same subjects |

House rules untouched: one writer per artifact, evidence or silence, surface
don't resolve, the event log outside the vault, Jira read-only, bounded queries,
id-scoped Slack search, sensitivity marks on every record (#180).

## 3. Target shape - 28 modules to about 21

The 09-20 shape (§9) still holds for the modules it covered. The three new
modules keep their own files - each is a pure detector or classifier over what
it is handed, which is the `ingestion` pattern - but compose from the shared
kernel instead of carrying copies.

| after | from | holds |
|---|---|---|
| `config.py` | `config` | `.env`, `${VAR}` resolution, timezone, `principal_id` (one shape check), `path_from` |
| `voice.py` | `voice` | the house voice **and the one clock/day formatter** (D6). Kept separate: folding it into `push` is an import cycle (§9) |
| `markdown.py` | the regexes in `pulse`, `state`, `recipes` + `movement._blocks` | one heading, one bullet, one section walk. Small, and the thing three modules currently disagree about |
| `recipes.py` | `recipes` + `payloads` | what to ask each connector and how to read the answer; the four dead `gh_*` builders gone |
| `push.py` | `brief` primitives | `Section`, `claim`, `render_push`, `Reader`, one `Push`, one `PushError`, `_instant`/`_link`/`line_from_state` once |
| `loops.py` | `brief` body + `eod_wrap` + `week_ahead` + the runner's loop bodies | one `Loop` descriptor; each loop fetches only what it reads |
| `run.py` | `run` | plan, render, the payload adapter (#112 fixed); `main --mirrors` (#138) |
| `prep.py` | `prep` + `prep_selector` | qualification, selection, the ping, `Schedule` (#25) |
| `prep_ahead.py` | `prep_ahead` | the day-before rules and match; renders through `push`, formats through `voice` |
| `call_notes.py` | `call_notes` | the notes join and sweep; name tokens from `ledger` |
| `movement.py` | `movement` | the detector; sections from `markdown`, keys from `pulse` |
| `closure.py` | `closure` | reads the vault through `StateDoc` |
| `statedoc.py` | `state` (document half) | `StateDoc`/`Block`/`StateFolder`/`update_state`, one parser, writes through `vault` |
| `eventlog.py` | `state` (log half) | `EventLog`, one decoder, the record stored whole (the `kind` fix) |
| `vault.py` | `vault` | the one write path: atomic write, placeholder refusal, CAS line edits |
| `ledger.py` / `people.py` | same | one `judge` behind both qualifiers (#110); one name-token rule |
| `pulse.py` | `pulse` + `board`'s two live functions | mirrors, the join, the **one** Watchlist parser (repos, projects, channels) |
| `observe.py` | `smoke` + `runlog` | one `Result` from probe to projection |
| `registry.py` | `registry` + `manifests` | one validate step |
| `delivery.py` | `delivery` | the guardrail shape (D3) |
| `ingestion.py`, `soak.py` | same | `evalset`'s test half moves to `tests/` |

**Expected size, estimated not counted:** the first stack took the tag from
12,199 to 10,620 source lines (−13%) without cutting reasoning prose
(CONTRIBUTING). The same moves on today's tree, plus folding the new
duplication, should land around **14,000 lines**. The number to watch is not
the total but that each fact - a bullet, a clock, a person's tokens, a watched
project - is defined once.

## 4. How parity is proven

1. **Tag.** `pre-simplification` exists at `e7e1336` (#135). Add
   `pre-simplification-2` on `main` before the rebuild's first merge.
2. **Golden renders** for every loop in §2, now including `prep-ahead` and
   `ship`, captured from today's `main` over the fixture payloads. Each PR must
   reproduce them byte-for-byte, with a named whitelist for its one deliberate
   change (the Monday queue appearing; `ship` rendering; D6's format change).
3. **Guardrail ledger.** Each PR states the guardrail count before and after
   (308 today) and names every removed one with the subject it covered.
4. **Tripwires re-pointed, not dropped** - the module-name scanners in
   `test_guardrails.py`, the classifier-imports-no-vault assertions, the
   `USING IT` docstring binding.
5. **Preflight unchanged.**

## 5. The rebuild

Each PR branches from `main`, is green alone, and merges before the next opens
- no stack this time. That is CONTRIBUTING's own lesson ("a merged stacked PR
has not reached `main`") and what made the first stack impossible to land once
`main` moved. Each row names the old branch to port from.

| # | change | reference branch | risk |
|---|---|---|---|
| 1 | **Fixes first, no reshaping:** the event log stores a record whole (`kind`); `_Payloads.weekly_note` honours `path` (#112); `main --mirrors` so `ship` renders (#138); `State.md` written through `vault`'s atomic writer | `fix/remembered-kind`, `simplify-09-loops`, `simplify-03-loops`, `simplify-02-state` | low - each is a defect with a failing test first |
| 2 | Goldens for every loop; tag `pre-simplification-2` | `simplify-01-retire` | none |
| 3 | Retire unreached code: `board.py` minus its two live functions (moved to `pulse`), four `gh_*` builders, `DecisionQueue`, `smoke.run`, `evalset`'s test half | `simplify-01-retire` | low |
| 4 | `markdown.py` + one Watchlist parser; `movement` and `prep_ahead` move onto it | new | medium - nesting must be pinned by a test for each reader (D7), not by whichever rule wins |
| 5 | `state` → `statedoc` + `eventlog` | `simplify-02-state`, `simplify-08-sweep` | medium |
| 6 | `push` kernel; `voice` owns the one clock/day format (D6); `call_notes` and `prep_ahead` compose from it | `simplify-03-loops` | medium |
| 7 | `loops.py` fold; the Monday queue renders (D4) | `simplify-09-loops` | medium - largest port, `run.py` changed most since |
| 8 | sources: one `judge` (#110), one name-token rule incl. `call_notes`, `payloads` into `recipes` | `simplify-04-sources` | medium |
| 9 | `observe`, `registry` absorbs `manifests`, `cli.py` | `simplify-05-observe` | medium |
| 10 | tests follow modules | `simplify-10-tests` | low |
| 11 | prose and docs pass | `simplify-06-prose`, `simplify-07-docs` | low |

PR 1 is worth landing whether or not the rest ever does.

The old `chore/simplify-*`, `integration/simplification-to-main` and
`fix/remembered-kind` branches stay until their PR here merges, then go.

## 6. Decisions

Settled 2026-09-18, still standing: **D2** wire `vault.py` as the one write
path · **D3** keep `delivery.py`, trimmed to the guardrail shape · **D4** render
the Monday prep queue · **D5** BUILD and USAGE fold into README.

Open - each changes what gets built. A recommendation for each:

- **D0 Resume now, or keep parking?** Recommended: land PR 1 now regardless;
  start PR 2 onward once the team-practices code (`path/team-practices*`) has
  either merged or been dropped, so the rebuild ports against a `main` that is
  not about to move under it again.
- **D1 `board.py`** (re-opened). Recommended: move `keys_in` (one line) and the
  Jira project list into `pulse`'s single Watchlist parser, retire the rest
  until M2-4 wires a `sprint` command. Alternative: keep `board.py` whole.
- **D6 one time format.** The morning says `1:00` / `fri oct 2`; the day-before
  prep says `1pm` / `fri 10/2`. Pick one for every push. No recommendation -
  it is his DM and a voice call, not a code one.
- **D7 nesting.** `pulse`-style readers flatten a nested bullet into a
  top-level one; `state` keeps the distinction. Recommended: the one rule set
  keeps nesting and each reader says whether it wants it - a sub-bullet under a
  watched repo or a prep rule is a note, not a second entry - pinned by a test
  in PR 4.

## 7. What was verified, and what was not

Verified on 2026-10-02 against `a1497ee`: every row of §1's tables by grep and
by reading the call site; the `kind` collision reproduced through
`run.render` for morning and eod, and shown not to affect prep-ahead; the
current Google Calendar connector's record shape (no `kind`); #112 and #138 by
reading `run.py`; the 18-file conflict set by `git merge-tree`; the
measurements by the script in §8, which reproduces the tag's 09-20 figures.

Not verified: the 14,000-line target (an estimate from the first stack's ratio);
whether every Calendar source the agent might use omits `kind`; the line counts
for the smaller unreached items (rounded, from reading).

## 8. How to re-measure

Modules exclude `__init__` and `__main__`. Code and prose are split by
`tokenize`: a line is prose if it carries only a comment or a docstring
(a string statement on its own), code if it carries any other token.

```sh
git archive <ref> src tests | tar -x -C /tmp/m && python3 measure.py /tmp/m
uv run --extra dev pytest -o addopts="" -q --co               # tests
uv run --extra dev pytest -o addopts="" -q --co -m guardrail  # guardrails
git merge-tree --write-tree --name-only main <branch>         # conflict set
```

## 9. What the first stack did, 2026-09-18 to 09-20

Kept as evidence, measured against the `pre-simplification` tag:

| | tag | after PR 7 | after PR 11 |
|---|---|---|---|
| modules | 24 | 22 | 20 |
| source lines | 12,199 | 10,793 | 10,620 |
| of which code | 5,557 | 5,156 | 5,173 |
| of which docstrings + comments | 5,199 | 4,321 | 4,116 |
| test lines | 15,900 | 15,834 | 15,494 |

The source did not halve: the prose pass kept the reasoning, as CONTRIBUTING
asks, and removed only what was said twice. Every duplication the 09-18 read
listed became one thing, and `ship` from the CLI and the Monday prep queue
worked for the first time. Guardrails 244 before and after PR 10; the full
suite was green on every PR.

Kept against the original plan, each for its ticket: `vault.py` (#16),
`people.py` as its own module, `prep.Schedule` (#25), the ledger's reingest
queue (#17), the pre-flight pass in `observe`, `soak.py`, and `voice.py` (in
`push` it is an import cycle through `statedoc`).

Defects the stack found along the way, three of them still on `main` (§1):
the `kind` collision, #112, #138; plus #109 and #111 (calendar windows), #110
(two meeting qualifiers that disagree in two cases), and #155-#157 filed from
the memory path, of which `main` has since fixed #155 independently.
