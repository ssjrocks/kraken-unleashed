"""Platform differences, in one place.

Linux is the reference platform. Windows support is newer and marked
experimental; everything that differs between them lives here rather than being
sprinkled through the daemon as `if sys.platform` checks.
"""
import os
import sys
import time

IS_WINDOWS = sys.platform == 'win32'
IS_LINUX = sys.platform.startswith('linux')


def boot_clock():
    """Seconds since boot, identical in every process on this machine.

    This is what keeps effects in phase between independent processes -- the
    daemon and rgb-sync compute the same curve from it with nothing passing
    between them. Linux has CLOCK_BOOTTIME for exactly this. On Windows,
    time.monotonic() is already a since-boot counter shared by every process,
    so it has the same property; it is not merely "some monotonic clock".
    """
    if IS_WINDOWS:
        return time.monotonic()
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def _appdata():
    return os.environ.get('PROGRAMDATA', r'C:\ProgramData')


def config_path():
    """Where the config lives, honouring an explicit override first."""
    override = os.environ.get('KRAKEN_UNLEASHED_CONFIG')
    if override:
        return override
    if IS_WINDOWS:
        return os.path.join(_appdata(), 'KrakenUnleashed', 'config.json')
    return '/etc/kraken-unleashed.conf'


def legacy_config_path():
    return None if IS_WINDOWS else '/etc/kraken-lcd.conf'


def runtime_dir():
    """Directory for the control endpoint, status file and preview image."""
    if IS_WINDOWS:
        return os.path.join(_appdata(), 'KrakenUnleashed', 'run')
    return '/run/kraken-unleashed'


def status_paths():
    """Every path the live reading is published to.

    The second Linux path is kept because exporters already read it; dropping
    it would quietly break someone's dashboard on upgrade.
    """
    if IS_WINDOWS:
        return (os.path.join(runtime_dir(), 'status.json'),)
    return ('/run/kraken-unleashed/status.json', '/run/kraken-lcd/status.json')


def preview_path():
    return os.path.join(runtime_dir(), 'preview.png')


def control_endpoint():
    """How the GUI/CLI reaches the daemon.

    A Unix socket on Linux, filesystem permissions and all. Windows Python's
    AF_UNIX support is patchy, so there it is a loopback TCP port paired with a
    token file -- loopback alone is not an access control, since any process on
    the machine can connect to it.
    """
    override = os.environ.get('KRAKEN_UNLEASHED_SOCKET')
    if override:
        return override
    if IS_WINDOWS:
        return '127.0.0.1:48931'
    return os.path.join(runtime_dir(), 'control.sock')


def token_path():
    return os.path.join(runtime_dir(), 'token')


def is_elevated():
    """True when we can plausibly open the device (root, or Windows admin)."""
    if IS_WINDOWS:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0
