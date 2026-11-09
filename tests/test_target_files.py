"""Local filesystem qualification before wiring target writes into SSH jobs."""
import base64
import copy
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import forge_target_files as files
if __package__:
    from .target_test_support import linux_target
else:
    from target_test_support import linux_target


@linux_target
class TargetFilesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.path = self.root / 'app'
        self.path.write_bytes(b'original')
        self.path.chmod(0o751)
        os.setxattr(self.path, 'user.forge', b'original metadata')
        self.before = self.read()
        self.after = copy.deepcopy(self.before)
        data = b'new executable contents'
        self.after.update(data=base64.b64encode(data).decode(), size=len(data),
                          sha256=hashlib.sha256(data).hexdigest(), mode=0o700,
                          mtime_ns=self.before['mtime_ns'] + 1000, xattrs={})

    def read(self):
        with files.parent_fd(str(self.path)) as (fd, name):
            return files.snapshot(fd, name, str(self.path))

    def test_apply_restore_bytes_metadata_xattrs_and_idempotence(self):
        self.assertEqual(files.apply_file(self.before, self.after)['status'], 'applied')
        self.assertEqual(self.read(), self.after)
        self.assertEqual(files.apply_file(self.before, self.after)['status'], 'already-applied')
        self.assertEqual(files.apply_file(self.after, self.before)['status'], 'applied')
        self.assertEqual(self.read(), self.before)

    def test_create_and_restore_absence(self):
        self.path.unlink()
        absent = self.read()
        self.assertEqual(files.apply_file(absent, self.after)['status'], 'applied')
        self.assertEqual(files.apply_file(self.after, absent)['status'], 'applied')
        self.assertFalse(self.path.exists())
        self.assertEqual(files.apply_file(self.after, absent)['status'], 'already-applied')

    def test_linkless_publication_uses_atomic_noreplace(self):
        self.path.unlink()
        absent = self.read()
        with patch('forge_target_files.os.link', side_effect=OSError(errno.EPERM, 'no links')):
            self.assertEqual(files.apply_file(absent, self.after)['status'], 'applied')
        self.assertEqual(self.read(), self.after)
        self.assertEqual(files.apply_file(self.after, absent)['status'], 'applied')

    def test_linkless_publication_preserves_late_creator(self):
        scratch = self.root / 'scratch'
        scratch.write_bytes(b'new')
        with files.parent_fd(str(self.path)) as (fd, name):
            with patch('forge_target_files.os.link', side_effect=OSError(errno.EOPNOTSUPP, 'no links')):
                with self.assertRaises(FileExistsError):
                    files.publish_absent(fd, scratch.name, name)
        self.assertEqual(self.path.read_bytes(), b'original')
        self.assertEqual(scratch.read_bytes(), b'new')

    def test_link_io_error_does_not_fallback(self):
        with patch('forge_target_files.os.link', side_effect=OSError(errno.EIO, 'io error')), \
                patch('forge_target_files.ctypes.CDLL') as libc:
            with self.assertRaises(OSError):
                files.publish_absent(-1, 'scratch', 'destination')
        libc.assert_not_called()

    def test_intervening_user_edit_preserved(self):
        self.path.write_bytes(b'user edit')
        with self.assertRaises(files.TargetConflict):
            files.apply_file(self.before, self.after)
        self.assertEqual(self.path.read_bytes(), b'user edit')

    def test_restore_refuses_intervening_edit(self):
        files.apply_file(self.before, self.after)
        self.path.chmod(0o644)
        with self.assertRaises(files.TargetConflict):
            files.apply_file(self.after, self.before)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)

    def test_atime_read_is_not_write_conflict(self):
        os.utime(self.path, ns=(self.before['atime_ns'] + 1000, self.before['mtime_ns']))
        files.apply_file(self.before, self.after)
        self.assertEqual(self.read(), self.after)

    def test_symlink_and_hardlink_targets_refused(self):
        other = self.root / 'other'
        other.write_bytes(b'do not change')
        self.path.unlink()
        self.path.symlink_to(other)
        with self.assertRaises(files.TargetConflict):
            files.apply_file(self.before, self.after)
        self.path.unlink()
        os.link(other, self.path)
        with self.assertRaises(files.TargetConflict):
            files.apply_file(self.before, self.after)
        self.assertEqual(other.read_bytes(), b'do not change')

    def test_parent_symlink_refused(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            with files.parent_fd(str(alias / 'app')):
                self.fail('symlink traversed')

    def test_concurrent_forge_writer_refused(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                files.apply_file(self.before, self.after)
        finally:
            os.close(fd)

    def test_staging_failure_preserves_target_and_removes_scratch(self):
        with patch('forge_target_files.os.fsync', side_effect=OSError('injected disk failure')):
            with self.assertRaises(OSError):
                files.apply_file(self.before, self.after)
        self.assertEqual(self.read(), self.before)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_conflict_during_staging_preserved(self):
        original = files.snapshot
        count = 0

        def racing_snapshot(fd, name, path):
            nonlocal count
            count += 1
            if count == 3:
                self.path.write_bytes(b'concurrent edit')
            return original(fd, name, path)

        with patch('forge_target_files.snapshot', side_effect=racing_snapshot):
            with self.assertRaises(files.TargetConflict):
                files.apply_file(self.before, self.after)
        self.assertEqual(self.path.read_bytes(), b'concurrent edit')
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_failure_after_publication_is_reconcilable_not_rolled_back(self):
        original = os.fsync
        count = 0

        def fail_directory_sync(fd):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('injected directory sync failure')
            original(fd)

        with patch('forge_target_files.os.fsync', side_effect=fail_directory_sync):
            with self.assertRaises(OSError):
                files.apply_file(self.before, self.after)
        self.assertEqual(self.read(), self.after)
        self.assertEqual(files.apply_file(self.before, self.after)['status'], 'already-applied')

    def test_mismatched_paths_and_invalid_content_rejected_before_write(self):
        for field, value in (('path', str(self.root / 'other')), ('data', 'bad base64!')):
            bad = copy.deepcopy(self.after)
            bad[field] = value
            with self.assertRaises(ValueError):
                files.apply_file(self.before, bad)
            self.assertEqual(self.read(), self.before)

    def test_absent_publication_never_clobbers_late_creator(self):
        self.path.unlink()
        absent = self.read()
        link = os.link

        def late_creator(*args, **kwargs):
            self.path.write_bytes(b'late user file')
            return link(*args, **kwargs)

        with patch('forge_target_files.os.link', side_effect=late_creator):
            with self.assertRaises(FileExistsError):
                files.apply_file(absent, self.after)
        self.assertEqual(self.path.read_bytes(), b'late user file')
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_already_applied_retries_directory_sync(self):
        files.apply_file(self.before, self.after)
        with patch('forge_target_files.os.fsync', side_effect=OSError('still not durable')):
            with self.assertRaises(OSError):
                files.apply_file(self.before, self.after)

    def stage(self):
        token = 'a' * 32
        scratch = self.root / ('.uconsole-forge-' + token)
        desired = dict(self.after, path=str(scratch))
        files.apply_file({'path': str(scratch), 'kind': 'absent'}, desired)
        return token, scratch

    def test_recover_fully_staged_unpublished_file(self):
        token, scratch = self.stage()
        self.assertEqual(files.apply_file(self.before, self.after, stage_token=token)['status'], 'applied')
        self.assertFalse(scratch.exists())
        self.assertEqual(self.read(), self.after)

    def test_recover_link_publication_before_scratch_unlink(self):
        token, scratch = self.stage()
        self.path.unlink()
        absent = {'path': str(self.path), 'kind': 'absent'}
        os.link(scratch, self.path)
        self.assertEqual(self.path.stat().st_nlink, 2)
        self.assertEqual(files.apply_file(absent, self.after, stage_token=token)['status'], 'already-applied')
        self.assertFalse(scratch.exists())
        self.assertEqual(self.path.stat().st_nlink, 1)
        self.assertEqual(self.read(), self.after)

    def test_partial_staging_is_preserved_not_deleted(self):
        token, scratch = self.stage()
        scratch.write_bytes(b'partial')
        with self.assertRaises(files.TargetConflict):
            files.apply_file(self.before, self.after, stage_token=token)
        self.assertEqual(scratch.read_bytes(), b'partial')
        self.assertEqual(self.read(), self.before)

    def test_staging_link_to_unrelated_file_is_preserved(self):
        token, scratch = self.stage()
        other = self.root / 'other'
        os.link(scratch, other)
        with self.assertRaises(files.TargetConflict):
            files.apply_file(self.before, self.after, stage_token=token)
        self.assertTrue(scratch.exists())
        self.assertEqual(self.read(), self.before)

    def test_staging_token_cannot_escape_directory(self):
        with self.assertRaises(ValueError):
            files.apply_file(self.before, self.after, stage_token='../escape')
        self.assertEqual(self.read(), self.before)


if __name__ == '__main__':
    unittest.main()
