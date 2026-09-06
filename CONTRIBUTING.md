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
uv run pre-commit install
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

## The AI review panel — currently OFF

The workflow is disabled (`gh workflow disable ai-review.yml`) and its four
checks are no longer required, because it never completed a single review. In
order: missing `id-token` permission, then a vacuous pass via the
workflow-validation skip, then an empty API credit balance, then an identical
failure on a subscription token. CI still gates every PR.

Re-enable with `gh workflow enable ai-review.yml`, re-add the four `ai: *`
contexts in `scripts/apply_branch_protection.sh`, and re-run that script.

The notes below still apply when it comes back.

The panel authenticates with a **Claude Code OAuth token**, not an API key. API
credit is a separate balance that a Pro or Max subscription does not fund, so an
API key on a subscription-only account fails every request with "credit balance
is too low" - which surfaces as all four `ai:` checks failing.

To (re)issue the token:

```sh
claude setup-token
gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo unnitin/daydag
```

**A PR that edits anything under `.github/workflows/` gets no real review.** The
action refuses to run when the workflow on the branch differs from the version on
`main` - a deliberate guard against a PR rewriting the workflow to exfiltrate
secrets. It exits *successfully* when it skips, so the four `ai:` checks go green
having done nothing. Treat a workflow-touching PR as unreviewed regardless of the
ticks, and review it by hand.

## Merging

`main` takes no direct pushes except from repo admins. Everything else goes through a PR
that must have: CI green (secret scan, lint, tests on 3.11 and 3.12, guardrails), an
approval, and **every review thread resolved** - including the AI panel's. The panel is
four narrow reviewers (architecture, guardrails, security, tests); each comments only
within its remit.
