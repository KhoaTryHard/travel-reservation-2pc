"""Lifecycle tests with REAL child processes (W06/W07): temp DB/log dirs and free ports."""
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import RpcError, rpc
from dashboard_api import ApiError, DashboardAPI, LifecycleError, create_http_server
from http.client import HTTPConnection
from lifecycle import NAMES, ProcessManager
import json


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ports = {name: free_port() for name in NAMES}
        self.manager = ProcessManager(root=ROOT, data_dir=Path(self.tmp.name) / 'data',
                                      log_dir=Path(self.tmp.name) / 'logs', ports=self.ports)
        self.addCleanup(self.manager.close_all)         # no child may outlive a test

    def url(self, name):
        return f'http://127.0.0.1:{self.ports[name]}'

    def urls(self):
        return {name: self.url(name) for name in NAMES}

    def settled(self, tx, expected_state, timeout=10.0):
        def check():
            try:
                status = rpc(self.url('coordinator'), 'status', {'tx': tx}, timeout=1.0)
                if not status.get('complete') or status.get('state') != expected_state:
                    return False
                for rm in ('airline', 'hotel'):
                    rm_st = rpc(self.url(rm), 'status', {'tx': tx}, timeout=1.0)
                    if rm_st.get('state') != expected_state:
                        return False
                return True
            except RpcError:
                return False
        self.assertTrue(wait_until(check, timeout=timeout), f'Transaction {tx} did not settle to {expected_state}')

    def test_start_is_idempotent_and_stop_is_harmless(self):
        first = self.manager.control('airline', 'start')
        self.assertEqual(first['state'], 'running')
        self.assertIsNotNone(first['pid'])
        self.assertEqual(self.manager.control('airline', 'start')['pid'], first['pid'])
        self.assertEqual(rpc(self.url('airline'), 'health')['role'], 'participant')
        self.assertEqual(self.manager.control('airline', 'stop')['state'], 'stopped')
        again = self.manager.control('airline', 'stop')  # already stopped: nothing happens
        self.assertEqual((again['state'], again['pid']), ('stopped', None))
        with self.assertRaises(RpcError):
            rpc(self.url('airline'), 'health', timeout=0.5)

    def test_restart_keeps_the_same_database(self):
        before = self.manager.control('airline', 'start')['pid']
        rpc(self.url('airline'), 'prepare', {'tx': 'tx-1', 'resource': 'DEFAULT'})
        rpc(self.url('airline'), 'commit', {'tx': 'tx-1'})
        after = self.manager.control('airline', 'restart')
        self.assertNotEqual(before, after['pid'])
        inventory = rpc(self.url('airline'), 'snapshot')['inventory']
        self.assertEqual(inventory[0]['available'], 4)   # 5 -> 4 survived the restart
        self.assertTrue(Path(after['db']).exists())

    def test_busy_port_is_reported_and_external_process_is_untouched(self):
        with socket.socket() as external:
            external.bind(('127.0.0.1', self.ports['hotel']))
            external.listen()
            with self.assertRaises(LifecycleError) as caught:
                self.manager.control('hotel', 'start')
            self.assertEqual((caught.exception.status, caught.exception.code), (409, 'port_busy'))
            self.assertEqual(self.manager.snapshot()['hotel']['state'], 'stopped')
            self.assertIsNone(self.manager.snapshot()['hotel']['pid'])
            self.manager.control('hotel', 'stop')        # must not touch the external listener
            with socket.create_connection(('127.0.0.1', self.ports['hotel']), timeout=1):
                pass                                     # still accepting connections

    def test_crash_is_isolated_not_auto_restarted_and_prepared_survives(self):
        for name in NAMES:
            self.manager.control(name, 'start')
        rpc(self.url('hotel'), 'fault', {'point': 'after_prepare', 'mode': 'crash'})
        with self.assertRaises(RpcError):                # child exits (code 86) mid-RPC
            rpc(self.url('hotel'), 'prepare', {'tx': 'tx-9', 'resource': 'DEFAULT'})
        self.assertTrue(wait_until(lambda: self.manager.snapshot()['hotel']['state'] == 'failed'))
        record = self.manager.snapshot()['hotel']
        self.assertIn('86', record['error'])
        self.assertIsNone(record['pid'])
        for name in ('coordinator', 'airline'):          # other nodes keep running
            self.assertEqual(self.manager.snapshot()[name]['state'], 'running')
            self.assertTrue(rpc(self.url(name), 'health')['ok'])
        time.sleep(1.0)                                  # never restarted by itself
        self.assertEqual(self.manager.snapshot()['hotel']['state'], 'failed')
        self.manager.control('hotel', 'start')           # restart with the old DB
        snap = rpc(self.url('hotel'), 'snapshot')
        self.assertEqual([r['state'] for r in snap['reservations']], ['PREPARED'])
        self.assertEqual([lock['tx'] for lock in snap['locks']], ['tx-9'])
        self.assertEqual(snap['inventory'][0]['available'], 5)   # tentative: not deducted

    def test_concurrent_start_creates_exactly_one_child(self):
        pids, errors = [], []

        def start():
            try:
                pids.append(self.manager.control('coordinator', 'start')['pid'])
            except Exception as exc:                     # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=start) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(pids)), 1)

    def test_snapshot_lists_three_nodes_even_when_nothing_runs(self):
        snap = self.manager.snapshot()
        self.assertEqual(set(snap), set(NAMES))
        for record in snap.values():
            self.assertEqual((record['state'], record['managed'], record['pid']),
                             ('stopped', True, None))

    def test_close_all_stops_owned_children_and_keeps_data(self):
        self.manager.control('airline', 'start')
        self.manager.control('hotel', 'start')
        db_files = [Path(self.manager.snapshot()[n]['db']) for n in ('airline', 'hotel')]
        self.manager.close_all()
        for name in ('airline', 'hotel'):
            self.assertEqual(self.manager.snapshot()[name]['state'], 'stopped')
            with self.assertRaises(RpcError):
                rpc(self.url(name), 'health', timeout=0.5)
        self.assertTrue(all(path.exists() for path in db_files))

    def test_unknown_service_or_action_is_rejected(self):
        for name, action in (('payments', 'start'), ('airline', 'delete'), ('../x', 'stop')):
            with self.assertRaises(LifecycleError) as caught:
                self.manager.control(name, action)
            self.assertEqual(caught.exception.status, 404)

    def test_http_lifecycle_boundary_and_web_survives_node_crash(self):
        api = DashboardAPI(self.urls(), manager=self.manager)
        server = create_http_server(api, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(timeout=3)))
        host = f'127.0.0.1:{server.server_port}'
        origin = f'http://{host}'

        def http_req(method, path, payload=None, with_token=True, token_val=None):
            conn = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                body = json.dumps(payload or {}).encode() if method == 'POST' else None
                headers = {'Host': host, 'Origin': origin}
                if method == 'POST':
                    headers['Content-Type'] = 'application/json'
                    headers['Content-Length'] = str(len(body))
                    if with_token:
                        headers['X-Session-Token'] = token_val
                conn.request(method, path, body=body, headers=headers)
                resp = conn.getresponse()
                return resp.status, json.loads(resp.read())
            finally:
                conn.close()

        _, session_data = http_req('GET', '/api/session', with_token=False)
        token = session_data['session_token']

        # Missing session token is rejected at HTTP boundary without starting child
        st, err = http_req('POST', '/api/services/airline/start', with_token=False)
        self.assertEqual((st, err['error']['code']), (403, 'invalid_session'))
        self.assertEqual(self.manager.snapshot()['airline']['state'], 'stopped')

        # Start all 3 services via HTTP lifecycle endpoint and verify log files
        for name in ('airline', 'hotel', 'coordinator'):
            st, res = http_req('POST', f'/api/services/{name}/start', token_val=token)
            self.assertEqual(st, 200)
            self.assertEqual(res['state'], 'running')
            self.assertTrue(Path(self.manager.snapshot()[name]['log']).exists())

        # Stop hotel and occupy port externally -> HTTP 409 port_busy
        st, _ = http_req('POST', '/api/services/hotel/stop', token_val=token)
        self.assertEqual(st, 200)
        with socket.socket() as external:
            external.bind(('127.0.0.1', self.ports['hotel']))
            external.listen()
            st, err = http_req('POST', '/api/services/hotel/start', token_val=token)
            self.assertEqual((st, err['error']['code']), (409, 'port_busy'))

        # Start hotel back, crash it, and verify web HTTP overview stays alive
        st, _ = http_req('POST', '/api/services/hotel/start', token_val=token)
        self.assertEqual(st, 200)
        rpc(self.url('hotel'), 'fault', {'point': 'after_prepare', 'mode': 'crash'})
        with self.assertRaises(RpcError):
            rpc(self.url('hotel'), 'prepare', {'tx': 'tx-http-crash', 'resource': 'DEFAULT'})
        self.assertTrue(wait_until(lambda: self.manager.snapshot()['hotel']['state'] == 'failed'))

        st, overview = http_req('GET', '/api/overview', with_token=False)
        self.assertEqual(st, 200)
        self.assertEqual(overview['nodes']['hotel']['lifecycle']['state'], 'failed')
        self.assertTrue(overview['nodes']['hotel']['stale'])
        for alive in ('coordinator', 'airline'):
            self.assertEqual(overview['nodes'][alive]['lifecycle']['state'], 'running')
            self.assertFalse(overview['nodes'][alive]['stale'])
            self.assertTrue(overview['nodes'][alive]['health']['ok'])

    def test_coordinator_crash_before_and_after_decision_via_manager(self):
        for name in ('airline', 'hotel', 'coordinator'):
            self.manager.control(name, 'start')
        api = DashboardAPI(self.urls(), manager=self.manager)

        # 1. Coordinator crash BEFORE decision (after_votes) -> PREPARED -> restart -> ABORTED
        api.fault({'service': 'coordinator', 'point': 'after_votes', 'mode': 'crash'})
        payload_before = {'token': 'mgr-before', 'airline': 'DEFAULT', 'hotel': 'DEFAULT'}
        with self.assertRaises(ApiError) as cm:
            api.book(payload_before)
        self.assertTrue(cm.exception.extra['outcome_unknown'])
        tx_before = cm.exception.extra['tx']
        self.assertTrue(wait_until(lambda: self.manager.snapshot()['coordinator']['state'] == 'failed'))
        for rm in ('airline', 'hotel'):
            self.assertEqual(rpc(self.url(rm), 'status', {'tx': tx_before})['state'], 'PREPARED')

        self.manager.control('coordinator', 'start')
        self.settled(tx_before, 'ABORTED')
        retry_before = api.book(payload_before)
        self.assertEqual((retry_before['tx'], retry_before['state'], retry_before['complete']),
                         (tx_before, 'ABORTED', True))
        for rm in ('airline', 'hotel'):
            snap = rpc(self.url(rm), 'snapshot')
            self.assertEqual(snap['locks'], [])
            self.assertEqual(snap['inventory'][0]['available'], 5)

        # 2. Coordinator crash AFTER decision (after_decision) -> restart -> COMMIT replayed once
        api.fault({'service': 'coordinator', 'point': 'after_decision', 'mode': 'crash'})
        payload_after = {'token': 'mgr-after', 'airline': 'DEFAULT', 'hotel': 'DEFAULT'}
        with self.assertRaises(ApiError) as cm:
            api.book(payload_after)
        self.assertTrue(cm.exception.extra['outcome_unknown'])
        tx_after = cm.exception.extra['tx']
        self.assertTrue(wait_until(lambda: self.manager.snapshot()['coordinator']['state'] == 'failed'))

        self.manager.control('coordinator', 'start')
        self.settled(tx_after, 'COMMITTED')
        retry_after = api.book(payload_after)
        self.assertEqual((retry_after['tx'], retry_after['state'], retry_after['complete']),
                         (tx_after, 'COMMITTED', True))
        for rm in ('airline', 'hotel'):
            self.assertEqual(rpc(self.url(rm), 'snapshot')['inventory'][0]['available'], 4)

    def test_lost_ack_after_commit_and_late_prepare_tombstone_via_manager(self):
        for name in ('airline', 'hotel', 'coordinator'):
            self.manager.control(name, 'start')
        api = DashboardAPI(self.urls(), manager=self.manager)

        # 1. Participant crashes after COMMIT before ACK -> restart replays ACK without double-deducting
        api.fault({'service': 'hotel', 'point': 'after_commit', 'mode': 'crash'})
        payload_ack = {'token': 'mgr-lost-ack', 'airline': 'DEFAULT', 'hotel': 'DEFAULT'}
        res_ack = api.book(payload_ack)
        self.assertEqual((res_ack['state'], res_ack['complete'], res_ack['ack']),
                         ('COMMITTED', False, ['airline']))
        self.assertTrue(wait_until(lambda: self.manager.snapshot()['hotel']['state'] == 'failed'))

        self.manager.control('hotel', 'start')
        self.settled(res_ack['tx'], 'COMMITTED')
        retry_ack = api.book(payload_ack)
        self.assertTrue(retry_ack['complete'])
        self.assertEqual(rpc(self.url('hotel'), 'snapshot')['inventory'][0]['available'], 4)

        # 2. Delayed PREPARE exceeds timeout -> tombstone rejects late PREPARE and never revives lock
        api.fault({'service': 'hotel', 'point': 'before_prepare', 'mode': 'delay', 'seconds': 3})
        payload_late = {'token': 'mgr-late-prep', 'airline': 'DEFAULT', 'hotel': 'DEFAULT'}
        res_late = api.book(payload_late)
        self.assertEqual(res_late['state'], 'ABORTED')
        time.sleep(1.5)  # allow delayed before_prepare on hotel to finish after tombstone is written
        self.settled(res_late['tx'], 'ABORTED')
        hotel_snap = rpc(self.url('hotel'), 'snapshot')
        self.assertEqual(hotel_snap['locks'], [])
        self.assertEqual(hotel_snap['inventory'][0]['available'], 4)


if __name__ == '__main__':
    unittest.main()

