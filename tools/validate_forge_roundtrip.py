#!/usr/bin/env python3
"""Modify, checkpoint, export and reimport a new disposable CM4 image.

Installs an intentional persistent app/service and candidate kernel module.
Retains all artifacts on success or failure. Never writes a physical device.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from unittest.mock import patch
import uuid

from emulator_image import BootPartition
from forge_filesystem import check_overlay_root
from forge_runtime import Runtime
from forge_workspace import Workspace, sha256
import uconsole_emulator as emulator
import validate_systemd_poweroff


def boot_identity(image):
    result = {}
    with BootPartition(image) as boot, tempfile.TemporaryDirectory(prefix='uc-boot-proof-') as directory:
        for name in ('config.txt', 'cmdline.txt', 'kernel8.img', 'bcm2711-rpi-cm4.dtb'):
            target = Path(directory) / name
            boot.extract(name, target)
            result[name] = sha256(target)
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--image', type=Path, required=True)
    cli.add_argument('--sha256', required=True)
    cli.add_argument('--module', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True,
                     help='new directory for workspaces, exported image and evidence')
    options = cli.parse_args()
    output = options.output.resolve()
    # Several sparse image copies are retained; reject inadequate free space
    # before starting. This is a conservative fixture budget, not a reservation.
    if shutil.disk_usage(output.parent).free < 32 * 1024 ** 3:
        cli.error('round-trip acceptance needs at least 32 GiB free space')
    output.mkdir()  # Exclusive: never reuse or overwrite another test's data.
    token = uuid.uuid4().hex
    name = 'uconsole-forge-proof-' + token
    app = '/usr/local/bin/' + name
    service = '/etc/systemd/system/' + name + '.service'
    link = '/etc/systemd/system/multi-user.target.wants/' + name + '.service'
    state = '/var/lib/' + name
    source = options.image.resolve()
    candidate = options.module.resolve()
    evidence = {'validation': 'running', 'identity': token, 'commands': [],
                'physical_boot': 'unverified', 'module_sha256': sha256(candidate),
                'source': str(source), 'source_sha256': options.sha256,
                'persistent_guest_files': [app, service, link, state]}
    runtime = None
    module_path = None

    def execute(script):
        result = runtime.execute(script)
        evidence['commands'].append({'workspace': str(runtime.workspace),
                                     'script': script, 'result': result})
        if result['exit_code']:
            raise ValueError(f'Guest command failed: {result}')
        return result['stdout']

    def start(workspace):
        nonlocal runtime
        runtime = Runtime(emulator.parser().parse_args(
            ['--workspace', str(workspace), 'run', '--mode', 'maintenance']))
        runtime.start()
        emulator.wait_for_log(workspace / 'serial.log', b'root@(none):/#', runtime.process, 120)
        execute('mountpoint -q /proc || mount -t proc proc /proc; '
                'mountpoint -q /sys || mount -t sysfs sysfs /sys')

    def accept(workspace, label):
        print(f'Round trip: normal boot/shutdown ({label})', flush=True)
        # An exported result file proves persistence, not that the service ran
        # again. Clear only this fixture's output before each normal boot.
        start(workspace)
        execute(f'rm -f {state}/result')
        runtime.stop()
        evidence[label + '_result_cleared'] = True
        argv = ['validate_systemd_poweroff', '--workspace', str(workspace),
                '--module', str(candidate), '--expect-installed']
        with patch.object(sys, 'argv', argv):
            report = validate_systemd_poweroff.main()
        result = json.loads(report.read_text())
        evidence[label] = str(report)
        if result['validation'] != 'passed':
            raise ValueError(f'Normal shutdown failed: {report}')

    def verify(workspace):
        start(workspace)
        execute(f'test "$({app})" = {token} && test "$(cat {state}/result)" = {token} '
                f'&& test -L {link}')
        execute(f'test "$(modinfo -n i2c_bcm2835)" = {module_path}')
        observed = execute(f'sha256sum {module_path}').split()[0]
        if observed != evidence['module_sha256']:
            raise ValueError('Installed candidate changed during image round trip')
        runtime.stop()

    try:
        workspace = output / 'workspace'
        print('Round trip: import source image', flush=True)
        emulator.prepare(emulator.parser().parse_args([
            '--workspace', str(workspace), 'prepare', str(source), '--sha256', options.sha256]))
        evidence['native_boot_before'] = boot_identity(workspace / 'base.img')
        start(workspace)
        release = execute('uname -r').strip()
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+', release):
            raise ValueError('Unexpected guest kernel release')
        module_path = f'/lib/modules/{release}/updates/{name}/i2c-bcm2835.ko'
        module_directory = str(Path(module_path).parent)
        evidence['persistent_guest_files'].append(module_path)
        execute(f'test ! -e {app} && test ! -e {service} && test ! -e {link} && '
                f'test ! -e {state} && test ! -e {module_directory} && '
                f'mkdir -p /usr/local/bin /etc/systemd/system/multi-user.target.wants '
                f'{state} {module_directory}')
        with tempfile.TemporaryDirectory(prefix='uc-forge-proof-') as staging:
            files = {
                app: '#!/bin/sh\nprintf "%s\\n" ' + token + '\n',
                service: '[Unit]\nDescription=Persistent forge image proof\n'
                    '[Service]\nType=oneshot\nRemainAfterExit=yes\n'
                    f'ExecStart=/bin/sh -c "{app} > {state}/result"\n'
                    '[Install]\nWantedBy=multi-user.target\n',
            }
            for index, (destination, content) in enumerate(files.items()):
                path = Path(staging) / str(index)
                path.write_text(content)
                runtime.upload(path, destination)
        runtime.upload(candidate, module_path)
        execute(f'chmod 755 {app} && ln -s {service} {link} && depmod -a {release} && '
                f'test "$(modinfo -n i2c_bcm2835)" = {module_path}')
        runtime.stop()
        manager = Workspace(workspace, runtime.args.qemu_img)
        evidence['checkpoint'] = manager.checkpoint('enhanced-image')
        # Prove restore actually reinstates user data, rather than only creating
        # a checkpoint manifest. The safety checkpoint retains the mutation.
        start(workspace)
        execute(f'printf "%s\\n" altered-for-restore-test > {app}')
        runtime.stop()
        evidence['restore'] = manager.restore('enhanced-image')
        accept(workspace, 'before_export_shutdown')
        verify(workspace)
        evidence['pre_export_root'] = check_overlay_root(workspace, runtime.args.qemu_img)
        exported = output / 'enhanced.img'
        print('Round trip: export modified image', flush=True)
        emulator.export(emulator.parser().parse_args(
            ['--workspace', str(workspace), 'export', str(exported)]))
        evidence['export_sha256'] = sha256(exported)
        evidence['native_boot_after'] = boot_identity(exported)
        if evidence['native_boot_before'] != evidence['native_boot_after']:
            raise ValueError('Native boot artifacts changed; inspect retained before/after identities')
        imported = output / 'reimported'
        print('Round trip: reimport exported image', flush=True)
        emulator.prepare(emulator.parser().parse_args([
            '--workspace', str(imported), 'prepare', str(exported),
            '--sha256', evidence['export_sha256']]))
        accept(imported, 'after_reimport_shutdown')
        verify(imported)
        evidence['reimport_root'] = check_overlay_root(imported, runtime.args.qemu_img)
        if sha256(source) != options.sha256.lower():
            raise ValueError('Source image changed during acceptance')
        evidence['validation'] = 'passed'
    except BaseException as exc:
        evidence['error'] = f'{type(exc).__name__}: {exc}'
        evidence['validation'] = 'failed'
        raise
    finally:
        try:
            if runtime and runtime.process is not None and runtime.process.poll() is None:
                evidence['forced_cleanup'] = True
                runtime.stop(force=True)
            if runtime:
                runtime.release()
        finally:
            report = output / 'roundtrip.json'
            with report.open('x') as destination:
                json.dump(evidence, destination, indent=2)
                destination.write('\n')
            print(f'Round trip: {evidence["validation"]}; {report}', flush=True)


if __name__ == '__main__':
    main()
