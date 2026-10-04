#!/usr/bin/env python3
"""Generate the bundled demo background GIF.

The shape matters more than the prettiness: a bright animated rim with a dark
centre, because Kraken Unleashed lightens the sensor readout over this image.
Anything busy or pale in the middle third will fight the numbers for attention.

Run from the repo root:  python3 tools/make-demo-gif.py assets/demo.gif
"""
import sys

import numpy as np
from PIL import Image

# 480 is upscaled to the panel's 640 at load time; on smooth plasma that is
# invisible and it keeps the bundled asset to a few hundred KB.
SIZE = 480
FRAMES = 18
DURATION = 90          # ms per frame -> 1.62s loop


def frame(phase):
    """One frame of a swirling rim, falling off to black in the middle."""
    y, x = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32)
    cx = cy = SIZE / 2.0
    dx, dy = x - cx, y - cy
    r = np.hypot(dx, dy) / (SIZE / 2.0)          # 0 at centre, 1 at edge midpoint
    a = np.arctan2(dy, dx)

    # Three drifting lobes around the rim, plus a slow overall shimmer.
    swirl = (np.sin(3 * a + phase * 2 * np.pi)
             + 0.6 * np.sin(5 * a - phase * 4 * np.pi)
             + 0.4 * np.sin(7 * a + phase * 6 * np.pi))
    swirl = (swirl + 2.0) / 4.0                  # -> roughly 0..1

    # Radial envelope: nothing inside 0.45, peak near the inscribed circle, and
    # a soft outer edge so the corners the panel can't show don't matter.
    inner = np.clip((r - 0.45) / 0.3, 0.0, 1.0) ** 1.6
    outer = np.clip((1.12 - r) / 0.25, 0.0, 1.0)
    envelope = inner * outer

    intensity = np.clip(envelope * (0.35 + 0.65 * swirl), 0.0, 1.0)

    # Green plasma with a cyan push at the hot end; stays clear of the reds the
    # renderer uses for warnings.
    rgb = np.empty((SIZE, SIZE, 3), dtype=np.float32)
    rgb[..., 0] = intensity ** 2.6 * 90          # a little red only at the peaks
    rgb[..., 1] = intensity * 235
    rgb[..., 2] = intensity ** 1.7 * 150
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), 'RGB')


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else 'assets/demo.gif'
    frames = [frame(i / FRAMES) for i in range(FRAMES)]
    # One shared adaptive palette, so the loop doesn't flicker between frames.
    palette = frames[0].quantize(colors=32, method=Image.MEDIANCUT)
    # No dithering: the noise it adds costs more in LZW than the slight banding
    # it removes, and the banding is invisible once this is dimmed under the text.
    frames = [f.quantize(colors=32, palette=palette, dither=Image.NONE)
              for f in frames]
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=DURATION, loop=0, optimize=True)
    print(f'wrote {out}: {len(frames)} frames, {SIZE}x{SIZE}')


if __name__ == '__main__':
    main()
