"""W03b: a disconnected HTTP reader must not replay or interrupt a booking."""
import json
import threading
import unittest
from http.client import HTTPConnection, IncompleteRead, RemoteDisconnected
from pathlib import Path
from unittest.mock import Mock, patch

from dashboard_api import DashboardAPI, create_http_server
from test_dashboard_api import FakeRPC, URLS


class DisconnectTests(unittest.TestCase):
    def setUp(self):
        self.api = DashboardAPI(URLS, transport=FakeRPC())
        original_book = self.api.book
        self.book_results = []

        def book(data):
            result = original_book(data)
            self.book_results.append(result)
            return result

        self.api.book = Mock(side_effect=book)
        self.server = create_http_server(
            self.api, port=0, static_dir=Path(__file__).resolve().parents[1] / 'web')
        self.server.handle_error = Mock()
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_server)
        self.token = json.loads(self.request('/api/session'))['session_token']

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=3)

    def request(self, path, data=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            body = None if data is None else json.dumps(data).encode()
            connection.request('GET' if data is None else 'POST', path, body, {
                'Content-Type': 'application/json', 'X-Session-Token': getattr(self, 'token', '')})
            return connection.getresponse().read()
        finally:
            connection.close()

    def check_disconnect(self, path, data, fail_on_write):
        handler_type = self.server.RequestHandlerClass
        original_setup = handler_type.setup
        original_response = handler_type.send_response
        original_process = self.server.process_request_thread
        completed = threading.Event()
        statuses = []

        class AbortedWriter:
            def __init__(self, writer):
                self.writer = writer
                self.calls = 0

            def write(self, body):
                self.calls += 1
                if self.calls == fail_on_write:
                    raise ConnectionAbortedError('client reloaded during response')
                return self.writer.write(body)

            def __getattr__(self, name):
                return getattr(self.writer, name)

        def setup(handler):
            original_setup(handler)
            handler.wfile = AbortedWriter(handler.wfile)

        def response(handler, status, message=None):
            statuses.append(status)
            return original_response(handler, status, message)

        def process(*args):
            try:
                original_process(*args)
            finally:
                completed.set()

        with patch.object(handler_type, 'setup', setup), \
                patch.object(handler_type, 'send_response', response), \
                patch.object(self.server, 'process_request_thread', process):
            try:
                self.request(path, data)
            except (RemoteDisconnected, IncompleteRead, ConnectionError):
                pass
            self.assertTrue(completed.wait(3), 'Disconnected request did not finish')

        self.assertEqual(statuses, [200], 'Do not send a second response to a closed client')
        self.server.handle_error.assert_not_called()
        self.assertIn('nodes', json.loads(self.request('/api/overview')))

    def test_booking_disconnect_during_headers_or_body_never_repeats_mutation(self):
        for write in (1, 2):
            with self.subTest(write='headers' if write == 1 else 'body'):
                payload = {'token': f'disconnect-{write}', 'airline': 'DEFAULT', 'hotel': 'DEFAULT'}
                self.api.book.reset_mock()
                self.check_disconnect('/api/bookings', payload, write)
                self.api.book.assert_called_once_with(payload)
                first = self.book_results[-1]
                retry = json.loads(self.request('/api/bookings', payload))
                self.assertEqual(retry['tx'], first['tx'])
                self.assertEqual(retry['state'], 'COMMITTED')
                self.assertTrue(retry['complete'])

    def test_static_disconnect_during_headers_or_body_leaves_server_available(self):
        for write in (1, 2):
            with self.subTest(write='headers' if write == 1 else 'body'):
                self.check_disconnect('/', None, write)
                self.api.book.assert_not_called()
                self.assertIn(b'lang="vi"', self.request('/'))


if __name__ == '__main__':
    unittest.main()
