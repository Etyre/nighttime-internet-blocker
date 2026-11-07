# nightblock

Blocks the internet on this Mac from **00:00 to 06:00** every day, with a short allowlist, and a
60-minute override you have to type out by hand. Built 2026-10-31 because the block has to survive sleep and
reboots (so it is a reconciler, not a timer) and the override has to cost deliberate effort, while Backblaze,
macOS updates and the local tools (Sleep Bandit, Task Tracker, Roam) keep working. Claude Code is deliberately
not on the allowlist: it shares `api.anthropic.com` with Sleep Bandit and Task Tracker, and the firewall cannot
tell processes apart, so the host is closed to everyone at night. Do not add it back to `direct_hosts`.

## How it works

Three launchd jobs plus two small apps and a Chrome extension:

- `com.elityre.nightblock` (LaunchDaemon, every 60 s, root): decides blocked/open from the clock, the
  override file and the force file, and makes reality match. Because it is a reconciler, a Mac that was
  asleep at midnight or 06:00 catches up within a minute of waking, and `sudo pfctl -d` lasts a minute.
- Firewall: a `pf` anchor `nightblock` that blocks all outbound traffic except loopback, DNS/NTP/DHCP,
  link-local/multicast, sockets owned by root or by `_nightproxy`, and TCP 443 to the IPs of
  `direct_hosts` (re-resolved every minute; every IP seen in the last 36 h stays allowed).
- Proxy: `com.elityre.nightblock-proxy` (LaunchDaemon, user `_nightproxy`) on 127.0.0.1:8899. During the
  block the system HTTP/HTTPS proxy on every network service points at it, and it completes connections
  only to `allow_hosts` + `direct_hosts`. Previous proxy settings are saved and restored at 06:00. It also
  serves `http://127.0.0.1:8899/__nightblock__/state` (JSON) for the Chrome extension.
- Cover: `com.elityre.nightblock-cover` (LaunchAgent, your user) runs `nightblock cover-loop`, which hides
  `cover_apps` every 5 s while blocked.
- Override: `sudo nightblock override` is the one passwordless sudo rule, so the legitimate path is
  frictionless and every bypass is a deliberate, password-prompted act.

### What is blocked / allowed

| | during the block |
|---|---|
| Websites, Filtered Twitter, Slack, mail sync, app updaters running as you | **blocked** (connections are refused immediately, nothing hangs) |
| Open browser tabs | stay open; they just won't reload until 06:00 |
| Anything on this Mac: localhost, `*.test`, `*.local`, and `/etc/hosts` loopback names such as Sleep Bandit at `sleepbandit.test:8777`, Task Tracker (8791), Roam local | works, in Chrome too (never covered) |
| Google Docs/Sheets in Chrome (daily checklist) | works, via the local proxy, allowlisted by hostname |
| Oura and Open-Meteo APIs (Sleep Bandit's ring and weather data) | work, from any app, by IP |
| Backblaze and macOS system updates (run as root) | work |
| DNS, NTP, Bonjour | work |
| Task Tracker's Toggl / Google Sheets sync (Python, not proxy-aware) | blocked until 06:00 |
| Claude Code, and anything else on the Anthropic API (Sleep Bandit's night tagging, Task Tracker's LLM plans) | **blocked** until 06:00 |

`nightblock status` prints the live version of this list plus the hosts the proxy refused recently.
`nightblock check <host>` says what would happen to one host.

### Covering what is blocked

- **Chrome tabs**: the extension in `chrome-extension/` lays an opaque black panel over every tab whose host is
  not allowlisted while the block is on, and pauses any playing media. Tabs are not closed or reloaded; the
  panel lifts at 06:00 or on override. It asks the local proxy for the state every 30 s (and when a tab finishes
  loading); if the proxy is not running it treats everything as open.
- **Desktop apps**: `cover_apps` in the config (Filtered Twitter, Slack, Discord, WhatsApp, Signal by default)
  are hidden every 5 s while blocked. They keep running; Cmd-Tab shows one for a few seconds at most.

### Menu bar and Dock

`Night Block.app` sits in the menu bar: an outlined moon while the internet is open, a filled moon with "zz"
while blocked (a moon in a circle means nightblock is not installed or not responding). Its menu shows the state
and the time of the next change, and has **Override…**, which opens Terminal straight into `nightblock override`.
`Night Block Override.app` in the Dock does the same thing with one click. The menu's **Exceptions** submenu
lists the allowed websites, the API hosts open to every app, and the apps hidden during the block; click an
entry to remove it or use "Add…" to add one. Changes go through the macOS administrator-password dialog and take
effect within a minute. Text typed into an "Add…" field is saved as you type (UserDefaults) and comes back the
next time that dialog opens; it is cleared only once the entry was actually added. Both apps are built by
`menubar/build.sh` (needs Xcode) and installed by `install.sh`. The menu, the Dock app and the Terminal
launchers all open `.command` files in the config folder, so no Automation permission is needed.

### The override

`nightblock override` shows a fixed paragraph plus four random words and reads your keystrokes one at a
time. Six keystrokes inside 150 ms is faster than any human types, so paste and Superwhisper dictation are
rejected and the attempt restarts. A wrong key just beeps (after three, it shows the next expected character).
Then you type a reason of at least 15 characters. The internet opens for 60 minutes and re-blocks itself.
Every override is appended to `overrides.log` with its typed reason.

## Files

| File | Purpose |
|---|---|
| `nightblock/nightblock.py` | The whole tool: reconciler, pf rules, system-proxy switching, allowlist proxy, typed override, app cover loop, status/check/config subcommands. Installed as `/usr/local/bin/nightblock`. |
| `nightblock/config.example.json` | Shipped default config (committed). `install.sh` copies it on first install and merges missing keys on reinstall. A local `nightblock/config.json` (gitignored) is used instead if present. |
| `install.sh` | `sudo ./install.sh`: creates the `_nightproxy` user, installs the binary, config, logs, four launchd jobs, Terminal `.command` launchers, the menu bar and Dock apps, and the passwordless sudo rule for `override`. Idempotent. |
| `uninstall.sh` | `sudo ./uninstall.sh [--purge]`: unloads everything, opens the network, removes files; `--purge` also deletes config, logs and the service user. |
| `build.sh` | Top-level wrapper for `menubar/build.sh`. |
| `menubar/build.sh` | Builds `build/Night Block.app`, `build/Night Block Override.app` and `build/nightblock.icns`. Needs Xcode. |
| `menubar/NightBlockMenu.swift` | The menu bar app (Cocoa, one file). Polls `nightblock state-json` every 20 s; Exceptions submenu edits the config as admin. |
| `menubar/override.applescript` | Source of the Dock app: opens Terminal running the override. |
| `menubar/make_icon.swift` | Renders the moon-on-blue app icon as an `.iconset`. |
| `chrome-extension/manifest.json`, `background.js` | MV3 extension that covers non-allowlisted tabs while blocked. Loaded unpacked. |
| `build/` | Build output (gitignored). |

## Runtime locations

| What | Where |
|---|---|
| Installed program | `/usr/local/bin/nightblock` (root-owned copy of `nightblock/nightblock.py`) |
| Config | `/Library/Application Support/nightblock/config.json` (root-owned; `sudo nano` it or use `nightblock allow` / the menu). Changes apply within a minute. |
| State | same folder: `state.json` (what the daemon applied, saved proxy settings, resolved IPs), `override.json`, `force.json`, `pf-main.conf` (copy of `/etc/pf.conf` plus the anchor) |
| Terminal launchers | same folder: `override.command`, `status.command`, `denied.command`, `edit-config.command` |
| pf anchor | `/etc/pf.anchors/nightblock` (rewritten by the daemon; empty when open) |
| Logs | `/Library/Logs/nightblock/`: `nightblock.log` (daemon + config changes), `proxy.log` (DENIED lines, upstream failures), `overrides.log` (every override with its reason), `cover.log`, `daemon.stdout.log`, `daemon.stderr.log`, `proxy.stderr.log`. Every line nightblock writes is timestamped. |
| launchd | `/Library/LaunchDaemons/com.elityre.nightblock.plist`, `com.elityre.nightblock-proxy.plist`; `/Library/LaunchAgents/com.elityre.nightblock-cover.plist`, `com.elityre.nightblock-menu.plist` |
| Apps | `/Applications/Night Block.app`, `/Applications/Night Block Override.app` (also in the Dock) |
| sudoers | `/etc/sudoers.d/nightblock` (only `nightblock override` is passwordless) |
| Service user | `_nightproxy` (hidden, no shell; its sockets pass the firewall) |
| Menu app drafts | UserDefaults domain `com.elityre.nightblock.menu` |

The directories are system-wide (`/Library`, not `~/Library`) on purpose: the daemon runs as root and the config
must not be editable by the user without a password at night.

## Common tasks

```
nightblock status                 # anyone
nightblock check docs.google.com
nightblock denied                 # recently refused hostnames, for tuning the allowlist
nightblock override               # lift the block for 60 min (asks nothing else; you type the paragraph)
sudo nightblock allow HOST        # add a hostname pattern (fnmatch: *.example.com) to the proxy allowlist
sudo nightblock disallow HOST
sudo nightblock config-add direct_hosts HOST      # lists: allow_hosts | direct_hosts | cover_apps
sudo nightblock config-remove direct_hosts HOST
sudo nightblock force-block 5     # test: block right now for 5 minutes
sudo nightblock force-off
sudo nightblock unblock-now       # emergency: open everything (daemon re-applies the schedule within 60 s)
```

Install, update, remove (all from Terminal.app, see Gotchas):

```
sudo ./install.sh                 # first install and every update; restarts the daemons and the menu app
sudo ./uninstall.sh [--purge]
./build.sh                        # rebuild the menu bar / Dock apps into build/ (install.sh builds if missing)
```

Test without waiting for midnight:

```
sudo nightblock force-block 3     # real block for 3 min: check a tab goes black, Slack hides, status says BLOCKED
sudo nightblock force-off
# no-root dry run of the read-only commands against a scratch config (nothing on the system is touched):
D=$(mktemp -d); cp nightblock/config.example.json $D/config.json
NIGHTBLOCK_APP_DIR=$D NIGHTBLOCK_LOG_DIR=$D /usr/bin/python3 nightblock/nightblock.py status
NIGHTBLOCK_APP_DIR=$D NIGHTBLOCK_LOG_DIR=$D /usr/bin/python3 nightblock/nightblock.py check api.anthropic.com
```

Logs and state:

```
tail -f /Library/Logs/nightblock/nightblock.log
tail -f /Library/Logs/nightblock/proxy.log          # DENIED host:port lines while blocked
cat /Library/Logs/nightblock/overrides.log
cat "/Library/Application Support/nightblock/state.json"
launchctl print system/com.elityre.nightblock | head
launchctl print gui/$(id -u)/com.elityre.nightblock-menu | grep -E 'state|pid'
```

Chrome extension: `chrome://extensions` -> Developer mode -> Load unpacked -> pick the `chrome-extension`
folder. After editing `background.js`, click the reload arrow on its card.

## Settings that matter

- **Chrome**: the cover extension must show as enabled in `chrome://extensions`. It is loaded unpacked into the
  Default profile only; other profiles are not covered.
- **System Settings -> Privacy & Security -> Automation**: the first time the cover loop runs, macOS asks to let
  `python3` control System Events; click OK, or apps will not be hidden.
- **System Settings -> Network -> (service) -> Proxies**: during the block, Web Proxy and Secure Web Proxy on
  every service point at 127.0.0.1:8899. nightblock sets and restores these itself; do not fix them by hand at
  night (the daemon re-applies within a minute anyway).
- **Dock**: `Night Block Override` is added once by `install.sh`; remove it by dragging it out.
- **sudoers**: `/etc/sudoers.d/nightblock` makes only `nightblock override` passwordless. Everything else needs
  the admin password.
- **Xcode** is needed to build the menu bar app (`swiftc`, `iconutil`, `osacompile`).

## Gotchas learned

- Claude Code and Sleep Bandit / Task Tracker all talk to `api.anthropic.com`, and pf only sees IPs and
  socket owners, not processes. Allowing the host for one allows it for all, so it is blocked for all. Do not
  add `api.anthropic.com`, `console.anthropic.com` or `platform.claude.com` back to `direct_hosts`.
- `install.sh` merges the shipped config into the installed one with "add missing keys only": it never removes
  an entry you (or an older config) added. Removing a host from `config.example.json` does nothing to the
  installed `/Library/Application Support/nightblock/config.json`; use `sudo nightblock config-remove ...`.
- Anything needing `sudo` must run in Terminal.app: Claude Code's shell has no tty for the password prompt.
  Open Terminal with `osascript -e 'tell app "Terminal" to do script "..."'`.
- `launchctl bootout` is asynchronous. `install.sh` waits for the old job to disappear before `bootstrap`;
  without that wait the new job silently never loads.
- Never restart a job with `pkill` alone: a silently failed pkill leaves the old code running. Use
  `launchctl bootout` / `bootstrap` (or `sudo ./install.sh`).
- The Claude Chrome connector dies during a block (claude.ai is blocked), so verify covers with `screencapture`
  or by eye, not through the browser tool.
- Only hostname granularity: allowing `docs.google.com` allows all of it. `nightblock denied` shows what the
  proxy refused so the allowlist can be tuned; patterns are fnmatch (`*.gstatic.com`, `clients?.google.com`).
- Python/Node/curl are not proxy-aware, so hosts in `allow_hosts` only work from Chrome and Electron apps.
  Anything a script needs must be in `direct_hosts`.
- The Dock applet shows the generic script icon unless `Assets.car` is deleted after `osacompile`
  (`menubar/build.sh` does this).
- `sudo pfctl -d` or editing the proxy settings by hand lasts at most a minute: the reconciler puts them back.
- The root daemon runs Apple's `/usr/bin/python3` (needs Command Line Tools), not Homebrew's, so a root process
  never executes user-writable code.
