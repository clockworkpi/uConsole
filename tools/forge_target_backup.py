"""Read-only SSH preimages for bounded live-target file deployments.

Not a whole-system snapshot or authority to deploy: package/service transactions
and boot recovery require additional state. No target files are written here.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess

MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024

# Kept self-contained for execution using the target's existing Python/SSH.
CAPTURE = r'''
import base64, hashlib, json, os, stat, sys
paths = json.load(sys.stdin)
records = []
total = 0
def signature(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid,
            s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
for path in paths:
    parts = path.split('/')[1:]
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        try:
            before = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            records.append({'path': path, 'kind': 'absent'})
            continue
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError('Only singly linked regular file preimages supported: ' + path)
        if before.st_size > 8 * 1024 * 1024:
            raise ValueError('File exceeds preimage limit: ' + path)
        source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK, dir_fd=fd)
        try:
            if signature(os.fstat(source)) != signature(before):
                raise ValueError('File changed before capture: ' + path)
            attrs = {key: base64.b64encode(os.getxattr(source, key)).decode()
                     for key in os.listxattr(source)}
            with os.fdopen(os.dup(source), 'rb') as stream:
                data = stream.read(8 * 1024 * 1024 + 1)
            after = os.fstat(source)
            attrs_after = {key: base64.b64encode(os.getxattr(source, key)).decode()
                           for key in os.listxattr(source)}
            if (signature(before) != signature(after) or attrs != attrs_after or
                    signature(after) != signature(os.stat(parts[-1], dir_fd=fd, follow_symlinks=False))):
                raise ValueError('File changed during capture: ' + path)
            if len(data) != before.st_size:
                raise ValueError('Incomplete file read: ' + path)
            total += len(data)
            if total > 32 * 1024 * 1024:
                raise ValueError('Preimages exceed total limit')
            records.append({'path': path, 'kind': 'file', 'size': len(data),
                'sha256': hashlib.sha256(data).hexdigest(),
                'data': base64.b64encode(data).decode(), 'mode': stat.S_IMODE(before.st_mode),
                'uid': before.st_uid, 'gid': before.st_gid,
                'atime_ns': before.st_atime_ns, 'mtime_ns': before.st_mtime_ns,
                'xattrs': attrs})
        finally:
            os.close(source)
    finally:
        os.close(fd)
with open('/etc/machine-id') as stream:
    identity = stream.read().strip()
print(json.dumps({'schema': 1, 'machine_id': identity, 'files': records,
                  'scope': 'per-file preimages, not an atomic system snapshot'}))
'''


def paths_checked(paths):
    if not isinstance(paths, list) or not 1 <= len(paths) <= 32:
        raise ValueError('Select 1..32 explicit target files')
    for path in paths:
        if (not isinstance(path, str) or not path.startswith('/') or
                str(PurePosixPath(path)) != path or path == '/' or
                any(part in ('', '.', '..') for part in path.split('/')[1:]) or
                any(ord(c) < 32 or ord(c) == 127 for c in path)):
            raise ValueError('Target paths must be canonical absolute file paths')
    if len(set(paths)) != len(paths):
        raise ValueError('Duplicate target file')
    return list(paths)


def verify(record, paths):
    paths = paths_checked(paths)
    if (type(record.get('schema')) is not int or record['schema'] != 1 or
            not re.fullmatch('[0-9a-f]{32}', record.get('machine_id', '')) or
            not isinstance(record.get('files'), list) or
            [item.get('path') for item in record['files']] != paths):
        raise ValueError('Invalid target backup identity or file set')
    total = 0
    for item in record['files']:
        if item.get('kind') == 'absent' and set(item) == {'path', 'kind'}:
            continue
        if item.get('kind') != 'file':
            raise ValueError('Unsupported preimage kind')
        data = base64.b64decode(item['data'], validate=True)
        total += len(data)
        if (type(item['size']) is not int or item['size'] != len(data) or
                len(data) > MAX_FILE or total > MAX_TOTAL or
                hashlib.sha256(data).hexdigest() != item['sha256']):
            raise ValueError('Preimage content verification failed')
        for field in ('mode', 'uid', 'gid', 'atime_ns', 'mtime_ns'):
            if type(item.get(field)) is not int or item[field] < 0:
                raise ValueError('Invalid file metadata')
        if item['mode'] > 0o7777 or not isinstance(item['xattrs'], dict):
            raise ValueError('Invalid mode or extended attributes')
        for key, value in item['xattrs'].items():
            if not isinstance(key, str) or not key or '\0' in key:
                raise ValueError('Invalid extended attribute name')
            base64.b64decode(value, validate=True)
    return record


def capture(host, paths, output):
    paths = paths_checked(paths)
    if not re.fullmatch(r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Use an SSH hostname or user@hostname, not SSH options')
    output = Path(output).absolute()
    # Reserve a private exclusive artifact before contacting the target. An
    # interrupted capture leaves an invalid/empty file, never a valid backup.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                                 host, 'sudo -n python3 -c ' + shlex.quote(CAPTURE)],
                                input=json.dumps(paths), text=True, capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError('Target preimage capture failed: ' + result.stderr[-2000:])
        record = verify(json.loads(result.stdout), paths)
        record['ssh_host'] = host
        json.dump(record, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    verify(json.loads(output.read_text()), paths)
    return {'backup': str(output), 'files': len(paths), 'status': 'verified-preimages',
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--host', required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('paths', nargs='+')
    args = cli.parse_args()
    print(json.dumps(capture(args.host, args.paths, args.output)))


if __name__ == '__main__':
    main()
