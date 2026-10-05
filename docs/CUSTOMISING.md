# Customising the display and lighting

Three ways to change things, all doing the same thing underneath:

- **The app** — `kraken-unleashed`, or "Kraken Unleashed" in your applications
  menu. Everything here is in it, with a live preview.
- **The command line** — `kraken-unleashed-ctl`, for scripting.
- **The config file** — `/etc/kraken-unleashed.conf`.

The app and the CLI apply changes **immediately**, with no restart. Editing the
config file by hand needs `sudo systemctl restart kraken-unleashed`.

---

## Try before you commit

You don't have to change anything to see what a change would look like. The
service can render exactly what it would send and write it to a PNG, **without
touching the device**:

```bash
kraken-unleashed-ctl preview /tmp/lcd.png
xdg-open /tmp/lcd.png
```

The app's Display page shows this preview live and updates it as you move a
slider, which is the easiest way to work.

Without the service running at all — no root needed:

```bash
kraken-unleashed-daemon --preview /tmp/lcd.png
```

---

## The background

```bash
kraken-unleashed-ctl set lcd.gif=/home/you/Pictures/my-loop.gif
kraken-unleashed-ctl set lcd.gif=null      # back to the bundled demo
```

In the app: **Display → Background → Choose…**

Any animated GIF, or a still PNG/JPEG.

**The one rule that matters: the middle should be dark.** The sensor readout is
*lightened* over your background — bright pixels in the background win, and
anything busy or pale behind the numbers makes them unreadable. GIFs built
around a glowing rim, a dark vignette, or a black centre look fantastic. A
full-frame bright animation looks like a mess.

Other practical notes:

- **Square.** It is resized to 640 × 640 regardless, so anything else gets
  squashed. The corners are not visible — the panel is a circle.
- **Resolution.** 480 × 480 is plenty; it is upscaled and then dimmed.
- **Length.** Every frame is decoded and held in memory at startup. A 20-frame
  loop costs a few tens of MB; a 500-frame clip will hurt.
- **Frame delays** are honoured, clamped to a 20 ms minimum.

If a GIF looks too busy, raise `dim` before giving up on it.

## Brightness of the background

```bash
kraken-unleashed-ctl set lcd.dim=0.55
```

`0` leaves the background at full brightness, `1` makes it black. The default
0.4 suits most GIFs.

![dim comparison](../assets/screenshots/dim-comparison.png)

## Which numbers are shown

```bash
kraken-unleashed-ctl set lcd.style=cpu_gpu
kraken-unleashed-ctl styles          # list them
```

| `triple` | `liquid_ring` | `cpu_gpu` |
|:---:|:---:|:---:|
| ![](../assets/screenshots/triple.png) | ![](../assets/screenshots/liquid_ring.png) | ![](../assets/screenshots/cpu_gpu.png) |
| Coolant, CPU and GPU together, with pump and fan RPM | Big coolant temperature in an arc | CPU and GPU side by side |

Any sensor that is missing shows as `--` rather than disappearing, so a machine
with no NVIDIA GPU still gets a sensible screen.

## Getting it the right way up

```bash
kraken-unleashed-ctl set lcd.rotate=180
```

Depends entirely on how your cooler sits on the CPU. Valid values are `0`, `90`,
`180`, `270`. There is no clever way to detect it — try each and keep what looks
right. In the app it's a dropdown, which is quicker.

## Frame rate

```bash
kraken-unleashed-ctl set lcd.fps=8
```

12 is the practical ceiling for this panel and costs about 7% of one core. Lower
it if you would rather have the CPU back — 8 still looks smooth, 4 is visibly
steppy. Going above 12 does not help; the device starts refusing frames, and the
service stops rather than push through it.

## Turning the screen off but keeping the lights

```bash
kraken-unleashed-ctl set lcd.enabled=false
```

The cooler keeps whatever was last sent. Note the firmware takes the screen back
after a few seconds of silence and shows its own display — so this leaves the
NZXT screen, not a blank one.

## The arc colour

```bash
kraken-unleashed-ctl set lcd.ring=FF0066
```

Hex RRGGBB, used for the liquid-temperature arc. Only `liquid_ring` and
`cpu_gpu` draw one — the `triple` layout deliberately has no arc, so the
background GIF provides the rim instead.

The arc turns amber and then red on its own as the coolant warms past the warning
thresholds, whatever you set here.

---

## The cooler's lighting

The pump ring and the radiator fans are 24 LEDs each, driven as one 48-LED
strip so an effect travels across both.

### Built-in effects

```bash
kraken-unleashed-ctl effects                              # list them
kraken-unleashed-ctl set led.effect=chase led.params.color=FF0000
```

| Effect | What it does | Reads |
|---|---|---|
| `breathing` | raised cosine fade, gamma corrected | colour, period, min/max brightness, gamma |
| `static` | one fixed colour | colour |
| `pulse` | sharp flash with a slow decay | colour, period |
| `spectrum` | whole ring cycles through hues together | period, brightness |
| `rainbow` | rainbow wrapped around the ring, rotating | period, brightness, spread |
| `wave` | one colour, brightness travelling round as a sine | colour, period, brightness, spread |
| `chase` | a lit comet with a fading tail | colour, period, tail |
| `gradient` | static blend between two colours | colour, second colour |
| `temperature` | blue → red with the coolant temperature | min/max temperature, brightness |
| `off` | LEDs black | — |

The app shows only the knobs the chosen effect actually reads, so there are no
sliders that do nothing.

### Zones

```bash
kraken-unleashed-ctl set led.zones.ring=true led.zones.fans=false
```

Leave a zone off and the service doesn't write to it at all, so something else
could — though nothing else can reach this cooler, so in practice this just
freezes that zone on its last colour.

### Letting OpenRGB drive it instead

```bash
kraken-unleashed-ctl set openrgb.enabled=true led.source=openrgb
```

OpenRGB cannot talk to this cooler directly, but the service can accept E1.31
from it and relay it to the LEDs. Full setup — including the OpenRGB side — is in
[RGB.md](RGB.md). When OpenRGB stops sending, the built-in effect takes over
again rather than the lights freezing.

### Staying in step with the rest of your lighting

```jsonc
"follow": "/etc/rgb-sync.json"
```

If that file exists, its `color`, `period`, `min_brightness`, `max_brightness`
and `gamma` override the effect parameters. That is how the cooler stays in exact
step with [rgb-sync](RGB.md#rgb-sync) driving everything else — both read one
file, and both compute the breath from `CLOCK_BOOTTIME`, which every process on
the machine shares. No coordination, no drift. Set it to `null` if you aren't
using rgb-sync.

---

## The config file

`/etc/kraken-unleashed.conf` is JSON in sections. Whole lines starting with `//`
are stripped before parsing, but **the app rewrites the file without comments**
when you change something, so don't keep notes in there.

```jsonc
{
    "lcd": {
        "enabled": true,
        "gif": null,            // null = the bundled demo
        "style": "triple",      // triple | liquid_ring | cpu_gpu
        "dim": 0.4,             // 0 = bright background, 1 = black
        "ring": "7C3AED",
        "fps": 12,
        "rotate": 90            // 0 / 90 / 180 / 270
    },
    "led": {
        "enabled": true,
        "source": "effect",     // effect | openrgb | off
        "effect": "breathing",
        "params": { "color": "00FF00", "period": 5.0, "gamma": 2.2 },
        "follow": "/etc/rgb-sync.json",
        "zones": { "ring": true, "fans": true }
    },
    "openrgb": {
        "enabled": false,
        "universe": 1, "start_channel": 1, "port": 5568, "timeout": 2.0
    },
    "control": {
        "socket": null,         // null = this platform's default
        "group": "you"          // group allowed to talk to the service
    }
}
```

`kraken-unleashed-ctl set` takes dotted paths matching this structure, so
`led.params.color=00FF00` sets exactly what it looks like.

---

## Going further: editing the renderer

The screens are drawn by `/opt/kraken-unleashed/ok/backend/lcd_render.py`,
vendored from [OpenKraken](https://github.com/davidboulay/OpenKraken). It is
ordinary Pillow drawing code and it is meant to be edited.

### Colours

Near the top of that file:

```python
_BG       = (13, 14, 18)      # background — see the warning below
_ACCENT   = (124, 58, 237)    # NZXT purple
_TEXT     = (236, 237, 241)   # the big numbers
_TEXT_DIM = (139, 142, 152)   # labels like PUMP / FAN
_OK       = (52, 211, 153)    # below the warning threshold
_WARN     = (251, 191, 36)    # amber
_CRIT     = (239, 68, 68)     # red
_CPU      = (56, 189, 248)    # CPU accents
_GPU      = (52, 211, 153)    # GPU accents
```

> **Don't change `_BG`.** Compositing works by subtracting the renderer's
> background from the rendered screen, which leaves only the lit pixels to
> lighten over your GIF. If `_BG` is not the actual background colour of the
> rendered image, that subtraction leaves a visible rectangle. `compositor.py`
> imports `_BG` for exactly this purpose.

### Thresholds

```python
_LIQUID_MIN = 20.0      # arc empty
_LIQUID_MAX = 60.0      # arc full
```

### Fonts

The renderer looks for DejaVu Sans Bold, then Liberation Sans Bold, then Noto
Sans Bold, at the usual Debian and Arch paths, and falls back to a bitmap font if
none is found (which looks rough — install `fonts-dejavu-core` or equivalent).
To use something else, add its path to the top of `_FONT_CANDIDATES`.

### Writing your own screen

Add a function that takes an `LcdData` and returns a 640 × 640 `Image`, then
register it:

```python
def _render_mine(data: LcdData) -> Image.Image:
    img = Image.new("RGB", (CANVAS, CANVAS), _BG)
    draw = ImageDraw.Draw(img)
    draw.text(CENTER, f"{data.cpu_temp:.0f}°", font=_font(180),
              fill=_TEXT, anchor="mm")
    return img

_RENDERERS["mine"] = _render_mine
```

`LcdData` gives you `liquid_temp`, `cpu_temp`, `cpu_load`, `gpu_temp`,
`gpu_load`, `pump_rpm`, `fan_rpm`, `cpu_vendor`, `gpu_vendor` and `ring_color`.
**Any of them can be `None`** when a sensor is missing, so guard before
formatting.

Keep content within `SAFE_RADIUS` of `CENTER` — the panel is a circle and the
corners are simply not there.

Then `kraken-unleashed-ctl set lcd.style=mine`. Check it with a preview first; an
exception in a renderer takes the service down.

### Writing your own effect

`/opt/kraken-unleashed/kraken_unleashed/effects.py`. An effect is a function
`(t, count, params) -> [(r, g, b), ...]` of length `count`, registered in
`EFFECTS`, with the knobs it reads listed in `PARAMS` so the app shows the right
ones. `t` is seconds from the boot clock — use it rather than a per-process
start time, or your effect won't stay in phase with anything else.

> Edits inside `/opt/` are **overwritten by `install.sh`**. Make changes in a
> clone under `src/` and install from there so upgrades keep them.

---

## All settings at a glance

| Setting | CLI | Default | Meaning |
|---|---|---|---|
| `lcd.enabled` | `set lcd.enabled=` | `true` | draw the sensor screen |
| `lcd.gif` | `set lcd.gif=` | bundled demo | background image |
| `lcd.style` | `set lcd.style=` | `triple` | which readout |
| `lcd.dim` | `set lcd.dim=` | `0.4` | background dimming, 0–1 |
| `lcd.ring` | `set lcd.ring=` | `7C3AED` | liquid arc colour |
| `lcd.fps` | `set lcd.fps=` | `12` | frames per second |
| `lcd.rotate` | `set lcd.rotate=` | `90` | 0/90/180/270 |
| `led.enabled` | `set led.enabled=` | `true` | drive the cooler's LEDs |
| `led.source` | `set led.source=` | `effect` | `effect` / `openrgb` / `off` |
| `led.effect` | `set led.effect=` | `breathing` | which effect |
| `led.params.*` | `set led.params.color=` | — | effect knobs |
| `led.zones.*` | `set led.zones.ring=` | `true` | which zones to drive |
| `led.follow` | `set led.follow=` | `/etc/rgb-sync.json` | shared settings file |
| `openrgb.enabled` | `set openrgb.enabled=` | `false` | accept E1.31 |
| `openrgb.universe` | `set openrgb.universe=` | `1` | sACN universe |
| `openrgb.timeout` | `set openrgb.timeout=` | `2.0` | seconds before falling back |

Other CLI commands: `status`, `effects`, `styles`, `preview PATH`, `diagnose`.
