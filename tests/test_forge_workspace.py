"""Workspace exclusion, checkpoint preservation and interrupted restore recovery."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_workspace import FILES, Workspace, WorkspaceLock, sha256
from uconsole_emulator import executable


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        for name in FILES:
            (self.path / name).write_bytes(('original ' + name).encode())
        self.workspace = Workspace(self.path, 'fixture-qemu-img')
        self.runner = patch('forge_workspace.subprocess.run', side_effect=self.convert)
        self.runner.start()
        self.addCleanup(self.runner.stop)

    @staticmethod
    def convert(argv, **kwargs):
        assert argv[1:6] == ['convert', '-f', 'qcow2', '-O', 'qcow2']
        shutil.copyfile(argv[-2], argv[-1])

    def test_exclusion_and_release(self):
        with WorkspaceLock(self.path):
            with self.assertRaisesRegex(ValueError, 'busy'):
                self.workspace.checkpoint('blocked')
            self.assertFalse((self.path / 'checkpoints').exists())
        self.workspace.checkpoint('allowed')

    def test_child_keeps_lock_after_launcher_closes_its_copy(self):
        lock = WorkspaceLock(self.path)
        child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
                                 stdin=subprocess.PIPE, pass_fds=(lock.fileno(),))
        try:
            lock.close()
            with self.assertRaisesRegex(ValueError, 'busy'):
                WorkspaceLock(self.path)
        finally:
            child.communicate(timeout=10)
        with WorkspaceLock(self.path):
            pass

    def test_checkpoint_preserves_identity_and_restore_keeps_safety_copy(self):
        original = {name: sha256(self.path / name) for name in FILES}
        manifest = self.workspace.checkpoint('baseline')
        self.assertEqual(manifest['files'], original)
        (self.path / 'disk.qcow2').write_bytes(b'user edits')
        (self.path / 'kernel8.img').write_bytes(b'user kernel')
        result = self.workspace.restore('baseline')
        self.assertEqual({name: sha256(self.path / name) for name in FILES}, original)
        safety, _ = self.workspace.verify_checkpoint(result['safety_checkpoint'])
        self.assertEqual((safety / 'disk.qcow2').read_bytes(), b'user edits')
        self.assertEqual((safety / 'kernel8.img').read_bytes(), b'user kernel')

    def test_invalid_names_and_existing_checkpoints_do_not_overwrite(self):
        for name in ('../escape', '/tmp/escape', '', '.', 'a/b'):
            with self.assertRaises(ValueError):
                self.workspace.checkpoint(name)
        self.workspace.checkpoint('once')
        with self.assertRaises(FileExistsError):
            self.workspace.checkpoint('once')

    def test_corruption_prevents_any_restore(self):
        self.workspace.checkpoint('baseline')
        (self.path / 'checkpoints/baseline/kernel8.img').write_bytes(b'corrupt')
        (self.path / 'disk.qcow2').write_bytes(b'keep user edits')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.workspace.restore('baseline')
        self.assertEqual((self.path / 'disk.qcow2').read_bytes(), b'keep user edits')
        self.assertFalse((self.path / 'restore-pending.json').exists())

    def test_interrupted_restore_blocks_launch_and_recovers_idempotently(self):
        self.workspace.checkpoint('baseline')
        (self.path / 'disk.qcow2').write_bytes(b'new disk')
        (self.path / 'kernel8.img').write_bytes(b'new kernel')
        real_replace = Path.replace

        def interrupted_replace(source, target):
            if source.name == 'kernel8.img':
                raise OSError('simulated power interruption after disk replacement')
            return real_replace(source, target)

        with patch.object(Path, 'replace', interrupted_replace):
            with self.assertRaisesRegex(OSError, 'simulated'):
                self.workspace.restore('baseline')
        self.assertTrue((self.path / 'restore-pending.json').exists())
        with self.assertRaisesRegex(ValueError, 'Interrupted restore'):
            WorkspaceLock(self.path)
        result = self.workspace.recover()
        self.assertTrue(result['recovered'])
        self.assertEqual((self.path / 'kernel8.img').read_bytes(), b'original kernel8.img')
        safety, _ = self.workspace.verify_checkpoint(result['safety_checkpoint'])
        self.assertEqual((safety / 'disk.qcow2').read_bytes(), b'new disk')
        self.assertFalse(self.workspace.recover()['recovered'])

    def test_invalid_recovery_journal_does_not_escape_workspace(self):
        (self.path / 'restore-pending.json').write_text(json.dumps({
            'schema': 1, 'stage': '../escape', 'files': dict.fromkeys(FILES, 'bad')}))
        with self.assertRaisesRegex(ValueError, 'Invalid restore journal'):
            self.workspace.recover()
        self.assertEqual((self.path / 'disk.qcow2').read_bytes(), b'original disk.qcow2')


QEMU_IMG = executable('qemu-img')
QEMU_IO = executable('qemu-io')


@unittest.skipUnless(shutil.which(QEMU_IMG) and shutil.which(QEMU_IO), 'requires QEMU image tools')
class RealImageTests(unittest.TestCase):
    def test_checkpoint_is_standalone_and_restores_real_disk_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            base = path / 'base.raw'
            base.write_bytes(b'base image' + b'\0' * (1024 * 1024 - 10))
            for name in FILES[1:]:
                (path / name).write_text('fixture ' + name)
            subprocess.run([QEMU_IMG, 'create', '-f', 'qcow2', '-F', 'raw', '-b',
                            str(base), str(path / 'disk.qcow2')], check=True, capture_output=True)
            workspace = Workspace(path, QEMU_IMG)
            workspace.checkpoint('before')
            info = json.loads(subprocess.check_output([
                QEMU_IMG, 'info', '--output=json', str(path / 'checkpoints/before/disk.qcow2')]))
            self.assertNotIn('backing-filename', info)
            subprocess.run([QEMU_IO, '-f', 'qcow2', '-c', 'write -P 42 0 512',
                            str(path / 'disk.qcow2')], check=True, capture_output=True)
            workspace.restore('before')
            subprocess.run([QEMU_IMG, 'convert', '-O', 'raw', str(path / 'disk.qcow2'),
                            str(path / 'restored.raw')], check=True)
            self.assertEqual((path / 'restored.raw').read_bytes(), base.read_bytes())


if __name__ == '__main__':
    unittest.main()
