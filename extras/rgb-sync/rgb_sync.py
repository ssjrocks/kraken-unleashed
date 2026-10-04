#!/usr/bin/env python3
"""Synchronised breathing effect for OpenRGB devices.

Streams one shared brightness curve to every device on the local OpenRGB SDK
server, so everything breathes in phase (hardware breathing modes run on each
controller's own clock and drift apart).

Devices matching "exclude" are left alone. The NZXT Kraken must stay excluded:
its firmware rejects per-LED frames with an error report on the same HID
interface CoolerControl reads pump status from, so streaming to it would
corrupt CoolerControl's liquid temperature and pump/fan readings.

Settings live in /etc/rgb-sync.json; restart rgb-sync.service after editing.

Requires openrgb-python 0.3.7 exactly (pinned in /opt/rgb-sync/venv): it uses
Device._set_device_color, a private method, to send raw LED frames.
"""

import json
import math
import signal
import sys
import time

from openrgb import OpenRGBClient
from openrgb.utils import RGBColor

CONFIG_PATH = "/etc/rgb-sync.json"
DEFAULTS = {
    "color": "00FF00",          # hex RGB at full brightness
    "period": 5.0,              # seconds per breath
    "min_brightness": 0.0,      # 0..1, floor of the breath
    "max_brightness": 1.0,      # 0..1, peak of the breath
    "gamma": 2.2,               # perceptual correction so the fade looks even
    "fps": 30,
    "exclude": ["Kraken"],      # case-insensitive substrings of device names
    "check_seconds": 5,         # re-assert Direct if the server says a device left it
    "reassert_seconds": 60,     # re-send Direct regardless, in case hardware reset
}

running = True


def load_config():
    config = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            config.update(json.load(f))
    except FileNotFoundError:
        pass
    return config


def connect(retries=30):
    for attempt in range(retries):
        try:
            # Protocol 4 adds a plugin-list request that the headless 1.0
            # server never answers, stalling every connect for 10s.
            return OpenRGBClient("127.0.0.1", 6742, "rgb-sync", protocol_version=3)
        except (ConnectionRefusedError, TimeoutError, OSError):
            time.sleep(1)
    sys.exit("rgb-sync: OpenRGB server not reachable")


def set_direct(devices):
    for device in devices:
        device.set_mode("Direct")


def ensure_direct(devices):
    # Something else (a profile load, the OpenRGB GUI) may have changed modes.
    for device in devices:
        device.update()
        if device.modes[device.active_mode].name.lower() != "direct":
            print(f"rgb-sync: {device.name} left Direct mode, restoring", flush=True)
            device.set_mode("Direct")


def brightness(t, config):
    # Raised cosine: smooth in and out, one full breath per period.
    phase = (1 - math.cos(2 * math.pi * t / config["period"])) / 2
    level = config["min_brightness"] + (config["max_brightness"] - config["min_brightness"]) * phase
    return level ** config["gamma"]


def stop(signum, frame):
    global running
    running = False


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    config = load_config()
    base = [int(config["color"][i:i + 2], 16) for i in (0, 2, 4)]
    exclude = [name.lower() for name in config["exclude"]]

    client = connect()
    devices = [d for d in client.devices
               if not any(x in d.name.lower() for x in exclude)
               and any(m.name.lower() == "direct" for m in d.modes)]
    print("rgb-sync: driving " + ", ".join(d.name for d in devices), flush=True)

    set_direct(devices)
    last_check = last_reassert = time.monotonic()

    frame = 1.0 / config["fps"]
    # CLOCK_BOOTTIME is shared by every process on this machine, so kraken-lcd
    # (which drives the Kraken's own LEDs, since OpenRGB must not touch that
    # device) breathes in exact phase with these without any coordination.
    start = 0.0

    while running:
        now = time.monotonic()
        level = brightness(time.clock_gettime(time.CLOCK_BOOTTIME) - start, config)
        color = RGBColor(*(round(c * level) for c in base))

        # Send every frame, even when it rounds to the same 8-bit colour as the
        # last one. Skipping duplicates looks free but desynchronises the RAM:
        # ~1% of SMBus writes to the Fury DIMMs are NACKed (ENXIO) because the
        # module's controller is busy, and OpenRGB does not retry, so that DIMM
        # holds its previous colour until the next frame it actually receives.
        # With duplicates skipped the next frame can be 400ms away (the breath
        # sits on rgb(0,0,0) for 12 frames at the trough), leaving one stick
        # visibly lit while the other three are dark. Re-sending caps that at
        # one frame, and costs ~15% more SMBus traffic since the colour already
        # changes on most frames.
        for device in devices:
            # Not device.set_color(): it branches on the client's cached
            # mode, which lags the server after set_mode(). While the cache
            # still says a mode-colour mode (e.g. the RAM's Static), it
            # sends a mode change instead of LED colours, knocking the
            # device straight back out of Direct. Always send LED colours.
            device._set_device_color(color, fast=True)

        if now - last_reassert >= config["reassert_seconds"]:
            set_direct(devices)
            last_check = last_reassert = now
        elif now - last_check >= config["check_seconds"]:
            ensure_direct(devices)
            last_check = now

        time.sleep(max(0.0, frame - (time.monotonic() - now)))

    client.disconnect()


if __name__ == "__main__":
    main()
