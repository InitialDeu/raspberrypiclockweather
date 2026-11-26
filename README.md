# Raspberry Pi Clock & Weather

A fullscreen weather and clock display for Raspberry Pi using Tkinter and OpenWeatherMap.

![Clock & Weather Preview](ClockExample.png)

---

## Features
- Fullscreen digital clock with date
- Current weather with an icon (day and night versions), drawn by the app itself
- A rotating line with the forecast for the next 24 hours (min/max, rain or snow), feels-like
  temperature, humidity, wind, sunrise and sunset
- Zodiac sign, moon phase and saint of the day, downloaded 7 days in advance
- Warning on screen when the system time is not synchronised (Raspberry Pi boards usually have
  no battery-backed clock)
- Night mode with a smooth colour fade, at fixed hours (default 22:30–07:30) or from sunset to sunrise
- Layout that adapts by itself to the screen: any resolution, landscape or portrait, also when
  it changes while the clock is running
- Optional LAN management console and Wi-Fi setup hotspot

---

## Requirements
Install required dependencies:
```bash
sudo apt update
sudo apt install -y python3 python3-requests python3-tk git
```

The requirements differ between Raspberry Pi OS Bookworm (HDMI, first test) and
Bullseye (Pi Zero 2 W with a 3.5" SPI display): see [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md).

---

## Installation

Clone the repository
```bash
git clone https://github.com/InitialDeu/raspberrypiclockweather.git
cd raspberrypiclockweather
```
Configure your city and API key
Edit the script to set your location and OpenWeatherMap key:

```bash
nano display_clock_weather.py
```

Recommended: put them in an environment file read by the systemd service,
so the key never ends up in the script or in git:

```bash
echo 'OWM_API_KEY=YourOpenWeatherAPIKey' | sudo tee /etc/clockweather.env
echo 'CW_CITY=YourCity' | sudo tee -a /etc/clockweather.env
echo 'CW_LANG=en' | sudo tee -a /etc/clockweather.env     # optional: "it" for the Italian version
sudo chmod 600 /etc/clockweather.env
```

All settings (in `/etc/clockweather.env`, also editable from the management console):

| Variable | Default | Meaning |
|---|---|---|
| `OWM_API_KEY` | | OpenWeatherMap key (the free plan is enough) |
| `CW_CITY` | `Turin` | City, as known to OpenWeatherMap |
| `CW_LANG` | `en` | `it` = Italian screen (texts, `dd/mm/yyyy` date, weather description), like the preview above |
| `CW_NIGHT` | `on` | `on` = fixed hours, `sun` = from sunset to sunrise, `off` = disabled |
| `CW_NIGHT_START` | `22:30` | Start of the night with fixed hours (also used by `sun` until the first weather data arrives) |
| `CW_NIGHT_END` | `07:30` | End of the night |
| `CW_NIGHT_FG` | `#5C3300` | Night text colour (lower = darker) |
| `CW_SAINT_CACHE` | `~/.cache/clockweather/saints.json` | Where the downloaded saints are saved |

Alternatively, change the defaults directly in the script:

```python
CITY = os.environ.get("CW_CITY", "YourCity")
API_KEY = os.environ.get("OWM_API_KEY", "YourOpenWeatherAPIKey")
```
You can get a free API key here: https://openweathermap.org/api

Run manually (test)
```bash
python3 display_clock_weather.py
```

If everything is correct, you’ll see the fullscreen clock and weather on your display.
The layout adapts automatically to the screen resolution and orientation
(tested at 480x320, 320x480, 800x480, 1920x1080, 2560x1440 and 1440x2560). In portrait the
weather icon gets its own row and the long lines are split in two.

**Optional management console:** a LAN-only web page to change city, language, night mode,
Wi-Fi networks, restart/update the clock — plus a setup hotspot (`piclock-setup`) when the Pi
can't find a known Wi-Fi network, so it can be moved to a new place and configured from a phone.
See [docs/ADMIN.md](docs/ADMIN.md).

Using a 3.5" SPI touchscreen (ILI9486, "3.5inch RPi Display")? See [docs/OTTIMIZZAZIONI.md](docs/OTTIMIZZAZIONI.md)
for the display setup.

Autostart on boot (systemd)
To automatically start the display after boot, use the included service file.

Create or edit:

```bash
sudo nano /etc/systemd/system/clockweather.service
```

Paste this:
```ini
[Unit]
Description=Raspberry Pi Clock & Weather
After=graphical.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/raspberrypiclockweather
ExecStart=/usr/bin/python3 /opt/raspberrypiclockweather/display_clock_weather.py
Restart=always
RestartSec=10
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/pi/.Xauthority
EnvironmentFile=-/etc/clockweather.env

[Install]
WantedBy=graphical.target
```
Create the log file (the service runs as `pi`, which cannot create files in `/var/log`;
if you skip this, the script falls back to `~/clockweather.log`):

```bash
sudo touch /var/log/clockweather.log
sudo chown pi:pi /var/log/clockweather.log
```

Then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now clockweather
```

Check status:

```bash
sudo systemctl status clockweather
```

To stop or restart:

```bash
sudo systemctl stop clockweather
sudo systemctl restart clockweather
```
---

## Quick Install (One Command)

You can automate everything (install, clone, setup, and service) with the script below.

Copy and paste it directly into your terminal on your Raspberry Pi.

```bash
#!/bin/bash
# Raspberry Pi Clock & Weather - Quick Installer
# Author: InitialDeu
# License: GPL

set -e

INSTALL_DIR="/opt/raspberrypiclockweather"
SERVICE_NAME="clockweather.service"

echo "Installing Raspberry Pi Clock & Weather..."
sudo apt update -y
sudo apt install -y python3 python3-requests python3-tk git

if [ ! -d "$INSTALL_DIR" ]; then
    sudo git clone https://github.com/InitialDeu/raspberrypiclockweather.git "$INSTALL_DIR"
else
    echo "Repository already exists, updating..."
    cd "$INSTALL_DIR" && sudo git pull
fi

echo "Installing systemd service..."
sudo bash -c "cat > /etc/systemd/system/$SERVICE_NAME" <<'EOF'
[Unit]
Description=Raspberry Pi Clock & Weather
After=graphical.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/raspberrypiclockweather
ExecStart=/usr/bin/python3 /opt/raspberrypiclockweather/display_clock_weather.py
Restart=always
RestartSec=10
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/pi/.Xauthority
EnvironmentFile=-/etc/clockweather.env

[Install]
WantedBy=graphical.target
EOF

sudo touch /var/log/clockweather.log
sudo chown pi:pi /var/log/clockweather.log

sudo systemctl daemon-reload
sudo systemctl enable --now clockweather

echo ""
echo "Installation complete!"
echo "Use 'sudo systemctl status clockweather' to check status."
echo "Use 'journalctl -u clockweather -f' to view logs."
```

Make it executable (if saved separately):

```bash
chmod +x quick-install.sh
```
Run it:

```bash
./quick-install.sh
```
---

## Logging

If you want to store the logs and enable the logrotate, you can create a logrotate file:
```bash
/var/log/clockweather.log {
    daily
    missingok
    notifempty
    copytruncate
    dateext
    maxage 1
    rotate 0
}
```

And Force it using:

```bash
sudo logrotate -f /etc/logrotate.d/clockweather
```

## Updating an existing installation

From the management console press **Aggiorna da GitHub**, or over SSH:

```bash
cd /opt/raspberrypiclockweather && sudo git pull
# only if the management console is installed (it runs from its own root-owned copy):
sudo install -m 755 admin/clockweather_admin.py admin/piclock_wifi.py /usr/local/lib/piclock-admin/
sudo systemctl restart clockweather-admin
sudo systemctl restart clockweather
```

---

## Updates

V1.0.1 Added Zodiac Sign, Moon Phases and Saint of the Day

V1.1.0 Auto-fit layout for any resolution/orientation, non-blocking network updates,
weather errors no longer crash the app, clock synced to the minute, Pillow no longer required,
optional Italian version (`CW_LANG=it`)

V1.2.0 Night mode back (darker, configurable), watchdog that restarts the app if updates get stuck,
stale weather/saint no longer shown as current

V1.3.0 Optional LAN management console and Wi-Fi setup hotspot (`admin/`, see [docs/ADMIN.md](docs/ADMIN.md));
the clock shows the hotspot details and the console address on screen
(details in [docs/OTTIMIZZAZIONI.md](docs/OTTIMIZZAZIONI.md))

V1.4.0
- Saint of the day requested by date, checked and downloaded 7 days in advance, so it stays right
  across network outages and reboots
- Weather icons, 24 h forecast with rain/snow warning, feels-like temperature, humidity, wind,
  sunrise and sunset
- Night mode from sunset to sunrise (`CW_NIGHT=sun`)
- Warning when the system time is not synchronised
- New layout that adapts to any screen, also when resolution or rotation change while running
- Weather errors logged on one line, without the API key

## Notes
Tested on Raspberry Pi OS (Bookworm) with X11 desktop session using a Raspberry PI 4

Works with HDMI displays and with 3.5" SPI displays (fbtft framebuffer, tested on Pi Zero 2 W / Bullseye)

Requires an internet connection for weather, forecast and saints (saints already downloaded
remain available offline)

Screen automatically dims at night (see `CW_NIGHT`)

---

## Author
Developed by InitialDeu
Feel free to fork, improve, and contribute.

## License
This project is released under the GPL License.
