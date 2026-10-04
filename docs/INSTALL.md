# Installing

## Quick version

```bash
git clone https://github.com/ssjrocks/kraken-unleashed.git
cd kraken-unleashed
sudo ./install.sh
```

The installer checks your hardware, installs Python dependencies, warns about
other software that would fight over the cooler, installs the service and starts
it.

Useful flags:

```bash
sudo ./install.sh --dry-run    # show exactly what it would do, change nothing
sudo ./install.sh --no-start   # install without enabling the service
```

Re-running it upgrades in place. **Your `/etc/kraken-lcd.conf` is never
overwritten.**

---

## What it puts where

| Path | What |
|---|---|
| `/opt/kraken-lcd/kraken_lcd.py` | the program |
| `/opt/kraken-lcd/ok/` | the vendored sensor-screen renderer |
| `/opt/kraken-lcd/assets/demo.gif` | the default background |
| `/etc/kraken-lcd.conf` | your settings — yours, never overwritten |
| `/etc/systemd/system/kraken-lcd.service` | the service |
| `/etc/systemd/system/kraken-lcd-resume.service` | restarts it after sleep |
| `/run/kraken-lcd/status.json` | live cooler readings for other tools (tmpfs) |

## Requirements

- **NZXT Kraken 2024 Elite** or **Elite V2** — USB ID `1e71:3012`
- Linux with systemd
- Python 3.9 or newer
- `numpy`, `Pillow`, `pyusb` for the **system** Python 3
- A bold TrueType font (DejaVu, Liberation or Noto) for the readout to look right

The service runs as root because it opens the cooler's hidraw node and claims its
USB interface.

### Why not a virtualenv

The unit runs `/usr/bin/python3` as root. Installing the dependencies into a
virtualenv in your home directory will not help it. Use your distribution's
packages:

| Distro | Command |
|---|---|
| Debian / Ubuntu | `sudo apt install python3-numpy python3-pil python3-usb fonts-dejavu-core` |
| Fedora | `sudo dnf install python3-numpy python3-pillow python3-pyusb dejavu-sans-fonts` |
| Arch | `sudo pacman -S python-numpy python-pillow python-pyusb ttf-dejavu` |
| openSUSE | `sudo zypper install python3-numpy python3-Pillow python3-pyusb dejavu-fonts` |

`install.sh` does this for you on all four.

---

## Before you start: make room for it

Only one process may hold the Kraken's USB interface. If any of these are
running, deal with them first — the installer warns, but will not change other
people's software for you.

**CoolerControl** — disable the Kraken *device* in its UI. Your fan and pump
curves are stored in the cooler's firmware and keep working.

**OpenRGB** — disable the NZXT Kraken detector. OpenRGB cannot drive this
cooler's LEDs anyway; this service does it instead. See [RGB.md](RGB.md).

**liquidctl** — don't run it against this device while the service is up.

Full details and the file edits in
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#the-screen-is-black-flickering-or-showing-the-wrong-thing).

---

## Check it worked

```bash
systemctl status kraken-lcd
journalctl -u kraken-lcd -n 20 --no-pager
```

You should see `device ok:` with a liquid temperature and pump RPM. The screen
should be showing the demo background with your temperatures over it.

If it isn't, [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## Running it by hand

Useful while you are tuning. Stop the service first so the two don't fight:

```bash
sudo systemctl stop kraken-lcd
sudo python3 /opt/kraken-lcd/kraken_lcd.py --seconds 20 --fps 12
sudo systemctl start kraken-lcd
```

`--seconds` makes it exit on its own, which is safer than leaving an ad-hoc run
holding the device.

To render a frame without touching the cooler at all, no root needed:

```bash
python3 /opt/kraken-lcd/kraken_lcd.py --rotate 0 --preview /tmp/lcd.png
```

---

## Upgrading

```bash
cd kraken-unleashed
git pull
sudo ./install.sh
```

Your config survives. Anything you edited inside `/opt/kraken-lcd/` does not —
if you have customised the renderer, keep those edits in your clone under
`src/ok/backend/` and install from there.

## Removing

```bash
sudo ./uninstall.sh            # keeps /etc/kraken-lcd.conf
sudo ./uninstall.sh --purge    # removes it too
```

The cooler keeps showing the last frame it received until something else writes
to it; its own firmware screen comes back after a power cycle. If you disabled
the device in CoolerControl or OpenRGB to make room, re-enable it there to hand
the cooler back.
