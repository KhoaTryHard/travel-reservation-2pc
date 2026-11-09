"""Dashboard contract tests. Test doubles do not prove child lifecycle or browser UI."""
import json
import sys
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard_api import ApiError, DashboardAPI, UpstreamError, create_http_server


URLS = {name: 'http://' + name for name in ('coordinator', 'airline', 'hotel')}


class FakeRPC:
    def __init__(self):
        self.failed = set()
        self.calls = []

    def __call__(self, url, action, data, timeout):
        name = url.removeprefix('http://')
        self.calls.append((name, action, data, timeout))
        if name in self.failed:
            raise UpstreamError('unavailable', 'Node unavailable')
        if action == 'health':
            return {'ok': True, 'role': 'coordinator' if name == 'coordinator' else 'participant'}
        if action == 'snapshot':
            if name == 'coordinator':
                return {'transactions': [{'tx': 't1', 'token': 'b1', 'state': 'COMMITTED',
                                         'payload': '{"airline":"DEFAULT","hotel":"DEFAULT"}',
                                         'ack': '["airline"]'}], 'events': []}
            return {'inventory': [{'resource': 'DEFAULT', 'available': 4}],
                    'reservations': [{'tx': 't1', 'resource': 'DEFAULT', 'state': 'PREPARED'}],
                    'locks': [{'tx': 't1', 'resource': 'DEFAULT'}], 'events': []}
        if action in ('begin', 'end'):
            return {'tx': 't1', 'token': 'b1', 'payload': {'airline': 'DEFAULT', 'hotel': 'DEFAULT'},
                    'state': 'COMMITTED', 'ack': ['airline', 'hotel'], 'complete': True}
        raise AssertionError(action)


class APITests(unittest.TestCase):
    def setUp(self):
        self.rpc = FakeRPC()
        self.api = DashboardAPI(URLS, transport=self.rpc)

    def test_snapshot_normalizes_json_and_ack_without_mutating_source(self):
        result = self.api.overview()
        row = result['nodes']['coordinator']['snapshot']['transactions'][0]
        self.assertIsInstance(row['payload'], dict)
        self.assertEqual(row['ack'], ['airline'])
        self.assertFalse(row['complete'])

    def test_partial_failure_keeps_stale_data_and_never_invents_zero(self):
        self.api.overview()
        self.rpc.failed.add('hotel')
        nodes = self.api.overview()['nodes']
        self.assertTrue(nodes['hotel']['stale'])
        self.assertEqual(nodes['hotel']['snapshot']['inventory'][0]['available'], 4)
        self.assertFalse(nodes['airline']['stale'])
        fresh_api = DashboardAPI(URLS, transport=self.rpc)
        self.assertIsNone(fresh_api.overview()['nodes']['hotel']['snapshot'])

    def test_unknown_tx_requires_fresh_coordinator(self):
        with self.assertRaises(ApiError) as cm:
            self.api.transaction('missing')
        self.assertEqual(cm.exception.status, 404)
        self.api.overview()
        self.rpc.failed.add('coordinator')
        with self.assertRaises(ApiError) as cm:
            self.api.transaction('t1')
        self.assertEqual(cm.exception.status, 502)
        self.assertTrue(cm.exception.extra['nodes']['coordinator']['stale'])

    def test_booking_uses_same_token_and_one_total_deadline(self):
        payload = {'token': 'b1', 'airline': 'DEFAULT', 'hotel': 'DEFAULT'}
        result = self.api.book(payload)
        self.assertTrue(result['complete'])
        self.assertEqual(self.rpc.calls[0][2], payload)
        self.assertEqual(self.rpc.calls[1][2], {'tx': 't1'})
        self.assertLessEqual(self.rpc.calls[1][3], self.rpc.calls[0][3])
        for bad in ({}, dict(payload, token=''), dict(payload, hotel=True)):
            with self.assertRaises(ApiError) as cm:
                self.api.book(bad)
            self.assertEqual(cm.exception.status, 400)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.api = DashboardAPI(URLS, transport=FakeRPC())
        self.server = create_http_server(self.api, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.token = self.request('/api/session')[1]['session_token']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, data=None, headers=None, raw=None):
        body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        headers = {'Content-Type': 'application/json', **(headers or {})}
        req = Request(self.base + path, data=body, headers=headers)
        try:
            response = urlopen(req, timeout=3)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_host_origin_and_session_checked_before_mutation(self):
        booking = {'token': 'b1', 'airline': 'DEFAULT', 'hotel': 'DEFAULT'}
        self.assertEqual(self.request('/api/session', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/api/bookings', booking)[0], 403)
        headers = {'X-Session-Token': self.token, 'Origin': 'https://evil.example'}
        self.assertEqual(self.request('/api/bookings', booking, headers)[0], 403)
        headers['Origin'] = self.base
        self.assertEqual(self.request('/api/bookings', booking, headers)[0], 200)

    def test_invalid_json_non_object_and_request_size(self):
        headers = {'X-Session-Token': self.token}
        for raw in (b'{', b'[]', b'null'):
            self.assertEqual(self.request('/api/bookings', headers=headers, raw=raw)[0], 400)
        self.assertEqual(self.request('/api/bookings', headers=headers, raw=b' ' * 65537)[0], 413)
        self.assertEqual(self.request('/api/missing')[0], 404)


class RealRPCAPITests(unittest.TestCase):
    """Real RPC children and separate temporary DBs; no lifecycle manager or UI."""
    def setUp(self):
        import test_system
        self.cluster = test_system.SystemTests('test_success_and_duplicate_end')
        self.cluster.setUp()
        self.api = DashboardAPI(self.cluster.urls)

    def tearDown(self):
        self.cluster.tearDown()

    def test_booking_retry_and_detail_use_real_rpc_data(self):
        payload = {'token': 'web-success', 'airline': 'DEFAULT', 'hotel': 'DEFAULT'}
        first = self.api.book(payload)
        second = self.api.book(payload)
        self.assertEqual(first['tx'], second['tx'])
        self.assertTrue(second['complete'])
        detail = self.api.transaction(first['tx'])
        self.assertEqual(detail['transaction']['state'], 'COMMITTED')
        self.assertEqual(self.cluster.capacity('airline'), 4)
        self.assertEqual(self.cluster.capacity('hotel'), 4)
        payload['hotel'] = 'MISSING'
        with self.assertRaises(ApiError) as cm:
            self.api.book(payload)
        self.assertEqual(cm.exception.status, 409)

    def test_abort_and_partial_failure_keep_correct_inventory(self):
        result = self.api.book({'token': 'web-abort', 'airline': 'DEFAULT', 'hotel': 'MISSING'})
        self.assertEqual(result['state'], 'ABORTED')
        self.assertEqual(self.cluster.capacity('airline'), 5)
        self.api.overview()
        self.cluster.stop('hotel')
        nodes = self.api.overview()['nodes']
        self.assertTrue(nodes['hotel']['stale'])
        self.assertEqual(nodes['hotel']['snapshot']['inventory'][0]['available'], 5)
        self.assertFalse(nodes['coordinator']['stale'])


if __name__ == '__main__':
    unittest.main()
