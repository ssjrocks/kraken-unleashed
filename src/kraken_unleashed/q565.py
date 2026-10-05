"""The q565 encoder: a QOI variant carrying RGB565, which the panel accepts.

Container: "q565" + uint16 LE width + uint16 LE height + opcodes + 0xff.
This emits only the two opcodes verified byte-for-byte against a captured
all-black frame -- RUN (0xc0 | len-1, len <= 62) and literal (0xfe + uint16 LE
RGB565). The INDEX/DIFF/LUMA range exists but was never decoded; it is not
needed to write to the device. See docs/PROTOCOL.md.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

import struct

import numpy as np


def encode(img):
    """RUN + literal only. Vectorised: ~6 ms for a photographic 640x640 frame."""
    a = np.asarray(img.convert('RGB'), dtype=np.uint16)
    v = (((a[:, :, 0] & 0xF8) << 8)
         | ((a[:, :, 1] & 0xFC) << 3)
         | (a[:, :, 2] >> 3)).reshape(-1)
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
    # Runs over 62 px need several RUN opcodes; rare enough to loop over.
    for i in np.flatnonzero(nrun > 1).tolist():
        left = int(rep[i])
        o = int(offs[i]) + 3
        while left > 0:
            n = min(62, left)
            out[o] = 0xC0 | (n - 1)
            o += 1
            left -= n
    return b'q565' + struct.pack('<HH', img.width, img.height) + out.tobytes() + b'\xff'
