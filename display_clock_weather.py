#!/usr/bin/env python3
"""Fullscreen clock & weather for Raspberry Pi (Tkinter + OpenWeatherMap).

The layout adapts automatically to any resolution and orientation:
font sizes are computed from the real screen size so every line fits.
"""
import datetime
import json
import logging
import os
import threading
import time
import tkinter as tk
from tkinter import font

import requests

# ============================================================
# CONFIGURATION
# ============================================================
# These can also come from the environment (e.g. /etc/clockweather.env loaded by systemd),
# so the API key never has to be written into this file or committed to git.
CITY = os.environ.get("CW_CITY", "Turin")
API_KEY = os.environ.get("OWM_API_KEY", "YOUR_API_KEY")
# Interface language: "en" (default) or "it" (optional Italian version).
# Also used for the OpenWeatherMap weather description.
LANG = os.environ.get("CW_LANG", "en").lower()
UNITS = "metric"
WEATHER_INTERVAL = 600        # seconds between weather refreshes
RETRY_INTERVAL = 60           # seconds before retrying after a failure
LOG_FILE = "/var/log/clockweather.log"

FG = "#FFD75E"
BG = "black"

# Night mode: dimmer text colour between NIGHT_START and NIGHT_END (HH:MM, may cross midnight).
# CW_NIGHT_FG lower = darker. Set CW_NIGHT=off to disable.
NIGHT_MODE = os.environ.get("CW_NIGHT", "on").lower() not in ("off", "0", "false", "no")
NIGHT_START = os.environ.get("CW_NIGHT_START", "22:30")
NIGHT_END = os.environ.get("CW_NIGHT_END", "07:30")
FG_NIGHT = os.environ.get("CW_NIGHT_FG", "#5C3300")      # dim amber
FADE_STEPS, FADE_MS = 30, 100                             # 3 s colour fade

# Watchdog: if the network thread does not complete a cycle for this long (e.g. stuck
# on DNS after a Wi-Fi drop), the app exits and systemd (Restart=always) restarts it.
WATCHDOG_S = 3 * WEATHER_INTERVAL
STALE_S = 3600                # weather older than this is shown as unavailable

# Optional management console / Wi-Fi manager (admin/): status written by piclock_wifi.py
NET_STATUS = os.environ.get("CW_NET_STATUS", "/run/piclock/net.json")
ADMIN_PORT = os.environ.get("CW_ADMIN_PORT", "80")
SHOW_URL_S = 120              # show the console address this long after start / after joining a network
MARGIN = 0.04                 # fraction of the screen kept free on each side

# Share of the usable height given to each line: date, time, info1, info2
LINE_WEIGHTS = {"date": 0.13, "time": 0.46, "info1": 0.085, "info2": 0.085}

# ============================================================
# LOGGING (falls back to the home dir if /var/log is not writable)
# ============================================================
if not (os.access(LOG_FILE, os.W_OK) or os.access(os.path.dirname(LOG_FILE), os.W_OK)):
    LOG_FILE = os.path.expanduser("~/clockweather.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.info("=== ClockWeather started ===")

# ============================================================
# ZODIAC & MOON DATA
# ============================================================
ZODIAC_ICONS = {
    "Ariete": "♈", "Toro": "♉", "Gemelli": "♊", "Cancro": "♋",
    "Leone": "♌", "Vergine": "♍", "Bilancia": "♎", "Scorpione": "♏",
    "Sagittario": "♐", "Capricorno": "♑", "Acquario": "♒", "Pesci": "♓",
}

if LANG not in ("en", "it"):
    logging.warning("Unsupported CW_LANG=%r, falling back to English", LANG)
    LANG = "en"

# ============================================================
# TEXTS (en / it)
# ============================================================
TEXTS = {
    "en": {
        "date_format": "%d %B %Y",
        "weather_unavailable": "weather unavailable",
        "saint_unavailable": "Saint unavailable",
        "hotspot_1": "Wi-Fi {ssid} · pw {pw}",
        "hotspot_2": "then open http://{ip}",
        "console": "Console: {url}",
        "moon": [
            "New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous",
            "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent",
        ],
    },
    "it": {
        "date_format": "%d/%m/%Y",          # no locale needed for month names
        "weather_unavailable": "meteo non disponibile",
        "saint_unavailable": "Santo non disponibile",
        "hotspot_1": "Wi-Fi {ssid} · pw {pw}",
        "hotspot_2": "poi apri http://{ip}",
        "console": "Console: {url}",
        "moon": [
            "Luna Nuova", "Luna Crescente", "Primo Quarto", "Gibbosa Crescente",
            "Luna Piena", "Gibbosa Calante", "Ultimo Quarto", "Luna Calante",
        ],
    },
}
T = TEXTS[LANG]

ZODIAC_EN = {
    "Ariete": "Aries", "Toro": "Taurus", "Gemelli": "Gemini",
    "Cancro": "Cancer", "Leone": "Leo", "Vergine": "Virgo",
    "Bilancia": "Libra", "Scorpione": "Scorpio",
    "Sagittario": "Sagittarius", "Capricorno": "Capricorn",
    "Acquario": "Aquarius", "Pesci": "Pisces",
}

MOON_ICONS = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]
SYNODIC_MONTH = 29.53058867
KNOWN_NEW_MOON = datetime.datetime(2000, 1, 6, 18, 14)  # UTC

# ============================================================
# ASTRONOMY
# ============================================================
def get_zodiac_sign(date):
    zodiac = [
        ("Capricorno", (1, 19)), ("Acquario", (2, 18)),
        ("Pesci", (3, 20)), ("Ariete", (4, 19)),
        ("Toro", (5, 20)), ("Gemelli", (6, 20)),
        ("Cancro", (7, 22)), ("Leone", (8, 22)),
        ("Vergine", (9, 22)), ("Bilancia", (10, 22)),
        ("Scorpione", (11, 21)), ("Sagittario", (12, 21)),
        ("Capricorno", (12, 31)),
    ]
    for sign, (m, d) in zodiac:
        if (date.month == m and date.day <= d) or date.month < m:
            return sign
    return "Capricorno"


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def get_moon_phase(utc):
    """Nearest of the 8 phases (rounded, so each phase is centred on its moment)."""
    days = (utc - KNOWN_NEW_MOON).total_seconds() / 86400
    age = (days / SYNODIC_MONTH) % 1
    index = int(age * 8 + 0.5) % 8
    return f"{MOON_ICONS[index]} {T['moon'][index]}"


def zodiac_label(sign_it):
    name = sign_it if LANG == "it" else ZODIAC_EN[sign_it]
    return f"{ZODIAC_ICONS[sign_it]} {name}"


# santodelgiorno.it already returns the title in the name ("Santa Cecilia", "Sant'Aldo"):
# keep it as is in Italian, translate the title in English.
SAINT_TITLES_EN = [
    ("Sant'", "St. "), ("Sant’", "St. "), ("Santa ", "St. "), ("Santo ", "St. "),
    ("Santi ", "Sts. "), ("Sante ", "Sts. "), ("San ", "St. "),
    ("Beata ", "Blessed "), ("Beato ", "Blessed "), ("Beati ", "Blessed "),
]


def saint_label(name):
    name = name.strip()
    if LANG == "it":
        return name
    for it_title, en_title in SAINT_TITLES_EN:
        if name.startswith(it_title):
            return en_title + name[len(it_title):].lstrip()
    return name

# ============================================================
# NETWORK DATA (background thread: the UI never freezes on slow requests)
# ============================================================
state = {"weather": None, "weather_ts": 0.0, "saint": None, "saint_day": None,
         "heartbeat": time.monotonic()}
state_lock = threading.Lock()


def fetch_weather():
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {"q": CITY, "appid": API_KEY, "units": UNITS, "lang": LANG}
    data = requests.get(url, params=params, timeout=10).json()
    if "main" not in data:
        raise ValueError(f"OpenWeatherMap error: {data.get('message', data)}")
    return round(data["main"]["temp"]), data["weather"][0]["description"].capitalize()


def fetch_saint():
    r = requests.get("https://www.santodelgiorno.it/santi.json", timeout=10).json()
    if isinstance(r, list) and r:
        return saint_label(r[0]["nome"])
    raise ValueError("Unexpected saint payload")


def seconds_to_midnight():
    now = datetime.datetime.now()
    nxt = datetime.datetime.combine(now.date() + datetime.timedelta(days=1), datetime.time(0, 0, 5))
    return (nxt - now).total_seconds()


def network_loop(stop):
    while not stop.is_set():
        wait = WEATHER_INTERVAL
        try:
            weather = fetch_weather()
            with state_lock:
                state["weather"], state["weather_ts"] = weather, time.monotonic()
            logging.info("Weather updated: %s", weather)
        except Exception:
            logging.exception("Weather fetch failed")
            wait = RETRY_INTERVAL

        today = datetime.date.today()
        if state["saint_day"] != today:
            try:
                saint = fetch_saint()
                with state_lock:
                    state["saint"], state["saint_day"] = saint, today
                logging.info("Saint updated: %s", saint)
            except Exception:
                logging.exception("Saint fetch failed")
                wait = min(wait, RETRY_INTERVAL)

        with state_lock:
            state["heartbeat"] = time.monotonic()
        # wake up right after midnight too, so the new saint shows up immediately
        stop.wait(min(wait, seconds_to_midnight()))

# ============================================================
# TKINTER WINDOW
# ============================================================
root = tk.Tk()
SCREEN_W = root.winfo_screenwidth()
SCREEN_H = root.winfo_screenheight()
root.geometry(f"{SCREEN_W}x{SCREEN_H}+0+0")   # works even without a window manager
root.attributes("-fullscreen", True)
root.configure(bg=BG, cursor="none")
USABLE_W = int(SCREEN_W * (1 - 2 * MARGIN))
USABLE_H = int(SCREEN_H * (1 - 2 * MARGIN))
logging.info("Screen %dx%d", SCREEN_W, SCREEN_H)

fonts = {
    key: font.Font(root, size=-int(USABLE_H * w),
                   weight="bold" if key in ("date", "time") else "normal")
    for key, w in LINE_WEIGHTS.items()
}

frame = tk.Frame(root, bg=BG)
frame.place(relx=0.5, rely=0.5, anchor="center")
labels = {}
for key in LINE_WEIGHTS:
    labels[key] = tk.Label(frame, font=fonts[key], fg=FG, bg=BG, bd=0, pady=0)
    labels[key].pack()

shown = {}


def set_line(key, text):
    """Show `text`, shrinking its font until it fits the screen width.
    Re-fits only when the text changes, so it costs nothing on normal ticks."""
    if shown.get(key) == text:
        return
    shown[key] = text
    f = fonts[key]
    px = int(USABLE_H * LINE_WEIGHTS[key])
    f.configure(size=-px)
    while px > 6 and f.measure(text) > USABLE_W:
        px -= 1
        f.configure(size=-px)
    labels[key].config(text=text)


def read_net_status():
    try:
        with open(NET_STATUS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


net_seen = {"mode": None, "url_until": time.monotonic() + SHOW_URL_S}


def info_lines(now):
    net = read_net_status()
    if net:
        if net.get("mode") == "ap":
            net_seen["mode"] = "ap"
            return (T["hotspot_1"].format(ssid=net.get("ap_ssid", "?"), pw=net.get("ap_password", "?")),
                    T["hotspot_2"].format(ip=net.get("ap_ip", "?")))
        if net_seen["mode"] == "ap":                       # just left the hotspot: show the new address
            net_seen["url_until"] = time.monotonic() + SHOW_URL_S
        net_seen["mode"] = net.get("mode")
    with state_lock:
        weather = state["weather"]
        if time.monotonic() - state["weather_ts"] > STALE_S:
            weather = None                                  # too old: don't show it as current
        saint = state["saint"] if state["saint_day"] == now.date() else None
    saint = saint or T["saint_unavailable"]
    if weather:
        temp, desc = weather
        line1 = f"{CITY} • {desc} • {temp}°C"
    else:
        line1 = f"{CITY} • {T['weather_unavailable']}"
    line2 = (f"{zodiac_label(get_zodiac_sign(now))}   "
             f"{get_moon_phase(utc_now())}   {saint}")
    if net and net.get("ip") and time.monotonic() < net_seen["url_until"]:
        port = "" if ADMIN_PORT == "80" else f":{ADMIN_PORT}"
        line2 = T["console"].format(url=f"http://{net['ip']}{port}")
    return line1, line2


def refresh_info():
    """Every 5 s pick up new network data (no-op if nothing changed) and check the watchdog."""
    line1, line2 = info_lines(datetime.datetime.now())
    set_line("info1", line1)
    set_line("info2", line2)
    with state_lock:
        silent = time.monotonic() - state["heartbeat"]
    if silent > WATCHDOG_S:
        logging.error("Network thread stuck for %d s: exiting so systemd restarts the app", silent)
        os._exit(1)
    root.after(5000, refresh_info)


# ============================================================
# NIGHT MODE
# ============================================================
def _hhmm(txt):
    h, m = txt.strip().split(":")
    return datetime.time(int(h), int(m))


def is_night(now):
    if not NIGHT_MODE:
        return False
    start, end, t = _hhmm(NIGHT_START), _hhmm(NIGHT_END), now.time()
    return (start <= t < end) if start < end else (t >= start or t < end)


def _rgb(color):
    r, g, b = root.winfo_rgb(color)                         # accepts "#RRGGBB" and colour names
    return r // 257, g // 257, b // 257


current_fg = FG


def fade_to(color, step=0, start=None):
    global current_fg
    start = start or _rgb(current_fg)
    end = _rgb(color)
    k = (step + 1) / FADE_STEPS
    mix = "#%02x%02x%02x" % tuple(round(a + (b - a) * k) for a, b in zip(start, end))
    for lbl in labels.values():
        lbl.config(fg=mix)
    if step + 1 < FADE_STEPS:
        root.after(FADE_MS, fade_to, color, step + 1, start)
    else:
        current_fg = color


night_on = None


def check_night(now):
    global night_on, current_fg
    night = is_night(now)
    if night != night_on:
        logging.info("Night mode %s", "ON" if night else "OFF")
        if night_on is None:                                 # at startup: no fade
            current_fg = FG_NIGHT if night else FG
            for lbl in labels.values():
                lbl.config(fg=current_fg)
        else:
            fade_to(FG_NIGHT if night else FG)
        night_on = night


def tick():
    now = datetime.datetime.now()
    set_line("date", now.strftime(T["date_format"]))
    set_line("time", now.strftime("%H:%M"))
    check_night(now)
    # Fire again right at the start of the next minute, so the clock never lags
    ms = (60 - now.second) * 1000 - now.microsecond // 1000 + 50
    root.after(ms, tick)


stop_event = threading.Event()
threading.Thread(target=network_loop, args=(stop_event,), daemon=True).start()

tick()
refresh_info()
try:
    root.mainloop()
finally:
    stop_event.set()
