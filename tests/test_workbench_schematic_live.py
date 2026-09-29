import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_runtime import Runtime
from workbench_schematic_live import Collector, Sampler


class SampleTests(unittest.TestCase):
    def runtime(self):
        runtime = SimpleNamespace(identity='owned-1', total=100, calls=[])
        def observe(operation, arguments=None):
            runtime.calls.append(operation)
            if operation == 'query-status':
                return {'status': 'running'}
            if operation == 'query-blockstats':
                return [{'stats': {'rd_bytes': runtime.total, 'wr_bytes': 0}}]
            if operation == 'qom-list':
                if arguments['path'] == '/machine/unattached':
                    return [{'name': 'sd-fixture', 'type': 'child<sd-card>'}]
                if arguments['path'] == '/machine/unattached/sd-fixture':
                    return [{'name': field, 'type': 'uint64'} for field in ('observed-read-bytes', 'observed-write-bytes')]
                if arguments['path'] == '/machine/peripheral/deck':
                    return [{'name': 'delivered-reports', 'type': 'uint64'}]
                if arguments['path'] == '/machine/soc/peripherals/fb':
                    return [{'name': 'display-updates', 'type': 'uint64'}]
                if arguments['path'] == '/machine/soc/peripherals':
                    return [{'name': 'fb', 'type': 'child<bcm2835-fb>'}]
                return [{'name': 'deck', 'type': 'child<usb-uconsole-keyboard>'}]
            if operation == 'qom-get':
                if arguments['property'] in ('delivered-reports', 'display-updates', 'observed-read-bytes', 'observed-write-bytes'):
                    return runtime.total
                return True
            raise AssertionError(operation)
        runtime.observe = observe
        return runtime

    def test_counters_not_queries_drive_activity_and_reset_on_new_vm(self):
        runtime = self.runtime()
        sampler = Sampler()
        with patch('workbench_schematic_live.query_power', return_value={'power': {'battery_capacity': 50}}):
            first = {e['component']: e for e in sampler.sample(runtime)}
            self.assertEqual(first['storage']['state'], 'present')
            self.assertEqual(first['keyboard']['state'], 'present')
            self.assertEqual(first['audio']['state'], 'disconnected')
            self.assertEqual(first['display']['state'], 'present')
            runtime.total += 512
            second = {e['component']: e for e in sampler.sample(runtime)}
            self.assertEqual(second['storage']['state'], 'active')
            self.assertEqual(second['power']['state'], 'present')
            self.assertEqual(second['keyboard']['state'], 'active')
            self.assertEqual(second['display']['state'], 'active')
            runtime.identity = 'owned-2'
            third = {e['component']: e for e in sampler.sample(runtime)}
            self.assertEqual(third['storage']['state'], 'present')
            self.assertEqual({e['identity'] for e in third.values()}, {'owned-2'})
        self.assertTrue(set(runtime.calls) <= {'query-status', 'query-blockstats', 'qom-list', 'qom-get'})

    def test_busy_runtime_has_no_false_disconnect(self):
        runtime = self.runtime()
        def busy(*unused):
            raise BlockingIOError('busy')
        runtime.observe = busy
        self.assertEqual(Sampler().sample(runtime), [])

    def test_runtime_observe_is_read_only_nonblocking_and_identity_checked(self):
        runtime = Runtime(SimpleNamespace(workspace=Path('/tmp')))
        runtime.process = SimpleNamespace(poll=lambda: None)
        runtime.directory = SimpleNamespace(name='/tmp/unused-observer-test')
        with patch('uconsole_emulator.qmp') as qmp:
            with self.assertRaises(ValueError):
                runtime.observe('qom-set', {})
            with runtime.control_mutex:
                with self.assertRaises(BlockingIOError):
                    runtime.observe('query-status')
            qmp.assert_not_called()
            qmp.return_value = {'name': 'not-the-owned-vm'}
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                runtime.observe('query-status')
            self.assertEqual(qmp.call_count, 1)
            qmp.reset_mock()
            qmp.side_effect = [{'name': runtime.identity}, {'status': 'running'}]
            self.assertEqual(runtime.observe('query-status'), {'status': 'running'})
            self.assertEqual(qmp.call_count, 2)

    def test_collector_discards_inflight_result_after_rebind(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        def sample(runtime):
            started.set()
            release.wait(2)
            finished.set()
            return [{'identity': runtime.identity}]
        collector = Collector(interval=.001, sampler=SimpleNamespace(sample=sample))
        try:
            collector.bind(SimpleNamespace(identity='old'))
            self.assertTrue(started.wait(1))
            collector.bind(None)
            release.set()
            self.assertTrue(finished.wait(1))
        finally:
            release.set()
            collector.close()
            collector.worker.join(1)
        self.assertIsNone(collector.take())
        self.assertFalse(collector.worker.is_alive())

    def test_fast_producer_keeps_only_one_batch_and_reports_drops(self):
        ready = threading.Event()
        count = 0
        def sample(runtime):
            nonlocal count
            count += 1
            if count >= 1000:
                ready.set()
            return [{'identity': runtime.identity, 'sample': count}]
        collector = Collector(interval=.00001, sampler=SimpleNamespace(sample=sample))
        try:
            collector.bind(SimpleNamespace(identity='owned'))
            self.assertTrue(ready.wait(5))
            batch = collector.take()
            self.assertEqual(len(batch), 1)
            self.assertGreaterEqual(batch[0]['sample'], 999)
            self.assertGreaterEqual(collector.dropped, 998)
        finally:
            collector.close()
            collector.worker.join(1)


if __name__ == '__main__':
    unittest.main()
