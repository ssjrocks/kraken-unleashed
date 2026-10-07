"""Shared test setup.

Everything here runs without a Kraken attached: the device layer is only
imported, never opened. Tests that would need hardware do not exist, by design
-- if a test needs the cooler it cannot run in CI, and a test that cannot run in
CI does not protect anything.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """A config path that is not the real /etc one."""
    path = tmp_path / 'kraken-unleashed.conf'
    monkeypatch.setenv('KRAKEN_UNLEASHED_CONFIG', str(path))
    return path
