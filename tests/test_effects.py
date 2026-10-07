"""The LED effect engine.

Every effect must return exactly as many valid RGB tuples as asked for, for any
time and any parameters -- the daemon feeds the result straight into a 24-LED
packet per zone, so a short or malformed list corrupts the frame.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

import math

import pytest

from kraken_unleashed import effects

PARAMS = {
    'color': '00FF00', 'color2': 'FF0000', 'period': 5.0,
    'min_brightness': 0.0, 'max_brightness': 1.0, 'gamma': 2.2,
    'spread': 1.0, 'tail': 6, 'temp_min': 25.0, 'temp_max': 50.0, 'value': 33.0,
}


@pytest.mark.parametrize('name', sorted(effects.EFFECTS))
@pytest.mark.parametrize('count', [1, 24, 48])
def test_every_effect_returns_valid_colours(name, count):
    for t in (0.0, 0.7, 3.3, 1234.5):
        out = effects.render(name, t, count, PARAMS)
        assert len(out) == count, name
        for colour in out:
            assert len(colour) == 3
            for channel in colour:
                assert isinstance(channel, int)
                assert 0 <= channel <= 255


@pytest.mark.parametrize('name', sorted(effects.EFFECTS))
def test_every_effect_survives_empty_parameters(name):
    """A config missing a key must not take the daemon down."""
    assert len(effects.render(name, 1.0, 48, {})) == 48


@pytest.mark.parametrize('name', sorted(effects.EFFECTS))
def test_declared_parameters_exist(name):
    assert name in effects.PARAMS, f'{name} has no declared parameter list'


def test_unknown_effect_falls_back_rather_than_raising():
    assert len(effects.render('disco-inferno', 1.0, 48, PARAMS)) == 48


def test_effects_are_a_pure_function_of_time():
    """Phase sync with rgb-sync depends on this: same clock, same colour."""
    a = effects.render('breathing', 12.25, 48, PARAMS)
    b = effects.render('breathing', 12.25, 48, PARAMS)
    assert a == b


def test_breathing_is_periodic():
    params = dict(PARAMS, period=5.0)
    assert (effects.render('breathing', 1.0, 4, params)
            == effects.render('breathing', 6.0, 4, params))


def test_breathing_reaches_its_floor_and_peak():
    params = dict(PARAMS, period=4.0, min_brightness=0.0, max_brightness=1.0)
    trough = effects.render('breathing', 0.0, 1, params)[0]
    peak = effects.render('breathing', 2.0, 1, params)[0]
    assert trough == (0, 0, 0)
    assert peak == (0, 255, 0)


def test_off_is_black_and_static_is_the_colour():
    assert effects.render('off', 3.0, 8, PARAMS) == [(0, 0, 0)] * 8
    assert effects.render('static', 3.0, 8, PARAMS) == [(0, 255, 0)] * 8


def test_positional_effects_vary_around_the_ring():
    """rainbow and gradient must differ per LED; breathing must not."""
    assert len(set(effects.render('rainbow', 0.0, 48, PARAMS))) > 10
    assert len(set(effects.render('gradient', 0.0, 48, PARAMS))) > 10
    assert len(set(effects.render('breathing', 0.3, 48, PARAMS))) == 1


def test_chase_lights_a_contiguous_head():
    out = effects.render('chase', 0.0, 48, dict(PARAMS, tail=6))
    lit = [i for i, c in enumerate(out) if c != (0, 0, 0)]
    assert 0 < len(lit) <= 7, 'the comet should be about tail-length long'


def test_temperature_effect_follows_the_reading():
    cold = effects.render('temperature', 0.0, 1, dict(PARAMS, value=25.0))[0]
    hot = effects.render('temperature', 0.0, 1, dict(PARAMS, value=50.0))[0]
    assert cold[2] > cold[0], 'cold should be blue-dominant'
    assert hot[0] > hot[2], 'hot should be red-dominant'


def test_temperature_effect_handles_a_missing_reading():
    out = effects.render('temperature', 0.0, 4, dict(PARAMS, value=None))
    assert len(out) == 4 and all(len(c) == 3 for c in out)


def test_zero_period_does_not_divide_by_zero():
    for name in sorted(effects.EFFECTS):
        assert len(effects.render(name, 1.0, 8, dict(PARAMS, period=0))) == 8
