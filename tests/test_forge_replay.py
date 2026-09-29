"""Host-clock replay validation, ordering, cancellation and partial effects."""
import json
from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_replay import Event, Replay, ReplayCancelled, run_recorded, completed_event_records
from forge_scenario import Power, PROPERTIES


class ReplayTests(unittest.TestCase):
    def test_completed_records_exclude_partial_steps_and_run_summary(self):
        complete = {'event': 0, 'status': 'completed', 'result': {'observed': {}}}
        records = [{'runtime_identity': 'fixture'}, {'event': 0, 'before': {}},
                   {'event': 0, 'state': 'dispatch'}, {'event': 0, 'state': 'acknowledged'},
                   complete, {'event': 1, 'status': 'failed'},
                   {'status': 'completed', 'completed_events': 1}]
        self.assertEqual(completed_event_records(records), [complete])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'schedule.json'
        self.events = [{'at_ms': 0, 'power': {'ac_present': False}},
                       {'at_ms': 1000, 'power': {'ac_present': True}}]
        self.path.write_text(json.dumps({'schema': 1, 'events': self.events}))

    def test_schema_bounds_types_duplicates_and_order(self):
        invalid = [[], self.events * 33,
                   [{'at_ms': True, 'power': {'ac_present': False}}],
                   [{'at_ms': 300001, 'power': {'ac_present': False}}],
                   list(reversed(self.events)),
                   [{'at_ms': 0, 'power': {'battery_capacity': True}}],
                   [{'at_ms': 0, 'power': {'ac_present': False, 'battery_present': True}}]]
        for entries in invalid:
            self.path.write_text(json.dumps({'schema': 1, 'events': entries}))
            with self.assertRaises(ValueError):
                Replay.load(self.path)
        self.path.write_text('{"schema":1,"schema":1,"events":[]}')
        with self.assertRaises(ValueError):
            Replay.load(self.path)

    def test_snapshot_order_and_deadline_waits(self):
        replay = Replay.load(self.path)
        self.path.write_text('{}')
        now = [0.0]
        waits, records = [], []
        cancel = Mock()
        cancel.is_set.return_value = False
        def wait(delay):
            waits.append(delay)
            now[0] += delay
            return False
        cancel.wait.side_effect = wait
        control = Mock(return_value={'running': True})
        with patch('forge_replay.change_power', return_value={'observed': {}}) as change:
            result = replay.run(control, cancel, records.append, clock=lambda: now[0])
        self.assertEqual(waits, [0, 1])
        self.assertEqual([call.args[2] for call in change.call_args_list], [False, True])
        self.assertEqual(result['completed_events'], 2)
        self.assertEqual([r['status'] for r in records],
                         ['dispatching', 'completed', 'dispatching', 'completed', 'completed'])

    def test_cancel_after_first_event_keeps_partial_evidence(self):
        records = []
        cancel = threading.Event()
        def change(*args, **kwargs):
            cancel.set()
            return {'observed': {'ac_present': False}}
        with patch('forge_replay.change_power', side_effect=change) as mutate:
            with self.assertRaises(ReplayCancelled):
                Replay.load(self.path).run(Mock(return_value={'running': True}), cancel, records.append)
        mutate.assert_called_once()
        self.assertEqual(records[-1]['status'], 'cancelled')
        self.assertEqual(records[-1]['completed_events'], 1)
        self.assertFalse(records[-1]['rollback'])

    def test_paused_guest_is_not_resumed_or_mutated(self):
        records = []
        with patch('forge_replay.change_power') as change:
            with self.assertRaisesRegex(ValueError, 'running owned VM'):
                Replay.load(self.path).run(Mock(return_value={'running': False}),
                                          threading.Event(), records.append)
            change.assert_not_called()
        self.assertEqual(records[-1]['status'], 'failed')

    def test_failed_readback_preserves_dispatch_without_false_completion(self):
        records = []
        with patch('forge_replay.change_power', side_effect=ValueError('readback')):
            with self.assertRaises(ValueError):
                Replay.load(self.path).run(Mock(return_value={'running': True}),
                                          threading.Event(), records.append)
        self.assertEqual([r['status'] for r in records], ['dispatching', 'failed'])
        self.assertEqual(records[-1]['completed_events'], 0)

    def test_real_change_lost_readback_retains_each_event_phase_and_stops_future_events(self):
        values = {PROPERTIES[k][0]: v for k, v in asdict(Power()).items()}
        writes = []
        evidence = self.path.with_name('replay.jsonl')
        def control(command, arguments=None):
            if command == 'query-status':
                return {'running': True}
            if command == 'qom-list':
                if arguments['path'] == '/machine':
                    return [{'name': 'battery-adc', 'type': 'child<adc101c>'}]
                if arguments['path'] == '/machine/unattached':
                    return [{'name': 'pmic', 'type': 'child<axp221_pmu>'}]
                return [{'name': prop, 'type': 'bool' if kind is bool else 'int'}
                        for prop, kind, _ in PROPERTIES.values()]
            if command == 'qom-set':
                rows = [json.loads(line) for line in evidence.read_text().splitlines()]
                self.assertEqual(rows[-1]['state'], 'dispatch')
                self.assertEqual(rows[-1]['event'], len(writes))
                writes.append(arguments['property'])
                values[arguments['property']] = arguments['value']
                return {}
            if command == 'qom-get':
                if values['pmic-over-temperature']:
                    raise ConnectionError('abrupt exit')
                return values[arguments['property']]
            raise AssertionError(command)
        replay = Replay('fixture', (Event(0, 'ac_present', False),
            Event(0, 'pmic_over_temperature', True), Event(0, 'ac_present', True)))
        with self.assertRaisesRegex(ValueError, 'write acknowledged, but readback failed'):
            run_recorded(replay, Mock(identity='owned', control=control), threading.Event(), evidence)
        rows = [json.loads(line) for line in evidence.read_text().splitlines()]
        self.assertEqual(writes, ['ac-present', 'pmic-over-temperature'])
        self.assertEqual([(r['event'], r['state']) for r in rows if 'state' in r],
                         [(0, 'dispatch'), (0, 'acknowledged'), (1, 'dispatch'), (1, 'acknowledged')])
        self.assertEqual(rows[-1]['completed_events'], 1)
        self.assertEqual(rows[-1]['status'], 'failed')
        self.assertFalse(rows[-1]['rollback'])
        self.assertFalse(any(r.get('event') == 2 for r in rows))

    def test_initial_fsync_failure_prevents_replay(self):
        replay = Replay.load(self.path)
        runtime = Mock(identity='owned')
        with patch('forge_replay.os.fsync', side_effect=OSError('disk full')), \
                self.assertRaisesRegex(OSError, 'disk full'):
            run_recorded(replay, runtime, threading.Event(), self.path.with_name('failed.jsonl'))
        runtime.control.assert_not_called()


if __name__ == '__main__':
    unittest.main()
