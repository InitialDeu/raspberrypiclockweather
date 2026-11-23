#!/usr/bin/env python3
"""piclock Wi-Fi manager.

- Keeps the list of known Wi-Fi networks (add / remove / scan).
- If no known network is reachable, turns wlan0 into a setup hotspot
  ("piclock-setup") so the Pi can be configured from a phone.
- Periodically tries to go back to client mode, with automatic rollback
  to the hotspot if the connection does not come up.

Backends: "wpa"  -> wpa_supplicant + dhcpcd (Raspberry Pi OS Bullseye)
          "nm"   -> NetworkManager / nmcli  (Raspberry Pi OS Bookworm)

Usage (as root):
  piclock_wifi.py boot              at boot: wait for Wi-Fi, otherwise start the hotspot
  piclock_wifi.py check             periodic (timer): recover from lost Wi-Fi / leave hotspot
  piclock_wifi.py status            print the status JSON
  piclock_wifi.py scan              scan and print visible networks
  piclock_wifi.py list              print saved networks
  piclock_wifi.py add SSID [PASSWORD] [PRIORITY]
  piclock_wifi.py remove SSID
  piclock_wifi.py client [SECONDS]  leave the hotspot and try client mode (rollback on failure)
  piclock_wifi.py ap                start the hotspot now
  piclock_wifi.py ap-test [SECONDS] start the hotspot, then go back to client mode after SECONDS
                                    (default 120 s). Over SSH/Wi-Fi run it DETACHED, otherwise it dies
                                    with the SSH session and the Pi stays in hotspot mode:
                                    sudo systemd-run --unit=piclock-aptest /usr/bin/python3 piclock_wifi.py ap-test 120
"""
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time

IFACE = os.environ.get("PICLOCK_IFACE", "wlan0")
ROOT = os.environ.get("PICLOCK_ROOT", "")            # path prefix, only for tests
RUN_DIR = ROOT + "/run/piclock"
STATUS_FILE = RUN_DIR + "/net.json"
LOCK_FILE = RUN_DIR + "/wifi.lock"
CONF_DIR = ROOT + "/etc/piclock"
AP_CONF = CONF_DIR + "/ap.json"
WPA_CONF = ROOT + "/etc/wpa_supplicant/wpa_supplicant.conf"
HOSTAPD_CONF = RUN_DIR + "/hostapd.conf"
DNSMASQ_CONF = RUN_DIR + "/dnsmasq.conf"
AP_IP = "192.168.4.1"                                 # wpa backend; NM picks its own (10.42.0.1)
NM_AP_CON = "piclock-ap"

BOOT_WAIT_S = 90          # how long to wait for a known network at boot
CLIENT_TRY_S = 60         # how long to try client mode before rolling back to the hotspot
LOST_CHECKS = 2           # consecutive failed checks before switching to hotspot

# --------------------------------------------------------------------------- utils

def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def run(cmd, timeout=30, check=False):
    """Run a command given as a list (never through a shell)."""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        if check:
            raise
        return 1, str(e)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} -> {p.returncode}: {p.stdout.strip()}")
    return p.returncode, p.stdout


def atomic_write(path, text, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


@contextlib.contextmanager
def locked():
    os.makedirs(RUN_DIR, exist_ok=True)
    with open(LOCK_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def read_status():
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_status(**changes):
    st = read_status()
    st.update(changes)
    st["updated"] = int(time.time())
    atomic_write(STATUS_FILE, json.dumps(st, ensure_ascii=False, indent=1), mode=0o644)
    return st


def backend():
    rc, out = run(["systemctl", "is-active", "NetworkManager"], timeout=5)
    return "nm" if out.strip() == "active" else "wpa"


def ap_settings():
    """Hotspot name/password. Created on first use with a random password."""
    try:
        with open(AP_CONF, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    changed = False
    if not cfg.get("ssid"):
        cfg["ssid"], changed = "piclock-setup", True
    if not cfg.get("password") or len(cfg["password"]) < 8:
        alphabet = "abcdefghjkmnpqrstuvwxyz23456789"          # no ambiguous chars
        cfg["password"], changed = "".join(secrets.choice(alphabet) for _ in range(10)), True
    cfg.setdefault("country", "IT")
    cfg.setdefault("channel", 6)
    if changed:
        atomic_write(AP_CONF, json.dumps(cfg, indent=1), mode=0o600)
    return cfg


def current_ip():
    rc, out = run(["ip", "-4", "-o", "addr", "show", "dev", IFACE], timeout=5)
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", out)
    return m.group(1) if m else None

# ------------------------------------------------------------ validation

def validate_ssid(ssid):
    if not isinstance(ssid, str) or not ssid or len(ssid.encode("utf-8")) > 32:
        raise ValueError("SSID deve essere lungo 1-32 byte")
    if any(ord(c) < 32 for c in ssid):
        raise ValueError("SSID contiene caratteri non validi")
    return ssid


def validate_psk(psk):
    if psk in (None, ""):
        return ""                                     # open network
    if not (8 <= len(psk) <= 63) or any(ord(c) < 32 or ord(c) > 126 for c in psk):
        raise ValueError("La password Wi-Fi deve avere 8-63 caratteri ASCII stampabili")
    return psk


def validate_priority(prio):
    prio = int(prio)
    if not 0 <= prio <= 100:
        raise ValueError("Priorita' fuori range (0-100)")
    return prio

# ------------------------------------------------ wpa_supplicant.conf editing

NET_BLOCK = re.compile(r"^[ \t]*network\s*=\s*\{.*?^[ \t]*\}[ \t]*\n?", re.S | re.M)


def _decode_ssid(value):
    value = value.strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1]
    if re.fullmatch(r"[0-9a-fA-F]+", value) and len(value) % 2 == 0:
        try:
            return bytes.fromhex(value).decode("utf-8")
        except UnicodeDecodeError:
            return bytes.fromhex(value).decode("latin-1")
    return value


def wpa_read():
    try:
        with open(WPA_CONF, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\nupdate_config=1\ncountry=IT\n"


def wpa_list(text=None):
    text = wpa_read() if text is None else text
    nets = []
    for block in NET_BLOCK.findall(text):
        m = re.search(r"^\s*ssid\s*=\s*(.+?)\s*$", block, re.M)
        if not m:
            continue
        prio = re.search(r"^\s*priority\s*=\s*(-?\d+)", block, re.M)
        open_net = re.search(r"^\s*key_mgmt\s*=\s*NONE", block, re.M) is not None
        nets.append({"ssid": _decode_ssid(m.group(1)),
                     "priority": int(prio.group(1)) if prio else 0,
                     "open": open_net})
    return nets


def wpa_without(text, ssid):
    def keep(m):
        s = re.search(r"^\s*ssid\s*=\s*(.+?)\s*$", m.group(0), re.M)
        return "" if s and _decode_ssid(s.group(1)) == ssid else m.group(0)
    return re.sub(r"\n{3,}", "\n\n", NET_BLOCK.sub(keep, text))


def wpa_block(ssid, psk, priority):
    """ssid written in hex and psk pre-hashed: no quoting/escaping problems, no clear-text password."""
    ssid_hex = ssid.encode("utf-8").hex()
    lines = ["network={", f"\tssid={ssid_hex}"]
    if psk:
        key = hashlib.pbkdf2_hmac("sha1", psk.encode(), ssid.encode("utf-8"), 4096, 32).hex()
        lines.append(f"\tpsk={key}")
    else:
        lines.append("\tkey_mgmt=NONE")
    lines += [f"\tpriority={priority}", "}", ""]
    return "\n".join(lines)


def wpa_add(ssid, psk, priority):
    text = wpa_without(wpa_read(), ssid).rstrip("\n") + "\n\n" + wpa_block(ssid, psk, priority)
    atomic_write(WPA_CONF, text, mode=0o600)
    run(["wpa_cli", "-i", IFACE, "reconfigure"], timeout=10)


def wpa_remove(ssid):
    atomic_write(WPA_CONF, wpa_without(wpa_read(), ssid), mode=0o600)
    run(["wpa_cli", "-i", IFACE, "reconfigure"], timeout=10)

# --------------------------------------------------------- nmcli backend

def nm_list():
    rc, out = run(["nmcli", "-t", "-f", "NAME,TYPE,AUTOCONNECT-PRIORITY", "connection", "show"], timeout=10)
    nets = []
    for line in out.splitlines():
        parts = re.split(r"(?<!\\):", line)
        if len(parts) >= 3 and parts[1] == "802-11-wireless" and parts[0] != NM_AP_CON:
            name = parts[0].replace("\\:", ":")
            rc2, ssid = run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", name], timeout=10)
            nets.append({"ssid": ssid.strip() or name, "name": name,
                         "priority": int(parts[2] or 0), "open": False})
    return nets


def nm_add(ssid, psk, priority):
    nm_remove(ssid)
    cmd = ["nmcli", "connection", "add", "type", "wifi", "ifname", IFACE,
           "con-name", ssid, "ssid", ssid, "connection.autoconnect", "yes",
           "connection.autoconnect-priority", str(priority)]
    if psk:
        cmd += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk]
    run(cmd, timeout=20, check=True)


def nm_remove(ssid):
    for n in nm_list():
        if n["ssid"] == ssid:
            run(["nmcli", "connection", "delete", n["name"]], timeout=20)

# ---------------------------------------------------------------- common API

def list_networks():
    return nm_list() if backend() == "nm" else wpa_list()


def add_network(ssid, psk="", priority=10):
    ssid, psk, priority = validate_ssid(ssid), validate_psk(psk), validate_priority(priority)
    (nm_add if backend() == "nm" else wpa_add)(ssid, psk, priority)
    log(f"network saved: {ssid!r} (priority {priority})")


def remove_network(ssid):
    validate_ssid(ssid)
    (nm_remove if backend() == "nm" else wpa_remove)(ssid)
    log(f"network removed: {ssid!r}")


def scan():
    """Visible networks, strongest first. Works in client mode (not while the hotspot is up)."""
    found = {}
    if backend() == "nm":
        run(["nmcli", "device", "wifi", "rescan", "ifname", IFACE], timeout=20)
        rc, out = run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                       "ifname", IFACE], timeout=20)
        for line in out.splitlines():
            parts = re.split(r"(?<!\\):", line)
            if len(parts) >= 3 and parts[0]:
                ssid = parts[0].replace("\\:", ":")
                sig = int(parts[1] or 0)
                if ssid not in found or sig > found[ssid]["signal"]:
                    found[ssid] = {"ssid": ssid, "signal": sig, "secure": parts[2] not in ("", "--")}
    else:
        run(["ip", "link", "set", IFACE, "up"], timeout=5)
        rc, out = run(["iw", "dev", IFACE, "scan"], timeout=30)
        for cell in out.split("\nBSS ")[0:]:
            m = re.search(r"^\s*SSID: (.+)$", cell, re.M)
            if not m or not m.group(1).strip() or "\\x00" in m.group(1):
                continue
            ssid = m.group(1).strip()
            s = re.search(r"signal: (-?\d+)", cell)
            dbm = int(s.group(1)) if s else -100
            sig = max(0, min(100, 2 * (dbm + 100)))          # dBm -> 0..100
            secure = ("RSN:" in cell) or ("WPA:" in cell) or ("Privacy" in cell)
            if ssid not in found or sig > found[ssid]["signal"]:
                found[ssid] = {"ssid": ssid, "signal": sig, "secure": secure}
    nets = sorted(found.values(), key=lambda n: -n["signal"])
    write_status(scan=nets, scan_ts=int(time.time()))
    return nets


def connected():
    """True if wlan0 is associated as a client and has an IP (not the hotspot one)."""
    if read_status().get("mode") == "ap":
        return False
    if backend() == "nm":
        rc, out = run(["nmcli", "-t", "-f", "DEVICE,STATE,CONNECTION", "device"], timeout=10)
        for line in out.splitlines():
            dev, _, rest = line.partition(":")
            if dev == IFACE:
                state, _, con = rest.partition(":")
                return state == "connected" and con != NM_AP_CON
        return False
    rc, out = run(["wpa_cli", "-i", IFACE, "status"], timeout=10)
    ip = current_ip()
    return "wpa_state=COMPLETED" in out and ip is not None and ip != AP_IP


def current_ssid():
    if backend() == "nm":
        rc, out = run(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi", "list", "ifname", IFACE], timeout=10)
        for line in out.splitlines():
            if line.startswith("yes:"):
                return line[4:].replace("\\:", ":")
        return None
    rc, out = run(["wpa_cli", "-i", IFACE, "status"], timeout=10)
    m = re.search(r"^ssid=(.*)$", out, re.M)
    return m.group(1) if m else None


def wait_connected(seconds):
    end = time.time() + seconds
    while time.time() < end:
        if connected():
            return True
        time.sleep(3)
    return False


def mark_client():
    write_status(mode="client", backend=backend(), ssid=current_ssid(), ip=current_ip(),
                 lost=0, last_error=None)

# ---------------------------------------------------------- hotspot on/off

def _kill_pidfile(path):
    try:
        with open(path) as f:
            os.kill(int(f.read().strip()), 15)
    except (OSError, ValueError):
        pass
    with contextlib.suppress(OSError):
        os.remove(path)


def start_ap(reason=""):
    cfg = ap_settings()
    try:
        scan()                                         # remember what's around, for the setup page
    except Exception as e:                             # noqa: BLE001 - scan is best effort
        log(f"scan before hotspot failed: {e}")
    log(f"starting hotspot {cfg['ssid']!r} ({reason})")
    if backend() == "nm":
        run(["nmcli", "connection", "delete", NM_AP_CON], timeout=20)
        run(["nmcli", "device", "wifi", "hotspot", "ifname", IFACE, "con-name", NM_AP_CON,
             "ssid", cfg["ssid"], "password", cfg["password"]], timeout=30, check=True)
        time.sleep(3)
        ip = current_ip() or "10.42.0.1"
    else:
        atomic_write(HOSTAPD_CONF, "\n".join([
            f"interface={IFACE}", "driver=nl80211", f"ssid={cfg['ssid']}",
            f"country_code={cfg['country']}", "ieee80211d=1", "hw_mode=g",
            f"channel={cfg['channel']}", "wmm_enabled=0", "auth_algs=1", "ignore_broadcast_ssid=0",
            "wpa=2", "wpa_key_mgmt=WPA-PSK", "rsn_pairwise=CCMP",
            f"wpa_passphrase={cfg['password']}", ""]), mode=0o600)
        atomic_write(DNSMASQ_CONF, "\n".join([
            f"interface={IFACE}", "bind-interfaces", f"listen-address={AP_IP}", "no-resolv",
            "dhcp-range=192.168.4.10,192.168.4.60,255.255.255.0,15m",
            f"dhcp-option=3,{AP_IP}", f"dhcp-option=6,{AP_IP}",
            f"address=/#/{AP_IP}",                      # every name -> the Pi (captive portal)
            ""]), mode=0o644)
        run(["wpa_cli", "-i", IFACE, "terminate"], timeout=10)
        run(["dhcpcd", "-k", IFACE], timeout=20)
        run(["rfkill", "unblock", "wifi"], timeout=5)
        run(["ip", "link", "set", IFACE, "down"], timeout=5)
        run(["ip", "addr", "flush", "dev", IFACE], timeout=5)
        run(["ip", "link", "set", IFACE, "up"], timeout=5)
        run(["ip", "addr", "add", f"{AP_IP}/24", "dev", IFACE], timeout=5)
        run(["hostapd", "-B", "-P", RUN_DIR + "/hostapd.pid", HOSTAPD_CONF], timeout=20, check=True)
        run(["dnsmasq", "-C", DNSMASQ_CONF, "--pid-file=" + RUN_DIR + "/dnsmasq.pid"], timeout=20, check=True)
        ip = AP_IP
    write_status(mode="ap", backend=backend(), ap_ssid=cfg["ssid"], ap_password=cfg["password"],
                 ap_ip=ip, ip=ip, ssid=None, lost=0, ap_since=int(time.time()))


def stop_ap():
    st = read_status()
    log("stopping hotspot")
    if backend() == "nm":
        run(["nmcli", "connection", "down", NM_AP_CON], timeout=20)
        run(["nmcli", "connection", "delete", NM_AP_CON], timeout=20)
        run(["nmcli", "device", "connect", IFACE], timeout=30)
    else:
        _kill_pidfile(RUN_DIR + "/hostapd.pid")
        _kill_pidfile(RUN_DIR + "/dnsmasq.pid")
        run(["ip", "addr", "flush", "dev", IFACE], timeout=5)
        run(["ip", "link", "set", IFACE, "down"], timeout=5)
        run(["ip", "link", "set", IFACE, "up"], timeout=5)
        # dhcpcd's wpa_supplicant hook brings wpa_supplicant back for wlan0
        run(["systemctl", "restart", "dhcpcd"], timeout=60)
    st.update(mode="client", ip=None)
    write_status(**st)


def ap_has_clients():
    rc, out = run(["iw", "dev", IFACE, "station", "dump"], timeout=10)
    return "Station" in out


def try_client(seconds=CLIENT_TRY_S):
    """Leave the hotspot (if up) and try client mode; roll back to the hotspot on failure."""
    if read_status().get("mode") == "ap":
        stop_ap()
    if wait_connected(seconds):
        mark_client()
        log(f"connected to {current_ssid()!r}, ip {current_ip()}")
        return True
    log("no known network came up: back to hotspot")
    start_ap("client attempt failed")
    write_status(last_error="Nessuna rete conosciuta raggiungibile (password errata o rete fuori portata)")
    return False

# ------------------------------------------------------------- entrypoints

def cmd_boot():
    with locked():
        write_status(mode="client", backend=backend(), lost=0)
        if wait_connected(BOOT_WAIT_S):
            mark_client()
            log(f"boot: connected to {current_ssid()!r}")
        else:
            start_ap("no known network at boot")


def cmd_check():
    with locked():
        st = read_status()
        if st.get("mode") == "ap":
            # retry client mode only if nobody is using the hotspot and some network is saved
            if not ap_has_clients() and list_networks():
                try_client()
            return
        if connected():
            if st.get("lost") or st.get("ip") != current_ip() or st.get("ssid") != current_ssid():
                mark_client()
            return
        lost = int(st.get("lost") or 0) + 1
        write_status(lost=lost)
        log(f"check: Wi-Fi not connected ({lost}/{LOST_CHECKS})")
        if lost >= LOST_CHECKS:
            start_ap("Wi-Fi lost")


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    if os.geteuid() != 0 and cmd not in ("status",) and not ROOT:
        print("run as root", file=sys.stderr)
        return 1
    if cmd == "boot":
        cmd_boot()
    elif cmd == "check":
        cmd_check()
    elif cmd == "status":
        print(json.dumps(read_status(), ensure_ascii=False, indent=1))
    elif cmd == "scan":
        with locked():
            print(json.dumps(scan(), ensure_ascii=False, indent=1))
    elif cmd == "list":
        print(json.dumps(list_networks(), ensure_ascii=False, indent=1))
    elif cmd == "add":
        with locked():
            add_network(args[0], args[1] if len(args) > 1 else "", args[2] if len(args) > 2 else 10)
    elif cmd == "remove":
        with locked():
            remove_network(args[0])
    elif cmd == "client":
        with locked():
            ok = try_client(int(args[0]) if args else CLIENT_TRY_S)
        return 0 if ok else 1
    elif cmd == "ap":
        with locked():
            start_ap("manual")
    elif cmd == "ap-test":
        secs = int(args[0]) if args else 120
        with locked():
            start_ap(f"test for {secs} s")
        time.sleep(secs)
        with locked():
            try_client()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
