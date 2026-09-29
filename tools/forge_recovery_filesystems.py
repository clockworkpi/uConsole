"""Offline checks of private host copies; no mounts, repairs or target writes."""
import base64
import contextlib
import hashlib
import os
from pathlib import Path
import selectors
import shutil
import stat
import struct
import subprocess
import sys
import time

from forge_recovery_archive import RESERVE_BYTES, fingerprint
from forge_recovery_layout import root_extent
from forge_target_journal import private_directory, read_record, write_record


def descriptor_directory():
    """Use inherited descriptors, never substitute a reopenable image pathname."""
    directory = '/proc/self/fd' if sys.platform.startswith('linux') else '/dev/fd' if sys.platform == 'darwin' else None
    if directory is None or not Path(directory).is_dir():
        raise RuntimeError('Offline filesystem checks require Linux or macOS descriptor paths')
    return directory


def filesystem_tool(name):
    """Resolve fixed checker/fixture tools, including keg-only Homebrew ext4 tools."""
    if name not in ('e2fsck', 'fsck.fat', 'mkfs.ext4', 'mke2fs', 'mkfs.fat'):
        raise ValueError('Unsupported filesystem tool')
    found = shutil.which(name)
    if found or sys.platform != 'darwin':
        return found
    for prefix in ('/opt/homebrew', '/usr/local'):
        for folder in ('opt/e2fsprogs/sbin', 'sbin'):
            candidate = Path(prefix)/folder/name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def partition_ranges(header, size, plan):
    """Bind the copied DOS header to the storage layout recorded during backup."""
    if len(header) != 512 or type(size) is not int or size % 512:
        raise ValueError('Invalid whole-card image geometry')
    parts = []
    for number in (1, 2):
        start, sectors = struct.unpack_from('<II', header, 446 + (number-1)*16 + 8)
        parts.append(dict(number=number, start=start, sectors=sectors))
    observed = dict(device=plan['device'], cid=plan['cid'], sector_size=512,
                    sectors=size//512, bytes=size, partitions=parts,
                    mbr=base64.b64encode(header).decode())
    extent = root_extent(observed, plan['cid'], plan['disk_id'], device=plan['device'])
    if plan['backup_kind'] != 'whole-card-bytes' or extent != plan['extent']:
        raise ValueError('Copied layout differs from verified backup layout')
    return [dict(name=name, offset=part['start']*512, bytes=part['sectors']*512)
            for name, part in zip(('boot', 'root'), parts)]


def run_check(argv, fd, *, timeout=600, maximum=2*1024*1024, heartbeat=None):
    """Bound diagnostics/duration, maintaining an optional caller-owned lease.

    Callers pass a read-only image FD. Heartbeat errors terminate the owned
    checker, including after stdout EOF, and never extend its deadline.
    """
    if heartbeat is not None and not callable(heartbeat):
        raise ValueError('Expected heartbeat callable')
    if heartbeat is not None:
        heartbeat()
    output = bytearray()
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(argv + [descriptor_directory() + '/' + str(fd)], pass_fds=(fd,),
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, env=dict(os.environ, LC_ALL='C'))
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if heartbeat is not None:
                    heartbeat()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Filesystem checker deadline exceeded')
                for key, _ in selector.select(min(remaining, 1)):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                    output.extend(data)
                    if len(output) > maximum:
                        raise ValueError('Filesystem checker diagnostics exceed bound')
        # A checker can close stdout and remain alive. Keep both the deadline
        # and optional recovery lease live until the owned child really exits.
        while True:
            if heartbeat is not None:
                heartbeat()
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Filesystem checker deadline exceeded')
            try:
                result = process.wait(timeout=min(1, remaining))
                break
            except subprocess.TimeoutExpired:
                pass
        return dict(argv=argv, returncode=result, output=output.decode('utf-8', errors='replace'))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def inspect_filesystems(materialized_directory, backup_directory, destination, *, heartbeat=None):
    """Split a verified whole-card host image, then run FAT/ext4 checkers with -n.

    A nonzero checker exit is evidence of a failed check, never permission to
    repair the archive or to write a target. All copies and logs are retained.
    """
    if heartbeat is not None and not callable(heartbeat):
        raise ValueError('Expected heartbeat callable')
    descriptor_directory()
    commands = {name: filesystem_tool(tool) for name, tool in (('boot', 'fsck.fat'), ('root', 'e2fsck'))}
    if not all(commands.values()):
        raise RuntimeError('Offline filesystem checks require fsck.fat and e2fsck; run make deps')
    with contextlib.ExitStack() as stack:
        image_dir = private_directory(materialized_directory)
        stack.callback(os.close, image_dir)
        backup_dir = private_directory(backup_directory)
        stack.callback(os.close, backup_dir)
        accepted = read_record(image_dir, 'acceptance.json')
        backup = read_record(backup_dir, 'acceptance.json')
        plan = read_record(backup_dir, 'plan.json')
        expected = accepted.get('image')
        if (accepted.get('status') != 'verified-host-image' or
                accepted.get('backup_kind') != 'whole-card-bytes' or
                backup.get('status') != 'verified-card-byte-backup' or
                not isinstance(expected, dict) or expected != backup.get('card')):
            raise ValueError('Matching completed whole-card receipts are required')
        fd = os.open('image.img', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=image_dir)
        stack.callback(os.close, fd)
        initial = os.fstat(fd)
        if (not stat.S_ISREG(initial.st_mode) or initial.st_uid != os.getuid() or
                initial.st_mode & 0o077 or initial.st_nlink != 1 or initial.st_size != expected['bytes']):
            raise PermissionError('Expected private, owned, single-link regular image')
        ranges = partition_ranges(os.pread(fd, 512, 0), initial.st_size, plan)
        destination = Path(destination).absolute()
        if shutil.disk_usage(destination.parent).free < sum(p['bytes'] for p in ranges) + RESERVE_BYTES:
            raise OSError('Insufficient host space for private partition copies')
        destination.mkdir(mode=0o700)
        parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        out_dir = private_directory(destination)
        stack.callback(os.close, out_dir)
        record = dict(status='incomplete', filesystem_consistency_qualified=False,
                      restore_authorized=False, target_written=False, repair_performed=False)
        try:
            write_record(out_dir, 'plan.json', dict(image=expected, partitions=ranges,
                         source=str(Path(materialized_directory).absolute()), commands=commands))
            with contextlib.ExitStack() as copies:
                writers = []
                for part in ranges:
                    out = os.open(part['name']+'.img', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  0o600, dir_fd=out_dir)
                    writer = copies.enter_context(os.fdopen(out, 'wb'))
                    writers.append(writer)
                total, digest = 0, hashlib.sha256()
                try:
                    while data := os.read(fd, min(1048576, initial.st_size-total+1)):
                        if heartbeat is not None:
                            heartbeat()
                        if total+len(data) > initial.st_size:
                            raise ValueError('Host image grew during copying')
                        if shutil.disk_usage(destination).free < len(data) + RESERVE_BYTES:
                            raise OSError('Partition copies would exhaust host reserve')
                        digest.update(data)
                        for part, writer in zip(ranges, writers):
                            start = max(total, part['offset'])
                            end = min(total+len(data), part['offset']+part['bytes'])
                            if start < end:
                                writer.write(data[start-total:end-total])
                        total += len(data)
                finally:
                    for writer in writers:
                        writer.flush()
                        os.fsync(writer.fileno())
            if (fingerprint(initial) != fingerprint(os.fstat(fd)) or total != expected['bytes'] or
                    digest.hexdigest() != expected['sha256']):
                raise ValueError('Host image changed or differs from verified backup hash')
            checks = {}
            for part in ranges:
                name = part['name']
                check_fd = os.open(name+'.img', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=out_dir)
                try:
                    if os.fstat(check_fd).st_size != part['bytes']:
                        raise ValueError('Partition copy length differs')
                    flags = ['-n', '-v'] if name == 'boot' else ['-f', '-n']
                    checks[name] = run_check([commands[name]] + flags, check_fd, heartbeat=heartbeat)
                    write_record(out_dir, name+'-check.json', checks[name])
                finally:
                    os.close(check_fd)
            qualified = all(result['returncode'] == 0 for result in checks.values())
            if heartbeat is not None:
                heartbeat()
            record.update(status='checked', filesystem_consistency_qualified=qualified,
                          check_returncodes={name: result['returncode'] for name, result in checks.items()},
                          image=expected)
        except BaseException as exc:
            record['error'] = type(exc).__name__ + ': ' + str(exc)
            raise
        finally:
            write_record(out_dir, 'acceptance.json', record)
        return record
