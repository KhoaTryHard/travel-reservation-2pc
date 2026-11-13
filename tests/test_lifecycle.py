"""Lifecycle tests with REAL child processes (W06/W07): temp DB/log dirs and free ports."""
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from common import RpcError, rpc
from dashboard_api import LifecycleError
from lifecycle import NAMES, ProcessManager

ROOT = Path(__file__).resolve().parent.parent


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


if __name__ == '__main__':
    unittest.main()

