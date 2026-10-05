# rgb-sync

Streams one shared breathing curve to every OpenRGB device, so a dozen
controllers actually breathe together instead of drifting apart on their own
clocks.

This is the companion to Kraken Unleashed: this daemon drives everything
*except* the cooler (RAM, GPU, motherboard headers, case fans), and Kraken
Unleashed drives the cooler's ring and fans. They stay in phase because both
compute the same curve from `CLOCK_BOOTTIME` and read their settings from the
same `/etc/rgb-sync.json`.

It is entirely optional. Kraken Unleashed works fine on its own.

## Install

```bash
sudo ./install.sh
```

Needs OpenRGB installed and `openrgb --server` running.

## Configure

`/etc/rgb-sync.json`:

```json
{
    "color": "00FF00",
    "period": 5.0,
    "min_brightness": 0.0,
    "max_brightness": 1.0,
    "exclude": ["Kraken"]
}
```

`exclude` is a list of case-insensitive substrings of device names to leave
alone. **Keep "Kraken" in it** — OpenRGB cannot drive that cooler directly, and
trying corrupts other software's status reads.

One wrinkle: if you turn on Kraken Unleashed's **E1.31 relay**, OpenRGB gains a
device called *Kraken Unleashed*, and the `"Kraken"` pattern matches that too, so
rgb-sync skips it. That is usually what you want — the cooler is already in phase
via the shared config and the boot clock. If you would rather rgb-sync drove the
cooler through OpenRGB as well, narrow the pattern, and pick one sync route
rather than running both. See [../../docs/RGB.md](../../docs/RGB.md).

```bash
sudo systemctl restart rgb-sync kraken-lcd
```

## Why it sends every frame, including duplicates

Because Kingston Fury DDR5 DIMMs NACK about 1-3% of SMBus writes and OpenRGB
does not retry. Skipping duplicate frames let a stick sit on a stale colour for
up to 400 ms at the trough of the breath, which looked exactly like one stick
lagging half a second behind the others. The full diagnosis, with the strace
commands, is in [../../docs/RGB.md](../../docs/RGB.md#4-the-kingston-fury-ddr5-desync-and-what-it-teaches).
