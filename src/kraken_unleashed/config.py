"""Configuration: defaults, loading, migration and atomic saving.

The daemon owns this file and the GUI edits it through the control socket, so
writes have to be atomic -- a half-written config read by a restarting daemon
would be worse than a stale one.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import tempfile

from . import compat

CONFIG_PATH = compat.config_path()
LEGACY_PATH = compat.legacy_config_path()

DEFAULTS = {
    'lcd': {
        'enabled': True,
        'gif': None,            # None -> the bundled assets/demo.gif
        'style': 'triple',      # triple | liquid_ring | cpu_gpu
        'dim': 0.4,             # 0 = full-brightness background, 1 = black
        'ring': '7C3AED',       # liquid arc colour
        'fps': 12.0,
        'rotate': 90,           # 0/90/180/270, to match how the cooler is mounted
    },
    'led': {
        'enabled': True,
        # Where the colours come from:
        #   "effect"  - this daemon's own effect engine (see effects.py)
        #   "openrgb" - whatever arrives over E1.31, falling back to the effect
        #               when nothing is sending
        #   "off"     - hold the LEDs black
        'source': 'effect',
        'effect': 'breathing',
        'params': {
            'color': '00FF00',
            'color2': '0000FF',
            'period': 5.0,
            'min_brightness': 0.0,
            'max_brightness': 1.0,
            'gamma': 2.2,
            'spread': 1.0,
            'tail': 6,
            'temp_min': 25.0,
            'temp_max': 50.0,
        },
        # When this file exists its colour/period/brightness/gamma override the
        # params above, so the cooler breathes in exact phase with an rgb-sync
        # install driving the rest of the machine. null to ignore it.
        # rgb-sync is a Linux companion, so there is nothing to follow on Windows.
        'follow': None if compat.IS_WINDOWS else '/etc/rgb-sync.json',
        'zones': {'ring': True, 'fans': True},
    },
    'openrgb': {
        'enabled': False,       # turn on to accept E1.31 from OpenRGB
        'universe': 1,
        'start_channel': 1,
        'port': 5568,
        'timeout': 2.0,         # seconds of silence before falling back
        'bind': '0.0.0.0',
    },
    'control': {
        # A Unix socket path on Linux, "host:port" on Windows. null means
        # "whatever this platform uses by default", which is the usual case.
        'socket': None,
        # Group allowed to talk to the daemon on Linux. install.sh sets this to
        # the installing user's group so the GUI works without a re-login.
        # Ignored on Windows, where a token file is used instead.
        'group': None,
    },
}


def deep_update(base, overlay):
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_update(base[key], value)
        else:
            base[key] = value
    return base


def defaults():
    return json.loads(json.dumps(DEFAULTS))


def _read_json(path):
    """Read JSON, tolerating whole-line // comments. {} for an empty file."""
    with open(path) as handle:
        text = ''.join(line for line in handle
                       if not line.lstrip().startswith('//')).strip()
    return json.loads(text) if text else {}


def migrate(raw):
    """Accept the older flat kraken-lcd.conf shape as well as the current one.

    The 1.0 config had the LCD keys at the top level and a single `led` block
    with the breathing parameters inline. Map that onto the sectioned layout so
    an upgrade keeps someone's settings instead of silently resetting them.
    """
    if not raw or any(k in raw for k in ('lcd', 'openrgb', 'control')):
        return raw                      # already the current shape
    out = {'lcd': {}, 'led': {}}
    for key in ('gif', 'style', 'dim', 'ring', 'fps', 'rotate'):
        if key in raw:
            out['lcd'][key] = raw[key]
    led = raw.get('led') or {}
    if 'enabled' in led:
        out['led']['enabled'] = led['enabled']
    if 'follow' in led:
        out['led']['follow'] = led['follow']
    params = {k: led[k] for k in
              ('color', 'period', 'min_brightness', 'max_brightness', 'gamma')
              if k in led}
    if params:
        out['led']['params'] = params
        out['led']['effect'] = 'breathing'
        out['led']['source'] = 'effect'
    return out


def load(path=None):
    """Config from *path*, else the legacy file, else the defaults."""
    config = defaults()
    candidates = [path] if path else [CONFIG_PATH, LEGACY_PATH]
    for candidate in candidates:
        if not candidate or not os.path.exists(candidate):
            continue
        try:
            raw = _read_json(candidate)
        except ValueError as exc:
            raise ValueError(f'{candidate}: not valid JSON ({exc})') from exc
        deep_update(config, migrate(raw))
        break
    if not config['control'].get('socket'):
        config['control']['socket'] = compat.control_endpoint()
    return config


def save(config, path=None):
    """Write *config* atomically. Comments in a hand-edited file are lost."""
    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or '.', prefix='.kuconf')
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(config, handle, indent=4)
            handle.write('\n')
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def led_params(config):
    """Effect parameters, with rgb-sync's shared file taking precedence."""
    params = dict(config['led']['params'])
    follow = config['led'].get('follow')
    if follow:
        try:
            with open(follow) as handle:
                shared = json.load(handle)
            for key in ('color', 'period', 'min_brightness', 'max_brightness', 'gamma'):
                if key in shared:
                    params[key] = shared[key]
        except (OSError, ValueError):
            pass
    return params
