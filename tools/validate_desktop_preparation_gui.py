#!/usr/bin/env python3
"""Linux real-guest desktop preparation and live cancellation acceptance.

Creates two retained disposable overlays backed read-only by a hash-pinned raw
image. Does not copy or modify the base. Requires Tk/Xvfb, ps and built QEMU.
"""
import argparse
import gc
import json
from pathlib import Path
import struct
import subprocess
import time
from unittest.mock import patch

from emulator_image import BootPartition
from emulator_dtb import patch_strings
from forge_filesystem import check_overlay_root
from forge_workspace import WorkspaceLock, sha256
from uconsole_emulator import CM4_PATCH, SURROGATE_SCHEMA, executable


def fixture(image, destination, digest):
    destination.mkdir(mode=0o700)
    (destination / 'base.img').symlink_to(image)
    with BootPartition(image) as boot:
        boot.extract('kernel8.img', destination / 'kernel8.img')
        boot.extract('bcm2711-rpi-cm4.dtb', destination / 'cm4-original.dtb')
    dtb = patch_strings((destination / 'cm4-original.dtb').read_bytes(), CM4_PATCH)
    (destination / 'cm4-qemu.dtb').write_bytes(dtb)
    with image.open('rb') as stream:
        stream.seek(440)
        disk_id, = struct.unpack('<I', stream.read(4))
    size = 1 << (image.stat().st_size - 1).bit_length()
    subprocess.run([executable('qemu-img'), 'create', '-f', 'qcow2', '-F', 'raw',
                    '-b', str(image), str(destination / 'disk.qcow2'), str(size)], check=True)
    config = {'schema': 1, 'machine': 'raspi4b', 'coverage': 'partial-cm4',
              'source': str(image), 'source_sha256': digest, 'base_sha256': digest,
              'base_bytes': image.stat().st_size,
              'root': f'PARTUUID={disk_id:08x}-02',
              'kernel_sha256': sha256(destination / 'kernel8.img'),
              'dtb_sha256': sha256(destination / 'cm4-qemu.dtb')}
    (destination / 'machine.json').write_text(json.dumps(config) + '\n')


def group_members(group):
    result = subprocess.run(['ps', '-eo', 'pid=,pgid=,stat=,comm='],
                            text=True, capture_output=True, check=True)
    rows = [line.split(None, 3) for line in result.stdout.splitlines()]
    return [{'pid': int(pid), 'state': state, 'command': command}
            for pid, pgid, state, command in rows if int(pgid) == group]


def exercise(workspace, cancel):
    import tkinter as tk
    from uconsole_workbench import Workbench
    root = tk.Tk()
    app = Workbench(root, workspace)
    app.mode.set('desktop')
    record = {'validation': 'failed', 'cancel_requested': False}
    errors, launches, continuation = [], [], []
    deadline = time.monotonic() + 240
    popen = subprocess.Popen

    def launch(command, *args, **kwargs):
        process = popen(command, *args, **kwargs)
        if any(str(item).endswith('/uconsole_emulator.py') for item in command):
            launches.append(process.pid)
        return process

    def poll():
        try:
            if time.monotonic() > deadline:
                raise TimeoutError('Desktop preparation acceptance timed out')
            if cancel and launches and not record['cancel_requested']:
                members = group_members(launches[0])
                if any(item['command'].startswith('qemu-system') and
                       not item['state'].startswith('Z') for item in members):
                    record['observed_live_group'] = members
                    app.cancel_lifecycle()
                    record['cancel_requested'] = True
            if app.lifecycle is not None:
                root.after(50, poll)
                return
            result = app.controller.job(record['job_id'])
            record['job'] = result
            config = json.loads((workspace / 'machine.json').read_text())
            record['boot_continuations'] = len(continuation)
            if cancel:
                if not record['cancel_requested'] or result['status'] != 'cancelled':
                    raise ValueError('Did not cancel live preparation')
                if continuation or config.get('surrogate_desktop'):
                    raise ValueError('Cancelled setup booted or claimed completed adapters')
                record['remaining_group'] = group_members(launches[0])
                if any(not row['state'].startswith('Z') for row in record['remaining_group']):
                    raise ValueError('Cancelled preparation left a running group member')
            else:
                if result['status'] != 'completed' or len(continuation) != 1:
                    raise ValueError('Preparation did not complete and continue exactly once')
                if config.get('surrogate_desktop', {}).get('schema') != SURROGATE_SCHEMA:
                    raise ValueError('Desktop schema was not published')
                record['root_after_setup'] = check_overlay_root(workspace, executable('qemu-img'))
            with WorkspaceLock(workspace):
                subprocess.run([executable('qemu-img'), 'info', str(workspace / 'disk.qcow2')],
                               check=True, capture_output=True)
            record['ownership_released'] = True
            record['validation'] = 'passed'
            root.quit()
        except BaseException as exc:
            errors.append(exc)
            root.quit()

    try:
        with patch('uconsole_workbench.history_default_path', return_value=workspace / 'jobs.sqlite3'), \
                patch.object(app, 'start', side_effect=app._start_prepared), \
                patch('forge_controller.subprocess.Popen', side_effect=launch), \
                patch.object(app, 'launch_locked', side_effect=lambda args: continuation.append(args.mode)):
            app._start_prepared()  # Isolate the desktop-preparation primitive.
            record['job_id'] = app.lifecycle
            root.after(50, poll)
            root.mainloop()
    except BaseException as exc:
        errors.append(exc)
    finally:
        if app.controller:
            if app.lifecycle:
                app.controller.cancel(app.lifecycle)
            app.controller.close()
        if errors:
            record['error'] = str(errors[0])
        record['console'] = app.console.get('1.0', 'end')
        (workspace / 'acceptance.json').write_text(json.dumps(record, indent=2) + '\n')
        root.destroy()
    if errors:
        raise errors[0]
    return record


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--image', type=Path, required=True)
    cli.add_argument('--sha256', required=True)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    image = args.image.resolve()
    digest = sha256(image)
    if digest != args.sha256.lower():
        raise ValueError('Base checksum mismatch')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    for name, cancel in (('complete', False), ('cancel', True)):
        workspace = output / name
        fixture(image, workspace, digest)
        exercise(workspace, cancel)
        gc.collect()  # Release Tk callback cycles on the creating thread.
    if sha256(image) != digest:
        raise ValueError('Base image changed')
    (output / 'summary.json').write_text(json.dumps({
        'validation': 'passed', 'base_image': str(image), 'base_sha256': digest,
        'base_unchanged': True, 'cases': ['complete', 'cancel'],
        'desktop_boot_executed': False,
    }, indent=2) + '\n')
    print(f'Desktop preparation acceptance passed; retained workspaces: {output}')


if __name__ == '__main__':
    main()
