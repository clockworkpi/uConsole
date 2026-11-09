"""Safety checks for the real desktop preparation acceptance harness."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from validate_desktop_preparation_gui import group_members, main


class DesktopPreparationValidationTests(unittest.TestCase):
    def test_process_scan_selects_only_exact_owned_group(self):
        result = subprocess.CompletedProcess([], 0,
            ' 10 10 Ss python3\n 11 10 Rl qemu-system-aar\n 12 20 Rl qemu-system-aar\n 13 10 Z dead\n', '')
        with patch('validate_desktop_preparation_gui.subprocess.run', return_value=result):
            rows = group_members(10)
        self.assertEqual([row['pid'] for row in rows], [10, 11, 13])
        self.assertEqual(rows[-1]['state'], 'Z')

    def test_process_scan_failure_is_not_treated_as_empty_group(self):
        with patch('validate_desktop_preparation_gui.subprocess.run',
                   side_effect=subprocess.CalledProcessError(1, 'ps')):
            with self.assertRaises(subprocess.CalledProcessError):
                group_members(10)

    def test_checksum_failure_creates_no_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'source.img'
            image.write_bytes(b'not the approved image')
            output = Path(directory) / 'acceptance'
            with patch.object(sys, 'argv', ['validator', '--image', str(image),
                                            '--sha256', '0' * 64, '--output', str(output)]):
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    main()
            self.assertFalse(output.exists())
            self.assertEqual(image.read_bytes(), b'not the approved image')


if __name__ == '__main__':
    unittest.main()
