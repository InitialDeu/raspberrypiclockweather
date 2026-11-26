#!/usr/bin/env python3
"""Small LAN-only management console for the Raspberry Pi clock.

Pages: status, settings (/etc/clockweather.env), Wi-Fi networks, actions
(restart clock, update from GitHub, reboot). Standard library only.

Security model:
  * HTTP Basic auth, password = CW_ADMIN_PASSWORD in /etc/clockweather.env
  * every POST carries a per-process CSRF token
  * no arbitrary commands: only a fixed set of actions, all inputs validated
  * while the setup hotspot is up, clients on the hotspot subnet can open the
    Wi-Fi page without the admin password (they already know the hotspot
    password, which is shown on the clock screen) - nothing else.
"""
import base64
import datetime
import hmac
import html
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import piclock_wifi as wifi  # noqa: E402

ROOT = os.environ.get("PICLOCK_ROOT", "")                      # path prefix, only for tests
ENV_FILE = ROOT + "/etc/clockweather.env"
REPO = os.environ.get("CW_REPO", os.path.dirname(HERE))         # git checkout of the project
SERVICE = "clockweather"
LOG_FILE = ROOT + "/var/log/clockweather.log"
PORT = int(os.environ.get("CW_ADMIN_PORT", "80"))
DRY_RUN = os.environ.get("CW_ADMIN_DRY_RUN") == "1"            # tests: don't run system commands

CSRF = secrets.token_urlsafe(24)
EDITABLE = ["CW_CITY", "CW_LANG", "CW_NIGHT", "CW_NIGHT_FG", "CW_NIGHT_START", "CW_NIGHT_END"]
DEFAULTS = {"CW_CITY": "Turin", "CW_LANG": "en", "CW_NIGHT": "on", "CW_NIGHT_FG": "#5C3300",
            "CW_NIGHT_START": "22:30", "CW_NIGHT_END": "07:30"}
RE_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
RE_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
RE_CITY = re.compile(r"^[^\W\d_][\w .,’()-]{0,59}$", re.U)     # no ' or ": they break systemd EnvironmentFile
RE_APIKEY = re.compile(r"^[0-9A-Za-z]{16,64}$")

# ------------------------------------------------------------------ helpers

def sh(cmd, timeout=60):
    if DRY_RUN:
        return 0, "(dry run) " + " ".join(cmd)
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def read_env():
    env, lines = {}, []
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        pass
    for line in lines:
        m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"')
    return env, lines


def write_env(updates):
    """Update KEY=VALUE lines, keep everything else (comments, unknown keys).
    A value of None means "leave this key as it is"."""
    updates = {k: v for k, v in updates.items() if v is not None}
    env, lines = read_env()
    done, out = set(), []
    for line in lines:
        m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            if key not in done and updates[key] is not None:
                out.append(f"{key}={updates[key]}")
            done.add(key)
        else:
            out.append(line)
    for key, val in updates.items():
        if key not in done and val is not None:
            out.append(f"{key}={val}")
    wifi.atomic_write(ENV_FILE, "\n".join(out) + "\n", mode=0o600)


def admin_password():
    return read_env()[0].get("CW_ADMIN_PASSWORD", "")


def git(*args):
    return sh(["git", "-c", f"safe.directory={REPO}", "-C", REPO] + list(args), timeout=120)


def chown_like_repo():
    st = os.stat(REPO)
    if DRY_RUN:
        return
    for base, dirs, files in os.walk(REPO):
        for n in dirs + files:
            try:
                os.lchown(os.path.join(base, n), st.st_uid, st.st_gid)
            except OSError:
                pass


def status_info():
    net = wifi.read_status()
    _, active = sh(["systemctl", "is-active", SERVICE], timeout=10)
    _, version = git("log", "-1", "--format=%h · %s · %cd", "--date=format:%d/%m/%Y")
    try:
        with open("/proc/uptime") as f:
            up = int(float(f.read().split()[0]))
        uptime = f"{up // 86400} g {up % 86400 // 3600} h {up % 3600 // 60} min"
    except OSError:
        uptime = "?"
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp = f"{int(f.read()) / 1000:.1f} °C"
    except (OSError, ValueError):
        temp = "?"
    try:
        with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
            log_tail = f.readlines()[-40:]
    except OSError:
        log_tail = ["(log non leggibile)"]
    return {"active": active.strip(), "version": version.strip(), "uptime": uptime,
            "temp": temp, "net": net, "log": "".join(log_tail)}


def background(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def later_client_try():
    time.sleep(3)                       # let the HTTP response reach the phone first
    if DRY_RUN:
        return
    subprocess.Popen([sys.executable, os.path.join(HERE, "piclock_wifi.py"), "client"],
                     start_new_session=True)

# ------------------------------------------------------------------- HTML

CSS = """
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#111;color:#eee}
header{background:#1d1d1d;padding:12px 16px;border-bottom:2px solid #FFD75E}
header b{color:#FFD75E}nav a{color:#ccc;margin-right:14px;text-decoration:none}
nav a.on{color:#FFD75E;font-weight:600}main{max-width:760px;margin:auto;padding:16px}
section{background:#1b1b1b;border:1px solid #2a2a2a;border-radius:10px;padding:14px 16px;margin-bottom:16px}
h2{margin:0 0 10px;font-size:1.05em;color:#FFD75E}table{width:100%;border-collapse:collapse}
td{padding:5px 4px;border-bottom:1px solid #262626;vertical-align:top}td:first-child{color:#999;width:38%}
label{display:block;margin:10px 0 4px;color:#bbb;font-size:.92em}
input,select{width:100%;box-sizing:border-box;padding:9px;border-radius:7px;border:1px solid #444;background:#0d0d0d;color:#eee;font-size:1em}
input[type=color]{height:42px;padding:2px}input[type=checkbox]{width:auto}
button{background:#FFD75E;color:#111;border:0;border-radius:7px;padding:10px 16px;font-weight:600;font-size:1em;cursor:pointer;margin-top:12px}
button.sec{background:#333;color:#eee}button.danger{background:#a33;color:#fff}
form.inline{display:inline}pre{white-space:pre-wrap;font-size:.8em;color:#bbb;max-height:340px;overflow:auto}
.msg{padding:10px 14px;border-radius:8px;margin-bottom:14px;background:#24361f;border:1px solid #3d6b31}
.err{background:#3a1f1f;border-color:#7a3333}.hint{color:#999;font-size:.88em}
.row{display:flex;gap:10px}.row>*{flex:1}
"""


def esc(v):
    return html.escape("" if v is None else str(v), quote=True)


def page(title, body, active="", msg=None, err=False, full_nav=True):
    nav = ""
    if full_nav:
        nav = "<nav>" + "".join(
            f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>'
            for key, href, label in [("status", "/", "Stato"), ("settings", "/settings", "Impostazioni"),
                                     ("wifi", "/wifi", "Wi-Fi")]) + "</nav>"
    note = f'<div class="msg{" err" if err else ""}">{esc(msg)}</div>' if msg else ""
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>piclock · {esc(title)}</title>
<style>{CSS}</style></head><body><header><b>piclock</b> &nbsp; {nav}</header>
<main>{note}{body}</main></body></html>"""


def csrf_field():
    return f'<input type="hidden" name="csrf" value="{CSRF}">'


def status_page():
    s = status_info()
    net = s["net"]
    if net.get("mode") == "ap":
        wifi_txt = f"HOTSPOT attivo: {net.get('ap_ssid')} ({net.get('ap_ip')})"
    else:
        wifi_txt = f"{net.get('ssid') or '?'} · IP {net.get('ip') or '?'}"
    rows = [("Orologio", s["active"]), ("Versione", s["version"]), ("Wi-Fi", wifi_txt),
            ("Uptime", s["uptime"]), ("Temperatura CPU", s["temp"])]
    table = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in rows)
    actions = "".join(
        f'<form class="inline" method="post" action="/action/{a}" '
        f'onsubmit="return confirm(\'{esc(q)}\')">{csrf_field()}<button class="{c}">{esc(t)}</button></form> '
        for a, t, c, q in [("restart", "Riavvia orologio", "sec", "Riavviare l'orologio?"),
                           ("update", "Aggiorna da GitHub", "sec", "Scaricare l'ultima versione e riavviare?"),
                           ("reboot", "Riavvia il Pi", "danger", "Riavviare il Raspberry Pi?")])
    body = (f"<section><h2>Stato</h2><table>{table}</table></section>"
            f"<section><h2>Azioni</h2>{actions}</section>"
            f"<section><h2>Ultime righe del log</h2><pre>{esc(s['log'])}</pre></section>")
    return page("Stato", body, "status")


def settings_page(msg=None, err=False):
    env = read_env()[0]
    v = {k: env.get(k, DEFAULTS[k]) for k in EDITABLE}
    has_key = bool(env.get("OWM_API_KEY")) and env.get("OWM_API_KEY") != "YOUR_API_KEY"
    night = v["CW_NIGHT"].strip().lower()
    if night in ("off", "0", "false", "no"):
        night = "off"
    elif night != "sun":
        night = "on"
    night_options = "".join(
        f'<option value="{key}"{" selected" if key == night else ""}>{label}</option>'
        for key, label in (("on", "A orari fissi"), ("sun", "Dal tramonto all&#39;alba"), ("off", "Disattivata")))
    body = f"""<section><h2>Impostazioni orologio</h2><form method="post" action="/settings">{csrf_field()}
<label>Città (come la conosce OpenWeatherMap)</label><input name="CW_CITY" value="{esc(v['CW_CITY'])}" required>
<label>Lingua</label><select name="CW_LANG">
<option value="it"{' selected' if v['CW_LANG'] == 'it' else ''}>Italiano</option>
<option value="en"{' selected' if v['CW_LANG'] == 'en' else ''}>English</option></select>
<label>Chiave API OpenWeatherMap {'(impostata: lascia vuoto per non cambiarla)' if has_key else '(NON impostata)'}</label>
<input name="OWM_API_KEY" value="" autocomplete="off" placeholder="{'••••••••' if has_key else 'incolla la chiave'}">
<label>Modalità notte</label><select name="CW_NIGHT">{night_options}</select>
<div class="row"><div><label>Dalle</label><input name="CW_NIGHT_START" value="{esc(v['CW_NIGHT_START'])}" pattern="[0-2][0-9]:[0-5][0-9]"></div>
<div><label>Alle</label><input name="CW_NIGHT_END" value="{esc(v['CW_NIGHT_END'])}" pattern="[0-2][0-9]:[0-5][0-9]"></div>
<div><label>Colore notte</label><input type="color" name="CW_NIGHT_FG" value="{esc(v['CW_NIGHT_FG'])}"></div></div>
<p class="hint">Più scuro il colore, meno luce di notte. Con «Dal tramonto all'alba» gli orari valgono solo finché non arrivano i dati meteo. Al salvataggio l'orologio si riavvia.</p>
<button>Salva e applica</button></form></section>
<section><h2>Password della console</h2><form method="post" action="/password">{csrf_field()}
<label>Nuova password (almeno 8 caratteri)</label><input type="password" name="pw1" autocomplete="new-password">
<label>Ripeti</label><input type="password" name="pw2" autocomplete="new-password">
<button class="sec">Cambia password</button></form></section>"""
    return page("Impostazioni", body, "settings", msg, err)


def wifi_page(msg=None, err=False, limited=False):
    st = wifi.read_status()
    try:
        saved = wifi.list_networks()
    except Exception as e:                                          # noqa: BLE001
        saved, msg, err = [], f"Impossibile leggere le reti salvate: {e}", True
    scan = st.get("scan") or []
    if st.get("mode") == "ap":
        head = (f"<p>Il Pi è in modalità <b>hotspot</b> ({esc(st.get('ap_ssid'))}) perché non trova reti conosciute."
                f" Aggiungi qui la rete del posto: dopo il salvataggio l'hotspot si spegne e il Pi prova a collegarsi."
                f" Se non ci riesce entro un minuto, l'hotspot ricompare.</p>")
        if st.get("last_error"):
            head += f'<div class="msg err">{esc(st["last_error"])}</div>'
    else:
        head = f"<p>Collegato a <b>{esc(st.get('ssid') or '?')}</b> · IP {esc(st.get('ip') or '?')}</p>"
    rows = "".join(
        f"<tr><td>{esc(n['ssid'])}</td><td>priorità {esc(n['priority'])}{' · aperta' if n.get('open') else ''}"
        f'<form class="inline" method="post" action="/wifi/remove" onsubmit="return confirm(\'Rimuovere questa rete?\')">'
        f'{csrf_field()}<input type="hidden" name="ssid" value="{esc(n["ssid"])}">'
        f'<button class="sec" style="margin:0 0 0 10px;padding:4px 10px">Rimuovi</button></form></td></tr>'
        for n in saved) or '<tr><td colspan="2">Nessuna rete salvata</td></tr>'
    options = "".join(f'<option value="{esc(n["ssid"])}">{esc(n["ssid"])} · {n["signal"]}%'
                      f'{"" if n.get("secure") else " · aperta"}</option>' for n in scan)
    scan_ts = st.get("scan_ts")
    scan_when = datetime.datetime.fromtimestamp(scan_ts).strftime("%d/%m %H:%M") if scan_ts else "mai"
    scan_btn = "" if st.get("mode") == "ap" else (
        f'<form class="inline" method="post" action="/wifi/scan">{csrf_field()}'
        f'<button class="sec">Cerca reti</button></form>')
    body = f"""<section><h2>Wi-Fi</h2>{head}</section>
<section><h2>Aggiungi una rete</h2><form method="post" action="/wifi/add">{csrf_field()}
<label>Reti visibili (ricerca: {esc(scan_when)})</label>
<select onchange="document.getElementById('ssid').value=this.value"><option value="">— scegli —</option>{options}</select>
<label>Nome rete (SSID)</label><input id="ssid" name="ssid" required maxlength="32">
<label>Password (vuota se la rete è aperta)</label><input type="password" name="psk" autocomplete="off" maxlength="63">
<label>Priorità (più alta = preferita)</label><input name="priority" type="number" min="0" max="100" value="20">
<button>Salva rete</button></form>{scan_btn}</section>
<section><h2>Reti salvate</h2><table>{rows}</table></section>"""
    return page("Wi-Fi", body, "wifi", msg, err, full_nav=not limited)

# ------------------------------------------------------------------ server

class Handler(BaseHTTPRequestHandler):
    server_version = "piclock-admin"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.client_address[0], fmt % args))

    # --- auth
    def hotspot_client(self):
        st = wifi.read_status()
        ap_ip = st.get("ap_ip")
        if st.get("mode") != "ap" or not ap_ip:
            return False
        return self.client_address[0].rsplit(".", 1)[0] == ap_ip.rsplit(".", 1)[0]

    def authorized(self):
        pw = admin_password()
        if not pw:
            return False
        hdr = self.headers.get("Authorization", "")
        if not hdr.startswith("Basic "):
            return False
        try:
            _, _, given = base64.b64decode(hdr[6:]).decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        return hmac.compare_digest(given.encode(), pw.encode())

    def ask_auth(self):
        body = ("Serve la password della console (CW_ADMIN_PASSWORD in /etc/clockweather.env)."
                if admin_password() else
                "Console non configurata: imposta CW_ADMIN_PASSWORD in /etc/clockweather.env").encode()
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="piclock", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # --- responses
    def send_html(self, text, code=200):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, where):
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def access(self, path):
        """'full', 'limited' (hotspot: Wi-Fi page only) or None."""
        if self.authorized():
            return "full"
        if self.hotspot_client() and (path == "/" or path.startswith("/wifi")):
            return "limited"
        return None

    # --- GET
    def do_GET(self):
        path = urlsplit(self.path).path
        hotspot = self.hotspot_client()
        known = path in ("/", "/settings", "/wifi")
        if hotspot and not known:                 # captive portal probes (generate_204, hotspot-detect...)
            return self.redirect(f"http://{wifi.read_status().get('ap_ip')}/wifi")
        level = self.access(path)
        if level is None:
            return self.ask_auth()
        if level == "limited":
            return self.send_html(wifi_page(limited=True))
        if path == "/":
            return self.send_html(status_page())
        if path == "/settings":
            return self.send_html(settings_page())
        if path == "/wifi":
            return self.send_html(wifi_page())
        self.send_html(page("404", "<p>Pagina non trovata</p>"), 404)

    # --- POST
    def form(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 20000:
            return {}
        data = parse_qs(self.rfile.read(n).decode("utf-8", "replace"), keep_blank_values=True)
        return {k: v[0] for k, v in data.items()}

    def do_POST(self):
        path = urlsplit(self.path).path
        level = self.access(path)
        if level is None:
            return self.ask_auth()
        f = self.form()
        if not hmac.compare_digest(f.get("csrf", ""), CSRF):
            return self.send_html(page("Errore", "<p>Sessione scaduta: ricarica la pagina e riprova.</p>"), 400)
        limited = level == "limited"
        try:
            if path == "/wifi/add":
                return self.wifi_add(f, limited)
            if path == "/wifi/remove":
                wifi.remove_network(f.get("ssid", ""))
                return self.send_html(wifi_page("Rete rimossa", limited=limited))
            if path == "/wifi/scan" and not limited:
                wifi.scan()
                return self.redirect("/wifi")
            if limited:
                return self.ask_auth()
            if path == "/settings":
                return self.save_settings(f)
            if path == "/password":
                return self.change_password(f)
            if path.startswith("/action/"):
                return self.action(path.split("/")[-1])
        except (ValueError, RuntimeError) as e:
            page_fn = wifi_page if path.startswith("/wifi") else settings_page
            kwargs = {"limited": limited} if page_fn is wifi_page else {}
            return self.send_html(page_fn(str(e), True, **kwargs))
        self.send_html(page("404", "<p>Azione non valida</p>"), 404)

    def wifi_add(self, f, limited):
        ssid = f.get("ssid", "").strip()
        wifi.add_network(ssid, f.get("psk", ""), f.get("priority") or 20)
        if wifi.read_status().get("mode") == "ap":
            background(later_client_try)
            body = (f"<section><h2>Rete salvata</h2><p>Tra pochi secondi l'hotspot si spegne e il Pi prova "
                    f"a collegarsi a <b>{esc(ssid)}</b>.</p><p>Se ci riesce, sullo schermo dell'orologio vedrai "
                    f"per qualche istante il nuovo indirizzo della console. Se non ci riesce entro un minuto, "
                    f"l'hotspot ricompare: ricollegati e controlla la password.</p></section>")
            return self.send_html(page("Wi-Fi", body, "wifi", full_nav=not limited))
        return self.send_html(wifi_page(f"Rete {ssid} salvata: il Pi la userà quando sarà in portata.",
                                        limited=limited))

    def save_settings(self, f):
        city = f.get("CW_CITY", "").strip()
        if not RE_CITY.fullmatch(city):
            raise ValueError("Città non valida")
        lang = f.get("CW_LANG", "en")
        if lang not in ("it", "en"):
            raise ValueError("Lingua non valida")
        start, end = f.get("CW_NIGHT_START", ""), f.get("CW_NIGHT_END", "")
        if not (RE_TIME.fullmatch(start) and RE_TIME.fullmatch(end)):
            raise ValueError("Orari non validi (formato HH:MM)")
        color = f.get("CW_NIGHT_FG", "")
        if not RE_COLOR.fullmatch(color):
            raise ValueError("Colore non valido")
        night = f.get("CW_NIGHT", "on")
        if night not in ("on", "sun", "off"):
            raise ValueError("Modalità notte non valida")
        key = f.get("OWM_API_KEY", "").strip()
        if key and not RE_APIKEY.fullmatch(key):
            raise ValueError("Chiave API non valida")
        write_env({"CW_CITY": city, "CW_LANG": lang, "CW_NIGHT": night,
                   "CW_NIGHT_FG": color.upper(), "CW_NIGHT_START": start, "CW_NIGHT_END": end,
                   "OWM_API_KEY": key or None})
        rc, out = sh(["systemctl", "restart", SERVICE])
        if rc:
            return self.send_html(settings_page(f"Salvato, ma il riavvio non è riuscito: {out}", True))
        return self.send_html(settings_page("Salvato: l'orologio si sta riavviando"))

    def change_password(self, f):
        p1, p2 = f.get("pw1", ""), f.get("pw2", "")
        if p1 != p2:
            raise ValueError("Le due password non coincidono")
        if len(p1) < 8 or any(ord(c) < 33 or ord(c) > 126 or c in "'\"\\`" for c in p1):
            raise ValueError("Password troppo corta o con caratteri non ammessi (spazi, apici, backslash)")
        write_env({"CW_ADMIN_PASSWORD": p1})
        return self.send_html(page("Password", "<section><p>Password cambiata: il browser te la richiederà.</p>"
                                               "</section>", "settings"))

    @staticmethod
    def reinstall_console():
        """The console runs from a root-owned copy (see install.sh): refresh it after git pull."""
        src = os.path.join(REPO, "admin")
        if DRY_RUN or os.path.realpath(src) == os.path.realpath(HERE):
            return ""
        for name in ("clockweather_admin.py", "piclock_wifi.py"):
            with open(os.path.join(src, name), "rb") as f:
                data = f.read()
            tmp = os.path.join(HERE, name + ".tmp")
            with open(tmp, "wb") as f:
                f.write(data)
            os.chmod(tmp, 0o755)
            os.replace(tmp, os.path.join(HERE, name))
        return "\nconsole aggiornata"

    def action(self, name):
        if name == "restart":
            rc, out = sh(["systemctl", "restart", SERVICE])
            msg = "Orologio riavviato" if rc == 0 else f"Errore: {out}"
            return self.send_html(page("Azioni", f"<section><p>{esc(msg)}</p></section>", "status"))
        if name == "update":
            rc, out = git("pull", "--ff-only")
            if rc == 0:
                chown_like_repo()
                out += self.reinstall_console()
                rc2, out2 = sh(["systemctl", "restart", SERVICE])
                out += "\n" + (out2 or "orologio riavviato")
                # restart the console itself too, after this response has been sent
                background(lambda: (time.sleep(2), sh(["systemctl", "restart", "clockweather-admin"])))
            return self.send_html(page("Aggiornamento", f"<section><h2>git pull</h2><pre>{esc(out)}</pre>"
                                                        f"</section>", "status", None if rc == 0 else
                                       "Aggiornamento non riuscito", rc != 0))
        if name == "reboot":
            background(lambda: (time.sleep(2), sh(["systemctl", "reboot"])))
            return self.send_html(page("Riavvio", "<section><p>Il Pi si sta riavviando: torna tra un minuto."
                                                  "</p></section>", "status"))
        raise ValueError("Azione sconosciuta")


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"piclock admin on port {PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
