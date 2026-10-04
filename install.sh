#!/usr/bin/env bash
#
# Kraken Unleashed installer.
#
#   sudo ./install.sh              install or upgrade, then start
#   sudo ./install.sh --no-start   install but don't enable/start the service
#   sudo ./install.sh --dry-run    print what would happen, change nothing
#
# Safe to re-run: your /etc/kraken-lcd.conf is never overwritten.

set -euo pipefail

PREFIX=/opt/kraken-lcd
CONFIG=/etc/kraken-lcd.conf
UNIT_DIR=/etc/systemd/system
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

bold "== Kraken Unleashed =="
[ "$DRY_RUN" = 1 ] && warn "dry run: nothing will be changed"

# --------------------------------------------------------------------------- #
# 1. Hardware
# --------------------------------------------------------------------------- #
bold "[1/6] Looking for the cooler"
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
# 2. Dependencies
# --------------------------------------------------------------------------- #
bold "[2/6] Checking Python dependencies"
missing=()
for mod in numpy PIL usb; do
    python3 -c "import $mod" 2>/dev/null || missing+=("$mod")
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "   missing: ${missing[*]}"
    if   command -v apt-get >/dev/null; then pkgs=(python3-numpy python3-pil python3-usb); mgr=(apt-get install -y)
    elif command -v dnf     >/dev/null; then pkgs=(python3-numpy python3-pillow python3-pyusb); mgr=(dnf install -y)
    elif command -v pacman  >/dev/null; then pkgs=(python-numpy python-pillow python-pyusb); mgr=(pacman -S --needed --noconfirm)
    elif command -v zypper  >/dev/null; then pkgs=(python3-numpy python3-Pillow python3-pyusb); mgr=(zypper install -y)
    else
        err "Unknown package manager. Install numpy, Pillow and pyusb for python3, then re-run."
        exit 1
    fi
    echo "   installing: ${pkgs[*]}"
    run "${mgr[@]}" "${pkgs[@]}"
else
    ok "numpy, Pillow and pyusb are present"
fi

# A bold font makes the readout look right; the renderer falls back if absent.
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
    warn "bitmap fallback and look rough. Install fonts-dejavu-core (or your"
    warn "distro's equivalent) for the intended look."
fi

# --------------------------------------------------------------------------- #
# 3. Conflicts -- the usual reason this does not work
# --------------------------------------------------------------------------- #
bold "[3/6] Checking for other software holding the cooler"
conflict=0
if systemctl is-active --quiet coolercontrold 2>/dev/null; then
    warn "CoolerControl is running. It must NOT drive this cooler's LCD, or the"
    warn "two will fight over the USB interface and the screen will flicker or"
    warn "go black. Open CoolerControl and disable the Kraken device, or see"
    warn "docs/TROUBLESHOOTING.md#coolercontrol for the config.toml edit."
    conflict=1
fi
if systemctl is-active --quiet openrgb 2>/dev/null; then
    warn "OpenRGB is running. Disable its NZXT Kraken detector so it never opens"
    warn "that hidraw node -- this service drives the cooler's LEDs itself."
    warn "See docs/RGB.md."
    conflict=1
fi
if pgrep -x liquidctl >/dev/null 2>&1; then
    warn "liquidctl is running and will contend for the device."
    conflict=1
fi
[ "$conflict" = 0 ] && ok "nothing else appears to hold the cooler"

# --------------------------------------------------------------------------- #
# 4. Files
# --------------------------------------------------------------------------- #
bold "[4/6] Installing to $PREFIX"
run install -d -m 755 "$PREFIX"
run cp -r "$SRC/src/." "$PREFIX/"
run find "$PREFIX" -name '__pycache__' -type d -prune -exec rm -rf {} +
run chmod 755 "$PREFIX/kraken_lcd.py"
# The program looks for its default background in assets/ beside itself.
run install -d -m 755 "$PREFIX/assets"
run install -m 644 "$SRC/assets/demo.gif" "$PREFIX/assets/demo.gif"
ok "installed $PREFIX/kraken_lcd.py, the vendored renderer and the demo background"

if [ -e "$CONFIG" ]; then
    ok "keeping your existing $CONFIG"
else
    run install -m 644 "$SRC/config/kraken-lcd.conf" "$CONFIG"
    ok "wrote default $CONFIG"
fi

# --------------------------------------------------------------------------- #
# 5. systemd
# --------------------------------------------------------------------------- #
bold "[5/6] Installing systemd units"
run install -m 644 "$SRC/systemd/kraken-lcd.service" "$UNIT_DIR/"
run install -m 644 "$SRC/systemd/kraken-lcd-resume.service" "$UNIT_DIR/"
run systemctl daemon-reload
ok "kraken-lcd.service and kraken-lcd-resume.service installed"

# --------------------------------------------------------------------------- #
# 6. Start
# --------------------------------------------------------------------------- #
bold "[6/6] Starting"
if [ "$START" = 1 ]; then
    run systemctl enable kraken-lcd.service kraken-lcd-resume.service
    # restart, not "enable --now": on an upgrade the unit is already active, so
    # --now would do nothing and leave the old code running.
    run systemctl restart kraken-lcd.service
    if [ "$DRY_RUN" = 0 ]; then
        sleep 3
        if systemctl is-active --quiet kraken-lcd; then
            ok "kraken-lcd is running"
        else
            err "kraken-lcd did not stay up. Logs:"
            journalctl -u kraken-lcd -n 20 --no-pager || true
            exit 1
        fi
    fi
else
    ok "skipped (--no-start). Enable with: systemctl enable --now kraken-lcd"
fi

cat <<EOF

$(bold "Done.")

  Preview a screen without touching the cooler:
      python3 $PREFIX/kraken_lcd.py --rotate 0 --preview /tmp/lcd.png

  Change what is shown (background, layout, brightness, rotation):
      sudo nano $CONFIG && sudo systemctl restart kraken-lcd

  Watch it:
      journalctl -u kraken-lcd -f

  Full customisation guide: docs/CUSTOMISING.md
EOF
