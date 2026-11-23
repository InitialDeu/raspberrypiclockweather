#!/bin/bash
# piclock - management console + Wi-Fi manager (setup hotspot) installer.
# Run on the Raspberry Pi as root, from the repository checkout:
#   sudo bash /opt/raspberrypiclockweather/admin/install.sh
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo bash $0"; exit 1; }

SRC="$(cd "$(dirname "$0")" && pwd)"            # .../raspberrypiclockweather/admin
REPO="$(dirname "$SRC")"
LIB=/usr/local/lib/piclock-admin                 # root-owned copy that actually runs as root
ENV=/etc/clockweather.env

if systemctl is-active --quiet NetworkManager; then BACKEND=nm; else BACKEND=wpa; fi
echo "==> Wi-Fi backend: $BACKEND"

if [ "$BACKEND" = wpa ]; then
    echo "==> Installing hostapd, dnsmasq-base, iw (hotspot)"
    apt-get update -q
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q hostapd dnsmasq-base iw rfkill
    # the hotspot is started on demand by piclock_wifi.py, never by the stock services
    systemctl disable --now hostapd 2>/dev/null || true
    systemctl mask hostapd 2>/dev/null || true
fi

echo "==> Installing console in $LIB"
install -d -m 755 "$LIB"
install -m 755 "$SRC/clockweather_admin.py" "$SRC/piclock_wifi.py" "$LIB/"

install -d -m 755 /etc/piclock
python3 - <<EOF
import sys; sys.path.insert(0, "$LIB")
import piclock_wifi as w
c = w.ap_settings()
print("==> Setup hotspot: %s   password: %s   (stored in /etc/piclock/ap.json)" % (c["ssid"], c["password"]))
EOF

touch "$ENV"; chmod 600 "$ENV"
if ! grep -q '^CW_ADMIN_PASSWORD=' "$ENV"; then
    PW=$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(12)))")
    echo "CW_ADMIN_PASSWORD=$PW" >> "$ENV"
    echo "==> Console password (generated, change it from the console): $PW"
else
    echo "==> Console password: already set in $ENV (CW_ADMIN_PASSWORD)"
fi

echo "==> systemd units"
cat > /etc/systemd/system/clockweather-admin.service <<EOF
[Unit]
Description=piclock management console (LAN)
After=network.target

[Service]
ExecStart=/usr/bin/python3 $LIB/clockweather_admin.py
Environment=CW_REPO=$REPO
EnvironmentFile=-$ENV
Restart=always
RestartSec=5
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/piclock-wifi.service <<EOF
[Unit]
Description=piclock Wi-Fi: join a known network or start the setup hotspot
After=network.target dhcpcd.service NetworkManager.service
Wants=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $LIB/piclock_wifi.py boot

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/piclock-wifi-check.service <<EOF
[Unit]
Description=piclock Wi-Fi periodic check

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $LIB/piclock_wifi.py check
EOF

cat > /etc/systemd/system/piclock-wifi-check.timer <<EOF
[Unit]
Description=piclock Wi-Fi periodic check (every 10 min)

[Timer]
OnBootSec=8min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now clockweather-admin.service
systemctl enable piclock-wifi.service piclock-wifi-check.timer
systemctl start piclock-wifi-check.timer
systemctl start --no-block piclock-wifi.service     # already connected -> just writes the status
systemctl restart clockweather 2>/dev/null || true   # so the clock shows the console address

IP=$(hostname -I | awk '{print $1}')
PORT=$(grep -E '^CW_ADMIN_PORT=' "$ENV" | cut -d= -f2); PORT=${PORT:-80}
echo
echo "Done. Console: http://$IP$([ "$PORT" = 80 ] || echo ":$PORT")   (user: any, password: CW_ADMIN_PASSWORD)"
echo "Safe hotspot test (detached from SSH, comes back by itself after 2 minutes):"
echo "  sudo systemd-run --unit=piclock-aptest /usr/bin/python3 $LIB/piclock_wifi.py ap-test 120"
