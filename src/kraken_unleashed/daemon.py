"""The daemon: sole owner of the cooler, and the server behind the GUI.

It runs one loop that paints the LCD and drives the LEDs, and serves a control
socket so the GUI can change things live without a restart. Nothing else may
open the device -- see docs/ARCHITECTURE.md for why that rule exists and what
breaks when it is broken.
"""
import argparse
import json
import os
import signal
import sys
import threading
import time

from PIL import Image

from . import config as cfg
from . import effects, q565, sacn
from .compositor import Compositor, STYLES, default_background
from .control import ControlServer
from .device import (KrakenLCD, BootloaderMode, NotFound,
                     MAX_CONSECUTIVE_FAILURES)
from .sensors import Sensors

#: Published so other tools can read the cooler without opening the device.
#: The kraken-lcd path is kept because existing exporters already read it.
STATUS_PATHS = ('/run/kraken-unleashed/status.json', '/run/kraken-lcd/status.json')

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
            cfg.deep_update(self.config, patch)
            # Only rebuild what actually changed: reloading a GIF takes a
            # moment and would stutter the screen on every slider nudge.
            if 'lcd' in patch and any(k in patch['lcd'] for k in
                                      ('gif', 'style', 'dim', 'rotate')):
                self._rebuild_lcd = True
            if 'openrgb' in patch:
                self._rebuild_sacn = True
            changed = json.dumps(self.config, sort_keys=True) != before
            if changed and request.get('persist', True):
                cfg.save(self.config, self.config_path)
        return {'changed': changed, 'config': self.config}

    def _h_reload(self, request):
        with self.lock:
            self.config = cfg.load(self.config_path)
            self._rebuild_lcd = True
            self._rebuild_sacn = True
        return {'config': self.config}

    def _h_preview(self, request):
        """Render one frame to a PNG. Never touches the device."""
        path = request.get('path') or '/tmp/kraken-unleashed-preview.png'
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
        except (BootloaderMode, NotFound) as exc:
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
                ring, fans = self.led_colours(time.clock_gettime(time.CLOCK_BOOTTIME))
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
