import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from target_recovery_inventory import capture, LIMIT


class RecoveryInventoryTests(unittest.TestCase):
    def test_active_runtime_and_open_timeout_never_claim_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in (('/sys/class/watchdog/watchdog0/state', 'active\n'),
                                ('/sys/module/watchdog/parameters/open_timeout', '120\n')):
                path = root / name.lstrip('/')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value)
            observed = capture(root)['watchdog_observations']
        self.assertEqual(observed['runtime_state'], 'active')
        self.assertEqual(observed['kernel_open_timeout'], '120')
        self.assertFalse(observed['firmware_handoff_qualified'])
        self.assertFalse(observed['failed_boot_fallback_qualified'])

    def test_missing_is_not_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            result = capture(Path(directory))
        self.assertFalse(result['recovery_qualified'])
        self.assertEqual(result['errors'], [])
        self.assertTrue(all(v['status'] == 'absent' for v in result['files'].values()))

    def test_binary_boot_property_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'partition'
            path.write_bytes(b'\0\0\0\1')
            with patch('target_recovery_inventory.PATHS', ('/partition',)):
                result = capture(Path(directory))
            self.assertEqual(path.read_bytes(), b'\0\0\0\1')
        self.assertEqual(base64.b64decode(result['files']['/partition']['base64']), b'\0\0\0\1')
        self.assertFalse(result['recovery_qualified'])

    def test_oversized_and_unreadable_are_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'large').write_bytes(b'x' * (LIMIT + 1))
            (root / 'directory').mkdir()
            with patch('target_recovery_inventory.PATHS', ('/large', '/directory')):
                result = capture(root)
        self.assertEqual(len(result['errors']), 2)
        self.assertTrue(all(v['status'] == 'error' for v in result['files'].values()))
