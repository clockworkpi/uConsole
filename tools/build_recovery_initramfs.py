"""Build a private, target-native RAM recovery image; never install or boot it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sysconfig

from forge_recovery_runtime import INIT, DHCP, SSHD_CONFIG, NETWORK_OBSERVER
from forge_recovery_watchdog import SOURCE as WATCHDOG_SOURCE

CREDENTIALS = ('wpa.conf', 'authorized_keys', 'ssh_host_ed25519_key')
BOOT_FILESYSTEM_MODULES = ('vfat', 'nls_cp437', 'nls_ascii')
LEASE_MODULES = ('forge_ram_identity', 'forge_recovery_lease', 'forge_recovery_lease_socket',
                 'forge_recovery_deadline', 'forge_recovery_lease_timer',
                 'forge_recovery_lease_watchdog', 'forge_recovery_lease_launcher')


def lease_resources(directory):
    source = Path(__file__).resolve().parent
    for name in LEASE_MODULES:
        (directory / (name + '.py')).write_bytes((source / (name + '.py')).read_bytes())
    # -I ignores PYTHONPATH; use only the explicitly installed private code path.
    (directory / 'lease-launch.py').write_text(
        'import sys\nsys.path.insert(0, "/etc/forge")\n'
        'from forge_recovery_lease_launcher import main\nmain()\n')


def python_runtime_hook(stdlib):
    """Bundle standard-library code only, with native extension dependencies.

    Never copy site customizations or follow library symlinks outside stdlib.
    Build on the target with its distribution Python, not a virtualenv.
    """
    stdlib = Path(stdlib).resolve(strict=True)
    commands = ['copy_exec /usr/bin/python3']
    excluded = {'test', 'tests', '__pycache__', 'site-packages', 'dist-packages'}
    selected = []
    for candidate in sorted(stdlib.rglob('*')):
        relative = candidate.relative_to(stdlib)
        if (set(relative.parts) & excluded or candidate.name in ('sitecustomize.py', 'usercustomize.py')
                or not (candidate.suffix == '.py' or
                        (relative.parts[0] == 'lib-dynload' and candidate.suffix == '.so'))):
            continue
        source = candidate.resolve(strict=True)
        if not source.is_relative_to(stdlib) or not source.is_file():
            raise ValueError('Python runtime dependency escapes standard library')
        selected.append((candidate, source))
    if not selected or len(selected) > 10000 or sum(source.stat().st_size for _, source in selected) > 128*1024*1024:
        raise ValueError('Python standard library is empty or exceeds recovery bounds')
    for candidate, source in selected:
        copier = 'copy_exec' if candidate.suffix == '.so' else 'copy_file config'
        commands.append(copier + ' ' + shlex.quote(str(source)) + ' ' + shlex.quote(str(candidate)))
    return '\n'.join(commands) + '\n'


def read_credentials(directory):
    directory = Path(directory)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise ValueError('Credentials must be in a private real directory')
    result = {}
    for name in CREDENTIALS:
        fd = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_mode & 0o077 or not 0 < info.st_size <= 65536):
                raise ValueError('Credential must be private, bounded and singly linked: ' + name)
            data = stream.read(65537)
            after = os.fstat(stream.fileno())
            if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != info.st_size:
                raise ValueError('Credential changed during capture')
            result[name] = data
    return result


def hook(resources, python_commands=''):
    return '''#!/bin/sh
set -eu
case "${1:-}" in prereqs) exit 0;; esac
# shellcheck disable=SC1091
. /usr/share/initramfs-tools/hook-functions
copy_exec /usr/sbin/sshd
for helper in /usr/lib/openssh/sshd-session /usr/lib/openssh/sshd-auth; do
    test ! -f "$helper" || copy_exec "$helper"
done
copy_exec /usr/sbin/wpa_supplicant
''' + python_commands + '''
copy_exec ''' + shlex.quote(str(resources / 'forge-watchdog')) + ''' /usr/sbin/forge-watchdog
manual_add_modules brcmfmac
# Firmware-vendor plugins are requested dynamically by brcmfmac, not listed
# as its ordinary module dependencies. Older kernels fold them into brcmfmac.
for vendor in brcmfmac_wcc brcmfmac_cyw brcmfmac_bca; do
    if modinfo -k "$version" "$vendor" >/dev/null 2>&1; then
        manual_add_modules "$vendor"
    fi
done
manual_add_modules cdc_ether
''' + ''.join('manual_add_modules ' + name + '\n' for name in BOOT_FILESYSTEM_MODULES) + '''
for firmware in /lib/firmware/brcm/brcmfmac43455-sdio.*; do
    test -e "$firmware" || continue
    copy_file firmware "$firmware" || test "$?" = 1
done
mkdir -p "$DESTDIR/etc/forge" "$DESTDIR/root" "$DESTDIR/run/sshd"
chmod 0700 "$DESTDIR/etc/forge" "$DESTDIR/root"
cp -p ''' + shlex.quote(str(resources)) + '''/init "$DESTDIR/init"
cp -p ''' + shlex.quote(str(resources)) + '''/forge/* "$DESTDIR/etc/forge/"
# Use an unguessable, non-locked account hash; password authentication remains
# disabled. A locked shadow entry would reject public-key login with UsePAM=no.
printf '%s\\n' 'root:x:0:0:root:/root:/bin/sh' 'sshd:x:100:65534::/run/sshd:/usr/sbin/nologin' >"$DESTDIR/etc/passwd"
printf '%s\\n' 'root:x:0:' 'nogroup:x:65534:' >"$DESTDIR/etc/group"
cp -p ''' + shlex.quote(str(resources)) + '''/shadow "$DESTDIR/etc/shadow"
'''


def build(output, credentials, kernel):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}', kernel):
        raise ValueError('Invalid kernel release')
    frozen = read_credentials(credentials)
    output = Path(output).absolute()
    output.mkdir(mode=0o700)  # Exclusive, never overwrite an earlier image.
    previous = os.umask(0o077)
    try:
        resources = output / 'resources'
        forge = resources / 'forge'
        forge.mkdir(parents=True, mode=0o700)
        lease_resources(forge)
        keeper_source = resources / 'watchdog.c'
        keeper_source.write_text(WATCHDOG_SOURCE)
        subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', str(keeper_source),
                        '-o', str(resources / 'forge-watchdog')], check=True, timeout=60)
        for name, data in frozen.items():
            (forge / name).write_bytes(data)
        (resources / 'init').write_text(INIT)
        (resources / 'init').chmod(0o755)
        (forge / 'dhcp.sh').write_text(DHCP)
        (forge / 'dhcp.sh').chmod(0o700)
        (forge / 'network-observer.sh').write_text(NETWORK_OBSERVER)
        (forge / 'network-observer.sh').chmod(0o700)
        (forge / 'sshd_config').write_text(SSHD_CONFIG)
        # No real account password or host shadow database enters this image.
        import secrets
        hashed = subprocess.run(['openssl', 'passwd', '-6', '-stdin'],
                                input=secrets.token_hex(32) + '\n', text=True,
                                capture_output=True, check=True, timeout=10).stdout.strip()
        if not hashed or hashed.startswith('*'):
            raise RuntimeError('Cannot generate recovery account hash')
        (resources / 'shadow').write_text('root:' + hashed + ':20000:0:99999:7:::\nsshd:!:20000:0:99999:7:::\n')
        conf = output / 'config'
        for name in ('hooks', 'scripts', 'conf.d'):
            (conf / name).mkdir(parents=True, mode=0o700)
        (conf / 'initramfs.conf').write_text('MODULES=most\nBUSYBOX=y\nCOMPRESS=gzip\n')
        (conf / 'modules').write_text('brcmfmac\n')
        script = conf / 'hooks/zz-forge-recovery'
        stdlib = Path(sysconfig.get_path('stdlib'))
        if not re.fullmatch(r'/usr/lib/python3\.[0-9]+', str(stdlib)):
            raise ValueError('Recovery build requires the native distribution Python under /usr/bin')
        script.write_text(hook(resources, python_runtime_hook(stdlib)))
        script.chmod(0o700)
        image = output / 'recovery.img'
        with (output / 'build.log').open('xb') as log:
            subprocess.run(['/usr/sbin/mkinitramfs', '-d', str(conf), '-c', 'gzip',
                            '-o', str(image), kernel], stdout=log, stderr=subprocess.STDOUT,
                           timeout=600, check=True)
        image.chmod(0o600)
        result = {'schema': 1, 'kernel': kernel, 'image': str(image),
                  'sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
                  'size': image.stat().st_size, 'contains_private_credentials': True,
                  'boot_qualified': False, 'deployment_performed': False}
        result['watchdog_keeper'] = {'opt_in': True, 'maximum_lifetime_seconds': 300,
                                    'hardware_qualified': False}
        result['python_runtime'] = {'stdlib': str(stdlib), 'site_customizations': False,
                                    'third_party_packages': False}
        result['recovery_lease'] = {'opt_in': True, 'purpose': 'offline-backup',
                                    'qualified': False, 'maximum_lifetime_seconds': 86400}
        result['boot_filesystem'] = {'modules': list(BOOT_FILESYSTEM_MODULES),
                                     'automounted': False, 'write_authorized': False,
                                     'qualified': False}
        (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
        return result
    finally:
        os.umask(previous)


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output', required=True, type=Path)
    cli.add_argument('--credentials', required=True, type=Path)
    cli.add_argument('--kernel', required=True)
    args = cli.parse_args()
    print(json.dumps(build(args.output, args.credentials, args.kernel)))
