"""Power writes must retain enough evidence to distinguish uncertain effects."""
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_controller import Controller
from forge_scenario import Power, PROPERTIES


class PowerEvidence(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.evidence = Path(temporary.name) / 'power.jsonl'
        self.values = {PROPERTIES[k][0]: v for k, v in asdict(Power()).items()}
        self.writes = 0
        self.fail = None
        self.owner = object.__new__(Controller)
        self.owner.runtime = MagicMock(return_value=MagicMock(identity='owned-vm', control=self.control))

    def records(self):
        return [json.loads(line) for line in self.evidence.read_text().splitlines()]

    def control(self, command, arguments):
        if command == 'qom-list':
            if arguments['path'] == '/machine':
                return [{'name': 'battery-adc', 'type': 'child<adc101c>'}]
            if arguments['path'] == '/machine/unattached':
                return [{'name': 'pmic', 'type': 'child<axp221_pmu>'}]
            return [{'name': prop, 'type': 'bool' if kind is bool else 'int'}
                    for prop, kind, _ in PROPERTIES.values()]
        if command == 'qom-set':
            # Intent, preimage and dispatch are already flushed before mutation.
            rows = self.records()
            self.assertEqual(rows[-1]['state'], 'dispatch')
            self.assertFalse(rows[-2]['before']['pmic_over_temperature'])
            self.writes += 1
            self.values[arguments['property']] = arguments['value']
            if self.fail == 'ack':
                raise ConnectionError('VM exited before acknowledgement')
            return {}
        if command == 'qom-get':
            if self.writes and self.fail == 'readback':
                raise ConnectionError('VM exited before readback')
            return self.values[arguments['property']]
        raise AssertionError(command)

    def run_change(self):
        return self.owner.power_operation('test', {'pmic_over_temperature': True}, self.evidence)

    def test_success_records_preimage_dispatch_ack_and_verified_result(self):
        result = self.run_change()
        rows = self.records()
        self.assertEqual([r['state'] for r in rows if 'state' in r], ['dispatch', 'acknowledged'])
        self.assertTrue(result['observed']['pmic_over_temperature'])
        self.assertEqual(rows[-1]['status'], 'completed')
        self.assertEqual(self.writes, 1)

    def test_adc_power_write_records_exact_device_and_retains_voltage(self):
        self.values['input-uv'] = 1650000
        result = self.owner.power_operation('test', {'adc_powered': False}, self.evidence)
        dispatch = next(row for row in self.records() if row.get('state') == 'dispatch')
        self.assertEqual(dispatch['arguments'], {'path': '/machine/battery-adc',
                                               'property': 'powered', 'value': False})
        self.assertFalse(result['observed']['adc_powered'])
        self.assertEqual(result['observed']['adc_input_uv'], 1650000)
        self.assertEqual(self.writes, 1)

    def test_lost_ack_and_lost_readback_do_not_retry_or_claim_rollback(self):
        for failure in ('ack', 'readback'):
            with self.subTest(failure=failure):
                self.fail = failure
                self.evidence = self.evidence.with_name(failure + '.jsonl')
                self.values['pmic-over-temperature'] = False
                self.writes = 0
                with self.assertRaises(ConnectionError if failure == 'ack' else ValueError) as error:
                    self.run_change()
                if failure == 'readback':
                    self.assertIn('Power write acknowledged, but readback failed', str(error.exception))
                rows = self.records()
                expected = ['dispatch'] + (['acknowledged'] if failure == 'readback' else [])
                self.assertEqual([r['state'] for r in rows if 'state' in r], expected)
                self.assertEqual(rows[-1]['status'], 'failed')
                self.assertFalse(rows[-1]['rollback'])
                self.assertTrue(self.values['pmic-over-temperature'])
                self.assertEqual(self.writes, 1)

    def test_failed_dispatch_fsync_prevents_mutation(self):
        # Intent file, parent directory, before sample, then dispatch intent.
        with patch('forge_controller.os.fsync', side_effect=[None, None, None,
                   OSError('storage failure'), None]), self.assertRaisesRegex(OSError, 'storage failure'):
            self.run_change()
        self.assertEqual(self.writes, 0)
        self.assertFalse(self.values['pmic-over-temperature'])

    def test_existing_evidence_is_never_overwritten(self):
        self.evidence.write_text('prior proof')
        with self.assertRaises(FileExistsError):
            self.run_change()
        self.assertEqual(self.evidence.read_text(), 'prior proof')
        self.assertEqual(self.writes, 0)
