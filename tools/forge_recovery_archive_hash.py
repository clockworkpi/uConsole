"""Derive independent range guards from retained card bytes, without extraction.

This is a host-only observation. Byte equivalence is not filesystem health,
bootability, permission to restore, or permission to release recovery hold.
"""
import gzip
import hashlib
import os
from pathlib import Path
import re
import stat

from forge_recovery_archive import fingerprint
from forge_target_journal import private_directory, read_record, write_record


def capture(backup_directory, destination, *, heartbeat=None):
    if heartbeat is not None and not callable(heartbeat):
        raise ValueError('Expected callable backup lease heartbeat')
    source = private_directory(backup_directory)
    archive = output = None
    try:
        plan = read_record(source, 'plan.json')
        accepted = read_record(source, 'acceptance.json')
        extent = plan.get('extent', {})
        length, start, size = (extent.get(key) for key in
                               ('disk_bytes', 'offset_bytes', 'length_bytes'))
        card = accepted.get('card', {})
        compressed = accepted.get('compressed_bytes')
        if (plan.get('backup_kind') != 'whole-card-bytes' or
                accepted.get('status') != 'verified-card-byte-backup' or
                any(type(value) is not int or value <= 0 or value % 512
                    for value in (length, start, size)) or start + size > length or
                plan.get('source') != dict(device=plan.get('device'), length_bytes=length) or
                not isinstance(plan.get('device'), str) or
                not re.fullmatch('/dev/mmcblk[0-9]{1,2}', plan['device']) or
                not isinstance(card, dict) or set(card) != {'bytes', 'sha256'} or
                type(card['bytes']) is not int or card['bytes'] != length or
                not isinstance(card['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', card['sha256']) or
                type(compressed) is not int or compressed <= 0):
            raise ValueError('Expected completed whole-card backup with aligned root extent')
        archive = os.open('card.img.gz', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source)
        initial = os.fstat(archive)
        if (not stat.S_ISREG(initial.st_mode) or initial.st_uid != os.getuid() or
                initial.st_mode & 0o077 or initial.st_nlink != 1 or initial.st_size != compressed):
            raise PermissionError('Archive must be private, owned, single-link and match receipt size')
        destination = Path(destination).absolute()
        destination.mkdir(mode=0o700)
        parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        output = private_directory(destination)
        result = dict(status='incomplete', target_written=False, restore_authorized=False,
                      normal_boot_release_authorized=False, filesystem_consistency_qualified=False)
        try:
            write_record(output, 'plan.json', dict(source=str(Path(backup_directory).absolute()),
                         backup_plan=plan, backup_acceptance=accepted))
            ranges = dict(prefix=(0, start), root=(start, start + size), suffix=(start + size, length))
            hashes = {key: hashlib.sha256() for key in ranges}
            whole, total = hashlib.sha256(), 0
            with os.fdopen(os.dup(archive), 'rb') as raw, gzip.GzipFile(fileobj=raw, mode='rb') as stream:
                while data := stream.read(min(1048576, length - total + 1)):
                    if heartbeat is not None:
                        heartbeat()
                    if total + len(data) > length:
                        raise ValueError('Archive expands beyond verified card length')
                    whole.update(data)
                    for key, (low, high) in ranges.items():
                        begin, end = max(low, total), min(high, total + len(data))
                        if begin < end:
                            hashes[key].update(data[begin - total:end - total])
                    total += len(data)
            if (fingerprint(initial) != fingerprint(os.fstat(archive)) or
                    fingerprint(initial) != fingerprint(os.stat('card.img.gz', dir_fd=source, follow_symlinks=False)) or
                    read_record(source, 'plan.json') != plan or read_record(source, 'acceptance.json') != accepted):
                raise ValueError('Backup source changed during hashing')
            if total != length or whole.hexdigest() != card['sha256']:
                raise ValueError('Archive length or checksum differs from verified backup')
            digests = {key: dict(offset=low, bytes=high-low, sha256=hashes[key].hexdigest())
                       for key, (low, high) in ranges.items()}
            digests['card'] = dict(bytes=total, sha256=whole.hexdigest())
            result.update(status='verified-backup-range-digests', digests=digests)
        except BaseException as exc:
            result['error'] = type(exc).__name__ + ': ' + str(exc)
            raise
        finally:
            write_record(output, 'acceptance.json', result)
        return result
    finally:
        if output is not None:
            os.close(output)
        if archive is not None:
            os.close(archive)
        os.close(source)
