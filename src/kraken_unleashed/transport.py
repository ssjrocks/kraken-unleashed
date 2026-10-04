"""Talking to the cooler, on Linux and on Windows.

The Kraken is a composite device with two interfaces, and this matters more
than anything else in the port:

    interface 0   vendor specific, bulk EP 0x02 OUT   -> LCD frame payloads
    interface 1   HID, EP 0x81 IN / 0x01 OUT          -> commands, status, LEDs

Because they are separate interfaces, each can have its own driver. On Linux
that is usbfs on 0 and usbhid on 1. On Windows it means WinUSB can be bound to
interface 0 alone while interface 1 stays on the native HID driver -- so there
is no need to take the whole device away from the HID stack. See docs/WINDOWS.md.

Every transport exposes the same four operations the device layer needs:
hid_write, hid_read, hid_drain and bulk_write.
"""
import os
import sys
import time

import usb.core
import usb.util

from .compat import IS_WINDOWS


def _backend():
    """A libusb backend, bundling one on Windows.

    Linux has libusb as a system library, so pyusb finds it by itself. Windows
    has no system libusb, and pyusb loads it through ctypes at runtime -- which
    PyInstaller cannot see, so a frozen build ships without it and every call
    fails with NoBackendError. libusb-package carries the DLL for exactly this.
    """
    if not IS_WINDOWS:
        return None                     # pyusb's own discovery is fine
    try:
        import libusb_package
        return libusb_package.get_libusb1_backend()
    except Exception:
        return None                     # fall back to pyusb's discovery

VID, PID, BOOTLOADER_PID = 0x1E71, 0x3012, 0x3011
HID_INTERFACE = 1
BULK_INTERFACE = 0
BULK_ENDPOINT = 0x02


class DeviceError(RuntimeError):
    """Something is wrong with the device or how it is attached."""


class NoBackend(DeviceError):
    """libusb is not available, so no USB device can be reached at all."""


class BootloaderMode(DeviceError):
    """The cooler is in recovery mode and must not be touched."""


class NotFound(DeviceError):
    """No Kraken on the bus."""


def find_usb():
    """The pyusb device handle, or raise with an explanation."""
    kwargs = {}
    backend = _backend()
    if backend is not None:
        kwargs['backend'] = backend
    try:
        if usb.core.find(idVendor=VID, idProduct=BOOTLOADER_PID, **kwargs) is not None:
            raise BootloaderMode(
                'Kraken is in BOOTLOADER mode (1e71:3011). It needs a full power cut '
                '(shut down, switch the PSU off ~30s); refusing to touch it. '
                'See docs/TROUBLESHOOTING.md.')
        dev = usb.core.find(idVendor=VID, idProduct=PID, **kwargs)
    except usb.core.NoBackendError as exc:
        raise NoBackend(
            'no USB backend (libusb) is available, so the cooler cannot be '
            'reached. On Windows this means the bundled libusb-1.0.dll is '
            'missing from the install folder; on Linux, install libusb-1.0.'
        ) from exc
    if dev is None:
        raise NotFound(
            f'Kraken {VID:04x}:{PID:04x} not found. Is it plugged into an '
            'internal USB 2.0 header?')
    return dev


class _BulkMixin:
    """Claims interface 0 and writes frame payloads to endpoint 0x02."""

    def _open_bulk(self):
        self.usb = find_usb()
        usb.util.claim_interface(self.usb, BULK_INTERFACE)
        self._claimed = True

    def bulk_write(self, data, timeout=2000):
        self.usb.write(BULK_ENDPOINT, data, timeout=timeout)

    def _close_bulk(self):
        if getattr(self, '_claimed', False):
            try:
                usb.util.release_interface(self.usb, BULK_INTERFACE)
            except Exception:
                pass
            self._claimed = False


class LinuxTransport(_BulkMixin):
    """hidraw for the HID interface, libusb for the bulk one."""

    def __init__(self, hidraw=None):
        import select
        self._select = select
        self._open_bulk()
        self.fd = os.open(hidraw or self._find_hidraw(), os.O_RDWR)

    @staticmethod
    def _find_hidraw():
        for node in sorted(os.listdir('/sys/class/hidraw')):
            try:
                uevent = open(f'/sys/class/hidraw/{node}/device/uevent').read()
            except OSError:
                continue
            if f'{VID:04X}' in uevent.upper() and f'{PID:04X}' in uevent.upper():
                return f'/dev/{node}'
        raise NotFound('no hidraw node for the Kraken (is this running as root?)')

    def hid_write(self, data):
        os.write(self.fd, data)

    def hid_read(self, size=64, timeout=0.0):
        ready, _, _ = self._select.select([self.fd], [], [], timeout)
        if not ready:
            return None
        try:
            return os.read(self.fd, size)
        except BlockingIOError:
            return None

    def hid_drain(self):
        while self.hid_read(timeout=0) is not None:
            pass

    def close(self):
        self._close_bulk()
        try:
            os.close(self.fd)
        except OSError:
            pass


class WindowsTransport(_BulkMixin):
    """hidapi for the HID interface, libusb/WinUSB for the bulk one.

    Interface 1 keeps the native Windows HID driver, so hidapi reaches it
    without any driver work. Interface 0 is vendor specific and has no inbox
    driver, so WinUSB must be bound to *that interface only* -- binding the
    whole device would take the HID interface away too.
    """

    def __init__(self, hidraw=None):
        try:
            import hid
        except ImportError as exc:                      # pragma: no cover
            raise DeviceError(
                'the "hid" package (hidapi) is required on Windows: '
                'pip install hid') from exc
        self._hid_module = hid
        self._open_bulk()
        self.dev = self._open_hid()

    def _open_hid(self):
        hid = self._hid_module
        # Pick interface 1 explicitly: the device publishes two, and opening by
        # VID/PID alone can land on the wrong one.
        path = None
        for info in hid.enumerate(VID, PID):
            if info.get('interface_number') in (HID_INTERFACE, -1):
                path = info['path']
                if info.get('interface_number') == HID_INTERFACE:
                    break
        if path is None:
            raise NotFound(
                'Kraken HID interface not found. The cooler is on USB but its '
                'HID interface is not available -- if you bound WinUSB to the '
                'whole device, re-bind it to interface 0 only. See docs/WINDOWS.md.')
        device = hid.device()
        device.open_path(path)
        device.set_nonblocking(True)
        return device

    def hid_write(self, data):
        self.dev.write(data)

    def hid_read(self, size=64, timeout=0.0):
        # hidapi gives us a non-blocking read; poll it rather than blocking, so
        # the caller's timeout semantics match the Linux select() path.
        deadline = time.monotonic() + timeout
        while True:
            data = self.dev.read(size)
            if data:
                return bytes(data)
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.002)

    def hid_drain(self):
        while self.dev.read(64):
            pass

    def close(self):
        self._close_bulk()
        try:
            self.dev.close()
        except Exception:
            pass


def open_transport(hidraw=None):
    """The right transport for this platform."""
    if IS_WINDOWS:
        return WindowsTransport(hidraw)
    return LinuxTransport(hidraw)


def diagnose():
    """Human-readable report on whether the device can be driven here.

    Written for Windows, where a failure is almost always driver binding rather
    than a bug, and the fix depends on which half is broken.
    """
    lines = []
    ok = True

    try:
        usb_dev = find_usb()
        lines.append(f'[ok]   found Kraken {VID:04x}:{PID:04x} on USB')
    except DeviceError as exc:
        # Every "cannot reach the device" case lands here: bootloader mode, no
        # cooler attached, or no libusb at all. Report it rather than traceback;
        # this command exists for exactly these situations.
        return False, [f'[FAIL] {exc}']

    try:
        cfg = usb_dev.get_active_configuration()
        names = {i.bInterfaceNumber: i.bInterfaceClass for i in cfg}
        lines.append(f'[ok]   interfaces present: {sorted(names)}')
    except Exception as exc:
        lines.append(f'[warn] could not read the configuration: {exc}')

    try:
        usb.util.claim_interface(usb_dev, BULK_INTERFACE)
        usb.util.release_interface(usb_dev, BULK_INTERFACE)
        lines.append('[ok]   interface 0 (bulk) can be claimed')
    except Exception as exc:
        busy = getattr(exc, 'errno', None) == 16 or 'busy' in str(exc).lower()
        if busy:
            # Expected while the service is up: it holds the interface, and
            # only one process may. That is the design, not a fault.
            lines.append('[ok]   interface 0 (bulk) is held by another process '
                         '- almost certainly the Kraken Unleashed service')
            lines.append('       Stop it first if you meant to test a direct claim.')
        else:
            ok = False
            lines.append(f'[FAIL] cannot claim interface 0 (bulk): {exc}')
            if IS_WINDOWS:
                lines.append('       Bind WinUSB to INTERFACE 0 ONLY with Zadig.')
                lines.append('       See docs/WINDOWS.md for the exact steps.')
            else:
                lines.append('       Are you running as root?')

    if IS_WINDOWS:
        try:
            import hid
            found = [i for i in hid.enumerate(VID, PID)]
            if found:
                lines.append(f'[ok]   hidapi sees {len(found)} HID interface(s)')
            else:
                ok = False
                lines.append('[FAIL] hidapi cannot see the HID interface.')
                lines.append('       If you bound WinUSB to the whole device, '
                             're-bind it to interface 0 only.')
        except ImportError:
            ok = False
            lines.append('[FAIL] the "hid" package is not installed')
    else:
        try:
            LinuxTransport._find_hidraw()
            lines.append('[ok]   hidraw node present')
        except NotFound as exc:
            ok = False
            lines.append(f'[FAIL] {exc}')

    return ok, lines
