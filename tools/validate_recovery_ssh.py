"""Exercise extracted recovery SSH in private mount/network namespaces only."""
import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time


def client(key, known_hosts, port=2222):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Invalid SSH port')
    return ['ssh', '-F', '/dev/null', '-p', str(port), '-i', str(key),
            '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'IdentityAgent=none', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile=' + str(known_hosts),
            '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=10',
            'root@127.0.0.1', 'printf "FORGE_SSH_OK\\n"; id -u']


def inside(root, output):
    # Refuse accidental direct invocation in the host namespaces.
    for namespace in ('net', 'mnt'):
        if os.readlink('/proc/self/ns/' + namespace) == os.readlink('/proc/1/ns/' + namespace):
            raise RuntimeError('Dedicated network and mount namespaces required')
    root = Path(root).resolve(strict=True)
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    os.umask(0o077)
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    subprocess.run(['mount', '--bind', '/dev/null', str(root / 'dev/null')], check=True)
    try:
        key = root / 'etc/forge/ssh_host_ed25519_key'
        public = subprocess.run(['ssh-keygen', '-y', '-f', str(key)],
                                text=True, capture_output=True, check=True).stdout.strip()
        known = output / 'known_hosts'
        known.write_text('[127.0.0.1]:2222 ' + public + '\n')
        wrong = output / 'wrong-key'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(wrong)], check=True)
        with (output / 'sshd.log').open('xb') as log:
            process = subprocess.Popen(['chroot', str(root), '/usr/sbin/sshd', '-D', '-e',
                                        '-f', '/etc/forge/sshd_config'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                       start_new_session=True)
            try:
                deadline = time.monotonic() + 15
                captured = b''
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stderr, selectors.EVENT_READ)
                    while b'Server listening on 0.0.0.0 port 2222.' not in captured:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise TimeoutError('Recovery SSH listener did not become ready')
                        data = os.read(process.stderr.fileno(), 8192)
                        if not data:
                            raise RuntimeError('Recovery SSH exited before readiness')
                        log.write(data)
                        log.flush()
                        captured += data
                denied = subprocess.run(client(wrong, known), capture_output=True, text=True, timeout=15)
                (output / 'denied.json').write_text(json.dumps({'returncode': denied.returncode,
                                                               'stderr': denied.stderr}) + '\n')
                if denied.returncode != 255 or 'Permission denied' not in denied.stderr:
                    raise RuntimeError('Unauthorized key was not rejected by authentication')
                mismatched = output / 'mismatched_known_hosts'
                mismatched.write_text('[127.0.0.1]:2222 ' + wrong.with_suffix('.pub').read_text())
                untrusted = subprocess.run(client(key, mismatched), capture_output=True, text=True, timeout=15)
                (output / 'untrusted.json').write_text(json.dumps({'returncode': untrusted.returncode,
                                                                  'stderr': untrusted.stderr}) + '\n')
                if untrusted.returncode != 255 or 'Host key verification failed' not in untrusted.stderr:
                    raise RuntimeError('Mismatched host identity was not rejected')
                accepted = subprocess.run(client(key, known), capture_output=True, text=True, timeout=15)
                (output / 'accepted.json').write_text(json.dumps({'returncode': accepted.returncode,
                                                                 'stdout': accepted.stdout,
                                                                 'stderr': accepted.stderr}) + '\n')
                if accepted.returncode != 0 or accepted.stdout != 'FORGE_SSH_OK\n0\n':
                    raise RuntimeError('Authenticated recovery shell failed')
            finally:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    _, tail = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    _, tail = process.communicate(timeout=5)
                log.write(tail)
        result = {'status': 'passed', 'scope': 'isolated extracted-userspace SSH',
                  'unauthorized_key_rejected': True, 'authorized_root_shell': True,
                  'strict_host_key_checking': True, 'mismatched_host_key_rejected': True,
                  'physical_network_qualified': False}
        (output / 'acceptance.json').write_text(json.dumps(result, indent=2) + '\n')
        return result
    finally:
        subprocess.run(['umount', str(root / 'dev/null')], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--inside', action='store_true')
    args = parser.parse_args()
    if args.inside:
        print(json.dumps(inside(args.root, args.output)))
    else:
        subprocess.run(['unshare', '--net', '--mount', '--propagation', 'private',
                        '/usr/bin/python3', str(Path(__file__).resolve()), '--inside',
                        '--root', str(args.root.resolve()), '--output', str(args.output.absolute())],
                       check=True, timeout=75)
