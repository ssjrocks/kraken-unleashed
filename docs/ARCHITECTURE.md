# How it works

## Device ownership

The single most important design rule: **exactly one process holds the cooler.**

The Kraken exposes a hidraw node (commands, status, LEDs) and USB bulk endpoint
`0x02` (frame payloads). Both must be held by the same process, because the
hidraw node is shared state — liquidctl reads *whatever report arrives next*, so
a command issued by another program lands in its reply.

```
                      ┌──────────────────────────┐
  CoolerControl ──X──▶│                          │
  OpenRGB       ──X──▶│   Kraken 1e71:3012       │
  liquidctl     ──X──▶│                          │
                      │   hidraw  +  bulk 0x02   │
  kraken-lcd ────────▶│                          │
                      └────────────┬─────────────┘
                                   │
                        /run/kraken-lcd/status.json
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
            Home Assistant exporter        status bars, conky, ...
```

Because this service is the only thing reading the cooler, it publishes what it
reads so nothing else has to open the device:

```json
{"liquid": 31.4, "pump_rpm": 2280, "fan_rpm": 780, "ts": 1759553280.1}
```

`/run` is tmpfs, written atomically via rename, roughly once a second. Treat `ts`
as a staleness check — if it stops advancing, the service is down.

## Fan and pump control

**Kraken Unleashed does not set pump or fan speeds.** It only reads them.

The cooler runs whatever curve is stored in its own firmware, which is whatever
the last tool to configure it left behind. That is safe — the pump does not stop
when the host stops talking to it — but it does mean nothing on the host is
adapting it any more once you disable the device in CoolerControl.

If you want host-side control back you have two options: re-enable the device in
CoolerControl and accept that it also wants the LCD (it will fight), or add
curve-setting to this service. Neither is implemented here.

---

## The frame loop

At 12 fps, every ~83 ms:

1. **Poll the cooler** (once a second, not every frame) for liquid temp and RPMs;
   publish to `status.json`.
2. **Read host sensors** from a background thread's latest snapshot.
3. **Render the sensor overlay** — but only if a displayed value changed.
4. **Pick the GIF frame** for the current playback position.
5. **Composite**, rotate, encode to q565.
6. **Send**: wait for the device's ack, then two bulk writes.
7. **Sleep** for whatever is left of the frame interval.

### Compositing

The readout is laid over the background with a *lighten* operation, so only lit
pixels show through:

```python
overlay   = ImageChops.subtract(rendered_screen, flat_background)
composite = ImageChops.lighter(dimmed_gif_frame, overlay)
```

Subtracting the renderer's own background colour (`_BG`) turns the rendered
screen into something that is black everywhere except where there is content.
Without that step you would paste an opaque square over the GIF.

This is also why `_BG` must match the renderer's actual background — change one
without the other and you get a visible rectangle. `kraken_lcd.py` imports `_BG`
from the renderer for exactly this reason.

### Where the CPU went

Three decisions account for most of the difference between "works" and "works at
7% of a core":

| Decision | Why |
|---|---|
| **Dim the GIF once at startup**, not per frame | The background never changes. Doing it per frame was the single largest cost. |
| **Cache the sensor overlay**, keyed on displayed values | The readout changes about once a second, not 12 times. This alone is ~11% → ~4%. |
| **Vectorised NumPy q565 encoder** | ~6 ms per frame. A per-pixel Python loop takes about two seconds. |

The encoder finds runs with `np.flatnonzero(v[1:] != v[:-1])`, computes every
opcode's output offset with a cumulative sum, and writes the whole buffer in a
handful of array operations. Only runs longer than 62 pixels — which need
multiple RUN opcodes — fall back to a Python loop, and they are rare.

### Sensors off the hot path

`nvidia-smi` takes tens of milliseconds to start. Calling it inline would blow
the frame budget on its own, so a daemon thread polls once a second and the frame
loop reads its last snapshot under a lock.

CPU temperature comes from `hwmon` (`coretemp`, `k10temp` or `zenpower`,
preferring a sensor labelled package/Tctl/Tdie). GPU is `nvidia-smi` if present,
otherwise the `amdgpu` hwmon node plus `gpu_busy_percent`. Anything unavailable
is `None` and renders as `--`.

---

## Resilience

The service is deliberately *reluctant*, because the failure mode is expensive —
see [PROTOCOL.md § 4](PROTOCOL.md#4-flow-control-and-the-way-this-device-breaks).

| Mechanism | Purpose |
|---|---|
| Wait for the `37 01` ack before every frame | Never push at a device that isn't ready |
| Stop after 3 consecutive refusals, exit non-zero | Don't grind against a sick device |
| `StartLimitIntervalSec=600`, `StartLimitBurst=3` | systemd gives up rather than restart-looping |
| `RestartSec=30` | Slow retries, not fast ones |
| `KillSignal=SIGTERM`, `TimeoutStopSec=15` | Finish the frame in flight before releasing the interface |
| Bootloader check at startup | Refuse to touch a device in recovery mode |
| 1.2 MB payload cap | A bug in a renderer can't flood the endpoint |

`kraken-lcd-resume.service` restarts the stream after suspend, because USB
re-enumerates on wake and the old handles are stale.

---

## Repository layout

```
src/kraken_lcd.py          the program
src/ok/                    vendored OpenKraken renderer (MIT) + its licence
src/assets/demo.gif        default background, installed alongside the program
systemd/                   the two unit files
config/kraken-lcd.conf     the default config
assets/                    demo GIF source + screenshots for the docs
tools/make-demo-gif.py     regenerates the demo background
extras/rgb-sync/           optional companion daemon for everything else
docs/                      this documentation
```

`src/` is what gets copied to `/opt/kraken-lcd/`. Everything else is repo-only.

## The vendored renderer

`src/ok/` is taken verbatim from
[OpenKraken](https://github.com/davidboulay/OpenKraken) (MIT, and it stays MIT —
see [NOTICE](../NOTICE)) so the screens match it exactly, with three deliberate
changes recorded in `src/ok/README`:

- the `triple` screen's liquid arc and track removed — the background GIF
  provides the rim instead
- vendor badges enlarged (size 20 → 30)
- the Intel badge blue brightened, `(0,113,197)` → `(0,160,255)`

It is vendored rather than depended on because OpenKraken is a whole application
with its own engine and device handling; only the drawing code is wanted here.
The upstream licence is kept at `src/ok/LICENSE-OpenKraken`.
