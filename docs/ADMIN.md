# Management console & Wi-Fi setup hotspot (optional)

A small web console, reachable only from the local network, plus a Wi-Fi manager
that turns the Pi into a setup hotspot when it cannot find a known network.
Everything lives in `admin/` and uses only the Python standard library.

## What it does

| Page | |
|---|---|
| **Stato** | clock service status, version (git commit), Wi-Fi network and IP, uptime, CPU temperature, last 40 log lines. Buttons: restart clock, update from GitHub (`git pull`), reboot the Pi |
| **Impostazioni** | city, language, OpenWeatherMap key, night mode (fixed hours / sunset to sunrise / off, start, end, colour). Saved to `/etc/clockweather.env`, the clock restarts by itself. Change the console password |
| **Wi-Fi** | saved networks (remove), visible networks, add a network (SSID, password, priority) |

### Setup hotspot (moving the Pi to a network you don't know yet)

1. At boot the Pi waits up to 90 s for a known Wi-Fi network.
2. If none is reachable it starts the hotspot **`piclock-setup`** and the clock screen shows:
   `Wi-Fi piclock-setup · pw xxxxxxxxxx` / `poi apri http://192.168.4.1`
3. Join it from a phone: the "sign in to network" page opens by itself (captive portal),
   otherwise open `http://192.168.4.1`. On the hotspot only the Wi-Fi page is available,
   without the console password (the hotspot password, shown on the screen, is the gate).
4. Pick the network, type its password, **Salva rete**: the hotspot turns off and the Pi joins
   the network. If it fails within 60 s (wrong password, out of range) the hotspot comes back,
   with the error shown on the page.
5. Once connected, the clock shows `Console: http://<new IP>` for 2 minutes.

Every 10 minutes a timer also checks the connection: if the Wi-Fi has been lost for two
checks in a row the hotspot starts; while the hotspot is up and nobody is connected to it,
the Pi retries the saved networks (e.g. after a router reboot).

The hotspot name/password are in `/etc/piclock/ap.json` (random password, created at install).

## Install

On the Pi, from the repository checkout, as root:

```bash
cd /opt/raspberrypiclockweather && git pull
sudo bash admin/install.sh
```

The installer:
- on Bullseye (wpa_supplicant/dhcpcd) installs `hostapd`, `dnsmasq-base`, `iw` and keeps the
  stock `hostapd` service masked (the hotspot is started only on demand);
  on Bookworm (NetworkManager) nothing extra is needed: it uses `nmcli device wifi hotspot`
  (address `10.42.0.1` instead of `192.168.4.1`, no captive-portal DNS);
- copies the console to `/usr/local/lib/piclock-admin` (root-owned: the console runs as root,
  it must not execute files the `pi` user can modify);
- generates `CW_ADMIN_PASSWORD` in `/etc/clockweather.env` if missing and prints it;
- installs `clockweather-admin.service`, `piclock-wifi.service` (boot) and
  `piclock-wifi-check.timer` (every 10 min).

Console: `http://<IP of the Pi>` — any username, password = `CW_ADMIN_PASSWORD`.
Port: `CW_ADMIN_PORT` in `/etc/clockweather.env` (default 80, needed for the captive portal).

### Test the hotspot safely (over SSH)

Switching to the hotspot drops the SSH session, so run the test **detached** — it goes back
to the normal Wi-Fi by itself after 2 minutes:

```bash
sudo systemd-run --unit=piclock-aptest /usr/bin/python3 /usr/local/lib/piclock-admin/piclock_wifi.py ap-test 120
```

During those 2 minutes the hotspot is visible from the phone and the clock shows its details.

### Command line

```bash
sudo python3 /usr/local/lib/piclock-admin/piclock_wifi.py status   # current state (JSON)
sudo python3 /usr/local/lib/piclock-admin/piclock_wifi.py list     # saved networks
sudo python3 /usr/local/lib/piclock-admin/piclock_wifi.py add "SSID" "password" 20
journalctl -u clockweather-admin -u piclock-wifi -u piclock-wifi-check -n 50
```

### Emergency (Bullseye only)

If everything else fails: put the SD card in a PC and create `wpa_supplicant.conf` in the
`boot` partition — Bullseye moves it to `/etc/wpa_supplicant/` at the next boot.

## Security notes

- Meant for a trusted home LAN: plain HTTP, Basic auth. Don't forward the port on the router.
- Every form has a CSRF token; inputs are validated (city, `HH:MM`, `#RRGGBB`, SSID 1–32 bytes,
  Wi-Fi password 8–63 chars); only a fixed set of actions, no arbitrary commands.
- Wi-Fi passwords are stored as pre-computed PSK hashes in `wpa_supplicant.conf`, not in clear text.
