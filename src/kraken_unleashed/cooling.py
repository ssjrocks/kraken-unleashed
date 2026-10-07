"""Pump and fan control.

The cooler runs the curve itself. We upload 40 duty values -- one per degree of
liquid temperature from 20 to 59 C -- and the firmware does the rest. That is
much better than driving duty from the host every second: if this daemon dies,
is killed, or the machine never gets that far in boot, the cooler keeps using
the last curve it was given instead of whatever the host last happened to say.

Wire format, confirmed against liquidctl's kraken3 driver, which lists this
device (1e71:3012) explicitly:

    0x72 | channel id (3 bytes) | 40 duty bytes

    pump   id 01 01 00    duty clamped to 20-100
    fan    id 02 01 01    duty clamped to  0-100

The 20% pump floor is the firmware's, not ours. 59 C is the critical
temperature: the curve ends there and the firmware takes over above it.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

CRITICAL_TEMPERATURE = 59
MIN_TEMPERATURE = 20
#: One duty value per degree, 20..59 inclusive -- 40 of them.
TEMPERATURES = list(range(MIN_TEMPERATURE, CRITICAL_TEMPERATURE + 1))

SET_COOLING = 0x72

#: name -> (channel id bytes, min duty, max duty)
CHANNELS = {
    'pump': ((0x01, 0x01, 0x00), 20, 100),
    'fan': ((0x02, 0x01, 0x01), 0, 100),
}

MODES = ('firmware', 'default', 'curve', 'full')

#: Which temperature the curve is read against.
#:
#: "liquid" is special, and better: the cooler's own curve is indexed by coolant
#: temperature, so a liquid curve is uploaded once and the firmware runs it --
#: it keeps working even if this daemon stops. The firmware cannot read host
#: sensors, so a cpu or gpu curve has to be evaluated here and pushed as a flat
#: duty whenever it changes. The (59 C, 100%) coolant failsafe still applies
#: underneath, so a stalled daemon cannot cook the loop, but it will hold
#: whatever duty it last sent until the coolant gets that hot.
SENSORS = ('liquid', 'cpu', 'gpu')

#: Sensible curve breakpoints per sensor. Coolant moves in a narrow band; CPU
#: and GPU temperatures do not, so the same 20-50 C points would be useless.
SENSOR_POINTS = {
    'liquid': (20, 30, 40, 50),
    'cpu': (40, 55, 70, 85),
    'gpu': (40, 55, 70, 85),
}

SENSOR_LABELS = {
    'liquid': 'Coolant temperature',
    'cpu': 'CPU temperature',
    'gpu': 'GPU temperature',
}

#: The "quiet default" preset.
#:
#: There is no factory-reset command in this protocol -- liquidctl's initialize()
#: only sets a status-polling interval, and NZXT CAM's "default" is a profile CAM
#: uploads, not something the cooler keeps in reserve. Once a curve is written,
#: the previous one is gone. So this is a preset, not a restoration.
#:
#: It is not invented, though: it is fitted to what a Kraken 2024 Elite was
#: measured doing on its shipped curve -- about 38% pump and 25% fan at 32.5 C
#: coolant -- then ramped to full before the critical temperature.
QUIET_CURVES = {
    'pump': [[20, 35], [35, 40], [45, 70], [50, 90]],
    'fan': [[20, 22], [35, 27], [45, 60], [50, 85]],
}

#: What a new custom curve starts from, if the user has not drawn one.
DEFAULT_CURVES = {
    'pump': [[20, 50], [30, 60], [40, 80], [50, 100]],
    'fan': [[20, 30], [30, 40], [40, 65], [50, 100]],
}


def clamp(value, low, high):
    return max(low, min(high, value))


def validate_points(points, what='curve'):
    """Raise ValueError unless *points* is a usable [(temp, duty), ...] list.

    Worth being strict here: an invalid curve that reaches the config file is
    re-applied on every start, so it fails quietly forever rather than once.
    """
    if isinstance(points, (str, bytes)):
        raise ValueError(
            f'{what} must be a list of [temperature, duty] pairs, not a string '
            f'({points!r}) -- quote it as JSON, e.g. [[20,40],[50,100]]')
    try:
        pairs = list(points)
    except TypeError:
        raise ValueError(f'{what} must be a list of [temperature, duty] pairs') from None
    if not pairs:
        raise ValueError(f'{what} needs at least one [temperature, duty] point')
    out = []
    for item in pairs:
        if isinstance(item, (str, bytes)) or not hasattr(item, '__len__') or len(item) != 2:
            raise ValueError(
                f'{what}: each point must be a [temperature, duty] pair, got {item!r}')
        try:
            temp, duty = int(item[0]), int(item[1])
        except (TypeError, ValueError):
            raise ValueError(
                f'{what}: temperature and duty must be numbers, got {item!r}') from None
        if not 0 <= temp <= 100:
            raise ValueError(f'{what}: temperature {temp} is outside 0-100 C')
        if not 0 <= duty <= 100:
            raise ValueError(f'{what}: duty {duty}% is outside 0-100%')
        out.append((temp, duty))
    return out


def validate(config):
    """Check a whole cooling config section. Returns nothing; raises on error."""
    mode = config.get('mode', 'firmware')
    if mode not in MODES:
        raise ValueError(f'cooling mode must be one of {", ".join(MODES)}, got {mode!r}')
    sensor = config.get('sensor', 'liquid')
    if sensor not in SENSORS:
        raise ValueError(
            f'cooling sensor must be one of {", ".join(SENSORS)}, got {sensor!r}')
    for channel in ('pump', 'fan'):
        if config.get(channel) is not None:
            validate_points(config[channel], f'cooling.{channel}')


def normalize(points, failsafe=True):
    """Sort, make monotonic, and (by default) enforce a (critical, 100%) point.

    Mirrors liquidctl's normalize_profile. The failsafe is the important part:
    whatever someone draws, the curve ends at 100% by the critical temperature,
    so a careless curve cannot leave the cooler idling while the loop boils.

    It is a *coolant* failsafe, though, so it must not be applied when reading a
    curve against a host sensor -- 59 C is a hot loop but an ordinary CPU, and
    injecting the point there would pin any CPU curve to 100% above 59 C.
    """
    extra = [(CRITICAL_TEMPERATURE, 100)] if failsafe else []
    pts = sorted(validate_points(points) + extra, key=lambda p: (p[0], -p[1]))
    mono = pts[:1]
    for (x, y), (xb, yb) in zip(pts[1:], pts[:-1]):
        if x == xb:
            continue
        mono.append((x, max(y, yb)))
    # Keep only the first point that reaches 100%; anything after it is noise.
    out = []
    for point in mono:
        out.append(point)
        if point[1] >= 100:
            break
    return out


def interpolate(points, temp):
    """Duty at *temp*, linearly between the surrounding points."""
    lower = points[0]
    for upper in points:
        if upper[0] > temp:
            if upper[0] == lower[0]:
                return upper[1]
            span = upper[0] - lower[0]
            return round(lower[1] + (temp - lower[0]) * (upper[1] - lower[1]) / span)
        lower = upper
    return points[-1][1]


def channel_limits(channel, channels=None):
    """(channel id, min duty, max duty), or a useful error for a bad name.

    *channels* lets a caller pass the attached model's layout; the older Kraken
    Z3 uses different channel ids from the 2023/2024 family.
    """
    table = channels or CHANNELS
    try:
        return table[channel]
    except KeyError:
        raise ValueError(
            f'unknown speed channel {channel!r}; expected one of '
            f'{", ".join(table)}') from None


def duties(channel, points, channels=None):
    """The 40 duty bytes for *channel* from a list of (temp, duty) points."""
    _, dmin, dmax = channel_limits(channel, channels)
    norm = normalize(points)
    return [clamp(interpolate(norm, t), dmin, dmax) for t in TEMPERATURES]


def packet(channel, points, channels=None):
    """The full HID report: report id, command, channel, duties, padding."""
    cid, _, _ = channel_limits(channel, channels)
    body = [SET_COOLING] + list(cid) + duties(channel, points, channels)
    # 64-byte payload after the leading report id, matching every other command
    # this daemon sends.
    return bytes([0x00] + body + [0x00] * (64 - len(body)))


def curves_for(mode, config):
    """The (pump, fan) point lists a mode implies, or None to change nothing.

    'firmware' is the only mode that writes nothing. Note what that means once
    any other mode has been used: the cooler keeps running the last curve it was
    given, because there is no way to ask it for its original one back.
    """
    if mode == 'firmware':
        return None
    if mode == 'default':
        return {k: list(v) for k, v in QUIET_CURVES.items()}
    if mode == 'full':
        # Flat 100%. normalize() would add the failsafe anyway, but being
        # explicit means the uploaded curve reads as "full" at every degree.
        return {'pump': [[MIN_TEMPERATURE, 100]], 'fan': [[MIN_TEMPERATURE, 100]]}
    return {'pump': config.get('pump') or DEFAULT_CURVES['pump'],
            'fan': config.get('fan') or DEFAULT_CURVES['fan']}


def flat(duty):
    """A curve that is *duty* everywhere.

    How a fixed speed is set: there is no "set duty" command, only "set curve",
    so a flat curve is the fixed-speed primitive. normalize() still appends the
    coolant failsafe, which is what keeps host-driven control from being
    dangerous when the host stops driving.
    """
    return [[MIN_TEMPERATURE, int(duty)], [CRITICAL_TEMPERATURE - 1, int(duty)]]


def retarget(points, sensor):
    """Move a curve's points onto *sensor*'s breakpoints, keeping the duties.

    Switching sensor should keep the shape someone drew rather than silently
    reading 20-50 C points as CPU temperatures, where they would mean "always
    100%".
    """
    temps = SENSOR_POINTS[sensor]
    duties_only = [int(d) for _, d in validate_points(points)]
    # Pad or trim to the number of breakpoints this sensor uses.
    while len(duties_only) < len(temps):
        duties_only.append(duties_only[-1] if duties_only else 50)
    return [[t, d] for t, d in zip(temps, duties_only[:len(temps)])]


def duty_at(points, temperature):
    """Duty the curve asks for at *temperature*, for host-driven sensors.

    No coolant failsafe here: this curve is read against a CPU or GPU, where
    59 C means nothing in particular. The failsafe still exists on the device,
    in the flat curve that gets uploaded.
    """
    return clamp(interpolate(normalize(points, failsafe=False), temperature), 0, 100)


def describe(channel, points):
    """A short human summary, for logs and the status command."""
    values = duties(channel, points)
    return (f'{channel}: {values[0]}% at {MIN_TEMPERATURE}C -> '
            f'{values[-1]}% at {CRITICAL_TEMPERATURE}C')
