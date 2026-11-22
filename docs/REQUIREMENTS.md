# Requirements by OS / hardware

The project has been run on two quite different setups. The Python code is the same;
what changes is the OS, the packages and how the screen is driven.

| | **First test setup** | **SPI display setup** |
|---|---|---|
| Board | Raspberry Pi 4 | Raspberry Pi Zero 2 W (Rev 1.0, 512 MB) |
| OS | Raspberry Pi OS **Bookworm** (Debian 12) | Raspberry Pi OS **Bullseye** 32-bit (Debian 11, kernel 5.15 `armv7l`) |
| Python / Tk | Python 3.11, Tk 8.6 | Python 3.9, Tk 8.6 |
| Display | HDMI (`HDMI-1`) | 3.5" SPI 480x320, ILI9486 + XPT2046 ("3.5inch RPi Display") |
| Graphics stack | KMS (`vc4-kms-v3d`), X11 session | **Legacy** framebuffer (no KMS), X11 on `/dev/fb1` via `fbturbo` |
| Boot config file | `/boot/firmware/config.txt` | `/boot/config.txt` |
| Desktop session | Default is Wayland → must be switched to **X11** | X11 (LXDE/Openbox, Bullseye default) |

---

## Packages

### Common (both)
```bash
sudo apt update
sudo apt install -y python3 python3-requests python3-tk git
```

From v1.1.0 Pillow is **not** needed anymore. With the old v1.0.1 script
(`from PIL import Image, ImageTk`) you also needed:

| | Bookworm | Bullseye |
|---|---|---|
| v1.0.1 only | `python3-pil python3-pil.imagetk` | `python3-pil python3-pil.imagetk` |

> On Debian/Raspberry Pi OS `ImageTk` lives in the separate package `python3-pil.imagetk`:
> installing only `python3-pil` gives `ImportError: cannot import name 'ImageTk'`.

### Bookworm + HDMI (first test)
Nothing beyond the common packages. The desktop must run **X11**: on Bookworm the default
session is Wayland (labwc), and the service relies on `DISPLAY=:0` + `/home/pi/.Xauthority`.

```bash
sudo raspi-config nonint do_wayland W1    # W1 = X11 (Openbox). Same as: raspi-config > Advanced Options > Wayland > X11
sudo reboot
```

### Bullseye + 3.5" SPI display
Extra pieces on top of the common packages:

| What | Package / file | Notes |
|---|---|---|
| X driver for the SPI framebuffer | `xserver-xorg-video-fbturbo` | `/usr/share/X11/xorg.conf.d/99-fbturbo.conf` → `Option "fbdev" "/dev/fb1"` |
| Device-tree overlay | `/boot/overlays/tft35a.dtbo` | Only the file from `goodtft/LCD-show` (`usr/tft35a-overlay.dtb`), **not** their install script |
| `config.txt` | `dtparam=spi=on` + `dtoverlay=tft35a:rotate=270` | `rotate=90/270` landscape, `0/180` portrait. No `dtoverlay=vc4-kms-v3d` |
| Device-tree tools (optional, to inspect overlays) | `device-tree-compiler` | `dtc -I dtb -O dts file.dtbo` |
| No screen blanking | — | `sudo raspi-config nonint do_blanking 1` |

Full step-by-step and troubleshooting: [OTTIMIZZAZIONI.md](OTTIMIZZAZIONI.md) (section 2).

> SPI displays on **Bookworm** (KMS) need a different approach (the `fbtft` drivers are
> deprecated there). Not tested with this project.

---

## Service user (both)

The systemd unit runs as `pi` and needs `pi` to own the X session:

```bash
# only if the user does not exist (images from 2022 onwards no longer create it)
sudo adduser --disabled-password --gecos "" pi
sudo usermod -aG video,input,render,audio,gpio,spi,i2c,plugdev,netdev,users pi
sudo sed -i 's/^\s*autologin-user=.*/autologin-user=pi/' /etc/lightdm/lightdm.conf

sudo touch /var/log/clockweather.log && sudo chown pi:pi /var/log/clockweather.log
```

API key and city go in `/etc/clockweather.env` (read by the service):
```
OWM_API_KEY=your_key
CW_CITY=Turin
CW_LANG=en        # optional, "it" = Italian version
```
