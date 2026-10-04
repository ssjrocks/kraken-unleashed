# The NZXT Kraken 2024 Elite LCD protocol

Everything here was established on a **Kraken Elite 360 RGB 2024 V2**,
USB `1e71:3012`, firmware **1.2.0**, by a mixture of reading
[liquidctl](https://github.com/liquidctl/liquidctl)'s KrakenZ3 driver, capturing
SignalRGB's USB traffic on Windows with USBPcap, and testing against the device.

Where something is **verified** it was reproduced on hardware. Where it is
**inferred** or **unknown**, it says so. Nothing here is from NZXT.

---

## 1. The device

| | |
|---|---|
| Normal mode | `1e71:3012` — "NZXT Kraken Elite V2" |
| Bootloader mode | `1e71:3011` — "NZXT BOOTarea" |
| Panel | 640 × 640 circular IPS (the corners of a square frame are not visible) |
| Interfaces | HID (control + status) and USB bulk endpoint `0x02` (image data) |

Two things share the device: a **hidraw node** for commands and status, and
**bulk endpoint 0x02** for frame payloads. Both must be held by the same process.

The inscribed circle is what you actually see. The renderer keeps content inside
a radius of 320 px minus a 24 px safe margin for this reason.

---

## 2. Two ways to put an image on the screen

This is the central finding. The firmware supports both; which one you use
decides whether you get a slideshow or a video.

### 2.1 Bucket path — what every Linux tool uses

Used by liquidctl, CoolerControl and OpenKraken. The image is written into a
numbered memory "bucket" and the display is then switched to that bucket.

```
delete bucket      32 02 <idx>
setup bucket       32 01 <idx> <asset idx> <start kib16> <len kib16> 01
start write        36 01 <idx>
bulk header        12 fa 01 e8 ab cd ef 98 76 54 32 10 | 02 00 00 00 | <uint32 LE len>
bulk payload       raw RGBX, 4 bytes per pixel, 640*640*4 = 1,638,400 bytes
end write          36 02
switch to bucket   38 01 04 <idx>
```

The payload is **uncompressed RGBX** and must be rotated host-side to match the
cooler's mounting.

**Measured ceiling: ~2.4 fps.** 253 ms to push 1.6 MB over the bulk endpoint,
plus ~157 ms for the panel to redraw. That is the whole reason this project
exists.

Display modes seen with `38 01 <mode>`: `2` is the firmware's own liquid-temp
screen, `4` is "show bucket". Modes 1, 3 and 5–9 were refused.

### 2.2 q565 streaming path — what SignalRGB and NZXT CAM use

No buckets, no switch, no end command. Three steps per frame:

```
1. HID   36 01 00 01 08                         -> device replies 37 01 ...
2. BULK  12 fa 01 e8 ab cd ef 98 76 54 32 10
         08 00 00 00
         <uint32 LE payload length>              (20 bytes total)
3. BULK  <q565 payload>
```

**Measured: 12 fps at ~230–390 KB per frame**, about 7% of one core including
compositing and encoding.

Note the `08` in both the HID command and the bulk header, where the bucket path
uses `02`. SignalRGB's own published plugin source says `09` — that is wrong, or
at least is not what the shipping binary sends. The capture is authoritative.

---

## 3. The q565 payload format

A variant of [QOI](https://qoiformat.org/) carrying RGB565 pixels instead of
RGBA8888. Verified against a complete captured all-black frame (6,616 bytes).

```
"q565"                 4 bytes, ASCII magic
width                  uint16 LE
height                 uint16 LE
<opcodes>              see below
0xff                   terminator
```

### Opcodes

| Byte | Meaning | Status |
|---|---|---|
| `0xFE` + uint16 LE | literal RGB565 pixel | **verified** |
| `0xC0 \| (n-1)`, n ≤ 62 | repeat the previous pixel n more times | **verified** |
| `0xFF` | end of stream | **verified** |
| `0x00`–`0xBF` | QOI-style INDEX / DIFF / LUMA | **not solved** |

RGB565 is packed the usual way and stored **little-endian**:

```
value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
```

The `0xC0` run opcode caps at 62, not QOI's 62-with-bias-quirks; runs longer than
that are simply emitted as consecutive RUN opcodes.

### The opcodes that are not solved

`0x00`–`0xBF` certainly encode QOI's INDEX, DIFF and LUMA forms, and captured
frames are full of them. Decoding them with standard QOI semantics produces
speckle, so at least one of the hash function, the bias constants or the channel
order differs. **This was never cracked, and it does not matter for writing to
the device** — an encoder that emits only literals and runs produces a stream
the firmware renders perfectly.

It would matter if you wanted to *decode* captured frames. If you want to try:
the capture in the original work was truncated to 65,535 bytes per URB except
for the all-black frames, so a fresh capture with `-s 1048576` is step one.

### What it costs

| Content | Encoded size |
|---|---|
| Solid black | 6.6 KB |
| The bundled demo GIF with a readout | ~230 KB |
| A photographic frame | ~390 KB |

Encoding is ~6 ms for a 640×640 frame with the vectorised NumPy encoder in
`encode_q565()`. A naive per-pixel Python loop takes about two seconds, which is
why that function looks the way it does.

---

## 4. Flow control, and the way this device breaks

**This is the most important section in this document.**

Streaming frames at the device without waiting for its acknowledgement put the
cooler into its bootloader. It re-enumerated as `1e71:3011 "NZXT BOOTarea"` and
showed a white screen with a spinning rectangle that broke in two and rejoined.

Recovering from that needs a **complete power cut**: shut down, switch the PSU
off at the wall, wait ~30 seconds. **A reboot is not enough** — the device stays
powered by standby rails.

The pump keeps running on firmware defaults throughout, so the CPU is not in
danger, but the screen is unusable until the power cycle.

The rules the shipped code follows, and that any modification must keep:

1. **Wait for the `37 01` acknowledgement** after `36 01 00 01 08`, with a
   timeout. No ack, no frame.
2. **Pace the frames.** 12 fps. Do not send the next frame before the panel has
   had the previous one.
3. **Never release the USB interface mid-transfer.** Handle SIGTERM by finishing
   the frame in flight, then releasing. `KillSignal=SIGTERM` with a 15 s
   `TimeoutStopSec` in the unit file exists for this.
4. **Stop after repeated refusals.** Three consecutive failures and the process
   exits non-zero; systemd gives up after 3 restarts in 10 minutes rather than
   hammering a device that is already unhappy.
5. **Refuse absurd payloads** rather than flooding the endpoint — there is a
   1.2 MB cap in the code.
6. **Check for `1e71:3011` on startup** and refuse to touch the device if it is
   in the bootloader.

See also liquidctl's [PR #927](https://github.com/liquidctl/liquidctl/pull/927),
which deals with the same fragility on the bucket path.

---

## 5. Status reports

Ask for a status report over HID:

```
write:  74 01
reply:  75 01 ...
```

Byte offsets in the reply (verified against CoolerControl's readings):

| Offset | Meaning |
|---|---|
| 15 | liquid temperature, whole degrees C |
| 16 | liquid temperature, tenths |
| 17–18 | pump RPM, uint16 LE |
| 23–24 | fan RPM, uint16 LE |

Reports arrive unsolicited too, so drain the node before reading a reply you
asked for — otherwise you get an older report. liquidctl reads the next report
blindly, which is exactly why two programs polling this device at once corrupt
each other's readings.

---

## 6. LEDs

The cooler's ring and radiator fans are driven over the **same hidraw node**, not
through any RGB controller:

```
26 14 <channel> <group> | 24 × GRB triplets        padded to 513 bytes
```

| Channel | Group | Covers |
|---|---|---|
| `01` | `01` | pump ring |
| `02` | `02` | radiator fans |

Two things catch people out:

- **The byte order is GRB, not RGB.** Verified the hard way: sending RGB green
  lit the ring red.
- **OpenRGB's Hue2 direct packets (`0x22`) are rejected by this firmware.** Worse,
  the rejection comes back as an `ff 01` report on the hidraw node that
  liquidctl is blindly reading, corrupting whatever status poll is in flight.
  This is why the Kraken should be removed from OpenRGB entirely rather than
  just left alone. See [RGB.md](RGB.md).

`26 14` is what SignalRGB sends every frame, and it works.

---

## 7. How this was captured

On Windows, with [USBPcap](https://desowin.org/usbpcap/):

1. Identify the Kraken's root hub and device address in USBPcapCMD.
2. Capture while SignalRGB runs with a GIF and a stats overlay on the LCD.
3. Use a large snap length — `-s 1048576`. The default truncates every frame to
   65,535 bytes, which is enough to see the headers and the start of a payload
   but not to decode a whole frame.

The pcap linktype is 249 (`USBPCAP_BUFFER_PACKET_HEADER`). Bulk OUT transfers on
endpoint `0x02` carry the frames; the HID traffic carries `36 01`, `37 01`,
`74 01`/`75 01` and the `26 14` LED writes.

Useful detail: the all-black frames that appear when the screen blanks are short
enough to survive truncation intact, which is how the container format and the
RUN/literal opcodes were pinned down exactly.

---

## 8. Still unknown

- INDEX / DIFF / LUMA opcode semantics (`0x00`–`0xBF`).
- Whether the panel does anything with `width`/`height` other than full-frame —
  a partial-rectangle update would cut bandwidth a lot, and the header has the
  fields for it.
- What the other bytes of the `75 01` status report mean.
- Whether the `08` in the header selects the codec, and what other values do.
  `02` is raw RGBX on the bucket path; `08` is q565 streaming. Nothing else was
  tried, deliberately — see section 4.
