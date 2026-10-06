"""The Kraken's protocol. Platform details live in transport.py.

Protocol notes are in docs/PROTOCOL.md. The short version, per LCD frame:

  1. HID  36 01 00 01 08            -> device replies 37 01 ...
  2. BULK 12 fa 01 e8 ab cd ef 98 76 54 32 10 | 08 00 00 00 | uint32 LE size
  3. BULK q565 payload

Flow control is not optional. Streaming without it put this cooler into its
bootloader, which needs the PSU switched off at the wall to clear. Wait for each
ack, pace the frames, stop after repeated refusals, and never release the
interface mid-transfer.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

import struct
import time

from . import cooling
from .transport import (BootloaderMode, DeviceError, NotFound,  # noqa: F401
                        VID, PID, BOOTLOADER_PID, open_transport)

W = H = 640
MAGIC = bytes([0x12, 0xFA, 0x01, 0xE8, 0xAB, 0xCD, 0xEF, 0x98, 0x76, 0x54, 0x32, 0x10])
MAX_PAYLOAD = 1_200_000          # refuse absurd frames rather than flood the device
MAX_CONSECUTIVE_FAILURES = 3


class KrakenLCD:
    #: (name, channel, group) for the pump ring and the radiator fans.
    LED_CHANNELS = (('ring', 0x01, 0x01), ('fans', 0x02, 0x02))
    LEDS_PER_CHANNEL = 24

    def __init__(self, hidraw=None):
        self.io = open_transport(hidraw)

    # -- plumbing ----------------------------------------------------------- #

    def drain(self):
        """Discard unread reports so the next read is the one we asked for."""
        self.io.hid_drain()

    def prepare(self, timeout=0.3):
        """Announce a frame and wait for the device's 37 01 go-ahead."""
        self.io.hid_write(bytes([0x00, 0x36, 0x01, 0x00, 0x01, 0x08] + [0] * 59))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            report = self.io.hid_read(64, max(0, deadline - time.monotonic()))
            if report is None:
                break
            if len(report) >= 2 and report[0] == 0x37 and report[1] == 0x01:
                return True
        return False

    def send(self, payload):
        """One LCD frame. True only if the whole frame reached the device."""
        if len(payload) > MAX_PAYLOAD:
            return False
        self.drain()
        if not self.prepare():
            return False
        self.io.bulk_write(MAGIC + bytes([0x08, 0, 0, 0])
                           + struct.pack('<I', len(payload)), timeout=2000)
        self.io.bulk_write(payload, timeout=8000)
        return True

    # -- LEDs --------------------------------------------------------------- #

    def set_zone(self, channel, group, colours):
        """Write one channel's 24 LEDs.

        *colours* is a list of RGB tuples; it is padded or truncated to fit.
        The cooler takes **GRB** order -- verified the hard way, by sending RGB
        green and watching the ring light up red. This is the only place that
        conversion happens.
        """
        body = bytearray()
        for i in range(self.LEDS_PER_CHANNEL):
            r, g, b = colours[i] if i < len(colours) else (0, 0, 0)
            body += bytes((int(g) & 0xFF, int(r) & 0xFF, int(b) & 0xFF))
        packet = bytes([0x00, 0x26, 0x14, channel, group]) + bytes(body)
        self.io.hid_write(packet + bytes(max(0, 513 - len(packet))))

    def set_leds(self, ring=None, fans=None):
        """Set either zone. Pass None to leave that zone untouched."""
        for name, channel, group in self.LED_CHANNELS:
            colours = ring if name == 'ring' else fans
            if colours is not None:
                self.set_zone(channel, group, colours)

    def set_uniform(self, rgb, ring=True, fans=True):
        """Convenience: one colour across the selected zones."""
        block = [tuple(rgb)] * self.LEDS_PER_CHANNEL
        self.set_leds(ring=block if ring else None,
                      fans=block if fans else None)

    # -- cooling -------------------------------------------------------------- #

    def set_speed_profile(self, channel, points):
        """Upload a liquid-temperature curve for 'pump' or 'fan'.

        The firmware runs it from then on, so this is sent once on a change
        rather than every loop -- and it keeps working if this daemon stops.
        """
        if channel not in cooling.CHANNELS:
            raise ValueError(f'unknown speed channel {channel!r}')
        self.io.hid_write(cooling.packet(channel, points))
        return cooling.duties(channel, points)

    # -- status ------------------------------------------------------------- #

    def status(self):
        """Liquid temperature and pump/fan RPM, straight from the cooler."""
        self.drain()
        self.io.hid_write(bytes([0x00, 0x74, 0x01] + [0] * 61))
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            report = self.io.hid_read(64, max(0, deadline - time.monotonic()))
            if report is None:
                break
            if len(report) >= 25 and report[0] == 0x75 and report[1] == 0x01:
                return {'liquid': report[15] + report[16] / 10,
                        'pump_rpm': report[18] << 8 | report[17],
                        'fan_rpm': report[24] << 8 | report[23]}
        return None

    def close(self):
        self.io.close()
