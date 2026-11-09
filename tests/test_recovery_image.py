import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forge_recovery_image import publish, remove, inspect
from forge_target_files import publish_absent


class RecoveryImageTests(unittest.TestCase):
    def test_inspection_reports_matching_and_partial_without_mutation(self):
        publish(str(self.source), str(self.destination), self.digest, self.size, self.token)
        scratch = self.root / ('.forge-image-' + self.token)
        scratch.write_bytes(b'partial')
        scratch.chmod(0o600)
        result = inspect(str(self.destination), self.digest, self.size, self.token)
        self.assertEqual(result['destination']['state'], 'matching-bytes')
        self.assertEqual(result['scratch']['state'], 'different-size')
        self.assertFalse(result['retry_authorized'])
        self.assertEqual(scratch.read_bytes(), b'partial')
        self.assertEqual(self.destination.read_bytes(), self.source.read_bytes())

    def test_inspection_does_not_follow_symlink(self):
        self.destination.symlink_to(self.source)
        result = inspect(str(self.destination), self.digest, self.size, self.token)
        self.assertEqual(result['destination']['state'], 'unsafe-metadata')
        self.assertTrue(self.destination.is_symlink())

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        # macOS temporary paths may start at /var -> /private/var. The target
        # primitive intentionally rejects symlinked parents; canonicalize the
        # test fixture rather than weakening that production check.
        self.root = Path(directory.name).resolve()
        self.source = self.root / 'source'
        self.source.write_bytes(b'image\n' * 2048)
        self.source.chmod(0o600)
        self.destination = self.root / 'image'
        self.size = self.source.stat().st_size
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.token = 'a' * 32

    def test_publish_and_restore_absence(self):
        result = publish(str(self.source), str(self.destination), self.digest, self.size, self.token)
        self.assertEqual(result['status'], 'published')
        self.assertEqual(self.source.read_bytes(), self.destination.read_bytes())
        remove(str(self.destination), self.digest, self.size, self.token)
        self.assertFalse(self.destination.exists())

    def test_bad_digest_and_existing_destination_preserved(self):
        with self.assertRaises(ValueError):
            publish(str(self.source), str(self.destination), '0'*64, self.size, self.token)
        self.assertFalse(self.destination.exists())
        self.destination.write_bytes(b'unrelated')
        with self.assertRaises(FileExistsError):
            publish(str(self.source), str(self.destination), self.digest, self.size, self.token)
        self.assertEqual(self.destination.read_bytes(), b'unrelated')

    def test_public_parent_and_modified_restore_rejected(self):
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):
            publish(str(self.source), str(self.destination), self.digest, self.size, self.token)
        self.root.chmod(0o700)
        publish(str(self.source), str(self.destination), self.digest, self.size, self.token)
        self.destination.write_bytes(b'user edit')
        with self.assertRaises(ValueError):
            remove(str(self.destination), self.digest, self.size, self.token)
        self.assertEqual(self.destination.read_bytes(), b'user edit')

    def test_late_creator_is_not_overwritten_and_scratch_is_retained(self):
        def race(fd, temporary, name):
            self.destination.write_bytes(b'late creator')
            return publish_absent(fd, temporary, name)
        with patch('forge_recovery_image.publish_absent', side_effect=race):
            with self.assertRaises(FileExistsError):
                publish(str(self.source), str(self.destination), self.digest, self.size, self.token)
        self.assertEqual(self.destination.read_bytes(), b'late creator')
        self.assertEqual((self.root / ('.forge-image-' + self.token)).read_bytes(), self.source.read_bytes())

    def test_public_source_and_invalid_bounds_rejected(self):
        self.source.chmod(0o644)
        with self.assertRaises(ValueError):
            publish(str(self.source), str(self.destination), self.digest, self.size, self.token)
        self.source.chmod(0o600)
        for size in (0, True, 128 * 1024 * 1024 + 1):
            with self.subTest(size=size), self.assertRaises(ValueError):
                publish(str(self.source), str(self.destination), self.digest, size, self.token)
