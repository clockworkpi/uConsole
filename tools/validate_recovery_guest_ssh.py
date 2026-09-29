"""Read-only SSH evidence from the RAM-booted recovery emulator."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from validate_recovery_ssh import client


def check_output(text):
    prefix, rest = text.split('FORGE_MOUNTS\n', 1)
    mounts, cmdline = rest.split('FORGE_CMDLINE\n', 1)
    if prefix != '0\n':
        raise ValueError('Recovery user is not root')
    rows = [line.split() for line in mounts.splitlines()]
    if not any(len(row) >= 3 and row[1] == '/' and row[2] in ('rootfs', 'ramfs', 'tmpfs') for row in rows):
        raise ValueError('Recovery root is not a RAM filesystem')
    if any(row[0].startswith('/dev/') and row[0] != '/dev/pts' for row in rows):
        raise ValueError('Unexpected device filesystem mounted in recovery')
    tokens = cmdline.split()
    if tokens.count('uconsole.emulator=1') != 1 or tokens.count('uconsole.recovery=1') != 1:
        raise ValueError('Missing exact emulator/recovery markers')


def run(key, output, port):
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    previous = os.umask(0o077)
    try:
        public = subprocess.run(['ssh-keygen', '-y', '-f', str(key)], text=True,
                                capture_output=True, check=True).stdout.strip()
        known = output / 'known_hosts'
        known.write_text('[127.0.0.1]:' + str(port) + ' ' + public + '\n')
        command = client(key, known, port)
        command[-1] = 'id -u; printf "FORGE_MOUNTS\\n"; cat /proc/mounts; printf "FORGE_CMDLINE\\n"; cat /proc/cmdline'
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        (output / 'ssh.json').write_text(json.dumps({'returncode': result.returncode,
                                                    'stdout': result.stdout, 'stderr': result.stderr}) + '\n')
        if result.returncode:
            raise RuntimeError('Recovery guest SSH did not succeed')
        check_output(result.stdout)
        evidence = {'status': 'passed', 'scope': 'RAM-booted emulator USB Ethernet DHCP and SSH',
                    'ram_root_verified': True, 'physical_wifi_qualified': False}
        (output / 'acceptance.json').write_text(json.dumps(evidence, indent=2) + '\n')
        return evidence
    finally:
        os.umask(previous)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--port', required=True, type=int)
    args = parser.parse_args()
    print(json.dumps(run(args.key, args.output, args.port)))
