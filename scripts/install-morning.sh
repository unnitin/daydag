#!/bin/zsh
# Install (or remove) the launchd job behind the scheduled morning. The plist
# is written to ~/Library/LaunchAgents with THIS machine's repo path, so no
# local path is committed (the repo is public).
#
#   scripts/install-morning.sh            # install or update, load, and probe
#   scripts/install-morning.sh --remove   # unload and delete
#
# launchd does not run scripts/morning.sh directly. macOS privacy protection
# gives a bare launchd zsh no access to ~/Documents or iCloud Drive - the
# 2026-10-14 06:47 run died on "can't open input file" - so launchd runs a copy
# of scripts/morning-gate.sh placed in ~/.local/state/daydag, every 15 minutes
# from 06:43 to 09:58 on weekdays, and the gate opens the real run headless, in
# "DayDAG Morning.app": a faceless app this installer builds beside the gate
# from scripts/morning-launcher.c. The app runs morning.sh as its child and
# waits, so macOS attributes the file access to the app, and the grant it asks
# for once - here, at install, with you at the keyboard - covers every morning
# after. No window, no terminal app (the Ghostty launch it replaces spawned no
# shell while the screen was locked and died when its window was closed), no
# sudo, no scheduled wake. A Mac asleep at 06:43 gets its brief at the first
# slot after it wakes, and not at all if it sleeps past 10:00.
set -eu
REPO="${0:A:h:h}"
LABEL="com.daydag.morning"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TARGET="gui/$(id -u)"
STATE="$HOME/.local/state/daydag"
GATE="$STATE/morning-gate.sh"
APP="$STATE/DayDAG Morning.app"
BIN="$APP/Contents/MacOS/daydag-morning"
PROBE="$STATE/morning.probe"

launchctl bootout "$TARGET/$LABEL" 2>/dev/null || true
if [[ "${1:-}" == "--remove" ]]; then
  rm -f "$PLIST" "$GATE" "$STATE/morning.env" "$PROBE"
  rm -rf "$APP"
  echo "removed $LABEL and $APP (its entry under System Settings > Privacy & Security > Files and Folders can be deleted there)"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$STATE/runs" "$APP/Contents/MacOS"
# The gate, the app and their one setting live outside ~/Documents, where a
# bare zsh can read them. REPO stays out of the repo: it is this machine's path.
cp "$REPO/scripts/morning-gate.sh" "$GATE"
chmod +x "$GATE"
printf 'REPO=%q\n' "$REPO" >"$STATE/morning.env"

# The app: an Info.plist that says "no UI", and the launcher as its executable.
# Rebuilt only when the C source changed, because the privacy grant is tied to
# the binary's hash and a rebuild asks for it again (the probe below does).
cat >"$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>DayDAG Morning</string>
  <key>CFBundleDisplayName</key><string>DayDAG Morning</string>
  <key>CFBundleIdentifier</key><string>$LABEL</string>
  <key>CFBundleExecutable</key><string>daydag-morning</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSBackgroundOnly</key><true/>
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST
printf 'APPL????' >"$APP/Contents/PkgInfo"
if [[ ! -x "$BIN" || "$REPO/scripts/morning-launcher.c" -nt "$BIN" ]]; then
  cc -O2 -Wall -o "$BIN" "$REPO/scripts/morning-launcher.c"
  echo "built $BIN"
fi

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
  <key>WorkingDirectory</key><string>$STATE</string>
  <key>StartCalendarInterval</key>
  <array>
$days  </array>
  <key>StandardOutPath</key><string>$STATE/runs/launchd.log</string>
  <key>StandardErrorPath</key><string>$STATE/runs/launchd.log</string>
</dict>
</plist>
PLIST
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$TARGET" "$PLIST"
echo "installed $LABEL: weekdays 06:43-09:58 every 15 min -> $GATE -> $APP -> $REPO/scripts/morning.sh (headless)"

# The probe: the app reads the repo, the vault and the event log, and asks
# claude for one word, exactly as a morning would - so the two privacy prompts
# (Documents folder, iCloud Drive) come now, while someone is here to allow
# them, and a headless 06:43 never waits on a dialog nobody sees.
rm -f "$PROBE"
echo "probing - if macOS asks, allow DayDAG Morning to access your Documents folder and iCloud Drive"
/usr/bin/open -W -g -a "$APP" --args --probe
if [[ -f "$PROBE" ]]; then
  cat "$PROBE"
  if grep -q "denied\|failed" "$PROBE"; then
    echo "fix: System Settings > Privacy & Security > Files and Folders > DayDAG Morning - allow Documents Folder and iCloud Drive, then rerun $0"
    exit 1
  fi
  echo "probe passed: the morning can run headless"
else
  echo "no probe result: the app could not read $REPO/scripts/morning.sh"
  echo "fix: System Settings > Privacy & Security > Files and Folders > DayDAG Morning - allow Documents Folder (and iCloud Drive), then rerun $0"
  echo "     if the app is not listed there, add it under Full Disk Access with the + button: $APP"
  exit 1
fi
