#!/bin/bash
# Installs nightblock. Run:  sudo ./install.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="/Library/Application Support/nightblock"
LOG_DIR="/Library/Logs/nightblock"
BIN=/usr/local/bin/nightblock
PROXY_USER=_nightproxy
OWNER="${SUDO_USER:-elityre}"

load_daemon() {  # bootout is asynchronous; wait for it to finish, then bootstrap and verify
  local domain="$1" label="$2" plist="$3" i
  launchctl bootout "$domain/$label" 2>/dev/null || true
  for i in 1 2 3 4 5 6 7 8 9 10; do launchctl print "$domain/$label" >/dev/null 2>&1 || break; sleep 1; done
  for i in 1 2 3; do
    launchctl bootstrap "$domain" "$plist" 2>/dev/null || true
    sleep 1
    launchctl print "$domain/$label" >/dev/null 2>&1 && { echo "loaded $label"; return 0; }
  done
  echo "WARNING: $label did not load; try: sudo launchctl bootstrap $domain $plist"; return 1
}


# 1. Python: prefer Apple's (root daemon should not run user-writable Homebrew code)
if xcode-select -p >/dev/null 2>&1 && /usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
  PY=/usr/bin/python3
else
  PY="$(command -v python3)"; echo "warning: using $PY"
fi
echo "python: $PY"

# 2. Service user for the proxy (pf lets only this user's and root's sockets out)
if ! dscl . -read /Users/$PROXY_USER >/dev/null 2>&1; then
  uid=300; while dscl . -list /Users UniqueID | awk '{print $2}' | grep -qx "$uid"; do uid=$((uid+1)); done
  dscl . -create /Users/$PROXY_USER
  dscl . -create /Users/$PROXY_USER UniqueID "$uid"
  dscl . -create /Users/$PROXY_USER PrimaryGroupID 20
  dscl . -create /Users/$PROXY_USER UserShell /usr/bin/false
  dscl . -create /Users/$PROXY_USER RealName "nightblock proxy"
  dscl . -create /Users/$PROXY_USER NFSHomeDirectory /var/empty
  dscl . -create /Users/$PROXY_USER IsHidden 1
  echo "created user $PROXY_USER (uid $uid)"
fi

# 3. Files
# The shipped defaults are nightblock/config.example.json (committed). A local nightblock/config.json
# (gitignored) wins if present. On reinstall only missing keys are added; existing lists are left alone.
SEED="$HERE/nightblock/config.example.json"
[ -f "$HERE/nightblock/config.json" ] && SEED="$HERE/nightblock/config.json"
mkdir -p "$APP_DIR" "$LOG_DIR"
chmod 755 "$APP_DIR" "$LOG_DIR"
if [ -f "$APP_DIR/config.json" ]; then
  "$PY" - "$SEED" "$APP_DIR/config.json" <<'MERGE'
import json, sys
new, cur_p = json.load(open(sys.argv[1])), sys.argv[2]
cur = json.load(open(cur_p))
for k, v in new.items():
    cur.setdefault(k, v)
json.dump(cur, open(cur_p, "w"), indent=2)
MERGE
else
  cp "$SEED" "$APP_DIR/config.json"
fi
chmod 644 "$APP_DIR/config.json"
install -m 755 -o root -g wheel "$HERE/nightblock/nightblock.py" "$BIN"
touch "$LOG_DIR/nightblock.log" "$LOG_DIR/overrides.log" "$LOG_DIR/proxy.log" "$LOG_DIR/proxy.stderr.log"
chmod 644 "$LOG_DIR"/*.log; chmod 666 "$LOG_DIR/nightblock.log"
chown $PROXY_USER "$LOG_DIR/proxy.log" "$LOG_DIR/proxy.stderr.log"
# the per-minute reconciler writes state.json; the proxy only reads it
chmod 644 "$APP_DIR"/*.json 2>/dev/null || true

# 4. launchd daemons
cat > /Library/LaunchDaemons/com.elityre.nightblock.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.elityre.nightblock</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$BIN</string><string>reconcile</string></array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$LOG_DIR/daemon.stdout.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/daemon.stderr.log</string>
</dict></plist>
PLIST
cat > /Library/LaunchDaemons/com.elityre.nightblock-proxy.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.elityre.nightblock-proxy</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$BIN</string><string>proxy</string></array>
  <key>UserName</key><string>$PROXY_USER</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/proxy.stderr.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/proxy.stderr.log</string>
</dict></plist>
PLIST
cat > /Library/LaunchAgents/com.elityre.nightblock-cover.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.elityre.nightblock-cover</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$BIN</string><string>cover-loop</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/cover.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/cover.log</string>
</dict></plist>
PLIST
chmod 644 /Library/LaunchAgents/com.elityre.nightblock-cover.plist; chown root:wheel /Library/LaunchAgents/com.elityre.nightblock-cover.plist
touch "$LOG_DIR/cover.log"; chown "$OWNER" "$LOG_DIR/cover.log"
OWNER_UID="$(id -u "$OWNER")"
load_daemon gui/$OWNER_UID com.elityre.nightblock-cover /Library/LaunchAgents/com.elityre.nightblock-cover.plist || echo "cover agent will start at next login"
chmod 644 /Library/LaunchDaemons/com.elityre.nightblock*.plist
chown root:wheel /Library/LaunchDaemons/com.elityre.nightblock*.plist
for l in com.elityre.nightblock-proxy com.elityre.nightblock; do
  load_daemon system $l /Library/LaunchDaemons/$l.plist || true
done

# 4b. Terminal launchers (opened by the menu bar app and the Dock app; no Automation permission needed)
for c in override status denied; do
  printf '#!/bin/bash\nclear\n/usr/local/bin/nightblock %s\n' "$c" > "$APP_DIR/$c.command"
  chmod 755 "$APP_DIR/$c.command"
done
printf '#!/bin/bash\nclear\necho "Editing the nightblock config (nano: ctrl-O to save, ctrl-X to exit). Changes apply within a minute."\nsudo nano "%s/config.json"\n' "$APP_DIR" > "$APP_DIR/edit-config.command"
chmod 755 "$APP_DIR/edit-config.command"

# 4c. Menu bar app + Dock app (built by menubar/build.sh; needs Xcode, so build as the user if missing)
if [ ! -d "$HERE/build/Night Block.app" ]; then
  sudo -u "$OWNER" "$HERE/menubar/build.sh" || echo "warning: could not build the menu bar / Dock apps"
fi
if [ -d "$HERE/build/Night Block.app" ]; then
  pkill -x NightBlockMenu 2>/dev/null || true
  rm -rf "/Applications/Night Block.app" "/Applications/Night Block Override.app"
  cp -R "$HERE/build/Night Block.app" "$HERE/build/Night Block Override.app" /Applications/
  chown -R root:wheel "/Applications/Night Block.app" "/Applications/Night Block Override.app"
  cat > /Library/LaunchAgents/com.elityre.nightblock-menu.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.elityre.nightblock-menu</string>
  <key>ProgramArguments</key><array><string>/Applications/Night Block.app/Contents/MacOS/NightBlockMenu</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
</dict></plist>
PLIST
  chmod 644 /Library/LaunchAgents/com.elityre.nightblock-menu.plist; chown root:wheel /Library/LaunchAgents/com.elityre.nightblock-menu.plist
  load_daemon gui/$(id -u "$OWNER") com.elityre.nightblock-menu /Library/LaunchAgents/com.elityre.nightblock-menu.plist || true
  # Dock: add the override app once
  if ! sudo -u "$OWNER" defaults read com.apple.dock persistent-apps 2>/dev/null | grep -q "Night Block Override.app"; then
    sudo -u "$OWNER" defaults write com.apple.dock persistent-apps -array-add \
      '<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>file:///Applications/Night%20Block%20Override.app/</string><key>_CFURLStringType</key><integer>15</integer></dict></dict></dict>'
    sudo -u "$OWNER" killall Dock 2>/dev/null || true
    echo "added Night Block Override to the Dock"
  fi
fi

# 5. sudoers: only "nightblock override" is passwordless; everything else needs the admin password
echo "$OWNER ALL=(root) NOPASSWD: $BIN override" > /etc/sudoers.d/nightblock.tmp
if visudo -cf /etc/sudoers.d/nightblock.tmp >/dev/null; then
  mv /etc/sudoers.d/nightblock.tmp /etc/sudoers.d/nightblock; chmod 440 /etc/sudoers.d/nightblock
else
  rm -f /etc/sudoers.d/nightblock.tmp; echo "sudoers entry failed validation; override will ask for a password"
fi

sleep 2
echo; echo "installed."; echo
"$PY" "$BIN" status
