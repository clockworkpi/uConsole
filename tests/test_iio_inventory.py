import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from iio_inventory import capture_iio, compare_iio
from uconsole_hardware_probe import compare, capture


class IIOInventoryTests(unittest.TestCase):
    def test_probe_selection_runs_only_requested_commands(self):
        with mock.patch('uconsole_hardware_probe.run_command', return_value={'exit_code': 0}) as run:
            result = capture('target', ['iio', 'iio'])
            self.assertEqual(list(result['probes']), ['iio'])
            self.assertEqual(run.call_count, 1)
            with self.assertRaises(ValueError):
                capture('target', ['invalid'])
            self.assertEqual(run.call_count, 1)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def fixture(self):
        device = self.root / 'iio:device0'
        device.mkdir()
        (device / 'name').write_text('1-0054\n')
        (device / 'in_voltage_raw').write_text('512\n')
        (device / 'in_voltage_scale').write_text('3.222656250\n')
        (device / 'of_node').mkdir()
        (device / 'of_node/compatible').write_bytes(b'ti,adc101c\0')
        (device / 'of_node/reg').write_bytes(b'\0\0\0\x54')
        return device

    def test_allowlisted_sample_and_identity_without_controls_or_identifiers(self):
        device = self.fixture()
        for name in ('serial', 'in_voltage_calibbias', 'sampling_frequency'):
            (device / name).write_text('not-collected')
        result = capture_iio(self.root)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['devices'][0]['compatible'], ['ti,adc101c'])
        self.assertEqual(result['devices'][0]['reg'], '00000054')
        self.assertEqual(result['devices'][0]['fields']['in_voltage_raw'], '512')
        self.assertNotIn('not-collected', json.dumps(result))

    def test_missing_and_empty_subsystem_are_distinct(self):
        self.assertFalse(capture_iio(self.root / 'missing')['available'])
        self.assertTrue(capture_iio(self.root)['available'])

    def test_iio_bus_symlink_reports_parent_driver(self):
        device = self.root / 'physical/1-0054/iio:device0'
        device.mkdir(parents=True)
        driver = self.root / 'drivers/adc081c'
        driver.mkdir(parents=True)
        (device.parent / 'driver').symlink_to(driver)
        (self.root / 'iio:device0').symlink_to(device)
        result = capture_iio(self.root)
        self.assertEqual(result['devices'][0]['driver'], 'adc081c')

    def test_read_failure_preserves_identity_and_other_measurements(self):
        device = self.fixture()
        (device / 'in_temp_raw').mkdir()
        result = capture_iio(self.root)
        self.assertEqual(result['errors'][0]['field'], 'in_temp_raw')
        self.assertEqual(result['devices'][0]['fields']['in_voltage_raw'], '512')
        self.assertFalse(compare_iio(result, result)['complete'])
        self.assertFalse(compare_iio(result, result)['matching'])
        wrapped = {'probes': {'iio': {'exit_code': 2, 'stdout': json.dumps(result)}}}
        comparison = compare(wrapped, wrapped)
        self.assertFalse(comparison['matching'])
        self.assertEqual(comparison['incomplete_probes'], ['iio'])
        self.assertFalse(comparison['semantic_iio']['complete'])

    def test_indices_and_bus_numbers_normalize_but_address_and_values_do_not(self):
        self.fixture()
        first = capture_iio(self.root)
        second = copy.deepcopy(first)
        second['devices'][0]['name'] = 'iio:device7'
        second['devices'][0]['fields']['name'] = '22-0054'
        self.assertTrue(compare_iio(first, second)['matching'])
        second['devices'][0]['fields']['in_voltage_raw'] = '513'
        self.assertFalse(compare_iio(first, second)['matching'])
        second['devices'][0]['fields']['in_voltage_raw'] = '512'
        second['devices'][0]['reg'] = '00000055'
        self.assertFalse(compare_iio(first, second)['matching'])

    def test_duplicates_and_malformed_captures_are_not_ignored(self):
        self.fixture()
        first = capture_iio(self.root)
        second = copy.deepcopy(first)
        second['devices'] *= 2
        self.assertFalse(compare_iio(first, second)['matching'])
        with self.assertRaises(ValueError):
            compare_iio({}, {})
        bad = {'probes': {'iio': {'exit_code': 0, 'stdout': '{}'}}}
        self.assertFalse(compare(bad, bad)['complete'])

    def test_attribute_limit_and_binary_decode_failures_are_reported(self):
        device = self.fixture()
        (device / 'in_voltage_raw').write_text('1' * 65537)
        (device / 'of_node/compatible').write_bytes(b'\xff')
        result = capture_iio(self.root)
        self.assertEqual({error['field'] for error in result['errors']},
                         {'in_voltage_raw', 'compatible'})
