"""Internal exact symlink transitions; caller must journal and quiesce writers.

Restores literal target, ownership and mtime, not arbitrary symlink xattrs or
atime. Does not follow the link, create directories, or call systemctl.
"""
import os
import re
import stat
import uuid

from forge_target_backup import paths_checked
from forge_target_files import TargetConflict, parent_fd


def validate(record):
    if not isinstance(record, dict):
        raise ValueError('Link state must be an object')
    paths_checked([record.get('path')])
    if record.get('kind') == 'absent' and set(record) == {'path', 'kind'}:
        return record
    if (set(record) != {'path', 'kind', 'target', 'uid', 'gid', 'mtime_ns'} or
            record['kind'] != 'symlink' or not isinstance(record['target'], str) or
            not 1 <= len(record['target']) <= 4096 or
            any(ord(c) < 32 or ord(c) == 127 for c in record['target']) or
            any(type(record[k]) is not int or record[k] < 0 for k in ('uid', 'gid', 'mtime_ns'))):
        raise ValueError('Invalid symlink preimage')
    return record


def snapshot(fd, name, path, *, links=1):
    try:
        before = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return {'path': path, 'kind': 'absent'}
    if not stat.S_ISLNK(before.st_mode) or before.st_nlink != links:
        raise TargetConflict('Expected a singly linked symlink or absence, never a regular file')
    target = os.readlink(name, dir_fd=fd)
    after = os.stat(name, dir_fd=fd, follow_symlinks=False)
    def identity(info):
        return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_nlink,
                info.st_mode, info.st_mtime_ns, info.st_ctime_ns)
    if identity(before) != identity(after):
        raise TargetConflict('Symlink changed during inspection')
    return validate({'path': path, 'kind': 'symlink', 'target': target,
                     'uid': before.st_uid, 'gid': before.st_gid, 'mtime_ns': before.st_mtime_ns})


def recover_stage(fd, name, temporary, desired):
    try:
        info = os.stat(temporary, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if (desired['kind'] != 'symlink' or info.st_nlink not in (1, 2) or
            snapshot(fd, temporary, desired['path'], links=info.st_nlink) != desired):
        raise TargetConflict('Unrecognized link scratch; preserve for inspection')
    if info.st_nlink == 2:
        published = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (published.st_dev, published.st_ino):
            raise TargetConflict('Scratch is linked elsewhere; preserve it')
        os.unlink(temporary, dir_fd=fd)
        os.fsync(fd)
        return False
    return True


def apply_link(expected, desired, *, stage_token=None):
    validate(expected)
    validate(desired)
    path = expected['path']
    if path != desired['path']:
        raise ValueError('Link transition paths differ')
    token = uuid.uuid4().hex if stage_token is None else stage_token
    if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{32}', token):
        raise ValueError('Invalid link journal token')
    temporary = '.uconsole-link-' + token
    with parent_fd(path) as (fd, name):
        staged = recover_stage(fd, name, temporary, desired)
        current = snapshot(fd, name, path)
        if current == desired:
            if staged:
                os.unlink(temporary, dir_fd=fd)
            os.fsync(fd)
            return {'status': 'already-applied', 'path': path}
        if current != expected:
            raise TargetConflict('Link no longer matches its journaled preimage')
        try:
            if desired['kind'] == 'symlink' and not staged:
                os.symlink(desired['target'], temporary, dir_fd=fd)
                staged = True
                os.chown(temporary, desired['uid'], desired['gid'], dir_fd=fd, follow_symlinks=False)
                os.utime(temporary, ns=(desired['mtime_ns'], desired['mtime_ns']), dir_fd=fd, follow_symlinks=False)
                if snapshot(fd, temporary, path) != desired:
                    raise TargetConflict('Staged link metadata did not round-trip')
            os.fsync(fd)
            if snapshot(fd, name, path) != expected:
                raise TargetConflict('Link changed during staging')
            if desired['kind'] == 'absent':
                os.unlink(name, dir_fd=fd)
            elif expected['kind'] == 'absent':
                os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                os.unlink(temporary, dir_fd=fd)
                staged = False
            else:
                os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                staged = False
            os.fsync(fd)
            if snapshot(fd, name, path) != desired:
                raise TargetConflict('Link publication uncertain; reconcile journal')
            return {'status': 'applied', 'path': path}
        finally:
            if staged:
                os.unlink(temporary, dir_fd=fd)
