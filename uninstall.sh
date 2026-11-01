#!/bin/bash
# Removes nightblock and restores network settings. Run:  sudo ./uninstall.sh [--purge]
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
OWNER="${SUDO_USER:-elityre}"; launchctl bootout gui/$(id -u "$OWNER")/com.elityre.nightblock-cover 2>/dev/null; rm -f /Library/LaunchAgents/com.elityre.nightblock-cover.plist
for l in com.elityre.nightblock com.elityre.nightblock-proxy; do launchctl bootout system/$l 2>/dev/null; rm -f /Library/LaunchDaemons/$l.plist; done
[ -x /usr/local/bin/nightblock ] && /usr/bin/python3 /usr/local/bin/nightblock unblock-now
pfctl -a nightblock -F all 2>/dev/null
OWNER="${SUDO_USER:-elityre}"; launchctl bootout gui/$(id -u "$OWNER")/com.elityre.nightblock-menu 2>/dev/null; pkill -x NightBlockMenu 2>/dev/null
rm -f /Library/LaunchAgents/com.elityre.nightblock-menu.plist; rm -rf "/Applications/Night Block.app" "/Applications/Night Block Override.app"
echo "(remove 'Night Block Override' from the Dock by dragging it out)"
rm -f /etc/sudoers.d/nightblock /usr/local/bin/nightblock /etc/pf.anchors/nightblock
if [ "${1:-}" = "--purge" ]; then
  rm -rf "/Library/Application Support/nightblock" /Library/Logs/nightblock
  dscl . -delete /Users/_nightproxy 2>/dev/null
fi
echo "removed. (pf main ruleset still references an empty anchor until reboot; harmless)"
