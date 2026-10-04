"""Unix-socket control server: the daemon's API for the GUI and the CLI.

Newline-delimited JSON, one request per line, one reply per line:

    -> {"cmd": "status"}
    <- {"ok": true, "device": {...}, "led": {...}, "openrgb": {...}}

The daemon is the only process that may hold the cooler, so everything else --
the GUI, scripts, anything later -- goes through here rather than opening the
device. Keeping that boundary is what stops a second writer corrupting the
status reads the LCD loop depends on.
"""
import grp
import json
import os
import socket
import threading
import traceback


class ControlServer(threading.Thread):
    """Serves the control socket. One short-lived thread per connection."""

    daemon = True

    def __init__(self, path, handlers, group=None, mode=0o660):
        super().__init__(daemon=True)
        self.path = path
        self.handlers = handlers
        self.group = group
        self.mode = mode
        self.sock = None
        self.stop_requested = False
        self.error = None

    def _bind(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # A socket left behind by a crash would make bind() fail with EADDRINUSE.
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.path)
        sock.listen(8)
        sock.settimeout(0.5)
        # The daemon runs as root; the GUI does not. Hand the socket to a group
        # the desktop user is in rather than making it world-writable.
        if self.group:
            try:
                os.chown(self.path, 0, grp.getgrnam(self.group).gr_gid)
            except (KeyError, OSError) as exc:
                self.error = f'could not chown socket to group {self.group!r}: {exc}'
        os.chmod(self.path, self.mode)
        return sock

    def run(self):
        try:
            self.sock = self._bind()
        except OSError as exc:
            self.error = f'cannot bind {self.path}: {exc}'
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
            result = {'ok': False, 'error': str(exc), # take down the daemon
                      'traceback': traceback.format_exc()}
        return json.dumps(result, default=str).encode()

    def stop(self):
        """Stop serving and remove the socket file.

        This runs the cleanup on the caller's thread rather than leaving it to
        the server thread. That thread is a daemon thread, so on shutdown the
        interpreter kills it before it reaches its own cleanup and the socket
        file survives -- harmless, since bind() unlinks a stale one, but it
        leaves litter in /run and makes "is it running?" ambiguous.
        """
        self.stop_requested = True
        self._cleanup()

    def _cleanup(self):
        try:
            self.sock.close()
        except (OSError, AttributeError):
            pass
        try:
            os.unlink(self.path)
        except OSError:
            pass


class Client:
    """Minimal client. Used by the GUI and by `kraken-unleashed-ctl`."""

    def __init__(self, path='/run/kraken-unleashed/control.sock', timeout=5.0):
        self.path = path
        self.timeout = timeout

    def call(self, cmd, **kwargs):
        payload = dict(kwargs, cmd=cmd)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.path)
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
