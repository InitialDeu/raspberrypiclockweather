#!/usr/bin/env python3
import tkinter as tk
from tkinter import font
import requests
import datetime
import time
import threading
import logging
from io import BytesIO
from PIL import Image, ImageTk

# ============================================================
# CONFIGURATION
# ============================================================
CITY = "Turin"
API_KEY = "YOUR_API_KEY"
UNITS = "metric"
LANG = "en"
UPDATE_INTERVAL = 600
LOG_FILE = "/var/log/clockweather.log"

cached_day = None
cached_saint = None
cached_moon = None

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logging.info("=== ClockWeather started (EN) ===")

# ============================================================
# ZODIAC & MOON DATA
# ============================================================
ZODIAC_ICONS = {
    "Ariete": "♈", "Toro": "♉", "Gemelli": "♊", "Cancro": "♋",
    "Leone": "♌", "Vergine": "♍", "Bilancia": "♎", "Scorpione": "♏",
    "Sagittario": "♐", "Capricorno": "♑", "Acquario": "♒", "Pesci": "♓"
}

ZODIAC_EN = {
    "Ariete": "Aries", "Toro": "Taurus", "Gemelli": "Gemini",
    "Cancro": "Cancer", "Leone": "Leo", "Vergine": "Virgo",
    "Bilancia": "Libra", "Scorpione": "Scorpio",
    "Sagittario": "Sagittarius", "Capricorno": "Capricorn",
    "Acquario": "Aquarius", "Pesci": "Pisces"
}

MOON_ICONS = ["🌑","🌒","🌓","🌔","🌕","🌖","🌗","🌘"]
MOON_NAMES_EN = [
    "New Moon", "Waxing Crescent", "First Quarter",
    "Waxing Gibbous", "Full Moon",
    "Waning Gibbous", "Last Quarter", "Waning Crescent"
]

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
        ("Capricorno", (12, 31))
    ]
    for sign, (m, d) in zodiac:
        if (date.month == m and date.day <= d) or date.month < m:
            return sign


def get_moon_phase(date):
    global cached_day, cached_moon
    today = date.date()

    if cached_day == today and cached_moon:
        return cached_moon

    known_new_moon = datetime.date(2000, 1, 6)
    days = (today - known_new_moon).days
    index = int(((days / 29.53058867) % 1) * 8)

    cached_moon = f"{MOON_ICONS[index]} {MOON_NAMES_EN[index]}"
    return cached_moon


def get_saint_of_the_day():
    global cached_day, cached_saint
    today = datetime.date.today()

    if cached_day == today and cached_saint:
        return cached_saint

    try:
        url = "https://www.santodelgiorno.it/santi.json"
        logging.info("Fetching saint of the day")
        r = requests.get(url, timeout=5).json()

        if isinstance(r, list) and r:
            cached_day = today
            cached_saint = f"Saint {r[0]['nome']}"
            return cached_saint

    except Exception:
        logging.exception("Saint fetch failed")

    return "Saint unavailable"

# ============================================================
# UI / WEATHER (unchanged logic)
# ============================================================
def get_weather():
    url = (
        "https://api.openweathermap.org/data/2.5/weather"
        f"?q={CITY}&appid={API_KEY}&units={UNITS}&lang={LANG}"
    )
    data = requests.get(url, timeout=5).json()
    return (
        int(data["main"]["temp"]),
        data["weather"][0]["icon"],
        data["weather"][0]["description"].capitalize()
    )

# ============================================================
# TKINTER WINDOW
# ============================================================
root = tk.Tk()
root.attributes("-fullscreen", True)
root.configure(bg="black")

screen_h = root.winfo_screenheight()

date_font = font.Font(size=int(screen_h * 0.08), weight="bold")
time_font = font.Font(size=int(screen_h * 0.35), weight="bold")
info_font = font.Font(size=int(screen_h * 0.04))

date_label = tk.Label(root, font=date_font, fg="#FFD75E", bg="black")
date_label.pack()

time_label = tk.Label(root, font=time_font, fg="#FFD75E", bg="black")
time_label.pack()

info_label = tk.Label(root, font=info_font, fg="#FFD75E", bg="black")
info_label.pack(pady=10)

def update_ui():
    now = datetime.datetime.now()
    zodiac_it = get_zodiac_sign(now)
    zodiac = ZODIAC_EN[zodiac_it]
    moon = get_moon_phase(now)
    saint = get_saint_of_the_day()

    temp, _, desc = get_weather()

    date_label.config(text=now.strftime("%d %B %Y"))
    time_label.config(text=now.strftime("%H:%M"))
    info_label.config(
        text=f"{ZODIAC_ICONS[zodiac_it]} {zodiac}   {moon}   {saint}   {temp}°C {desc}"
    )

    root.after(60000, update_ui)

update_ui()
root.mainloop()

