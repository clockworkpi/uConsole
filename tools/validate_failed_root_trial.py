"""Diskless check of intentional failed-root arguments, not firmware fallback."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time


def arguments(recipe):
    if recipe.get('kind') != 'alternate-firmware-failed-root-trial':
        raise ValueError('Expected a failed-root trial recipe')
    nonce = recipe.get('nonce')
    if not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce):
        raise ValueError('Invalid trial nonce')
    selected = [f for f in recipe['files'] if f['path'] == '/boot/firmware/forge-trial-cmdline.txt']
    if len(selected) != 1:
        raise ValueError('Expected exactly one trial command line')
    record = selected[0]
    raw = base64.b64decode(record['data'], validate=True)
    if len(raw) != record['size'] or hashlib.sha256(raw).hexdigest() != record['sha256']:
        raise ValueError('Trial command line failed integrity check')
    tokens = raw.decode().split()
    for prefix, value in (('root=', '/dev/ram0'), ('panic=', '0'),
                          ('init=', '/forge-intentional-failure'),
                          ('rdinit=', '/forge-intentional-failure'), ('uconsole.forge_trial=', nonce)):
        if [t for t in tokens if t.startswith(prefix)] != [prefix + value]:
            raise ValueError('Unexpected failed-root argument: ' + prefix)
    if tokens.count('noinitrd') != 1 or any(t.startswith(('rootwait', 'resume=', 'initrd=')) for t in tokens):
        raise ValueError('Trial must not wait for or resume a persistent root')
    # Only the console is adapted for QEMU; the failure-producing arguments are
    # retained. No disks, initrd, firmware or network are attached by this test.
    tokens = [t for t in tokens if not t.startswith(('console=', 'earlycon='))]
    return ' '.join(['earlycon=pl011,mmio32,0xfe201000', 'console=ttyAMA1,115200', *tokens])


def check_log(text, nonce):
    commands = re.findall(r'Kernel command line: ([^\r\n]+)', text)
    if len(commands) != 1 or ('uconsole.forge_trial=' + nonce) not in commands[0].split():
        raise ValueError('Console does not identify the intended trial')
    prefix = 'Kernel panic - not syncing: VFS: Unable to mount root fs on '
    if not any(prefix + suffix in text for suffix in (
            'unknown-block(1,0)', '"/dev/ram0" or unknown-block(1,0)')):
        raise ValueError('Expected RAM-root mount panic was not observed')
    if any(marker in text for marker in ('Run /init as init process', 'Run /sbin/init',
                                         'reboot: Restarting system', 'Mounted root (')):
        raise ValueError('Trial reached init, mounted a root, or software-rebooted')


def run(qemu, kernel, dtb, recipe, output):
    compiled = json.loads(recipe.read_text())
    command_line = arguments(compiled)
    files = {name: path.resolve(strict=True) for name, path in
             (('qemu', qemu), ('kernel', kernel), ('dtb', dtb), ('recipe', recipe))}
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}
    output.mkdir(mode=0o700)
    command = [str(files['qemu']), '-machine', 'raspi4b', '-accel', 'tcg',
               '-kernel', str(files['kernel']), '-dtb', str(files['dtb']), '-append', command_line,
               '-display', 'none', '-monitor', 'none', '-serial', 'stdio', '-no-reboot', '-nic', 'none']
    (output/'command.json').write_text(json.dumps(command, indent=2) + '\n')
    evidence = dict(status='failed', hashes=hashes, firmware_exercised=False,
                    physical_fallback_qualified=False, disk_attached=False)
    process = None
    log = output/'console.log'
    try:
        with log.open('xb') as stream:
            process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 90
            observed = None
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Guest exited instead of remaining at the intentional panic')
                text = log.read_text(errors='replace')
                if 'Kernel panic - not syncing:' in text:
                    check_log(text, compiled['nonce'])
                    if observed is None:
                        observed = time.monotonic()
                    if time.monotonic() - observed >= 5:
                        break
                if time.monotonic() >= deadline:
                    raise TimeoutError('Expected intentional panic was not observed before deadline')
                time.sleep(0.2)
            evidence.update(status='passed', scope='diskless RAM-root panic with software reboot disabled',
                            observed_panic_seconds=time.monotonic() - observed)
    except BaseException as exc:
        evidence['error'] = str(exc)
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            evidence['owned_guest_cleanup'] = True
        (output/'acceptance.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('qemu', 'kernel', 'dtb', 'recipe', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    print(json.dumps(run(**vars(parser.parse_args())), indent=2))
