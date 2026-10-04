# Lighting: the cooler's LEDs, and keeping everything in step

Two separate problems live here:

1. **Driving the Kraken's own ring and fan LEDs**, which OpenRGB cannot do on
   this firmware. Kraken Unleashed does it itself.
2. **Keeping them in phase with the rest of your lighting**, which is solved
   without the two programs talking to each other at all.

---

## 1. The cooler's LEDs

### The command

Over the **same hidraw node** used for frames and status:

```
26 14 <channel> <group> | 24 × GRB triplets      padded to 513 bytes
```

| Channel | Group | Covers |
|---|---|---|
| `01` | `01` | pump ring |
| `02` | `02` | radiator fans |

This is exactly what SignalRGB sends, every frame.

### Two things that will catch you

**The byte order is GRB, not RGB.** This was found the obvious way: sending
`(0, 255, 0)` lit the ring red. The code swaps the first two bytes:

```python
body = bytes((rgb[1], rgb[0], rgb[2])) * 24
```

**OpenRGB cannot drive this device, and trying is actively harmful.** OpenRGB
uses the older Hue2 direct packets (`0x22`), which this firmware rejects. The
rejection arrives as an `ff 01` report on the hidraw node — the same node
liquidctl polls for status, reading whatever report shows up next. So an OpenRGB
write doesn't just fail, it poisons someone else's temperature reading.

**Disable the NZXT Kraken detector in OpenRGB entirely.** Not "leave it alone" —
its detection probe alone is enough to interfere. In `/etc/openrgb/OpenRGB.json`,
under `Detectors`, set the Kraken entry to `false`, then restart OpenRGB.

### Related NZXT devices

The separate **NZXT RGB Controller** (`1e71:2021`, the Hue2 box your case fans
plug into) is a different story: OpenRGB's Direct mode works fine on it. It
ignores OpenRGB's *hardware effect* modes, but Direct — where the host sends
every frame — is reliable.

---

## 2. Staying in phase without coordination

The cooler's LEDs are driven by Kraken Unleashed. Everything else — RAM, GPU,
motherboard headers, case fans — is typically driven by OpenRGB. Two independent
processes, two different buses, and they need to breathe as one.

The trick is to not synchronise at all. Both compute the same curve from a clock
every process on the machine already shares:

```python
t = time.clock_gettime(time.CLOCK_BOOTTIME)
phase = (1 - math.cos(2 * math.pi * t / period)) / 2
level = (min_b + (max_b - min_b) * phase) ** gamma
```

`CLOCK_BOOTTIME` is identical in every process, so two daemons started hours
apart produce the same value at the same instant. No IPC, no master, no drift,
and either side can restart without resynchronising.

They share **settings** through one file, `/etc/rgb-sync.json`. Kraken
Unleashed's `led.follow` config key points at it; if it exists, its values win.
Change the colour there and both follow.

A raised cosine (not a triangle or a sine) makes the breath ease in and out, and
`gamma` 2.2 corrects for the eye's response so the fade looks even rather than
spending most of its time looking bright.

---

## 3. rgb-sync

`extras/rgb-sync/` holds the companion daemon that drives everything *except* the
cooler, through OpenRGB's SDK. It is optional — Kraken Unleashed works without
it — but the two together are the complete picture.

```bash
cd extras/rgb-sync
sudo ./install.sh
```

It needs a running OpenRGB server (`openrgb --server`) and
`openrgb-python==0.3.7`.

### Why stream frames instead of using hardware effects

Every controller's built-in Breathing mode runs on its own clock. Start them
together and they visibly drift apart within minutes. Streaming one shared curve
to all of them is the only way to keep a dozen devices actually in step.

The cost is that the host now has to deliver every frame — which leads directly
to the most interesting bug in this project.

---

## 4. The Kingston Fury DDR5 desync, and what it teaches

**Symptom:** with everything breathing green, one RAM stick would visibly lag the
other three by roughly half a second, intermittently, worst when the breath was
near its darkest.

**First hypothesis, wrong:** the sticks' effect clocks had drifted. Kingston FURY
modules have *Infrared Sync Technology* — IR transceivers on the DIMM tops that
keep the modules' lighting aligned — so a natural guess is that it had failed.

It hadn't, and it couldn't have helped: **IR sync only aligns the modules' own
onboard effects with each other.** In Direct mode the host supplies every
colour, there is no onboard effect running, and IR sync has nothing to align.
Using it would mean putting the RAM on a hardware mode and giving up phase-match
with everything else.

**Actual cause,** found by tracing OpenRGB's SMBus traffic:

```bash
sudo strace -f -p $(pgrep -x openrgb) -e trace=ioctl -T -tt
```

```
ioctl(6, _IOC(_IOC_NONE, 0x7, 0x3, 0), 0x60) = 0          # I2C_SLAVE, address 0x60
ioctl(6, _IOC(_IOC_NONE, 0x7, 0x20, 0), ...) = -1 ENXIO   # I2C_SMBUS -- refused
```

Two things compounding:

1. **The DIMMs NACK about 1–3% of SMBus writes** (`ENXIO`) because the module's
   RGB controller is busy. All four slots equally, at random. **OpenRGB does not
   retry**, so that stick keeps its previous colour until the next frame it
   actually receives.
2. **The daemon was skipping duplicate frames.** When consecutive frames rounded
   to the same 8-bit colour, it didn't resend. At the trough of the breath the
   colour sits on `rgb(0,0,0)` for *12 frames* — 400 ms at 30 fps. A stick that
   NACKed its "go dark" write stayed lit for that whole time while the other
   three were off.

**Fix:** send every frame, even duplicates. A NACK is then healed on the next
frame.

| | worst-case stale colour |
|---|---|
| skipping duplicates | **400 ms** |
| sending every frame (measured over 2+ full breaths) | **45 ms** |

Cost: 0.3% of one core and essentially no extra bus traffic, because the colour
already changed on most frames.

**The general lesson**, which applies to any RGB bus: *"skip the write if nothing
changed" is only safe on a transport that doesn't drop writes.* The USB path to
the Kraken is reliable, so Kraken Unleashed does skip identical LED frames. SMBus
to a DIMM is not, so rgb-sync must not.

Host-side skew between the four sticks, for the record, is about **1.3 ms** — one
block write each, ~0.36 ms apiece. The host was never the phase problem.

---

## 5. OpenRGB gotchas worth knowing

Collected the hard way.

**`set_color()` can knock a device out of Direct mode.** In openrgb-python it
branches on the *client's cached* mode, which lags the server after
`set_mode()`. While the cache still says a mode-colour mode, it sends a mode
change instead of LED data — flipping the device straight back out of Direct.
Use the private `_set_device_color(color, fast=True)` to always send LED colours.
(This is why the dependency is pinned to `openrgb-python==0.3.7`.)

**Protocol version 4 hangs on a headless server.** Its plugin-list request is
never answered by `openrgb --server`, stalling every connect for 10 seconds.
Connect with `protocol_version=3`.

**`--save-profile` silently does nothing** unless you also pass full device, mode
and colour arguments. On its own it aborts with an unrelated-looking error about
the number of colours. Saving also *merges* into an existing profile file, so
delete the file first after a layout change.

**Devices lose state over suspend, and OpenRGB's write caches don't know.** The
Kingston driver in particular caches register writes. A fresh server after resume
is more reliable than trying to re-initialise in place — hence an
`openrgb-resume.service` that does `systemctl try-restart openrgb`.

**Re-assert Direct mode periodically.** A profile load, or the GUI being opened,
can change modes underneath a streaming daemon.

**The Effects plugin only loads in the GUI**, and doesn't re-apply Direct after a
reconnect — which is why a small daemon beats it for an always-on setup. Don't
run both; they fight.

---

## 6. Motherboard-specific note: ASUS ROG Maximus Z890

Not required for Kraken Unleashed, recorded because it cost a day.

On this board OpenRGB 1.0 mis-maps the Aura channels. The board reports 0 onboard
LEDs and 1 12V header; stock OpenRGB drops the header, which shifts every
addressable effect channel by one. On this board the direct channel index equals
the effect channel index (top strip = 0, GPU strip = 2), and channel 0 is
exposed as a resizable addressable zone despite the config table calling it a 12V
header.

Fixing it needs a patch to `AsusAuraMainboardController.cpp` and a local build.
Installing an official OpenRGB package will overwrite it.
