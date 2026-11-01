#!/usr/bin/env python3
"""
nightblock - a midnight-to-6am internet block for macOS.

Subcommands
  status              what is blocked right now, override/force state, proxy health, recent denials
  check <host>        would <host> be reachable during the block, and how
  denied              recent hosts the proxy refused (use to tune the allowlist)
  override            lift the block for a while; you must type the paragraph by hand
  reconcile           (root, run by launchd every minute) enforce the state the clock says
  proxy               (root->_nightproxy, run by launchd) the hostname-allowlist proxy
  cover-loop          (your user, run by launchd) hides cover_apps every 5 s while blocked
  state-json          one JSON line with the current state (used by the menu bar app)
  allow <host>        (root) add a hostname pattern to allow_hosts
  disallow <host>     (root) remove it
  config-add <list> <value>      (root) list = allow_hosts | direct_hosts | cover_apps
  config-remove <list> <value>   (root)
  force-block <min>   (root) block now for <min> minutes regardless of the clock, for testing
  force-off           (root) cancel force-block
  unblock-now         (root) drop the block and restore network settings (daemon re-applies within a minute)

Mechanics: a pf anchor blocks all outbound traffic except loopback, DNS/NTP/DHCP, link-local and
multicast, sockets owned by root (Backblaze, system updaters), sockets owned by _nightproxy, and
TCP/443 to the resolved IPs of direct_hosts. The system HTTP/HTTPS proxy is pointed at the local
allowlist proxy, so Chrome and Electron apps can still reach allow_hosts by name.
"""
import asyncio
import datetime as dt
import fnmatch
import ipaddress
import json
import os
import pwd
import random
import re
import shutil
import socket
import subprocess
import sys
import termios
import time
import tty
from collections import deque

APP_DIR = os.environ.get("NIGHTBLOCK_APP_DIR", "/Library/Application Support/nightblock")
LOG_DIR = os.environ.get("NIGHTBLOCK_LOG_DIR", "/Library/Logs/nightblock")
CONFIG = os.path.join(APP_DIR, "config.json")
STATE = os.path.join(APP_DIR, "state.json")
OVERRIDE = os.path.join(APP_DIR, "override.json")
FORCE = os.path.join(APP_DIR, "force.json")
MAIN_CONF = os.path.join(APP_DIR, "pf-main.conf")
ANCHOR = "nightblock"
ANCHOR_FILE = "/etc/pf.anchors/nightblock"
BIN = "/usr/local/bin/nightblock"
PROXY_USER = "_nightproxy"
LOG = os.path.join(LOG_DIR, "nightblock.log")
PROXY_LOG = os.path.join(LOG_DIR, "proxy.log")
OVERRIDE_LOG = os.path.join(LOG_DIR, "overrides.log")
RESOLVED_TTL = 36 * 3600  # keep an IP in the firewall table this long after it was last seen

PARAGRAPH = (
    "It is after midnight and I am turning the internet back on. I have thought about why I want this "
    "and the reason is not boredom, avoidance, or habit. If I cannot name a concrete reason that my "
    "morning self would endorse, I should close this and go to sleep."
)

# ----------------------------------------------------------------------------- helpers

def log(msg, path=LOG):
    line = "%s %s\n" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(path, "a") as f:
            f.write(line)
    except OSError:
        pass
    if os.environ.get("NIGHTBLOCK_VERBOSE"):
        sys.stderr.write(line)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


_cfg_cache = {"mtime": None, "data": None}


def config():
    try:
        m = os.stat(CONFIG).st_mtime
    except OSError:
        m = None
    if m != _cfg_cache["mtime"] or _cfg_cache["data"] is None:
        data = read_json(CONFIG, {}) or {}
        data.setdefault("block_start", "00:00")
        data.setdefault("block_end", "06:00")
        data.setdefault("override_minutes", 60)
        data.setdefault("proxy_port", 8899)
        data.setdefault("allow_root_processes", True)
        data.setdefault("allow_hosts", [])
        data.setdefault("direct_hosts", [])
        data.setdefault("cover_apps", [])
        _cfg_cache.update(mtime=m, data=data)
    return _cfg_cache["data"]


def parse_hhmm(s):
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def in_window(now=None):
    c = config()
    now = now or dt.datetime.now()
    t = now.time()
    start, end = parse_hhmm(c["block_start"]), parse_hhmm(c["block_end"])
    if start < end:
        return start <= t < end
    return t >= start or t < end


def next_boundary(now=None):
    """Datetime of the next block start or end."""
    c = config()
    now = now or dt.datetime.now()
    cands = []
    for s in (c["block_start"], c["block_end"]):
        t = parse_hhmm(s)
        d = dt.datetime.combine(now.date(), t)
        if d <= now:
            d += dt.timedelta(days=1)
        cands.append(d)
    return min(cands)


def active_until(path):
    d = read_json(path)
    if not d:
        return None
    try:
        until = dt.datetime.fromisoformat(d["until"])
    except (KeyError, ValueError):
        return None
    return until if until > dt.datetime.now() else None


def desired_state():
    """'blocked' or 'open', plus the reason."""
    ov = active_until(OVERRIDE)
    fo = active_until(FORCE)
    if ov:
        return "open", "override until %s" % ov.strftime("%H:%M")
    if fo:
        return "blocked", "force-block until %s" % fo.strftime("%H:%M")
    if in_window():
        return "blocked", "inside block window %s-%s" % (config()["block_start"], config()["block_end"])
    return "open", "outside block window"


_local_cache = {}


def loopback_hosts_from_etc_hosts():
    """Names that /etc/hosts points at 127.x or ::1 (e.g. sleepbandit.test)."""
    names = set()
    try:
        for line in open("/etc/hosts"):
            line = line.split("#", 1)[0].split()
            if len(line) >= 2 and (line[0].startswith("127.") or line[0] == "::1"):
                names.update(n.lower() for n in line[1:])
    except OSError:
        pass
    return sorted(names)


def is_local_host(host):
    """True for anything that lives on this Mac: localhost, *.local/*.localhost/*.test, and any name
    that resolves only to loopback addresses (/etc/hosts entries such as sleepbandit.test)."""
    host = host.lower().rstrip(".")
    if host in ("localhost", "127.0.0.1", "::1") or host.startswith("127."):
        return True
    if host.endswith((".local", ".localhost", ".test")):
        return True
    now = time.time()
    hit = _local_cache.get(host)
    if hit and now - hit[1] < 60:
        return hit[0]
    try:
        addrs = {sa[0] for _, _, _, _, sa in socket.getaddrinfo(host, 80, proto=socket.IPPROTO_TCP)}
        local = bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_loopback for a in addrs)
    except (socket.gaierror, ValueError):
        local = False
    _local_cache[host] = (local, now)
    return local


def host_allowed_by_proxy(host):
    host = host.lower().rstrip(".")
    if is_local_host(host):
        return True
    c = config()
    for pat in list(c["allow_hosts"]) + list(c["direct_hosts"]):
        if fnmatch.fnmatchcase(host, pat.lower()):
            return True
    return False


def run(cmd, check=False, quiet=True):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("%s failed (%d): %s" % (" ".join(cmd), r.returncode, r.stderr.strip()))
    return r


def need_root():
    if os.geteuid() != 0:
        sys.exit("this command needs root: sudo nightblock %s" % " ".join(sys.argv[1:]))


# ----------------------------------------------------------------------------- pf

def proxy_uid():
    try:
        return pwd.getpwnam(PROXY_USER).pw_uid
    except KeyError:
        return None


def resolve_direct_hosts(state):
    """Union of every IP seen for direct_hosts within RESOLVED_TTL."""
    now = time.time()
    seen = state.setdefault("resolved", {})
    for host in config()["direct_hosts"]:
        entry = seen.setdefault(host, {})
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            for fam, _, _, _, sa in infos:
                entry[sa[0]] = now
        except socket.gaierror:
            pass
        for ip in [ip for ip, ts in entry.items() if now - ts > RESOLVED_TTL]:
            del entry[ip]
    for host in [h for h in seen if h not in config()["direct_hosts"]]:
        del seen[host]
    ips = sorted({ip for e in seen.values() for ip in e})
    return ips


def anchor_rules(ips):
    c = config()
    uid = proxy_uid()
    lines = [
        "# generated by nightblock - do not edit",
        "table <night_allow> persist { %s }" % " ".join(ips) if ips else "table <night_allow> persist",
        "pass quick on lo0 all",
        "pass in quick all",
        "pass out quick proto udp from any to any port { 53 67 68 123 }",
        "pass out quick proto tcp from any to any port 53",
        "pass out quick from any to { 224.0.0.0/4 169.254.0.0/16 }",
        "pass out quick inet6 from any to { ff00::/8 fe80::/10 }",
    ]
    if uid is not None:
        lines.append("pass out quick all user = %d" % uid)
    if c["allow_root_processes"]:
        lines.append("pass out quick all user = 0")
    lines.append("pass out quick proto tcp from any to <night_allow> port 443")
    lines.append("block return out quick all")
    return "\n".join(lines) + "\n"


def pf_enabled():
    r = run(["pfctl", "-s", "info"])
    return "Status: Enabled" in r.stdout


def pf_anchor_referenced():
    r = run(["pfctl", "-sr"])
    return 'anchor "%s"' % ANCHOR in r.stdout


def pf_anchor_rules_loaded():
    r = run(["pfctl", "-a", ANCHOR, "-sr"])
    return "block return out quick" in r.stdout


def pf_ensure_main():
    if pf_anchor_referenced():
        return
    with open("/etc/pf.conf") as f:
        base = f.read()
    extra = '\nanchor "%s"\nload anchor "%s" from "%s"\n' % (ANCHOR, ANCHOR, ANCHOR_FILE)
    if not os.path.exists(ANCHOR_FILE):
        with open(ANCHOR_FILE, "w") as f:
            f.write("# nightblock anchor (empty = nothing blocked)\n")
    with open(MAIN_CONF, "w") as f:
        f.write(base + extra)
    r = run(["pfctl", "-f", MAIN_CONF])
    log("loaded main ruleset with %s anchor (rc=%d) %s" % (ANCHOR, r.returncode, r.stderr.strip().replace("\n", " | ")))


def pf_apply_block(state):
    ips = resolve_direct_hosts(state)
    rules = anchor_rules(ips)
    pf_ensure_main()
    if not pf_enabled():
        state["we_enabled_pf"] = True
        r = run(["pfctl", "-e"])
        log("pf enabled (rc=%d)" % r.returncode)
    old = read_json(STATE, {}) or {}
    if rules != state.get("anchor_rules") or not pf_anchor_rules_loaded():
        with open(ANCHOR_FILE, "w") as f:
            f.write(rules)
        r = run(["pfctl", "-a", ANCHOR, "-f", ANCHOR_FILE])
        if r.returncode != 0:
            log("ERROR loading anchor: %s" % r.stderr.strip())
        else:
            state["anchor_rules"] = rules
            log("anchor loaded with %d direct IPs" % len(ips))


def pf_release(state):
    if pf_anchor_referenced() and pf_anchor_rules_loaded():
        run(["pfctl", "-a", ANCHOR, "-F", "all"])
        with open(ANCHOR_FILE, "w") as f:
            f.write("# nightblock anchor (empty = nothing blocked)\n")
        log("anchor flushed")
    state.pop("anchor_rules", None)
    if state.get("we_enabled_pf") and pf_enabled():
        run(["pfctl", "-d"])
        log("pf disabled (we had enabled it)")
    state["we_enabled_pf"] = False


# ----------------------------------------------------------------------------- system proxy

def network_services():
    r = run(["networksetup", "-listallnetworkservices"])
    out = []
    for line in r.stdout.splitlines()[1:]:
        line = line.strip()
        if line and not line.startswith("*"):
            out.append(line)
    return out


def get_proxy(service, kind):
    r = run(["networksetup", "-get%sproxy" % kind, service])
    d = {}
    for line in r.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip()
    return {"enabled": d.get("Enabled") == "Yes", "server": d.get("Server", ""), "port": d.get("Port", "0")}


def proxy_set_all(state):
    port = str(config()["proxy_port"])
    saved = state.setdefault("saved_proxy", {})
    for svc in network_services():
        for kind in ("web", "secure"):
            cur = get_proxy(svc, kind)
            key = "%s|%s" % (svc, kind)
            if key not in saved:
                saved[key] = cur
            if not (cur["enabled"] and cur["server"] == "127.0.0.1" and cur["port"] == port):
                run(["networksetup", "-set%sproxy" % kind, svc, "127.0.0.1", port])
                run(["networksetup", "-set%sproxystate" % kind, svc, "on"])


def proxy_restore_all(state):
    saved = state.get("saved_proxy") or {}
    port = str(config()["proxy_port"])
    for svc in network_services():
        for kind in ("web", "secure"):
            cur = get_proxy(svc, kind)
            if not (cur["enabled"] and cur["server"] == "127.0.0.1" and cur["port"] == port):
                continue  # not ours; leave alone
            prev = saved.get("%s|%s" % (svc, kind))
            if prev and prev["enabled"] and prev["server"]:
                run(["networksetup", "-set%sproxy" % kind, svc, prev["server"], prev["port"]])
            else:
                run(["networksetup", "-set%sproxystate" % kind, svc, "off"])
    state["saved_proxy"] = {}


# ----------------------------------------------------------------------------- reconcile

def reconcile(force_open=False):
    need_root()
    os.makedirs(APP_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    state = read_json(STATE, {}) or {}
    if force_open:
        desired, why = "open", "unblock-now"
    else:
        desired, why = desired_state()
    prev = state.get("state")
    if desired == "blocked":
        pf_apply_block(state)
        proxy_set_all(state)
        if prev != "blocked":
            run(["pfctl", "-F", "states"])
            log("BLOCKED (%s); flushed connection states" % why)
            state["since"] = dt.datetime.now().isoformat(timespec="seconds")
    else:
        pf_release(state)
        proxy_restore_all(state)
        if prev != "open":
            log("OPEN (%s)" % why)
            state["since"] = dt.datetime.now().isoformat(timespec="seconds")
    state["state"] = desired
    state["why"] = why
    state["last_run"] = dt.datetime.now().isoformat(timespec="seconds")
    write_json(STATE, state)


# ----------------------------------------------------------------------------- proxy server

_denied_recent = {}


def proxy_log(msg):
    log(msg, PROXY_LOG)


async def _pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


def _http_response(status, body_html):
    body = body_html.encode()
    head = ("HTTP/1.1 %s\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: %d\r\n"
            "Connection: close\r\n\r\n" % (status, len(body)))
    return head.encode() + body


async def _handle(reader, writer):
    peer = writer.get_extra_info("peername")
    try:
        req = await asyncio.wait_for(reader.readline(), 30)
        if not req:
            writer.close()
            return
        parts = req.decode("latin1").split()
        if len(parts) < 3:
            writer.write(_http_response("400 Bad Request", "bad request"))
            await writer.drain()
            writer.close()
            return
        method, target, ver = parts[0], parts[1], parts[2]
        headers = []
        while True:
            line = await asyncio.wait_for(reader.readline(), 30)
            if line in (b"\r\n", b"\n", b""):
                break
            headers.append(line)
        if method.upper() == "CONNECT":
            host, _, port = target.rpartition(":")
            port = int(port) if port.isdigit() else 443
            path = None
        elif target.startswith("/__nightblock__/"):
            st, why = desired_state()
            until = (active_until(FORCE) or next_boundary()) if st == "blocked" else next_boundary()
            c = config()
            body = json.dumps({"state": st, "why": why, "until": until.strftime("%-I:%M %p"),
                               "allow": list(c["allow_hosts"]) + list(c["direct_hosts"]),
                               "local": loopback_hosts_from_etc_hosts()}).encode()
            writer.write(("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n"
                          "Cache-Control: no-store\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body)).encode() + body)
            await writer.drain()
            writer.close()
            return
        else:
            m = re.match(r"^[a-zA-Z]+://([^/]+)(/.*)?$", target)
            if not m:
                writer.write(_http_response("400 Bad Request", "absolute URL required"))
                await writer.drain()
                writer.close()
                return
            hostport, path = m.group(1), m.group(2) or "/"
            if hostport.startswith("["):
                host, _, port = hostport.rpartition(":")
                port = int(port) if port.isdigit() else 80
            elif ":" in hostport:
                host, port = hostport.rsplit(":", 1)
                port = int(port)
            else:
                host, port = hostport, 80
        host = host.strip("[]").lower()

        state, _ = desired_state()
        allowed = state == "open" or host_allowed_by_proxy(host)
        if not allowed:
            now = time.time()
            if now - _denied_recent.get(host, 0) > 60:
                _denied_recent[host] = now
                proxy_log("DENIED %s:%d" % (host, port))
            until = (active_until(FORCE) or next_boundary()).strftime("%-I:%M %p")
            writer.write(_http_response(
                "403 Forbidden",
                "<html><body style='font-family:-apple-system;margin:3em'><h2>Night block</h2>"
                "<p><b>%s</b> is off the allowlist until %s.</p>"
                "<p>Go to sleep. (To open the internet anyway: <code>nightblock override</code>)</p></body></html>"
                % (host, until)))
            await writer.drain()
            writer.close()
            return

        try:
            ur, uw = await asyncio.wait_for(asyncio.open_connection(host, port), 20)
        except (OSError, asyncio.TimeoutError) as e:
            proxy_log("UPSTREAM-FAIL %s:%d %s" % (host, port, e))
            writer.write(_http_response("502 Bad Gateway", "could not reach %s" % host))
            await writer.drain()
            writer.close()
            return

        if path is None:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
        else:
            out = ["%s %s %s\r\n" % (method, path, ver)]
            for h in headers:
                if h.lower().startswith((b"proxy-connection:", b"connection:")):
                    continue
                out.append(h.decode("latin1"))
            out.append("Connection: close\r\n\r\n")
            uw.write("".join(out).encode("latin1"))
            await uw.drain()
        await asyncio.gather(_pipe(reader, uw), _pipe(ur, writer))
    except (asyncio.TimeoutError, ConnectionError, OSError):
        try:
            writer.close()
        except Exception:
            pass
    except Exception as e:  # keep the server alive no matter what
        proxy_log("ERROR %r from %s" % (e, peer))
        try:
            writer.close()
        except Exception:
            pass


async def _serve():
    port = config()["proxy_port"]
    server = await asyncio.start_server(_handle, "127.0.0.1", port, backlog=256)
    proxy_log("proxy listening on 127.0.0.1:%d as uid %d" % (port, os.geteuid()))
    async with server:
        await server.serve_forever()


def proxy_main():
    asyncio.run(_serve())


# ----------------------------------------------------------------------------- override (typed by hand)

BURST_N = 6          # this many keystrokes...
BURST_SECONDS = 0.15  # ...inside this window = paste/dictation, not typing


class Burst(Exception):
    pass


def _drain(fd, quiet=0.4):
    """Swallow everything still arriving (a paste lands in chunks) until the input is quiet."""
    import select
    termios.tcflush(fd, termios.TCIFLUSH)
    while True:
        r, _, _ = select.select([fd], [], [], quiet)
        if not r:
            return
        try:
            os.read(fd, 65536)
        except OSError:
            return


def _words(n):
    pool = []
    try:
        with open("/usr/share/dict/words") as f:
            pool = [w.strip() for w in f if re.fullmatch(r"[a-z]{4,7}", w.strip())]
    except OSError:
        pass
    if len(pool) < 100:
        pool = "river stone cloud maple quiet amber field north lantern copper".split()
    return random.SystemRandom().sample(pool, n)


def _read_line_typed(fd, target=None, min_len=0):
    """Read one line in raw mode. With target: only the correct next char advances (typos are ignored
    with a bell). Without: free text with backspace, at least min_len chars. Raises Burst on paste."""
    out = sys.stdout
    times = deque(maxlen=BURST_N)
    buf = []
    pos = 0
    wrong = 0
    while True:
        ch = os.read(fd, 1)
        if not ch:
            raise KeyboardInterrupt
        now = time.monotonic()
        times.append(now)
        if len(times) == BURST_N and times[-1] - times[0] < BURST_SECONDS:
            termios.tcflush(fd, termios.TCIFLUSH)
            raise Burst()
        if ch in (b"\x03", b"\x04"):
            raise KeyboardInterrupt
        c = ch.decode("latin1")
        if target is not None:
            if pos >= len(target):
                if c in "\r\n":
                    out.write("\r\n"); out.flush()
                    return target
                out.write("\a"); out.flush()
                continue
            if c == target[pos]:
                out.write(c); out.flush()
                pos += 1
                wrong = 0
            else:
                wrong += 1
                out.write("\a")
                if wrong >= 3:
                    nxt = target[pos]
                    out.write("\r\n  (next character: %s)\r\n%s" % (repr(nxt) if nxt == " " else nxt, target[:pos]))
                    wrong = 0
                out.flush()
        else:
            if c in "\r\n":
                if len(buf) >= min_len:
                    out.write("\r\n"); out.flush()
                    return "".join(buf)
                out.write("\a"); out.flush()
            elif c in ("\x7f", "\x08"):
                if buf:
                    buf.pop()
                    out.write("\b \b"); out.flush()
            elif c.isprintable():
                buf.append(c)
                out.write(c); out.flush()


def override_main():
    if os.geteuid() != 0 and not os.environ.get("NIGHTBLOCK_APP_DIR"):
        os.execvp("sudo", ["sudo", BIN, "override"])
    if not sys.stdin.isatty():
        sys.exit("nightblock override must be run from a terminal, by hand")
    state, why = desired_state()
    c = config()
    if state == "open":
        print("The internet is already open (%s). Nothing to do." % why)
        return
    minutes = int(c["override_minutes"])
    phrase = " ".join(_words(4))
    lines = [PARAGRAPH, "Tonight's phrase: %s" % phrase]

    print()
    print("Night block is active (%s)." % why)
    print("To open the internet for %d minutes, type the following by hand, then give your reason." % minutes)
    print("Paste and dictation are detected and reset the attempt. Press Enter after each line. Ctrl-C aborts.")
    print()
    for ln in lines:
        print("   " + ln)
    print()

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        attempt = 0
        while True:
            attempt += 1
            try:
                for ln in lines:
                    sys.stdout.write("> "); sys.stdout.flush()
                    _read_line_typed(fd, target=ln)
                sys.stdout.write("\r\nThe reason is (one line, at least 15 characters, typed):\r\n> "); sys.stdout.flush()
                reason = _read_line_typed(fd, target=None, min_len=15)
                break
            except Burst:
                _drain(fd)
                sys.stdout.write("\r\n\r\n  That arrived too fast to have been typed. Start over, by hand.\r\n\r\n")
                sys.stdout.flush()
                if attempt >= 20:
                    raise KeyboardInterrupt
    except KeyboardInterrupt:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print("\n\nAborted. The block stays on. Good night.")
        return
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    until = dt.datetime.now() + dt.timedelta(minutes=minutes)
    write_json(OVERRIDE, {"until": until.isoformat(timespec="seconds"),
                          "created": dt.datetime.now().isoformat(timespec="seconds"),
                          "reason": reason})
    log("override for %d min, reason: %s" % (minutes, reason), OVERRIDE_LOG)
    log("override granted until %s: %s" % (until.strftime("%H:%M"), reason))
    if os.geteuid() == 0:
        reconcile()
    print("\nInternet open until %s. It re-blocks automatically after that." % until.strftime("%-I:%M %p"))


# ----------------------------------------------------------------------------- app cover (LaunchAgent, as the user)

HIDE_SCRIPT = """
tell application "System Events"
  set runningNames to name of every application process
  set hiddenList to {}
  repeat with n in {%s}
    set n to n as text
    if runningNames contains n then
      if visible of application process n then
        set visible of application process n to false
        set end of hiddenList to n
      end if
    end if
  end repeat
  set AppleScript's text item delimiters to ", "
  return hiddenList as text
end tell
"""


def cover_loop():
    """Every 5 s during the block, hide the apps listed in cover_apps. They keep running; they are just
    not on screen. Cmd-Tab brings one back for a few seconds at most."""
    last_state = None
    while True:
        try:
            st, _ = desired_state()
            apps = config()["cover_apps"]
            if st == "blocked" and apps:
                quoted = ", ".join('"%s"' % a.replace('"', "") for a in apps)
                r = run(["osascript", "-e", HIDE_SCRIPT % quoted])
                if r.returncode != 0 and st != last_state:
                    log("cover: osascript failed: %s" % r.stderr.strip())
                elif r.stdout.strip():
                    log("cover: hid %s" % r.stdout.strip())
            last_state = st
        except Exception as e:
            log("cover: %r" % e)
        time.sleep(5)


# ----------------------------------------------------------------------------- status & utilities

def tail_denied(n=25):
    try:
        with open(PROXY_LOG) as f:
            lines = f.readlines()[-2000:]
    except OSError:
        return []
    seen = {}
    for ln in reversed(lines):
        if " DENIED " in ln:
            host = ln.split(" DENIED ", 1)[1].strip()
            if host not in seen:
                seen[host] = ln[:16]
        if len(seen) >= n:
            break
    return [(h, t) for h, t in seen.items()]


def proxy_listening():
    try:
        with socket.create_connection(("127.0.0.1", config()["proxy_port"]), timeout=1):
            return True
    except OSError:
        return False


def status_main():
    c = config()
    st = read_json(STATE, {}) or {}
    desired, why = desired_state()
    print("nightblock")
    print("  window:           %s - %s daily, override length %d min" % (c["block_start"], c["block_end"], c["override_minutes"]))
    print("  state now:        %s (%s)" % (desired.upper(), why))
    applied = st.get("state", "never run")
    flag = "" if applied == desired else "   <-- daemon has not applied this yet (runs every minute)"
    print("  daemon applied:   %s since %s, last run %s%s" % (applied, st.get("since", "?"), st.get("last_run", "?"), flag))
    ov = active_until(OVERRIDE)
    fo = active_until(FORCE)
    if ov:
        print("  override:         active until %s  reason: %s" % (ov.strftime("%H:%M"), (read_json(OVERRIDE) or {}).get("reason", "")))
    if fo:
        print("  force-block:      active until %s" % fo.strftime("%H:%M"))
    print("  proxy:            %s on 127.0.0.1:%s" % ("listening" if proxy_listening() else "NOT RUNNING", c["proxy_port"]))
    print("  next change:      %s" % next_boundary().strftime("%a %H:%M"))
    print()
    print("During the block, what still works:")
    print("  - anything on this Mac: localhost, *.test, *.local, and /etc/hosts loopback names"
          " (Sleep Bandit at sleepbandit.test:8777, Task Tracker :8791, Roam local, ...)")
    print("  - in Chrome/Electron apps, via the proxy, these hostnames:")
    for h in c["allow_hosts"]:
        print("      %s" % h)
    print("  - from any app (Python, Node, Claude Code), these hosts, by IP (TCP 443):")
    res = st.get("resolved", {})
    for h in c["direct_hosts"]:
        print("      %-34s %d IPs known" % (h, len(res.get(h, {}))))
    if c["allow_root_processes"]:
        print("  - processes running as root (Backblaze bztransmit/bzserv, macOS updaters)")
    print("  - DNS, NTP, DHCP, Bonjour/link-local")
    if c["cover_apps"]:
        print("Hidden from the screen while blocked: %s" % ", ".join(c["cover_apps"]))
    print("Chrome tabs on other hosts are covered by the nightblock extension (chrome://extensions must show it enabled).")
    print("Everything else is blocked: other websites, Filtered Twitter, Slack, mail sync, Toggl/Sheets sync in Task Tracker, ...")
    den = tail_denied(15)
    if den:
        print()
        print("Recently refused by the proxy (add with: sudo nightblock allow <host>):")
        for h, t in den:
            print("  %s  %s" % (t, h))


def check_main(host):
    host = host.lower()
    c = config()
    via_proxy = host_allowed_by_proxy(host)
    direct = host in [h.lower() for h in c["direct_hosts"]]
    if is_local_host(host):
        print("%s: ALLOWED for every app, always (it lives on this Mac; loopback traffic is never blocked)." % host)
    elif direct:
        print("%s: ALLOWED for every app (firewall opens its IPs on 443) and via the proxy." % host)
    elif via_proxy:
        print("%s: ALLOWED via the system proxy only (Chrome, Electron). Blocked for Python/Node/curl." % host)
    else:
        print("%s: BLOCKED during the window." % host)


LISTS = ("allow_hosts", "direct_hosts", "cover_apps")


def edit_list(name, value, add):
    need_root()
    if name not in LISTS:
        sys.exit("list must be one of %s" % ", ".join(LISTS))
    value = value.strip()
    if not value:
        sys.exit("empty value")
    c = read_json(CONFIG, {}) or {}
    lst = c.setdefault(name, [])
    if add and value not in lst:
        lst.append(value)
    if not add and value in lst:
        lst.remove(value)
    write_json(CONFIG, c)
    log("config: %s %s %s" % ("added" if add else "removed", value, name))
    print("%s now: %s" % (name, ", ".join(lst)))


def edit_allow(host, add):
    edit_list("allow_hosts", host, add)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        status_main()
    elif cmd == "check" and len(argv) > 2:
        check_main(argv[2])
    elif cmd == "denied":
        for h, t in tail_denied(50):
            print("%s  %s" % (t, h))
    elif cmd == "override":
        override_main()
    elif cmd == "reconcile":
        reconcile()
    elif cmd == "proxy":
        proxy_main()
    elif cmd == "cover-loop":
        cover_loop()
    elif cmd == "state-json":
        st, why = desired_state()
        c = config()
        until = (active_until(FORCE) or next_boundary()) if st == "blocked" else next_boundary()
        print(json.dumps({"state": st, "why": why, "until": until.strftime("%-I:%M %p"),
                          "proxy": proxy_listening(), "allow_hosts": c["allow_hosts"],
                          "direct_hosts": c["direct_hosts"], "cover_apps": c["cover_apps"]}))
    elif cmd == "config-add" and len(argv) > 3:
        edit_list(argv[2], argv[3], True)
    elif cmd == "config-remove" and len(argv) > 3:
        edit_list(argv[2], argv[3], False)
    elif cmd == "allow" and len(argv) > 2:
        edit_allow(argv[2], True)
    elif cmd == "disallow" and len(argv) > 2:
        edit_allow(argv[2], False)
    elif cmd == "force-block":
        need_root()
        mins = int(argv[2]) if len(argv) > 2 else 10
        until = dt.datetime.now() + dt.timedelta(minutes=mins)
        write_json(FORCE, {"until": until.isoformat(timespec="seconds")})
        reconcile()
        print("forced block until %s" % until.strftime("%H:%M"))
    elif cmd == "force-off":
        need_root()
        try:
            os.remove(FORCE)
        except OSError:
            pass
        reconcile()
        print("force-block cleared")
    elif cmd == "unblock-now":
        need_root()
        try:
            os.remove(FORCE)
        except OSError:
            pass
        reconcile(force_open=True)
        print("unblocked; the daemon re-applies the schedule within a minute unless it is unloaded")
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv)
