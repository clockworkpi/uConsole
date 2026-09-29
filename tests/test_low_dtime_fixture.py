import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from validate_low_dtime_fixture import validate


class LowDtimeFixtureTests(unittest.TestCase):
    def test_existing_output_is_never_adopted_or_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preserved = root/'keep'
            preserved.write_bytes(b'original')
            with patch('validate_low_dtime_fixture.subprocess.run') as run:
                with self.assertRaises(FileExistsError):
                    validate(root)
                run.assert_not_called()
            self.assertEqual(preserved.read_bytes(), b'original')

    def test_tool_failure_retains_failed_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'fixture'
            with patch('validate_low_dtime_fixture.subprocess.run',
                       return_value=SimpleNamespace(returncode=1, stdout='', stderr='fixture failure')):
                with self.assertRaises(AssertionError):
                    validate(output)
            self.assertEqual(json.loads((output/'acceptance.json').read_text())['status'], 'incomplete')
            self.assertEqual(json.loads((output/'command-00.json').read_text())['returncode'], 1)
            self.assertEqual((output/'low-dtime.img').stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(all(shutil.which(command) for command in ('mke2fs', 'debugfs', 'e2fsck', 'e2undo')),
                         'ext4 fixture tools required')
    def test_actual_timestamp_warning_repair_and_exact_undo(self):
        with tempfile.TemporaryDirectory() as directory:
            result = validate(Path(directory)/'fixture')
            self.assertEqual(result['status'], 'passed')
            self.assertTrue(result['warning_reproduced'])
            self.assertTrue(result['repaired_copy_checked'])
            self.assertTrue(result['undo_restored_exact_bytes'])
            self.assertTrue(result['original_preserved'])
            self.assertFalse(result['physical_qualified'])
            self.assertFalse(result['target_written'])
