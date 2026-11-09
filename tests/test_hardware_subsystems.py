"""Passive inventory fields, absence and read failures are not conflated."""
import json
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from uconsole_hardware_probe import SUBSYSTEM_SYSFS, compare_subsystems, compare


class SubsystemComparisonTests(unittest.TestCase):
    def fixture(self):
        return {'schema': 1, 'source': 'passive-kernel-sysfs', 'errors': [],
                'classes': {name: {'available': True, 'devices': []} for name in
                            ('framebuffer', 'drm', 'backlight', 'rfkill', 'bluetooth', 'serial', 'thermal', 'usb')}}

    def test_bus_renumbering_and_record_order_do_not_change_contract(self):
        left = self.fixture()
        left['classes']['usb']['devices'] = [
            {'name': 'usb1', 'fields': {'busnum': '1', 'devnum': '1', 'maxchild': '1'}},
            {'name': '1-1.1:1.0', 'fields': {'bInterfaceClass': '03'}, 'driver': 'usbhid'}]
        right = copy.deepcopy(left)
        right['classes']['usb']['devices'][0].update(name='usb3', fields={'busnum': '3', 'devnum': '4', 'maxchild': '1'})
        right['classes']['usb']['devices'][1]['name'] = '3-1.1:1.0'
        right['classes']['usb']['devices'].reverse()
        self.assertTrue(compare_subsystems(left, right)['matching'])
        wrapped = lambda data: {'probes': {'subsystems': {'exit_code': 0, 'stdout': json.dumps(data)}}}
        self.assertTrue(compare(wrapped(left), wrapped(right))['matching'])

    def test_usb_port_topology_and_multiplicity_are_preserved(self):
        left = self.fixture()
        left['classes']['usb']['devices'] = [{'name': '1-1.1', 'fields': {'speed': '12'}}]
        right = copy.deepcopy(left)
        right['classes']['usb']['devices'][0]['name'] = '1-1.2'
        self.assertFalse(compare_subsystems(left, right)['matching'])
        right = copy.deepcopy(left)
        right['classes']['usb']['devices'] *= 2
        self.assertFalse(compare_subsystems(left, right)['matching'])

    def test_failed_missing_and_malformed_captures_never_match(self):
        failed = self.fixture()
        failed['errors'] = [{'field': 'temp', 'error': 'permission denied'}]
        self.assertFalse(compare_subsystems(failed, failed)['complete'])
        del failed['classes']['thermal']
        with self.assertRaises(ValueError):
            compare_subsystems(failed, failed)
        bad = {'probes': {'subsystems': {'exit_code': 0, 'stdout': '{}'}}}
        self.assertFalse(compare(bad, bad)['complete'])

    def test_display_state_and_mode_differences_are_reported(self):
        left = self.fixture()
        left['classes']['drm']['devices'] = [{'name': 'card1-DSI-1',
                                            'fields': {'modes': '720x1280', 'enabled': 'disabled'}}]
        right = copy.deepcopy(left)
        right['classes']['drm']['devices'][0]['name'] = 'card3-DSI-1'
        self.assertTrue(compare_subsystems(left, right)['matching'])
        right['classes']['drm']['devices'][0]['fields']['enabled'] = 'enabled'
        difference = compare_subsystems(left, right)['differences'][0]
        self.assertEqual(difference['class'], 'drm')
        self.assertEqual(difference['candidate']['devices'][0]['fields']['enabled'], 'enabled')


class SubsystemCaptureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def capture(self):
        result = subprocess.run([sys.executable, '-c', SUBSYSTEM_SYSFS.replace('/sys/', str(self.root) + '/')],
                                text=True, capture_output=True, timeout=5)
        return result.returncode, json.loads(result.stdout)

    def test_missing_class_is_explicit_and_empty_class_is_distinct(self):
        (self.root / 'class/bluetooth').mkdir(parents=True)
        code, result = self.capture()
        self.assertEqual(code, 0)
        self.assertEqual(result['classes']['bluetooth'], {'available': True, 'devices': []})
        self.assertEqual(result['classes']['rfkill'], {'available': False, 'devices': []})

    def test_framebuffer_console_class_is_not_a_framebuffer_device(self):
        for name in ('fb0', 'fbcon'):
            (self.root / 'class/graphics' / name).mkdir(parents=True)
        code, result = self.capture()
        self.assertEqual(code, 0)
        self.assertEqual([d['name'] for d in result['classes']['framebuffer']['devices']], ['fb0'])

    def test_captures_only_allowlisted_fields_without_writing_devices(self):
        usb = self.root / 'bus/usb/devices/1-1'
        usb.mkdir(parents=True)
        for name, value in {'idVendor': '1eaf', 'idProduct': '0024', 'serial': 'do-not-collect'}.items():
            (usb / name).write_text(value)
        code, result = self.capture()
        self.assertEqual(code, 0)
        device = result['classes']['usb']['devices'][0]
        self.assertEqual(device['fields'], {'idVendor': '1eaf', 'idProduct': '0024'})
        self.assertEqual((usb / 'serial').read_text(), 'do-not-collect')
        self.assertNotIn('do-not-collect', json.dumps(result))

    def test_failed_attribute_read_retains_partial_capture_and_nonzero_status(self):
        zone = self.root / 'class/thermal/thermal_zone0'
        zone.mkdir(parents=True)
        (zone / 'type').write_text('cpu-thermal')
        (zone / 'temp').mkdir()  # Read fails even if the tests run as root.
        code, result = self.capture()
        self.assertEqual(code, 2)
        self.assertEqual(result['errors'][0]['field'], 'temp')
        self.assertEqual(result['classes']['thermal']['devices'][0]['fields']['type'], 'cpu-thermal')
