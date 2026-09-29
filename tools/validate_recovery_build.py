"""Build with disposable, non-network credentials; never deploy the result."""
import argparse
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess

from build_recovery_initramfs import build, LEASE_MODULES, BOOT_FILESYSTEM_MODULES
from forge_recovery_runtime import INIT, NETWORK_OBSERVER


def check_modules(root, kernel, vendor_modules=()):
    """Resolve packaged module dependencies without loading host modules."""
    result = {}
    for name in ('bcm2835_wdt', 'brcmfmac', 'cdc_ether', *BOOT_FILESYSTEM_MODULES, *vendor_modules):
        checked = subprocess.run(['chroot', str(root), '/sbin/modprobe',
                                  '--show-depends', '--set-version', kernel, name],
                                 capture_output=True, text=True, check=True, timeout=10)
        lines = checked.stdout.splitlines()
        if not lines or any(not line.startswith(('builtin ', 'insmod ')) for line in lines):
            raise RuntimeError('Unrecognized packaged module resolution: ' + name)
        for line in lines:
            if line.startswith('insmod '):
                path = line.split()[1]
                if not path.startswith('/lib/modules/' + kernel + '/'):
                    raise RuntimeError('Module resolved outside selected kernel: ' + name)
                candidate = (root / path.lstrip('/')).resolve(strict=True)
                if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
                    raise RuntimeError('Missing or escaping packaged module: ' + name)
        result[name] = lines
    return result


def native_vendor_modules(kernel):
    result = []
    for name in ('brcmfmac_wcc', 'brcmfmac_cyw', 'brcmfmac_bca'):
        checked = subprocess.run(['/sbin/modinfo', '-k', kernel, name],
                                 capture_output=True, text=True, timeout=10)
        if checked.returncode == 0:
            result.append(name)
        elif checked.returncode != 1:
            raise RuntimeError('Cannot inspect native firmware-vendor modules')
    return result


def run(directory):
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)
    previous = os.umask(0o077)
    try:
        credentials = directory / 'test-credentials'
        credentials.mkdir(mode=0o700)
        key = credentials / 'ssh_host_ed25519_key'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
        (credentials / 'authorized_keys').write_bytes(key.with_suffix('.pub').read_bytes())
        (credentials / 'wpa.conf').write_text('network={\n ssid="forge-offline-build-test"\n psk=' + secrets.token_hex(32) + '\n}\n')
        result = build(directory / 'build', credentials, os.uname().release)
        extracted = directory / 'extracted'
        subprocess.run(['unmkinitramfs', result['image'], str(extracted)], check=True, timeout=120)
        root = extracted / 'main' if (extracted / 'main/init').exists() else extracted
        if (root / 'init').read_text() != INIT:
            raise RuntimeError('Packaged init is not the RAM-only recovery runtime')
        if (root / 'etc/forge/network-observer.sh').read_text() != NETWORK_OBSERVER:
            raise RuntimeError('Packaged network observer differs from selected runtime')
        source = Path(__file__).resolve().parent
        for name in LEASE_MODULES:
            if (root / 'etc/forge' / (name + '.py')).read_bytes() != (source / (name + '.py')).read_bytes():
                raise RuntimeError('Packaged lease module differs: ' + name)
        for relative in ('usr/sbin/sshd', 'usr/sbin/wpa_supplicant', 'usr/sbin/forge-watchdog',
                         'usr/lib/systemd/systemd-udevd', 'etc/forge/authorized_keys', 'usr/bin/python3'):
            if not (root / relative).is_file():
                raise RuntimeError('Missing runtime dependency: ' + relative)
        if not list(root.glob('**/brcmfmac.ko*')):
            raise RuntimeError('Missing Wi-Fi module')
        result['module_dependencies'] = check_modules(root, result['kernel'],
                                                      native_vendor_modules(result['kernel']))
        for helper in ('sshd-session', 'sshd-auth'):
            relative = 'usr/lib/openssh/' + helper
            if Path('/' + relative).exists() and not (root / relative).is_file():
                raise RuntimeError('Missing SSH helper: ' + helper)
        # The boot runtime mounts devtmpfs. Offline validation needs only null;
        # bind that single device because /tmp may be mounted nodev.
        root.chmod(0o755)
        (root / 'dev').mkdir(exist_ok=True)
        (root / 'dev').chmod(0o755)
        os.mknod(root / 'dev/null', stat.S_IFCHR | 0o600, os.makedev(1, 3))
        (root / 'dev/null').chmod(0o666)
        subprocess.run(['mount', '--bind', '/dev/null', str(root / 'dev/null')], check=True)
        try:
            subprocess.run(['chroot', str(root), '/usr/sbin/sshd', '-t', '-f',
                            '/etc/forge/sshd_config'], check=True, timeout=10)
            subprocess.run(['chroot', str(root), '/usr/bin/python3', '-I', '-S', '-c',
                            'import base64,ctypes,fcntl,hashlib,json,pathlib,shlex,ssl,subprocess,sysconfig; '
                            'assert hashlib.sha256(b"forge").hexdigest(); ctypes.CDLL(None)'],
                           check=True, timeout=10)
            subprocess.run(['chroot', str(root), '/usr/bin/python3', '-I', '-S', '-c',
                            'import sys; sys.path.insert(0,"/etc/forge"); '
                            'import forge_recovery_lease_launcher,forge_recovery_lease_watchdog'],
                           check=True, timeout=10)
        finally:
            subprocess.run(['umount', str(root / 'dev/null')], check=True)
        result.update(status='build-and-sshd-preflight-passed', credentials='disposable-test-only')
        (directory / 'acceptance.json').write_text(json.dumps(result, indent=2) + '\n')
        return result
    finally:
        os.umask(previous)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    print(json.dumps(run(parser.parse_args().output)))
