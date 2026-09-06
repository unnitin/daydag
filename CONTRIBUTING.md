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

Work happens on parallel paths cut from `main`, one per independent surface:

| Branch | Owns | Issues |
|---|---|---|
| `path/state-store` | the `DayDAG/` vault folder, event log, decision queue | #5, #26, #36 |
| `path/engineering-pulse` | git mirrors, PR/CI state, board join | #11, #12, #32 |
| `path/meeting-ledger` | calendar-driven note reconciliation | #35 |
| `path/skill-registry` | skill manifests, the one-writer check, evidence bus | #33, #34 |

They are parallel because they own separate modules and share only `daydag.config`. Keep
it that way - if two paths need the same new helper, it belongs on `main` first.

## Review before you push

Reviews happen locally, before the push - not in CI. Two layers, and they are
different kinds of check:

**1. Deterministic - automated, blocks the push.**

```sh
bash scripts/preflight.sh          # CI-equivalent: throwaway venv, ~30s
bash scripts/preflight.sh --fast   # reuse .venv, a few seconds
```

Runs exactly what CI runs: secret scan over every tracked file, ruff check and
format, the suite on a clean install, and the guardrail tests on their own. It
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

### Why not in CI

It was, briefly, and never completed a single review across four distinct
failure modes. The worst was silent: the action exits *successfully* when it
skips, so four required checks went green having read nothing. A review layer
that fails open is worse than none, because it looks like coverage.

`.github/workflows/ai-review.yml` is still in the repo, disabled, with its
reviewer prompts intact. If it is ever revived: land it **optional**, prove it
against a branch with planted defects (a hardcoded id, a second writer, a
weakened guardrail test) before trusting it, and only make it required once it
has produced findings worth acting on. A check that has never caught anything
should never be able to block a merge.

## Merging

`main` takes no direct pushes except from repo admins. Everything else goes through a PR
that must have: CI green (secret scan, lint, tests on 3.11 and 3.12, guardrails), an
approval, and **every review thread resolved** - including the AI reviewer's, when it is
enabled. It makes one pass over the diff and judges all four criteria - security,
guardrails, architecture, tests - in a single verdict.
