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

# -----------------------
# Configuration
# -----------------------

CITY = "<YOUR CITY>"
API_KEY = "<YOUR_OPENWEATHERMAP_API_KEY>"
UNITS = "metric"
LANG = "en"
UPDATE_INTERVAL = 600  # seconds between weather updates

LOG_FILE = "/var/log/clockweather.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# Internal state
is_color_locked = False
last_weather_img = None
last_weather_text = ""
last_weather_desc = ""


# -----------------------
# Helpers
# -----------------------

def is_daytime():
    hour = datetime.datetime.now().hour
    return 9 <= hour < 20


def is_night():
    now = datetime.datetime.now().time()
    start = datetime.time(22, 30)
    end = datetime.time(7, 30)
    return (now >= start) or (now < end)


def get_color_for_weather(desc):
    desc = desc.lower()
    if "pioggia" in desc or "temporale" in desc:
        return "#4FA3FF"
    elif "neve" in desc:
        return "#B3E5FC"
    elif "nuvol" in desc or "coperto" in desc:
        return "#C0C0C0"
    elif "sereno" in desc or "sole" in desc:
        return "#FFD75E"
    return "#FFFFFF"


def get_weather():
    try:
        url = (
            f"https://api.openweathermap.org/data/2.5/weather?"
            f"q={CITY}&appid={API_KEY}&units={UNITS}&lang={LANG}"
        )
        logging.info(f"Fetching weather from API: {url}")
        data = requests.get(url, timeout=5).json()

        temp = int(data["main"]["temp"])
        icon_code = data["weather"][0]["icon"]
        desc = data["weather"][0]["description"].capitalize()

        return temp, icon_code, desc

    except Exception as e:
        logging.error(f"Weather fetch error: {e}")
        return None, None, "Weather error"


# -----------------------
# UI Update Functions
# -----------------------

def update_display():
    now = datetime.datetime.now()
    date_label.config(text=now.strftime("%d/%m/%Y"))
    time_label.config(text=now.strftime("%H:%M"))
    root.after(1000, update_display)


def update_weather_loop():
    while True:
        temp, icon_code, desc = get_weather()

        if temp is not None and icon_code:
            try:
                icon_url = f"http://openweathermap.org/img/wn/{icon_code}@2x.png"
                data = requests.get(icon_url, timeout=5).content
                img = Image.open(BytesIO(data)).resize((100, 100))
                tk_img = ImageTk.PhotoImage(img)
                text = f"{CITY} • {desc} • {temp}°C"

                root.after(0, update_weather_ui, tk_img, text, desc)
                logging.info(f"Weather updated: {desc}, {temp}°C, icon={icon_code}")

            except Exception as e:
                logging.error(f"Weather icon error: {e}")
                root.after(0, update_weather_ui, None, text, desc)

        else:
            root.after(0, update_weather_ui, None, "Weather error", "error")

        time.sleep(UPDATE_INTERVAL)


def update_weather_ui(img, text, desc):
    global is_color_locked, last_weather_img, last_weather_text, last_weather_desc

    last_weather_img = img
    last_weather_text = text
    last_weather_desc = desc

    # Always draw icon and text
    if img:
        weather_icon.config(image=img)
        weather_icon.image = img

    weather_text.config(text=text)

    # Night mode: do not apply weather-based colors
    if is_night():
        return

    # If locked (during transition), skip color update only
    if is_color_locked:
        return

    # Apply dynamic weather color
    color = get_color_for_weather(desc)
    if not is_daytime() and color == "#FFFFFF":
        color = "#999999"

    for lbl in (date_label, time_label, weather_text):
        lbl.config(fg=color)


# -----------------------
# Night Mode Control
# -----------------------

def adjust_night_colors():
    global is_color_locked

    is_color_locked = True

    if is_night():
        color = "#995500"
        bg = "#000000"
        logging.info("Night mode ON")
    else:
        color = "#FFFFFF"
        bg = "black"
        logging.info("Night mode OFF")

    for lbl in (date_label, time_label, weather_text):
        lbl.config(fg=color, bg=bg)
    root.config(bg=bg)

    def unlock():
        global is_color_locked
        is_color_locked = False

    root.after(1000, unlock)

    def restore_weather():
        if last_weather_text:
            update_weather_ui(last_weather_img, last_weather_text, last_weather_desc)

    root.after(1100, restore_weather)
    root.after(5 * 60 * 1000, adjust_night_colors)


# -----------------------
# UI Setup
# -----------------------

root = tk.Tk()
root.title("Clock & Weather")
root.configure(bg="black")
root.attributes("-fullscreen", True)

screen_w = root.winfo_screenwidth()
screen_h = root.winfo_screenheight()

date_font = font.Font(family="Helvetica", size=int(screen_h * 0.05 * 1.56), weight="bold")
time_font = font.Font(family="Helvetica", size=int(screen_h * 0.25 * 1.56), weight="bold")
weather_font = font.Font(family="Helvetica", size=int(screen_h * 0.035 * 1.56))

container = tk.Frame(root, bg="black")
container.place(relx=0.5, rely=0.5, anchor="center")

date_label = tk.Label(container, font=date_font, bg="black", fg="#FFFFFF")
date_label.pack()

time_label = tk.Label(container, font=time_font, bg="black", fg="#FFFFFF")
time_label.pack(pady=(5, 5))

weather_frame = tk.Frame(container, bg="black")
weather_frame.pack()

weather_icon = tk.Label(weather_frame, bg="black")
weather_icon.pack(side="left", padx=5)

weather_text = tk.Label(weather_frame, font=weather_font, bg="black", fg="#CCCCCC")
weather_text.pack(side="left")


# -----------------------
# Start Threads and Loop
# -----------------------

threading.Thread(target=update_weather_loop, daemon=True).start()

logging.info("=== ClockWeather started ===")
update_display()
adjust_night_colors()

root.mainloop()

