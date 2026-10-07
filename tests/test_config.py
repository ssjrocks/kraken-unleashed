"""Config loading, migration and saving.

The migration test matters most: someone upgrading from 1.x should keep the
screen they set up, not have it quietly reset to the demo background.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os

import pytest

from kraken_unleashed import config as cfg


def test_defaults_are_complete_and_safe():
    conf = cfg.defaults()
    for section in ('lcd', 'led', 'cooling', 'openrgb', 'control'):
        assert section in conf
    # Defaults must not take over anything the user has not asked for.
    assert conf['cooling']['mode'] == 'firmware', 'must not manage cooling by default'
    assert conf['openrgb']['enabled'] is False, 'must not open a network port by default'


def test_missing_file_is_not_an_error(tmp_path):
    conf = cfg.load(str(tmp_path / 'nope.conf'))
    assert conf['lcd']['style'] == 'triple'


def test_empty_file_means_defaults(tmp_path):
    """Regression: an empty config used to abort with a JSON error."""
    path = tmp_path / 'empty.conf'
    path.write_text('')
    assert cfg.load(str(path))['lcd']['style'] == 'triple'


def test_comment_lines_are_stripped(tmp_path):
    path = tmp_path / 'commented.conf'
    path.write_text('// a note\n{\n  "lcd": {"dim": 0.7}\n}\n')
    assert cfg.load(str(path))['lcd']['dim'] == 0.7


def test_malformed_json_is_reported_not_ignored(tmp_path):
    path = tmp_path / 'bad.conf'
    path.write_text('{ this is not json ')
    with pytest.raises(ValueError):
        cfg.load(str(path))


def test_migration_from_1x_keeps_the_display(tmp_path):
    """The 1.x shape had LCD keys at the top level and a flat led block."""
    path = tmp_path / 'kraken-lcd.conf'
    path.write_text(json.dumps({
        'gif': '/home/someone/loop.gif',
        'style': 'cpu_gpu',
        'dim': 0.55,
        'ring': 'FF0066',
        'fps': 10,
        'rotate': 180,
        'led': {'enabled': True, 'color': '00AAFF', 'period': 3.0,
                'follow': '/etc/rgb-sync.json'},
    }))
    conf = cfg.load(str(path))
    assert conf['lcd']['gif'] == '/home/someone/loop.gif'
    assert conf['lcd']['style'] == 'cpu_gpu'
    assert conf['lcd']['dim'] == 0.55
    assert conf['lcd']['rotate'] == 180
    assert conf['lcd']['fps'] == 10
    assert conf['led']['params']['color'] == '00AAFF'
    assert conf['led']['params']['period'] == 3.0
    assert conf['led']['follow'] == '/etc/rgb-sync.json'
    assert conf['led']['effect'] == 'breathing'


def test_current_shape_is_left_alone(tmp_path):
    path = tmp_path / 'current.conf'
    path.write_text(json.dumps({'lcd': {'dim': 0.9}, 'cooling': {'mode': 'full'}}))
    conf = cfg.load(str(path))
    assert conf['lcd']['dim'] == 0.9
    assert conf['cooling']['mode'] == 'full'
    assert conf['lcd']['style'] == 'triple', 'absent keys still get defaults'


def test_save_is_atomic_and_round_trips(tmp_path):
    path = tmp_path / 'out.conf'
    conf = cfg.defaults()
    conf['lcd']['dim'] = 0.33
    cfg.save(conf, str(path))
    assert json.loads(path.read_text())['lcd']['dim'] == 0.33
    # No temporary files left behind by the rename dance.
    assert [p.name for p in tmp_path.iterdir()] == ['out.conf']


def test_save_leaves_the_old_file_intact_on_failure(tmp_path):
    path = tmp_path / 'out.conf'
    cfg.save(cfg.defaults(), str(path))
    before = path.read_text()
    with pytest.raises(TypeError):
        cfg.save({'lcd': {object(): 1}}, str(path))   # not serialisable
    assert path.read_text() == before


def test_deep_update_merges_rather_than_replaces():
    base = {'lcd': {'dim': 0.4, 'style': 'triple'}}
    cfg.deep_update(base, {'lcd': {'dim': 0.8}})
    assert base['lcd'] == {'dim': 0.8, 'style': 'triple'}


def test_led_params_follow_the_shared_file(tmp_path):
    """How the cooler stays in phase with rgb-sync."""
    shared = tmp_path / 'rgb-sync.json'
    shared.write_text(json.dumps({'color': '112233', 'period': 9.0}))
    conf = cfg.defaults()
    conf['led']['follow'] = str(shared)
    params = cfg.led_params(conf)
    assert params['color'] == '112233'
    assert params['period'] == 9.0


def test_led_params_survive_a_missing_shared_file(tmp_path):
    conf = cfg.defaults()
    conf['led']['follow'] = str(tmp_path / 'absent.json')
    assert cfg.led_params(conf)['color'] == conf['led']['params']['color']
