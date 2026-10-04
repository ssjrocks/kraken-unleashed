# Windows (experimental)

> **This build has not been tested on real hardware.** It is published so people
> can try it and report back. The Linux build is the tested one. If you try this
> and it works — or doesn't — please
> [open an issue](https://github.com/ssjrocks/kraken-unleashed/issues) and say so.

The Windows build is the **service and the command-line client**. The GTK4 app
is Linux-only for now; on Windows you configure things by editing a JSON file
and using `kraken-unleashed-ctl.exe`.

Everything else works: the LCD at 12 fps, all the LED effects, and the E1.31
relay that lets OpenRGB drive the cooler's lighting.

---

## Should you use this?

Be honest with yourself about the answer. On Windows, **NZXT CAM and SignalRGB
already do this well** — in fact the fast LCD protocol in this project was
reverse-engineered from a capture of SignalRGB. If you just want stats on your
screen, use those.

This is worth running on Windows if you want lighting and LCD driven by
something open, scriptable and without a background suite — or if you run the
same configuration on both Linux and Windows and want it to behave identically.

**Installing it will stop NZXT CAM and SignalRGB from driving the LCD**, because
of the driver change below. That is reversible, but know it before you start.

---

## The driver step

This is the part that needs care, and the only part that is genuinely fiddly.

The Kraken presents **two USB interfaces**:

| Interface | Class | Endpoint | Carries |
|---|---|---|---|
| **0** | Vendor Specific | `0x02` OUT (bulk) | LCD frame data |
| **1** | HID | `0x81` IN / `0x01` OUT | Commands, status, LEDs |

Interface 1 works out of the box — Windows' own HID driver handles it and the
app reaches it through hidapi. **Interface 0 has no inbox driver**, so Windows
leaves it unbound and nothing can send frames to the screen. You have to bind
**WinUSB** to it.

> **Bind WinUSB to interface 0 ONLY.** If you bind it to the whole device, you
> take interface 1 away from the HID driver as well, and the app loses the
> commands, status and LEDs. `kraken-unleashed-ctl.exe diagnose` tells you if
> you've done this.

### With Zadig

1. Download [Zadig](https://zadig.akeo.ie/).
2. **Options → List All Devices** (tick it).
3. **Uncheck** "Ignore Hubs or Composite Parents".
4. In the dropdown pick the entry that reads something like
   **`NZXT Kraken Elite V2 (Interface 0)`**. The USB ID must be `1E71 3012`
   and it **must say Interface 0**.
5. Set the target driver to **WinUSB**.
6. Click **Replace Driver** (or Install Driver).

Then check it:

```powershell
.\kraken-unleashed-ctl.exe diagnose
```

A healthy result:

```
[ok]   found Kraken 1e71:3012 on USB
[ok]   interfaces present: [0, 1]
[ok]   interface 0 (bulk) can be claimed
[ok]   hidapi sees 1 HID interface(s)
overall: OK
```

If it says hidapi cannot see the HID interface, you bound WinUSB too widely —
go back and re-bind to interface 0 only.

### Undoing it

Device Manager → find the Kraken's WinUSB entry → **Uninstall device**, tick
"Attempt to remove the driver", then unplug/replug or reboot. Windows restores
the original state and NZXT CAM works again.

---

## Installing

1. Download `KrakenUnleashed-windows-x64.zip` from the
   [releases page](https://github.com/ssjrocks/kraken-unleashed/releases) and
   unzip it somewhere permanent — `C:\Program Files\KrakenUnleashed` is fine.
2. Do the driver step above.
3. Open **PowerShell as Administrator** in that folder and run:

```powershell
.\install-service.ps1
```

That checks the device first, then registers a Scheduled Task that starts the
daemon at boot as SYSTEM. It's a scheduled task rather than a true service
because the daemon is a plain console program; wrapping it would add a
dependency for no real gain.

Remove it with `.\uninstall-service.ps1`.

---

## Configuring

Settings live in:

```
C:\ProgramData\KrakenUnleashed\config.json
```

Same schema as Linux, so [CUSTOMISING.md](CUSTOMISING.md) applies in full —
background image, layout, dimming, rotation, frame rate, effects. Edit the file
and restart:

```powershell
Restart-ScheduledTask -TaskName KrakenUnleashed
```

Or change things live, without a restart:

```powershell
.\kraken-unleashed-ctl.exe status
.\kraken-unleashed-ctl.exe set led.effect=rainbow led.params.period=8
.\kraken-unleashed-ctl.exe set lcd.dim=0.55
.\kraken-unleashed-ctl.exe preview C:\Users\you\Desktop\lcd.png
```

`preview` renders a frame to a PNG without touching the cooler, which is the
quickest way to audition a background or layout.

---

## Letting OpenRGB drive the lighting

Works exactly as on Linux — see [RGB.md](RGB.md). Turn the relay on:

```powershell
.\kraken-unleashed-ctl.exe set openrgb.enabled=true led.source=openrgb
```

then add an E1.31 device in OpenRGB pointing at `127.0.0.1`, universe 1, start
channel 1, 48 LEDs, and make sure the **E1.31** detector is enabled in
OpenRGB's settings.

---

## What is different from Linux

| | Linux | Windows |
|---|---|---|
| GUI | GTK4 app | not yet — CLI and config file |
| Service | systemd unit | Scheduled Task, running as SYSTEM |
| Config | `/etc/kraken-unleashed.conf` | `C:\ProgramData\KrakenUnleashed\config.json` |
| Control channel | Unix socket, group-restricted | loopback TCP + token file |
| Driver work | none | WinUSB on interface 0 |
| **CPU temperature** | from `hwmon`, always available | **needs LibreHardwareMonitor running** |
| GPU | `nvidia-smi` / `amdgpu` | `nvidia-smi` only |

### CPU temperature

Windows gives an ordinary program no reliable way to read CPU temperature — it
needs a kernel driver. So this build reads it from
[LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)'s
WMI provider if that is running, and reports nothing otherwise. The screen then
shows `--` for CPU rather than a made-up number.

To get CPU temperature: install LibreHardwareMonitor, and in its options enable
**Remote Web Server → WMI Provider** (and set it to start with Windows).
Coolant temperature, pump and fan RPM come from the cooler itself and are always
available.

---

## If it doesn't work

```powershell
.\kraken-unleashed-ctl.exe diagnose
```

**"cannot claim interface 0"** — the WinUSB bind didn't take. Redo the driver
step, and check Device Manager shows WinUSB on the Kraken's interface 0.

**"hidapi cannot see the HID interface"** — WinUSB was bound too widely. Re-bind
to interface 0 only.

**"Kraken 1e71:3012 not found"** — the cooler's internal USB 2.0 header cable
isn't connected, or Windows hasn't enumerated it. Check Device Manager.

**"Kraken is in BOOTLOADER mode"** — see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md). You need a full power cut; a reboot
will not clear it.

**The task runs but nothing happens** — run the daemon in the foreground from an
admin PowerShell to see its output:

```powershell
Stop-ScheduledTask -TaskName KrakenUnleashed
.\kraken-unleashed-daemon.exe
```

---

## Building it yourself

```powershell
git clone https://github.com/ssjrocks/kraken-unleashed.git
cd kraken-unleashed
python -m pip install -r packaging\windows\requirements.txt
pyinstaller packaging\windows\kraken-unleashed.spec
```

The build lands in `dist\KrakenUnleashed\`. This is exactly what CI does — see
`.github/workflows/windows.yml`.
