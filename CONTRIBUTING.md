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

## Merging

`main` takes no direct pushes except from repo admins. Everything else goes through a PR
that must have: CI green (secret scan, lint, tests on 3.11 and 3.12, guardrails), an
approval, and **every review thread resolved** - including the AI panel's. The panel is
four narrow reviewers (architecture, guardrails, security, tests); each comments only
within its remit.
