# Troubleshooting

Start here:

```bash
systemctl status kraken-lcd
journalctl -u kraken-lcd -n 50 --no-pager
```

A healthy run prints a line like this when it stops:

```
3593 frames in 299.7s = 12.0 fps, 0 refused, payload avg 388 KB
```

`0 refused` is what you want. Refusals mean the device is rejecting frames.

---

## The cooler shows a white screen with a spinning rectangle

**It is in its bootloader.** Confirm:

```bash
lsusb -d 1e71:3011
```

If that prints a device ("NZXT BOOTarea"), the cooler has dropped into recovery
mode. This happens if something streams frames at it without flow control.

**Recovery needs a complete power cut:**

1. Shut the machine down.
2. Switch the power supply off at the wall (or flip the PSU's own switch).
3. Wait about 30 seconds.
4. Power on.

**A reboot will not fix it.** The device stays powered by the standby rail, so it
never fully resets. You have to actually remove power.

Your cooling is not at risk while this is happening — the pump runs on firmware
defaults — but the screen is unusable until you do this.

The shipped code refuses to touch a device in this state, and its flow control
exists specifically to prevent it. If you have modified the streaming path, read
[PROTOCOL.md § 4](PROTOCOL.md#4-flow-control-and-the-way-this-device-breaks)
before going further.

---

## The screen is black, flickering, or showing the wrong thing

Almost always **two programs are fighting over the device**. Only one process may
hold the Kraken's USB interface.

Check what else has it:

```bash
systemctl is-active coolercontrold openrgb
pgrep -a liquidctl
sudo fuser -v /dev/hidraw* 2>&1 | grep -i -B1 kraken
```

### CoolerControl

CoolerControl drives the LCD through liquidctl's bucket path, which fights this
service directly.

Disable the Kraken **device** in the CoolerControl UI. If you would rather edit
the file, in `/etc/coolercontrol/config.toml` find the Kraken's UID and add:

```toml
[settings.<uid>]
disable = true
```

and comment out its `[device-settings.<uid>]` block. Back the file up first, then
`sudo systemctl restart coolercontrold`.

**Your fan and pump curves keep working.** They are stored in the cooler's own
firmware, so disabling the device in CoolerControl does not stop them. It does
mean nothing on the host is adjusting them any more — see
[ARCHITECTURE.md](ARCHITECTURE.md#fan-and-pump-control).

### OpenRGB

OpenRGB cannot drive this cooler's LEDs anyway (its Hue2 `0x22` packets are
rejected by the firmware), and its probe interferes with status reads. Disable
the **NZXT Kraken detector** in OpenRGB's settings, or in
`/etc/openrgb/OpenRGB.json` set that detector to `false`, then restart it.

Details and the reasoning in [RGB.md](RGB.md).

### liquidctl

Don't run `liquidctl` against this device while the service is up. Its status
poll reads whatever report arrives next on the hidraw node, so a command from
another program lands in its reply and gives you nonsense temperatures.

---

## The service starts and immediately stops

```
Kraken 1e71:3012 not found
```

- Check `lsusb -d 1e71:3012` — if nothing, the cooler's **internal USB 2.0 header
  cable** is not connected. This is a separate cable from the pump power and the
  fan headers, and it is easy to miss during a build.
- If `lsusb` shows `1e71:3011` instead, see the bootloader section above.
- The service runs as root, so permissions are not usually the issue. If you are
  running it by hand, use `sudo`.

```
device refused 3 frames in a row - stopping
```

Something else grabbed the device mid-run, or the cooler is unhappy. Check for
conflicts as above. After three restarts in ten minutes systemd stops trying —
clear that with:

```bash
sudo systemctl reset-failed kraken-lcd
sudo systemctl start kraken-lcd
```

```
ModuleNotFoundError: No module named 'numpy'
```

Dependencies missing. Re-run `sudo ./install.sh`, or install `numpy`, `Pillow`
and `pyusb` for the **system** Python 3 — not a virtualenv, since the service
runs as root with `/usr/bin/python3`.

---

## The screen is upside down or on its side

Set `"rotate"` in `/etc/kraken-lcd.conf` to `0`, `90`, `180` or `270`. There's a
loop in [CUSTOMISING.md](CUSTOMISING.md#getting-it-the-right-way-up) that walks
through all four.

---

## The text is unreadable over my GIF

The readout is lightened over the background, so bright backgrounds win. Either
raise `"dim"` (0.6–0.7 tames most things) or pick a GIF with a dark centre. See
[CUSTOMISING.md](CUSTOMISING.md#the-background).

---

## The numbers say `--`

That sensor could not be read.

- **CPU temperature** — needs an `hwmon` driver named `coretemp` (Intel),
  `k10temp` or `zenpower` (AMD). Check with `sensors` or
  `cat /sys/class/hwmon/*/name`.
- **GPU** — NVIDIA needs `nvidia-smi` on `PATH`; AMD is read from the `amdgpu`
  hwmon node and `gpu_busy_percent`. Intel GPUs are not implemented.
- **Liquid / pump / fan** — comes from the cooler itself. If these are `--` but
  the screen is drawing, something else is stealing the status reports; see the
  conflict section.

A missing sensor is not an error and will not stop the service.

---

## It stops working after suspend

`kraken-lcd-resume.service` should restart it on wake. Check it is enabled:

```bash
systemctl is-enabled kraken-lcd-resume
```

If USB takes longer than 5 seconds to settle on your board, raise the
`ExecStartPre=/bin/sleep 5` in
`/etc/systemd/system/kraken-lcd-resume.service` and `daemon-reload`.

---

## It's using more CPU than I expected

Expect ~7% of one core at 12 fps. If it is much higher:

- A very large or very long GIF — every frame is held decoded in memory.
- `fps` set above 12.
- An edited renderer doing expensive drawing on every frame. The overlay is
  cached and only re-rendered when a displayed value changes, so this only
  matters if your renderer's output changes constantly.

---

## Reporting a bug

Please include:

```bash
# version and state
systemctl status kraken-lcd --no-pager
journalctl -u kraken-lcd -n 100 --no-pager

# hardware
lsusb -d 1e71:
cat /sys/class/hwmon/*/name

# config (check it for anything personal first)
cat /etc/kraken-lcd.conf

# what the frame looks like, without the device involved
python3 /opt/kraken-lcd/kraken_lcd.py --rotate 0 --preview /tmp/lcd.png
```

If the cooler ended up in the bootloader, say exactly what was running and what
you changed — that is the failure mode worth hardening against.
