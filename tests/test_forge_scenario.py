"""Scenario validation, ordered application and fail-closed startup."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_scenario import Scenario, PROPERTIES, query_power, change_power
from forge_runtime import Runtime
from forge_workspace import WorkspaceLock
from uconsole_emulator import command, parser


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.file = self.path / 'scenario.json'
        self.file.write_text(json.dumps({'schema': 1, 'power': {
            'battery_present': True, 'battery_capacity': 50, 'battery_current_ma': 500,
            'battery_voltage_uv': 3300550}}))
        (self.path / 'machine.json').write_text('{"root":"PARTUUID=1234-02"}')
        self.values = {}
        self.calls = []

    def control(self, operation, args=None):
        self.calls.append((operation, args))
        if operation == 'query-status':
            return {'running': False}
        if operation == 'qom-list':
            if args['path'] == '/machine':
                return [{'name': 'battery-adc', 'type': 'child<adc101c>'}]
            if args['path'] == '/machine/unattached':
                return [{'name': 'device[9]', 'type': 'child<axp221_pmu>'}]
            return [{'name': prop, 'type': 'bool' if kind is bool else 'int'}
                    for prop, kind, _ in PROPERTIES.values()]
        if operation == 'qom-set':
            value = args['value']
            if args['property'] == 'battery-voltage-uv':
                value = value // 1100 * 1100
            if args['property'] == 'pmic-temperature-mc':
                value = value // 100 * 100
            self.values[args['property']] = value
        if operation == 'qom-get':
            return self.values[args['property']]

    def test_validated_snapshot_and_observed_quantization(self):
        scenario = Scenario.load(self.file)
        self.file.write_text('{}')
        evidence = scenario.apply(self.control)
        self.assertEqual(evidence['observed']['power']['battery_voltage_uv'], 3300000)
        self.assertEqual(evidence['requested']['power']['battery_voltage_uv'], 3300550)
        writes = [args for op, args in self.calls if op == 'qom-set']
        self.assertEqual(writes[0]['property'], 'battery-current-ma')
        self.assertEqual(writes[0]['value'], 0)
        self.assertEqual(len(scenario.sha256), 64)

    def test_runtime_query_and_single_change_with_quantized_readback(self):
        Scenario.load(self.file).apply(self.control)
        self.calls.clear()
        before = query_power(self.control)['power']
        result = change_power(self.control, 'battery_voltage_uv', 4400999)
        self.assertEqual(result['before'], before)
        self.assertEqual(result['observed']['battery_voltage_uv'], 4400000)
        self.assertEqual(result['observed']['battery_capacity'], before['battery_capacity'])
        self.assertEqual(len([op for op, _ in self.calls if op == 'qom-set']), 1)
        self.assertNotIn('stop', [op for op, _ in self.calls])

    def test_runtime_invalid_values_fail_before_any_control(self):
        for name, value in [('battery_capacity', True), ('battery_capacity', 101), ('unknown', 1),
                            ('pmic_temperature_mc', True), ('pmic_temperature_mc', -267701),
                            ('pmic_temperature_mc', 141801), ('adc_input_uv', -1),
                            ('adc_input_uv', 3300001), ('adc_input_uv', True), ('adc_powered', 1)]:
            with self.assertRaises(ValueError):
                change_power(self.control, name, value)
        self.assertEqual(self.calls, [])

    def test_adc_controls_route_to_adc_and_leave_pmic_voltage_independent(self):
        Scenario.load(self.file).apply(self.control)
        self.calls.clear()
        changed = change_power(self.control, 'adc_input_uv', 1650000)
        self.assertEqual(changed['observed']['adc_input_uv'], 1650000)
        self.assertEqual(changed['observed']['battery_voltage_uv'], 3300000)
        writes = [args for op, args in self.calls if op == 'qom-set']
        self.assertEqual(writes, [{'path': '/machine/battery-adc', 'property': 'input-uv', 'value': 1650000}])
        changed = change_power(self.control, 'adc_powered', False)
        self.assertFalse(changed['observed']['adc_powered'])
        self.assertEqual(changed['observed']['adc_input_uv'], 1650000)

    def test_adc_identity_and_properties_are_required_before_initial_writes(self):
        for failure in ('identity', 'property'):
            self.calls.clear()
            def monitor(operation, args=None):
                result = self.control(operation, args)
                if operation == 'qom-list':
                    if failure == 'identity' and args['path'] == '/machine':
                        return [{'name': 'battery-adc', 'type': 'child<other-model>'}]
                    if failure == 'property' and args['path'] == '/machine/battery-adc':
                        return [{'name': 'powered', 'type': 'int'}]
                return result
            with self.subTest(failure=failure), self.assertRaisesRegex(ValueError, 'ADC'):
                Scenario.load(self.file).apply(monitor)
            self.assertFalse(any(op == 'qom-set' for op, _ in self.calls))

    def test_initial_adc_profile_preserves_unpowered_voltage_input(self):
        self.file.write_text(json.dumps({'schema': 1, 'power': {
            'adc_input_uv': 1650000, 'adc_powered': False}}))
        result = Scenario.load(self.file).apply(self.control)
        self.assertEqual(result['observed']['power']['adc_input_uv'], 1650000)
        self.assertFalse(result['observed']['power']['adc_powered'])

    def test_temperature_profile_default_and_negative_quantization(self):
        evidence = Scenario.load(self.file).apply(self.control)
        self.assertEqual(evidence['observed']['power']['pmic_temperature_mc'], 25000)
        result = change_power(self.control, 'pmic_temperature_mc', -101)
        self.assertEqual(result['observed']['pmic_temperature_mc'], -200)
        self.file.write_text(json.dumps({'schema': 1, 'power': {'pmic_temperature_mc': 25999}}))
        evidence = Scenario.load(self.file).apply(self.control)
        self.assertEqual(evidence['requested']['power']['pmic_temperature_mc'], 25999)
        self.assertEqual(evidence['observed']['power']['pmic_temperature_mc'], 25900)

    def test_thermal_fault_is_explicit_boolean_not_derived_from_adc(self):
        evidence = Scenario.load(self.file).apply(self.control)
        self.assertFalse(evidence['observed']['power']['pmic_over_temperature'])
        changed = change_power(self.control, 'pmic_over_temperature', True)
        self.assertTrue(changed['observed']['pmic_over_temperature'])
        self.assertEqual(changed['observed']['pmic_temperature_mc'], 25000)
        with self.assertRaises(ValueError):
            change_power(self.control, 'pmic_over_temperature', 1)

    def test_runtime_no_op_readback_is_not_success(self):
        Scenario.load(self.file).apply(self.control)
        def monitor(operation, args=None):
            if operation != 'qom-set':
                return self.control(operation, args)
        with self.assertRaisesRegex(ValueError, 'not rolled back'):
            change_power(monitor, 'battery_capacity', 40)

    def test_invalid_documents_and_inconsistent_power_are_rejected(self):
        cases = ['{"schema":1,"schema":1,"power":{}}',
                 '{"schema":true,"power":{}}', '{"schema":2,"power":{}}',
                 '{"schema":1,"power":{"thermal":10}}',
                 '{"schema":1,"power":{"battery_capacity":true}}',
                 '{"schema":1,"power":{"battery_capacity":101}}',
                 '{"schema":1,"power":{"battery_current_ma":1}}',
                 '{"schema":1,"power":{"battery_present":true,"battery_current_ma":1}}',
                 '{"schema":1,"power":{},"modem":{}}', ' ' * 16385]
        for value in cases:
            with self.subTest(value=value[:80]):
                self.file.write_text(value)
                with self.assertRaises(ValueError):
                    Scenario.load(self.file)

    def test_running_vm_is_rejected_before_mutation(self):
        monitor = MagicMock(return_value={'running': True})
        with self.assertRaisesRegex(ValueError, 'paused'):
            Scenario.load(self.file).apply(monitor)
        self.assertEqual(monitor.call_count, 1)

    def test_missing_device_properties_fail_before_mutation(self):
        def monitor(operation, args=None):
            if operation == 'qom-list' and args['path'] != '/machine/unattached':
                return []
            return self.control(operation, args)
        with self.assertRaisesRegex(ValueError, 'compatible'):
            Scenario.load(self.file).apply(monitor)
        self.assertFalse(any(op == 'qom-set' for op, _ in self.calls))

    def test_silent_no_op_is_detected(self):
        def monitor(operation, args=None):
            result = self.control(operation, args)
            return False if operation == 'qom-get' and args['property'] == 'battery-present' else result
        with self.assertRaisesRegex(ValueError, 'readback mismatch'):
            Scenario.load(self.file).apply(monitor)

    def runtime(self, pause=False):
        argv = ['--workspace', str(self.path), 'run', '--managed', '--scenario', str(self.file)]
        if pause:
            argv += ['--pause']
        return Runtime(parser().parse_args(argv))

    def test_invalid_scenario_does_not_refresh_or_touch_logs(self):
        self.file.write_text('{}')
        (self.path / 'serial.log').write_text('prior evidence')
        with patch('forge_runtime.refresh_boot') as refresh, patch('forge_runtime.subprocess.Popen') as spawn:
            with self.assertRaises(ValueError):
                self.runtime().start()
            refresh.assert_not_called()
            spawn.assert_not_called()
        self.assertEqual((self.path / 'serial.log').read_text(), 'prior evidence')

    def test_startup_pauses_applies_records_then_optionally_resumes(self):
        for pause, snapshot in ((False, False), (True, False), (False, True)):
            runtime = self.runtime(pause)
            initial = Scenario.load(self.file) if snapshot else None
            if snapshot:
                runtime.args.scenario = None
            child = MagicMock()
            child.poll.return_value = None
            self.calls.clear()
            def spawn(*args, **kwargs):
                self.assertIn('-S', args[0])
                runtime.qmp_endpoint.touch()
                return child
            try:
                with patch('forge_runtime.refresh_boot'), patch('forge_runtime.subprocess.Popen', side_effect=spawn), \
                     patch.object(runtime, 'control', side_effect=self.control):
                    runtime.start(initial_scenario=initial)
                record = json.loads((self.path / f'scenario-{runtime.identity}.json').read_text())
                self.assertEqual(record['runtime_identity'], runtime.identity)
                self.assertEqual(any(op == 'cont' for op, _ in self.calls), not pause)
                child.terminate.assert_not_called()
            finally:
                child.poll.return_value = 0
                runtime.release()

    def test_failed_application_reaps_child_and_releases_workspace(self):
        runtime = self.runtime()
        child = MagicMock()
        child.poll.return_value = None
        child.terminate.side_effect = lambda: setattr(child.poll, 'return_value', 0)
        def spawn(*args, **kwargs):
            runtime.qmp_endpoint.touch()
            return child
        with patch('forge_runtime.refresh_boot'), patch('forge_runtime.subprocess.Popen', side_effect=spawn), \
             patch.object(runtime, 'control', return_value={'running': True}) as monitor:
            with self.assertRaisesRegex(ValueError, 'paused'):
                runtime.start()
        child.terminate.assert_called_once()
        self.assertFalse(any(call.args[0] == 'cont' for call in monitor.call_args_list))
        self.assertIsNone(runtime.directory)
        with WorkspaceLock(self.path):
            pass


if __name__ == '__main__':
    unittest.main()
