"""LED effects for the Kraken's ring and radiator fans.

Every effect is a function ``(t, count, params) -> [(r, g, b), ...]`` of length
*count*. ``t`` is seconds from ``CLOCK_BOOTTIME``, never a per-process start
time: that clock is identical in every process on the machine, so an effect
computed here lands in exact phase with the same effect computed by rgb-sync
for the rest of your lighting, with nothing passing between them.

Colours are plain RGB here. The GRB byte order the cooler wants is applied once,
at the device layer, so nothing in this file has to think about it.
"""
import colorsys
import math

#: Effects pick their colours from this when ``params["color"]`` is absent.
DEFAULT_COLOR = '00FF00'


def _rgb(hex_colour):
    return tuple(int(hex_colour[i:i + 2], 16) for i in (0, 2, 4))


def _scale(colour, level):
    return tuple(max(0, min(255, round(c * level))) for c in colour)


def _hsv(h, s=1.0, v=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return round(r * 255), round(g * 255), round(b * 255)


def _param(params, key, default):
    value = params.get(key, default)
    return default if value is None else value


# --------------------------------------------------------------------------- #
# Effects
# --------------------------------------------------------------------------- #

def off(t, count, params):
    return [(0, 0, 0)] * count


def static(t, count, params):
    return [_rgb(_param(params, 'color', DEFAULT_COLOR))] * count


def breathing(t, count, params):
    """Raised cosine, gamma corrected. The default, and what rgb-sync runs."""
    period = max(0.1, _param(params, 'period', 5.0))
    lo = _param(params, 'min_brightness', 0.0)
    hi = _param(params, 'max_brightness', 1.0)
    gamma = _param(params, 'gamma', 2.2)
    phase = (1 - math.cos(2 * math.pi * t / period)) / 2
    level = (lo + (hi - lo) * phase) ** gamma
    return [_scale(_rgb(_param(params, 'color', DEFAULT_COLOR)), level)] * count


def pulse(t, count, params):
    """Sharper than breathing: a quick flash with a slow decay."""
    period = max(0.1, _param(params, 'period', 2.0))
    phase = (t % period) / period
    level = math.exp(-4.0 * phase)
    return [_scale(_rgb(_param(params, 'color', DEFAULT_COLOR)), level)] * count


def spectrum(t, count, params):
    """The whole ring cycles through hues together."""
    period = max(0.1, _param(params, 'period', 10.0))
    brightness = _param(params, 'max_brightness', 1.0)
    return [_hsv(t / period, 1.0, brightness)] * count


def rainbow(t, count, params):
    """A rainbow wrapped around the ring, rotating."""
    period = max(0.1, _param(params, 'period', 10.0))
    brightness = _param(params, 'max_brightness', 1.0)
    spread = _param(params, 'spread', 1.0)      # how many full rainbows fit
    return [_hsv(t / period + (i / count) * spread, 1.0, brightness)
            for i in range(count)]


def wave(t, count, params):
    """One colour, brightness travelling around the ring as a sine wave."""
    period = max(0.1, _param(params, 'period', 3.0))
    colour = _rgb(_param(params, 'color', DEFAULT_COLOR))
    lo = _param(params, 'min_brightness', 0.0)
    hi = _param(params, 'max_brightness', 1.0)
    spread = _param(params, 'spread', 1.0)
    out = []
    for i in range(count):
        phase = (1 - math.cos(2 * math.pi * (t / period - (i / count) * spread))) / 2
        out.append(_scale(colour, lo + (hi - lo) * phase))
    return out


def chase(t, count, params):
    """A lit comet running around the ring, with a fading tail."""
    period = max(0.1, _param(params, 'period', 2.0))
    colour = _rgb(_param(params, 'color', DEFAULT_COLOR))
    tail = max(1, int(_param(params, 'tail', 6)))
    head = (t / period) * count
    out = []
    for i in range(count):
        # Distance behind the head, wrapping around the ring.
        behind = (head - i) % count
        level = max(0.0, 1.0 - behind / tail) if behind < tail else 0.0
        out.append(_scale(colour, level))
    return out


def gradient(t, count, params):
    """A static blend between two colours across the ring."""
    a = _rgb(_param(params, 'color', DEFAULT_COLOR))
    b = _rgb(_param(params, 'color2', '0000FF'))
    out = []
    for i in range(count):
        f = i / max(1, count - 1)
        out.append(tuple(round(a[c] + (b[c] - a[c]) * f) for c in range(3)))
    return out


def temperature(t, count, params):
    """Colour follows the coolant temperature: cool blue -> warm red.

    ``params["value"]`` is injected by the daemon each frame; it is the liquid
    temperature in C, or None when the reading is unavailable (shown white).
    """
    value = params.get('value')
    if value is None:
        return [(120, 120, 120)] * count
    lo = _param(params, 'temp_min', 25.0)
    hi = _param(params, 'temp_max', 50.0)
    f = max(0.0, min(1.0, (value - lo) / max(0.1, hi - lo)))
    # Hue 0.58 (blue) down to 0.0 (red).
    brightness = _param(params, 'max_brightness', 1.0)
    return [_hsv(0.58 * (1.0 - f), 1.0, brightness)] * count


EFFECTS = {
    'off': off,
    'static': static,
    'breathing': breathing,
    'pulse': pulse,
    'spectrum': spectrum,
    'rainbow': rainbow,
    'wave': wave,
    'chase': chase,
    'gradient': gradient,
    'temperature': temperature,
}

#: Which knobs each effect actually reads, so a GUI can show only those.
PARAMS = {
    'off': (),
    'static': ('color',),
    'breathing': ('color', 'period', 'min_brightness', 'max_brightness', 'gamma'),
    'pulse': ('color', 'period'),
    'spectrum': ('period', 'max_brightness'),
    'rainbow': ('period', 'max_brightness', 'spread'),
    'wave': ('color', 'period', 'min_brightness', 'max_brightness', 'spread'),
    'chase': ('color', 'period', 'tail'),
    'gradient': ('color', 'color2'),
    'temperature': ('temp_min', 'temp_max', 'max_brightness'),
}


def render(name, t, count, params):
    """Render *name* at time *t*, falling back to breathing if it is unknown."""
    return EFFECTS.get(name, breathing)(t, count, params)
