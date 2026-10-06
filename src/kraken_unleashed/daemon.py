"""The daemon: sole owner of the cooler, and the server behind the GUI.

It runs one loop that paints the LCD and drives the LEDs, and serves a control
socket so the GUI can change things live without a restart. Nothing else may
open the device -- see docs/ARCHITECTURE.md for why that rule exists and what
breaks when it is broken.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

import argparse
import json
import os
import signal
import sys
import threading
import time

from PIL import Image

from . import compat
from . import config as cfg
from . import cooling, effects, q565, sacn
from .compositor import Compositor, STYLES, default_background
from .control import ControlServer
from .device import (KrakenLCD, DeviceError, MAX_CONSECUTIVE_FAILURES)
from .sensors import Sensors

#: Published so other tools can read the cooler without opening the device.
#: On Linux the older kraken-lcd path is kept too, because existing exporters
#: already read it and an upgrade should not quietly break someone's dashboard.
STATUS_PATHS = compat.status_paths()

LED_COUNT = KrakenLCD.LEDS_PER_CHANNEL


def write_status(dev, extra=None):
    """Best-effort publish of the latest reading; never disturb the loop."""
    if not dev:
        return
    payload = dict(dev, ts=time.time())
    if extra:
        payload.update(extra)
    for path in STATUS_PATHS:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + '.tmp'
            with open(tmp, 'w') as handle:
                json.dump(payload, handle)
            os.replace(tmp, path)
        except Exception:
            pass


class Daemon:
    def __init__(self, config_path=None):
        self.config_path = config_path
        self.config = cfg.load(config_path)
        self.lock = threading.Lock()
        self.stop_requested = False
        self.kraken = None
        self.compositor = None
        self.receiver = None
        self.control = None
        self.sensors = Sensors()
        self.dev = {}
        self.stats = {'frames': 0, 'refused': 0, 'started': time.time(),
                      'last_error': None}
        self._rebuild_lcd = True
        self._rebuild_sacn = True
        self._apply_cooling = True
        self.cooling_applied = None
        self._cooling_duties = {}      # last duty actually written, per channel
        self._cooling_tick = 0.0
        self._cooling_smoothed = None  # EMA of the host sensor

    # -- subsystem (re)building -------------------------------------------- #

    def build_compositor(self):
        lcd = self.config['lcd']
        path = lcd.get('gif') or default_background()
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            self.stats['last_error'] = f'background not found: {path}'
            path = default_background()
        self.compositor = Compositor(lcd, gif_path=path,
                                     cpu_vendor=self.sensors.cpu_vendor,
                                     gpu_vendor=self.sensors.gpu_vendor)
        self._rebuild_lcd = False

    def build_receiver(self):
        if self.receiver:
            self.receiver.stop()
            self.receiver = None
        orgb = self.config['openrgb']
        if orgb.get('enabled'):
            self.receiver = sacn.Receiver(
                universe=orgb['universe'], start_channel=orgb['start_channel'],
                port=orgb['port'], timeout=orgb['timeout'], bind=orgb['bind'])
            self.receiver.start()
        self._rebuild_sacn = False

    def apply_cooling(self):
        """Upload the pump/fan curves, or leave the firmware's alone.

        Sent once per change, not per loop: the cooler runs the curve itself, so
        re-sending it every frame would be pointless traffic on the same HID
        interface the LCD handshake depends on.
        """
        self._apply_cooling = False
        conf = self.config['cooling']
        mode = conf.get('mode', 'firmware')
        if mode not in cooling.MODES:
            self.stats['last_error'] = f'unknown cooling mode {mode!r}'
            return
        sensor = conf.get('sensor', 'liquid')
        curves = cooling.curves_for(mode, conf)
        if curves is None:
            print('cooling: leaving the firmware curve alone', flush=True)
            self.cooling_applied = {'mode': mode, 'sensor': sensor}
            return

        # A host-driven sensor is not uploaded as a curve -- the firmware only
        # understands coolant temperature. The loop pushes a flat duty instead.
        if sensor != 'liquid' and mode != 'full':
            self._cooling_duties = {}
            self._cooling_tick = 0.0
            self._cooling_smoothed = None
            self.cooling_applied = {'mode': mode, 'sensor': sensor,
                                    'driven_by': 'host'}
            print(f'cooling: following {sensor} temperature from the host',
                  flush=True)
            self.tick_cooling(force=True)
            return

        applied = {'mode': mode, 'sensor': 'liquid'}
        for channel in ('pump', 'fan'):
            try:
                self.kraken.set_speed_profile(channel, curves[channel])
                applied[channel] = cooling.describe(channel, curves[channel])
                print(f'cooling: {applied[channel]}', flush=True)
            except Exception as exc:
                self.stats['last_error'] = f'cooling {channel}: {exc}'
                print(f'cooling: {channel} failed: {exc}', flush=True)
        self.cooling_applied = applied

    def sensor_value(self, sensor):
        cpu_t, _, gpu_t, _ = self.sensors.snapshot()
        return {'liquid': self.dev.get('liquid'), 'cpu': cpu_t, 'gpu': gpu_t}.get(sensor)

    def tick_cooling(self, force=False):
        """Host-driven cooling: read the sensor, push a flat duty if it moved.

        Only writes when the duty actually changes by a couple of points. The
        HID interface is shared with the LCD handshake, so a write every second
        for a degree of noise would be real contention for no benefit.
        """
        conf = self.config['cooling']
        mode, sensor = conf.get('mode', 'firmware'), conf.get('sensor', 'liquid')
        if sensor == 'liquid' or mode in ('firmware', 'full'):
            return
        now = time.monotonic()
        if not force and now - self._cooling_tick < 2.0:
            return
        self._cooling_tick = now

        raw = self.sensor_value(sensor)
        if raw is not None:
            # CPU and GPU temperatures are noisy at the degree level, and a
            # steep curve turns that into audible fan hunting plus a device
            # write every couple of seconds on the same interface the LCD
            # handshake uses. Smooth before reading the curve.
            self._cooling_smoothed = (
                raw if self._cooling_smoothed is None
                else self._cooling_smoothed + 0.25 * (raw - self._cooling_smoothed))
        value = self._cooling_smoothed if raw is not None else None
        if value is None:
            if self._cooling_duties.get('_fallback') != sensor:
                print(f'cooling: {sensor} temperature unavailable, falling back '
                      'to the quiet coolant preset', flush=True)
                for channel in ('pump', 'fan'):
                    try:
                        self.kraken.set_speed_profile(
                            channel, cooling.QUIET_CURVES[channel])
                    except Exception as exc:
                        self.stats['last_error'] = f'cooling {channel}: {exc}'
                self._cooling_duties = {'_fallback': sensor}
            return
        self._cooling_duties.pop('_fallback', None)

        base = (cooling.QUIET_CURVES if mode == 'default' else conf)
        for channel in ('pump', 'fan'):
            points = base.get(channel) or cooling.DEFAULT_CURVES[channel]
            if mode == 'default':
                points = cooling.retarget(points, sensor)
            duty = cooling.duty_at(points, value)
            _, dmin, dmax = cooling.CHANNELS[channel]
            duty = cooling.clamp(duty, dmin, dmax)
            previous = self._cooling_duties.get(channel)
            # 3 points of hysteresis on top of the smoothing: together these
            # stop a degree of sensor noise becoming a speed change.
            if previous is not None and abs(duty - previous) < 3:
                continue
            try:
                self.kraken.set_speed_profile(channel, cooling.flat(duty))
                self._cooling_duties[channel] = duty
            except Exception as exc:
                self.stats['last_error'] = f'cooling {channel}: {exc}'

    # -- LED colours -------------------------------------------------------- #

    def led_colours(self, t):
        """(ring, fans) colour lists, or (None, None) to leave them alone.

        Sources in priority order: OpenRGB over E1.31 when it is actually
        sending, otherwise the local effect. That fallback matters -- closing
        OpenRGB should not leave the cooler frozen on the last colour it sent.
        """
        led = self.config['led']
        if not led.get('enabled'):
            return None, None
        zones = led.get('zones', {})
        want_ring, want_fans = zones.get('ring', True), zones.get('fans', True)

        source = led.get('source', 'effect')
        colours = None
        if source == 'off':
            colours = [(0, 0, 0)] * (LED_COUNT * 2)
        elif source == 'openrgb' and self.receiver is not None:
            colours = self.receiver.colors(LED_COUNT * 2)
        if colours is None:
            params = dict(cfg.led_params(self.config))
            params['value'] = self.dev.get('liquid')
            # Render both zones as one strip so a wave or chase travels from the
            # ring onto the fans instead of restarting halfway.
            colours = effects.render(led.get('effect', 'breathing'), t,
                                     LED_COUNT * 2, params)
        ring = colours[:LED_COUNT] if want_ring else None
        fans = colours[LED_COUNT:LED_COUNT * 2] if want_fans else None
        return ring, fans

    # -- control socket handlers ------------------------------------------- #

    def handlers(self):
        return {
            'ping': lambda r: {'pong': True},
            'status': self._h_status,
            'get_config': lambda r: {'config': self.config},
            'set_config': self._h_set_config,
            'reload': self._h_reload,
            'effects': lambda r: {'effects': sorted(effects.EFFECTS),
                                  'params': {k: list(v) for k, v in effects.PARAMS.items()}},
            'styles': lambda r: {'styles': list(STYLES)},
            'preview': self._h_preview,
        }

    def _h_status(self, request):
        with self.lock:
            uptime = time.time() - self.stats['started']
            return {
                'device': self.dev,
                'sensors': dict(zip(('cpu_temp', 'cpu_load', 'gpu_temp', 'gpu_load'),
                                    self.sensors.snapshot())),
                'lcd': {'enabled': self.config['lcd']['enabled'],
                        'style': self.config['lcd']['style'],
                        'background': getattr(self.compositor, 'path', None),
                        'fps': self.config['lcd']['fps']},
                'led': {'enabled': self.config['led']['enabled'],
                        'source': self.config['led']['source'],
                        'effect': self.config['led']['effect']},
                'openrgb': self.receiver.stats() if self.receiver else
                           {'listening': False, 'live': False},
                'cooling': dict(self.cooling_applied or {},
                                configured=self.config['cooling']['mode'],
                                sensor=self.config['cooling'].get('sensor', 'liquid'),
                                duties={k: v for k, v in self._cooling_duties.items()
                                        if not k.startswith('_')},
                                smoothed=(round(self._cooling_smoothed, 1)
                                          if self._cooling_smoothed is not None else None)),
                'stats': dict(self.stats, uptime=uptime,
                              fps=self.stats['frames'] / max(uptime, 0.01)),
                'version': __import__('kraken_unleashed').__version__,
            }

    def _h_set_config(self, request):
        patch = request.get('patch') or {}
        if not isinstance(patch, dict):
            raise ValueError('patch must be an object')
        with self.lock:
            before = json.dumps(self.config, sort_keys=True)
            if 'cooling' in patch:
                # Validate against the merged result, not the patch alone, and
                # before persisting: a bad curve in the config file would be
                # re-applied on every start and fail silently each time.
                merged = cfg.deep_update(json.loads(before)['cooling'],
                                         patch['cooling'])
                cooling.validate(merged)
            cfg.deep_update(self.config, patch)
            # Only rebuild what actually changed: reloading a GIF takes a
            # moment and would stutter the screen on every slider nudge.
            if 'lcd' in patch and any(k in patch['lcd'] for k in
                                      ('gif', 'style', 'dim', 'rotate')):
                self._rebuild_lcd = True
            if 'openrgb' in patch:
                self._rebuild_sacn = True
            if 'cooling' in patch:
                self._apply_cooling = True
            changed = json.dumps(self.config, sort_keys=True) != before
            if changed and request.get('persist', True):
                cfg.save(self.config, self.config_path)
        return {'changed': changed, 'config': self.config}

    def _h_reload(self, request):
        with self.lock:
            self.config = cfg.load(self.config_path)
            self._rebuild_lcd = True
            self._rebuild_sacn = True
            self._apply_cooling = True
        return {'config': self.config}

    def _h_preview(self, request):
        """Render one frame to a PNG. Never touches the device."""
        path = request.get('path') or compat.preview_path()
        overrides = request.get('lcd') or {}
        with self.lock:
            lcd = dict(self.config['lcd'])
        lcd.update(overrides)
        gif = os.path.expanduser(lcd.get('gif') or default_background())
        if not os.path.exists(gif):
            raise ValueError(f'background not found: {gif}')
        comp = Compositor(lcd, gif_path=gif,
                          cpu_vendor=self.sensors.cpu_vendor,
                          gpu_vendor=self.sensors.gpu_vendor)
        data = comp.data(self.dev or {'liquid': 31.4, 'pump_rpm': 2280,
                                      'fan_rpm': 780},
                         self.sensors.snapshot())
        image = comp.compose(request.get('elapsed', 0.0), data,
                             rotate=request.get('rotate', False))
        image.save(path)
        return {'path': path, 'style': lcd['style'], 'rotate': lcd['rotate']}

    # -- main loop ---------------------------------------------------------- #

    def run(self):
        signal.signal(signal.SIGTERM, self._signal)
        signal.signal(signal.SIGINT, self._signal)
        self.sensors.start()

        control_cfg = self.config['control']
        self.control = ControlServer(control_cfg['socket'], self.handlers(),
                                     group=control_cfg.get('group'))
        self.control.start()

        try:
            self.kraken = KrakenLCD()
        except DeviceError as exc:
            print(exc, file=sys.stderr, flush=True)
            return 1

        self.dev = self.kraken.status() or {}
        print(f'device ok: {self.dev or "no status report"}', flush=True)
        if self.control.error:
            print(f'control socket: {self.control.error}', flush=True)
        else:
            print(f'control socket: {control_cfg["socket"]}', flush=True)
        write_status(self.dev)

        consecutive = 0
        last_status = 0.0
        last_led = None
        exit_code = 0
        started = time.monotonic()
        try:
            while not self.stop_requested:
                now = time.monotonic()
                with self.lock:
                    if self._rebuild_lcd:
                        self.build_compositor()
                    if self._rebuild_sacn:
                        self.build_receiver()
                    if self._apply_cooling:
                        self.apply_cooling()
                    # Under the lock: it reads the config and writes to the
                    # device, and set_config may be mutating that config from a
                    # control-socket thread. The write is about a millisecond.
                    self.tick_cooling()
                    lcd_on = self.config['lcd']['enabled']
                    fps = float(self.config['lcd']['fps']) if lcd_on else 30.0
                interval = 1.0 / max(1.0, fps)

                if now - last_status >= 1.0:
                    fresh = self.kraken.status()
                    if fresh:
                        self.dev = fresh
                        write_status(self.dev, {'led_source': self.config['led']['source']})
                    last_status = now

                # LEDs. CLOCK_BOOTTIME, not elapsed time, so effects stay in
                # phase with rgb-sync on the rest of the machine.
                ring, fans = self.led_colours(compat.boot_clock())
                if (ring, fans) != last_led:
                    self.kraken.set_leds(ring=ring, fans=fans)
                    last_led = (ring, fans)

                if lcd_on:
                    data = self.compositor.data(self.dev, self.sensors.snapshot())
                    payload = q565.encode(self.compositor.compose(now - started, data))
                    if self.kraken.send(payload):
                        self.stats['frames'] += 1
                        consecutive = 0
                    else:
                        self.stats['refused'] += 1
                        consecutive += 1
                        if consecutive >= MAX_CONSECUTIVE_FAILURES:
                            print(f'device refused {consecutive} frames in a row '
                                  '- stopping', flush=True)
                            exit_code = 1
                            break
                slack = interval - (time.monotonic() - now)
                if slack > 0:
                    time.sleep(slack)
        except Exception as exc:
            print(f'stopping: {exc}', flush=True)
            exit_code = 1
        finally:
            elapsed = time.monotonic() - started
            self.sensors.stop()
            if self.receiver:
                self.receiver.stop()
            if self.control:
                self.control.stop()
            self.kraken.close()
            print(f'{self.stats["frames"]} frames in {elapsed:.1f}s = '
                  f'{self.stats["frames"] / max(elapsed, 0.01):.1f} fps, '
                  f'{self.stats["refused"]} refused', flush=True)
        return exit_code

    def _signal(self, signum, frame):
        self.stop_requested = True


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='kraken-unleashed',
        description='Stream a sensor screen and drive the LEDs on an NZXT Kraken 2024.')
    parser.add_argument('--config', default=None, help='config file (JSON)')
    parser.add_argument('--preview', metavar='PATH',
                        help='render one frame to PATH and exit, device untouched')
    parser.add_argument('--list-effects', action='store_true')
    parser.add_argument('--list-styles', action='store_true')
    args = parser.parse_args(argv)

    if args.list_effects:
        for name in sorted(effects.EFFECTS):
            print(name)
        return 0
    if args.list_styles:
        for name in STYLES:
            print(name)
        return 0
    if args.preview:
        conf = cfg.load(args.config)
        sensors = Sensors()
        comp = Compositor(conf['lcd'], cpu_vendor=sensors.cpu_vendor,
                          gpu_vendor=sensors.gpu_vendor)
        data = comp.data({'liquid': 31.4, 'pump_rpm': 2280, 'fan_rpm': 780},
                         (45.0, 12.0, 38.0, 3.0))
        comp.compose(0.0, data, rotate=False).save(args.preview)
        print(f'wrote {args.preview}')
        return 0

    return Daemon(args.config).run()


if __name__ == '__main__':
    sys.exit(main())
