#!/usr/bin/env bash
#
# Remove Kraken Unleashed.
#
#   sudo ./uninstall.sh            remove the service and files, keep your config
#   sudo ./uninstall.sh --purge    also remove /etc/kraken-lcd.conf
#
# The cooler keeps whatever was last on its screen until something else writes
# to it; the firmware's own screen returns after a power cycle.

set -euo pipefail
PREFIX=/opt/kraken-lcd
CONFIG=/etc/kraken-lcd.conf
UNIT_DIR=/etc/systemd/system

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1
[ "$(id -u)" = 0 ] || { echo "run me with sudo" >&2; exit 1; }

echo "Stopping and disabling the service"
systemctl disable --now kraken-lcd.service kraken-lcd-resume.service 2>/dev/null || true
rm -f "$UNIT_DIR/kraken-lcd.service" "$UNIT_DIR/kraken-lcd-resume.service"
systemctl daemon-reload

echo "Removing $PREFIX"
rm -rf "$PREFIX"
rm -rf /run/kraken-lcd

if [ "$PURGE" = 1 ]; then
    rm -f "$CONFIG"
    echo "Removed $CONFIG"
else
    echo "Kept $CONFIG (use --purge to remove it)"
fi

echo
echo "Done. If you disabled the cooler in CoolerControl or OpenRGB to run this,"
echo "re-enable it there to hand the device back."
