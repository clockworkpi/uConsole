"""Check a verified derivative root copy without repair or deployment authority."""
import hashlib
import os
from pathlib import Path
import shutil

from forge_recovery_archive import RESERVE_BYTES, fingerprint
from forge_recovery_derivative import load, stream, validate
from forge_recovery_commit_protocol import exact
from forge_recovery_restore_source import digest, sha
from forge_recovery_filesystems import descriptor_directory, filesystem_tool, run_check
from forge_target_journal import private_directory, read_record, write_record


def check_health(value, pin, manifest, manifest_pin):
    """Validate externally pinned health without interpreting it as authority."""
    validate(manifest, manifest_pin)
    fields = {'status', 'derivative_manifest_sha256', 'image', 'root',
              'root_filesystem_consistency_qualified', 'boot_filesystem_checked',
              'repair_performed', 'target_written', 'target_write_authorized',
              'normal_boot_release_authorized', 'root_check_returncode'}
    if (not sha(pin) or not isinstance(value, dict) or set(value) != fields or
            digest(value) != pin or value['status'] != 'checked-derivative-root' or
            value['derivative_manifest_sha256'] != manifest_pin or
            not exact(value['image'], manifest['card']) or not exact(value['root'], manifest['root']) or
            any(value[key] is not False for key in ('boot_filesystem_checked', 'repair_performed',
                'target_written', 'target_write_authorized', 'normal_boot_release_authorized')) or
            type(value['root_check_returncode']) is not int or not 0 <= value['root_check_returncode'] <= 255 or
            type(value['root_filesystem_consistency_qualified']) is not bool or
            value['root_filesystem_consistency_qualified'] != (value['root_check_returncode'] == 0)):
        raise ValueError('Derivative root health differs from pinned evidence')
    return value


def read_health(directory, pin, manifest, manifest_pin):
    """Load a retained check, including its verified-stream and checker records.

    The disposable root copy need not remain. This proves historical checks of
    exact source bytes, not current source identity, native bootability, or an
    owner's approval to deploy. Dispatch must independently reverify its source.
    """
    fd = private_directory(directory)
    try:
        value = check_health(read_record(fd, 'acceptance.json'), pin, manifest, manifest_pin)
        verified = read_record(fd, 'source-stream.json')
        checked = read_record(fd, 'root-check.json')
        plan = read_record(fd, 'plan.json')
    finally:
        os.close(fd)
    expected = dict(status='verified-derivative-stream', manifest_sha256=manifest_pin,
                    chunks=len(manifest['chunks']), root=manifest['root'],
                    target_restore_verified=False, normal_boot_release_authorized=False)
    if not exact(verified, expected):
        raise ValueError('Derivative health source-stream evidence differs')
    if (not isinstance(plan, dict) or set(plan) != {'derivative_manifest_sha256', 'source', 'image', 'root', 'command'} or
            plan['derivative_manifest_sha256'] != manifest_pin or
            not exact(plan['image'], manifest['card']) or not exact(plan['root'], manifest['root']) or
            not isinstance(plan['source'], str) or not Path(plan['source']).is_absolute() or
            not isinstance(plan['command'], list) or len(plan['command']) != 3 or
            not isinstance(plan['command'][0], str) or not Path(plan['command'][0]).is_absolute() or
            plan['command'][1:] != ['-f', '-n']):
        raise ValueError('Derivative health plan differs')
    if (not isinstance(checked, dict) or set(checked) != {'argv', 'returncode', 'output'} or
            not exact(checked['argv'], plan['command']) or
            not exact(checked['returncode'], value['root_check_returncode']) or
            not isinstance(checked['output'], str)):
        raise ValueError('Derivative health checker evidence differs')
    return value


def inspect_root(directory, pin, destination, *, heartbeat=None):
    """Retain a chunk-verified private root copy and read-only e2fsck evidence.

    Completion checks the derivative's full image and rollback lineage before
    invoking e2fsck. Only the root is checked: unchanged boot bytes are lineage
    evidence, not a filesystem health claim. Nonzero e2fsck exits are retained,
    never interpreted as permission to repair, deploy, or release recovery.
    """
    if heartbeat is not None and not callable(heartbeat):
        raise ValueError('Expected heartbeat callable')
    descriptor_directory()
    checker = filesystem_tool('e2fsck')
    if not checker:
        raise RuntimeError('Derivative root checks require e2fsck; run make deps')
    manifest, _ = load(directory, pin)
    destination = Path(destination).absolute()
    if shutil.disk_usage(destination.parent).free < manifest['root']['bytes'] + RESERVE_BYTES:
        raise OSError('Insufficient host space for derivative root copy')
    destination.mkdir(mode=0o700)
    parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    journal = private_directory(destination)
    record = dict(status='incomplete', derivative_manifest_sha256=pin,
                  image=manifest['card'], root=manifest['root'],
                  root_filesystem_consistency_qualified=False,
                  boot_filesystem_checked=False, repair_performed=False,
                  target_written=False, target_write_authorized=False,
                  normal_boot_release_authorized=False)
    try:
        write_record(journal, 'plan.json', dict(derivative_manifest_sha256=pin,
                     source=str(Path(directory).absolute()), image=manifest['card'],
                     root=manifest['root'], command=[checker, '-f', '-n']))
        fd = os.open('root.img', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=journal)
        with os.fdopen(fd, 'wb') as output:
            def consume(chunk, data):
                if output.tell() != chunk['offset'] or len(data) != chunk['bytes']:
                    raise ValueError('Noncontiguous derivative root copy')
                if shutil.disk_usage(destination).free < len(data) + RESERVE_BYTES:
                    raise OSError('Derivative copy would exhaust host reserve')
                if output.write(data) != len(data):
                    raise OSError('Incomplete derivative root copy')
            verified = stream(directory, pin, consume, heartbeat=heartbeat)
            output.flush()
            os.fsync(output.fileno())
            if output.tell() != manifest['root']['bytes']:
                raise ValueError('Derivative root copy length differs')
            copied = fingerprint(os.fstat(output.fileno()))
        write_record(journal, 'source-stream.json', verified)
        fd = os.open('root.img', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=journal)
        try:
            initial = fingerprint(os.fstat(fd))
            if initial != copied:
                raise ValueError('Derivative root copy identity changed')
            digest, total = hashlib.sha256(), 0
            while data := os.read(fd, 1048576):
                if heartbeat is not None:
                    heartbeat()
                total += len(data)
                if total > manifest['root']['bytes']:
                    raise ValueError('Derivative root copy grew')
                digest.update(data)
            if total != manifest['root']['bytes'] or digest.hexdigest() != manifest['root']['sha256']:
                raise ValueError('Derivative root copy checksum differs')
            os.lseek(fd, 0, os.SEEK_SET)
            checked = run_check([checker, '-f', '-n'], fd, heartbeat=heartbeat)
            if heartbeat is not None:
                heartbeat()
            if (initial != fingerprint(os.fstat(fd)) or
                    initial != fingerprint(os.stat('root.img', dir_fd=journal, follow_symlinks=False))):
                raise ValueError('Derivative root copy changed during checking')
        finally:
            os.close(fd)
        if type(checked['returncode']) is not int or not 0 <= checked['returncode'] <= 255:
            raise ValueError('Derivative filesystem checker did not exit normally')
        write_record(journal, 'root-check.json', checked)
        record.update(status='checked-derivative-root',
                      root_filesystem_consistency_qualified=checked['returncode'] == 0,
                      root_check_returncode=checked['returncode'])
    except BaseException as exc:
        record['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        try:
            write_record(journal, 'acceptance.json', record)
        finally:
            os.close(journal)
    return record
