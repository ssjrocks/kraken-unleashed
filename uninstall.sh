#!/usr/bin/env bash
#
# Remove Kraken Unleashed.
#
#   sudo ./uninstall.sh            remove the service and files, keep your config
#   sudo ./uninstall.sh --purge    also remove /etc/kraken-unleashed.conf
#
# The cooler keeps whatever was last on its screen until something else writes
# to it; the firmware's own screen returns after a power cycle.

set -euo pipefail

PREFIX=/opt/kraken-unleashed
CONFIG=/etc/kraken-unleashed.conf
UNIT_DIR=/etc/systemd/system
BINDIR=/usr/bin
APPDIR=/usr/share/applications
ICONDIR=/usr/share/icons/hicolor/scalable/apps
APP_ID=io.github.ssjrocks.KrakenUnleashed

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1
[ "$(id -u)" = 0 ] || { echo "run me with sudo" >&2; exit 1; }

echo "Stopping and disabling the service"
systemctl disable --now kraken-unleashed.service kraken-unleashed-resume.service \
    2>/dev/null || true
rm -f "$UNIT_DIR/kraken-unleashed.service" "$UNIT_DIR/kraken-unleashed-resume.service"

# Also clear the 1.x units, in case this is cleaning up a machine that never
# finished the upgrade.
systemctl disable --now kraken-lcd.service kraken-lcd-resume.service 2>/dev/null || true
rm -f "$UNIT_DIR/kraken-lcd.service" "$UNIT_DIR/kraken-lcd-resume.service"
systemctl daemon-reload

echo "Removing program files"
rm -rf "$PREFIX" /opt/kraken-lcd
rm -rf /run/kraken-unleashed /run/kraken-lcd
for exe in kraken-unleashed kraken-unleashed-daemon kraken-unleashed-ctl; do
    rm -f "$BINDIR/$exe"
done

echo "Removing the desktop entry"
rm -f "$APPDIR/$APP_ID.desktop" "$ICONDIR/$APP_ID.svg"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPDIR" || true
command -v gtk-update-icon-cache >/dev/null && \
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor || true

if [ "$PURGE" = 1 ]; then
    rm -f "$CONFIG" /etc/kraken-lcd.conf
    echo "Removed $CONFIG"
else
    echo "Kept $CONFIG (use --purge to remove it)"
fi

cat <<'EOF'

Done.

If you disabled the cooler in CoolerControl or OpenRGB to make room for this,
re-enable it there to hand the device back. If you added an E1.31 device to
OpenRGB for the lighting relay, it now has nothing to talk to -- remove it from
OpenRGB's settings.

rgb-sync, if you installed it from extras/, is separate and still running.
EOF
