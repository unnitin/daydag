#!/bin/zsh
# The launch gate for the weekend progress-deck refresh (the progress-deck skill).
#
# launchd runs a copy of THIS from ~/.local/state/daydag (a bare launchd zsh
# cannot read ~/Documents), every 30 minutes from 07:13 to 12:43 on Saturday
# and Sunday. The gate opens the real run headless in "DayDAG Morning.app" - the
# faceless app scripts/install-morning.sh built, which holds the Documents and
# iCloud grant - with --progress-deck, which morning.sh hands to
# scripts/progress-deck.sh. Saturday is the run; Sunday is the catch-up for a
# Mac that slept through Saturday. Once a weekend's run has finished (a 1:1
# prepped, none in the window, or nothing moved), the rest of the weekend stands
# down; a run that fails before building is retried, three attempts at most.
#
#   scripts/progress-deck-gate.sh                        # what launchd runs (via its copy)
#   DAYDAG_GATE_DRY=1 scripts/progress-deck-gate.sh      # print the decision, open nothing
#   DAYDAG_GATE_NOW=0812 DAYDAG_GATE_DOW=6 ...           # pretend it is Saturday 08:12
set -u
STATE="$HOME/.local/state/daydag"
ENV_FILE="$STATE/morning.env"   # REPO=, written by install-morning.sh
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"
REPO="${REPO:-}"
APP="$STATE/DayDAG Morning.app"
RUNS="$STATE/runs"
mkdir -p "$RUNS"
DOW="${DAYDAG_GATE_DOW:-$(date +%u)}"   # 6 Saturday, 7 Sunday
if [[ -n "${DAYDAG_GATE_NOW:-}" ]]; then
  NOW=$((10#$DAYDAG_GATE_NOW))
else
  NOW=$((10#$(date +%H) * 100 + 10#$(date +%M)))
fi
OPEN="${DAYDAG_GATE_OPEN:-700}"
CLOSE="${DAYDAG_GATE_CLOSE:-1300}"
MAX="${DAYDAG_GATE_ATTEMPTS:-3}"
MON="$(date -v+mon +%F)"   # the coming Monday: the window's first day, and the weekend's key

say() { echo "== $(date '+%F %T %Z') progress-deck gate: $*"; }   # launchd keeps stdout in runs/launchd.log

if (( DOW < 6 )); then
  say "not a weekend - not running"
  exit 0
fi
if (( NOW < OPEN || NOW >= CLOSE )); then
  say "outside the $OPEN-$CLOSE window - not running"
  exit 0
fi
if [[ -f "$RUNS/progress-deck-$MON.done" ]]; then
  say "this weekend's run finished ($(cat "$RUNS/progress-deck-$MON.done")) - standing down"
  exit 0
fi
for lock in "$STATE/progress-deck.lock" "$STATE/morning.lock"; do
  if [[ -d "$lock" ]] && [[ -z "$(find "$lock" -maxdepth 0 -mmin +240 2>/dev/null)" ]]; then
    say "${lock:t} is held - a run is in progress, standing down"
    exit 0
  fi
done
if [[ -z "$REPO" ]]; then
  say "no REPO in $ENV_FILE - run scripts/install-morning.sh"
  exit 1
fi
if [[ ! -x "$APP/Contents/MacOS/daydag-morning" ]]; then
  say "no $APP - run scripts/install-morning.sh, then scripts/install-progress-deck.sh"
  exit 1
fi
ATTEMPTS="$RUNS/progress-deck-$MON.attempts"
N=$(( $(cat "$ATTEMPTS" 2>/dev/null || echo 0) + 1 ))
if (( N > MAX )); then
  say "$MAX attempts this weekend already - giving up until next weekend"
  exit 0
fi
if [[ -n "${DAYDAG_GATE_DRY:-}" ]]; then
  say "dry: would open attempt $N headless in $APP -> $REPO/scripts/morning.sh --progress-deck"
  exit 0
fi
echo "$N" >"$ATTEMPTS"
say "attempt $N -> $APP -> $REPO/scripts/morning.sh --progress-deck (headless)"
exec /usr/bin/open -g -a "$APP" --args --progress-deck
