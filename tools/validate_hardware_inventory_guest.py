#!/usr/bin/env python3
"""Capture maintenance-guest kernel contracts; differences are not test failures."""
import argparse
import json
from pathlib import Path
import threading
import uuid

from forge_filesystem import check_overlay_root
from forge_runtime import Runtime
from forge_workspace import sha256
from uconsole_emulator import parser, wait_for_log
from uconsole_hardware_probe import SUBSYSTEM_SYSFS, compare_subsystems
from validate_desktop_preparation_gui import fixture


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--image', type=Path, required=True)
    cli.add_argument('--sha256', required=True)
    cli.add_argument('--reference', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    image, output = args.image.resolve(), args.output.resolve()
    if sha256(image) != args.sha256:
        raise ValueError('Backing image hash mismatch')
    reference_record = json.loads(args.reference.read_text())
    probe = reference_record['probes']['subsystems']
    if probe['exit_code'] != 0:
        raise ValueError('Physical subsystem capture is incomplete')
    reference = json.loads(probe['stdout'])
    if not compare_subsystems(reference, reference)['complete']:
        raise ValueError('Physical subsystem capture contains read errors')
    fixture(image, output, args.sha256)
    runtime = Runtime(parser().parse_args(['--workspace', str(output), 'run',
                                          '--mode', 'maintenance', '--keyboard', 'composite']))
    evidence = {'status': 'failed', 'guest_mode': 'maintenance', 'image_sha256': args.sha256,
                'reference_sha256': sha256(args.reference), 'forced_cleanup': False}
    guest = '/tmp/subsystem-probe-' + uuid.uuid4().hex + '.py'
    cancel = threading.Event()
    try:
        runtime.start()
        wait_for_log(output / 'serial.log', b'root@(none):/#', runtime.process, 120)
        setup = runtime.execute('set -e; mountpoint -q /proc || mount -t proc proc /proc; '
                                'mountpoint -q /sys || mount -t sysfs sysfs /sys; '
                                'mountpoint -q /dev || mount -t devtmpfs devtmpfs /dev; '
                                'modprobe usbhid; modprobe cdc_acm', cancel=cancel)
        if setup['exit_code']:
            raise ValueError('Guest inventory setup failed')
        source = output / 'subsystem-probe.py'
        source.write_text(SUBSYSTEM_SYSFS)
        runtime.upload(source, guest)
        result = runtime.execute('python3 ' + guest, cancel=cancel)
        evidence['probe'] = result
        if result['exit_code']:
            raise ValueError('Guest subsystem capture failed; see partial probe output')
        observed = json.loads(result['stdout'])
        evidence['comparison'] = compare_subsystems(reference, observed)
        if not evidence['comparison']['complete']:
            raise ValueError('Guest subsystem comparison is incomplete')
        cleanup = runtime.execute('rm -- ' + guest, cancel=cancel)
        if cleanup['exit_code']:
            raise ValueError('Could not remove exact guest probe')
        runtime.stop()
        if runtime.process.returncode != 0:
            raise ValueError('Guest did not exit cleanly')
        evidence['root_after_stop'] = check_overlay_root(output, runtime.args.qemu_img)
        evidence['base_unchanged'] = sha256(image) == args.sha256
        if not evidence['base_unchanged']:
            raise ValueError('Backing image changed')
        evidence['status'] = 'captured'
    except BaseException as exc:
        evidence['error'] = str(exc)
        raise
    finally:
        try:
            if runtime.process is not None and runtime.process.poll() is None:
                try:
                    runtime.stop()
                except Exception as exc:
                    evidence['cleanup_error'] = str(exc)
                    evidence['forced_cleanup'] = True
                    runtime.stop(force=True)
        finally:
            (output / 'inventory.json').write_text(json.dumps(evidence, indent=2) + '\n')
            print(f'Subsystem inventory: {evidence["status"]}; {output / "inventory.json"}', flush=True)


if __name__ == '__main__':
    main()
