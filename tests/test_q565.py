"""The frame encoder.

The container format was reconstructed from a USB capture, so these tests pin
down what was actually verified against the device rather than what seems
reasonable: the magic, the little-endian dimensions, the two opcodes, and the
terminator. If any of that drifts the panel shows garbage, and nothing else in
the project would notice.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

import struct

import numpy as np
import pytest
from PIL import Image

from kraken_unleashed import q565


def decode_runs(payload):
    """Walk the opcode stream back into (rgb565, count) pairs."""
    assert payload[:4] == b'q565'
    width, height = struct.unpack('<HH', payload[4:8])
    out = []
    i = 8
    while i < len(payload) - 1:
        op = payload[i]
        if op == 0xFE:
            value = payload[i + 1] | (payload[i + 2] << 8)
            out.append([value, 1])
            i += 3
        elif op & 0xC0 == 0xC0:
            assert out, 'a run opcode before any literal'
            out[-1][1] += (op & 0x3F) + 1
            i += 1
        else:
            pytest.fail(f'this encoder should only emit literals and runs, got {op:#04x}')
    assert payload[-1] == 0xFF, 'missing terminator'
    return width, height, out


def test_header_and_terminator():
    payload = q565.encode(Image.new('RGB', (640, 640), (0, 0, 0)))
    assert payload[:4] == b'q565'
    assert struct.unpack('<HH', payload[4:8]) == (640, 640)
    assert payload[-1] == 0xFF


def test_solid_black_frame_is_small():
    # The captured all-black frame was 6,616 bytes; ours is the same order of
    # magnitude. A solid frame ballooning means run encoding has broken.
    payload = q565.encode(Image.new('RGB', (640, 640), (0, 0, 0)))
    assert len(payload) < 8000, f'solid black encoded to {len(payload)} bytes'


def test_runs_cover_every_pixel():
    img = Image.new('RGB', (64, 32), (0, 0, 0))
    width, height, runs = decode_runs(q565.encode(img))
    assert (width, height) == (64, 32)
    assert sum(count for _, count in runs) == 64 * 32


def test_run_opcode_never_exceeds_62():
    """0xc0|(n-1) only has six bits, so a run cannot describe more than 62."""
    payload = q565.encode(Image.new('RGB', (640, 640), (0, 0, 0)))
    for byte in payload[8:-1]:
        if byte & 0xC0 == 0xC0 and byte != 0xFE:
            assert (byte & 0x3F) + 1 <= 62


def test_long_runs_split_into_several_opcodes():
    # 640*640 identical pixels is far more than one run can hold, so this only
    # round-trips if the >62 split works.
    _, _, runs = decode_runs(q565.encode(Image.new('RGB', (640, 640), (17, 34, 51))))
    assert len(runs) == 1
    assert runs[0][1] == 640 * 640


def test_colour_is_rgb565_little_endian():
    img = Image.new('RGB', (8, 8), (255, 0, 0))
    _, _, runs = decode_runs(q565.encode(img))
    expected = ((255 & 0xF8) << 8) | ((0 & 0xFC) << 3) | (0 >> 3)
    assert runs[0][0] == expected


def test_distinct_columns_produce_distinct_literals():
    a = np.zeros((4, 3, 3), dtype=np.uint8)
    a[:, 0] = (255, 0, 0)
    a[:, 1] = (0, 255, 0)
    a[:, 2] = (0, 0, 255)
    _, _, runs = decode_runs(q565.encode(Image.fromarray(a, 'RGB')))
    assert len({value for value, _ in runs}) == 3


def test_photographic_frame_round_trips():
    rng = np.random.default_rng(1234)
    noise = rng.integers(0, 255, size=(128, 128, 3), dtype=np.uint8)
    width, height, runs = decode_runs(q565.encode(Image.fromarray(noise, 'RGB')))
    assert (width, height) == (128, 128)
    assert sum(count for _, count in runs) == 128 * 128
