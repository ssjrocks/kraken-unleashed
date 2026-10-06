# Changelog

## 2.1.0 — 2026-10-06

### Added
- **Pump and fan control**, with a Cooling page in the app. Four modes:
  `firmware` (write nothing — still the default, so upgrades change nothing),
  `default` (a quiet preset), `curve` (four editable points per channel) and
  `full`. The curve is uploaded to the cooler, which then runs it itself, so it
  keeps working if the service stops.
- A `(59 °C, 100%)` failsafe point is appended to every curve before upload, and
  duties are clamped to the firmware's own limits (20% pump floor).
- **Choose which temperature the curve follows** — `cooling.sensor`, one of
  `liquid`, `cpu` or `gpu`. `liquid` is uploaded to the cooler and runs there;
  `cpu`/`gpu` are evaluated by the daemon and pushed as a fixed speed, with the
  coolant failsafe still underneath. The reading is smoothed, since CPU
  temperature is noisy enough to make a steep curve hunt audibly. Curve
  breakpoints follow the sensor (20–50 °C for coolant, 40–85 °C for CPU/GPU).
- `kraken-unleashed-ctl set` now accepts JSON values, so curves can be set from
  the shell: `set 'cooling.pump=[[20,40],[50,100]]'`.

### Fixed
- An invalid cooling curve is rejected before it is written to the config.
  Previously a malformed value was persisted and then failed to apply on every
  start, silently and forever.

## 2.0.1 — 2026-10-05

### Fixed
- **`uninstall.sh` removed the 1.x install, not this one.** Wrong prefix, wrong
  config, wrong units, and it left the binaries, desktop entry and icon behind.
  It now removes the 2.0 install, clears 1.x leftovers too, and says what to
  undo in OpenRGB and CoolerControl afterwards.
- **The documentation was still 1.x throughout.** It told people to run
  `/opt/kraken-lcd/kraken_lcd.py`, which 2.0 deletes — so the quickstart failed
  on its first command. INSTALL, CUSTOMISING and TROUBLESHOOTING are rewritten
  around the app, the CLI and the sectioned config; every command in them has
  been run against real hardware.
- RGB.md gained the section 2.0 shipped without: how to let OpenRGB drive the
  cooler over E1.31, including that OpenRGB's E1.31 detector is probably
  disabled if its detector list was ever trimmed.

### Changed
- **Relicensed from MIT to GNU AGPL v3 or later.** Derivative works now have to
  stay open under the same terms, including when a modified version is offered
  to others over a network. Releases already published under MIT (v1.0.0,
  v2.0.0) remain MIT — a licence cannot be withdrawn from what was already
  distributed.
- The vendored sensor-screen renderer in `src/ok/` **stays under its original
  MIT licence** from OpenKraken. MIT is AGPL-compatible, so the combined work
  ships under the AGPL without relicensing anyone else's code. See
  [NOTICE](NOTICE).

### Removed
- The 1.x entry point `src/kraken_lcd.py` and the retired `kraken-lcd` systemd
  units. Unreferenced since 2.0, and the entry point still claimed MIT.

## 2.0.0 — 2026-10-05

Kraken Unleashed becomes an application rather than a single-purpose streamer.

### Added
- **GTK4/libadwaita desktop app** (Linux) with a live preview rendered by the
  service without touching the cooler, plus `kraken-unleashed-ctl` for scripting.
- **Control socket.** The service is still the only process that may hold the
  cooler; everything else is now a client of it. Unix socket on Linux,
  group-restricted; loopback TCP with a token file on Windows.
- **LED effect engine**: breathing, static, pulse, spectrum, rainbow, wave,
  chase, gradient, and a coolant-temperature mode. Rendered per LED across the
  ring and fans as one 48-LED strip, still phase-locked to the boot clock so an
  effect stays in step with rgb-sync elsewhere on the machine.
- **OpenRGB support over E1.31.** OpenRGB cannot drive this cooler directly —
  its Hue2 packets are rejected by the firmware — and an OpenRGB plugin would
  not help, because plugins only load in its GUI. Instead the service listens
  for sACN, which OpenRGB already speaks, and relays it to the LEDs. OpenRGB
  never touches the device, so the one-owner rule holds, and every OpenRGB
  effect and profile works on the cooler. Falls back to the local effect when
  nothing is sending.
- **Experimental Windows build** (service + CLI). The cooler is a composite
  device, so WinUSB binds to interface 0 while interface 1 keeps the native HID
  driver. **Not tested on hardware** — see [docs/WINDOWS.md](docs/WINDOWS.md).
- `kraken-unleashed-ctl diagnose`, which checks the device and driver binding
  without going through the control socket.

### Changed
- Config moves to `/etc/kraken-unleashed.conf` (Windows:
  `C:\ProgramData\KrakenUnleashed\config.json`) and gains sections. 1.x
  configs are migrated on upgrade rather than reset.
- `kraken-lcd.service` is replaced by `kraken-unleashed.service`; the installer
  retires the old one. `/run/kraken-lcd/status.json` is still written on Linux so
  existing exporters keep working.
- Platform-specific code is isolated in `compat.py` and `transport.py`.

### Fixed
- The control server's cleanup ran on a daemon thread the interpreter kills at
  shutdown, leaving a stale socket in `/run`.
- `--preview` only worked after installation, not from a clone — which is the
  first command the customisation guide tells people to run.
- Windows builds shipped without libusb: pyusb loads it via ctypes, so
  PyInstaller never saw it and every USB call failed with `NoBackendError`.

## 1.0.0 — 2026-10-04

First release.

### Added
- q565 streaming to the NZXT Kraken 2024 Elite LCD at 12 fps, roughly five times
  the ~2.4 fps ceiling of the bucket upload path every other Linux tool uses.
- Sensor readout (coolant, CPU, GPU, pump and fan RPM) composited over an
  animated GIF background, in three layouts: `triple`, `liquid_ring`, `cpu_gpu`.
- Direct control of the cooler's ring and radiator-fan LEDs over the `26 14`
  command, which OpenRGB cannot drive on this firmware.
- Phase-locked breathing shared with `rgb-sync` via `CLOCK_BOOTTIME` and a
  common config file, with no coordination between the processes.
- `/etc/kraken-lcd.conf` for all settings, and `--preview` to render a frame to
  a PNG without touching the device.
- Automatic CPU sensor detection for Intel (`coretemp`) and AMD (`k10temp`,
  `zenpower`); GPU via `nvidia-smi` or the `amdgpu` hwmon node.
- `install.sh` / `uninstall.sh` with dependency handling for apt, dnf, pacman
  and zypper, and detection of software that would fight over the device.
- Resume-from-sleep handling, and restart limits that stop rather than hammer a
  device that is refusing frames.
- Cooler readings published to `/run/kraken-lcd/status.json` for other tools.
- Documentation of the LCD protocol, including the q565 container format and the
  flow-control rules that keep the device out of its bootloader.
- `extras/rgb-sync/`, the optional companion daemon for every other OpenRGB
  device.
