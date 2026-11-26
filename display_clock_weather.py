#!/usr/bin/env python3
"""Fullscreen clock & weather for Raspberry Pi (Tkinter + OpenWeatherMap).

The layout adapts automatically to any resolution and orientation, also when it changes
while running: font sizes are computed from the real screen size so every line fits.
"""
import datetime
import html
import json
import logging
import os
import re
import subprocess
import threading
import time
import tkinter as tk
from tkinter import font

import requests

import weather_icons

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
FORECAST_INTERVAL = 1800      # seconds between forecast refreshes
SYNC_CHECK_INTERVAL = 30      # seconds between checks of the clock synchronisation
ROTATE_S = 8                  # seconds each detail (forecast, wind, ...) stays on screen
RAIN_POP = 0.3                # forecast probability from which rain or snow is announced
RETRY_INTERVAL = 60           # seconds before retrying after a failure
LOOP_TICK = 15                # seconds between checks of what is due (follows clock jumps after NTP sync)
SAINT_URL = "https://www.santodelgiorno.it/santi.json"
SAINT_DAYS_AHEAD = 7          # saints downloaded in advance, so a network outage doesn't matter
SAINT_CACHE = os.path.expanduser(os.environ.get("CW_SAINT_CACHE", "~/.cache/clockweather/saints.json"))
LOG_FILE = "/var/log/clockweather.log"

FG = "#FFD75E"
BG = "black"

# Night mode: dimmer text colour. CW_NIGHT=on uses NIGHT_START..NIGHT_END (HH:MM, may cross
# midnight), CW_NIGHT=sun goes from sunset to sunrise (the fixed hours are used until the
# first weather data arrives), CW_NIGHT=off disables it. CW_NIGHT_FG lower = darker.
NIGHT_MODE = os.environ.get("CW_NIGHT", "on").strip().lower()
if NIGHT_MODE in ("off", "0", "false", "no"):
    NIGHT_MODE = "off"
elif NIGHT_MODE != "sun":
    NIGHT_MODE = "on"
NIGHT_START = os.environ.get("CW_NIGHT_START", "22:30")
NIGHT_END = os.environ.get("CW_NIGHT_END", "07:30")
FG_NIGHT = os.environ.get("CW_NIGHT_FG", "#5C3300")      # dim amber
FADE_STEPS, FADE_MS = 30, 100                             # 3 s colour fade

# Watchdog: if the network thread does not complete a cycle for this long (e.g. stuck
# on DNS after a Wi-Fi drop), the app exits and systemd (Restart=always) restarts it.
WATCHDOG_S = 3 * WEATHER_INTERVAL
STALE_S = 3600                # weather older than this is shown as unavailable
FORECAST_STALE_S = 3 * 3600   # same for the forecast

# Optional management console / Wi-Fi manager (admin/): status written by piclock_wifi.py
NET_STATUS = os.environ.get("CW_NET_STATUS", "/run/piclock/net.json")
ADMIN_PORT = os.environ.get("CW_ADMIN_PORT", "80")
SHOW_URL_S = 120              # show the console address this long after start / after joining a network
MARGIN = 0.04                 # fraction of the screen kept free on each side
MIN_PX = 7                    # smallest font size

# Rows from top to bottom with their share of the usable height. In portrait the weather
# icon has its own row, in landscape it sits to the left of the weather text.
LAYOUTS = {
    "landscape": (("date", 0.11), ("time", 0.39), ("weather", 0.115),
                  ("detail", 0.072), ("astro", 0.072), ("saint", 0.072)),
    "portrait": (("date", 0.065), ("time", 0.22), ("icon", 0.16), ("weather", 0.05),
                 ("detail", 0.04), ("astro", 0.04), ("saint", 0.04)),
}
# Portrait screens are narrow but tall: these rows are split in two at the separator
PORTRAIT_SPLIT = {"weather": (" • ", " · "), "astro": ("   ",)}
BOLD = ("date", "time")

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
        "unsynced": "⚠ Time not synchronised",
        "forecast": "Next 24 h: min {low}° · max {high}°",
        "rain_from": "Rain from {time} ({pop}%)",
        "rain_now": "Rain in the next hours ({pop}%)",
        "snow_from": "Snow from {time} ({pop}%)",
        "snow_now": "Snow in the next hours ({pop}%)",
        "dry": "Rain unlikely in the next 12 h",
        "comfort": "Feels like {feels}° · Humidity {humidity}%",
        "wind": "Wind {speed} km/h {direction}",
        "sun": "Sunrise {rise} · Sunset {set}",
        "compass": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
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
        "unsynced": "⚠ Ora non sincronizzata",
        "forecast": "Prossime 24 ore: min {low}° · max {high}°",
        "rain_from": "Pioggia dalle {time} ({pop}%)",
        "rain_now": "Pioggia nelle prossime ore ({pop}%)",
        "snow_from": "Neve dalle {time} ({pop}%)",
        "snow_now": "Neve nelle prossime ore ({pop}%)",
        "dry": "Pioggia improbabile per 12 ore",
        "comfort": "Percepita {feels}° · Umidità {humidity}%",
        "wind": "Vento {speed} km/h {direction}",
        "sun": "Alba {rise} · Tramonto {set}",
        "compass": ["N", "NE", "E", "SE", "S", "SO", "O", "NO"],
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
    name = re.sub(r"^(Sant['’])\s+", r"\1", name.strip())       # the site writes "Sant' Umile"
    if LANG == "it":
        return name
    for it_title, en_title in SAINT_TITLES_EN:
        if name.startswith(it_title):
            return en_title + name[len(it_title):].lstrip()
    return name

# ============================================================
# NETWORK DATA (background thread: the UI never freezes on slow requests)
# ============================================================
state = {"weather": None, "weather_ts": 0.0,
         "forecast": None, "forecast_ts": 0.0,
         "sun": None,                      # (sunrise, sunset) as local times
         "synced": None,                   # False = system time not synchronised
         "saints": {},                     # {date: name as returned by the site}
         "heartbeat": time.monotonic()}
state_lock = threading.Lock()


def owm_get(endpoint, **extra):
    url = f"https://api.openweathermap.org/data/2.5/{endpoint}"
    params = {"q": CITY, "appid": API_KEY, "units": UNITS, "lang": LANG, **extra}
    return requests.get(url, params=params, timeout=10).json()


def fetch_weather():
    data = owm_get("weather")
    if "main" not in data:
        raise ValueError(f"OpenWeatherMap error: {data.get('message', data)}")
    main, sky = data["main"], data["weather"][0]
    wind, sun = data.get("wind", {}), data.get("sys", {})
    return {"temp": round(main["temp"]), "desc": sky["description"].capitalize(),
            "code": int(sky["id"]), "night": str(sky.get("icon", "")).endswith("n"),
            "feels": main.get("feels_like"), "humidity": main.get("humidity"),
            "wind": wind.get("speed"), "wind_deg": wind.get("deg"),
            "sunrise": sun.get("sunrise"), "sunset": sun.get("sunset")}


def fetch_forecast():
    """The next 27 hours in 3-hour steps."""
    data = owm_get("forecast", cnt=9)
    if not data.get("list"):
        raise ValueError(f"OpenWeatherMap error: {data.get('message', data)}")
    return [{"ts": slot["dt"], "low": slot["main"]["temp_min"], "high": slot["main"]["temp_max"],
             "code": int(slot["weather"][0]["id"]), "pop": slot.get("pop", 0)}
            for slot in data["list"]]


def check_synced():
    """True/False as reported by systemd, None if it can't be known."""
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return {"yes": True, "no": False}.get(out)


def safe_error(e):
    """One log line for a failed request, without the API key that is part of the URL."""
    text = f"{type(e).__name__}: {e}"
    return text.replace(API_KEY, "***") if API_KEY else text


def fetch_saint(day):
    """Main saint of `day`, asked for by date. The reply is checked because the site can
    answer with another day (stale server cache, date parameter ignored). Only month and
    day are compared: the year in the reply is not always the requested one."""
    r = requests.get(SAINT_URL, params={"data": day.isoformat()}, timeout=10)
    r.raise_for_status()
    payload = r.json()
    wanted = day.strftime("-%m-%d")
    matches = [s for s in (payload if isinstance(payload, list) else [])
               if isinstance(s, dict) and str(s.get("data", "")).endswith(wanted)
               and str(s.get("nome") or "").strip()]
    if not matches:
        raise ValueError(f"no saint for {day} in the reply")
    best = next((s for s in matches if str(s.get("default")) == "1"), matches[0])
    return html.unescape(str(best["nome"])).strip()


def load_saints():
    """Saints saved by a previous run, so they are available right after a reboot."""
    try:
        with open(SAINT_CACHE, encoding="utf-8") as f:
            data = json.load(f)
        return {datetime.date.fromisoformat(day): name for day, name in data.items()
                if isinstance(name, str)}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, AttributeError):
        logging.warning("Saint cache unreadable, ignoring it", exc_info=True)
        return {}


def save_saints(saints):
    try:
        os.makedirs(os.path.dirname(SAINT_CACHE), exist_ok=True)
        tmp = SAINT_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({day.isoformat(): name for day, name in sorted(saints.items())}, f,
                      ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())                     # survive a power cut
        os.replace(tmp, SAINT_CACHE)
    except OSError:
        logging.warning("Could not save the saint cache", exc_info=True)


def update_saints(today):
    """Download the missing saints from `today` on (today first) and drop the old ones."""
    oldest = today - datetime.timedelta(days=1)
    with state_lock:
        saints = {day: name for day, name in state["saints"].items() if day >= oldest}
    changed = False
    for offset in range(SAINT_DAYS_AHEAD + 1):
        day = today + datetime.timedelta(days=offset)
        if day in saints:
            continue
        try:
            saints[day] = fetch_saint(day)
        except Exception as e:
            logging.warning("Saint fetch failed for %s: %s", day, e)
            if isinstance(e, (requests.ConnectionError, requests.Timeout)):
                break                                # no network: the other days would fail too
            continue
        changed = True
        logging.info("Saint for %s: %s", day, saints[day])
        with state_lock:
            state["saints"] = dict(saints)           # show it without waiting for the other days
    if changed:
        save_saints(saints)


def refresh_weather():
    weather = fetch_weather()
    sun = None
    if weather["sunrise"] and weather["sunset"]:
        sun = tuple(datetime.datetime.fromtimestamp(weather[k]).time()
                    for k in ("sunrise", "sunset"))
    with state_lock:
        state["weather"], state["weather_ts"] = weather, time.monotonic()
        state["sun"] = sun or state["sun"]
    logging.info("Weather updated: %s°C %s", weather["temp"], weather["desc"])


def refresh_forecast():
    forecast = fetch_forecast()
    with state_lock:
        state["forecast"], state["forecast_ts"] = forecast, time.monotonic()
    logging.info("Forecast updated")


def network_loop(stop):
    jobs = ((refresh_weather, "Weather", WEATHER_INTERVAL),
            (refresh_forecast, "Forecast", FORECAST_INTERVAL))
    due = {name: 0.0 for _, name, _ in jobs}         # time.monotonic() deadlines
    next_sync = next_saints = 0.0
    while not stop.is_set():
        if time.monotonic() >= next_sync:
            synced = check_synced()
            with state_lock:
                if synced is not None and synced != state["synced"]:
                    logging.info("System time synchronised: %s", "yes" if synced else "no")
                state["synced"] = synced
            next_sync = time.monotonic() + SYNC_CHECK_INTERVAL

        for job, name, interval in jobs:
            if time.monotonic() >= due[name]:
                wait = interval
                try:
                    job()
                except Exception as e:
                    logging.warning("%s fetch failed: %s", name, safe_error(e))
                    wait = RETRY_INTERVAL
                due[name] = time.monotonic() + wait

        if time.monotonic() >= next_saints:
            update_saints(datetime.date.today())     # does nothing if no day is missing
            next_saints = time.monotonic() + RETRY_INTERVAL

        with state_lock:
            state["heartbeat"] = time.monotonic()
        stop.wait(LOOP_TICK)

# ============================================================
# SCREEN CONTENT
# ============================================================
def forecast_lines(slots, now_ts):
    upcoming = [x for x in slots if now_ts - 3 * 3600 < x["ts"] < now_ts + 24 * 3600]
    if not upcoming:
        return []
    lines = [T["forecast"].format(low=round(min(x["low"] for x in upcoming)),
                                  high=round(max(x["high"] for x in upcoming)))]
    soon = [x for x in upcoming if x["ts"] < now_ts + 12 * 3600]
    wet = next((x for x in soon
                if x["code"] // 100 in (2, 3, 5, 6) and x["pop"] >= RAIN_POP), None)
    if wet is None:
        lines.append(T["dry"])
    else:
        kind = "snow" if wet["code"] // 100 == 6 else "rain"
        key = kind + ("_now" if wet["ts"] <= now_ts else "_from")
        start = datetime.datetime.fromtimestamp(wet["ts"]).strftime("%H:%M")
        lines.append(T[key].format(time=start, pop=round(wet["pop"] * 100)))
    return lines


def current_lines(weather, sun):
    lines = []
    if weather["feels"] is not None and weather["humidity"] is not None:
        lines.append(T["comfort"].format(feels=round(weather["feels"]),
                                         humidity=weather["humidity"]))
    if weather["wind"] is not None:
        deg = weather["wind_deg"]
        direction = "" if deg is None else T["compass"][int(deg / 45 + 0.5) % 8]
        lines.append(T["wind"].format(speed=round(weather["wind"] * 3.6),
                                      direction=direction).strip())
    if sun:
        lines.append(T["sun"].format(rise=sun[0].strftime("%H:%M"), set=sun[1].strftime("%H:%M")))
    return lines


def read_net_status():
    try:
        with open(NET_STATUS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


net_seen = {"mode": None, "url_until": time.monotonic() + SHOW_URL_S}


def info_rows(now):
    """Text of the rows below the clock, plus "icon": (condition code, night) or None."""
    net = read_net_status()
    if net:
        if net.get("mode") == "ap":
            net_seen["mode"] = "ap"
            return {"weather": T["hotspot_1"].format(ssid=net.get("ap_ssid", "?"),
                                                     pw=net.get("ap_password", "?")),
                    "detail": T["hotspot_2"].format(ip=net.get("ap_ip", "?")),
                    "detail_all": [], "astro": "", "saint": "", "icon": None}
        if net_seen["mode"] == "ap":                       # just left the hotspot: show the new address
            net_seen["url_until"] = time.monotonic() + SHOW_URL_S
        net_seen["mode"] = net.get("mode")
    with state_lock:
        age = time.monotonic()
        weather = state["weather"] if age - state["weather_ts"] <= STALE_S else None
        forecast = state["forecast"] if age - state["forecast_ts"] <= FORECAST_STALE_S else None
        sun, synced = state["sun"], state["synced"]
        saint = state["saints"].get(now.date())             # never another day's saint

    # the detail row rotates through the forecast and the current conditions
    pages = forecast_lines(forecast, now.timestamp()) if forecast else []
    if weather:
        pages += current_lines(weather, sun)
    detail = pages[int(time.monotonic() / ROTATE_S) % len(pages)] if pages else ""
    if synced is False:
        detail = T["unsynced"]
    elif net and net.get("ip") and time.monotonic() < net_seen["url_until"]:
        port = "" if ADMIN_PORT == "80" else f":{ADMIN_PORT}"
        detail = T["console"].format(url=f"http://{net['ip']}{port}")

    if weather:
        line = f"{CITY} • {weather['desc']} • {weather['temp']}°C"
        icon = (weather["code"], weather["night"])
    else:
        line, icon = f"{CITY} • {T['weather_unavailable']}", None
    return {"weather": line, "detail": detail, "detail_all": [detail] + pages,
            "astro": f"{zodiac_label(get_zodiac_sign(now))}   {get_moon_phase(utc_now())}",
            "saint": saint_label(saint) if saint else T["saint_unavailable"], "icon": icon}


# ============================================================
# TKINTER WINDOW
# ============================================================
root = tk.Tk()
# explicit size: works even without a window manager
root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")
root.attributes("-fullscreen", True)
root.configure(bg=BG, cursor="none")

fonts = {key: font.Font(root, size=-20, weight="bold" if key in BOLD else "normal")
         for key, _ in LAYOUTS["landscape"]}
frame = tk.Frame(root, bg=BG)
frame.place(relx=0.5, rely=0.5, anchor="center")

screen = {}          # geometry of the current layout, filled by apply_layout()
labels = {}
shown = {}
ink = FG             # current text and icon colour


def apply_layout(width, height):
    """(Re)build the rows for a screen of width x height pixels."""
    usable_w = int(width * (1 - 2 * MARGIN))
    usable_h = int(height * (1 - 2 * MARGIN))
    portrait = height > width
    rows = LAYOUTS["portrait" if portrait else "landscape"]
    # Line heights depend on the installed fonts: shrink all rows if they don't fit
    total = 0
    for key, share in rows:
        px = max(MIN_PX, int(usable_h * share))
        if key in fonts:
            fonts[key].configure(size=-px)
            lines = 2 if portrait and key in PORTRAIT_SPLIT else 1
            total += lines * fonts[key].metrics("linespace")
        else:
            total += px
    scale = min(1.0, usable_h / max(total, 1))
    row_px = {key: max(MIN_PX, int(usable_h * share * scale)) for key, share in rows}
    for key in fonts:
        fonts[key].configure(size=-row_px[key])

    for child in frame.winfo_children():
        child.destroy()
    labels.clear()
    shown.clear()
    icon_size = (min(row_px["icon"], usable_w) if portrait
                 else fonts["weather"].metrics("linespace"))
    holder = None
    for key, _ in rows:
        if key == "icon":
            holder = tk.Frame(frame, bg=BG, width=icon_size, height=icon_size)
            holder.pack_propagate(False)                  # keep the space when there is no icon
            holder.pack()
            continue
        parent = frame
        if key == "weather":
            parent = tk.Frame(frame, bg=BG)
            parent.pack()
            holder = holder or parent
        labels[key] = tk.Label(parent, font=fonts[key], fg=ink, bg=BG, bd=0, pady=0)
        labels[key].pack(side="left" if key == "weather" else "top")
    canvas = tk.Canvas(holder, width=icon_size, height=icon_size, bg=BG, bd=0, highlightthickness=0)
    screen.update(size=(width, height), usable_w=usable_w, row_px=row_px, portrait=portrait,
                  canvas=canvas, icon_size=icon_size, icon=None)
    logging.info("Screen %dx%d (%s layout)", width, height,
                 "portrait" if portrait else "landscape")
    now = datetime.datetime.now()
    show_clock(now)
    show_info(now)


def set_line(key, text, reserved=0, sizing=()):
    """Show `text`, shrinking its font until every line fits the screen width.
    The texts in `sizing` must fit too, so a row that rotates keeps the same size.
    Re-fits only when something changes, so it costs nothing on normal ticks."""
    available = screen["usable_w"] - reserved
    wanted = (text, available, tuple(sizing))
    if shown.get(key) == wanted:
        return
    shown[key] = wanted
    f = fonts[key]
    lines = [line for t in (text, *sizing) for line in t.split("\n")]

    def widest():
        return max(f.measure(line) for line in lines)

    px = screen["row_px"][key]
    f.configure(size=-px)
    width = widest()
    if width > available:
        px = max(MIN_PX, px * available // width + 1)      # close guess, then step down
        f.configure(size=-px)
        while px > MIN_PX and widest() > available:
            px -= 1
            f.configure(size=-px)
    labels[key].config(text=text)


def draw_icon():
    if screen.get("icon"):
        code, night = screen["icon"]
        weather_icons.draw(screen["canvas"], screen["icon_size"], code, night, ink, BG)


def show_icon(icon):
    if icon == screen["icon"]:
        return
    screen["icon"] = icon
    canvas = screen["canvas"]
    if icon is None:
        canvas.pack_forget()
        return
    draw_icon()
    if screen["portrait"]:
        canvas.pack()
    else:
        canvas.pack(side="left", before=labels["weather"], padx=(0, screen["icon_size"] // 4))


def show_clock(now):
    set_line("date", now.strftime(T["date_format"]))
    set_line("time", now.strftime("%H:%M"))


def show_info(now):
    rows = info_rows(now)
    icon, pages = rows.pop("icon"), rows.pop("detail_all")
    show_icon(icon)
    beside = icon and not screen["portrait"]
    for key, text in rows.items():
        if screen["portrait"]:
            for separator in PORTRAIT_SPLIT.get(key, ()):
                text = text.replace(separator, "\n", 1)
        reserved = screen["icon_size"] * 5 // 4 if beside and key == "weather" else 0
        set_line(key, text, reserved, pages if key == "detail" else ())


def refresh_info():
    """Every second pick up new data (no-op if nothing changed) and check the watchdog."""
    root.after(1000, refresh_info)                   # first, so an error can't stop the updates
    now = datetime.datetime.now()
    check_night(now)
    show_info(now)
    with state_lock:
        silent = time.monotonic() - state["heartbeat"]
    if silent > WATCHDOG_S:
        logging.error("Network thread stuck for %d s: exiting so systemd restarts the app", silent)
        os._exit(1)


resize_job = None


def on_resize(event):
    """Follow resolution or rotation changes made while the clock is running."""
    global resize_job
    if event.widget is not root:
        return
    if resize_job:
        root.after_cancel(resize_job)
        resize_job = None
    size = (event.width, event.height)
    if size != screen["size"] and min(size) >= 100:
        resize_job = root.after(500, apply_layout, *size)      # wait until it settles


# ============================================================
# NIGHT MODE
# ============================================================
def _hhmm(txt):
    h, m = txt.strip().split(":")
    return datetime.time(int(h), int(m))


def is_night(now, sun=None):
    if NIGHT_MODE == "off":
        return False
    if NIGHT_MODE == "sun" and sun:
        end, start = sun                                    # from sunset to sunrise
    else:
        start, end = _hhmm(NIGHT_START), _hhmm(NIGHT_END)
    t = now.time()
    return (start <= t < end) if start < end else (t >= start or t < end)


def _rgb(color):
    r, g, b = root.winfo_rgb(color)                         # accepts "#RRGGBB" and colour names
    return r // 257, g // 257, b // 257


def paint(color):
    global ink
    ink = color
    for lbl in labels.values():
        lbl.config(fg=color)
    draw_icon()


def fade_to(color, step=0, start=None):
    start = start or _rgb(ink)
    end = _rgb(color)
    k = (step + 1) / FADE_STEPS
    paint("#%02x%02x%02x" % tuple(round(a + (b - a) * k) for a, b in zip(start, end)))
    if step + 1 < FADE_STEPS:
        root.after(FADE_MS, fade_to, color, step + 1, start)


night_on = None


def check_night(now):
    global night_on
    with state_lock:
        sun = state["sun"]
    night = is_night(now, sun)
    if night != night_on:
        logging.info("Night mode %s", "ON" if night else "OFF")
        if night_on is None:                                 # at startup: no fade
            paint(FG_NIGHT if night else FG)
        else:
            fade_to(FG_NIGHT if night else FG)
        night_on = night


def tick():
    now = datetime.datetime.now()
    # Fire again right at the start of the next minute, so the clock never lags
    ms = (60 - now.second) * 1000 - now.microsecond // 1000 + 50
    root.after(ms, tick)
    show_clock(now)


stop_event = threading.Event()
state["saints"] = load_saints()
threading.Thread(target=network_loop, args=(stop_event,), daemon=True).start()

apply_layout(root.winfo_screenwidth(), root.winfo_screenheight())
root.bind("<Configure>", on_resize)
tick()
refresh_info()
try:
    root.mainloop()
finally:
    stop_event.set()
