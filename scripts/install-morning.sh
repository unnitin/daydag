#!/bin/zsh
# Install (or remove) the launchd job behind the scheduled morning. The plist
# is written to ~/Library/LaunchAgents with THIS machine's repo path, so no
# local path is committed (the repo is public).
#
#   scripts/install-morning.sh            # install or update, and load
#   scripts/install-morning.sh --remove   # unload and delete
#
# launchd does not run scripts/morning.sh directly. macOS privacy protection
# gives a bare launchd zsh no access to ~/Documents or iCloud Drive - the
# 2026-10-14 06:47 run died on "can't open input file" - so launchd runs a
# copy of scripts/morning-gate.sh placed in ~/.local/state/daydag, every 15
# minutes from 06:43 to 09:58 on weekdays. The gate opens the real run in a
# terminal app (DAYDAG_TERMINAL, default Ghostty), which already has access to
# both folders, and only when the Mac is awake inside the window and today's
# brief has not shipped. A Mac asleep at 06:43 therefore gets its brief at the
# first slot after it wakes, not at 23:08 (2026-10-15), and not at all if it
# sleeps past 10:00 (10/16, 10/19) - without sudo or a scheduled wake, which
# he does not want. DAYDAG_TERMINAL= (empty) runs zsh directly, which needs
# Full Disk Access for /bin/zsh.
set -eu
REPO="${0:A:h:h}"
TERMINAL="${DAYDAG_TERMINAL-Ghostty}"
LABEL="com.daydag.morning"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TARGET="gui/$(id -u)"
STATE="$HOME/.local/state/daydag"
GATE="$STATE/morning-gate.sh"

launchctl bootout "$TARGET/$LABEL" 2>/dev/null || true
if [[ "${1:-}" == "--remove" ]]; then
  rm -f "$PLIST" "$GATE"
  echo "removed $LABEL"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$STATE/runs"
# The gate and its two settings live outside ~/Documents, where a bare zsh can
# read them. REPO stays out of the repo: it is this machine's path.
cp "$REPO/scripts/morning-gate.sh" "$GATE"
chmod +x "$GATE"
printf 'REPO=%q\nTERMINAL=%q\n' "$REPO" "$TERMINAL" >"$STATE/morning.env"
days=""
for weekday in 1 2 3 4 5; do
  for slot in "6 43" "6 58" "7 13" "7 28" "7 43" "7 58" "8 13" "8 28" "8 43" "8 58" "9 13" "9 28" "9 43" "9 58"; do
    days+="    <dict><key>Weekday</key><integer>$weekday</integer><key>Hour</key><integer>${slot% *}</integer><key>Minute</key><integer>${slot#* }</integer></dict>
"
  done
done
PROGRAM="<string>/bin/zsh</string><string>$GATE</string>"
cat >"$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>$PROGRAM</array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartCalendarInterval</key>
  <array>
$days  </array>
  <key>StandardOutPath</key><string>$HOME/.local/state/daydag/runs/launchd.log</string>
  <key>StandardErrorPath</key><string>$HOME/.local/state/daydag/runs/launchd.log</string>
</dict>
</plist>
PLIST
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$TARGET" "$PLIST"
echo "installed $LABEL: weekdays 06:43-09:58 every 15 min -> $GATE -> $REPO/scripts/morning.sh (in ${TERMINAL:-a bare zsh})"
