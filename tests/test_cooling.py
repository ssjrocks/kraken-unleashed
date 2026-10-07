"""Pump and fan curves.

Two of these exist because the bug happened: the coolant failsafe used to leak
into host-sensor curves (pinning any CPU above 59 C to 100%), and an invalid
curve used to reach the config file and then fail silently on every start.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

from kraken_unleashed import cooling


# --------------------------------------------------------------------------- #
# Wire format
# --------------------------------------------------------------------------- #

def test_packet_shape():
    packet = cooling.packet('pump', cooling.QUIET_CURVES['pump'])
    assert len(packet) == 65, 'report id plus a 64-byte payload'
    assert packet[0] == 0x00, 'leading HID report id'
    assert packet[1] == cooling.SET_COOLING


@pytest.mark.parametrize('channel,cid', [
    ('pump', (0x01, 0x01, 0x00)),
    ('fan', (0x02, 0x01, 0x01)),
])
def test_channel_ids_match_liquidctl(channel, cid):
    """These come from liquidctl's kraken3 driver for this exact device."""
    packet = cooling.packet(channel, cooling.QUIET_CURVES[channel])
    assert tuple(packet[2:5]) == cid


def test_forty_duty_values_one_per_degree():
    duties = cooling.duties('fan', cooling.QUIET_CURVES['fan'])
    assert len(duties) == 40
    assert len(cooling.TEMPERATURES) == 40
    assert cooling.TEMPERATURES[0] == 20
    assert cooling.TEMPERATURES[-1] == 59


def test_unknown_channel_rejected():
    with pytest.raises(ValueError):
        cooling.packet('radiator', [[20, 50]])


# --------------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------------- #

def test_pump_floor_is_enforced():
    """The 20% floor is the firmware's; asking for less must not get through."""
    duties = cooling.duties('pump', [[20, 0], [50, 0]])
    assert min(duties) >= 20


def test_fan_may_be_zero():
    assert cooling.duties('fan', [[20, 0], [50, 0]])[0] == 0


def test_failsafe_always_reaches_full_by_critical():
    for points in ([[20, 25]], [[20, 0], [50, 10]], [[20, 100]]):
        for channel in ('pump', 'fan'):
            assert cooling.duties(channel, points)[-1] == 100, points


def test_full_mode_is_full_everywhere():
    curves = cooling.curves_for('full', {})
    for channel in ('pump', 'fan'):
        assert set(cooling.duties(channel, curves[channel])) == {100}


def test_firmware_mode_writes_nothing():
    assert cooling.curves_for('firmware', {}) is None


# --------------------------------------------------------------------------- #
# Curve maths
# --------------------------------------------------------------------------- #

def test_normalize_sorts_and_makes_monotonic():
    out = cooling.normalize([[40, 60], [25, 30], [40, 55], [30, 45]])
    temps = [t for t, _ in out]
    duties = [d for _, d in out]
    assert temps == sorted(temps)
    assert duties == sorted(duties), 'duty must never decrease as it gets hotter'


def test_interpolation_is_linear_between_points():
    points = cooling.normalize([[20, 20], [40, 60]])
    assert cooling.interpolate(points, 30) == 40


def test_duty_at_does_not_apply_the_coolant_failsafe():
    """Regression: 59 C is a hot loop but an ordinary CPU.

    The failsafe belongs to the curve uploaded to the device. Applying it while
    reading a CPU curve pinned everything above 59 C to 100%.
    """
    cpu_curve = [[40, 25], [55, 35], [70, 70], [85, 100]]
    assert cooling.duty_at(cpu_curve, 62) == pytest.approx(51, abs=1)
    assert cooling.duty_at(cpu_curve, 60) < 100


def test_uploaded_flat_curve_still_carries_the_failsafe():
    """The counterpart: what goes to the device must still fail safe."""
    duties = cooling.duties('fan', cooling.flat(35))
    assert duties[0] == 35
    assert duties[-1] == 100


def test_retarget_keeps_duties_and_moves_temperatures():
    liquid = [[20, 35], [35, 40], [45, 70], [50, 90]]
    cpu = cooling.retarget(liquid, 'cpu')
    assert [t for t, _ in cpu] == list(cooling.SENSOR_POINTS['cpu'])
    assert [d for _, d in cpu] == [35, 40, 70, 90]


def test_quiet_preset_is_quiet_at_idle():
    """Fitted to a measured cooler: about 39% pump and 26% fan at 32 C."""
    assert cooling.duties('pump', cooling.QUIET_CURVES['pump'])[12] == pytest.approx(39, abs=2)
    assert cooling.duties('fan', cooling.QUIET_CURVES['fan'])[12] == pytest.approx(26, abs=2)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def test_a_curve_given_as_a_string_is_rejected():
    """Regression: this used to be persisted and then fail on every start."""
    with pytest.raises(ValueError, match='not a string'):
        cooling.validate_points('[[20,40],[50,100]]')


@pytest.mark.parametrize('bad', [
    [],                       # no points
    [[20]],                   # not a pair
    [[20, 40, 60]],           # too many
    [['a', 'b']],             # not numbers
    [[20, 150]],              # duty out of range
    [[-5, 40]],               # temperature out of range
])
def test_malformed_curves_are_rejected(bad):
    with pytest.raises(ValueError):
        cooling.validate_points(bad)


def test_validate_checks_mode_and_sensor():
    cooling.validate({'mode': 'curve', 'sensor': 'cpu'})         # fine
    with pytest.raises(ValueError, match='mode'):
        cooling.validate({'mode': 'turbo'})
    with pytest.raises(ValueError, match='sensor'):
        cooling.validate({'mode': 'curve', 'sensor': 'ambient'})


def test_every_mode_and_sensor_is_usable():
    for mode in cooling.MODES:
        curves = cooling.curves_for(mode, {'pump': [[20, 50]], 'fan': [[20, 30]]})
        if curves is None:
            continue
        for channel in ('pump', 'fan'):
            assert len(cooling.duties(channel, curves[channel])) == 40
    for sensor in cooling.SENSORS:
        assert len(cooling.SENSOR_POINTS[sensor]) == 4
