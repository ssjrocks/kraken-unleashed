# Changelog

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
