"""Load concurrency and manager adapter tests; doubles are not lifecycle acceptance."""
import json
import threading
import time
import unittest

from test_dashboard_api import FakeRPC, URLS
from dashboard_api import ApiError, DashboardAPI, LifecycleError, create_http_server


class ManagerDouble:
    def __init__(self):
        self.called = []

    def snapshot(self):
        return {name: {'state': 'stopped', 'managed': True, 'pid': None, 'error': None}
                for name in URLS}

    def control(self, name, action):
        self.called.append((name, action))
        return {'state': 'running', 'managed': True, 'pid': 123, 'error': None}


class OperationTests(unittest.TestCase):
    def test_lifecycle_adapter_and_missing_manager(self):
        api = DashboardAPI(URLS, transport=FakeRPC())
        with self.assertRaises(ApiError) as cm:
            api.service('hotel', 'start')
        self.assertEqual(cm.exception.status, 503)
        manager = ManagerDouble()
        api = DashboardAPI(URLS, transport=FakeRPC(), manager=manager)
        self.assertEqual(api.service('hotel', 'start')['state'], 'running')
        self.assertEqual(manager.called, [('hotel', 'start')])
        self.assertTrue(api.overview()['nodes']['hotel']['lifecycle']['managed'])
        for name, action in [('external', 'start'), ('hotel', 'kill')]:
            with self.assertRaises(ApiError) as cm:
                api.service(name, action)
            self.assertEqual(cm.exception.status, 404)
        def busy(name, action):
            raise LifecycleError(409, 'port_busy', 'External process owns port')
        manager.control = busy
        with self.assertRaises(ApiError) as cm:
            api.service('hotel', 'start')
        self.assertEqual(cm.exception.status, 409)

    def test_failed_manager_does_not_erase_healthy_rpc_data(self):
        manager = ManagerDouble()
        def broken():
            raise RuntimeError('Manager unavailable')
        manager.snapshot = broken
        api = DashboardAPI(URLS, transport=FakeRPC(), manager=manager)
        node = api.overview()['nodes']['hotel']
        self.assertFalse(node['stale'])
        self.assertIsNotNone(node['snapshot'])
        self.assertEqual(node['lifecycle']['state'], 'failed')

    def test_load_is_background_bounded_single_run_and_unique_tokens(self):
        api = DashboardAPI(URLS, transport=FakeRPC())
        gate = threading.Event()
        tokens = []
        lock = threading.Lock()
        def book(data):
            with lock:
                tokens.append(data['token'])
            gate.wait(3)
            if data['token'].endswith(':0'):
                raise ApiError(502, 'unavailable', 'Lost response', token=data['token'], outcome_unknown=True)
            return {'state': 'COMMITTED', 'tx': data['token']}
        api.book = book
        try:
            run_id = api.start_load({'count': 10, 'workers': 4})['run_id']
            self.assertEqual(api.load_status(run_id)['state'], 'running')
            with self.assertRaises(ApiError) as cm:
                api.start_load({'count': 1})
            self.assertEqual(cm.exception.status, 409)
            # Read endpoint remains available while workers are waiting.
            self.assertIn('nodes', api.overview())
        finally:
            gate.set()
        result = self.wait_load(api, run_id)
        self.assertEqual(result['processed'], 10)
        self.assertEqual(len(set(tokens)), 10)
        unknown = [row for row in result['results'] if row.get('outcome_unknown')]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(len(result['results']), 10)

    @staticmethod
    def wait_load(api, run_id):
        until = time.monotonic() + 10
        while time.monotonic() < until:
            result = api.load_status(run_id)
            if result['state'] == 'complete':
                return result
            time.sleep(.01)
        raise AssertionError('Load did not finish')

    def test_load_validation_and_retention(self):
        api = DashboardAPI(URLS, transport=FakeRPC())
        for data in ({'count': 0}, {'count': 51}, {'workers': 9}, {'workers': 0},
                     {'count': True}, {'count': 1.5}, {'workers': '4'}):
            with self.assertRaises(ApiError) as cm:
                api.start_load(data)
            self.assertEqual(cm.exception.status, 400)
        ids = []
        for _ in range(21):
            run_id = api.start_load({'count': 1, 'workers': 1})['run_id']
            ids.append(run_id)
            self.wait_load(api, run_id)
        with self.assertRaises(ApiError) as cm:
            api.load_status(ids[0])
        self.assertEqual(cm.exception.status, 404)
        self.assertEqual(api.load_status(ids[-1])['processed'], 1)


class RealHTTPLoadTests(unittest.TestCase):
    def test_http_booking_fault_load_and_missing_manager(self):
        import test_system
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        cluster = test_system.SystemTests('test_success_and_duplicate_end')
        cluster.setUp()
        server = None
        try:
            api = DashboardAPI(cluster.urls)
            server = create_http_server(api, 0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = 'http://127.0.0.1:' + str(server.server_port)
            def request(path, data=None, token=''):
                body = None if data is None else json.dumps(data).encode()
                req = Request(base + path, data=body, headers={
                    'Content-Type': 'application/json', 'X-Session-Token': token})
                try:
                    response = urlopen(req, timeout=35)
                except HTTPError as exc:
                    response = exc
                with response:
                    return response.status, json.load(response)
            token = request('/api/session')[1]['session_token']
            payload = {'token': 'http-book', 'airline': 'DEFAULT', 'hotel': 'DEFAULT'}
            status, first = request('/api/bookings', payload, token)
            self.assertEqual(status, 200)
            second = request('/api/bookings', payload, token)[1]
            self.assertEqual(first['tx'], second['tx'])
            self.assertEqual(request('/api/services/hotel/restart', {}, token)[0], 503)
            self.assertEqual(request('/api/faults', {
                'service': 'hotel', 'point': 'before_prepare', 'mode': 'delay', 'seconds': 0}, token)[0], 200)
            status, run = request('/api/load', {'count': 10, 'workers': 4}, token)
            self.assertEqual(status, 202)
            result = OperationTests.wait_load(api, run['run_id'])
            status, via_http = request('/api/load/' + run['run_id'])
            self.assertEqual(status, 200)
            self.assertEqual(via_http['processed'], 10)
            committed = sum(row.get('result', {}).get('state') == 'COMMITTED' for row in result['results'])
            self.assertLessEqual(committed, 4)
            self.assertEqual(cluster.capacity('hotel'), 4 - committed)
            self.assertEqual(cluster.capacity('airline'), 4 - committed)
        finally:
            if server:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)
            cluster.tearDown()


if __name__ == '__main__':
    unittest.main()
