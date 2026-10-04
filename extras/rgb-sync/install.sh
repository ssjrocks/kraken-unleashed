#!/usr/bin/env bash
#
# Install rgb-sync: one shared breathing curve across every OpenRGB device.
# Optional companion to Kraken Unleashed -- see ../../docs/RGB.md
#
#   sudo ./install.sh

set -euo pipefail
PREFIX=/opt/rgb-sync
CONFIG=/etc/rgb-sync.json
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ "$(id -u)" = 0 ] || { echo "run me with sudo" >&2; exit 1; }

command -v openrgb >/dev/null || {
    echo "OpenRGB is not installed. Install it and start 'openrgb --server' first." >&2
    exit 1
}

echo "Installing to $PREFIX"
install -d -m 755 "$PREFIX"
install -m 755 "$SRC/rgb_sync.py" "$PREFIX/rgb_sync.py"

# openrgb-python is pinned: rgb_sync.py uses Device._set_device_color, a private
# method, because the public set_color() branches on a stale cached mode and
# knocks devices back out of Direct. See ../../docs/RGB.md.
if [ ! -x "$PREFIX/venv/bin/python" ]; then
    echo "Creating venv and installing openrgb-python==0.3.7"
    python3 -m venv "$PREFIX/venv"
    "$PREFIX/venv/bin/pip" install --quiet --upgrade pip
    "$PREFIX/venv/bin/pip" install --quiet 'openrgb-python==0.3.7'
fi

if [ -e "$CONFIG" ]; then
    echo "Keeping your existing $CONFIG"
else
    install -m 644 "$SRC/rgb-sync.json" "$CONFIG"
    echo "Wrote default $CONFIG"
fi

install -m 644 "$SRC/rgb-sync.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now rgb-sync.service
sleep 2
systemctl is-active --quiet rgb-sync && echo "rgb-sync is running" || {
    echo "rgb-sync did not stay up:"; journalctl -u rgb-sync -n 20 --no-pager; exit 1; }

cat <<MSG

Done. Both daemons now read $CONFIG, so changing the colour or period
there changes everything at once:

    sudo nano $CONFIG
    sudo systemctl restart rgb-sync kraken-lcd
MSG
