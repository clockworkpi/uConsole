import hashlib
from pathlib import Path
import tempfile
import unittest

from validate_recovery_watchdog import storage_fixture, verify_storage


class StorageRangeTests(unittest.TestCase):
    def test_boot_changes_explicitly_bounded_and_post_probe_state_frozen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'fixture.img'
            fixture = storage_fixture(path)
            source = path.read_bytes()
            self.assertEqual(fixture['prefix_sha256'], hashlib.sha256(source[:fixture['boot_offset']]).hexdigest())
            self.assertEqual(fixture['root_sha256'], hashlib.sha256(source[fixture['root_offset']:]).hexdigest())
            self.assertTrue(verify_storage(path, fixture)['unchanged'])
            with path.open('r+b') as output:
                output.seek(fixture['boot_offset']+512)
                output.write(b'fixture boot mutation')
            with self.assertRaisesRegex(ValueError, 'Read-only'):
                verify_storage(path, fixture)
            accepted = verify_storage(path, fixture, allow_boot_changes=True)
            self.assertFalse(accepted['unchanged'])
            self.assertTrue(accepted['protected_prefix_unchanged'])
            self.assertTrue(accepted['root_unchanged'])
            self.assertEqual(accepted['initial_sha256'], fixture['sha256'])
            with path.open('r+b') as output:
                output.seek(fixture['boot_offset']+1024)
                output.write(b'late unapproved mutation')
            with self.assertRaisesRegex(ValueError, 'after the boot-file probe'):
                verify_storage(path, fixture, allow_boot_changes=True, expected_sha256=accepted['sha256'])

    def test_root_table_and_geometry_changes_are_never_boot_only_changes(self):
        for target in ('prefix', 'root', 'size'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'fixture.img'
                fixture = storage_fixture(path)
                with path.open('r+b') as output:
                    if target == 'size':
                        output.truncate(fixture['bytes']-1)
                    else:
                        output.seek(440 if target == 'prefix' else fixture['root_offset'])
                        output.write(b'changed')
                with self.assertRaises(ValueError):
                    verify_storage(path, fixture, allow_boot_changes=True)

    def test_nonboolean_scope_rejected_before_reading_any_path(self):
        for invalid in (None, 1, 'yes'):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                verify_storage(None, None, allow_boot_changes=invalid)
