"""Control server: the daemon's API for the GUI and the CLI.

Newline-delimited JSON, one request per line, one reply per line:

    -> {"cmd": "status"}
    <- {"ok": true, "device": {...}, "led": {...}, "openrgb": {...}}

The daemon is the only process that may hold the cooler, so everything else --
the GUI, scripts, anything later -- goes through here rather than opening the
device. Keeping that boundary is what stops a second writer corrupting the
status reads the LCD loop depends on.

On Linux the endpoint is a Unix socket, and access is controlled by its group
and mode. Windows Python's AF_UNIX support is patchy, so there it is a loopback
TCP port guarded by a token file -- binding to 127.0.0.1 is not an access
control on its own, since any process on the machine can connect to it.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import secrets
import socket
import threading
import traceback

from .compat import IS_WINDOWS, token_path

try:
    import grp
except ImportError:                     # Windows
    grp = None


def is_tcp(endpoint):
    """True for a "host:port" endpoint rather than a filesystem socket path."""
    return os.sep not in endpoint and ':' in endpoint


def parse_endpoint(endpoint):
    host, _, port = endpoint.rpartition(':')
    return host or '127.0.0.1', int(port)


def read_token():
    try:
        with open(token_path()) as handle:
            return handle.read().strip()
    except OSError:
        return None


class ControlServer(threading.Thread):
    """Serves the control endpoint. One short-lived thread per connection."""

    daemon = True

    def __init__(self, endpoint, handlers, group=None, mode=0o660):
        super().__init__(daemon=True)
        self.endpoint = endpoint
        self.handlers = handlers
        self.group = group
        self.mode = mode
        self.tcp = is_tcp(endpoint)
        self.token = None
        self.sock = None
        self.stop_requested = False
        self.error = None

    # -- binding ------------------------------------------------------------ #

    def _bind_tcp(self):
        host, port = parse_endpoint(self.endpoint)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(8)
        # Loopback is reachable by every process on the machine, so prove the
        # caller can read a file only this account can read.
        self.token = secrets.token_hex(32)
        path = token_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as handle:
            handle.write(self.token)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return sock

    def _bind_unix(self):
        os.makedirs(os.path.dirname(self.endpoint), exist_ok=True)
        # A socket left behind by a crash would make bind() fail with EADDRINUSE.
        try:
            os.unlink(self.endpoint)
        except FileNotFoundError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.endpoint)
        sock.listen(8)
        # The daemon runs as root; the GUI does not. Hand the socket to a group
        # the desktop user is in rather than making it world-writable.
        if self.group and grp is not None:
            try:
                os.chown(self.endpoint, 0, grp.getgrnam(self.group).gr_gid)
            except (KeyError, OSError) as exc:
                self.error = f'could not chown socket to group {self.group!r}: {exc}'
        os.chmod(self.endpoint, self.mode)
        return sock

    def run(self):
        try:
            self.sock = self._bind_tcp() if self.tcp else self._bind_unix()
            self.sock.settimeout(0.5)
        except OSError as exc:
            self.error = f'cannot bind {self.endpoint}: {exc}'
            return
        while not self.stop_requested:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()
        self._cleanup()

    # -- serving ------------------------------------------------------------ #

    def _serve(self, conn):
        try:
            conn.settimeout(10.0)
            with conn.makefile('rwb') as stream:
                for line in stream:
                    line = line.strip()
                    if not line:
                        continue
                    stream.write(self._dispatch(line) + b'\n')
                    stream.flush()
        except (OSError, ValueError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _dispatch(self, line):
        try:
            request = json.loads(line)
            cmd = request.get('cmd')
        except ValueError as exc:
            return json.dumps({'ok': False, 'error': f'bad JSON: {exc}'}).encode()
        if self.token and not secrets.compare_digest(
                str(request.get('token', '')), self.token):
            return json.dumps({'ok': False, 'error': 'bad or missing token'}).encode()
        handler = self.handlers.get(cmd)
        if handler is None:
            return json.dumps({
                'ok': False,
                'error': f'unknown command {cmd!r}',
                'commands': sorted(self.handlers),
            }).encode()
        try:
            result = handler(request) or {}
            result.setdefault('ok', True)
        except Exception as exc:                      # a handler bug must not
            result = {'ok': False, 'error': str(exc),  # take down the daemon
                      'traceback': traceback.format_exc()}
        return json.dumps(result, default=str).encode()

    # -- shutdown ----------------------------------------------------------- #

    def stop(self):
        """Stop serving and clean up.

        The cleanup runs on the caller's thread rather than being left to the
        server thread. That thread is a daemon thread, so on shutdown the
        interpreter kills it before it reaches its own cleanup and the socket
        file survives -- harmless, since bind() unlinks a stale one, but it
        leaves litter and makes "is it running?" ambiguous.
        """
        self.stop_requested = True
        self._cleanup()

    def _cleanup(self):
        try:
            self.sock.close()
        except (OSError, AttributeError):
            pass
        if self.tcp:
            try:
                os.unlink(token_path())
            except OSError:
                pass
        else:
            try:
                os.unlink(self.endpoint)
            except OSError:
                pass


class Client:
    """Minimal client. Used by the GUI and by kraken-unleashed-ctl."""

    def __init__(self, endpoint=None, timeout=5.0):
        from .compat import control_endpoint
        self.endpoint = endpoint or control_endpoint()
        self.timeout = timeout
        self.tcp = is_tcp(self.endpoint)

    def _connect(self):
        if self.tcp:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect(parse_endpoint(self.endpoint))
        else:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect(self.endpoint)
        return sock

    def call(self, cmd, **kwargs):
        payload = dict(kwargs, cmd=cmd)
        if self.tcp:
            token = read_token()
            if token:
                payload['token'] = token
        sock = self._connect()
        try:
            with sock.makefile('rwb') as stream:
                stream.write(json.dumps(payload).encode() + b'\n')
                stream.flush()
                line = stream.readline()
        finally:
            sock.close()
        if not line:
            raise ConnectionError('daemon closed the connection without replying')
        return json.loads(line)

    @property
    def available(self):
        try:
            return bool(self.call('status').get('ok'))
        except (OSError, ValueError, ConnectionError):
            return False
