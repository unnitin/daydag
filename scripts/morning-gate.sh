#!/bin/zsh
# The launch gate for the scheduled morning - no sudo, no pmset, no window.
#
# launchd runs THIS, not morning.sh: install-morning.sh copies it to
# ~/.local/state/daydag (a folder a bare launchd zsh may read; ~/Documents and
# iCloud are off limits to it) and fires it every 15 minutes from 06:43 to
# 09:58 on weekdays. The gate opens the real run headless - in "DayDAG
# Morning.app", the faceless app the installer builds beside this file, which
# holds the privacy grant for the repo and the vault - only when the Mac is
# awake inside that window and today's brief has not shipped. A Mac that slept
# through 06:43 gets its brief at the first slot after it wakes - never at
# 23:08 as on 2026-10-15 - and after 10:00 the day is skipped. Three attempts a
# day, so a run that keeps failing does not retry all morning.
#
#   scripts/morning-gate.sh                      # what launchd runs (via its copy)
#   DAYDAG_GATE_DRY=1 scripts/morning-gate.sh    # print the decision, open nothing
#   DAYDAG_GATE_NOW=0712 ...                     # pretend it is 07:12
set -u
STATE="$HOME/.local/state/daydag"
ENV_FILE="$STATE/morning.env"   # REPO=, written by install-morning.sh
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"
REPO="${REPO:-}"
APP="$STATE/DayDAG Morning.app"
DB="$STATE/events.db"
RUNS="$STATE/runs"
mkdir -p "$RUNS"
DAY=$(date +%F)
if [[ -n "${DAYDAG_GATE_NOW:-}" ]]; then
  NOW=$((10#$DAYDAG_GATE_NOW))
else
  NOW=$((10#$(date +%H) * 100 + 10#$(date +%M)))
fi
OPEN="${DAYDAG_GATE_OPEN:-640}"
CLOSE="${DAYDAG_GATE_CLOSE:-1000}"
MAX="${DAYDAG_GATE_ATTEMPTS:-3}"

say() { echo "== $(date '+%F %T %Z') gate: $*"; }   # launchd keeps stdout in runs/launchd.log

if (( NOW < OPEN || NOW >= CLOSE )); then
  say "outside the $OPEN-$CLOSE window - not running"
  exit 0
fi
if [[ -f "$DB" ]]; then
  shipped=$(/usr/bin/sqlite3 "$DB" \
    "select count(*) from events where kind = 'soak_shipped' and payload like '%\"$DAY\"%'" \
    2>/dev/null || echo 0)
  if [[ "$shipped" != "0" ]]; then
    say "today's brief already shipped - standing down"
    exit 0
  fi
fi
if [[ -d "$STATE/morning.lock" ]]; then
  if [[ -n "$(find "$STATE/morning.lock" -maxdepth 0 -mmin +180 2>/dev/null)" ]]; then
    say "a lock older than three hours is a dead run, not a running one - clearing it"
    rmdir "$STATE/morning.lock" 2>/dev/null || true
  else
    say "a morning run is in progress - standing down"
    exit 0
  fi
fi
if [[ -z "$REPO" ]]; then
  say "no REPO in $ENV_FILE - run scripts/install-morning.sh"
  exit 1
fi
if [[ ! -x "$APP/Contents/MacOS/daydag-morning" ]]; then
  say "no $APP - run scripts/install-morning.sh"
  exit 1
fi
ATTEMPTS="$RUNS/morning-$DAY.attempts"
N=$(( $(cat "$ATTEMPTS" 2>/dev/null || echo 0) + 1 ))
if (( N > MAX )); then
  say "$MAX attempts today already - giving up until tomorrow"
  exit 0
fi
if [[ -n "${DAYDAG_GATE_DRY:-}" ]]; then
  say "dry: would open attempt $N headless in $APP -> $REPO/scripts/morning.sh"
  exit 0
fi
echo "$N" >"$ATTEMPTS"
say "attempt $N -> $APP -> $REPO/scripts/morning.sh (headless)"
# -g: do not bring anything to the front (the app has no window anyway). No -n:
# an app already running IS a morning in progress, and the lock above said so.
exec /usr/bin/open -g -a "$APP"
