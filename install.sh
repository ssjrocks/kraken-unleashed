#!/usr/bin/env bash
#
# Kraken Unleashed installer.
#
#   sudo ./install.sh              install or upgrade, then start
#   sudo ./install.sh --no-start   install but don't enable/start the service
#   sudo ./install.sh --dry-run    print what would happen, change nothing
#
# Safe to re-run: your /etc/kraken-unleashed.conf is never overwritten.

set -euo pipefail

PREFIX=/opt/kraken-unleashed
CONFIG=/etc/kraken-unleashed.conf
LEGACY_CONFIG=/etc/kraken-lcd.conf
BINDIR=/usr/bin
UNIT_DIR=/etc/systemd/system
APPDIR=/usr/share/applications
ICONDIR=/usr/share/icons/hicolor/scalable/apps
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DRY_RUN=0
START=1
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --no-start) START=0 ;;
        -h|--help) sed -n '2,10p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
warn()  { printf '\033[33m!!\033[0m %s\n' "$*"; }
err()   { printf '\033[31mxx\033[0m %s\n' "$*" >&2; }
ok()    { printf '\033[32mok\033[0m %s\n' "$*"; }
run()   { if [ "$DRY_RUN" = 1 ]; then echo "   would: $*"; else "$@"; fi; }

[ "$(id -u)" = 0 ] || { err "run me with sudo"; exit 1; }

# The desktop user, so the control socket can be reachable without a re-login.
DESKTOP_USER="${SUDO_USER:-}"
DESKTOP_GROUP=""
if [ -n "$DESKTOP_USER" ]; then
    DESKTOP_GROUP="$(id -gn "$DESKTOP_USER" 2>/dev/null || true)"
fi

bold "== Kraken Unleashed =="
[ "$DRY_RUN" = 1 ] && warn "dry run: nothing will be changed"

# --------------------------------------------------------------------------- #
bold "[1/7] Looking for the cooler"
if lsusb -d 1e71:3011 >/dev/null 2>&1; then
    err "The Kraken is in BOOTLOADER mode (1e71:3011)."
    err "Shut down, switch the PSU off at the wall for ~30 seconds, and boot again."
    err "A reboot alone will NOT clear this. See docs/TROUBLESHOOTING.md."
    exit 1
fi
if lsusb -d 1e71:3012 >/dev/null 2>&1; then
    ok "found 1e71:3012 (Kraken Elite 2024 / V2)"
else
    warn "No 1e71:3012 on USB. Supported: NZXT Kraken 2024 Elite / Elite V2."
    warn "Check the cooler's internal USB 2.0 header cable is connected."
    warn "Installing anyway -- the service will exit cleanly until it appears."
fi

# --------------------------------------------------------------------------- #
bold "[2/7] Checking dependencies"
missing=()
for mod in numpy PIL usb; do
    python3 -c "import $mod" 2>/dev/null || missing+=("$mod")
done
# The GUI is optional: the daemon and the CLI work without it.
gui_ok=1
python3 -c "
import gi
gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Gtk, Adw
" 2>/dev/null || gui_ok=0

if [ ${#missing[@]} -gt 0 ] || [ "$gui_ok" = 0 ]; then
    if   command -v apt-get >/dev/null; then
        pkgs=(python3-numpy python3-pil python3-usb python3-gi gir1.2-gtk-4.0 gir1.2-adw-1)
        mgr=(apt-get install -y)
    elif command -v dnf >/dev/null; then
        pkgs=(python3-numpy python3-pillow python3-pyusb python3-gobject gtk4 libadwaita)
        mgr=(dnf install -y)
    elif command -v pacman >/dev/null; then
        pkgs=(python-numpy python-pillow python-pyusb python-gobject gtk4 libadwaita)
        mgr=(pacman -S --needed --noconfirm)
    elif command -v zypper >/dev/null; then
        pkgs=(python3-numpy python3-Pillow python3-pyusb python3-gobject gtk4 libadwaita)
        mgr=(zypper install -y)
    else
        err "Unknown package manager. Install numpy, Pillow, pyusb and PyGObject"
        err "with GTK4 + libadwaita for python3, then re-run."
        exit 1
    fi
    echo "   installing: ${pkgs[*]}"
    run "${mgr[@]}" "${pkgs[@]}"
else
    ok "numpy, Pillow, pyusb and GTK4/libadwaita are present"
fi

# Probe the same paths the renderer does rather than parsing fc-list -- under
# `set -o pipefail` a `fc-list | grep -q` pipeline reports failure on success,
# because grep exits early and fc-list dies of SIGPIPE.
font_found=0
for f in /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf \
         /usr/share/fonts/TTF/DejaVuSans-Bold.ttf \
         /usr/share/fonts/liberation/LiberationSans-Bold.ttf \
         /usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf \
         /usr/share/fonts/noto/NotoSans-Bold.ttf \
         /usr/share/fonts/truetype/noto/NotoSans-Bold.ttf; do
    [ -r "$f" ] && { font_found=1; break; }
done
if [ "$font_found" = 1 ]; then
    ok "found a bold font for the readout"
else
    warn "No DejaVu/Liberation/Noto Bold font found -- the readout will use a"
    warn "bitmap fallback and look rough. Install fonts-dejavu-core."
fi

# --------------------------------------------------------------------------- #
bold "[3/7] Checking for other software holding the cooler"
conflict=0
if systemctl is-active --quiet coolercontrold 2>/dev/null; then
    if python3 -c "import liquidctl" 2>/dev/null; then
        warn "CoolerControl is running with liquidctl available, so it will try to"
        warn "drive this cooler and fight over the USB interface. Disable the"
        warn "Kraken device in CoolerControl -- see docs/TROUBLESHOOTING.md."
        conflict=1
    else
        ok "CoolerControl is running but has no liquidctl, so it cannot reach the cooler"
    fi
fi
if systemctl is-active --quiet openrgb 2>/dev/null; then
    warn "OpenRGB is running. Make sure its NZXT Kraken detector is DISABLED --"
    warn "this service drives those LEDs. To let OpenRGB control them anyway,"
    warn "turn on the E1.31 relay on the OpenRGB page in the app. See docs/RGB.md."
    conflict=1
fi
if pgrep -x liquidctl >/dev/null 2>&1; then
    warn "liquidctl is running and will contend for the device."
    conflict=1
fi
[ "$conflict" = 0 ] && ok "nothing else appears to hold the cooler"

# --------------------------------------------------------------------------- #
bold "[4/7] Retiring any previous install"
for old in kraken-lcd kraken-lcd-resume; do
    if systemctl list-unit-files "$old.service" --no-legend 2>/dev/null | grep -q .; then
        warn "disabling the older $old.service (replaced by kraken-unleashed)"
        run systemctl disable --now "$old.service"
        run rm -f "$UNIT_DIR/$old.service"
    fi
done
[ -d /opt/kraken-lcd ] && run rm -rf /opt/kraken-lcd
ok "previous install cleared"

# --------------------------------------------------------------------------- #
bold "[5/7] Installing to $PREFIX"
run install -d -m 755 "$PREFIX" "$PREFIX/assets"
run cp -r "$SRC/src/kraken_unleashed" "$PREFIX/"
run cp -r "$SRC/src/ok" "$PREFIX/"
run find "$PREFIX" -name '__pycache__' -type d -prune -exec rm -rf {} +
run install -m 644 "$SRC/assets/demo.gif" "$PREFIX/assets/demo.gif"
for exe in kraken-unleashed kraken-unleashed-daemon kraken-unleashed-ctl; do
    run install -m 755 "$SRC/src/bin/$exe" "$BINDIR/$exe"
done
run install -d -m 755 "$APPDIR" "$ICONDIR"
run install -m 644 "$SRC/packaging/io.github.ssjrocks.KrakenUnleashed.desktop" "$APPDIR/"
run install -m 644 "$SRC/packaging/icons/io.github.ssjrocks.KrakenUnleashed.svg" "$ICONDIR/"
command -v update-desktop-database >/dev/null && run update-desktop-database -q "$APPDIR" || true
command -v gtk-update-icon-cache >/dev/null && \
    run gtk-update-icon-cache -qtf /usr/share/icons/hicolor || true
ok "installed the daemon, the app and the command-line client"

# --------------------------------------------------------------------------- #
bold "[6/7] Configuration"
if [ -e "$CONFIG" ]; then
    ok "keeping your existing $CONFIG"
elif [ -e "$LEGACY_CONFIG" ]; then
    # Carry 1.x settings forward rather than silently resetting someone's screen.
    if [ "$DRY_RUN" = 0 ]; then
        PYTHONPATH="$PREFIX" python3 -c "
from kraken_unleashed import config as cfg
c = cfg.load('$LEGACY_CONFIG')
c['control']['group'] = '${DESKTOP_GROUP}' or None
cfg.save(c, '$CONFIG')
print('   migrated $LEGACY_CONFIG -> $CONFIG')"
    else
        echo "   would: migrate $LEGACY_CONFIG -> $CONFIG"
    fi
    ok "migrated your previous settings"
else
    if [ "$DRY_RUN" = 0 ]; then
        PYTHONPATH="$PREFIX" python3 -c "
from kraken_unleashed import config as cfg
c = cfg.defaults()
c['control']['group'] = '${DESKTOP_GROUP}' or None
cfg.save(c, '$CONFIG')"
    fi
    ok "wrote default $CONFIG"
fi
if [ -n "$DESKTOP_GROUP" ]; then
    ok "the app will reach the service as group '$DESKTOP_GROUP'"
else
    warn "Could not tell which user to grant access to (no SUDO_USER)."
    warn "Set control.group in $CONFIG or the GUI will not connect."
fi

# --------------------------------------------------------------------------- #
bold "[7/7] Service"
run install -m 644 "$SRC/systemd/kraken-unleashed.service" "$UNIT_DIR/"
run install -m 644 "$SRC/systemd/kraken-unleashed-resume.service" "$UNIT_DIR/"
run systemctl daemon-reload
if [ "$START" = 1 ]; then
    run systemctl enable kraken-unleashed.service kraken-unleashed-resume.service
    # restart, not "enable --now": on an upgrade the unit is already active, so
    # --now would do nothing and leave the old code running.
    run systemctl restart kraken-unleashed.service
    if [ "$DRY_RUN" = 0 ]; then
        sleep 3
        if systemctl is-active --quiet kraken-unleashed; then
            ok "kraken-unleashed is running"
        else
            err "kraken-unleashed did not stay up. Logs:"
            journalctl -u kraken-unleashed -n 20 --no-pager || true
            exit 1
        fi
    fi
else
    ok "skipped (--no-start). Enable with: systemctl enable --now kraken-unleashed"
fi

cat <<EOF

$(bold "Done.")

  Open the app:            Kraken Unleashed, from your applications menu
  ...or from a terminal:   kraken-unleashed

  From the command line:
      kraken-unleashed-ctl status
      kraken-unleashed-ctl set led.effect=rainbow
      kraken-unleashed-ctl preview /tmp/lcd.png

  Watch the service:       journalctl -u kraken-unleashed -f

  Customisation guide:     docs/CUSTOMISING.md
  Letting OpenRGB drive the lighting: docs/RGB.md
EOF
