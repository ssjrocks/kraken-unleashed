"""Kraken Unleashed - GTK4/libadwaita front end.

The GUI never touches the cooler. It is a client of the daemon's control
socket, which is the only process allowed to hold the device. Everything here
is "read the config, show it, send a patch back".
"""
import os
import sys
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk, Gdk  # noqa: E402

from .control import Client  # noqa: E402

APP_ID = 'io.github.ssjrocks.KrakenUnleashed'
PREVIEW_PATH = '/run/kraken-unleashed/preview.png'

STYLE_LABELS = {
    'triple': 'All sensors',
    'liquid_ring': 'Coolant ring',
    'cpu_gpu': 'CPU and GPU',
}
SOURCE_LABELS = [
    ('effect', 'Built-in effects'),
    ('openrgb', 'OpenRGB (E1.31)'),
    ('off', 'Off'),
]
EFFECT_LABELS = {
    'breathing': 'Breathing', 'static': 'Static', 'pulse': 'Pulse',
    'spectrum': 'Spectrum cycle', 'rainbow': 'Rainbow', 'wave': 'Wave',
    'chase': 'Chase', 'gradient': 'Gradient', 'temperature': 'Coolant temperature',
    'off': 'Off',
}
#: Numeric effect parameters: key -> (label, min, max, step, digits, subtitle)
PARAM_SPECS = {
    'period': ('Period', 0.2, 60.0, 0.1, 1, 'Seconds for one full cycle'),
    'min_brightness': ('Minimum brightness', 0.0, 1.0, 0.05, 2, None),
    'max_brightness': ('Maximum brightness', 0.0, 1.0, 0.05, 2, None),
    'gamma': ('Gamma', 1.0, 3.0, 0.1, 1, 'Higher makes the fade look more even'),
    'spread': ('Spread', 0.1, 5.0, 0.1, 1, 'How many cycles fit around the ring'),
    'tail': ('Tail length', 1, 24, 1, 0, 'LEDs lit behind the comet'),
    'temp_min': ('Coolest temperature', 10.0, 60.0, 1.0, 0, 'Fully blue at or below'),
    'temp_max': ('Hottest temperature', 20.0, 90.0, 1.0, 0, 'Fully red at or above'),
}


def hex_to_rgba(value):
    rgba = Gdk.RGBA()
    rgba.parse('#' + value)
    return rgba


def rgba_to_hex(rgba):
    return '%02X%02X%02X' % (round(rgba.red * 255), round(rgba.green * 255),
                             round(rgba.blue * 255))


class Window(Adw.ApplicationWindow):
    def __init__(self, app, client):
        super().__init__(application=app, title='Kraken Unleashed',
                         default_width=900, default_height=720)
        self.client = client
        self.config = {}
        self.meta = {'effects': [], 'styles': [], 'params': {}}
        self._loading = False          # guards against change-handler feedback
        self._preview_pending = False
        self._param_rows = {}

        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=self.stack,
                                    policy=Adw.ViewSwitcherPolicy.WIDE)
        header = Adw.HeaderBar(title_widget=switcher)

        menu = Gio.Menu()
        menu.append('Reload from disk', 'win.reload')
        menu.append('About Kraken Unleashed', 'win.about')
        header.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic',
                                       menu_model=menu, tooltip_text='Menu'))

        self.banner = Adw.Banner(revealed=False, button_label='Retry')
        self.banner.connect('button-clicked', lambda *_: self.refresh_all())

        self.status_label = Gtk.Label(xalign=0, margin_start=12, margin_end=12,
                                      margin_top=6, margin_bottom=6)
        self.status_label.add_css_class('dim-label')
        self.status_label.add_css_class('caption')

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.banner)
        box.append(self.stack)
        self.stack.set_vexpand(True)
        box.append(Gtk.Separator())
        box.append(self.status_label)

        view = Adw.ToolbarView(content=box)
        view.add_top_bar(header)
        self.set_content(view)

        self.stack.add_titled_with_icon(self._display_page(), 'display',
                                        'Display', 'video-display-symbolic')
        self.stack.add_titled_with_icon(self._lighting_page(), 'lighting',
                                        'Lighting', 'weather-clear-symbolic')
        self.stack.add_titled_with_icon(self._openrgb_page(), 'openrgb',
                                        'OpenRGB', 'network-transmit-symbolic')

        for name, fn in (('reload', self._on_reload), ('about', self._on_about)):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', fn)
            self.add_action(action)

        self.refresh_all()
        GLib.timeout_add_seconds(1, self._tick)

    # -- pages -------------------------------------------------------------- #

    def _display_page(self):
        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup(title='Preview',
                                     description='What the cooler is showing, '
                                                 'rendered without touching it')
        self.preview = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN,
                                   height_request=300, margin_top=6,
                                   margin_bottom=6)
        frame = Gtk.Frame(child=self.preview)
        frame.add_css_class('view')
        group.add(frame)
        page.add(group)

        group = Adw.PreferencesGroup(title='Screen')
        self.lcd_enabled = Adw.SwitchRow(
            title='Show the sensor screen',
            subtitle='Turn off to leave the LCD alone and only drive the LEDs')
        self.lcd_enabled.connect('notify::active', self._on_change)
        group.add(self.lcd_enabled)

        self.style_row = Adw.ComboRow(title='Layout')
        self.style_row.connect('notify::selected', self._on_change)
        group.add(self.style_row)

        self.bg_row = Adw.ActionRow(title='Background',
                                    subtitle='Any GIF or still image')
        choose = Gtk.Button(label='Choose…', valign=Gtk.Align.CENTER)
        choose.connect('clicked', self._on_choose_background)
        reset = Gtk.Button(icon_name='edit-undo-symbolic', valign=Gtk.Align.CENTER,
                           tooltip_text='Use the bundled demo background')
        reset.connect('clicked', self._on_reset_background)
        buttons = Gtk.Box(spacing=6)
        buttons.append(choose)
        buttons.append(reset)
        self.bg_row.add_suffix(buttons)
        group.add(self.bg_row)

        self.dim_row = Adw.SpinRow.new_with_range(0.0, 1.0, 0.05)
        self.dim_row.set_title('Background dimming')
        self.dim_row.set_subtitle('Higher keeps the numbers readable over a busy image')
        self.dim_row.set_digits(2)
        self.dim_row.connect('notify::value', self._on_change)
        group.add(self.dim_row)

        self.rotate_row = Adw.ComboRow(
            title='Rotation',
            subtitle='Match how your cooler is mounted',
            model=Gtk.StringList.new(['0°', '90°', '180°', '270°']))
        self.rotate_row.connect('notify::selected', self._on_change)
        group.add(self.rotate_row)

        self.fps_row = Adw.SpinRow.new_with_range(1, 12, 1)
        self.fps_row.set_title('Frame rate')
        self.fps_row.set_subtitle('12 is the panel\'s practical ceiling, ~7% of a core')
        self.fps_row.connect('notify::value', self._on_change)
        group.add(self.fps_row)

        self.ring_row = Adw.ActionRow(
            title='Coolant arc colour',
            subtitle='Only the ring and CPU/GPU layouts draw an arc')
        self.ring_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                              valign=Gtk.Align.CENTER)
        self.ring_btn.connect('notify::rgba', self._on_change)
        self.ring_row.add_suffix(self.ring_btn)
        group.add(self.ring_row)
        page.add(group)
        return page

    def _lighting_page(self):
        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup(
            title='Cooler lighting',
            description='The pump ring and the radiator fans, 24 LEDs each')
        self.led_enabled = Adw.SwitchRow(title='Drive the cooler\'s LEDs')
        self.led_enabled.connect('notify::active', self._on_change)
        group.add(self.led_enabled)

        self.source_row = Adw.ComboRow(
            title='Colours come from',
            model=Gtk.StringList.new([label for _, label in SOURCE_LABELS]))
        self.source_row.connect('notify::selected', self._on_change)
        group.add(self.source_row)

        self.zone_ring = Adw.SwitchRow(title='Pump ring')
        self.zone_fans = Adw.SwitchRow(title='Radiator fans')
        for row in (self.zone_ring, self.zone_fans):
            row.connect('notify::active', self._on_change)
            group.add(row)
        page.add(group)

        self.effect_group = Adw.PreferencesGroup(title='Effect')
        self.effect_row = Adw.ComboRow(title='Effect')
        self.effect_row.connect('notify::selected', self._on_change)
        self.effect_group.add(self.effect_row)

        self.colour_row = Adw.ActionRow(title='Colour')
        self.colour_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                                valign=Gtk.Align.CENTER)
        self.colour_btn.connect('notify::rgba', self._on_change)
        self.colour_row.add_suffix(self.colour_btn)
        self.effect_group.add(self.colour_row)

        self.colour2_row = Adw.ActionRow(title='Second colour')
        self.colour2_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                                 valign=Gtk.Align.CENTER)
        self.colour2_btn.connect('notify::rgba', self._on_change)
        self.colour2_row.add_suffix(self.colour2_btn)
        self.effect_group.add(self.colour2_row)

        for key, (label, lo, hi, step, digits, subtitle) in PARAM_SPECS.items():
            row = Adw.SpinRow.new_with_range(lo, hi, step)
            row.set_title(label)
            if subtitle:
                row.set_subtitle(subtitle)
            row.set_digits(digits)
            row.connect('notify::value', self._on_change)
            self._param_rows[key] = row
            self.effect_group.add(row)
        page.add(self.effect_group)

        group = Adw.PreferencesGroup(
            title='Sync with the rest of your lighting',
            description='If this file exists, its colour and timing win, so the '
                        'cooler breathes in step with rgb-sync. Both read the '
                        'same clock, so they stay in phase without talking.')
        self.follow_row = Adw.EntryRow(title='Shared settings file')
        self.follow_row.connect('apply', self._on_change)
        self.follow_row.set_show_apply_button(True)
        group.add(self.follow_row)
        page.add(group)
        return page

    def _openrgb_page(self):
        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup(
            title='Let OpenRGB drive the cooler',
            description='OpenRGB cannot talk to this cooler directly — its '
                        'packets are rejected by the firmware. Instead this app '
                        'listens for E1.31, a protocol OpenRGB already speaks, '
                        'and relays it to the LEDs. OpenRGB never touches the '
                        'device, so nothing fights over it.')
        self.orgb_enabled = Adw.SwitchRow(
            title='Accept lighting from OpenRGB',
            subtitle='Set "Colours come from" to OpenRGB on the Lighting page too')
        self.orgb_enabled.connect('notify::active', self._on_change)
        group.add(self.orgb_enabled)

        self.orgb_status = Adw.ActionRow(title='Status', subtitle='—')
        self.orgb_icon = Gtk.Image(icon_name='media-playback-stop-symbolic',
                                   valign=Gtk.Align.CENTER)
        self.orgb_status.add_prefix(self.orgb_icon)
        group.add(self.orgb_status)
        page.add(group)

        group = Adw.PreferencesGroup(title='Network')
        self.universe_row = Adw.SpinRow.new_with_range(1, 63999, 1)
        self.universe_row.set_title('Universe')
        self.channel_row = Adw.SpinRow.new_with_range(1, 512, 1)
        self.channel_row.set_title('Start channel')
        self.port_row = Adw.SpinRow.new_with_range(1, 65535, 1)
        self.port_row.set_title('Port')
        self.port_row.set_subtitle('5568 is the standard sACN port')
        self.timeout_row = Adw.SpinRow.new_with_range(0.5, 30.0, 0.5)
        self.timeout_row.set_title('Fall back after')
        self.timeout_row.set_subtitle('Seconds of silence before the built-in '
                                      'effect takes over again')
        self.timeout_row.set_digits(1)
        for row in (self.universe_row, self.channel_row, self.port_row,
                    self.timeout_row):
            row.connect('notify::value', self._on_change)
            group.add(row)
        page.add(group)

        group = Adw.PreferencesGroup(
            title='Setting it up in OpenRGB',
            description='One-time setup on the OpenRGB side.')
        steps = Gtk.Label(
            xalign=0, wrap=True, selectable=True, margin_top=12,
            margin_bottom=12, margin_start=12, margin_end=12,
            label=(
                '1.  In OpenRGB, open <b>Settings → E1.31 Devices</b> and add a device:\n'
                '      Name: <tt>Kraken Unleashed</tt>\n'
                '      IP: <tt>127.0.0.1</tt>   Universe: <tt>1</tt>   '
                'Start channel: <tt>1</tt>   LEDs: <tt>48</tt>\n\n'
                '2.  In <b>Settings → General</b>, make sure the <b>E1.31</b> '
                'detector is enabled, then restart OpenRGB.\n\n'
                '3.  "Kraken Unleashed" now appears as a normal OpenRGB device. '
                'Every OpenRGB effect and profile works on it.\n\n'
                'The first 24 LEDs are the pump ring, the next 24 are the '
                'radiator fans.'))
        steps.set_use_markup(True)
        frame = Gtk.Frame(child=steps)
        frame.add_css_class('view')
        group.add(frame)
        page.add(group)
        return page

    # -- loading and saving ------------------------------------------------- #

    def refresh_all(self):
        try:
            self.config = self.client.call('get_config')['config']
            meta = self.client.call('effects')
            self.meta['effects'] = meta['effects']
            self.meta['params'] = meta['params']
            self.meta['styles'] = self.client.call('styles')['styles']
        except Exception as exc:
            self.banner.set_title(f'Cannot reach the Kraken Unleashed service: {exc}')
            self.banner.set_revealed(True)
            return
        self.banner.set_revealed(False)
        self._populate()
        self.request_preview()

    def _populate(self):
        self._loading = True
        try:
            lcd, led, orgb = (self.config['lcd'], self.config['led'],
                              self.config['openrgb'])

            styles = self.meta['styles']
            self.style_row.set_model(Gtk.StringList.new(
                [STYLE_LABELS.get(s, s) for s in styles]))
            if lcd['style'] in styles:
                self.style_row.set_selected(styles.index(lcd['style']))

            self.lcd_enabled.set_active(bool(lcd['enabled']))
            self.bg_row.set_subtitle(lcd.get('gif') or 'Bundled demo background')
            self.dim_row.set_value(float(lcd['dim']))
            self.rotate_row.set_selected([0, 90, 180, 270].index(int(lcd['rotate']))
                                         if int(lcd['rotate']) in (0, 90, 180, 270) else 1)
            self.fps_row.set_value(float(lcd['fps']))
            self.ring_btn.set_rgba(hex_to_rgba(lcd['ring']))

            self.led_enabled.set_active(bool(led['enabled']))
            sources = [key for key, _ in SOURCE_LABELS]
            self.source_row.set_selected(sources.index(led['source'])
                                         if led['source'] in sources else 0)
            zones = led.get('zones', {})
            self.zone_ring.set_active(bool(zones.get('ring', True)))
            self.zone_fans.set_active(bool(zones.get('fans', True)))

            names = self.meta['effects']
            self.effect_row.set_model(Gtk.StringList.new(
                [EFFECT_LABELS.get(n, n.title()) for n in names]))
            if led['effect'] in names:
                self.effect_row.set_selected(names.index(led['effect']))

            params = led['params']
            self.colour_btn.set_rgba(hex_to_rgba(params.get('color', '00FF00')))
            self.colour2_btn.set_rgba(hex_to_rgba(params.get('color2', '0000FF')))
            for key, row in self._param_rows.items():
                if key in params:
                    row.set_value(float(params[key]))
            self.follow_row.set_text(led.get('follow') or '')

            self.orgb_enabled.set_active(bool(orgb['enabled']))
            self.universe_row.set_value(float(orgb['universe']))
            self.channel_row.set_value(float(orgb['start_channel']))
            self.port_row.set_value(float(orgb['port']))
            self.timeout_row.set_value(float(orgb['timeout']))
        finally:
            self._loading = False
        self._update_param_visibility()

    def _update_param_visibility(self):
        """Show only the knobs the chosen effect actually reads."""
        names = self.meta['effects']
        index = self.effect_row.get_selected()
        effect = names[index] if 0 <= index < len(names) else 'breathing'
        used = set(self.meta['params'].get(effect, ()))
        self.colour_row.set_visible('color' in used)
        self.colour2_row.set_visible('color2' in used)
        for key, row in self._param_rows.items():
            row.set_visible(key in used)
        source = SOURCE_LABELS[self.source_row.get_selected()][0]
        # In OpenRGB mode the effect is still the fallback, so keep it visible.
        self.effect_group.set_visible(source != 'off')
        self.effect_group.set_title(
            'Fallback effect' if source == 'openrgb' else 'Effect')
        self.effect_group.set_description(
            'Used when OpenRGB is not sending anything' if source == 'openrgb' else None)

    def _on_change(self, *_args):
        if self._loading:
            return
        self._update_param_visibility()
        names = self.meta['effects']
        index = self.effect_row.get_selected()
        styles = self.meta['styles']
        patch = {
            'lcd': {
                'enabled': self.lcd_enabled.get_active(),
                'style': styles[self.style_row.get_selected()]
                         if 0 <= self.style_row.get_selected() < len(styles)
                         else self.config['lcd']['style'],
                'dim': round(self.dim_row.get_value(), 3),
                'rotate': [0, 90, 180, 270][self.rotate_row.get_selected()],
                'fps': int(self.fps_row.get_value()),
                'ring': rgba_to_hex(self.ring_btn.get_rgba()),
            },
            'led': {
                'enabled': self.led_enabled.get_active(),
                'source': SOURCE_LABELS[self.source_row.get_selected()][0],
                'effect': names[index] if 0 <= index < len(names)
                          else self.config['led']['effect'],
                'zones': {'ring': self.zone_ring.get_active(),
                          'fans': self.zone_fans.get_active()},
                'follow': self.follow_row.get_text().strip() or None,
                'params': dict(
                    {k: (int(r.get_value()) if PARAM_SPECS[k][4] == 0
                         else round(r.get_value(), 3))
                     for k, r in self._param_rows.items()},
                    color=rgba_to_hex(self.colour_btn.get_rgba()),
                    color2=rgba_to_hex(self.colour2_btn.get_rgba())),
            },
            'openrgb': {
                'enabled': self.orgb_enabled.get_active(),
                'universe': int(self.universe_row.get_value()),
                'start_channel': int(self.channel_row.get_value()),
                'port': int(self.port_row.get_value()),
                'timeout': round(self.timeout_row.get_value(), 2),
            },
        }
        try:
            self.config = self.client.call('set_config', patch=patch)['config']
            self.banner.set_revealed(False)
        except Exception as exc:
            self.banner.set_title(f'Could not apply the change: {exc}')
            self.banner.set_revealed(True)
            return
        self.request_preview()

    # -- background picker -------------------------------------------------- #

    def _on_choose_background(self, _button):
        dialog = Gtk.FileDialog(title='Choose a background')
        filt = Gtk.FileFilter()
        filt.set_name('Images')
        for pattern in ('*.gif', '*.png', '*.jpg', '*.jpeg', '*.webp', '*.bmp'):
            filt.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        dialog.open(self, None, self._on_background_chosen)

    def _on_background_chosen(self, dialog, result):
        try:
            path = dialog.open_finish(result).get_path()
        except GLib.Error:
            return                      # the user cancelled
        self._apply_background(path)

    def _on_reset_background(self, _button):
        self._apply_background(None)

    def _apply_background(self, path):
        try:
            self.config = self.client.call(
                'set_config', patch={'lcd': {'gif': path}})['config']
        except Exception as exc:
            self.banner.set_title(f'Could not set the background: {exc}')
            self.banner.set_revealed(True)
            return
        self.bg_row.set_subtitle(path or 'Bundled demo background')
        self.request_preview()

    # -- preview and status ------------------------------------------------- #

    def request_preview(self):
        """Debounced: a slider drag would otherwise re-render on every step."""
        if self._preview_pending:
            return
        self._preview_pending = True
        GLib.timeout_add(250, self._do_preview)

    def _do_preview(self):
        self._preview_pending = False
        try:
            self.client.call('preview', path=PREVIEW_PATH, rotate=False)
            # set_filename caches by path, and the path never changes, so force
            # a reload or the picture would stay on the first frame rendered.
            self.preview.set_file(None)
            self.preview.set_file(Gio.File.new_for_path(PREVIEW_PATH))
        except Exception:
            pass
        return GLib.SOURCE_REMOVE

    def _tick(self):
        try:
            status = self.client.call('status')
        except Exception:
            self.status_label.set_text('Service unavailable')
            self.banner.set_title('Cannot reach the Kraken Unleashed service')
            self.banner.set_revealed(True)
            return GLib.SOURCE_CONTINUE
        self.banner.set_revealed(False)
        dev, sens = status['device'], status['sensors']
        stats = status['stats']

        def fmt(value, unit=''):
            return f'{value:.0f}{unit}' if isinstance(value, (int, float)) else '--'

        self.status_label.set_text(
            f"Coolant {fmt(dev.get('liquid'), '°C')}   "
            f"Pump {fmt(dev.get('pump_rpm'))} rpm   Fans {fmt(dev.get('fan_rpm'))} rpm"
            f"      CPU {fmt(sens.get('cpu_temp'), '°C')} / {fmt(sens.get('cpu_load'), '%')}"
            f"   GPU {fmt(sens.get('gpu_temp'), '°C')} / {fmt(sens.get('gpu_load'), '%')}"
            f"      {stats['fps']:.1f} fps, {stats['refused']} refused")

        orgb = status['openrgb']
        if not orgb.get('listening'):
            self.orgb_status.set_subtitle('Not listening')
            self.orgb_icon.set_from_icon_name('media-playback-stop-symbolic')
        elif orgb.get('live'):
            self.orgb_status.set_subtitle(
                f"Receiving from {orgb.get('source')} — "
                f"{orgb.get('packets')} packets, universe {orgb.get('universe')}")
            self.orgb_icon.set_from_icon_name('emblem-ok-symbolic')
        else:
            self.orgb_status.set_subtitle(
                f"Listening on port {orgb.get('port')}, universe "
                f"{orgb.get('universe')} — nothing received yet")
            self.orgb_icon.set_from_icon_name('content-loading-symbolic')
        return GLib.SOURCE_CONTINUE

    # -- menu actions ------------------------------------------------------- #

    def _on_reload(self, *_a):
        try:
            self.client.call('reload')
        except Exception:
            pass
        self.refresh_all()

    def _on_about(self, *_a):
        about = Adw.AboutDialog(
            application_name='Kraken Unleashed',
            application_icon=APP_ID,
            developer_name='Kraken Unleashed contributors',
            version=__import__('kraken_unleashed').__version__,
            comments=('Live sensor stats and lighting for the NZXT Kraken 2024 '
                      'Elite on Linux, at the frame rate the Windows software '
                      'manages.'),
            website='https://github.com/ssjrocks/kraken-unleashed',
            issue_url='https://github.com/ssjrocks/kraken-unleashed/issues',
            license_type=Gtk.License.MIT_X11)
        about.add_credit_section('Sensor screens from', ['OpenKraken by David Boulay'])
        about.present(self)


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.client = Client(os.environ.get('KRAKEN_UNLEASHED_SOCKET',
                                            '/run/kraken-unleashed/control.sock'))

    def do_activate(self):
        window = self.props.active_window or Window(self, self.client)
        window.present()


def main(argv=None):
    return Application().run(argv if argv is not None else sys.argv)


if __name__ == '__main__':
    sys.exit(main())
