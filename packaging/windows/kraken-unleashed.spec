# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows build (service + CLI).

Built on a windows-latest GitHub runner; see .github/workflows/windows.yml.
The GUI is deliberately not included -- bundling GTK4/libadwaita on Windows is
a different and much larger problem, and the daemon plus the CLI are what make
the cooler work.

Run from the repository root:
    pyinstaller packaging/windows/kraken-unleashed.spec
"""
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = os.path.abspath(os.getcwd())
SRC = os.path.join(ROOT, 'src')

# The renderer is reached through a sys.path insert, not a normal import, so
# PyInstaller cannot see it by static analysis -- name it explicitly.
hidden = [
    'ok', 'ok.backend', 'ok.backend.lcd_render',
    'kraken_unleashed', 'kraken_unleashed.daemon', 'kraken_unleashed.device',
    'kraken_unleashed.transport', 'kraken_unleashed.effects',
    'kraken_unleashed.sacn', 'kraken_unleashed.control',
    'kraken_unleashed.compositor', 'kraken_unleashed.sensors',
    'kraken_unleashed.config', 'kraken_unleashed.compat',
    'PIL._tkinter_finder', 'hid', 'psutil', 'wmi', 'win32com.client',
    'libusb_package', 'usb.backend.libusb1',
]

datas = [
    (os.path.join(ROOT, 'assets', 'demo.gif'), 'assets'),
    (os.path.join(SRC, 'ok', 'resources'), os.path.join('ok', 'resources')),
]
# pyusb loads libusb through ctypes at runtime, so PyInstaller's static analysis
# never sees the DLL and a frozen build fails with NoBackendError on every call.
# libusb-package carries it; pull in both its data files and the DLL itself.
datas += collect_data_files('libusb_package')
libusb_binaries = collect_dynamic_libs('libusb_package')

daemon_a = Analysis([os.path.join(SRC, 'bin', 'kraken-unleashed-daemon')],
                    pathex=[SRC], binaries=libusb_binaries, datas=datas,
                    hiddenimports=hidden, hookspath=[], runtime_hooks=[],
                    excludes=['tkinter', 'gi', 'matplotlib'], noarchive=False)
ctl_a = Analysis([os.path.join(SRC, 'bin', 'kraken-unleashed-ctl')],
                 pathex=[SRC], binaries=libusb_binaries, datas=datas,
                 hiddenimports=hidden, hookspath=[], runtime_hooks=[],
                 excludes=['tkinter', 'gi', 'matplotlib'], noarchive=False)

MERGE((daemon_a, 'kraken-unleashed-daemon', 'kraken-unleashed-daemon'),
      (ctl_a, 'kraken-unleashed-ctl', 'kraken-unleashed-ctl'))

daemon_pyz = PYZ(daemon_a.pure, daemon_a.zipped_data)
ctl_pyz = PYZ(ctl_a.pure, ctl_a.zipped_data)

daemon_exe = EXE(daemon_pyz, daemon_a.scripts, [], exclude_binaries=True,
                 name='kraken-unleashed-daemon', console=True,
                 icon=os.path.join(ROOT, 'packaging', 'windows', 'app.ico')
                 if os.path.exists(os.path.join(ROOT, 'packaging', 'windows', 'app.ico'))
                 else None)
ctl_exe = EXE(ctl_pyz, ctl_a.scripts, [], exclude_binaries=True,
              name='kraken-unleashed-ctl', console=True)

COLLECT(daemon_exe, daemon_a.binaries, daemon_a.datas,
        ctl_exe, ctl_a.binaries, ctl_a.datas,
        strip=False, upx=False, name='KrakenUnleashed')
