"""Materialize a completed backup on the host; never mount or write a device."""
import gzip
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat

from forge_target_journal import private_directory, read_record, write_record

RESERVE_BYTES = 128 * 1024 * 1024


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_uid, info.st_nlink)


def materialize(backup_directory, destination, *, heartbeat=None):
    """Create an exclusive private image, retaining incomplete output on failure.

    The acceptance record proves byte equivalence only. Filesystem consistency,
    bootability and permission to restore are separate gates. An optional
    caller-owned heartbeat can maintain a recovery lease; failure is terminal.
    """
    if heartbeat is not None and not callable(heartbeat):
        raise ValueError('Expected heartbeat callable')
    source_fd = private_directory(backup_directory)
    archive_fd = output_fd = None
    try:
        acceptance = read_record(source_fd, 'acceptance.json')
        plan = read_record(source_fd, 'plan.json')
        kinds = {'whole-card-bytes': ('card', 'verified-card-byte-backup'),
                 'root-partition-bytes': ('root', 'verified-root-backup')}
        kind = plan.get('backup_kind')
        if kind not in kinds:
            raise ValueError('Unsupported backup scope')
        name, status = kinds[kind]
        expected = acceptance.get(name, {})
        length, digest = expected.get('bytes'), expected.get('sha256')
        compressed = acceptance.get('compressed_bytes')
        if (acceptance.get('status') != status or type(length) is not int or length <= 0 or
                not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) or
                type(compressed) is not int or compressed <= 0 or
                plan.get('source', {}).get('length_bytes') != length):
            raise ValueError('A completed, matching byte-backup receipt is required')
        archive_fd = os.open(name + '.img.gz', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=source_fd)
        initial = os.fstat(archive_fd)
        if (not stat.S_ISREG(initial.st_mode) or initial.st_uid != os.getuid() or
                initial.st_mode & 0o077 or initial.st_nlink != 1 or initial.st_size != compressed):
            raise PermissionError('Archive must be private, owned, single-link and match receipt size')
        destination = Path(destination).absolute()
        if shutil.disk_usage(destination.parent).free < length + RESERVE_BYTES:
            raise OSError('Insufficient host space for image plus reserve')
        destination.mkdir(mode=0o700)
        parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        output_fd = private_directory(destination)
        record = dict(status='incomplete', backup_kind=kind, restore_authorized=False,
                      filesystem_consistency_qualified=False, target_written=False)
        try:
            write_record(output_fd, 'plan.json', dict(backup_kind=kind, expected=expected,
                         compressed_bytes=compressed, source=str(Path(backup_directory).absolute())))
            fd = os.open('image.img', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=output_fd)
            total, hashed = 0, hashlib.sha256()
            with os.fdopen(fd, 'wb') as output:
                try:
                    with os.fdopen(os.dup(archive_fd), 'rb') as raw:
                        with gzip.GzipFile(fileobj=raw, mode='rb') as archive:
                            while data := archive.read(min(1048576, length - total + 1)):
                                if heartbeat is not None:
                                    heartbeat()
                                if total + len(data) > length:
                                    raise ValueError('Archive expands beyond verified length')
                                if shutil.disk_usage(destination).free < len(data) + RESERVE_BYTES:
                                    raise OSError('Image would exhaust host reserve')
                                output.write(data)
                                hashed.update(data)
                                total += len(data)
                finally:
                    output.flush()
                    os.fsync(output.fileno())
            if fingerprint(initial) != fingerprint(os.fstat(archive_fd)):
                raise ValueError('Archive changed during extraction')
            if total != length or hashed.hexdigest() != digest:
                raise ValueError('Archive length or checksum differs from verified backup')
            if heartbeat is not None:
                heartbeat()
            record.update(status='verified-host-image', image=dict(bytes=total, sha256=hashed.hexdigest()))
        except BaseException as exc:
            record['error'] = type(exc).__name__ + ': ' + str(exc)
            raise
        finally:
            write_record(output_fd, 'acceptance.json', record)
        return record
    finally:
        if output_fd is not None:
            os.close(output_fd)
        if archive_fd is not None:
            os.close(archive_fd)
        os.close(source_fd)
