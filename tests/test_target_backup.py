"""Preimages are read-only, explicit, private, and content-verified."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_target_backup import CAPTURE, capture, paths_checked, verify
if __package__:
    from .target_test_support import file_backup, linux_target
else:
    from target_test_support import file_backup, linux_target


class TargetBackupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.source = self.root / 'application'
        self.source.write_bytes(b'original\0payload')
        self.source.chmod(0o751)

    def remote(self, paths):
        return subprocess.run([sys.executable, '-c', CAPTURE], input=json.dumps(paths),
                              text=True, capture_output=True, timeout=5)

    @linux_target
    def test_regular_and_absent_with_metadata_and_no_atime_change(self):
        os.setxattr(self.source, 'user.forge-test', b'metadata')
        paths = [str(self.source), str(self.root / 'new-file')]
        before = self.source.stat()
        result = self.remote(paths)
        self.assertEqual(result.returncode, 0, result.stderr)
        after = self.source.stat()
        self.assertEqual((before.st_atime_ns, before.st_mtime_ns, before.st_ctime_ns),
                         (after.st_atime_ns, after.st_mtime_ns, after.st_ctime_ns))
        record = verify(json.loads(result.stdout), paths)
        self.assertEqual(record['files'][0]['mode'], 0o751)
        self.assertEqual(record['files'][0]['xattrs'], {'user.forge-test': 'bWV0YWRhdGE='})
        self.assertEqual(record['files'][1], {'kind': 'absent', 'path': paths[1]})
        self.assertFalse((self.root / 'new-file').exists())

    @linux_target
    def test_symlinks_hardlinks_directories_and_missing_parents_refused(self):
        link = self.root / 'link'
        link.symlink_to(self.source)
        parent = self.root / 'alias'
        parent.symlink_to(self.root, target_is_directory=True)
        hard = self.root / 'hard'
        os.link(self.source, hard)
        for path in (link, parent / 'application', hard, self.root, self.root / 'missing' / 'file'):
            with self.subTest(path=path):
                result = self.remote([str(path)])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')

    @linux_target
    def test_oversize_refused(self):
        with self.source.open('wb') as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
        self.assertNotEqual(self.remote([str(self.source)]).returncode, 0)

    def test_invalid_paths_rejected_before_ssh(self):
        for paths in ([], ['/'], ['relative'], ['/a/../b'], ['/a//b'], ['/a/'],
                      ['/a', '/a'], ['/a\nb'], ['/a/./b']):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                paths_checked(paths)

    def test_tamper_rejected(self):
        paths = [str(self.source)]
        original = file_backup(self.source)
        for field, value in [('data', 'ZmFrZQ=='), ('size', True), ('mode', -1),
                             ('sha256', '0' * 64), ('kind', 'directory')]:
            bad = copy.deepcopy(original)
            bad['files'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify(bad, paths)
        with self.assertRaises(ValueError):
            verify(original, ['/different'])

    def test_host_artifact_private_exclusive_and_verified(self):
        paths = [str(self.source)]
        response = subprocess.CompletedProcess([], 0, json.dumps(file_backup(self.source)), '')
        output = self.root / 'backup.json'
        with patch('forge_target_backup.subprocess.run', return_value=response) as run:
            result = capture('jkh@clockworkpi.local', paths, output)
            self.assertEqual(result['status'], 'verified-preimages')
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(run.call_count, 1)
            with self.assertRaises(FileExistsError):
                capture('jkh@clockworkpi.local', paths, output)
            self.assertEqual(run.call_count, 1)

    def test_failed_capture_never_leaves_valid_backup(self):
        output = self.root / 'backup.json'
        failure = subprocess.CompletedProcess([], 1, '', 'capture failure')
        with patch('forge_target_backup.subprocess.run', return_value=failure):
            with self.assertRaises(RuntimeError):
                capture('clockworkpi.local', [str(self.source)], output)
        self.assertEqual(output.read_bytes(), b'')

    def test_host_option_injection_rejected(self):
        with patch('forge_target_backup.subprocess.run') as run:
            for host in ('-oProxyCommand=bad', 'host; bad', 'user@host extra'):
                with self.assertRaises(ValueError):
                    capture(host, [str(self.source)], self.root / 'backup.json')
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
