#!/bin/zsh
# Install (or remove) the launchd job behind the weekend progress-deck refresh.
# It reuses "DayDAG Morning.app" and its privacy grant, so install the morning
# first (scripts/install-morning.sh); this adds only a gate and a plist, and
# asks macOS for nothing new. The plist is written with THIS machine's paths,
# so no local path is committed (the repo is public).
#
#   scripts/install-progress-deck.sh            # install or update, load, and probe
#   scripts/install-progress-deck.sh --remove   # unload and delete
set -eu
REPO="${0:A:h:h}"
LABEL="com.daydag.progress-deck"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TARGET="gui/$(id -u)"
STATE="$HOME/.local/state/daydag"
GATE="$STATE/progress-deck-gate.sh"
APP="$STATE/DayDAG Morning.app"

launchctl bootout "$TARGET/$LABEL" 2>/dev/null || true
if [[ "${1:-}" == "--remove" ]]; then
  rm -f "$PLIST" "$GATE"
  echo "removed $LABEL (the morning app and its job are untouched)"
  exit 0
fi
if [[ ! -x "$APP/Contents/MacOS/daydag-morning" || ! -f "$STATE/morning.env" ]]; then
  echo "no $APP yet - run scripts/install-morning.sh first; this job borrows its grant" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$STATE/runs"
cp "$REPO/scripts/progress-deck-gate.sh" "$GATE"
chmod +x "$GATE"

# launchd numbers Saturday 6 and Sunday 0. Every 30 minutes, 07:13 to 12:43.
slots=""
for weekday in 6 0; do
  for hour in 7 8 9 10 11 12; do
    for minute in 13 43; do
      slots+="    <dict><key>Weekday</key><integer>$weekday</integer><key>Hour</key><integer>$hour</integer><key>Minute</key><integer>$minute</integer></dict>
"
    done
  done
done
cat >"$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/zsh</string><string>$GATE</string></array>
  <key>WorkingDirectory</key><string>$STATE</string>
  <key>StartCalendarInterval</key>
  <array>
$slots  </array>
  <key>StandardOutPath</key><string>$STATE/runs/launchd.log</string>
  <key>StandardErrorPath</key><string>$STATE/runs/launchd.log</string>
</dict>
</plist>
PLIST
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$TARGET" "$PLIST"
echo "installed $LABEL: sat 07:13-12:43 every 30 min, sun catch-up -> $GATE -> $APP -> $REPO/scripts/morning.sh --progress-deck (headless)"

echo "probe:"
zsh "$REPO/scripts/progress-deck.sh" --probe | sed 's/^/  /'
DAYDAG_GATE_DRY=1 DAYDAG_GATE_DOW=6 DAYDAG_GATE_NOW=0813 zsh "$GATE"
