#!/bin/zsh
# Install (or remove) the launchd job that runs scripts/morning.sh at 06:43 on
# weekdays. The plist is written to ~/Library/LaunchAgents with THIS machine's
# repo path, so no local path is committed (the repo is public).
#
#   scripts/install-morning.sh            # install or update, and load
#   scripts/install-morning.sh --remove   # unload and delete
#
# A Mac asleep at 06:43 runs the job when it wakes - launchd's calendar jobs
# catch up once, they do not stack.
#
# The job opens the run in a terminal app (DAYDAG_TERMINAL, default Ghostty)
# rather than running /bin/zsh itself. macOS privacy protection gives a bare
# launchd zsh no access to ~/Documents or iCloud Drive - the 2026-10-14 06:47
# run died on "can't open input file" - while the terminal app he already uses
# for this repo has both, so no new permission is granted. The window closes
# when the run ends. DAYDAG_TERMINAL= (empty) runs zsh directly, which needs
# Full Disk Access for /bin/zsh.
set -eu
REPO="${0:A:h:h}"
TERMINAL="${DAYDAG_TERMINAL-Ghostty}"
LABEL="com.daydag.morning"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TARGET="gui/$(id -u)"

launchctl bootout "$TARGET/$LABEL" 2>/dev/null || true
if [[ "${1:-}" == "--remove" ]]; then
  rm -f "$PLIST"
  echo "removed $LABEL"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.local/state/daydag/runs"
days=""
for weekday in 1 2 3 4 5; do
  days+="    <dict><key>Weekday</key><integer>$weekday</integer><key>Hour</key><integer>6</integer><key>Minute</key><integer>43</integer></dict>
"
done
if [[ -n "$TERMINAL" ]]; then
  PROGRAM="<string>/usr/bin/open</string><string>-na</string><string>$TERMINAL</string><string>--args</string><string>-e</string><string>$REPO/scripts/morning.sh</string>"
else
  PROGRAM="<string>/bin/zsh</string><string>-lc</string><string>$REPO/scripts/morning.sh</string>"
fi
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
echo "installed $LABEL: weekdays 06:43 -> $REPO/scripts/morning.sh (in ${TERMINAL:-a bare zsh})"
