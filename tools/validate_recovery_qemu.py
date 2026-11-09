"""Boot the RAM recovery image without disks; qualify missing-WLAN reboot only."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def check_log(text, returncode):
    required = ('Run /init as init process', 'registered new interface driver brcmfmac',
                'ip: SIOCGIFFLAGS: No such device',
                'Forge recovery failed; returning to normal boot', 'reboot: Restarting system')
    if returncode != 0 or any(marker not in text for marker in required):
        raise RuntimeError('Did not observe the expected missing-WLAN recovery reboot')
    if 'Kernel panic' in text or 'mount: mounting' in text or 'error while loading shared libraries' in text:
        raise RuntimeError('Recovery boot failed before the qualified network boundary')
    positions = [text.index(marker) for marker in required]
    if positions != sorted(positions):
        raise RuntimeError('Recovery boot markers are out of order')


def check_deadline(text, returncode):
    """Qualify the controlled five-minute trial, not any QEMU process exit."""
    if returncode != 0:
        raise RuntimeError('QEMU did not exit on guest reboot')
    if any(marker in text for marker in ('Kernel panic', 'Forge recovery failed;',
                                         'terminating on signal', 'mount: mounting')):
        raise RuntimeError('Guest failed or was externally terminated')
    started = re.findall(r'\[\s*([0-9.]+)\] Run /init as init process', text)
    rebooted = re.findall(r'\[\s*([0-9.]+)\] reboot: Restarting system', text)
    if len(started) != 1 or len(rebooted) != 1 or 'Accepted publickey for root' not in text:
        raise RuntimeError('Expected one recovery boot with authenticated SSH and one reboot')
    elapsed = float(rebooted[0]) - float(started[0])
    if not 300 <= elapsed <= 315:
        raise RuntimeError('Reboot did not occur within the recovery deadline window')
    return {'status': 'passed', 'scope': 'controlled emulator software deadline',
            'init_to_reboot_guest_seconds': elapsed, 'hardware_watchdog_qualified': False}


def run(qemu, kernel, dtb, image, expected_sha256, output):
    files = {name: Path(path).resolve(strict=True) for name, path in
             (('qemu', qemu), ('kernel', kernel), ('dtb', dtb), ('image', image))}
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}
    if hashes['image'] != expected_sha256:
        raise ValueError('Recovery image differs from expected digest')
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    command = [str(files['qemu']), '-machine', 'raspi4b', '-accel', 'tcg',
               '-kernel', str(files['kernel']), '-dtb', str(files['dtb']),
               '-initrd', str(files['image']), '-append',
               'earlycon=pl011,mmio32,0xfe201000 console=ttyAMA1,115200 rdinit=/init uconsole.recovery=1 panic=10',
               '-display', 'none', '-monitor', 'none', '-serial', 'stdio', '-no-reboot']
    (output / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
    with (output / 'console.log').open('xb') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=90)
    check_log((output / 'console.log').read_text(errors='replace'), result.returncode)
    evidence = {'status': 'passed', 'scope': 'diskless RAM boot and missing-WLAN reboot',
                'hashes': hashes, 'network_qualified': False, 'hardware_recovery_qualified': False}
    (output / 'acceptance.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return evidence


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('qemu', 'kernel', 'dtb', 'image', 'expected-sha256', 'output'):
        cli.add_argument('--' + name, required=True)
    args = vars(cli.parse_args())
    print(json.dumps(run(**args)))
