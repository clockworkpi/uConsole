"""Qualify fresh FAT mount privacy/restore with an isolated disposable image."""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import hashlib

from forge_recovery_image import publish, remove
from validate_recovery_interruption import qualify as qualify_interruption


READER = '''import sys
from pathlib import Path
try:
    data = Path(sys.argv[1]).read_bytes()
except PermissionError:
    sys.exit(13)
if data != b'public qualification fixture\\n':
    sys.exit(2)
'''


def unprivileged_read(path):
    result = subprocess.run(['setpriv', '--reuid=65534', '--regid=65534', '--clear-groups',
                             '/usr/bin/python3', '-c', READER, str(path)],
                            capture_output=True, timeout=10)
    if result.returncode not in (0, 13):
        raise RuntimeError('Read probe failed for a reason other than access denial')
    return 'readable' if result.returncode == 0 else 'denied'


def run():
    if os.readlink('/proc/self/ns/mnt') == os.readlink('/proc/1/ns/mnt'):
        raise RuntimeError('Private mount namespace required')
    directory = Path(tempfile.mkdtemp(prefix='forge-fat-privacy-'))
    # Permit traversal so the baseline read proves that denial is not caused
    # by the fixture parent. The raw image remains root-only throughout.
    directory.chmod(0o711)
    disk = directory / 'disk.img'
    fd = os.open(disk, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.truncate(32 * 1024 * 1024)
    subprocess.run(['mkfs.vfat', str(disk)], capture_output=True, check=True, timeout=20)
    mount = directory / 'mount'
    mount.mkdir(mode=0o755)
    fixture = mount / 'fixture.txt'
    mounted = False
    observations = []
    try:
        for mask, expected in (('0022', 'readable'), ('0077', 'denied'), ('0022', 'readable')):
            subprocess.run(['mount', '-o', 'loop,uid=0,gid=0,fmask=' + mask + ',dmask=' + mask,
                            str(disk), str(mount)], check=True, timeout=20)
            mounted = True
            if not observations:
                with fixture.open('xb') as stream:
                    stream.write(b'public qualification fixture\n')
                    stream.flush()
                    os.fsync(stream.fileno())
            expected_mode = 0o700 if mask == '0077' else 0o755
            for path in (mount, fixture):
                info = path.stat()
                if (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid) != (expected_mode, 0, 0):
                    raise RuntimeError('Effective FAT metadata differs from selected policy')
            state = unprivileged_read(fixture)
            if state != expected:
                raise RuntimeError('Effective non-root read access differs from selected policy')
            if mask == '0077':
                source = directory / 'source.img'
                payload = b'qualification-image\n' * 131072
                fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                digest = hashlib.sha256(payload).hexdigest()
                destination = mount / 'recovery.img'
                publish(str(source), str(destination), digest, len(payload), 'a' * 32)
                if unprivileged_read(destination) != 'denied':
                    raise RuntimeError('Published image is readable by an unprivileged user')
                remove(str(destination), digest, len(payload), 'a' * 32)
                if destination.exists():
                    raise RuntimeError('Published image was not restored to absence')
                interruption = qualify_interruption(directory / 'interruption-evidence', mount / 'interruption')
            observations.append({'mask': mask, 'mode': oct(expected_mode), 'non_root_read': state})
            subprocess.run(['umount', str(mount)], check=True, timeout=20)
            mounted = False
        result = {'status': 'passed', 'fixture': str(directory), 'observations': observations,
                  'private_image_publish_restore': True,
                  'private_image_interruption': interruption,
                  'physical_policy_changed': False, 'reboot_persistence_qualified': False}
        (directory / 'acceptance.json').write_text(json.dumps(result, indent=2) + '\n')
        return result
    finally:
        if mounted:
            subprocess.run(['umount', str(mount)], check=True, timeout=20)
        directory.chmod(0o700)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inside', action='store_true')
    args = parser.parse_args()
    if args.inside:
        print(json.dumps(run()))
    else:
        subprocess.run(['unshare', '--mount', '--propagation', 'private',
                        '/usr/bin/python3', str(Path(__file__).resolve()), '--inside'],
                       check=True, timeout=100)
