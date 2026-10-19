#!/bin/zsh
# Install (or remove) the launchd job that runs scripts/morning.sh at 06:43 on
# weekdays. The plist is written to ~/Library/LaunchAgents with THIS machine's
# repo path, so no local path is committed (the repo is public).
#
#   scripts/install-morning.sh            # install or update, and load
#   scripts/install-morning.sh --remove   # unload and delete
#
# A Mac asleep at 06:43 runs the job when it wakes - launchd's calendar jobs
# catch up once, they do not stack. That is how 2026-10-15's brief ran at
# 23:08, and why 10/16 and 10/19 produced nothing: the lid was closed at 06:43.
# So the installer also sets a repeating wake (`pmset repeat wake MTWRF`, three
# minutes before the job; DAYDAG_WAKE=HH:MM:SS to change it, DAYDAG_WAKE= to
# skip). pmset needs sudo, so it prompts; a non-interactive install prints the
# command instead of failing. A scheduled wake is reliable with the lid open or
# an external display attached; a closed laptop may only dark-wake, which does
# not launch a terminal app. --remove cancels the repeat as well.
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
WAKE="${DAYDAG_WAKE-06:40:00}"
LABEL="com.daydag.morning"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TARGET="gui/$(id -u)"

launchctl bootout "$TARGET/$LABEL" 2>/dev/null || true
if [[ "${1:-}" == "--remove" ]]; then
  rm -f "$PLIST"
  echo "removed $LABEL"
  if pmset -g sched | grep -q "Repeating power events"; then
    if sudo -n true 2>/dev/null || [[ -t 0 ]]; then
      sudo pmset repeat cancel && echo "cancelled the repeating wake"
    else
      echo "run by hand to cancel the repeating wake:  sudo pmset repeat cancel"
    fi
  fi
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

if [[ -n "$WAKE" ]]; then
  # One repeating schedule per Mac: show what is there before replacing it.
  pmset -g sched | sed -n '/Repeating power events/,$p' | grep -v "^Scheduled" || true
  if sudo -n true 2>/dev/null || [[ -t 0 ]]; then
    if sudo pmset repeat wake MTWRF "$WAKE"; then
      echo "the Mac wakes weekdays at $WAKE so the 06:43 job runs (lid open or external display)"
    else
      echo "wake not set; run by hand:  sudo pmset repeat wake MTWRF $WAKE"
    fi
  else
    echo "wake not set (no tty for sudo); run by hand:  sudo pmset repeat wake MTWRF $WAKE"
  fi
fi
