"""Qualify file apply/restore on an explicitly supplied disposable FAT mount.

Never point this validator at a physical boot partition. It creates only two
exclusive fixture files and restores their original absence on success.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path

from forge_target_files import apply_file, equivalent, parent_fd, snapshot


CHECKS = ['linkless-create', 'metadata-readback', 'retry', 'content-replace',
          'restore-original', 'collision-preserved', 'restore-absence']


def run(directory, mode=0o755):
    if type(mode) is not int or mode not in (0o700, 0o755):
        raise ValueError('Fixture requires an explicit public or private FAT mask')
    directory = Path(directory).resolve(strict=True)
    path = directory / 'forge-fat-fixture.txt'
    competitor = directory / 'forge-fat-competitor.txt'
    if path.exists() or competitor.exists():
        raise ValueError('Fixture paths must be absent')
    payload = b'forge FAT apply and restore qualification\n'
    absent = {'path': str(path), 'kind': 'absent'}
    desired = dict(absent, kind='file', size=len(payload),
                   data=base64.b64encode(payload).decode(),
                   sha256=hashlib.sha256(payload).hexdigest(),
                   uid=0, gid=0, mode=mode, xattrs={},
                   atime_ns=1577836800000000000, mtime_ns=1577836800000000000)
    # A direct hard-link attempt must fail on the actual filesystem. This
    # prevents an ext4 run from accidentally being reported as FAT coverage.
    with competitor.open('xb') as stream:
        stream.write(b'preserve me')
        stream.flush()
        os.fsync(stream.fileno())
    import errno
    try:
        os.link(competitor, path)
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.EOPNOTSUPP):
            raise
    else:
        raise RuntimeError('Expected a filesystem without hard links; fixtures retained')
    apply_file(absent, desired)
    with parent_fd(str(path)) as (fd, name):
        if not equivalent(snapshot(fd, name, str(path)), desired):
            raise RuntimeError('FAT state differs after apply')
    if apply_file(absent, desired)['status'] != 'already-applied':
        raise RuntimeError('Retry did not reconcile')
    changed_payload = b'forge replacement boot-file fixture\n'
    changed = dict(desired, size=len(changed_payload),
                   data=base64.b64encode(changed_payload).decode(),
                   sha256=hashlib.sha256(changed_payload).hexdigest())
    apply_file(desired, changed)
    with parent_fd(str(path)) as (fd, name):
        if not equivalent(snapshot(fd, name, str(path)), changed):
            raise RuntimeError('FAT replacement differs')
    apply_file(changed, desired)
    with parent_fd(str(path)) as (fd, name):
        if not equivalent(snapshot(fd, name, str(path)), desired):
            raise RuntimeError('FAT original content was not restored')
    from forge_target_files import publish_absent
    with parent_fd(str(path)) as (fd, name):
        try:
            publish_absent(fd, competitor.name, name)
        except FileExistsError:
            pass
        else:
            raise RuntimeError('Collision unexpectedly published')
    if competitor.read_bytes() != b'preserve me':
        raise RuntimeError('Collision changed competitor')
    apply_file(desired, absent)
    competitor.unlink()
    with parent_fd(str(path)) as (fd, name):
        os.fsync(fd)
        if snapshot(fd, name, str(path)) != absent:
            raise RuntimeError('Restore did not return to absence')
    return {'status': 'passed', 'directory': str(directory),
            'checks': list(CHECKS),
            'boot_recovery_qualified': False}


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('disposable_mount', type=Path)
    args = cli.parse_args()
    print(json.dumps(run(args.disposable_mount)))
