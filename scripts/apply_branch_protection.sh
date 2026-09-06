#!/usr/bin/env bash
# Lock main: no direct pushes except repo admins, CI green, panel reviews resolved.
#
# Requires the repo to be public (or on GitHub Pro) - rulesets 403 on private/Free.
# Idempotent: deletes a prior ruleset of the same name before creating.
set -euo pipefail

REPO="${REPO:-unnitin/daydag}"
NAME="main-protection"

existing=$(gh api "/repos/$REPO/rulesets" --jq ".[] | select(.name==\"$NAME\") | .id" || true)
if [ -n "$existing" ]; then
  echo "removing existing ruleset $existing"
  gh api -X DELETE "/repos/$REPO/rulesets/$existing"
fi

gh api -X POST "/repos/$REPO/rulesets" --input - <<'JSON'
{
  "name": "main-protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "bypass_actors": [
    { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" }
  ],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": true,
        "allowed_merge_methods": ["squash", "merge", "rebase"]
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          { "context": "secret scan" },
          { "context": "lint" },
          { "context": "test (py3.11)" },
          { "context": "test (py3.12)" },
          { "context": "guardrails" },
          { "context": "ai: architecture" },
          { "context": "ai: guardrails" },
          { "context": "ai: security" },
          { "context": "ai: tests" }
        ]
      }
    }
  ]
}
JSON

echo
echo "applied. main now requires:"
echo "  - a pull request with 1 approval"
echo "  - every review thread resolved  (this is the 'reviews resolved before merge' rule)"
echo "  - ci: secret scan, lint, test 3.11 + 3.12, guardrails"
echo "  - ai panel: architecture, guardrails, security, tests"
echo "  - no force-push, no deletion"
echo "  - bypass: repository admins only (actor_id 5 = admin role)"
