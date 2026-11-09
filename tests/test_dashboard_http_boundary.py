"""HTTP boundary acceptance for W02c; no real lifecycle or browser is implied."""
import json
import threading
import time
import unittest
from http.client import HTTPConnection
from unittest.mock import Mock

from test_dashboard_api import FakeRPC, URLS
from dashboard_api import DashboardAPI, create_http_server


ROUTES = (
    ('/api/bookings', {'token': 'boundary-book', 'airline': 'DEFAULT', 'hotel': 'DEFAULT'}),
    ('/api/faults', {'service': 'hotel', 'point': 'after_prepare', 'mode': 'crash'}),
    ('/api/load', {'count': 1, 'workers': 1}),
    ('/api/services/hotel/start', {}),
    ('/api/services/hotel/stop', {}),
    ('/api/services/hotel/restart', {}),
)


class HTTPBoundaryTests(unittest.TestCase):
    def setUp(self):
        fake_rpc = FakeRPC()

        def transport(url, action, data, timeout):
            if action == 'fault':
                return {'armed': data['point']}
            return fake_rpc(url, action, data, timeout)

        self.rpc = Mock(side_effect=transport)
        self.manager = Mock()
        self.manager.control.return_value = {
            'state': 'running', 'managed': True, 'pid': 123, 'error': None}
        self.api = DashboardAPI(URLS, transport=self.rpc, manager=self.manager)
        self.api.dispatch = Mock(wraps=self.api.dispatch)
        self.api.start_load = Mock(wraps=self.api.start_load)
        self.server = create_http_server(self.api, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server, self.server, self.thread)
        self.host = f'127.0.0.1:{self.server.server_port}'
        self.origin = 'http://' + self.host
        self.token = self.request('/api/session', method='GET',
                                  headers=[('Host', self.host)])[1]['session_token']

    @staticmethod
    def stop_server(server, thread):
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    def headers(self):
        return [('Host', self.host), ('Origin', self.origin),
                ('X-Session-Token', self.token)]

    def request(self, path, data=None, *, method='POST', headers=None,
                body=None, framing=None):
        body = json.dumps(data).encode() if body is None else body
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for key, value in self.headers() if headers is None else headers:
                connection.putheader(key, value)
            if method == 'POST':
                fields = [('Content-Type', 'application/json'),
                          ('Content-Length', str(len(body)))] if framing is None else framing
                for key, value in fields:
                    connection.putheader(key, value)
            connection.endheaders(body if method == 'POST' else None)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def assert_rejected(self, path, payload, headers, code, *, status=403, **kwargs):
        actual, result = self.request(path, payload, headers=headers, **kwargs)
        self.assertEqual(actual, status)
        self.assertEqual(result['error']['code'], code)
        self.api.dispatch.assert_not_called()
        self.rpc.assert_not_called()
        self.manager.control.assert_not_called()
        self.api.start_load.assert_not_called()
        self.assertEqual(self.api.loads, {})
        self.assertIsNone(self.api.active_load)

    def test_invalid_or_duplicate_host_rejects_every_mutation_without_side_effects(self):
        for path, payload in ROUTES:
            for hosts in ([], ['evil.example'], ['127.0.0.1:1'],
                          [self.host, 'evil.example'], ['evil.example', self.host]):
                with self.subTest(path=path, hosts=hosts):
                    headers = [(key, value) for key, value in self.headers() if key != 'Host']
                    headers.extend(('Host', host) for host in hosts)
                    self.assert_rejected(path, payload, headers, 'invalid_host')

    def test_cross_origin_or_duplicate_origin_rejects_every_mutation_without_side_effects(self):
        invalid_origins = (['https://evil.example'], ['null'], [''],
                           ['https://' + self.host], ['http://localhost:' + str(self.server.server_port)],
                           [self.origin + '/'], [self.origin, 'https://evil.example'],
                           ['https://evil.example', self.origin])
        for path, payload in ROUTES:
            for origins in invalid_origins:
                with self.subTest(path=path, origins=origins):
                    headers = [(key, value) for key, value in self.headers() if key != 'Origin']
                    headers.extend(('Origin', origin) for origin in origins)
                    self.assert_rejected(path, payload, headers, 'invalid_origin')

    def test_missing_wrong_or_duplicate_session_rejects_every_mutation_without_side_effects(self):
        invalid_tokens = ([], [''], ['wrong'], ['\u00e9'],
                          [self.token, 'wrong'], ['wrong', self.token],
                          [self.token, self.token])
        for path, payload in ROUTES:
            for tokens in invalid_tokens:
                with self.subTest(path=path, tokens=tokens):
                    headers = [(key, value) for key, value in self.headers() if key != 'X-Session-Token']
                    headers.extend(('X-Session-Token', token) for token in tokens)
                    self.assert_rejected(path, payload, headers, 'invalid_session')

    def test_authentication_precedes_body_parsing_for_every_mutation(self):
        headers = [(key, value) for key, value in self.headers() if key != 'X-Session-Token']
        for path, payload in ROUTES:
            with self.subTest(path=path):
                self.assert_rejected(path, payload, headers, 'invalid_session', body=b'{')

    def test_invalid_body_or_framing_never_dispatches_any_mutation(self):
        cases = (
            (b'{', None, 400, 'invalid_input'),
            (b'[]', None, 400, 'invalid_input'),
            (b'null', None, 400, 'invalid_input'),
            (b' ' * 65537, None, 413, 'request_too_large'),
            (b'{}', [('Content-Type', 'text/plain'), ('Content-Length', '2')], 400, 'invalid_input'),
            (b'{}', [('Content-Type', 'application/json')], 400, 'invalid_input'),
            (b'{}', [('Content-Length', 'bad')], 400, 'invalid_input'),
            (b'{}', [('Content-Length', '2'), ('Content-Length', '2')], 400, 'invalid_input'),
            (b'{}', [('Content-Length', '2'), ('Transfer-Encoding', 'chunked')], 400, 'invalid_input'),
        )
        for path, payload in ROUTES:
            for body, framing, status, code in cases:
                with self.subTest(path=path, framing=framing, status=status, body=body[:8]):
                    self.assert_rejected(path, payload, self.headers(), code, status=status,
                                         body=body, framing=framing)

    def test_valid_requests_reach_rpc_manager_and_background_load(self):
        for path, payload in ROUTES:
            with self.subTest(path=path):
                self.rpc.reset_mock()
                self.manager.control.reset_mock()
                self.api.start_load.reset_mock()
                status, result = self.request(path, payload)
                self.assertEqual(status, 202 if path == '/api/load' else 200)
                if path == '/api/bookings':
                    self.assertEqual([call.args[1] for call in self.rpc.call_args_list], ['begin', 'end'])
                    self.assertEqual(self.rpc.call_args_list[0].args[2], payload)
                    self.assertTrue(result['complete'])
                elif path == '/api/faults':
                    self.assertEqual(self.rpc.call_args.args[:2], (URLS['hotel'], 'fault'))
                    self.assertEqual(result['armed'], 'after_prepare')
                elif path.startswith('/api/services/'):
                    self.manager.control.assert_called_once_with('hotel', path.rsplit('/', 1)[1])
                    self.rpc.assert_not_called()
                else:
                    self.api.start_load.assert_called_once_with(payload)
                    deadline = time.monotonic() + 3
                    while self.api.load_status(result['run_id'])['state'] != 'complete':
                        if time.monotonic() >= deadline:
                            self.fail('Background load did not complete')
                        time.sleep(.01)
                    run = self.api.load_status(result['run_id'])
                    self.assertEqual(run['processed'], 1)
                    self.assertEqual([call.args[1] for call in self.rpc.call_args_list], ['begin', 'end'])

    def test_localhost_and_originless_client_remain_supported(self):
        for origin in (None, 'http://localhost:' + str(self.server.server_port)):
            with self.subTest(origin=origin):
                headers = [('Host', 'localhost:' + str(self.server.server_port)),
                           ('X-Session-Token', self.token)]
                if origin is not None:
                    headers.append(('Origin', origin))
                self.assertEqual(self.request('/api/services/hotel/start', {}, headers=headers)[0], 200)
        self.assertEqual(self.manager.control.call_count, 2)

    def test_session_token_is_bound_to_one_http_server(self):
        other = create_http_server(self.api, port=0)
        worker = threading.Thread(target=other.serve_forever, daemon=True)
        worker.start()
        connection = HTTPConnection('127.0.0.1', other.server_port, timeout=3)
        try:
            connection.request('GET', '/api/session')
            other_token = json.loads(connection.getresponse().read())['session_token']
            self.assertNotEqual(other_token, self.token)
            headers = [(key, value) for key, value in self.headers() if key != 'X-Session-Token']
            headers.append(('X-Session-Token', other_token))
            for path, payload in ROUTES:
                with self.subTest(path=path):
                    self.assert_rejected(path, payload, headers, 'invalid_session')
        finally:
            connection.close()
            other.shutdown()
            other.server_close()
            worker.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
