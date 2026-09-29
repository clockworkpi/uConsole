#!/usr/bin/python3
"""Verify and optionally flash the bundled SIM7600G22 modem firmware packages."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

MANIFEST = Path(__file__).with_name('modem-firmware-manifest.json')
SYSTEM_HELPER = Path('/usr/lib/uconsole-modem/uconsole-modem-flash.py')
SYSTEM_FASTBOOT = SYSTEM_HELPER.with_name('fastboot')


def verify_package(directory, package):
    """Check every partition before allowing even the first flash command."""
    for item in package['partitions']:
        path = directory / item['file']
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Missing regular firmware file: {path}')
        checksum = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                checksum.update(chunk)
        digest = checksum.hexdigest()
        if digest != item['sha256']:
            raise ValueError(f'Firmware checksum mismatch: {path}')


def query_devices(fastboot):
    if not fastboot.is_file() or not os.access(fastboot, os.X_OK):
        raise ValueError(f'Fastboot executable not found: {fastboot}')
    listing = subprocess.run([str(fastboot), 'devices'], check=True,
                             capture_output=True, text=True, timeout=15)
    return [line.split() for line in listing.stdout.splitlines() if line.strip()]


def update(directory, package, fastboot, serial, flash=False):
    verify_package(directory, package)
    devices = query_devices(fastboot)
    if devices != [[serial, 'fastboot']]:
        raise ValueError('Expected exactly one fastboot device with the selected serial; '
                         'check the serial and disconnect other fastboot devices.')
    if not flash:
        print('Checksums and device selection verified. No partitions were written.')
        return
    for item in package['partitions']:
        print(f"Flashing {item['partition']}...", flush=True)
        subprocess.run([str(fastboot), '-s', serial, 'flash', item['partition'],
                        str(directory / item['file'])], check=True, timeout=180)
    # Reboot only after every partition has been written successfully.
    subprocess.run([str(fastboot), '-s', serial, 'reboot'], check=True, timeout=30)
    print('All partitions written; reboot requested. Verify modem operation after reboot.')


def main():
    packages = json.loads(MANIFEST.read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='extracted firmware package directory')
    parser.add_argument('--version', choices=packages, required=True)
    parser.add_argument('--serial', help='serial shown by fastboot devices')
    parser.add_argument('--fastboot', type=Path, help='override the package fastboot executable')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--verify-only', action='store_true', help='verify files without USB access')
    actions.add_argument('--list-devices', action='store_true', help='verify files and list fastboot devices without writes')
    actions.add_argument('--flash', action='store_true', help='write firmware after preflight checks')
    args = parser.parse_args()
    if not args.verify_only and not args.list_devices and not args.serial:
        parser.error('--serial is required for device checks or flashing')
    directory = args.directory.resolve()
    package = packages[args.version]
    try:
        if args.verify_only:
            verify_package(directory, package)
            print('All firmware file checksums match the selected bundled package.')
        else:
            # The installed administrative helper only executes its root-installed tool.
            # The source CLI retains --fastboot for native development builds.
            if Path(__file__).resolve() == SYSTEM_HELPER:
                if args.fastboot and args.fastboot.resolve() != SYSTEM_FASTBOOT:
                    raise ValueError('The installed updater requires its system fastboot executable')
                fastboot = SYSTEM_FASTBOOT
            else:
                fastboot = (args.fastboot or directory / 'fastboot/bin/fastboot').resolve()
            if args.list_devices:
                verify_package(directory, package)
                for device in query_devices(fastboot):
                    if len(device) == 2 and device[1] == 'fastboot':
                        print(device[0])
            else:
                update(directory, package, fastboot, args.serial, args.flash)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Stopped: {error}', file=sys.stderr)
        print('No further flash or reboot commands will be issued.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
