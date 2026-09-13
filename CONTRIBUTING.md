# Contributing

## The repo is public

Real Slack / Google / Notion / Atlassian / Databricks identifiers, internal emails and
absolute home paths live in `.env`, which is gitignored, and **nowhere else**. Docs and
code reference them as `${VAR_NAME}`; `src/daydag/config.py` resolves them.

`.env.example` is the committed schema. Add a key there whenever you add one to `.env`.

`scripts/scan_secrets.py` enforces this as a pre-commit hook and again in CI over every
tracked file. If it blocks you, the fix is to move the value into `.env` - never to
weaken the scanner.

## Setup

```sh
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run pre-commit install   # installs BOTH the pre-commit and pre-push hooks
cp .env.example .env    # then fill in real values
```

## Tests come first

Write the test before the code. A test describes behaviour someone wants; if you cannot
state the expected behaviour, the design is not ready. Concretely:

1. Write a test that fails for the right reason - run it and read the failure.
2. Write the least code that passes it.
3. Refactor with the test green.

Tests marked `@pytest.mark.guardrail` are SPEC section 6 safety properties. They are never
skipped, xfailed, or weakened to make a change pass. If one is in your way, the change is
wrong or the guardrail needs an explicit decision - not a quieter test.

## Branches

One branch per concern, cut from `main` or stacked on the branch it depends on.
Name it for what it does: `fix/*`, `chore/*`, `path/*` for a long-lived surface.

**The prefix is load-bearing, not cosmetic.** `.github/workflows/ci.yml` triggers
on an allowlist of BASE branches:

```yaml
branches: [main, 'path/**', 'chore/**', 'fix/**', 'canary/**']
```

`pull_request` filters on the branch a PR TARGETS. So a branch named `docs/x` or
`feat/x` gets full CI on its own PR into `main`, and silently gives **zero checks**
to anything stacked on top of it. That is not hypothetical: a narrow trigger let
eight PRs merge unchecked before anyone noticed. Until the allowlist is replaced
with something that cannot fail this way, stay inside those prefixes.

The paths stay independent by owning separate modules and sharing only
`daydag.config`. Keep it that way - if two branches need the same new helper, it
belongs on `main` first.

## Docstrings are read by agents

The first reader of this code is an agent, and an agent needs the contract
before it needs the reasoning. Long narrative docstrings buried the two things
a caller actually has to know - what to call, and what must not be broken - so
module docstrings lead with those:

```
"""One-line summary.

USING IT          the shortest real call sequence, copy-pasteable
CONTRACTS         numbered. Break one and a guarantee is gone
WHY IT EXISTS     the reasoning. Compressed, and after the contract
KNOWN LIMIT       what it does not do, when that is load-bearing
"""
```

`src/daydag/runlog.py` is the worked example. Function docstrings lead with
what the call returns, then `Args:` / `Raises:` for anything a caller can get
wrong - `record`'s `started` and `failure` are both there because both have a
wrong-looking-right form.

Keep the reasoning. It is why review finds real defects here, and the history
of a past bug is often the only thing that explains a guard. Compress it and
move it below the contract; do not delete it.

Two rules that follow:

- **A claim in a docstring is a claim under test.** `"never"`, `"always"`,
  `"every"` and `"exactly once"` have each been wrong at least once in this
  repo - `Skip`'s "never a bare name" and `RunRow.line`'s "all of them the
  agent's own words" were both false when written. Say what the code does.
- **A guardrail should scan code, not prose.** The clock guardrail in
  `tests/test_runlog.py` text-scanned the whole file and so fired on the
  module docstring's own usage example. It parses the AST now. A contract that
  cannot be written down is worse documented for the sake of a cruder check.

## Review before you push

Reviews happen locally, before the push - not in CI. Two layers, and they are
different kinds of check:

**1. Deterministic - automated, blocks the push.**

```sh
bash scripts/preflight.sh          # CI-equivalent: throwaway venv, ~30s
bash scripts/preflight.sh --fast   # reuse .venv, a few seconds
```

Runs exactly what CI runs: secret scan over every tracked file, ruff check and
format, the docs check, the suite on a clean install, and the guardrail tests on
their own.

The docs check is there because this repo's recurring failure is documentation
that asserts what the code does not do - a spec filename that had been renamed,
a parser the docs described and nothing called. A broken link is the cheapest
part of that to catch mechanically, so it is caught. It
is wired as a `pre-push` hook, so `git push` runs the `--fast` form for you.

It builds a **throwaway venv** by default rather than reusing yours, because
"it passed locally" has already been untrue twice: the editable install put
`src/daydag` on `sys.path` but not the repo root, and `setup-uv` needed a
lockfile that did not exist. Both passed in a warm venv and failed in CI.

**2. Judgement - you invoke it, costs nothing.**

Run `/code-review` in your Claude Code session before opening the PR. That is
the review layer. It reads the diff with the repo's context already loaded,
which is when a fix is cheapest - and it bills against the subscription rather
than API credit.

Worth asking it for specifically, since these are the failure modes this repo
actually has:

- a second writer to an artifact the ownership table assigns elsewhere
- a send path that reaches anything but Nitin's own DM
- a loop that auto-closes on repo or Jira evidence instead of surfacing
- a guardrail-marked test weakened, skipped, or xfailed
- a real identifier outside `.env` - the repo is public

### CodeQL

`.github/workflows/codeql.yml` runs GitHub's static analysis on every PR and
weekly on `main`. Detection is deterministic - it compiles the code
and queries it, so no model is involved in finding an alert and there is no
credential to expire.

**Fix suggestions are a different matter.** Copilot Autofix is enabled by
default on public repositories using CodeQL and generates suggested patches with
an LLM (GPT-5.3-Codex, an OpenAI model), sending code and alert context to do
so. It is a repo-level setting, not part of this workflow: disable it under
Settings -> Code security if the codebase should not leave GitHub. Detection is
unaffected; only the suggested patches stop.

It complements `scripts/scan_secrets.py` rather than duplicating it: the scanner
catches identifiers that must not be published, CodeQL catches dataflow -
untrusted input reaching a subprocess, a path built from unvalidated data. The
weekly run exists because queries improve after code lands.

Findings appear in the repository's Security tab, not as PR comments.

### Why review is local, not in CI

An LLM reviewer ran in CI for exactly as long as it took to prove it does not
work here. Seven attempts, four distinct failure modes: a missing `id-token`
permission; a silent skip that exited *successfully*, turning four required
checks green having read nothing; an empty API credit balance; and finally an
identical failure on a subscription token - model resolved, first call refused
at ~2s, `total_cost_usd: 0`, reason hidden by the action.

The likeliest remaining cause is not fixable in this repo: the account is an
organisation seat, and `claude setup-token` documents itself as requiring a
Pro/Max subscription. Organisations can restrict programmatic access.

The workflow is **deleted, not disabled**. A disabled workflow is a thing people
re-enable without re-reading why it was turned off, and a permanently-red check
is as corrosive as a permanently-green one: both teach you to stop reading the
signal.

If it is ever revived the conditions are unchanged, and were expensive to learn:
land it **optional**, prove it against a branch of planted defects that CI cannot
catch, and make it required only once it has caught something real. The branch
`canary/planted-defects` exists for exactly that.

CodeQL covers the half a machine is genuinely better at - dataflow, untrusted
input reaching a subprocess - and needs no credential at all.

## Merging

`main` takes no direct pushes except from repo admins. Everything else goes through a PR
that must have: CI green (secret scan, lint, tests on 3.11 and 3.12, guardrails), an
approval, and **every review thread resolved**.

### A merged stacked PR has not reached `main`

Merging a stack bottom-up moves almost nothing to the trunk, and every PR reads
MERGED afterwards. A six-PR stack was merged in order and `main` received **2 of
its 11 commits** - the other five PRs merged into their own parent branch, which
is what GitHub is asked to do when a PR's base is another feature branch.

Nothing catches this. Every PR shows MERGED, every CI run is green, and the
issues each one closes are closed. The signal is real and measures something
other than what you want to know.

So after merging a stack, **verify, do not assume**:

```sh
git fetch origin
for sha in $(git log --format=%H origin/main..<top-of-stack>); do
    git merge-base --is-ancestor $sha origin/main || echo "MISSING $sha"
done
```

Anything missing: branch an integration branch from `origin/main`, merge the one
branch carrying the whole stack (usually whichever received the last merge), run
the suite **on the merge result** rather than on the branches it came from, and
open a single PR into `main`.

The alternative - retargeting each PR to `main` as its parent merges - works too,
and has to be done in order, before each merge, every time. The integration
branch is one step at the end instead of five in the middle.
