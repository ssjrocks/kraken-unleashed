"""Which coolers this knows about, and how much of that is actually verified.

The protocol work in this project was done on one cooler: the Kraken 2024 Elite
(1e71:3012). Everything else here comes from liquidctl's kraken3 driver, which
lists the whole family, plus the assumption that models sharing a speed-channel
layout share the rest. That assumption is untested, and the table says so rather
than pretending otherwise.

Running on an untested model is not dangerous. The LCD path waits for the
device's `37 01` acknowledgement before sending a frame, so a cooler that does
not speak the q565 streaming protocol simply refuses and the daemon stops --
which is exactly what the flow control is for.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

VID = 0x1E71
BOOTLOADER_PID = 0x3011

#: Speed-channel layouts, from liquidctl: name -> (channel id, min, max duty).
_SPEED_KRAKEN_Z = {
    'pump': ((0x01, 0x00, 0x00), 20, 100),
    'fan': ((0x02, 0x00, 0x00), 0, 100),
}
_SPEED_KRAKEN_2023 = {
    'pump': ((0x01, 0x01, 0x00), 20, 100),
    'fan': ((0x02, 0x01, 0x01), 0, 100),
}


class Model:
    """One supported cooler."""

    def __init__(self, pid, name, lcd, speed, tested=False, leds=True):
        self.pid = pid
        self.name = name
        self.lcd = lcd                  # (w, h), or None for no screen
        self.speed = speed
        #: True only where the protocol was verified against real hardware.
        self.tested = tested
        #: Whether the `26 14` lighting command is expected to apply.
        self.leds = leds

    @property
    def usb_id(self):
        return f'{VID:04x}:{self.pid:04x}'

    def __repr__(self):
        return f'<Model {self.usb_id} {self.name}>'


MODELS = {
    m.pid: m for m in (
        # The one this project was built and verified on.
        Model(0x3012, 'Kraken 2024 Elite RGB', (640, 640), _SPEED_KRAKEN_2023,
              tested=True),

        # Same generation and speed-channel layout; the LCD is a different size.
        # Expected to work, never confirmed.
        Model(0x300C, 'Kraken 2023 Elite', (640, 640), _SPEED_KRAKEN_2023),
        Model(0x300E, 'Kraken 2023', (240, 240), _SPEED_KRAKEN_2023),
        Model(0x3014, 'Kraken 2024 Plus', (240, 240), _SPEED_KRAKEN_2023),

        # Older generation. Different speed channels, and whether its firmware
        # speaks the q565 streaming path at all is unknown -- it may only
        # support the slow bucket upload, in which case the LCD will refuse
        # frames and the daemon will stop.
        Model(0x3008, 'Kraken Z53/Z63/Z73', (320, 320), _SPEED_KRAKEN_Z),
    )
}

#: The model assumed when nothing is attached, so --preview and the tests work.
DEFAULT = MODELS[0x3012]


def get(pid):
    return MODELS.get(pid)


def supported_ids():
    return sorted(m.usb_id for m in MODELS.values())


def describe_support():
    """Lines for `diagnose`, so people can see where their cooler stands."""
    lines = []
    for pid in sorted(MODELS):
        model = MODELS[pid]
        mark = 'verified' if model.tested else 'untested'
        size = f'{model.lcd[0]}x{model.lcd[1]}' if model.lcd else 'no screen'
        lines.append(f'  {model.usb_id}  {model.name:24s} {size:9s} {mark}')
    return lines
