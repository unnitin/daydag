#!/bin/zsh
# The weekend refresh of the Sponsor progress deck (the progress-deck skill), headless.
#
# launchd runs a copy of scripts/progress-deck-gate.sh on Saturday and Sunday
# mornings; the gate opens "DayDAG Morning.app" with --progress-deck, and
# morning.sh hands that to this script, so the run holds the app's Documents and
# iCloud grant - the same reason the morning runs through the app (USAGE.md).
#
#   scripts/progress-deck.sh                    # the real thing: hunts the coming week's Sponsor 1:1,
#                                               # builds the next version, posts to his DM
#   DAYDAG_POST=no scripts/progress-deck.sh     # dry run: hunts and lists what would change; no build, no post
#   scripts/progress-deck.sh --probe            # can this machine run it? (deck, node, LibreOffice, directory, claude)
#   scripts/progress-deck.sh --build <out.pptx> [NO_NOTES=1 ...]
#                                               # one build of the deck to a NEW file in its folder
#   scripts/progress-deck.sh --snapshot built|his <file.pptx>
#                                               # a copy in the stickies folder, the base for drift checks
#
# --build and --snapshot exist so the headless run can be allowed exactly these
# two writes instead of `node` and `cp` in general. Every run appends to
# ~/.local/state/daydag/runs/progress-deck-<monday>.log.
set -eu
cd "${0:A:h}/.."

STATE="$HOME/.local/state/daydag"
RUNS="$STATE/runs"
STICKIES="$STATE/stickies"
EVENTS="$STATE/events.db"
POST="${DAYDAG_POST:-yes}"
CLAUDE="${DAYDAG_CLAUDE:-$(command -v claude || echo "$HOME/.local/bin/claude")}"
mkdir -p "$RUNS" "$STICKIES"

envval() { sed -n "s/^$1=//p" .env 2>/dev/null | head -1 | sed -e "s/^['\"]//" -e "s/['\"]\$//"; }
DECK="$(envval PROGRESS_DECK)"
DECK="${DECK/#\~/$HOME}"
if [[ -z "$DECK" ]]; then
  echo "no PROGRESS_DECK in .env - see .env.example" >&2
  exit 1
fi
DIR="${DECK:h}"
STEM="$(basename "${DECK%.pptx}" | sed -E 's/-v[0-9]+$//; s/-[0-9]{4}-[0-9]{2}-[0-9]{2}$//; s/-draft$//')"
NEXTVER=".claude/skills/consulting-slides/scripts/nextver.sh"

case "${1:-}" in
  --build)
    out="${2:?usage: progress-deck.sh --build <out.pptx> [FLAG=1 ...]}"
    shift 2
    if [[ "${out:h}" != "$DIR" ]]; then
      echo "refusing: $out is not in $DIR (pass the absolute path nextver.sh printed)" >&2; exit 2
    fi
    if [[ -e "$out" ]]; then
      echo "refusing: $out exists - every build is a new file" >&2; exit 2
    fi
    script="$DIR/$STEM-draft.build.js"
    [[ -f "$script" ]] || script="$DIR/$STEM.build.js"
    [[ -f "$script" ]] || { echo "no build script for $STEM in $DIR" >&2; exit 1; }
    flags=()
    for f in "$@"; do
      case "$f" in
        NO_NOTES=1|NO_CONFIRM=1|WIP=1|WITH_DROPPED=1|WITH_OVERALL=1) flags+=("$f") ;;
        *) echo "refusing flag $f: allowed NO_NOTES=1 NO_CONFIRM=1 WIP=1 WITH_DROPPED=1 WITH_OVERALL=1" >&2; exit 2 ;;
      esac
    done
    exec env "${flags[@]}" OUT="$out" node "$script"
    ;;
  --snapshot)
    kind="${2:?usage: progress-deck.sh --snapshot built|his <file.pptx>}"
    src="${3:?usage: progress-deck.sh --snapshot built|his <file.pptx>}"
    [[ -f "$src" ]] || { echo "no such file: $src" >&2; exit 1; }
    mkdir -p "$STICKIES"
    base="$(basename "${src%.pptx}")"
    case "$kind" in
      built) dst="$STICKIES/$base-built.pptx" ;;
      his) dst="$STICKIES/$base-his-$(date +%Y%m%d-%H%M).pptx" ;;
      *) echo "snapshot kind is built or his, not $kind" >&2; exit 2 ;;
    esac
    if [[ -e "$dst" ]]; then echo "$dst (kept, already there)"; exit 0; fi
    cp "$src" "$dst"
    echo "$dst"
    exit 0
    ;;
  --probe)
    if latest="$(bash "$NEXTVER" --latest "$DECK" 2>/dev/null)"; then echo "deck: ok ($latest)"
    else echo "deck: failed (no versioned build of $STEM in $DIR)"; fi
    if [[ -f "$DIR/$STEM-pending.md" ]]; then echo "pending: ok"; else echo "pending: missing ($DIR/$STEM-pending.md)"; fi
    if command -v node >/dev/null; then echo "node: ok"; else echo "node: failed (not on PATH)"; fi
    if command -v soffice >/dev/null || [[ -d /Applications/LibreOffice.app ]]; then echo "libreoffice: ok"
    else echo "libreoffice: failed (render.sh needs it)"; fi
    if .venv/bin/python -m daydag.people show sponsor --log "$EVENTS" 2>/dev/null | grep -q "emails.*@"; then
      echo "sponsor email: ok"
    else
      echo "sponsor email: failed (.venv/bin/python -m daydag.people set sponsor --email <addr> --log $EVENTS)"
    fi
    if out=$("$CLAUDE" -p "reply with the single word ok" --max-turns 1 --output-format text </dev/null 2>&1); then
      echo "claude: ok ($(printf '%s' "$out" | tr '\n' ' ' | head -c 40))"
    else
      echo "claude: failed ($(printf '%s' "$out" | tr '\n' ' ' | head -c 160))"
    fi
    exit 0
    ;;
esac

# The window: on a weekend, the coming Monday to Sunday; any other day (by hand),
# tomorrow to a week out. The marker is keyed on the window's first day, so
# Saturday's run and Sunday's catch-up share it.
if (( $(date +%u) >= 6 )); then
  FROM="$(date -v+mon +%F)"
else
  FROM="$(date -v+1d +%F)"
fi
TO="$(date -j -v+6d -f %F "$FROM" +%F)"
LOG="$RUNS/progress-deck-$FROM.log"
DONE="$RUNS/progress-deck-$FROM.done"
PRINCIPAL="$(envval SLACK_USER_PRINCIPAL)"

LOCK="$STATE/progress-deck.lock"
if [[ -d "$LOCK" ]] && [[ -n "$(find "$LOCK" -maxdepth 0 -mmin +240 2>/dev/null)" ]]; then
  echo "== $(date '+%F %T %Z') removing a stale lock from $(date -r "$LOCK" '+%F %T')" >>"$LOG"
  rmdir "$LOCK" 2>/dev/null || true
fi
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "== $(date '+%F %T %Z') another progress-deck run holds $LOCK - standing down" >>"$LOG"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# Reads are the same for a dry run and a real one; the writes are the deck's
# folder, the run's own log folder, the two helper modes above, and one Slack
# message, which the prompt restricts to his DM.
# Each repo script is allowed by its relative and its absolute path: the first
# dry run (17 Nov) called nextver.sh by absolute path and was refused.
REPO="$PWD"
both() { local runner="$1" rel="$2"; print -rl -- "Bash($runner $rel:*)" "Bash($runner $REPO/$rel:*)"; }
allowed=(
  Read Glob Grep ToolSearch Agent SendMessage
  "Bash(.venv/bin/python -m daydag.people:*)" "Bash(.venv/bin/python -m daydag.style:*)"
  "Bash(gh api:*)" "Bash(ls:*)" "Bash(wc:*)"
  ${(f)"$(both bash "$NEXTVER")"}
  ${(f)"$(both python3 .claude/skills/consulting-slides/scripts/stickies.py)"}
  ${(f)"$(both python3 .claude/skills/consulting-slides/scripts/drift.py)"}
  ${(f)"$(both python3 .claude/skills/slide-review/scripts/outline.py)"}
  mcp__claude_ai_Google_Drive__download_file_content
  mcp__claude_ai_Google_Calendar__list_events mcp__claude_ai_Google_Calendar__get_event
  mcp__claude_ai_Gmail__search_threads mcp__claude_ai_Gmail__get_thread mcp__claude_ai_Gmail__get_message
  mcp__claude_ai_Slack__slack_search_public_and_private mcp__claude_ai_Slack__slack_read_thread
  mcp__claude_ai_Slack__slack_read_channel mcp__claude_ai_Slack__slack_read_user_profile
  mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql mcp__claude_ai_Atlassian__getJiraIssue
  mcp__claude_ai_Google_Drive__read_file_content mcp__claude_ai_Google_Drive__search_files
  mcp__claude_ai_Google_Drive__get_file_metadata
  mcp__claude_ai_Notion__notion-search mcp__claude_ai_Notion__notion-fetch
  mcp__claude_ai_Notion__notion-query-meeting-notes
)
if [[ "$POST" == "yes" ]]; then
  allowed+=(
    "Write(/$DIR/**)" "Edit(/$DIR/**)" "Write(/$RUNS/**)"
    ${(f)"$(both zsh 'scripts/progress-deck.sh --build')"}
    ${(f)"$(both zsh 'scripts/progress-deck.sh --snapshot')"}
    ${(f)"$(both bash .claude/skills/consulting-slides/scripts/render.sh)"}
    mcp__claude_ai_Slack__slack_send_message
  )
  DELIVER="Run every step, 1 to 8. Post per step 8 to the principal's DM only - the Slack channel id is $PRINCIPAL - and nowhere else; create no drafts anywhere."
else
  DELIVER="This is a DRY RUN. Run steps 1 to 4 only: build nothing, write nothing, post nothing, take no snapshot. Print the step 4 table and the DM you would have posted."
fi

PROMPT="Run the progress-deck skill's unattended weekend refresh, following .claude/skills/progress-deck/SKILL.md.
Today is $(date '+%a %d %b %Y'). Hunt window: $FROM to $TO (one calendar query per day). The deck: $DECK (versions found by stem; its folder is $DIR).
$DELIVER
Never send anything to anyone but the principal's own DM. Never ask a question: what needs him goes under 'needs you'.
Headless, nobody can approve a command, so: one command per Bash call, with literal paths; no shell variables, \$(...), loops, ';', '&&' or pipes (run nextver.sh alone and paste the path it prints; one gh api call per issue, or one gh api graphql query). Count slide text and placeholders with slide-review's outline.py, never python -c. Read files with Read, not cat.
End with exactly one line, the last of your output, starting 'RESULT: ' and then one of: 'built vNN for the <date> 1:1' | 'no 1:1 in the window' | 'nothing moved, vNN current' | 'dry run' | 'failed before build: <step and why>' | 'failed after build vNN: <step and why>'."

if [[ -n "${DAYDAG_SHOW_ALLOWED:-}" ]]; then print -rl -- "${allowed[@]}"; exit 0; fi   # debug: the allowlist this run would get
rc=0
{
  echo "== $(date '+%F %T %Z') progress-deck $FROM..$TO (post: $POST)"
  # Every local copy of a watched repo first (daydag.repos), before Claude reads
  # GitHub. Run by the shell, so it needs no allowlist entry; never fatal.
  .venv/bin/python -m daydag.repos sync --log "$EVENTS" || echo "repos: refresh failed (exit $?)"
  # --add-dir: the deck, the drift bases and the run's own folder live outside the
  # repo, and a headless run may not even `ls` outside its working directories.
  "$CLAUDE" -p "$PROMPT" --max-turns 300 --output-format text \
    --add-dir "$DIR" "$STICKIES" "$RUNS" --allowedTools "${allowed[@]}" </dev/null || rc=$?
  echo "== $(date '+%F %T %Z') done (exit $rc)"
} >>"$LOG" 2>&1

# The weekend is done unless the run failed before it built anything; a failure
# after a build already posted its line, and a second build would only add a version.
result="$(grep -E '^RESULT: ' "$LOG" | tail -1 || true)"
if [[ "$POST" == "yes" && -n "$result" && "$result" != "RESULT: failed before build"* ]]; then
  echo "$result" >"$DONE"
fi
exit $rc
