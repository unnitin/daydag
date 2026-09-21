# Open work, and the decisions behind it

State as of **2026-09-21**. Seventeen PRs are open and none has merged. That is
deliberate, and this file is why - so the decision can be re-made later from
what was known, rather than re-derived from a stack of diffs.

Dated on purpose: the PR numbers go stale, the reasoning does not.

---

## 1. Two tracks, and why they are in this order

**Track A - simplification (#143 → #158, integrated by #159).** Twelve PRs that
reshape the existing code: 24 modules to 20, 12,199 source lines to 10,620, one
push kernel, one state parser, one test file per module. Green on every PR.
`docs/simplification.md` is the plan and §8 has the measured outcome.

**Track B - team-practices (#160 → #163) and the DE spec (#164).** New
capability: the "How we work" metrics the team's own doc asks for and nothing
computed.

**Decision, 2026-09-20: Track B lands first. Track A stays parked.**

> "dont merge simplification, get the new functionality in first, use it then
> use the experience to cut / simplify"

The reasoning, which is the part worth keeping: a simplification plan derived
from *reading* the code guesses at what is load-bearing; running the new thing
tells you. That was borne out immediately - both defects found in the first
team-practices PR passed a full fixture suite and only failed against the live
repo (a regex that silently dropped a real branch shape, and a push that
rendered 43 lines where the plan had said twelve and a vault pointer). Neither
would have been found by more review.

The cost of waiting is carried in §3 and it is small.

---

## 2. What is open

| PR | base | what it decides |
|---|---|---|
| #143 | `main` | the simplification plan. Merging it commits to nothing but the document |
| #144-#154 | each on the one below | the eleven simplification steps, in order. Each is green alone and each leaves the repo better than it found it |
| #158 | #154 | a calendar record stored whole - a real crash on the first morning with a log |
| #159 | `main` | **the only merge Track A needs.** Carries all fifteen commits. Parked by the decision above |
| #160 | `main` | the team-practices plan + two `.env.example` keys |
| #161 | #160 | `coverage` - metric 1, the skill, the manifest |
| #163 | #161 | `reviews` - metric 2 and the three checkable §2 rules |
| #164 | `main` | the DE team skills spec. Independent of everything above |
| #52 | `main` | **canary, draft, never merge.** Planted defects, kept open on purpose as a review test |

Track A merges as **#159 alone** - merging #143-#158 bottom-up is the thing
CONTRIBUTING warns about ("a merged stacked PR has not reached `main`"). Track B
merges **#160, then #161, then #163**, or by the same integration trick if that
is quicker. #164 merges whenever.

---

## 3. What the two tracks cost each other - measured, not estimated

Track B is written against the module names on `main` (`brief`, `state`,
`manifests`). Track A renames some of them. The collision was **run** on
2026-09-21, not reasoned about: `fix/remembered-kind` merged with
`path/team-practices-reviews` in a throwaway worktree, conflicts resolved, full
suite run.

**Result: 1159 passed, 1 skipped, 248 guardrails green.** The whole integration
is three things.

| what | why | fix |
|---|---|---|
| `src/daydag/team_practices.py` imports `daydag.brief` | `brief.py` is gone; `Section`, `claim`, `render_push` and `unsourced_claims` all live in `push.py` | `from daydag.push import Section, claim, render_push` |
| `tests/test_team_practices.py` imports `daydag.brief` | same | `from daydag.push import unsourced_claims` |
| `tests/test_manifests.py` modified by #161, deleted by #154 | PR 10 folded it into `test_registry.py` | move both edits (the `team-practices` schedule entry and `test_a_sub_id_is_its_own_artifact_with_its_own_writer`) into `tests/test_registry.py`, delete the old file |

Plus one line of `README.md`, where both tracks edited the module table.

Everything else survives untouched: `voice.WARN` and
`pulse.FORBIDDEN_IN_OUTPUT` are both still there after the stack, and
`team_practices.py` is a new file no simplification PR touches.

**So the order is reversible and cheap either way.** Nothing about parking
Track A makes it harder to land; it costs three import lines and a test move
whenever it goes.

---

## 4. Decisions still open

Not blocked work - genuinely undecided, and each changes what gets built.

1. **Does Track A still merge as-is?** The decision above was to use Track B's
   experience to inform the cut. That experience now exists in part, and it
   argues *for* most of the stack: `coverage` composes from `brief`'s
   primitives, which is exactly the `push` kernel PR 3 extracts, and the one
   thing that made writing it awkward was that those primitives live in a
   module called `brief`. Re-read §3 of `docs/simplification.md` before
   deciding; the retirement in PR 1 (`board.py`, `vault.py`'s unreached half)
   is the part worth re-examining against real use, not the consolidation.
2. **Ticket coverage definition** (#160's plan, §6 step 0). The doc's "roughly
   42%" reproduces under neither definition - 8% TDE-keyed over 30 days, 75%
   any-reference. `coverage` deliberately prints both and blesses neither
   until this is answered.
3. **The people directory has no GitHub login** (#162), so `reviews` cannot
   name a reviewer. Everything in that report that does not name a person
   works today.
4. **`#52` the canary.** Still open, still draft, still full of planted
   defects. It exists to test whether review catches them. Worth either
   running that test or closing it - an indefinitely open PR full of
   deliberate bugs is its own hazard.

---

## 5. If you are picking this up cold

Read in this order: this file, then `docs/simplification.md` §8 (what the stack
actually achieved, measured twice), then `docs/how-we-work-skills.md` §2 (the
baselines, and the number that does not reproduce). The three PR bodies of
#159, #161 and #163 carry the rest.
