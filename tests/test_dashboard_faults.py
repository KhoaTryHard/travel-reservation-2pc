"""Fault/unknown contract and real crash recovery through the dashboard API."""
import unittest

from test_dashboard_api import FakeRPC, URLS
from dashboard_api import ApiError, DashboardAPI, UpstreamError


class FaultTests(unittest.TestCase):
    def test_booking_timeout_preserves_request_and_known_tx(self):
        def transport(url, action, data, timeout):
            if action == 'begin':
                return {'tx': 'known'}
            raise UpstreamError('timeout', 'Deadline reached')
        api = DashboardAPI(URLS, transport=transport)
        with self.assertRaises(ApiError) as cm:
            api.book({'token': 'keep-me', 'airline': 'DEFAULT', 'hotel': 'DEFAULT'})
        self.assertEqual(cm.exception.status, 504)
        body = cm.exception.body()
        self.assertEqual(body['token'], 'keep-me')
        self.assertEqual(body['tx'], 'known')
        self.assertTrue(body['outcome_unknown'])
        self.assertNotIn('state', body)

    def test_unknown_before_begin_reply_and_invalid_upstream(self):
        for reply in ('failure', {'tx': 't1'}):
            def transport(url, action, data, timeout):
                if reply == 'failure':
                    raise UpstreamError('unavailable', 'Disconnected')
                return reply
            with self.assertRaises(ApiError) as cm:
                DashboardAPI(URLS, transport=transport).book(
                    {'token': 'keep', 'airline': 'DEFAULT', 'hotel': 'DEFAULT'})
            self.assertEqual(cm.exception.status, 502)
            self.assertTrue(cm.exception.extra['outcome_unknown'])

    def test_fault_whitelist_validation_before_rpc(self):
        calls = []
        def transport(url, action, data, timeout):
            calls.append((url, action, data))
            return {'armed': data['point']}
        api = DashboardAPI(URLS, transport=transport)
        valid = {'service': 'coordinator', 'point': 'after_votes', 'mode': 'crash'}
        self.assertEqual(api.fault(valid), {'armed': 'after_votes'})
        self.assertEqual(calls[0][0], URLS['coordinator'])
        for patch in ({'point': 'after_prepare'}, {'mode': 'shell'},
                      {'seconds': True}, {'seconds': float('nan')}, {'seconds': 31}):
            with self.assertRaises(ApiError) as cm:
                api.fault(dict(valid, **patch))
            self.assertEqual(cm.exception.status, 400)
        self.assertEqual(len(calls), 1)
        with self.assertRaises(ApiError) as cm:
            api.fault(dict(valid, service='external'))
        self.assertEqual(cm.exception.status, 404)

    def test_invalid_snapshot_preserves_last_good_cache(self):
        rpc = FakeRPC()
        api = DashboardAPI(URLS, transport=rpc)
        api.overview()
        def malformed(url, action, data, timeout):
            if url == URLS['coordinator'] and action == 'snapshot':
                return {'transactions': [{'ack': 'bad-json'}], 'events': []}
            return rpc(url, action, data, timeout)
        api.transport = malformed
        node = api.overview()['nodes']['coordinator']
        self.assertTrue(node['stale'])
        self.assertEqual(node['snapshot']['transactions'][0]['tx'], 't1')
        def malformed_rows(url, action, data, timeout):
            if url == URLS['hotel'] and action == 'snapshot':
                return {'inventory': [None], 'reservations': [], 'locks': [], 'events': []}
            return rpc(url, action, data, timeout)
        api.transport = malformed_rows
        self.assertTrue(api.overview()['nodes']['hotel']['stale'])


class RealRecoveryTests(unittest.TestCase):
    def setUp(self):
        import test_system
        self.cluster = test_system.SystemTests('test_success_and_duplicate_end')
        self.cluster.setUp()
        self.api = DashboardAPI(self.cluster.urls)

    def tearDown(self):
        self.cluster.tearDown()

    def test_crash_before_decision_keeps_prepared_then_aborts(self):
        self._crash_recovery('after_votes', 'ABORTED', 5)

    def test_crash_after_decision_replays_commit_once(self):
        self._crash_recovery('after_decision', 'COMMITTED', 4)

    def test_commit_waiting_for_ack_stays_committed_and_replays_once(self):
        self.api.fault({'service': 'hotel', 'point': 'after_commit', 'mode': 'crash'})
        payload = {'token': 'lost-ack', 'airline': 'DEFAULT', 'hotel': 'DEFAULT'}
        result = self.api.book(payload)
        self.assertEqual(result['state'], 'COMMITTED')
        self.assertFalse(result['complete'])
        self.assertEqual(result['ack'], ['airline'])
        self.cluster.stop('hotel')
        self.cluster.start('hotel')
        self.cluster.settled(result['tx'], 'COMMITTED')
        self.assertTrue(self.api.book(payload)['complete'])
        self.assertEqual(self.cluster.capacity('hotel'), 4)

    def _crash_recovery(self, point, expected, inventory):
        self.api.fault({'service': 'coordinator', 'point': point, 'mode': 'crash'})
        payload = {'token': 'crash-' + point, 'airline': 'DEFAULT', 'hotel': 'DEFAULT'}
        with self.assertRaises(ApiError) as cm:
            self.api.book(payload)
        self.assertTrue(cm.exception.extra['outcome_unknown'])
        tx = cm.exception.extra['tx']
        self.cluster.stop('coordinator')
        for name in ('airline', 'hotel'):
            self.assertEqual(self.cluster.call('status', {'tx': tx}, name)['state'], 'PREPARED')
        self.cluster.start('coordinator')
        self.cluster.settled(tx, expected)
        result = self.api.book(payload)
        self.assertEqual(result['tx'], tx)
        self.assertEqual(result['state'], expected)
        self.assertTrue(result['complete'])
        self.assertEqual(self.cluster.capacity('airline'), inventory)
        self.assertEqual(self.cluster.capacity('hotel'), inventory)


if __name__ == '__main__':
    unittest.main()
