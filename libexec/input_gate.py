"""Required keyboard gate control. The launcher owns this controller, not omarchy-shell.

The native filter is authoritative: focus epochs prevent delayed authorization
from surviving a leave/re-enter race, and a short lease closes input if this
controller stops. None of these helpers focus a window or synthesize keys.
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
import time
from pathlib import Path

LEASE_NS = 750_000_000
FRESH_NS = 400_000_000
RESTORE_WINDOW = 5.0  # seconds to keep retrying host recovery after losing a capture
RESTORE_INTERVAL = .5


def native_status(directory: Path, pid: int | None = None) -> dict:
    try:
        state = json.loads((directory / 'lease.status').read_text())
        age = time.monotonic_ns() - int(state['updatedNs'])
        if (state['protocol'] != 1 or not 0 <= age < FRESH_NS
                or not state['keyboards'] or (pid is not None and state['pid'] != pid)):
            raise ValueError('stale or incompatible input gate')
        return state
    except (OSError, ValueError, KeyError, TypeError):
        return {'state': 'unavailable'}


def request(directory: Path, action: str, timeout: float = 3) -> dict:
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(timeout)
        client.connect(str(directory / 'control.sock'))
        client.sendall((action + '\n').encode())
        data = b''
        while b'\n' not in data:
            chunk = client.recv(4096)
            if not chunk:
                raise RuntimeError('input gate controller disconnected')
            data += chunk
            if len(data) > 8192:
                raise RuntimeError('invalid gate response')
    result = json.loads(data)
    if not result.get('ok'):
        raise RuntimeError(result.get('error', 'input gate request failed'))
    return result


class Controller:
    """session() returns the nest PID; host() returns (focused PID, submap).

    Capture is accepted only over the private control socket after the guarded
    CLI has focused this nest and selected the nested submap. The native gate
    additionally binds the request to the observed keyboard focus epoch.
    """
    def __init__(self, directory: Path, session, host, restore_host=None):
        self.directory, self.session, self.host = directory, session, host
        self.restore_host = restore_host
        self.next_restore = 0
        self.restore_until = 0
        self.generation = 0
        self.epoch = 0
        self.pid = 0
        self.deadline = 0
        self.stop_event = threading.Event()
        self.thread = None
        self.listener = None

    def status(self):
        pid = self.session()
        return native_status(self.directory, int(pid)) if pid else {'state': 'unavailable'}

    def host_matches(self):
        pid = self.session()
        focused, submap = self.host()
        return bool(pid) and str(focused) == str(pid) and submap == 'nested'

    def revoke(self):
        self.generation = 0
        self.deadline = 0
        (self.directory / 'lease').unlink(missing_ok=True)

    def renew(self):
        self.deadline = time.monotonic_ns() + LEASE_NS
        tmp = self.directory / 'lease.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(f'{self.generation} {self.deadline} {self.epoch} {self.pid}\n')
        tmp.replace(self.directory / 'lease')

    def release(self):
        self.revoke()
        until = time.monotonic() + 1.5
        while time.monotonic() < until:
            state = self.status()
            if state['state'] == 'uncaptured' and state.get('generation') == 0 and state.get('forwarded') == 0:
                return state
            if not self.session():
                return {'state': 'uncaptured'}  # compositor no longer exists
            time.sleep(.02)
        raise RuntimeError('gate did not acknowledge release; host routing was not changed')

    def capture(self):
        self.revoke()
        until = time.monotonic() + 1
        while time.monotonic() < until:
            state = self.status()
            if state['state'] != 'unavailable' and state.get('entered'):
                break
            time.sleep(.02)
        else:
            raise RuntimeError('gate is not ready or the nest does not have keyboard focus')
        if not self.host_matches():
            raise RuntimeError('capture requires this nest to be focused in the nested submap')
        self.generation = time.monotonic_ns()
        self.epoch = state['focusEpoch']
        self.pid = state['pid']
        self.renew()
        until = time.monotonic() + .5
        while time.monotonic() < until:
            state = self.status()
            if (state.get('generation') == self.generation
                    and state['state'] in ('captured', 'pending') and self.host_matches()):
                return state
            time.sleep(.02)
        self.revoke()
        raise RuntimeError('capture was revoked or not acknowledged')

    def heartbeat(self):
        if not self.generation:
            self.restore_idle_host()
            return
        matches = self.host_matches()
        state = self.status()
        if (time.monotonic_ns() >= self.deadline or not matches
                or state.get('generation') != self.generation
                or state['state'] not in ('captured', 'pending')
                or state.get('focusEpoch') != self.epoch or state.get('pid') != self.pid):
            self.lose()
            self.restore_idle_host()
        else:
            self.renew()

    def lose(self):
        """Revoke a capture that ended without an explicit release, and arm host
        recovery: the nested submap would otherwise keep swallowing host keys."""
        self.revoke()
        if self.restore_host is not None:
            self.restore_until = time.monotonic() + RESTORE_WINDOW
            self.next_restore = 0

    def tick(self):
        try:
            self.heartbeat()
        except Exception:
            # A failed check while captured is a lost capture, too.
            if self.generation:
                self.lose()
            else:
                self.revoke()

    def restore_idle_host(self):
        # Revocation closes native input, but does not reset the host submap.
        # Retry briefly: acknowledgement may arrive on a later native tick.
        # Only armed after losing a capture, so idle nests never poll the host.
        # The callback checks every gate under the capture lock, without asking
        # this controller's socket (which would deadlock its own serve thread).
        now = time.monotonic()
        if self.restore_host is None or now >= self.restore_until or now < self.next_restore:
            return
        self.next_restore = now + RESTORE_INTERVAL
        if self.restore_host():
            self.restore_until = 0

    def start(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise RuntimeError('input gate directory must not be a symlink')
        self.revoke()
        sock = self.directory / 'control.sock'
        sock.unlink(missing_ok=True)
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(str(sock))
        os.chmod(sock, 0o600)
        self.listener.listen(8)
        self.listener.settimeout(.1)
        self.thread = threading.Thread(target=self.serve, name='omadev-input-gate', daemon=True)
        self.thread.start()
        return self

    def serve(self):
        try:
            while not self.stop_event.is_set():
                try:
                    client, _ = self.listener.accept()
                except socket.timeout:
                    client = None
                if client is not None:
                    with client:
                        client.settimeout(.5)
                        try:
                            data = b''
                            while b'\n' not in data and len(data) < 64:
                                chunk = client.recv(64)
                                if not chunk:
                                    break
                                data += chunk
                            action = data.decode().strip()
                            if action == 'capture':
                                state = self.capture()
                            elif action == 'release':
                                state = self.release()
                            elif action == 'status':
                                state = self.status()
                            else:
                                raise RuntimeError('unknown input gate action')
                            response = {'ok': True, **state}
                        except Exception as error:
                            self.revoke()
                            response = {'ok': False, 'error': str(error)}
                        with contextlib.suppress(OSError):
                            client.sendall((json.dumps(response) + '\n').encode())
                self.tick()
        finally:
            self.revoke()
            self.listener.close()
            (self.directory / 'control.sock').unlink(missing_ok=True)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=4)
        self.revoke()
