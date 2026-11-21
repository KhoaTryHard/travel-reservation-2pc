"""Process manager (W06): start/stop/restart the three 2PC services as owned children.

Implements the ``LifecycleManager`` protocol of ``dashboard_api``:

    snapshot() -> {name: {state, managed, pid, error, ...}}   (fast, never blocks on control)
    control(name, action) -> record                            (start / stop / restart)

Rules (docs/DASHBOARD_SPEC.md, section 5 and 8):
* Only children created here with ``Popen(shell=False)`` are ever controlled. argv, ports and
  DB paths come from a fixed service map decided by the server, never from the browser.
* A busy port that we do not own is reported as 409 ``port_busy``; that process is never
  adopted or killed.
* Restart reuses the same DB file. This module never deletes or resets data.
* stdout/stderr go to a log file per service, so a full pipe can never block a child.
* A child that dies (for example the chaos hook ``os._exit(86)``) is reported as ``failed``.
  It is NOT restarted automatically, so the demo operator can observe the crash first.
Standard library only.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from common import RpcError, rpc
from dashboard_api import LifecycleError

HOST = '127.0.0.1'
NAMES = ('coordinator', 'airline', 'hotel')
ACTIONS = ('start', 'stop', 'restart')
# name -> (server.py role, default port, database file inside the data directory)
SERVICES = {
    'coordinator': ('coordinator', 8000, 'coordinator.db'),
    'airline': ('participant', 8001, 'airline.db'),
    'hotel': ('participant', 8002, 'hotel.db'),
}
START_TIMEOUT = 10.0   # seconds to wait for a correct /health after Popen
STOP_TIMEOUT = 5.0     # seconds to wait after terminate() before kill()
ADDRESS_IN_USE = ('address already in use', 'winerror 10048', 'winerror 10013', 'eaddrinuse')


class ProcessManager:
    def __init__(self, root: str | Path | None = None, data_dir: str | Path | None = None,
                 log_dir: str | Path | None = None, ports: dict | None = None,
                 python: str | None = None, start_timeout: float = START_TIMEOUT,
                 stop_timeout: float = STOP_TIMEOUT, enable_chaos: bool = True) -> None:
        self.root = Path(root or Path(__file__).resolve().parent)
        self.data_dir = Path(data_dir or self.root / 'data')
        self.log_dir = Path(log_dir or self.root / 'logs')
        self.python = python or sys.executable          # same interpreter as the dashboard
        self.start_timeout, self.stop_timeout = start_timeout, stop_timeout
        self.enable_chaos = enable_chaos
        self.ports = {name: SERVICES[name][1] for name in NAMES}
        if ports:
            if set(ports) != set(NAMES):
                raise ValueError('ports must define exactly the three fixed services')
            self.ports.update(ports)
        self._control_locks = {name: threading.Lock() for name in NAMES}  # one control() per node
        self._mu = threading.Lock()                                       # guards _state only
        self._state = {name: {'proc': None, 'state': 'stopped', 'error': None, 'log': None}
                       for name in NAMES}

    # ------------------------------------------------------------------ helpers
    def _url(self, name: str) -> str:
        return f'http://{HOST}:{self.ports[name]}'

    def _argv(self, name: str) -> list[str]:
        role, _, db_file = SERVICES[name]
        argv = [self.python, str(self.root / 'server.py'), role,
                '--port', str(self.ports[name]), '--db', str(self.data_dir / db_file)]
        if name == 'coordinator':
            argv += ['--airline', self._url('airline'), '--hotel', self._url('hotel')]
        if self.enable_chaos:
            argv.append('--enable-chaos')
        return argv

    def _port_busy(self, name: str) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            return sock.connect_ex((HOST, self.ports[name])) == 0

    def _log_path(self, name: str) -> Path:
        return self.log_dir / f'{name}.log'

    def _log_tail(self, name: str, size: int = 400) -> str:
        try:
            data = self._log_path(name).read_bytes()[-size:]
        except OSError:
            return ''
        return data.decode('utf-8', 'replace').strip()

    def _refresh(self, name: str) -> None:
        """Detect a child that exited on its own (crash). Caller must not hold ``_mu``."""
        with self._mu:
            entry = self._state[name]
            proc = entry['proc']
            if proc is not None and proc.poll() is not None and entry['state'] == 'running':
                entry.update(state='failed', proc=None,
                             error=f'Child exited unexpectedly with code {proc.returncode}')
                self._close_log(entry)

    @staticmethod
    def _close_log(entry: dict) -> None:
        handle, entry['log'] = entry.get('log'), None
        if handle is not None:
            handle.close()

    def _record(self, name: str) -> dict:
        with self._mu:
            entry = self._state[name]
            proc = entry['proc']
            return {'name': name, 'state': entry['state'], 'managed': True,
                    'pid': proc.pid if proc is not None and proc.poll() is None else None,
                    'error': entry['error'], 'port': self.ports[name],
                    'db': str(self.data_dir / SERVICES[name][2]),
                    'log': str(self._log_path(name))}

    def _set(self, name: str, **fields) -> None:
        with self._mu:
            self._state[name].update(fields)

    # --------------------------------------------------------------- public API
    def snapshot(self) -> dict[str, dict]:
        """Fast read for /api/overview; never waits for a start/stop in progress."""
        for name in NAMES:
            self._refresh(name)
        return {name: self._record(name) for name in NAMES}

    def control(self, name: str, action: str) -> dict:
        if name not in NAMES or action not in ACTIONS:
            raise LifecycleError(404, 'not_found', 'Unknown service/action')
        with self._control_locks[name]:                  # serialize lifecycle per node
            self._refresh(name)
            if action in ('stop', 'restart'):
                self._stop(name)
            if action in ('start', 'restart'):
                self._start(name)
            return self._record(name)

    def close_all(self) -> None:
        """Cleanup on shutdown: stop only the children we own. DB files are kept."""
        for name in NAMES:
            with self._control_locks[name]:
                try:
                    self._stop(name)
                except Exception:                        # keep cleaning the other nodes
                    pass

    def __enter__(self) -> 'ProcessManager':
        return self

    def __exit__(self, *exc) -> None:
        self.close_all()

    # ------------------------------------------------------------------ internals
    def _start(self, name: str) -> None:
        with self._mu:
            proc = self._state[name]['proc']
        if proc is not None and proc.poll() is None:
            return                                       # already running: no second child
        if self._port_busy(name):                        # not ours: never adopt or kill it
            raise LifecycleError(409, 'port_busy',
                                 f'Port {self.ports[name]} is used by a process this manager '
                                 'does not own')
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log = open(self._log_path(name), 'ab')
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        self._set(name, state='starting', error=None, log=log)
        try:
            proc = subprocess.Popen(self._argv(name), cwd=self.root, shell=False,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    creationflags=flags)
        except OSError as exc:
            self._fail(name, f'Cannot launch child: {exc}')
            raise LifecycleError(502, 'child_failed', f'Cannot launch child: {exc}') from exc
        self._set(name, proc=proc)
        role = SERVICES[name][0]
        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:                  # child died before becoming healthy
                tail = self._log_tail(name)
                message = f'Child exited with code {proc.returncode} while starting'
                self._fail(name, message)
                if any(text in tail.lower() for text in ADDRESS_IN_USE):
                    raise LifecycleError(409, 'port_busy', f'Port {self.ports[name]} is busy')
                raise LifecycleError(502, 'child_failed', f'{message}: {tail}')
            try:
                health = rpc(self._url(name), 'health', {}, timeout=0.5)
                if health.get('ok') is True and health.get('role') == role:
                    self._set(name, state='running', error=None)
                    return
            except RpcError:
                pass
            time.sleep(0.1)
        self._terminate(proc)                            # our own child only
        self._fail(name, f'No healthy response within {self.start_timeout:g} seconds')
        raise LifecycleError(504, 'start_timeout',
                             f'{name} did not become healthy in {self.start_timeout:g} seconds')

    def _stop(self, name: str) -> None:
        with self._mu:
            proc = self._state[name]['proc']
        if proc is None:                                 # nothing owned: harmless no-op
            self._set(name, state='stopped', error=None)
            return
        self._set(name, state='stopping')
        self._terminate(proc)
        with self._mu:
            entry = self._state[name]
            entry.update(state='stopped', proc=None, error=None)
            self._close_log(entry)

    def _terminate(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(self.stop_timeout)
            except subprocess.TimeoutExpired:
                proc.kill()                              # forced, but only our own child
                proc.wait(self.stop_timeout)

    def _fail(self, name: str, message: str) -> None:
        with self._mu:
            entry = self._state[name]
            entry.update(state='failed', proc=None, error=message)
            self._close_log(entry)

