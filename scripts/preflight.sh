#!/usr/bin/env bash
# Run exactly what CI runs, before pushing.
#
# This exists because "it passed locally" was twice untrue: the editable install
# put `src/daydag` on sys.path but not the repo root, and `setup-uv` needed a
# lockfile that did not exist. Both passed in the working venv and failed in CI.
# So this builds a throwaway venv rather than reusing yours - reusing it is what
# hid the first bug.
#
#   bash scripts/preflight.sh          # full, CI-equivalent (slower, ~30s)
#   bash scripts/preflight.sh --fast   # reuse .venv, skip the rebuild
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
FAST=${1:-}
fail=0
step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
ok()   { printf '   \033[32mpass\033[0m  %s\n' "$1"; }
bad()  { printf '   \033[31mFAIL\033[0m  %s\n' "$1"; fail=1; }

step "secret scan (every tracked file, as CI does)"
if git ls-files -z | xargs -0 python3 scripts/scan_secrets.py; then
  ok "no real identifiers"
else
  bad "real identifiers found - move them to .env, do not weaken the scanner"
fi

if [ "$FAST" = "--fast" ] && [ -x .venv/bin/python ]; then
  PY=.venv/bin/python; RUFF=.venv/bin/ruff
  step "using existing .venv (--fast)"
else
  step "building a throwaway venv (CI parity)"
  VENV=$(mktemp -d)/venv
  uv venv -q --python 3.12 "$VENV"
  VIRTUAL_ENV="$VENV" uv pip install -q -e ".[dev]"
  PY="$VENV/bin/python"; RUFF="$VENV/bin/ruff"
  ok "built"
fi

step "lint"
"$RUFF" check -q . && ok "ruff check" || bad "ruff check"
"$RUFF" format --check -q . && ok "ruff format" || bad "ruff format - run: ruff format ."

step "tests"
"$PY" -m pytest -q -m "not integration" && ok "suite" || bad "suite"

step "guardrails (SPEC section 6)"
"$PY" -m pytest -q -m guardrail && ok "guardrails" || bad "guardrails - never weaken these to pass"

echo
if [ "$fail" -eq 0 ]; then
  printf '\033[32mpreflight clean.\033[0m Before opening the PR, run /code-review in your\nClaude Code session - that is the review layer, and it costs nothing.\n'
else
  printf '\033[31mpreflight failed.\033[0m Fix before pushing; CI will fail on the same thing.\n'
  exit 1
fi
