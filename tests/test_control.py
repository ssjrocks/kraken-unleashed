"""The control socket the app and the CLI talk to.

This is the boundary that keeps everything except the daemon away from the
device, so it has to stay reachable, stay well-behaved on bad input, and clean
up after itself.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import socket
import time

import pytest

from kraken_unleashed.control import Client, ControlServer, is_tcp, parse_endpoint


@pytest.fixture
def server(tmp_path):
    path = str(tmp_path / 'control.sock')
    handlers = {
        'ping': lambda r: {'pong': True},
        'echo': lambda r: {'said': r.get('say')},
        'boom': lambda r: 1 / 0,
    }
    srv = ControlServer(path, handlers, mode=0o600)
    srv.start()
    for _ in range(40):
        if os.path.exists(path):
            break
        time.sleep(0.05)
    yield srv, path
    srv.stop()


def test_endpoint_kinds():
    assert is_tcp('127.0.0.1:48931')
    assert not is_tcp('/run/kraken-unleashed/control.sock')
    assert parse_endpoint('127.0.0.1:48931') == ('127.0.0.1', 48931)


def test_round_trip(server):
    _, path = server
    assert Client(path).call('ping') == {'pong': True, 'ok': True}


def test_arguments_are_passed_through(server):
    _, path = server
    assert Client(path).call('echo', say='hello')['said'] == 'hello'


def test_unknown_command_lists_what_exists(server):
    _, path = server
    reply = Client(path).call('nonsense')
    assert reply['ok'] is False
    assert 'ping' in reply['commands']


def test_a_handler_crash_does_not_kill_the_server(server):
    """One bad command must not take the LCD down with it."""
    _, path = server
    reply = Client(path).call('boom')
    assert reply['ok'] is False
    assert 'division' in reply['error'].lower()
    assert Client(path).call('ping')['ok'] is True, 'server survived'


def test_malformed_json_is_answered_not_dropped(server):
    _, path = server
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(path)
    sock.sendall(b'{ not json at all\n')
    reply = json.loads(sock.makefile('rb').readline())
    sock.close()
    assert reply['ok'] is False
    assert 'JSON' in reply['error']


def test_several_requests_on_one_connection(server):
    _, path = server
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(path)
    stream = sock.makefile('rwb')
    for i in range(3):
        stream.write(json.dumps({'cmd': 'echo', 'say': i}).encode() + b'\n')
        stream.flush()
        assert json.loads(stream.readline())['said'] == i
    sock.close()


def test_socket_permissions_are_restrictive(server):
    _, path = server
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_stop_removes_the_socket(tmp_path):
    """Regression: cleanup ran on a daemon thread that never got to finish."""
    path = str(tmp_path / 'gone.sock')
    srv = ControlServer(path, {'ping': lambda r: {}})
    srv.start()
    for _ in range(40):
        if os.path.exists(path):
            break
        time.sleep(0.05)
    assert os.path.exists(path)
    srv.stop()
    assert not os.path.exists(path), 'a stale socket was left in place'


def test_a_stale_socket_does_not_block_binding(tmp_path):
    path = str(tmp_path / 'stale.sock')
    leftover = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    leftover.bind(path)
    leftover.close()
    assert os.path.exists(path)
    srv = ControlServer(path, {'ping': lambda r: {'pong': True}})
    srv.start()
    try:
        for _ in range(40):
            if srv.sock is not None:
                break
            time.sleep(0.05)
        assert srv.error is None, srv.error
        assert Client(path).call('ping')['ok'] is True
    finally:
        srv.stop()


def test_client_reports_an_absent_server(tmp_path):
    assert Client(str(tmp_path / 'nothing.sock')).available is False
