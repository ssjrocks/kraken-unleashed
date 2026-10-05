# How it works

## Device ownership

The single most important design rule: **exactly one process holds the cooler.**

The Kraken exposes a hidraw node (commands, status, LEDs) and USB bulk endpoint
`0x02` (frame payloads). Both must be held by the same process, because the
hidraw node is shared state — liquidctl reads *whatever report arrives next*, so
a command issued by another program lands in its reply.

```
   the app ───┐                    ┌──────────────────────────┐
   the CLI ───┼── control socket ─▶│  kraken-unleashed-daemon │
   OpenRGB ───┘      E1.31         │                          │
                                   │   sole owner of:         │
   CoolerControl ──X───────────────│   hidraw  +  bulk 0x02   │
   liquidctl     ──X───────────────│                          │
   OpenRGB direct──X───────────────└────────────┬─────────────┘
                                                │
                                                ▼
                                       Kraken 1e71:3012
                                                │
                              /run/kraken-unleashed/status.json
                              /run/kraken-lcd/status.json  (1.x path, kept)
                                                │
                              ┌─────────────────┴────────────┐
                              ▼                              ▼
                      Home Assistant exporter       status bars, conky, ...
```

Note which arrows are blocked. OpenRGB cannot write to the device — but it
*can* send E1.31 to the daemon, which relays it. The rule is never "OpenRGB is
banned", it is "exactly one process holds the device".

Because this service is the only thing reading the cooler, it publishes what it
reads so nothing else has to open the device:

```json
{"liquid": 31.4, "pump_rpm": 2280, "fan_rpm": 780, "ts": 1759553280.1,
 "led_source": "effect"}
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

The daemon runs one loop. At 12 fps, every ~83 ms:

1. **Poll the cooler** (once a second, not every frame) for liquid temp and RPMs;
   publish to `status.json`.
2. **Read host sensors** from a background thread's latest snapshot.
2b. **Work out the LED colours** — from E1.31 if OpenRGB is sending, otherwise
   from the effect engine.
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
without the other and you get a visible rectangle. `compositor.py` imports `_BG`
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

`kraken-unleashed-resume.service` restarts the stream after suspend, because USB
re-enumerates on wake and the old handles are stale.

## The control socket

Everything that is not the daemon talks to it over a socket rather than opening
the device: the app, the CLI, and anything you write. Newline-delimited JSON,
one request per line.

On Linux it is a Unix socket at `/run/kraken-unleashed/control.sock`, owned by
root and restricted to a group the desktop user is in. On Windows it is a
loopback TCP port guarded by a token file, because Windows Python's `AF_UNIX`
support is patchy and binding to localhost is not an access control on its own —
every process on the machine can reach it.

Commands: `status`, `get_config`, `set_config`, `reload`, `effects`, `styles`,
`preview`, `ping`. `preview` renders a frame to a PNG without going near the
cooler, which is what makes the app's live preview free.

---

## Repository layout

```
src/kraken_unleashed/      the package
    daemon.py              the main loop and the control-socket handlers
    device.py              the Kraken's protocol
    transport.py           platform I/O: hidraw+libusb, or hidapi+WinUSB
    compat.py              everything that differs between Linux and Windows
    q565.py                the frame encoder
    compositor.py          sensor readout over the background
    effects.py             the LED effect engine
    sacn.py                the E1.31 receiver (the OpenRGB relay)
    control.py             the control server and its client
    config.py              defaults, loading, migration, atomic saving
    sensors.py             host CPU/GPU readings
    gui.py                 the GTK4 app
src/bin/                   the three entry points
src/ok/                    vendored OpenKraken renderer (MIT) + its licence
systemd/                   the service and the resume unit
packaging/                 desktop entry, icon, Windows spec and scripts
assets/                    demo GIF + screenshots for the docs
tools/                     make-demo-gif.py, gui-shot
extras/rgb-sync/           optional companion daemon for everything else
docs/                      this documentation
```

`src/kraken_unleashed/` and `src/ok/` are copied to `/opt/kraken-unleashed/`, and
`src/bin/*` to `/usr/bin/`. Everything else is repo-only.

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
