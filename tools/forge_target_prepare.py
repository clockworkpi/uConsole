"""Author backed-up file transactions for owner review; never deploy them."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from forge_host_tasks import unique_object
from forge_target_backup import MAX_FILE, MAX_TOTAL, capture, paths_checked, verify
from forge_target_journal import prepare, private_directory, write_record


def freeze_sources(files):
    if not isinstance(files, list) or not 1 <= len(files) <= 32:
        raise ValueError('Select 1..32 file mappings')
    frozen = []
    total = 0
    for item in files:
        if (not isinstance(item, dict) or set(item) - {'source', 'target', 'mode', 'sha256'} or
                not {'source', 'target'} <= set(item)):
            raise ValueError('File mapping needs source and target, with optional mode and sha256')
        paths_checked([item['target']])
        if item['target'].startswith('/boot/') or item['target'] in ('/boot', '/etc/fstab', '/etc/crypttab'):
            raise ValueError('Boot/storage changes require a separately qualified recovery workflow')
        if (not isinstance(item['source'], str) or not Path(item['source']).is_absolute() or
                '\0' in item['source']):
            raise ValueError('Source must be an absolute local file path')
        mode = item.get('mode')
        if mode is not None and (not isinstance(mode, str) or not re.fullmatch('0[0-7]{3}', mode)):
            raise ValueError('Mode must be a four-digit octal string without special bits, e.g. 0755')
        source = Path(item['source']).resolve()
        fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE:
            os.close(fd)
            raise ValueError('Source must be a bounded regular file')
        with os.fdopen(fd, 'rb') as stream:
            data = stream.read(MAX_FILE + 1)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != before.st_size:
                raise ValueError('Source changed during snapshot')
        total += len(data)
        if total > MAX_TOTAL:
            raise ValueError('Source contents exceed transaction limit')
        digest = hashlib.sha256(data).hexdigest()
        if 'sha256' in item and item['sha256'] != digest:
            raise ValueError('Source differs from requested SHA-256')
        frozen.append({'source': str(source), 'target': item['target'], 'mode': mode,
                       'data': base64.b64encode(data).decode(), 'size': len(data), 'sha256': digest})
    paths_checked([item['target'] for item in frozen])
    return frozen


def author(output, host, files):
    frozen = freeze_sources(files)  # Fail locally before contacting any target.
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    paths = [item['target'] for item in frozen]
    backup = capture(host, paths, output / 'before.json')
    before = verify(json.loads((output / 'before.json').read_text()), paths)
    after = dict(before, files=[])
    reviews = []
    timestamp = time.time_ns()
    for old, source in zip(before['files'], frozen):
        if old['kind'] == 'file':
            new = dict(old)
        else:
            new = {'path': source['target'], 'kind': 'file', 'uid': 0, 'gid': 0,
                   'mode': 0o644, 'atime_ns': timestamp, 'xattrs': {}}
        new.update(data=source['data'], size=source['size'], sha256=source['sha256'], mtime_ns=timestamp)
        if source['mode'] is not None:
            if 'system.posix_acl_access' in new['xattrs']:
                raise ValueError('Explicit mode change with an existing ACL requires separate review')
            new['mode'] = int(source['mode'], 8)
        if new['mode'] & 0o6000 or 'security.capability' in new['xattrs']:
            raise ValueError('Privileged executable replacement requires a separately reviewed plan')
        after['files'].append(new)
        reviews.append({'path': source['target'], 'source': source['source'],
                        'before': {key: old[key] for key in ('kind', 'sha256', 'size', 'mode', 'uid', 'gid') if key in old},
                        'after': {key: new[key] for key in ('kind', 'sha256', 'size', 'mode', 'uid', 'gid')},
                        'preserved_xattr_names': sorted(new['xattrs'])})
    prepared = prepare(output / 'transaction', host, before, after)
    review = {'status': 'prepared-for-review', 'host': host, 'machine_id': before['machine_id'],
              **{key: prepared[key] for key in ('journal', 'plan_sha256')}, 'backup': backup,
              'files': reviews, 'deployment_performed': False,
              'scope': 'file contents and metadata only; no service/package/boot recovery'}
    fd = private_directory(output)
    try:
        write_record(fd, 'review.json', review)
    finally:
        os.close(fd)
    return review


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--host', required=True)
    cli.add_argument('--spec', type=Path, required=True, help='JSON object with schema:1 and files:[source,target,mode?]')
    cli.add_argument('--output', type=Path, required=True, help='New private directory for backups and review')
    args = cli.parse_args()
    with args.spec.open('rb') as stream:
        payload = stream.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError('File mapping specification exceeds 1 MiB')
    spec = json.loads(payload, object_pairs_hook=unique_object)
    if (not isinstance(spec, dict) or set(spec) != {'schema', 'files'} or
            type(spec['schema']) is not int or spec['schema'] != 1):
        raise ValueError('Expected schema 1 and files array')
    print(json.dumps(author(args.output, args.host, spec['files']), indent=2))


if __name__ == '__main__':
    main()
