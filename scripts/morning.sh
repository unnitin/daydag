#!/bin/zsh
# The scheduled morning brief (#25, the morning only - his call, 2026-10-12).
#
# One headless Claude Code run: plan, fetch over his claude.ai connectors,
# render, post to HIS DM. Not a bare cron of `daydag.run` - that cannot reach
# a connector and would post a brief with every source empty (USAGE.md).
# launchd runs it on weekday mornings through scripts/morning-gate.sh and
# "DayDAG Morning.app" (no window; see USAGE.md "The scheduled morning").
#
#   scripts/morning.sh                 # the real thing: posts to his DM
#   DAYDAG_POST=no scripts/morning.sh  # a dry run: renders, posts nothing
#   scripts/morning.sh --probe         # can this process read the repo, the
#                                      # vault, the log, and reach claude?
#                                      # answers in ~/.local/state/daydag/morning.probe
#
# Every run appends to ~/.local/state/daydag/runs/morning-<date>.log.
set -eu
cd "${0:A:h}/.."

STATE="$HOME/.local/state/daydag"
DAY=$(date +%F)
RUNS="$STATE/runs"
mkdir -p "$RUNS"
LOG="$RUNS/morning-$DAY.log"
PAYLOADS="$RUNS/payloads-$DAY.json"
EVENTS="$STATE/events.db"
POST="${DAYDAG_POST:-yes}"
CLAUDE="${DAYDAG_CLAUDE:-$(command -v claude || echo "$HOME/.local/bin/claude")}"

# The probe behind install-morning.sh: the same reads a morning makes, from the
# same process, so a privacy prompt comes now and not at 06:43. Writes its
# answers to a file because an app opened by launchd has no terminal to print to.
if [[ "${1:-}" == "--probe" ]]; then
  PROBE="$STATE/morning.probe"
  {
    echo "repo: ok ($PWD)"
    VAULT=$(sed -n 's/^VAULT_ROOT=//p' .env 2>/dev/null | sed -e "s/^['\"]//" -e "s/['\"]\$//")
    if [[ -z "$VAULT" ]]; then echo "vault: failed (no VAULT_ROOT in .env)"
    elif ls "$VAULT/DayDAG" >/dev/null 2>&1; then echo "vault: ok ($VAULT)"
    else echo "vault: denied ($VAULT)"; fi
    if .venv/bin/python -m daydag.run plan morning --log "$EVENTS" >/dev/null 2>&1; then echo "plan: ok"
    else echo "plan: failed (.venv/bin/python -m daydag.run plan morning)"; fi
    if out=$("$CLAUDE" -p "reply with the single word ok" --max-turns 1 --output-format text </dev/null 2>&1); then
      echo "claude: ok ($(printf '%s' "$out" | tr '\n' ' ' | head -c 40))"
    else
      echo "claude: failed ($(printf '%s' "$out" | tr '\n' ' ' | head -c 160))"
    fi
  } >"$PROBE" 2>&1
  exit 0
fi

if [[ "$POST" == "yes" ]]; then
  DELIVER="Post what render printed, per the skill's 'Posting the morning': everything above the line '--- thread ---' verbatim as ONE Slack message to the principal's DM (the channel id is the value of SLACK_USER_PRINCIPAL in .env), and everything below it, if any, as ONE reply in that message's thread. Then, only if both posts are byte-for-byte what render printed, run: .venv/bin/python -m daydag.soak shipped --log $EVENTS"
  SEND=(mcp__claude_ai_Slack__slack_send_message)
else
  DELIVER="This is a DRY RUN: do NOT post or send anything anywhere. Print what render printed."
  SEND=()
fi

PROMPT="Run DayDAG's morning brief unattended, following .claude/skills/daily-loops/SKILL.md.
1. Run: .venv/bin/python -m daydag.run plan morning --log $EVENTS
2. Perform every step it lists, exactly as given (Slack steps: pass keywords, filters, sort, sort_dir and after as the search tool's own fields). Write all results to $PAYLOADS as ONE JSON object keyed by each step's source; steps that share a source go into one list. A source you cannot reach: leave its key out - never guess.
3. Run: .venv/bin/python -m daydag.run render morning --payloads $PAYLOADS --log $EVENTS
4. $DELIVER
Never send anything to anyone but the principal's own DM. End with one line: posted, dry run, or failed and why."

# One morning at a time, and one post a day. On 2026-10-14 launchd's catch-up
# run, a dry run and a hand-run session overlapped, raced on the payloads file,
# and the hand-run one posted first: a second post would have duplicated it.
LOCK="$STATE/morning.lock"
# A morning never takes three hours. A lock that old is left over from a run
# that died without its trap, and would block every morning after it.
if [[ -d "$LOCK" ]] && [[ -n "$(find "$LOCK" -maxdepth 0 -mmin +180 2>/dev/null)" ]]; then
  echo "== $(date '+%F %T %Z') removing a stale lock from $(date -r "$LOCK" '+%F %T')" >>"$LOG"
  rmdir "$LOCK" 2>/dev/null || true
fi
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "== $(date '+%F %T %Z') another morning run holds $LOCK - standing down" >>"$LOG"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
if [[ "$POST" == "yes" ]] && [[ -f "$EVENTS" ]] && [[ "$(sqlite3 "$EVENTS" \
    "select count(*) from events where kind = 'soak_shipped' and payload like '%\"$DAY\"%'" \
    2>/dev/null || echo 0)" != "0" ]]; then
  echo "== $(date '+%F %T %Z') today's brief already shipped - not posting it twice" >>"$LOG"
  exit 0
fi

rc=0
{
  echo "== $(date '+%F %T %Z') morning (post: $POST)"
  # stdin from /dev/null: headless, there is no terminal to wait on, and a
  # `claude -p` given a closed pipe reads it as an empty prompt and moves on.
  "$CLAUDE" -p "$PROMPT" --max-turns 80 --output-format text --allowedTools \
    "Bash(.venv/bin/python -m daydag.run:*)" "Bash(.venv/bin/python -m daydag.soak:*)" \
    Read Write ToolSearch \
    mcp__claude_ai_Google_Calendar__list_events \
    mcp__claude_ai_Gmail__search_threads mcp__claude_ai_Gmail__get_message mcp__claude_ai_Gmail__get_thread \
    mcp__claude_ai_Slack__slack_search_public_and_private mcp__claude_ai_Slack__slack_read_thread \
    mcp__claude_ai_Slack__slack_read_channel "${SEND[@]}" </dev/null || rc=$?
  echo "== $(date '+%F %T %Z') done (exit $rc)"
} >>"$LOG" 2>&1
exit $rc
