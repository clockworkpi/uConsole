"""Shared, headless image lifecycle primitives for the uConsole forge.

Checkpoints are standalone qcow2 images, not links to mutable backing files.
Restore preserves a safety checkpoint and journals the multi-file replacement.
All supported hosts (Linux/macOS) provide flock; QEMU's image locks additionally
reject guests started outside the forge.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid


FILES = ('disk.qcow2', 'kernel8.img', 'cm4-original.dtb', 'cm4-qemu.dtb', 'machine.json')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z')


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_file(path):
    with path.open('rb') as stream:
        os.fsync(stream.fileno())


def write_json(path, value):
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        with temporary.open('x') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Publish complete JSON without overwriting.
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


class WorkspaceLock:
    """Nonblocking process-wide lock; never unlink a potentially held lockfile."""
    def __init__(self, workspace, allow_recovery=False):
        self.workspace = Path(workspace).resolve()
        self.stream = (self.workspace / '.forge.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.stream.seek(0, os.SEEK_END)
                if self.stream.tell() == 0:
                    self.stream.write(b'\0')
                    self.stream.flush()
                self.stream.seek(0)
                try:
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise BlockingIOError('Workspace lock unavailable') from exc
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not allow_recovery and (self.workspace / 'restore-pending.json').exists():
                raise ValueError('Interrupted restore: run recover before using this workspace')
        except BlockingIOError as exc:
            self.close()
            raise ValueError('Workspace is busy in another forge operation') from exc
        except BaseException:
            self.close()
            raise

    def fileno(self):
        return self.stream.fileno()

    def close(self):
        # Closing rather than LOCK_UN preserves the lock in an inheriting QEMU
        # process if its launcher exits unexpectedly.
        self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Workspace:
    def __init__(self, path, qemu_img):
        self.path = Path(path).resolve()
        self.qemu_img = str(qemu_img)

    def checkpoint_path(self, name):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError('Checkpoint name must use 1-80 letters, digits, dots, underscores or hyphens')
        root = self.path / 'checkpoints'
        if root.is_symlink():
            raise ValueError('Checkpoint directory must not be a symlink')
        path = root / name
        if path.is_symlink():
            raise ValueError('Checkpoint must not be a symlink')
        return path

    def _checkpoint(self, name):
        target = self.checkpoint_path(name)
        target.parent.mkdir(exist_ok=True)
        if target.exists():
            raise FileExistsError(target)
        stage = target.parent / ('.pending-' + uuid.uuid4().hex)
        stage.mkdir()
        # Retain incomplete staging directories as diagnostic/recovery evidence.
        subprocess.run([self.qemu_img, 'convert', '-f', 'qcow2', '-O', 'qcow2',
                        str(self.path / 'disk.qcow2'), str(stage / 'disk.qcow2')],
                       check=True, capture_output=True, text=True)
        for name_ in FILES[1:]:
            shutil.copyfile(self.path / name_, stage / name_)
        manifest = {'schema': 1, 'name': name, 'files': {}}
        for name_ in FILES:
            sync_file(stage / name_)
            manifest['files'][name_] = sha256(stage / name_)
        write_json(stage / 'checkpoint.json', manifest)
        stage.rename(target)
        sync_directory(target.parent)
        return manifest

    def checkpoint(self, name):
        with WorkspaceLock(self.path):
            return self._checkpoint(name)

    def verify_checkpoint(self, name):
        source = self.checkpoint_path(name)
        manifest = json.loads((source / 'checkpoint.json').read_text())
        if (manifest.get('schema') != 1 or manifest.get('name') != name or
                set(manifest.get('files', {})) != set(FILES)):
            raise ValueError('Invalid checkpoint manifest')
        for name_ in FILES:
            path = source / name_
            if path.is_symlink() or sha256(path) != manifest['files'][name_]:
                raise ValueError(f'Checkpoint integrity failure: {name_}')
        return source, manifest

    def restore(self, name):
        with WorkspaceLock(self.path):
            source, manifest = self.verify_checkpoint(name)
            safety = 'before-restore-' + uuid.uuid4().hex
            self._checkpoint(safety)  # Also proves QEMU can lock the current disk.
            stage = self.path / ('.restore-' + uuid.uuid4().hex)
            stage.mkdir()
            for name_ in FILES:
                shutil.copyfile(source / name_, stage / name_)
                sync_file(stage / name_)
            sync_directory(stage)
            journal = {'schema': 1, 'stage': stage.name, 'checkpoint': name,
                       'safety_checkpoint': safety, 'files': manifest['files']}
            write_json(self.path / 'restore-pending.json', journal)
            self._finish_restore(journal)
            return {'restored': name, 'safety_checkpoint': safety}

    def _finish_restore(self, journal):
        stage_name = journal.get('stage', '')
        if (journal.get('schema') != 1 or
                not re.fullmatch(r'\.restore-[0-9a-f]{32}', stage_name) or
                set(journal.get('files', {})) != set(FILES)):
            raise ValueError('Invalid restore journal; manual recovery required')
        stage = self.path / stage_name
        if stage.is_symlink():
            raise ValueError('Invalid restore staging directory')
        # Verify every remaining replacement before making any further changes.
        for name_ in FILES:
            candidate = stage / name_
            if not candidate.exists():
                candidate = self.path / name_
            if candidate.is_symlink() or sha256(candidate) != journal['files'][name_]:
                raise ValueError(f'Restore integrity failure: {name_}; safety checkpoint retained')
        for name_ in FILES:
            candidate = stage / name_
            if candidate.exists():
                candidate.replace(self.path / name_)
        sync_directory(self.path)
        (self.path / 'restore-pending.json').unlink()
        sync_directory(self.path)
        stage.rmdir()

    def recover(self):
        with WorkspaceLock(self.path, allow_recovery=True):
            journal_path = self.path / 'restore-pending.json'
            if not journal_path.exists():
                return {'recovered': False}
            journal = json.loads(journal_path.read_text())
            self._finish_restore(journal)
            return {'recovered': True, 'restored': journal['checkpoint'],
                    'safety_checkpoint': journal['safety_checkpoint']}
