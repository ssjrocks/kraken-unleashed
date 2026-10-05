"""E1.31 (sACN) receiver, so OpenRGB can drive the cooler's LEDs.

OpenRGB already ships an E1.31 device type. Pointing one at this daemon makes
the Kraken behave like any other OpenRGB device -- every effect, profile and SDK
call works -- without patching OpenRGB or writing a plugin. That matters:
OpenRGB plugins only load in the GUI, so a plugin would be useless to a service.

It also keeps the one-owner rule intact. OpenRGB never touches the cooler; it
sends DMX over the loopback and this daemon remains the only process holding the
device.

Packet layout (E1.31-2016): root layer 38 bytes, framing layer 77, DMP layer
header 10, then a start code byte, so DMX channel 1 is at offset 126. The
universe is a big-endian uint16 at offset 113.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

import socket
import struct
import threading
import time

ACN_PID = b'ASC-E1.17\x00\x00\x00'
VECTOR_ROOT_E131_DATA = 0x00000004
VECTOR_E131_DATA_PACKET = 0x00000002
DMX_DATA_OFFSET = 126
UNIVERSE_OFFSET = 113
DEFAULT_PORT = 5568


def multicast_group(universe):
    """The multicast address E1.31 assigns to *universe* (239.255.x.y)."""
    return f'239.255.{(universe >> 8) & 0xFF}.{universe & 0xFF}'


def parse(packet):
    """Return (universe, dmx_bytes) or None if this is not an sACN data packet."""
    if len(packet) < DMX_DATA_OFFSET:
        return None
    if packet[4:16] != ACN_PID:
        return None
    if struct.unpack('>I', packet[18:22])[0] != VECTOR_ROOT_E131_DATA:
        return None
    if struct.unpack('>I', packet[40:44])[0] != VECTOR_E131_DATA_PACKET:
        return None
    if packet[125] != 0x00:          # DMX start code; anything else is not level data
        return None
    universe = struct.unpack('>H', packet[UNIVERSE_OFFSET:UNIVERSE_OFFSET + 2])[0]
    return universe, packet[DMX_DATA_OFFSET:]


class Receiver(threading.Thread):
    """Listens for sACN and hands the newest frame to the LED loop.

    ``colors(count)`` returns the latest frame as RGB tuples, or None when
    nothing has arrived recently -- which is the signal for the daemon to fall
    back to its own effects, so closing OpenRGB doesn't leave the cooler frozen
    on whatever colour happened to be showing.
    """

    daemon = True

    def __init__(self, universe=1, start_channel=1, port=DEFAULT_PORT,
                 timeout=2.0, bind='0.0.0.0'):
        super().__init__(daemon=True)
        self.universe = int(universe)
        self.start_channel = max(1, int(start_channel))
        self.port = int(port)
        self.timeout = float(timeout)
        self.bind = bind
        self._lock = threading.Lock()
        self._data = b''
        self._at = 0.0
        self._packets = 0
        self._source = None
        self._error = None
        self.stop_requested = False
        self.sock = None

    # -- lifecycle ---------------------------------------------------------- #

    def _open(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.bind, self.port))
        # Unicast to this host works without this; joining the group as well
        # means a sender configured for multicast is also heard.
        try:
            mreq = struct.pack('4sl', socket.inet_aton(multicast_group(self.universe)),
                               socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError as exc:
            # Not fatal: unicast still works, and this fails on hosts with no
            # multicast-capable route.
            self._error = f'multicast join failed: {exc}'
        sock.settimeout(0.5)
        return sock

    def run(self):
        try:
            self.sock = self._open()
        except OSError as exc:
            self._error = f'cannot listen on {self.bind}:{self.port}: {exc}'
            return
        while not self.stop_requested:
            try:
                packet, addr = self.sock.recvfrom(1144)
            except socket.timeout:
                continue
            except OSError:
                break
            parsed = parse(packet)
            if not parsed:
                continue
            universe, dmx = parsed
            if universe != self.universe:
                continue
            with self._lock:
                self._data = dmx
                self._at = time.monotonic()
                self._packets += 1
                self._source = addr[0]
        try:
            self.sock.close()
        except OSError:
            pass

    def stop(self):
        self.stop_requested = True

    # -- reading ------------------------------------------------------------ #

    @property
    def live(self):
        with self._lock:
            return self._at > 0 and (time.monotonic() - self._at) < self.timeout

    def colors(self, count):
        """Latest frame as *count* RGB tuples, or None if nothing recent."""
        with self._lock:
            data, at = self._data, self._at
        if not at or (time.monotonic() - at) >= self.timeout:
            return None
        base = self.start_channel - 1          # channels are 1-based
        out = []
        for i in range(count):
            o = base + i * 3
            if o + 2 < len(data):
                out.append((data[o], data[o + 1], data[o + 2]))
            else:
                out.append((0, 0, 0))
        return out

    def stats(self):
        with self._lock:
            return {
                'listening': self.sock is not None and not self.stop_requested,
                'universe': self.universe,
                'start_channel': self.start_channel,
                'port': self.port,
                'packets': self._packets,
                'source': self._source,
                'live': self._at > 0 and (time.monotonic() - self._at) < self.timeout,
                'last_packet_age': (time.monotonic() - self._at) if self._at else None,
                'error': self._error,
            }
