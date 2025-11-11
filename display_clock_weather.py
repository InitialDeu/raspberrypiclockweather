#!/usr/bin/env python3
import tkinter as tk
from tkinter import font
import requests
import datetime
import time
import threading
from io import BytesIO
from PIL import Image, ImageTk

# CONFIGURATION
CITY = "<YOUR CITY>"
API_KEY = "<YOUR_OPENWEATHER_API_KEY>"
UNITS = "metric"
LANG = "it"
UPDATE_INTERVAL = 600  # Weather refresh every 10 minutes

#COLORS
COLOR_DAY = "#FFFFFF"
COLOR_NIGHT = "#A0A0A0"
is_color_locked = False
last_weather_img = None
last_weather_text = ""
last_weather_desc = ""

def is_daytime():
    hour = datetime.datetime.now().hour
    return 9 <= hour < 20

def get_color_for_weather(desc):
    desc = desc.lower()
    if "pioggia" in desc or "temporale" in desc:
        return "#4FA3FF"   # bright blue
    elif "neve" in desc:
        return "#B3E5FC"   # cold cyan
    elif "nuvol" in desc or "coperto" in desc:
        return "#C0C0C0"   # light grey
    elif "sole" in desc or "sereno" in desc:
        return "#FFD75E"   # warm yellow
    else:
        return "#FFFFFF"

def get_weather():
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={API_KEY}&units={UNITS}&lang={LANG}"
        data = requests.get(url, timeout=5).json()
        temp = int(data["main"]["temp"])
        icon_code = data["weather"][0]["icon"]
        desc = data["weather"][0]["description"].capitalize()
        return temp, icon_code, desc
    except Exception:
        return None, None, "Errore meteo"

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
                img_data = requests.get(icon_url, timeout=5).content
                img = Image.open(BytesIO(img_data)).resize((100, 100))
                tk_img = ImageTk.PhotoImage(img)
                root.after(0, update_weather_ui, tk_img, f"{CITY} • {desc} • {temp}°C", desc)
            except Exception:
                root.after(0, update_weather_ui, None, f"{CITY} • {desc} • {temp}°C", desc)
        else:
            root.after(0, update_weather_ui, None, "Errore meteo", "errore")
        time.sleep(UPDATE_INTERVAL)

def update_weather_ui(img, text, desc):
    global is_color_locked, last_weather_img, last_weather_text, last_weather_desc

    # Save last weather info
    last_weather_img = img
    last_weather_text = text
    last_weather_desc = desc

    #Night mode: do NOT update colors based on weather
    now = datetime.datetime.now().time()
    sleep_start = datetime.time(22, 30)
    sleep_end = datetime.time(7, 30)
    if sleep_start <= now or now < sleep_end:
        if img:
            weather_icon.config(image=img)
            weather_icon.image = img
        weather_text.config(text=text)
        return

    # Day mode: normal weather color update
    if is_color_locked:
        return

    color = get_color_for_weather(desc)
    if not is_daytime() and color == "#FFFFFF":
        color = "#999999"

    weather_text.config(fg=color)
    time_label.config(fg=color)
    date_label.config(fg=color)

    if img:
        weather_icon.config(image=img)
        weather_icon.image = img

    weather_text.config(text=text)

#Screen Management
def manage_screen():
    root.after(5 * 60 * 1000, manage_screen)

def smooth_color_transition(start_color, end_color, duration=5):
    """Smooth transition between two hex colors."""
    steps = 50
    delay = int(duration * 1000 / steps)
    sr, sg, sb = [int(start_color[i:i+2], 16) for i in (1, 3, 5)]
    er, eg, eb = [int(end_color[i:i+2], 16) for i in (1, 3, 5)]

    def step(i=0):
        if i <= steps:
            r = sr + (er - sr) * i / steps
            g = sg + (eg - sg) * i / steps
            b = sb + (eb - sb) * i / steps
            color = f"#{int(r):02x}{int(g):02x}{int(b):02x}"
            date_label.config(fg=color)
            time_label.config(fg=color)
            weather_text.config(fg=color)
            root.after(delay, step, i + 1)

    step()

def adjust_night_colors():
    """Automatically handles day/night color transitions."""
    global is_color_locked
    now = datetime.datetime.now().time()
    sleep_start = datetime.time(22, 30)
    sleep_end = datetime.time(7, 30)

    is_color_locked = True

    if sleep_start <= now or now < sleep_end:
        #Night mode
        smooth_color_transition("#FFFFFF", "#995500")  # warm amber tone
        root.config(bg="#000000")
    else:
        #Day mode
        smooth_color_transition("#995500", "#FFFFFF")
        root.config(bg="black")

    # Unlock weather update after transition
    def unlock():
        global is_color_locked
        is_color_locked = False
    root.after(2000, unlock)

    # Repaint last weather info
    def restore_weather():
        global last_weather_img, last_weather_text, last_weather_desc
        if last_weather_text:
            update_weather_ui(last_weather_img, last_weather_text, last_weather_desc)
    root.after(2100, restore_weather)

    root.after(5 * 60 * 1000, adjust_night_colors)

#Tkinter Window setup
root = tk.Tk()
root.title("Clock & Weather")
root.configure(bg="black")
root.attributes("-fullscreen", True)

#Screen
screen_w = root.winfo_screenwidth()
screen_h = root.winfo_screenheight()

#Font scaling (~56% bigger)
date_font = font.Font(family="Helvetica", size=int(screen_h * 0.05 * 1.56), weight="bold")
time_font = font.Font(family="Helvetica", size=int(screen_h * 0.25 * 1.56), weight="bold")
weather_font = font.Font(family="Helvetica", size=int(screen_h * 0.035 * 1.56))

#Layout centered
container = tk.Frame(root, bg="black")
container.place(relx=0.5, rely=0.5, anchor="center")

date_label = tk.Label(container, font=date_font, bg="black", fg="#CCCCCC")
date_label.pack()

time_label = tk.Label(container, font=time_font, bg="black", fg="white")
time_label.pack(pady=(5, 5))

weather_frame = tk.Frame(container, bg="black")
weather_frame.pack()

weather_icon = tk.Label(weather_frame, bg="black")
weather_icon.pack(side="left", padx=5)

weather_text = tk.Label(weather_frame, font=weather_font, bg="black", fg="#BBBBBB")
weather_text.pack(side="left")

#Threads and loops
threading.Thread(target=update_weather_loop, daemon=True).start()
update_display()
manage_screen()
adjust_night_colors()

root.mainloop()

