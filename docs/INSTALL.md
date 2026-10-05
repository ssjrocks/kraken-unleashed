# Installing

## Quick version

```bash
git clone https://github.com/ssjrocks/kraken-unleashed.git
cd kraken-unleashed
sudo ./install.sh
```

The installer checks your hardware, installs dependencies, warns about other
software that would fight over the cooler, retires any 1.x install, and starts
the service.

Then open **Kraken Unleashed** from your applications menu.

Useful flags:

```bash
sudo ./install.sh --dry-run    # show exactly what it would do, change nothing
sudo ./install.sh --no-start   # install without enabling the service
```

Re-running it upgrades in place. **Your `/etc/kraken-unleashed.conf` is never
overwritten.**

> **Windows:** an experimental service + CLI build is on the
> [releases page](https://github.com/ssjrocks/kraken-unleashed/releases).
> See [WINDOWS.md](WINDOWS.md) — it needs a driver step Linux does not.

---

## What it puts where

| Path | What |
|---|---|
| `/opt/kraken-unleashed/` | the program and the vendored renderer |
| `/opt/kraken-unleashed/assets/demo.gif` | the default background |
| `/usr/bin/kraken-unleashed` | the app |
| `/usr/bin/kraken-unleashed-daemon` | the service |
| `/usr/bin/kraken-unleashed-ctl` | the command-line client |
| `/etc/kraken-unleashed.conf` | your settings — yours, never overwritten |
| `/etc/systemd/system/kraken-unleashed.service` | the service |
| `/etc/systemd/system/kraken-unleashed-resume.service` | restarts it after sleep |
| `/run/kraken-unleashed/control.sock` | the control socket the app talks to |
| `/run/kraken-unleashed/status.json` | live cooler readings for other tools |
| `/run/kraken-lcd/status.json` | the same, at the 1.x path, for existing exporters |

## Requirements

- **NZXT Kraken 2024 Elite** or **Elite V2** — USB ID `1e71:3012`
- Linux with systemd
- Python 3.9 or newer
- `numpy`, `Pillow`, `pyusb` for the **system** Python 3
- `PyGObject` with **GTK 4** and **libadwaita** for the app (the service and the
  CLI work without them)
- A bold TrueType font (DejaVu, Liberation or Noto) for the readout to look right

The service runs as root because it opens the cooler's hidraw node and claims its
USB interface. The app does **not** — it talks to the service over a socket.

### Why not a virtualenv

The unit runs `/usr/bin/python3` as root. Dependencies in a virtualenv in your
home directory will not help it. Use your distribution's packages:

| Distro | Command |
|---|---|
| Debian / Ubuntu | `sudo apt install python3-numpy python3-pil python3-usb python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 fonts-dejavu-core` |
| Fedora | `sudo dnf install python3-numpy python3-pillow python3-pyusb python3-gobject gtk4 libadwaita dejavu-sans-fonts` |
| Arch | `sudo pacman -S python-numpy python-pillow python-pyusb python-gobject gtk4 libadwaita ttf-dejavu` |
| openSUSE | `sudo zypper install python3-numpy python3-Pillow python3-pyusb python3-gobject gtk4 libadwaita dejavu-fonts` |

`install.sh` does this for you on all four.

### Reaching the service from the app

The control socket is owned by root and restricted to a group. `install.sh` sets
that group to the group of the user running `sudo`, so the app works immediately
without a re-login. If you install some other way, set `control.group` in the
config to a group you are in.

---

## Before you start: make room for it

Only one process may hold the Kraken's USB interface. If any of these are
running, deal with them first — the installer warns, but will not change other
people's software for you.

**CoolerControl** — disable the Kraken *device* in its UI. Your fan and pump
curves are stored in the cooler's firmware and keep working. (If CoolerControl
has no `liquidctl` installed, it cannot reach the cooler at all and there is
nothing to do.)

**OpenRGB** — disable the NZXT Kraken detector. OpenRGB cannot drive this
cooler's LEDs anyway; this service does it instead. If you *want* OpenRGB
controlling the lighting, that works too — through the E1.31 relay, which does
not involve OpenRGB touching the device. See [RGB.md](RGB.md).

**liquidctl** — don't run it against this device while the service is up.

Full details in
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#the-screen-is-black-flickering-or-showing-the-wrong-thing).

---

## Check it worked

```bash
systemctl status kraken-unleashed
kraken-unleashed-ctl status
```

`status` should print a coolant temperature, pump and fan RPM, and a frame rate
around 12 fps with `0` refused. The screen should be showing the demo background
with your temperatures over it.

If it isn't, [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — and
`kraken-unleashed-ctl diagnose` checks the device directly, without needing the
service to be running.

---

## Running it by hand

Useful while you are tuning. Stop the service first so the two don't fight:

```bash
sudo systemctl stop kraken-unleashed
sudo kraken-unleashed-daemon
sudo systemctl start kraken-unleashed
```

To render a frame without touching the cooler at all — no root, no service:

```bash
kraken-unleashed-daemon --preview /tmp/lcd.png
```

---

## Upgrading

```bash
cd kraken-unleashed
git pull
sudo ./install.sh
```

### From 1.x

`install.sh` handles it: it disables and removes `kraken-lcd.service`, deletes
`/opt/kraken-lcd`, and migrates `/etc/kraken-lcd.conf` into the new sectioned
`/etc/kraken-unleashed.conf` rather than resetting your display settings.

`/run/kraken-lcd/status.json` is still written, so anything already reading it
keeps working.

Anything you edited inside `/opt/` does not survive an upgrade. If you have
customised the renderer, keep those edits in your clone under `src/ok/backend/`
and install from there.

## Removing

```bash
sudo ./uninstall.sh            # keeps /etc/kraken-unleashed.conf
sudo ./uninstall.sh --purge    # removes it too
```

The cooler keeps showing the last frame it received until something else writes
to it; its own firmware screen comes back after a power cycle. If you disabled
the device in CoolerControl or OpenRGB to make room, re-enable it there.
