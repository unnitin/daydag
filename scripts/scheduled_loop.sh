#!/bin/zsh
# Fire one DayDAG loop headless, on a schedule (#25). launchd calls this; it is
# also safe to run by hand: `scripts/scheduled_loop.sh prep-ahead`.
#
# Why local and not a cloud routine: the loops read the Obsidian vault (iCloud,
# on this machine) and the event log under ~/.local/state - neither exists in a
# cloud sandbox.
#
# The tool allowlist is the guardrail: read-only connectors, the repo's own
# runner, and ONE write - a Slack message, which the prompt and the skill's
# guardrail 1 restrict to the principal's DM.
set -u
loop="${1:?usage: scheduled_loop.sh <loop>}"
repo="${0:A:h:h}"
logdir="$HOME/.local/state/daydag/logs"
mkdir -p "$logdir"
log="$logdir/$loop-$(date +%Y-%m-%d).log"

case "$loop" in
  prep-ahead)
    ask="prep tomorrow's calls: run the prep-ahead loop. (1) .venv/bin/python -m daydag.run plan prep-ahead. (2) Fetch the calendar day it names and write the connector's events array to /tmp/daydag-calendar.json exactly as returned - do not reshape, rename or drop fields (the code reads google's own attendee flags, optional and self included). (3) .venv/bin/python -m daydag.run plan prep-ahead --calendar /tmp/daydag-calendar.json --log ~/.local/state/daydag/events.db. (4) Run every read it names and write the payloads to /tmp/daydag-payloads.json. (5) .venv/bin/python -m daydag.run render prep-ahead --payloads /tmp/daydag-payloads.json --log ~/.local/state/daydag/events.db. Post render's output verbatim to the principal's Slack DM (SLACK_USER_PRINCIPAL in .env) and nowhere else. Never compose the prep yourself: if any step fails, post one line naming the step that failed. If render says no call matched, post nothing." ;;
  *) echo "unknown loop: $loop" >&2; exit 2 ;;
esac

allowed=(
  "Read" "Glob" "Grep"
  "Bash(.venv/bin/python -m daydag.run:*)"
  "Bash(.venv/bin/python -m daydag.people:*)"
  "Edit(//tmp/daydag-*)"
  "mcp__claude_ai_Google_Calendar__list_events" "mcp__claude_ai_Google_Calendar__get_event"
  "mcp__claude_ai_Gmail__search_threads" "mcp__claude_ai_Gmail__get_thread" "mcp__claude_ai_Gmail__get_message"
  "mcp__claude_ai_Google_Drive__search_files" "mcp__claude_ai_Google_Drive__read_file_content"
  "mcp__claude_ai_Google_Drive__get_file_metadata"
  "mcp__claude_ai_Notion__notion-search" "mcp__claude_ai_Notion__notion-fetch"
  "mcp__claude_ai_Notion__notion-query-meeting-notes"
  "mcp__claude_ai_Slack__slack_search_public_and_private" "mcp__claude_ai_Slack__slack_read_channel"
  "mcp__claude_ai_Slack__slack_read_thread" "mcp__claude_ai_Slack__slack_send_message"
)

cd "$repo" || exit 1
{
  echo "=== $(date '+%F %T %Z') $loop"
  claude -p "/daily-loops $ask" --allowedTools "${allowed[@]}" --output-format text </dev/null
  echo "=== exit $?"
} >>"$log" 2>&1
