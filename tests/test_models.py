"""The supported-cooler table.

Mostly guarding honesty: exactly one model has been verified against hardware,
and the table must keep saying so rather than drifting into implying the rest
are supported.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

from kraken_unleashed import cooling, models


def test_the_verified_model_is_the_one_we_tested():
    verified = [m for m in models.MODELS.values() if m.tested]
    assert [m.pid for m in verified] == [0x3012], (
        'only the Kraken 2024 Elite has been confirmed on hardware; '
        'do not mark others tested without running them')


def test_every_model_is_complete():
    for model in models.MODELS.values():
        assert model.name
        assert model.lcd is None or len(model.lcd) == 2
        assert set(model.speed) == {'pump', 'fan'}
        for channel, (cid, dmin, dmax) in model.speed.items():
            assert len(cid) == 3, f'{model.name} {channel} channel id'
            assert 0 <= dmin < dmax <= 100


def test_pump_always_has_a_floor():
    """The 20% minimum is the firmware's and must not be relaxed per model."""
    for model in models.MODELS.values():
        assert model.speed['pump'][1] == 20, model.name


def test_default_model_is_the_verified_one():
    assert models.DEFAULT.pid == 0x3012
    assert models.DEFAULT.tested


def test_bootloader_id_is_not_a_model():
    """It must never be opened as a normal device."""
    assert models.BOOTLOADER_PID not in models.MODELS


def test_usb_ids_render_as_lsusb_does():
    assert models.MODELS[0x3012].usb_id == '1e71:3012'


def test_lookup():
    assert models.get(0x3012).name == 'Kraken 2024 Elite RGB'
    assert models.get(0xDEAD) is None


def test_support_listing_marks_untested_models():
    text = '\n'.join(models.describe_support())
    assert 'verified' in text
    assert 'untested' in text
    for model in models.MODELS.values():
        assert model.usb_id in text


def test_cooling_accepts_each_model_channel_layout():
    """A Z3's channel ids differ from the 2024 family; both must encode."""
    for model in models.MODELS.values():
        for channel in ('pump', 'fan'):
            packet = cooling.packet(channel, [[20, 40], [50, 100]], model.speed)
            assert len(packet) == 65
            assert packet[1] == cooling.SET_COOLING
            assert tuple(packet[2:5]) == model.speed[channel][0]


def test_z3_and_2024_really_do_differ():
    """If these ever match, one of the tables has been copied wrongly."""
    z3 = models.MODELS[0x3008].speed['fan'][0]
    modern = models.MODELS[0x3012].speed['fan'][0]
    assert z3 != modern
