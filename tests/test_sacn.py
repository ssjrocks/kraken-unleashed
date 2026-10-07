"""The E1.31 receiver that lets OpenRGB drive the cooler's lighting.

Byte offsets are the whole game here: DMX data starts at 126 and the universe is
a big-endian uint16 at 113. Get either wrong and OpenRGB appears to work while
sending colours nobody asked for.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

import socket
import struct
import time

import pytest

from kraken_unleashed import sacn


def build_packet(universe=1, channels=None, source=b'test'):
    """A spec-shaped E1.31 data packet (E1.31-2016)."""
    dmx = bytes(channels if channels is not None else [0] * 512)
    dmp = (struct.pack('>HBB HH H', 0x7000 | (10 + 1 + len(dmx)), 0x02, 0xA1,
                       0x0000, 0x0001, 1 + len(dmx)) + b'\x00' + dmx)
    framing = (struct.pack('>HI', 0x7000 | (77 + len(dmp)), 2)
               + source.ljust(64, b'\x00')
               + struct.pack('>BHBB H', 100, 0, 1, 0, universe))
    root = (struct.pack('>HH', 0x0010, 0x0000) + sacn.ACN_PID
            + struct.pack('>HI', 0x7000 | (22 + len(framing) + len(dmp)), 4)
            + b'\xaa' * 16)
    return root + framing + dmp


def test_parses_universe_and_data():
    universe, dmx = sacn.parse(build_packet(7, [255, 0, 0, 0, 255, 0] + [0] * 506))
    assert universe == 7
    assert list(dmx[:6]) == [255, 0, 0, 0, 255, 0]


def test_dmx_starts_at_the_documented_offset():
    assert sacn.DMX_DATA_OFFSET == 126
    assert sacn.UNIVERSE_OFFSET == 113


@pytest.mark.parametrize('mangle,why', [
    (lambda p: p[:50], 'too short'),
    (lambda p: p[:4] + b'XXXXXXXXXXXX' + p[16:], 'wrong ACN identifier'),
    (lambda p: p[:18] + struct.pack('>I', 9) + p[22:], 'wrong root vector'),
    (lambda p: p[:40] + struct.pack('>I', 9) + p[44:], 'wrong framing vector'),
    (lambda p: p[:125] + b'\xdd' + p[126:], 'not a DMX start code'),
])
def test_rejects_malformed_packets(mangle, why):
    assert sacn.parse(mangle(build_packet())) is None, why


def test_multicast_group_follows_the_spec():
    assert sacn.multicast_group(1) == '239.255.0.1'
    assert sacn.multicast_group(258) == '239.255.1.2'


def test_receiver_round_trip_over_loopback():
    receiver = sacn.Receiver(universe=7, port=45571, timeout=2.0)
    receiver.start()
    try:
        for _ in range(40):          # wait for the socket to be listening
            if receiver.sock is not None:
                break
            time.sleep(0.05)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.sendto(build_packet(7, [255, 0, 0, 0, 255, 0, 0, 0, 255] + [0] * 503),
                      ('127.0.0.1', 45571))
        for _ in range(40):
            if receiver.live:
                break
            time.sleep(0.05)
        assert receiver.live
        assert receiver.colors(3) == [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        assert receiver.stats()['packets'] == 1
    finally:
        receiver.stop()


def test_other_universes_are_ignored():
    receiver = sacn.Receiver(universe=7, port=45572, timeout=2.0)
    receiver.start()
    try:
        for _ in range(40):
            if receiver.sock is not None:
                break
            time.sleep(0.05)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.sendto(build_packet(9, [255] * 512), ('127.0.0.1', 45572))
        time.sleep(0.4)
        assert not receiver.live, 'a packet for another universe was accepted'
    finally:
        receiver.stop()


def test_colors_returns_none_when_nothing_recent():
    """This is what makes the daemon fall back to its own effect.

    Without it, closing OpenRGB would freeze the lighting on the last colour it
    happened to send.
    """
    receiver = sacn.Receiver(universe=1, port=45573, timeout=0.2)
    assert receiver.colors(48) is None


def test_start_channel_offsets_the_mapping():
    receiver = sacn.Receiver(universe=1, port=45574, start_channel=4)
    receiver._data = bytes([1, 2, 3, 10, 20, 30, 40, 50, 60])
    receiver._at = time.monotonic()
    assert receiver.colors(2) == [(10, 20, 30), (40, 50, 60)]
