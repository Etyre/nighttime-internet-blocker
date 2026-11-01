# nightblock

Blocks the internet on this Mac from **00:00 to 06:00** every day, with a short allowlist, and a
60-minute override you have to type out by hand.

## What is blocked / allowed

| | during the block |
|---|---|
| Websites, Filtered Twitter, Slack, mail sync, app updaters running as you | **blocked** (connections are refused immediately, nothing hangs) |
| Open browser tabs | stay open; they just won't reload until 06:00 |
| Anything on this Mac: localhost, `*.test`, `*.local`, and `/etc/hosts` loopback names such as Sleep Bandit at `sleepbandit.test:8777`, Task Tracker (8791), Roam local | works, in Chrome too (never covered) |
| Google Docs/Sheets in Chrome (daily checklist) | works, via the local proxy, allowlisted by hostname |
| Oura, Open-Meteo, Anthropic APIs (Sleep Bandit, Task Tracker's LLM, Claude Code) | work, from any app, by IP |
| Backblaze and macOS system updates (run as root) | work |
| DNS, NTP, Bonjour | work |
| Task Tracker's Toggl / Google Sheets sync (Python, not proxy-aware) | blocked until 06:00 |
| Claude Code's MCP connectors to claude.ai, telemetry | blocked; the model itself works |

`nightblock status` prints the live version of this list plus the hosts the proxy refused recently.
`nightblock check <host>` says what would happen to one host.

## Covering what is blocked

- **Chrome tabs**: the extension in `chrome-extension/` lays an opaque black panel over every tab whose host is
  not allowlisted while the block is on, and pauses any playing media. Tabs are not closed or reloaded; the
  panel lifts at 06:00 or on override. Load it once: `chrome://extensions` -> Developer mode -> Load unpacked
  -> pick the `chrome-extension` folder. It asks the local proxy for the state every 30 s.
- **Desktop apps**: `cover_apps` in the config (Filtered Twitter, Slack, Discord, WhatsApp, Signal by default)
  are hidden every 5 s while blocked. They keep running; Cmd-Tab shows one for a few seconds at most. The first
  time, macOS asks to let python3 control System Events: click OK.

## Menu bar and Dock

`Night Block.app` sits in the menu bar: an outlined moon while the internet is open, a filled moon with "zz"
while blocked. Its menu shows the state and the time of the next change, and has **Override…**, which opens
Terminal straight into `nightblock override`. `Night Block Override.app` in the Dock does the same thing with
one click. The menu's **Exceptions** submenu lists the allowed websites, the API hosts open to every app, and the
apps hidden during the block; click an entry to remove it or use "Add…" to add one. Changes go through the macOS
administrator-password dialog and take effect within a minute. Both are built by `menubar/build.sh` (needs Xcode) and installed by `install.sh`.

## Commands

```
nightblock status                 # anyone
nightblock check docs.google.com
nightblock denied                 # recently refused hostnames, for tuning the allowlist
nightblock override               # lift the block for 60 min (asks nothing else; you type the paragraph)
sudo nightblock allow HOST        # add a hostname pattern (fnmatch: *.example.com) to the proxy allowlist
sudo nightblock disallow HOST
sudo nightblock force-block 5     # test: block right now for 5 minutes
sudo nightblock force-off
sudo nightblock unblock-now       # emergency: open everything (daemon re-applies the schedule within 60 s)
```

Config: `/Library/Application Support/nightblock/config.json` (root-owned). Logs: `/Library/Logs/nightblock/`.
`overrides.log` keeps every override with its typed reason.

## The override

`nightblock override` shows a fixed paragraph plus four random words and reads your keystrokes one at a
time. Six keystrokes inside 150 ms is faster than any human types, so paste and Superwhisper dictation are
rejected and the attempt restarts. A wrong key just beeps (after three, it shows the next expected character).
Then you type a reason of at least 15 characters. The internet opens for 60 minutes and re-blocks itself.

## How it works

- `com.elityre.nightblock` (LaunchDaemon, every 60 s, root): decides blocked/open from the clock, the
  override file and the force file, and makes reality match. Because it is a reconciler, a Mac that was
  asleep at midnight or 06:00 catches up within a minute of waking, and `sudo pfctl -d` lasts a minute.
- Firewall: a `pf` anchor `nightblock` that blocks all outbound traffic except loopback, DNS/NTP/DHCP,
  link-local/multicast, sockets owned by root or by `_nightproxy`, and TCP 443 to the IPs of
  `direct_hosts` (re-resolved every minute; every IP seen in the last 36 h stays allowed).
- Proxy: `com.elityre.nightblock-proxy` (LaunchDaemon, user `_nightproxy`) on 127.0.0.1:8899. During the
  block the system HTTP/HTTPS proxy on every network service points at it, and it completes connections
  only to `allow_hosts` + `direct_hosts`. Previous proxy settings are saved and restored at 06:00.
- Override: `sudo nightblock override` is the one passwordless sudo rule, so the legitimate path is
  frictionless and every bypass is a deliberate, password-prompted act.

## Install / remove

```
sudo ./install.sh
sudo ./uninstall.sh [--purge]
```
