#!/usr/bin/env python3
"""Kraken Unleashed - stream live sensor frames to an NZXT Kraken 2024 LCD.

Composites a sensor readout over an animated GIF and streams the result to the
cooler's 640x640 LCD at up to ~12 fps, and optionally drives the cooler's own
ring and fan LEDs in the same breath as the rest of your lighting.

Protocol (reverse-engineered from a USBPcap capture of SignalRGB on Windows),
one frame:

  1. HID  36 01 00 01 08            -> device replies 37 01 ...
  2. BULK 12 fa 01 e8 ab cd ef 98 76 54 32 10 | 08 00 00 00 | uint32 LE size
  3. BULK q565 payload

No buckets, no bucket switch, no end command. See docs/PROTOCOL.md.

Payload: "q565" + uint16 LE w + uint16 LE h + opcodes + 0xff. This encoder emits
only RUN (0xc0|len-1, len<=62) and literal (0xfe + uint16 LE RGB565) -- the two
opcodes verified byte-for-byte against a captured all-black frame.

Flow control matters. Streaming without it put the cooler into its bootloader
(re-enumerating as 1e71:3011), which needs a full power cut to clear. So: wait
for each ack, pace the frames, stop after repeated refusals, and always finish
the frame in flight before releasing the interface. See docs/TROUBLESHOOTING.md.

Copyright (c) 2026 Kraken Unleashed contributors. MIT licensed.
The sensor-screen renderer in ok/ is vendored from OpenKraken (MIT).
"""
import argparse
import json
import math
import os
import select
import signal
import struct
import subprocess
import sys
import threading
import time

import numpy as np
import usb.core
import usb.util
from PIL import Image, ImageChops, ImageEnhance

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ok.backend.lcd_render import render, LcdData, _BG, _RENDERERS  # noqa: E402

VID, PID, BOOTLOADER_PID = 0x1E71, 0x3012, 0x3011
W = H = 640
MAGIC = bytes([0x12, 0xFA, 0x01, 0xE8, 0xAB, 0xCD, 0xEF, 0x98, 0x76, 0x54, 0x32, 0x10])
MAX_PAYLOAD = 1_200_000          # refuse absurd frames rather than flood the device
MAX_CONSECUTIVE_FAILURES = 3

CONFIG_PATH = os.environ.get('KRAKEN_LCD_CONFIG', '/etc/kraken-lcd.conf')
DEFAULT_GIF = os.path.join(HERE, 'assets', 'demo.gif')

DEFAULTS = {
    'gif': None,            # None -> the bundled assets/demo.gif
    'style': 'triple',      # triple | liquid_ring | cpu_gpu
    'dim': 0.4,             # 0 = full-brightness GIF, 1 = black
    'ring': '7C3AED',       # liquid arc colour (only drawn by liquid_ring/cpu_gpu)
    'fps': 12.0,
    'rotate': 90,           # match the orientation your cooler is mounted in
    'led': {
        'enabled': True,
        # If this file exists its colour/period/brightness/gamma win, so the
        # cooler breathes in lockstep with an rgb-sync install. Set to null to
        # ignore it and use the values below.
        'follow': '/etc/rgb-sync.json',
        'color': '00FF00',
        'period': 5.0,
        'min_brightness': 0.0,
        'max_brightness': 1.0,
        'gamma': 2.2,
    },
}

# This service owns the Kraken USB interface, so it is the single source of the
# cooler's readings. It publishes the latest {liquid, pump_rpm, fan_rpm} to a
# tmpfs file so other tools (Home Assistant exporters, status bars, conky...)
# can read them without opening the device. See docs/ARCHITECTURE.md.
STATUS_DIR = '/run/kraken-lcd'
STATUS_PATH = STATUS_DIR + '/status.json'


def deep_update(base, overlay):
    """Merge *overlay* into *base* recursively, returning *base*."""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_update(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path=CONFIG_PATH):
    """Read the JSON config, falling back to DEFAULTS for anything absent.

    A missing file is not an error: the defaults alone are a working setup.
    A malformed file is, though -- silently ignoring a typo in someone's config
    makes for a baffling afternoon.
    """
    config = json.loads(json.dumps(DEFAULTS))   # deep copy
    try:
        with open(path) as handle:
            # Strip // comment lines so the shipped config can explain itself.
            text = ''.join(line for line in handle
                           if not line.lstrip().startswith('//')).strip()
        if text:
            deep_update(config, json.loads(text))
    except FileNotFoundError:
        pass
    except ValueError as exc:
        sys.exit(f'{path}: not valid JSON ({exc})')
    return config


stop_requested = False


def _stop(signum, frame):
    global stop_requested
    stop_requested = True


def write_status(dev):
    """Best-effort publish of the latest device reading; never disturb the loop."""
    if not dev:
        return
    try:
        os.makedirs(STATUS_DIR, exist_ok=True)
        tmp = STATUS_PATH + '.tmp'
        with open(tmp, 'w') as handle:
            json.dump({'liquid': dev.get('liquid'), 'pump_rpm': dev.get('pump_rpm'),
                       'fan_rpm': dev.get('fan_rpm'), 'ts': time.time()}, handle)
        os.replace(tmp, STATUS_PATH)
    except Exception:
        pass


def encode_q565(img):
    """RUN + literal only. Vectorised: ~6 ms for a photographic 640x640 frame."""
    a = np.asarray(img.convert('RGB'), dtype=np.uint16)
    v = (((a[:, :, 0] & 0xF8) << 8) | ((a[:, :, 1] & 0xFC) << 3) | (a[:, :, 2] >> 3)).reshape(-1)
    starts = np.concatenate(([0], np.flatnonzero(v[1:] != v[:-1]) + 1))
    lengths = np.diff(np.concatenate((starts, [v.size])))
    rep = lengths - 1
    nrun = (rep + 61) // 62
    sizes = 3 + nrun
    offs = np.concatenate(([0], np.cumsum(sizes)[:-1]))
    out = np.zeros(int(sizes.sum()), dtype=np.uint8)
    px = v[starts]
    out[offs] = 0xFE
    out[offs + 1] = (px & 0xFF).astype(np.uint8)
    out[offs + 2] = (px >> 8).astype(np.uint8)
    one = np.flatnonzero(nrun == 1)
    out[offs[one] + 3] = 0xC0 | (rep[one] - 1).astype(np.uint8)
    # Runs longer than 62 px need several RUN opcodes; rare enough to loop over.
    for i in np.flatnonzero(nrun > 1).tolist():
        left = int(rep[i])
        o = int(offs[i]) + 3
        while left > 0:
            n = min(62, left)
            out[o] = 0xC0 | (n - 1)
            o += 1
            left -= n
    return b'q565' + struct.pack('<HH', img.width, img.height) + out.tobytes() + b'\xff'


class KrakenLCD:
    def __init__(self, hidraw=None):
        if usb.core.find(idVendor=VID, idProduct=BOOTLOADER_PID) is not None:
            sys.exit('Kraken is in BOOTLOADER mode (1e71:3011). It needs a full power '
                     'cut (shut down, switch the PSU off ~30s); refusing to touch it. '
                     'See docs/TROUBLESHOOTING.md.')
        self.dev = usb.core.find(idVendor=VID, idProduct=PID)
        if self.dev is None:
            sys.exit(f'Kraken {VID:04x}:{PID:04x} not found. Is it plugged into an '
                     'internal USB 2.0 header, and is this running as root?')
        self.fd = os.open(hidraw or self._find_hidraw(), os.O_RDWR)
        usb.util.claim_interface(self.dev, 0)
        self.claimed = True

    @staticmethod
    def _find_hidraw():
        for node in sorted(os.listdir('/sys/class/hidraw')):
            try:
                uevent = open(f'/sys/class/hidraw/{node}/device/uevent').read()
            except OSError:
                continue
            if f'{VID:04X}' in uevent.upper() and f'{PID:04X}' in uevent.upper():
                return f'/dev/{node}'
        sys.exit('no hidraw node for the Kraken')

    def drain(self):
        """Discard any unread reports so the next read is the one we asked for."""
        while True:
            ready, _, _ = select.select([self.fd], [], [], 0)
            if not ready:
                return
            try:
                os.read(self.fd, 64)
            except BlockingIOError:
                return

    def prepare(self, timeout=0.3):
        """Announce a frame and wait for the device's 37 01 go-ahead."""
        os.write(self.fd, bytes([0x00, 0x36, 0x01, 0x00, 0x01, 0x08] + [0] * 59))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.fd], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            report = os.read(self.fd, 64)
            if report[0] == 0x37 and report[1] == 0x01:
                return True
        return False

    def send(self, payload):
        """One frame. Returns True only if the whole frame reached the device."""
        if len(payload) > MAX_PAYLOAD:
            return False
        self.drain()
        if not self.prepare():
            return False
        self.dev.write(0x02, MAGIC + bytes([0x08, 0, 0, 0])
                       + struct.pack('<I', len(payload)), timeout=2000)
        self.dev.write(0x02, payload, timeout=8000)
        return True

    LED_CHANNELS = ((0x01, 0x01), (0x02, 0x02))   # (channel, group): ring, fans
    LEDS_PER_CHANNEL = 24

    def set_leds(self, rgb):
        """Set every LED on both channels to one colour.

        Command 26 14 <ch> <grp> + 24 RGB triplets, exactly as SignalRGB sends it
        every frame. OpenRGB's older Hue2 direct packets (0x22) are rejected by
        this firmware, which is why the Kraken is excluded from OpenRGB entirely.
        """
        # The Kraken takes GRB order (verified: sending RGB green lit the ring red).
        body = bytes((rgb[1], rgb[0], rgb[2])) * self.LEDS_PER_CHANNEL
        for channel, group in self.LED_CHANNELS:
            packet = bytes([0x00, 0x26, 0x14, channel, group]) + body
            os.write(self.fd, packet + bytes(max(0, 513 - len(packet))))

    def status(self):
        """Read liquid temp and pump/fan rpm straight from the cooler."""
        self.drain()
        os.write(self.fd, bytes([0x00, 0x74, 0x01] + [0] * 61))
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.fd], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            report = os.read(self.fd, 64)
            if report[0] == 0x75 and report[1] == 0x01:
                return {'liquid': report[15] + report[16] / 10,
                        'pump_rpm': report[18] << 8 | report[17],
                        'fan_rpm': report[24] << 8 | report[23]}
        return None

    def close(self):
        if getattr(self, 'claimed', False):
            usb.util.release_interface(self.dev, 0)
            self.claimed = False
        try:
            os.close(self.fd)
        except OSError:
            pass


def led_settings(config):
    """Resolve the LED breath settings, letting rgb-sync's config win if present.

    Sharing one file is what keeps the cooler in phase with an rgb-sync install
    without the two daemons talking to each other.
    """
    led = dict(config['led'])
    follow = led.get('follow')
    if follow:
        try:
            with open(follow) as handle:
                shared = json.load(handle)
            for key in ('color', 'period', 'min_brightness', 'max_brightness', 'gamma'):
                if key in shared:
                    led[key] = shared[key]
        except (OSError, ValueError):
            pass
    return led


def sync_colour(led):
    """The colour an rgb-sync breath is showing right now.

    CLOCK_BOOTTIME is shared by every process on the machine, so computing the
    same raised cosine from it keeps two independent daemons in exact phase with
    no coordination at all.
    """
    t = time.clock_gettime(time.CLOCK_BOOTTIME)
    phase = (1 - math.cos(2 * math.pi * t / led['period'])) / 2
    level = led['min_brightness'] + (led['max_brightness'] - led['min_brightness']) * phase
    level = level ** led['gamma']
    base = [int(led['color'][i:i + 2], 16) for i in (0, 2, 4)]
    return tuple(round(c * level) for c in base)


# --------------------------------------------------------------------------- #
# Host sensors
# --------------------------------------------------------------------------- #

# hwmon driver name -> whether its temp1_input is the package/die temperature.
_CPU_HWMON = ('coretemp', 'k10temp', 'zenpower')


def cpu_temp():
    """Package temperature from hwmon, Intel or AMD."""
    for node in os.listdir('/sys/class/hwmon'):
        path = f'/sys/class/hwmon/{node}'
        try:
            name = open(f'{path}/name').read().strip()
        except OSError:
            continue
        if name not in _CPU_HWMON:
            continue
        # Prefer an explicitly labelled package/die sensor; fall back to temp1.
        for entry in sorted(os.listdir(path)):
            if not entry.endswith('_label'):
                continue
            try:
                label = open(f'{path}/{entry}').read().strip().lower()
            except OSError:
                continue
            if any(k in label for k in ('package', 'tctl', 'tdie')):
                try:
                    return int(open(f'{path}/{entry[:-6]}_input').read()) / 1000
                except OSError:
                    pass
        try:
            return int(open(f'{path}/temp1_input').read()) / 1000
        except OSError:
            pass
    return None


def cpu_vendor():
    try:
        info = open('/proc/cpuinfo').read()
        return 'intel' if 'GenuineIntel' in info else ('amd' if 'AuthenticAMD' in info else None)
    except OSError:
        return None


def _amd_gpu_hwmon():
    """Path of the first amdgpu hwmon directory, or None."""
    for node in os.listdir('/sys/class/hwmon'):
        path = f'/sys/class/hwmon/{node}'
        try:
            if open(f'{path}/name').read().strip() == 'amdgpu':
                return path
        except OSError:
            pass
    return None


def detect_gpu():
    """Return ('nvidia'|'amd'|None, reader) where reader() -> (temp, load)."""
    if subprocess.run(['which', 'nvidia-smi'], capture_output=True).returncode == 0:
        def read_nvidia():
            try:
                out = subprocess.run(
                    ['nvidia-smi', '--query-gpu=temperature.gpu,utilization.gpu',
                     '--format=csv,noheader,nounits'],
                    capture_output=True, text=True, timeout=5).stdout.strip()
                if out:
                    temp, load = out.splitlines()[0].split(',')
                    return float(temp), float(load)
            except Exception:
                pass
            return None, None
        return 'nvidia', read_nvidia

    hwmon = _amd_gpu_hwmon()
    if hwmon:
        # busy_percent lives on the parent DRM device, not in hwmon itself.
        busy = os.path.join(os.path.realpath(hwmon + '/device'), 'gpu_busy_percent')

        def read_amd():
            temp = load = None
            try:
                temp = int(open(hwmon + '/temp1_input').read()) / 1000
            except OSError:
                pass
            try:
                load = float(open(busy).read().strip())
            except OSError:
                pass
            return temp, load
        return 'amd', read_amd

    return None, lambda: (None, None)


class Sensors(threading.Thread):
    """Poll host sensors off the frame loop; nvidia-smi is far too slow inline."""

    daemon = True

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.cpu_temp = self.cpu_load = self.gpu_temp = self.gpu_load = None
        self.gpu_vendor, self._read_gpu = detect_gpu()
        self._prev = self._read_stat()
        self.stop = False

    @staticmethod
    def _read_stat():
        try:
            fields = open('/proc/stat').readline().split()[1:]
            values = [int(x) for x in fields]
            return sum(values), values[3] + values[4]      # total, idle+iowait
        except (OSError, IndexError, ValueError):
            return None

    def run(self):
        while not self.stop:
            temp = cpu_temp()
            load = None
            current = self._read_stat()
            if current and self._prev:
                d_total = current[0] - self._prev[0]
                d_idle = current[1] - self._prev[1]
                if d_total > 0:
                    load = max(0.0, min(100.0, 100.0 * (d_total - d_idle) / d_total))
            self._prev = current
            gpu_temp, gpu_load = self._read_gpu()
            with self.lock:
                self.cpu_temp, self.cpu_load = temp, load
                self.gpu_temp, self.gpu_load = gpu_temp, gpu_load
            time.sleep(1.0)

    def snapshot(self):
        with self.lock:
            return self.cpu_temp, self.cpu_load, self.gpu_temp, self.gpu_load


# --------------------------------------------------------------------------- #
# Frame composition
# --------------------------------------------------------------------------- #

def load_gif(path):
    """Every frame of *path* as 640x640 RGB, plus each frame's duration."""
    image = Image.open(path)
    frames, durations = [], []
    try:
        while True:
            frames.append(image.convert('RGB').resize((W, H)))
            # Browsers clamp absurdly short GIF delays; match that so a 0ms GIF
            # doesn't spin at the frame rate.
            durations.append(max(image.info.get('duration', 50), 20) / 1000.0)
            image.seek(image.tell() + 1)
    except EOFError:
        pass
    if not frames:
        sys.exit(f'{path}: no frames could be read')
    return frames, durations


class Compositor:
    """Builds each frame: sensor readout lightened over the dimmed GIF."""

    def __init__(self, config, gif_path):
        self.style = config['style']
        self.ring = tuple(int(config['ring'][i:i + 2], 16) for i in (0, 2, 4))
        self.frames, self.durations = load_gif(gif_path)
        self.total = sum(self.durations)
        # The background frames never change, so dim them once up front rather
        # than per frame -- this is the single biggest CPU saving in the loop.
        dim = config['dim']
        if dim > 0:
            self.frames = [ImageEnhance.Brightness(f).enhance(1.0 - dim) for f in self.frames]
        self.bg = Image.new('RGB', (W, H), _BG)
        self.cpu_vendor = cpu_vendor()
        self._key = None
        self._overlay = None

    def overlay_for(self, data):
        """Cached sensor overlay: the readout minus the renderer's background.

        Subtracting the flat background turns the rendered screen into something
        that can be lightened over the GIF, so only the lit pixels show through.
        The readouts change about once a second, so re-rendering only when a
        displayed value actually changes is the difference between ~11% and ~4%
        of a core at 12 fps.
        """
        key = (round(data.liquid_temp or 0, 1), round(data.cpu_temp or 0),
               round(data.cpu_load or 0), round(data.gpu_temp or 0),
               round(data.gpu_load or 0), data.pump_rpm, data.fan_rpm)
        if key != self._key:
            self._overlay = ImageChops.subtract(render(self.style, data), self.bg)
            self._key = key
        return self._overlay

    def frame_at(self, elapsed):
        """The GIF frame showing at *elapsed* seconds into playback."""
        t = elapsed % self.total
        accumulated = 0.0
        for index, duration in enumerate(self.durations):
            accumulated += duration
            if t < accumulated:
                return self.frames[index]
        return self.frames[-1]

    def compose(self, elapsed, data):
        return ImageChops.lighter(self.frame_at(elapsed), self.overlay_for(data))


def build_data(compositor, dev, sensors):
    cpu_t, cpu_l, gpu_t, gpu_l = sensors.snapshot()
    return LcdData(liquid_temp=dev.get('liquid'), cpu_temp=cpu_t, cpu_load=cpu_l,
                   gpu_temp=gpu_t, gpu_load=gpu_l, pump_rpm=dev.get('pump_rpm'),
                   fan_rpm=dev.get('fan_rpm'), cpu_vendor=compositor.cpu_vendor,
                   gpu_vendor=sensors.gpu_vendor, ring_color=compositor.ring)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog='kraken_lcd.py',
        description='Stream a sensor screen over a GIF to an NZXT Kraken 2024 LCD.',
        epilog='Any option left out falls back to %s, then to the built-in '
               'defaults. See docs/CUSTOMISING.md.' % CONFIG_PATH)
    parser.add_argument('--config', default=CONFIG_PATH, help='config file (JSON)')
    parser.add_argument('--gif', help='animated or still background image')
    parser.add_argument('--style', choices=tuple(_RENDERERS), help='sensor screen layout')
    parser.add_argument('--dim', type=float, help='0 = full-brightness GIF, 1 = black')
    parser.add_argument('--ring', help='liquid arc colour, hex RRGGBB')
    parser.add_argument('--fps', type=float, help='target frame rate')
    parser.add_argument('--rotate', type=int, help='0/90/180/270 to match your mounting')
    parser.add_argument('--no-leds', action='store_true',
                        help="don't drive the cooler's own ring/fan LEDs")
    parser.add_argument('--seconds', type=float, default=0.0,
                        help='stop after this long (0 = run until stopped)')
    parser.add_argument('--preview', metavar='PATH',
                        help='write one composited frame to PATH and exit, '
                             'without opening the device')
    parser.add_argument('--list-styles', action='store_true', help='print styles and exit')
    return parser.parse_args(argv)


def resolve(args):
    """CLI options override the config file, which overrides the defaults."""
    config = load_config(args.config)
    for key in ('gif', 'style', 'dim', 'ring', 'fps', 'rotate'):
        value = getattr(args, key)
        if value is not None:
            config[key] = value
    if args.no_leds:
        config['led']['enabled'] = False
    if not config['gif']:
        config['gif'] = DEFAULT_GIF
    config['gif'] = os.path.expanduser(config['gif'])
    if not os.path.exists(config['gif']):
        sys.exit(f"background image not found: {config['gif']}")
    return config


def do_preview(config, path):
    """Render one frame with placeholder sensor values -- no device needed."""
    compositor = Compositor(config, config['gif'])
    data = LcdData(liquid_temp=31.4, cpu_temp=45.0, cpu_load=12.0,
                   gpu_temp=38.0, gpu_load=3.0, pump_rpm=2280, fan_rpm=780,
                   cpu_vendor=compositor.cpu_vendor, gpu_vendor=detect_gpu()[0],
                   ring_color=compositor.ring)
    image = compositor.compose(0.0, data).rotate(-config['rotate'])
    image.save(path)
    payload = encode_q565(image)
    print(f'wrote {path} ({config["style"]}, rotate {config["rotate"]}, '
          f'dim {config["dim"]}); q565 payload would be {len(payload) // 1024} KB')


def main():
    args = parse_args()
    if args.list_styles:
        for name in _RENDERERS:
            print(name)
        return
    config = resolve(args)

    if args.preview:
        do_preview(config, args.preview)
        return

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    compositor = Compositor(config, config['gif'])
    led = led_settings(config)
    leds_on = config['led']['enabled']
    last_led = None

    sensors = Sensors()
    sensors.start()
    kraken = KrakenLCD()
    dev = kraken.status() or {}
    print(f'device ok: {dev or "no status report"}', flush=True)
    write_status(dev)

    sent = fails = consecutive = 0
    sizes = []
    exit_code = 0
    interval = 1.0 / config['fps']
    started = time.monotonic()
    last_status = 0.0
    try:
        while not stop_requested:
            now = time.monotonic()
            if args.seconds and now - started >= args.seconds:
                break
            if now - last_status >= 1.0:
                fresh = kraken.status()
                if fresh:
                    dev = fresh
                    write_status(dev)
                last_status = now

            if leds_on:
                colour = sync_colour(led)
                # 8-bit steps repeat near the extremes of the breath; skipping
                # identical frames here is safe because the cooler is on USB,
                # not a bus that drops writes (contrast rgb-sync and the DIMMs).
                if colour != last_led:
                    kraken.set_leds(colour)
                    last_led = colour

            frame = compositor.compose(now - started, build_data(compositor, dev, sensors))
            payload = encode_q565(frame.rotate(-config['rotate']))
            sizes.append(len(payload))
            if kraken.send(payload):
                sent += 1
                consecutive = 0
            else:
                fails += 1
                consecutive += 1
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    print(f'device refused {consecutive} frames in a row - stopping',
                          flush=True)
                    exit_code = 1
                    break
            slack = interval - (time.monotonic() - now)
            if slack > 0:
                time.sleep(slack)
    except usb.core.USBError as exc:
        print(f'USB error, stopping: {exc}', flush=True)
        exit_code = 1
    finally:
        elapsed = time.monotonic() - started
        sensors.stop = True
        kraken.close()
        print(f'{sent} frames in {elapsed:.1f}s = {sent / max(elapsed, 0.01):.1f} fps, '
              f'{fails} refused, payload avg '
              f'{sum(sizes) // max(1, len(sizes)) // 1024} KB', flush=True)
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
