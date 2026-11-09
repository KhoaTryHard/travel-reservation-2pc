"""Local dashboard API, independent of business processes; standard library only."""
from __future__ import annotations

import copy
import json
import math
import secrets
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Protocol
from urllib.error import URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

NAMES = ('coordinator', 'airline', 'hotel')
DEFAULT_URLS = {name: f'http://127.0.0.1:{8000 + i}' for i, name in enumerate(NAMES)}
FAULT_POINTS = {
    'coordinator': ('after_votes', 'after_decision', 'after_ack_airline'),
    'airline': ('before_prepare', 'after_prepare', 'before_commit', 'after_commit'),
    'hotel': ('before_prepare', 'after_prepare', 'before_commit', 'after_commit'),
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.extra = status, code, extra

    def body(self) -> dict:
        return {'error': {'code': self.code, 'message': str(self)}, **self.extra}


class UpstreamError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class LifecycleError(ApiError):
    """Expected manager failure; status/code/message cross the HTTP boundary."""


class LifecycleManager(Protocol):
    def snapshot(self) -> dict[str, dict]: ...

    def control(self, name: str, action: str) -> dict: ...


def rpc_transport(url: str, action: str, data: dict, timeout: float) -> dict:
    request = Request(url + '/' + action, json.dumps(data).encode(),
                      {'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except (TimeoutError, socket.timeout) as exc:
        raise UpstreamError('timeout', str(exc)) from exc
    except URLError as exc:
        code = 'timeout' if isinstance(exc.reason, (TimeoutError, socket.timeout)) else 'unavailable'
        raise UpstreamError(code, str(exc.reason)) from exc
    except (OSError, ValueError) as exc:
        raise UpstreamError('protocol_error', str(exc)) from exc
    if not isinstance(result, dict):
        raise UpstreamError('protocol_error', 'RPC response must be an object')
    if 'error' in result:
        raise UpstreamError('rpc_error', str(result['error']))
    return result


def text_field(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ApiError(400, 'invalid_input', f'{key} must be a nonempty string, max 128 characters')
    return value


def normalized_transaction(row: dict) -> dict:
    row = copy.deepcopy(row)
    for key in ('payload', 'ack'):
        if isinstance(row.get(key), str):
            row[key] = json.loads(row[key])
    if not isinstance(row.get('payload'), dict) or not isinstance(row.get('ack'), list):
        raise ValueError('Invalid transaction payload/ack')
    if (len(set(row['ack'])) != len(row['ack'])
            or any(name not in ('airline', 'hotel') for name in row['ack'])):
        raise ValueError('Invalid participant ACK')
    if row.get('state') not in ('ACTIVE', 'PREPARING', 'COMMITTED', 'ABORTED'):
        raise ValueError('Invalid transaction state')
    row['complete'] = set(row['ack']) == {'airline', 'hotel'}
    return row


class DashboardAPI:
    def __init__(self, urls: dict | None = None,
                 transport: Callable = rpc_transport,
                 manager: LifecycleManager | None = None) -> None:
        self.urls = dict(DEFAULT_URLS if urls is None else urls)
        if set(self.urls) != set(NAMES):
            raise ValueError('Exactly three fixed services required')
        self.transport = transport
        self.cache: dict[str, dict] = {}
        self.node_locks = {name: threading.Lock() for name in NAMES}
        self.manager = manager
        self.loads: dict[str, dict] = {}
        self.load_lock = threading.Lock()
        self.active_load: str | None = None

    def _call(self, name: str, action: str, data: dict, deadline: float) -> dict:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UpstreamError('timeout', 'Upstream deadline exceeded')
        return self.transport(self.urls[name], action, data, timeout=remaining)

    def _node(self, name: str) -> dict:
        with self.node_locks[name]:
            deadline = time.monotonic() + 2
            try:
                health = self._call(name, 'health', {}, deadline)
                expected_role = 'coordinator' if name == 'coordinator' else 'participant'
                if health.get('ok') is not True or health.get('role') != expected_role:
                    raise UpstreamError('protocol_error', 'Unexpected service health/role')
                snapshot = copy.deepcopy(self._call(name, 'snapshot', {}, deadline))
                required = ('transactions', 'events') if name == 'coordinator' else (
                    'inventory', 'reservations', 'locks', 'events')
                if any(not isinstance(snapshot.get(key), list) for key in required):
                    raise ValueError('Invalid snapshot lists')
                if any(not isinstance(row, dict) for key in required for row in snapshot[key]):
                    raise ValueError('Invalid snapshot rows')
                if name == 'coordinator':
                    snapshot['transactions'] = [normalized_transaction(row)
                                                for row in snapshot['transactions']]
                result = {'health': health, 'snapshot': snapshot,
                          'last_success_at': datetime.now(timezone.utc).isoformat(),
                          'stale': False, 'error': None}
                self.cache[name] = copy.deepcopy(result)
            except (UpstreamError, ValueError, TypeError, KeyError) as exc:
                result = copy.deepcopy(self.cache.get(name, {
                    'health': None, 'snapshot': None, 'last_success_at': None}))
                result.update(stale=True, error={
                    'code': exc.code if isinstance(exc, UpstreamError) else 'protocol_error',
                    'message': str(exc)})
            result['lifecycle'] = {'state': 'unmanaged', 'managed': False}
            return result

    def overview(self) -> dict:
        with ThreadPoolExecutor(max_workers=3) as pool:
            nodes = dict(zip(NAMES, pool.map(self._node, NAMES)))
        if self.manager is not None:
            try:
                lifecycle = self.manager.snapshot()
                for name in NAMES:
                    if not isinstance(lifecycle[name], dict):
                        raise ValueError('Invalid lifecycle record')
                for name in NAMES:
                    nodes[name]['lifecycle'] = copy.deepcopy(lifecycle[name])
            except Exception as exc:
                for node in nodes.values():
                    node['lifecycle'] = {'state': 'failed', 'managed': False,
                                         'error': str(exc), 'pid': None}
        return {'nodes': nodes}

    def transaction(self, tx: str) -> dict:
        text_field({'tx': tx}, 'tx')
        nodes = self.overview()['nodes']
        transaction = None
        for name, node in nodes.items():
            snap = node['snapshot']
            if snap is None:
                continue
            keys = ('transactions', 'events') if name == 'coordinator' else (
                'reservations', 'locks', 'events')
            for key in keys:
                snap[key] = [row for row in snap[key] if row.get('tx') == tx]
            if name == 'coordinator' and snap['transactions']:
                transaction = snap['transactions'][0]
        result = {'tx': tx, 'transaction': transaction, 'nodes': nodes}
        if nodes['coordinator']['stale']:
            error = nodes['coordinator']['error']
            raise ApiError(504 if error['code'] == 'timeout' else 502,
                           error['code'], error['message'], **result)
        if transaction is None:
            raise ApiError(404, 'not_found', 'Unknown transaction', **result)
        return result

    def book(self, data: dict) -> dict:
        payload = {key: text_field(data, key) for key in ('token', 'airline', 'hotel')}
        deadline = time.monotonic() + 30
        tx = None
        try:
            begun = self._call('coordinator', 'begin', payload, deadline)
            tx = begun['tx']
            result = normalized_transaction(self._call('coordinator', 'end', {'tx': tx}, deadline))
            return result
        except UpstreamError as exc:
            if exc.code == 'rpc_error' and str(exc) == 'Idempotency token reused with different payload':
                raise ApiError(409, 'token_conflict', str(exc), token=payload['token']) from exc
            raise ApiError(504 if exc.code == 'timeout' else 502, exc.code, str(exc),
                           token=payload['token'], payload={k: payload[k] for k in ('airline', 'hotel')},
                           tx=tx, outcome_unknown=True) from exc

        except (KeyError, ValueError, TypeError) as exc:
            raise ApiError(502, 'protocol_error', str(exc), token=payload['token'],
                           payload={k: payload[k] for k in ('airline', 'hotel')},
                           tx=tx, outcome_unknown=True) from exc

    def fault(self, data: dict) -> dict:
        name = data.get('service')
        if not isinstance(name, str) or name not in NAMES:
            raise ApiError(404, 'not_found', 'Unknown service')
        point, mode, seconds = data.get('point'), data.get('mode'), data.get('seconds', 3)
        if point not in FAULT_POINTS[name] or mode not in ('crash', 'delay'):
            raise ApiError(400, 'invalid_input', 'Invalid fault point or mode for service')
        if (type(seconds) not in (int, float) or not math.isfinite(seconds)
                or not 0 <= seconds <= 30):
            raise ApiError(400, 'invalid_input', 'seconds must be finite, 0..30')
        try:
            return self._call(name, 'fault', {'point': point, 'mode': mode, 'seconds': seconds},
                              time.monotonic() + 2)
        except UpstreamError as exc:
            raise ApiError(504 if exc.code == 'timeout' else 502, exc.code, str(exc)) from exc

    def service(self, name: str, action: str) -> dict:
        if name not in NAMES or action not in ('start', 'stop', 'restart'):
            raise ApiError(404, 'not_found', 'Unknown service/action')
        if self.manager is None:
            raise ApiError(503, 'lifecycle_unavailable', 'Lifecycle manager is not connected')
        try:
            result = self.manager.control(name, action)
            if not isinstance(result, dict):
                raise ValueError('Invalid lifecycle result')
            return result
        except LifecycleError:
            raise
        except Exception as exc:
            raise ApiError(503, 'lifecycle_unavailable', str(exc)) from exc

    def start_load(self, data: dict) -> dict:
        count, workers = data.get('count', 10), data.get('workers', 4)
        if type(count) is not int or not 1 <= count <= 50:
            raise ApiError(400, 'invalid_input', 'count must be an integer, 1..50')
        if type(workers) is not int or not 1 <= workers <= 8:
            raise ApiError(400, 'invalid_input', 'workers must be an integer, 1..8')
        with self.load_lock:
            if self.active_load is not None:
                raise ApiError(409, 'load_busy', 'A load run is already active')
            while len(self.loads) >= 20:
                del self.loads[next(iter(self.loads))]
            run_id = secrets.token_hex(16)
            self.loads[run_id] = {'run_id': run_id, 'state': 'running', 'count': count,
                                  'workers': workers, 'processed': 0, 'results': []}
            self.active_load = run_id
        threading.Thread(target=self._run_load, args=(run_id, count, workers), daemon=True).start()
        return {'run_id': run_id}

    def _run_load(self, run_id: str, count: int, workers: int) -> None:
        def one(index: int) -> None:
            token = f'load:{run_id}:{index}'
            try:
                result = {'token': token, 'result': self.book({
                    'token': token, 'airline': 'DEFAULT', 'hotel': 'DEFAULT'})}
            except ApiError as exc:
                result = {'token': token, **exc.body()}
            except Exception:
                # Never leave the run stuck or silently invent a final decision.
                result = {'token': token, 'outcome_unknown': True,
                          'error': {'code': 'internal_error', 'message': 'Load request failed'}}
            with self.load_lock:
                self.loads[run_id]['results'].append(result)
                self.loads[run_id]['processed'] += 1
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(one, range(count)))
        finally:
            with self.load_lock:
                self.loads[run_id]['state'] = 'complete'
                self.active_load = None

    def load_status(self, run_id: str) -> dict:
        with self.load_lock:
            if run_id not in self.loads:
                raise ApiError(404, 'not_found', 'Unknown load run')
            return copy.deepcopy(self.loads[run_id])

    def dispatch(self, method: str, path: str, data: dict) -> tuple[int, dict]:
        if method == 'GET' and path == '/api/overview':
            return 200, self.overview()
        if method == 'GET' and path.startswith('/api/transactions/'):
            return 200, self.transaction(unquote(path.removeprefix('/api/transactions/')))
        if method == 'POST' and path == '/api/bookings':
            return 200, self.book(data)
        if method == 'POST' and path == '/api/faults':
            return 200, self.fault(data)
        if method == 'POST' and path.startswith('/api/services/'):
            parts = path.split('/')
            if len(parts) == 5:
                return 200, self.service(parts[3], parts[4])
        if method == 'POST' and path == '/api/load':
            return 202, self.start_load(data)
        if method == 'GET' and path.startswith('/api/load/'):
            return 200, self.load_status(path.removeprefix('/api/load/'))
        raise ApiError(404, 'not_found', 'Unknown API route')


def create_http_server(api: DashboardAPI, port: int = 8080) -> ThreadingHTTPServer:
    session_token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, status: int, result: dict) -> None:
            body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
            try:
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _handle(self, method: str) -> None:
            try:
                self.connection.settimeout(5)
                hosts = {f'127.0.0.1:{self.server.server_port}',
                         f'localhost:{self.server.server_port}'}
                host = self.headers.get('Host', '')
                if len(self.headers.get_all('Host', [])) != 1 or host not in hosts:
                    raise ApiError(403, 'invalid_host', 'Invalid Host')
                origin = self.headers.get('Origin')
                if (len(self.headers.get_all('Origin', [])) > 1
                        or origin is not None and origin != 'http://' + host):
                    raise ApiError(403, 'invalid_origin', 'Cross-origin request rejected')
                path = urlsplit(self.path).path
                if method == 'GET' and path == '/api/session':
                    self._respond(200, {'session_token': session_token})
                    return
                data = {}
                if method == 'POST':
                    supplied = self.headers.get('X-Session-Token', '')
                    if (len(self.headers.get_all('X-Session-Token', [])) != 1
                            or not secrets.compare_digest(supplied.encode('utf-8'), session_token.encode('ascii'))):
                        raise ApiError(403, 'invalid_session', 'Session token required')
                    if self.headers.get('Transfer-Encoding'):
                        raise ApiError(400, 'invalid_input', 'Transfer encoding unsupported')
                    lengths = self.headers.get_all('Content-Length', [])
                    if len(lengths) != 1:
                        raise ApiError(400, 'invalid_input', 'Exactly one Content-Length required')
                    try:
                        size = int(lengths[0])
                    except ValueError as exc:
                        raise ApiError(400, 'invalid_input', 'Invalid Content-Length') from exc
                    if not 0 <= size <= 65536:
                        raise ApiError(413, 'request_too_large', 'Request limit is 64 KiB')
                    if self.headers.get_content_type() != 'application/json':
                        raise ApiError(400, 'invalid_input', 'application/json required')
                    try:
                        data = json.loads(self.rfile.read(size))
                    except (ValueError, UnicodeError) as exc:
                        raise ApiError(400, 'invalid_input', 'Invalid JSON') from exc
                    if not isinstance(data, dict):
                        raise ApiError(400, 'invalid_input', 'JSON object required')
                status, result = api.dispatch(method, path, data)
                self._respond(status, result)
            except ApiError as exc:
                self._respond(exc.status, exc.body())
            except (TimeoutError, OSError):
                self._respond(400, ApiError(400, 'invalid_input', 'Incomplete request').body())

        def do_GET(self) -> None:
            self._handle('GET')

        def do_POST(self) -> None:
            self._handle('POST')

        def log_message(self, *args: Any) -> None:
            pass

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)
