"""Read-only keyboard descriptor capture resolves HID ownership through sysfs."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from uconsole_hardware_probe import KEYBOARD_SYSFS


class KeyboardCaptureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.usb = self.root / 'usb/devices/1-1'
        self.usb.mkdir(parents=True)
        self.hids = self.root / 'hid/devices'
        self.hids.mkdir(parents=True)
        for name, value in {'idVendor': '1eaf', 'idProduct': '0024',
                            'manufacturer': 'ClockworkPI', 'product': 'uConsole',
                            'serial': '20230713'}.items():
            (self.usb / name).write_text(value + '\n')
        (self.usb / 'descriptors').write_bytes(b'usb-contract')
        self.hid = self.usb / '1-1:1.0/0003:1EAF:0024.0001'
        self.hid.mkdir(parents=True)
        (self.hid / 'report_descriptor').write_bytes(b'hid-contract')
        (self.hids / self.hid.name).symlink_to(self.hid)

    def capture(self):
        return subprocess.run([sys.executable, '-c',
                               KEYBOARD_SYSFS.replace('/sys/bus', str(self.root))],
                              capture_output=True, text=True, timeout=5)

    def test_reads_exact_bytes_and_ignores_unrelated_hid(self):
        (self.hids / 'unrelated').mkdir()
        result = self.capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        device = json.loads(result.stdout)['devices'][0]
        self.assertEqual(bytes.fromhex(device['usb_descriptors_hex']), b'usb-contract')
        self.assertEqual(bytes.fromhex(device['report_descriptors'][0]['hex']), b'hid-contract')
        self.assertEqual(device['identity']['serial'], '20230713')

    def test_missing_report_is_a_failed_capture(self):
        (self.hid / 'report_descriptor').unlink()
        result = self.capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')

    def test_missing_or_ambiguous_keyboard_is_rejected(self):
        (self.usb / 'idVendor').write_text('ffff')
        self.assertNotEqual(self.capture().returncode, 0)
        (self.usb / 'idVendor').write_text('1eaf')
        (self.usb.parent / 'alias').symlink_to(self.usb)
        self.assertNotEqual(self.capture().returncode, 0)
