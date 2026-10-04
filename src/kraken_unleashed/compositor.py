"""Builds each LCD frame: the sensor readout lightened over a dimmed background.

Three decisions here account for most of the difference between "works" and
"works at 7% of a core":

  * the background is dimmed once at load, not per frame
  * the sensor overlay is cached and only re-rendered when a displayed value
    changes -- the readout moves about once a second, not twelve times
  * the q565 encoder is vectorised (see q565.py)
"""
import os
import sys

from PIL import Image, ImageChops, ImageEnhance

def _root():
    """Directory holding the vendored renderer and assets/.

    Normally the parent of this package. Under PyInstaller the tree is unpacked
    somewhere temporary and sys._MEIPASS points at it, so resolving from
    __file__ would look in a directory that does not exist.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


HERE = _root()
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from ok.backend.lcd_render import render, LcdData, _BG, _RENDERERS  # noqa: E402

W = H = 640
STYLES = tuple(_RENDERERS)


def default_background():
    """The bundled demo, whether installed or run straight from a clone."""
    for candidate in (os.path.join(HERE, 'assets', 'demo.gif'),
                      os.path.join(os.path.dirname(HERE), 'assets', 'demo.gif')):
        if os.path.exists(candidate):
            return candidate
    return os.path.join(HERE, 'assets', 'demo.gif')     # for the error message


def load_background(path):
    """Every frame of *path* as 640x640 RGB, plus each frame's duration."""
    image = Image.open(path)
    frames, durations = [], []
    try:
        while True:
            frames.append(image.convert('RGB').resize((W, H)))
            # Browsers clamp absurdly short GIF delays; match that so a 0 ms
            # GIF doesn't spin at the frame rate.
            durations.append(max(image.info.get('duration', 50), 20) / 1000.0)
            image.seek(image.tell() + 1)
    except EOFError:
        pass
    if not frames:
        raise ValueError(f'{path}: no frames could be read')
    return frames, durations


class Compositor:
    def __init__(self, lcd_config, gif_path=None, cpu_vendor=None, gpu_vendor=None):
        self.style = lcd_config['style']
        self.ring = tuple(int(lcd_config['ring'][i:i + 2], 16) for i in (0, 2, 4))
        self.rotate = int(lcd_config['rotate'])
        self.path = gif_path or lcd_config.get('gif') or default_background()
        self.frames, self.durations = load_background(self.path)
        self.total = sum(self.durations)
        dim = float(lcd_config['dim'])
        if dim > 0:
            self.frames = [ImageEnhance.Brightness(f).enhance(1.0 - dim)
                           for f in self.frames]
        self.bg = Image.new('RGB', (W, H), _BG)
        self.cpu_vendor = cpu_vendor
        self.gpu_vendor = gpu_vendor
        self._key = None
        self._overlay = None

    def data(self, dev, sensors_snapshot):
        cpu_t, cpu_l, gpu_t, gpu_l = sensors_snapshot
        return LcdData(liquid_temp=dev.get('liquid'), cpu_temp=cpu_t, cpu_load=cpu_l,
                       gpu_temp=gpu_t, gpu_load=gpu_l, pump_rpm=dev.get('pump_rpm'),
                       fan_rpm=dev.get('fan_rpm'), cpu_vendor=self.cpu_vendor,
                       gpu_vendor=self.gpu_vendor, ring_color=self.ring)

    def overlay_for(self, data):
        """Cached sensor overlay: the readout minus the renderer's background.

        Subtracting the flat background leaves only the lit pixels, which is
        what makes `lighter` composite them over the GIF instead of pasting an
        opaque square. This is why _BG must match the renderer's real
        background colour -- change one without the other and a rectangle
        appears.
        """
        key = (round(data.liquid_temp or 0, 1), round(data.cpu_temp or 0),
               round(data.cpu_load or 0), round(data.gpu_temp or 0),
               round(data.gpu_load or 0), data.pump_rpm, data.fan_rpm)
        if key != self._key:
            self._overlay = ImageChops.subtract(render(self.style, data), self.bg)
            self._key = key
        return self._overlay

    def frame_at(self, elapsed):
        t = elapsed % self.total
        accumulated = 0.0
        for index, duration in enumerate(self.durations):
            accumulated += duration
            if t < accumulated:
                return self.frames[index]
        return self.frames[-1]

    def compose(self, elapsed, data, rotate=True):
        """The finished frame, rotated for the cooler's mounting by default."""
        image = ImageChops.lighter(self.frame_at(elapsed), self.overlay_for(data))
        return image.rotate(-self.rotate) if rotate else image
