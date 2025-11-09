#!/usr/bin/env python3
import tkinter as tk
from tkinter import font
import requests
import datetime
import time
import threading
from io import BytesIO
from PIL import Image, ImageTk

#CONFIGURATION
CITY = "<YOUR CITY>"
API_KEY = "<YOUR_OPENWEATHER_API_KEY>"
UNITS = "metric"
LANG = "en"
UPDATE_INTERVAL = 600  # every 10 minutes
NIGHT_INTENSITY = 0.25

#Default colors
COLOR_DAY = "#FFFFFF"
COLOR_NIGHT = "#A0A0A0"

#Global variables
is_color_locked = False
last_weather_img = None
last_weather_text = ""
last_weather_desc = ""


#Detect if it's daytime
def is_daytime():
    hour = datetime.datetime.now().hour
    return 9 <= hour < 20


#Choose color based on weather description
def get_color_for_weather(desc):
    desc = desc.lower()
    if "rain" in desc or "storm" in desc:
        return "#4FA3FF"  # bright blue
    elif "snow" in desc:
        return "#B3E5FC"  # cold light blue
    elif "cloud" in desc or "overcast" in desc:
        return "#C0C0C0"  # neutral gray
    elif "sun" in desc or "clear" in desc:
        return "#FFD75E"  # warm yellow
    else:
        return "#FFFFFF"


#Fetch current weather from OpenWeatherMap
def get_weather():
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={API_KEY}&units={UNITS}&lang={LANG}"
        data = requests.get(url, timeout=5).json()
        temp = int(data["main"]["temp"])
        icon_code = data["weather"][0]["icon"]
        desc = data["weather"][0]["description"].capitalize()
        return temp, icon_code, desc
    except Exception:
        return None, None, "Weather error"


#Update time and date every second
def update_display():
    now = datetime.datetime.now()
    date_label.config(text=now.strftime("%d/%m/%Y"))
    time_label.config(text=now.strftime("%H:%M"))
    root.after(1000, update_display)


#Weather update loop
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
            root.after(0, update_weather_ui, None, "Weather error", "error")
        time.sleep(UPDATE_INTERVAL)


#Update weather and color dynamically
def update_weather_ui(img, text, desc):
    global is_color_locked, last_weather_img, last_weather_text, last_weather_desc

    # Always save the last known weather state
    last_weather_img = img
    last_weather_text = text
    last_weather_desc = desc

    if is_color_locked:
        return  # avoid color conflicts during transitions

    # Dynamic color based on weather
    color = get_color_for_weather(desc)
    if not is_daytime() and color == "#FFFFFF":
        color = "#999999"

    weather_text.config(fg=color)
    time_label.config(fg=color)
    date_label.config(fg=color)

    # Update image and text
    if img:
        weather_icon.config(image=img)
        weather_icon.image = img
    weather_text.config(text=text)


#Screen management
def manage_screen():
    root.after(5 * 60 * 1000, manage_screen)


#Smooth transition for color changes
def smooth_color_transition(start_color, end_color, duration=5):
    """Smoothly transition between two hex colors."""
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


#Automatic day/night color handling
def adjust_night_colors():
    """Automatically handle night mode with temporary weather lock."""
    global is_color_locked
    now = datetime.datetime.now().time()
    sleep_start = datetime.time(22, 30)
    sleep_end = datetime.time(7, 30)

    # Temporarily lock weather updates
    is_color_locked = True

    if sleep_start <= now or now < sleep_end:
        # Night mode
        smooth_color_transition("#FFFFFF", "#995500")  # warm amber
        root.config(bg="#000000")
    else:
        # Day mode
        smooth_color_transition("#995500", "#FFFFFF")
        root.config(bg="black")

    # Unlock after 2s
    def unlock():
        global is_color_locked
        is_color_locked = False
    root.after(2000, unlock)

    # Restore last weather after unlock
    def restore_weather():
        global last_weather_img, last_weather_text, last_weather_desc
        if last_weather_text:
            update_weather_ui(last_weather_img, last_weather_text, last_weather_desc)
    root.after(2100, restore_weather)

    # Check again every 5 minutes
    root.after(5 * 60 * 1000, adjust_night_colors)


#GUI SETUP
root = tk.Tk()
root.title("Clock & Weather")
root.configure(bg="black")
root.attributes("-fullscreen", True)

#Screen size
screen_w = root.winfo_screenwidth()
screen_h = root.winfo_screenheight()

#Fonts (scaled up ~56%)
date_font = font.Font(family="Helvetica", size=int(screen_h * 0.05 * 1.56), weight="bold")
time_font = font.Font(family="Helvetica", size=int(screen_h * 0.25 * 1.56), weight="bold")
weather_font = font.Font(family="Helvetica", size=int(screen_h * 0.035 * 1.56))

#Layout container
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

#Start background weather thread
threading.Thread(target=update_weather_loop, daemon=True).start()

#Start loops
update_display()
manage_screen()
adjust_night_colors()

root.mainloop()

