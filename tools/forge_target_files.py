"""Internal single-file apply/restore primitive for quiescent SSH targets.

Callers must first durably journal verified host preimages and the desired state.
This is not a multi-file transaction, service/package rollback, or boot recovery.
Directory locks coordinate forge writers only; stop other writers beforehand.
"""
import base64
import ctypes
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import os
import re
import stat
import sys
import uuid

from forge_target_backup import MAX_FILE, paths_checked, verify


class TargetConflict(RuntimeError):
    """Target changed; preserve it and reconcile rather than overwriting it."""


def publish_absent(fd, temporary, name):
    """Atomic no-clobber publication, including Linux filesystems without links."""
    try:
        os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
    except OSError as exc:
        if sys.platform != 'linux' or exc.errno not in (errno.EPERM, errno.EOPNOTSUPP):
            raise
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, 'renameat2', None)
        if rename is None:
            raise OSError(errno.ENOSYS, 'Atomic no-replace rename is unavailable') from exc
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                           ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(fd, os.fsencode(temporary), fd, os.fsencode(name), 1) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), name)
    else:
        os.unlink(temporary, dir_fd=fd)


@contextmanager
def parent_fd(path):
    paths_checked([path])
    parts = path.split('/')[1:]
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        # Nonblocking: an occupied target never hangs an SSH job indefinitely.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd, parts[-1]
    finally:
        os.close(fd)


def snapshot(fd, name, path, *, links=1):
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return {'path': path, 'kind': 'absent'}
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != links or info.st_size > MAX_FILE:
        raise TargetConflict('Unsupported or oversized target file')
    source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK, dir_fd=fd)
    try:
        def signature(s):
            return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid,
                    s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if signature(info) != signature(os.fstat(source)):
            raise TargetConflict('Target changed before read')
        attrs = {key: base64.b64encode(os.getxattr(source, key)).decode()
                 for key in os.listxattr(source)}
        with os.fdopen(os.dup(source), 'rb') as stream:
            data = stream.read(MAX_FILE + 1)
        if (len(data) != info.st_size or signature(info) != signature(os.fstat(source)) or
                signature(info) != signature(os.stat(name, dir_fd=fd, follow_symlinks=False))):
            raise TargetConflict('Target changed during read')
        return {'path': path, 'kind': 'file', 'size': len(data),
                'data': base64.b64encode(data).decode(), 'sha256': hashlib.sha256(data).hexdigest(),
                'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode),
                'atime_ns': info.st_atime_ns, 'mtime_ns': info.st_mtime_ns, 'xattrs': attrs}
    finally:
        os.close(source)


def equivalent(left, right):
    # Ordinary reads can update atime; it is restored but not a write conflict.
    return {k: v for k, v in left.items() if k != 'atime_ns'} == {
        k: v for k, v in right.items() if k != 'atime_ns'}


def recover_stage(fd, name, temporary, desired):
    """Recognize only the exact journaled scratch name and verified payload."""
    try:
        info = os.stat(temporary, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if desired['kind'] != 'file' or info.st_nlink not in (1, 2):
        raise TargetConflict('Unrecognized staging file; preserve for inspection: ' + temporary)
    if not equivalent(snapshot(fd, temporary, desired['path'], links=info.st_nlink), desired):
        raise TargetConflict('Incomplete or changed staging file; preserve for inspection: ' + temporary)
    source = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        os.fsync(source)
    finally:
        os.close(source)
    if info.st_nlink == 2:
        published = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if (published.st_dev, published.st_ino) != (info.st_dev, info.st_ino):
            raise TargetConflict('Staging hard link is not the journaled destination')
        # Publication happened; only unlink the second name of the same inode.
        os.unlink(temporary, dir_fd=fd)
        os.fsync(fd)
        return False
    return True


def apply_file(expected, desired, *, stage_token=None):
    """Apply one journaled state transition; swapping arguments restores it.

    Re-entry at the desired state is safe. Exceptions after publication imply
    uncertain completion: retain the journal and inspect, never infer rollback.
    """
    path = expected['path']
    for item in (expected, desired):
        verify({'schema': 1, 'machine_id': '0' * 32, 'files': [item]}, [path])
    stage_token = uuid.uuid4().hex if stage_token is None else stage_token
    if not isinstance(stage_token, str) or not re.fullmatch('[0-9a-f]{32}', stage_token):
        raise ValueError('Invalid journal staging token')
    temporary = '.uconsole-forge-' + stage_token
    with parent_fd(path) as (fd, name):
        staged = recover_stage(fd, name, temporary, desired)
        current = snapshot(fd, name, path)
        if equivalent(current, desired):
            if staged:
                os.unlink(temporary, dir_fd=fd)
            # A prior SSH attempt may have published and then failed while
            # syncing the directory. Re-entry must finish that durability step.
            os.fsync(fd)
            return {'status': 'already-applied', 'path': path}
        if not equivalent(current, expected):
            raise TargetConflict('Target no longer matches journaled preimage')
        try:
            if desired['kind'] == 'file' and not staged:
                out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              0o600, dir_fd=fd)
                staged = True
                with os.fdopen(out, 'wb') as stream:
                    stream.write(base64.b64decode(desired['data'], validate=True))
                    stream.flush()
                    # chown can clear special bits; permissions and xattrs follow it.
                    os.fchown(stream.fileno(), desired['uid'], desired['gid'])
                    os.fchmod(stream.fileno(), desired['mode'])
                    for key in os.listxattr(stream.fileno()):
                        os.removexattr(stream.fileno(), key)
                    for key, value in desired['xattrs'].items():
                        os.setxattr(stream.fileno(), key, base64.b64decode(value, validate=True))
                    os.utime(stream.fileno(), ns=(desired['atime_ns'], desired['mtime_ns']))
                    os.fsync(stream.fileno())
                if snapshot(fd, temporary, path) != desired:
                    raise TargetConflict('Staged metadata/content did not round-trip')
            if not equivalent(snapshot(fd, name, path), expected):
                raise TargetConflict('Target changed during staging')
            if desired['kind'] == 'absent':
                os.unlink(name, dir_fd=fd)
            elif expected['kind'] == 'absent':
                # Atomic no-clobber publication for previously absent paths.
                publish_absent(fd, temporary, name)
                staged = False
            else:
                os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                staged = False
            os.fsync(fd)
            if not equivalent(snapshot(fd, name, path), desired):
                raise TargetConflict('Post-publication verification failed; reconcile journal')
            return {'status': 'applied', 'path': path}
        finally:
            if staged:
                os.unlink(temporary, dir_fd=fd)
