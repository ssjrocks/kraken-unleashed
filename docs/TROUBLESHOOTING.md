# Troubleshooting

Start here:

```bash
kraken-unleashed-ctl diagnose        # checks the device directly, no service needed
kraken-unleashed-ctl status          # what the service thinks is going on
systemctl status kraken-unleashed
journalctl -u kraken-unleashed -n 50 --no-pager
```

`diagnose` is the one to reach for when nothing works — it does **not** go
through the service, so it still answers when the service won't start.

A healthy `status` shows a frame rate near 12 and `refused: 0`. Refusals mean the
device is rejecting frames.

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

Almost always **two programs are driving the LCD**. That is the one genuinely
exclusive part of this cooler: frame data goes over a USB interface only one
process can claim. Cooling and status are *not* exclusive and are not the
problem — CoolerControl or liquidctl can read and set those alongside this
service without trouble.

Check what else has it:

```bash
systemctl is-active coolercontrold openrgb
pgrep -a liquidctl
sudo fuser -v /dev/hidraw* 2>&1 | grep -i -B1 kraken
```

### CoolerControl

CoolerControl drives coolers through liquidctl's bucket path, which fights this
service directly.

First check whether it can even reach the cooler:

```bash
journalctl -u coolercontrold -b | grep -i liquidctl
```

If it says `liquidctl system Python package not found`, CoolerControl has **no**
cooler support and is not your problem — leave it alone.

Otherwise, disable the Kraken **device** in the CoolerControl UI. If you would
rather edit the file, in `/etc/coolercontrol/config.toml` find the Kraken's UID
and add:

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

OpenRGB cannot drive this cooler's LEDs (its Hue2 `0x22` packets are rejected by
the firmware), and its probe interferes with status reads. Disable the **NZXT
Kraken detector** in OpenRGB's settings, or in `/etc/openrgb/OpenRGB.json` set
that detector to `false`, then restart it.

If you *want* OpenRGB controlling the lighting, that is supported — but through
the E1.31 relay, which never has OpenRGB touch the device. See [RGB.md](RGB.md).

### liquidctl

Don't run `liquidctl` against this device while the service is up. Its status
poll reads whatever report arrives next on the hidraw node, so a command from
another program lands in its reply and gives you nonsense temperatures.

---

## The app says it cannot reach the service

```
Cannot reach the Kraken Unleashed service
```

First, is it running?

```bash
systemctl status kraken-unleashed
```

If it is, this is a permissions problem on the control socket. It is owned by
root and restricted to a group:

```bash
ls -l /run/kraken-unleashed/control.sock
# srw-rw---- 1 root yourgroup ...
id -nG            # are you in that group?
```

`install.sh` sets `control.group` to the group of whoever ran `sudo`. If you
installed another way, set it by hand — not with the CLI, which cannot reach the
service either when this is broken:

```bash
sudo sed -i "s/\"group\": null/\"group\": \"$(id -gn)\"/" /etc/kraken-unleashed.conf
sudo systemctl restart kraken-unleashed
```

Then check the socket picked the group up:

```bash
ls -l /run/kraken-unleashed/control.sock
```

---

## The service starts and immediately stops

```
Kraken 1e71:3012 not found
```

- Check `lsusb -d 1e71:3012` — if nothing, the cooler's **internal USB 2.0 header
  cable** is not connected. This is a separate cable from the pump power and the
  fan headers, and it is easy to miss during a build.
- If `lsusb` shows `1e71:3011` instead, see the bootloader section above.
- The service runs as root, so permissions are not usually the issue. Running it
  by hand needs `sudo`.

```
device refused 3 frames in a row - stopping
```

Something else grabbed the device mid-run, or the cooler is unhappy. Check for
conflicts as above. After three restarts in ten minutes systemd stops trying —
clear that with:

```bash
sudo systemctl reset-failed kraken-unleashed
sudo systemctl start kraken-unleashed
```

```
ModuleNotFoundError: No module named 'numpy'
```

Dependencies missing. Re-run `sudo ./install.sh`, or install `numpy`, `Pillow`
and `pyusb` for the **system** Python 3 — not a virtualenv, since the service
runs as root with `/usr/bin/python3`.

---

## The app won't start

```
ModuleNotFoundError: No module named 'gi'
```

The GUI needs PyGObject with GTK 4 and libadwaita. The service and the CLI do
not, so this only affects the app:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
```

(or your distribution's equivalent — see [INSTALL.md](INSTALL.md#requirements))

---

## OpenRGB isn't driving the lighting

```bash
kraken-unleashed-ctl status
```

Look at the `openrgb` block:

- `listening: false` — the relay is off. `kraken-unleashed-ctl set openrgb.enabled=true`
- `listening: true, packets: 0` — nothing is arriving. The E1.31 device in
  OpenRGB is missing or misconfigured, or OpenRGB's **E1.31 detector** is
  disabled. See [RGB.md](RGB.md#letting-openrgb-drive-the-cooler).
- `packets` climbing but the lights don't change — `led.source` is still
  `effect`. Set it to `openrgb`.
- `error` mentions a multicast join failing — harmless if OpenRGB is configured
  for unicast to `127.0.0.1`, which is the normal setup.

---

## The screen is upside down or on its side

Set `lcd.rotate` to `0`, `90`, `180` or `270` — in the app it's a dropdown, or:

```bash
kraken-unleashed-ctl set lcd.rotate=180
```

---

## The text is unreadable over my GIF

The readout is lightened over the background, so bright backgrounds win. Either
raise `lcd.dim` (0.6–0.7 tames most things) or pick a GIF with a dark centre. See
[CUSTOMISING.md](CUSTOMISING.md#the-background).

---

## The numbers say `--`

That sensor could not be read.

- **CPU temperature** — needs an `hwmon` driver named `coretemp` (Intel),
  `k10temp` or `zenpower` (AMD). Check with `sensors` or
  `cat /sys/class/hwmon/*/name`. On Windows it needs LibreHardwareMonitor
  running; see [WINDOWS.md](WINDOWS.md#cpu-temperature).
- **GPU** — NVIDIA needs `nvidia-smi` on `PATH`; AMD is read from the `amdgpu`
  hwmon node and `gpu_busy_percent`. Intel GPUs are not implemented.
- **Liquid / pump / fan** — comes from the cooler itself. If these are `--` but
  the screen is drawing, something else is stealing the status reports; see the
  conflict section.

A missing sensor is not an error and will not stop the service.

---

## It stops working after suspend

`kraken-unleashed-resume.service` should restart it on wake. Check it is enabled:

```bash
systemctl is-enabled kraken-unleashed-resume
```

If USB takes longer than 5 seconds to settle on your board, raise the
`ExecStartPre=/bin/sleep 5` in
`/etc/systemd/system/kraken-unleashed-resume.service` and `daemon-reload`.

---

## It's using more CPU than I expected

Expect ~7% of one core at 12 fps. If it is much higher:

- A very large or very long GIF — every frame is held decoded in memory.
- `lcd.fps` set above 12.
- An edited renderer doing expensive drawing on every frame. The overlay is
  cached and only re-rendered when a displayed value changes, so this only
  matters if your renderer's output changes constantly.

---

## Reporting a bug

Please include:

```bash
kraken-unleashed-ctl diagnose
kraken-unleashed-ctl status
systemctl status kraken-unleashed --no-pager
journalctl -u kraken-unleashed -n 100 --no-pager

lsusb -d 1e71:
cat /sys/class/hwmon/*/name

# check it for anything personal first
cat /etc/kraken-unleashed.conf

# what the frame looks like, without the device involved
kraken-unleashed-ctl preview /tmp/lcd.png
```

If the cooler ended up in the bootloader, say exactly what was running and what
you changed — that is the failure mode worth hardening against.
