"""Compile a reviewed root-only boot mount policy; never apply or remount it.

FAT remount may silently retain old masks. Deployment must verify a fresh mount
and non-root read denial before staging secrets; restore only after removing
private artifacts. This is access control, not encryption against card removal.
"""
import re
import base64
import copy
import hashlib
import time
import shlex
import subprocess
import json

from forge_target_backup import verify
from forge_target_journal import prepare


PARSER_CHECK = '''import pathlib, subprocess, sys, tempfile
data = sys.stdin.buffer.read(1048577)
if len(data) > 1048576:
    raise ValueError('Candidate fstab is too large')
with tempfile.TemporaryDirectory(prefix='forge-fstab-', dir='/run') as directory:
    path = pathlib.Path(directory) / 'fstab'
    path.write_bytes(data)
    path.chmod(0o600)
    result = subprocess.run(['findmnt', '--verify', '--tab-file', str(path)], timeout=20)
    sys.exit(result.returncode)
'''


def verify_candidate(host, data):
    if not isinstance(host, str) or not re.fullmatch(
            r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Invalid SSH target')
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', host,
                           'sudo -n python3 -c ' + shlex.quote(PARSER_CHECK)],
                          input=data, capture_output=True, timeout=35, check=True)


MOUNT_CHECK = '''import json, pathlib, stat, subprocess
paths = ['/boot/firmware', '/boot/firmware/config.txt']
metadata = {}
for name in paths:
    info = pathlib.Path(name).stat()
    metadata[name] = dict(mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
probe = "import sys\\ntry:\\n open('/boot/firmware/config.txt','rb').close()\\nexcept PermissionError:\\n sys.exit(13)"
result = subprocess.run(['setpriv', '--reuid=65534', '--regid=65534', '--clear-groups',
                         'python3', '-c', probe], capture_output=True, timeout=10)
mounts = json.loads(subprocess.check_output(['findmnt', '--json', '--mountpoint',
    '/boot/firmware', '--output', 'SOURCE,FSTYPE,OPTIONS'], text=True))
print(json.dumps(dict(metadata=metadata, probe_exit=result.returncode, mounts=mounts)))
'''


def observe_mount(host):
    if not isinstance(host, str) or not re.fullmatch(
            r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Invalid SSH target')
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', host,
                             'sudo -n python3 -c ' + shlex.quote(MOUNT_CHECK)],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=25, check=True)
    return json.loads(result.stdout)


def verify_mount(observed, source, private):
    mounts = observed['mounts']['filesystems']
    if len(mounts) != 1 or mounts[0]['source'] != source or mounts[0]['fstype'] != 'vfat':
        raise ValueError('Unexpected boot filesystem')
    options = mounts[0]['options'].split(',')
    mask = '0077' if private else '0022'
    for key in ('fmask', 'dmask'):
        if [s for s in options if s.startswith(key + '=')] != [key + '=' + mask]:
            raise ValueError('Effective mount mask differs')
    expected = dict(uid=0, gid=0, mode=0o700 if private else 0o755)
    if set(observed['metadata']) != {'/boot/firmware', '/boot/firmware/config.txt'} or any(
            item != expected for item in observed['metadata'].values()):
        raise ValueError('Effective ownership/permissions differ')
    if observed['probe_exit'] != (13 if private else 0):
        raise ValueError('Non-root access check differs')
    return {'status': 'verified-private' if private else 'verified-original', 'source': source}


def private_fstab(text, source):
    if not isinstance(text, str) or '\0' in text or not isinstance(source, str):
        raise ValueError('Invalid fstab input')
    if not re.fullmatch(r'(?:PARTUUID|UUID)=[A-Za-z0-9-]+|/dev/[A-Za-z0-9/_.-]+', source):
        raise ValueError('Unsupported boot source')
    lines = text.splitlines(keepends=True)
    selected = []
    for index, line in enumerate(lines):
        body = line.split('#', 1)[0]
        fields = body.split()
        if not fields:
            continue
        if '\\' in body:
            raise ValueError('Escaped fstab entries require separate review')
        if len(fields) != 6:
            raise ValueError('Expected six fields per active fstab entry')
        if fields[1] != '/boot/firmware':
            continue
        if fields[0] != source or fields[2] != 'vfat':
            raise ValueError('Boot mount source or filesystem differs')
        options = fields[3].split(',')
        keys = [option.split('=', 1)[0] for option in options]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate mount option')
        kept = []
        for option in options:
            key, _, value = option.partition('=')
            if key in ('uid', 'gid'):
                if value != '0':
                    raise ValueError('Boot mount is not root-owned')
            elif key in ('fmask', 'dmask', 'umask'):
                if not re.fullmatch('0?[0-7]{3}', value):
                    raise ValueError('Invalid FAT permission mask')
            elif option in ('defaults', 'rw', 'relatime', 'noatime', 'nosuid', 'nodev', 'noexec'):
                kept.append(option)
            else:
                raise ValueError('Unsupported boot mount option: ' + option)
        spans = list(re.finditer(r'\S+', body))
        replacement = ','.join(kept + ['uid=0', 'gid=0', 'fmask=0077', 'dmask=0077'])
        selected.append((index, spans[3].span(), replacement))
    if len(selected) != 1:
        raise ValueError('Expected exactly one boot mount entry')
    index, (start, end), replacement = selected[0]
    lines[index] = lines[index][:start] + replacement + lines[index][end:]
    return ''.join(lines)


def prepare_policy(directory, host, backup, source):
    """Freeze an fstab-only apply/restore plan, with no deployment authority."""
    verify(backup, ['/etc/fstab'])
    old = backup['files'][0]
    if old['kind'] != 'file' or old['uid'] != 0 or old['mode'] & 0o7022:
        raise ValueError('Expected root-owned fstab without special or untrusted write bits')
    data = private_fstab(base64.b64decode(old['data']).decode('utf-8'), source).encode('utf-8')
    after = copy.deepcopy(backup)
    after['files'][0].update(data=base64.b64encode(data).decode('ascii'), size=len(data),
                             sha256=hashlib.sha256(data).hexdigest(), mtime_ns=time.time_ns())
    return prepare(directory, host, backup, after)
