import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import forge_target_links as links


class TargetLinkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.path = self.root / 'proof.service'
        self.absent = {'path': str(self.path), 'kind': 'absent'}
        self.desired = {'path': str(self.path), 'kind': 'symlink', 'target': '../proof.service',
                        'uid': os.getuid(), 'gid': os.getgid(), 'mtime_ns': 1000000000}

    def read(self):
        with links.parent_fd(str(self.path)) as (fd, name):
            return links.snapshot(fd, name, str(self.path))

    def test_create_restore_absence_and_idempotence(self):
        links.apply_link(self.absent, self.desired)
        self.assertEqual(self.read(), self.desired)
        self.assertEqual(links.apply_link(self.absent, self.desired)['status'], 'already-applied')
        links.apply_link(self.desired, self.absent)
        self.assertEqual(self.read(), self.absent)

    def test_replace_restores_literal_target_and_metadata_without_touching_referent(self):
        referent = self.root / 'actual'
        referent.write_bytes(b'untouched')
        original = dict(self.desired, target=str(referent))
        links.apply_link(self.absent, original)
        links.apply_link(original, self.desired)
        links.apply_link(self.desired, original)
        self.assertEqual(self.read(), original)
        self.assertEqual(referent.read_bytes(), b'untouched')

    def test_regular_file_and_unexpected_link_are_preserved(self):
        self.path.write_bytes(b'user file')
        with self.assertRaises(links.TargetConflict):
            links.apply_link(self.absent, self.desired)
        self.assertEqual(self.path.read_bytes(), b'user file')
        self.path.unlink()
        self.path.symlink_to('user-target')
        with self.assertRaises(links.TargetConflict):
            links.apply_link(self.absent, self.desired)
        self.assertEqual(os.readlink(self.path), 'user-target')

    def test_late_creation_is_never_clobbered(self):
        original = os.link
        def racing(*args, **kwargs):
            self.path.symlink_to('late-target')
            return original(*args, **kwargs)
        with patch.object(links.os, 'link', side_effect=racing):
            with self.assertRaises(FileExistsError):
                links.apply_link(self.absent, self.desired)
        self.assertEqual(os.readlink(self.path), 'late-target')
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def stage(self):
        token = 'a' * 32
        scratch = self.root / ('.uconsole-link-' + token)
        state = dict(self.desired, path=str(scratch))
        links.apply_link({'path': str(scratch), 'kind': 'absent'}, state)
        return token, scratch

    def test_recover_staged_and_published_links(self):
        token, scratch = self.stage()
        links.apply_link(self.absent, self.desired, stage_token=token)
        self.assertFalse(scratch.is_symlink())
        links.apply_link(self.desired, self.absent)
        token, scratch = self.stage()
        os.link(scratch, self.path, follow_symlinks=False)
        self.assertEqual(self.path.lstat().st_nlink, 2)
        self.assertEqual(links.apply_link(self.absent, self.desired, stage_token=token)['status'], 'already-applied')
        self.assertFalse(scratch.is_symlink())
        self.assertEqual(self.path.lstat().st_nlink, 1)

    def test_changed_scratch_is_preserved(self):
        token, scratch = self.stage()
        scratch.unlink()
        scratch.symlink_to('unexpected')
        with self.assertRaises(links.TargetConflict):
            links.apply_link(self.absent, self.desired, stage_token=token)
        self.assertEqual(os.readlink(scratch), 'unexpected')

    def test_failure_after_publication_is_reconciled(self):
        original = os.fsync
        count = 0
        def failure(fd):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('sync failed after publication')
            return original(fd)
        with patch.object(links.os, 'fsync', side_effect=failure):
            with self.assertRaises(OSError):
                links.apply_link(self.absent, self.desired)
        self.assertEqual(self.read(), self.desired)
        self.assertEqual(links.apply_link(self.absent, self.desired)['status'], 'already-applied')

    def test_parent_symlink_and_bad_token_rejected(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            links.apply_link(dict(self.absent, path=str(alias / 'proof.service')),
                             dict(self.desired, path=str(alias / 'proof.service')))
        with self.assertRaises(ValueError):
            links.apply_link(self.absent, self.desired, stage_token='../unsafe')


if __name__ == '__main__':
    unittest.main()
