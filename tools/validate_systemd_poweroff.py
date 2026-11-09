#!/usr/bin/env python3
"""Temporary, marker-gated normal-boot shutdown test on a disposable image."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from unittest.mock import patch
import uuid

from forge_runtime import Runtime
from forge_filesystem import check_overlay_root
from uconsole_emulator import command, parser, wait_for_log


def wait_for_shutdown(process, console_path, identity, timeout):
    """Fail promptly on a guest fixture error, without mistaking trace text for it."""
    deadline = time.monotonic() + timeout
    failure = re.compile(r'(?:^|: )UCONSOLE_SYSTEMD_FAILURE_' + re.escape(identity) +
                         r':([1-9][0-9]*)\r?$', re.MULTILINE)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout)
        try:
            process.wait(timeout=min(1, remaining))
        except subprocess.TimeoutExpired:
            pass
        console = console_path.read_text(errors='replace')
        error = failure.search(console)
        if error:
            raise ValueError(f'Guest shutdown fixture failed with exit {error.group(1)}')
        if process.poll() is not None:
            return


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--workspace', type=Path, required=True)
    cli.add_argument('--module', type=Path, required=True)
    cli.add_argument('--expect-installed', action='store_true',
                     help='verify the already installed candidate; do not hot-replace the adapter')
    cli.add_argument('--boot-shutdown-timeout', type=int, default=600,
                     help='seconds allowed for normal boot (including first-boot reboot) and shutdown')
    options = cli.parse_args()
    if options.boot_shutdown_timeout <= 0:
        cli.error('--boot-shutdown-timeout must be positive')
    workspace = options.workspace.resolve()
    identity = uuid.uuid4().hex
    name = 'uconsole-poweroff-test-' + identity
    directory = '/var/tmp/' + name
    service = '/etc/systemd/system/' + name + '.service'
    timer = '/etc/systemd/system/' + name + '.timer'
    link = '/etc/systemd/system/timers.target.wants/' + name + '.timer'
    diagnostic_service = '/etc/systemd/system/' + name + '-diagnostic.service'
    diagnostic_timer = '/etc/systemd/system/' + name + '-diagnostic.timer'
    diagnostic_link = '/etc/systemd/system/timers.target.wants/' + name + '-diagnostic.timer'
    marker = 'uconsole.poweroff-test=' + identity
    evidence_path = workspace / f'systemd-poweroff-{identity}.json'
    fixture_files = [link, timer, service, diagnostic_link, diagnostic_timer, diagnostic_service]
    evidence = {'validation': 'running', 'commands': [], 'fixtures': [directory, *fixture_files]}
    evidence['boot_shutdown_timeout'] = options.boot_shutdown_timeout
    evidence['expect_installed'] = options.expect_installed
    with options.module.open('rb') as source:
        evidence['module_sha256'] = hashlib.file_digest(source, 'sha256').hexdigest()
    runtime = None
    installed = False

    def start(mode):
        nonlocal runtime
        args = parser().parse_args(['--workspace', str(workspace), 'run', '--mode', mode])
        runtime = Runtime(args)
        if mode == 'normal':
            def marked_command(*args, **kwargs):
                result = command(*args, **kwargs)
                result[result.index('-append') + 1] += ' ' + marker
                return result
            with patch('uconsole_emulator.command', side_effect=marked_command):
                runtime.start()
        else:
            runtime.start()
            wait_for_log(workspace / 'serial.log', b'root@(none):/#', runtime.process, 120)

    def execute(script):
        result = runtime.execute(script)
        evidence['commands'].append({'script': script, 'result': result})
        assert result['exit_code'] == 0, result
        return result['stdout']

    try:
        start('maintenance')
        execute(f'test ! -e {directory} && mkdir {directory}')
        installed = True
        runtime.upload(options.module, directory + '/driver.ko')
        driver_setup = ('modprobe i2c_bcm2835\n' if options.expect_installed else
                        # The stock remove callback frees its IRQ before
                        # deleting clients. Unbind the PMIC on a live bus.
                        'test -L /sys/bus/i2c/drivers/axp20x-i2c/22-0034\n'
                        'printf %s 22-0034 > /sys/bus/i2c/drivers/axp20x-i2c/unbind\n'
                        'modprobe -r i2c_bcm2835\n'
                        f'insmod {directory}/driver.ko\n')
        with tempfile.TemporaryDirectory(prefix='uc-systemd-poweroff-') as staging:
            files = {
                directory + '/run.sh': '#!/bin/sh\nset -eu\n'
                    "trap 'result=$?; if [ \"$result\" -ne 0 ]; then "
                    f'echo UCONSOLE_SYSTEMD_FAILURE_{identity}:$result; '
                    "fi' EXIT\nset -x\n"
                    'systemctl is-active --quiet multi-user.target\n'
                    + driver_setup +
                    'modprobe axp20x_i2c\nmodprobe axp20x_ac_power\n'
                    'test -L /sys/bus/i2c/drivers/axp20x-i2c/22-0034\n'
                    'cat /sys/class/power_supply/axp22x-ac/online\n'
                    'cat /sys/module/i2c_bcm2835/srcversion\n'
                    'test "$(cat /sys/module/i2c_bcm2835/srcversion)" = '
                    f'"$(modinfo -F srcversion {directory}/driver.ko)"\n'
                    f'echo UCONSOLE_SYSTEMD_POWEROFF_{identity}\n'
                    'systemctl poweroff --no-block\n',
                service: '[Unit]\nDescription=Disposable forge shutdown acceptance\n'
                    f'ConditionKernelCommandLine={marker}\nAfter=multi-user.target\n'
                    '[Service]\nType=oneshot\nTimeoutStartSec=30\n'
                    f'ExecStart=/bin/sh {directory}/run.sh\nStandardOutput=journal+console\n'
                    'StandardError=journal+console\n',
                timer: '[Unit]\nDescription=Disposable forge shutdown test trigger\n'
                    f'ConditionKernelCommandLine={marker}\n'
                    '[Timer]\nOnBootSec=15s\nAccuracySec=1s\n'
                    f'Unit={name}.service\n',
                diagnostic_service: '[Unit]\nDescription=Disposable forge boot diagnostics\n'
                    f'ConditionKernelCommandLine={marker}\n'
                    '[Service]\nType=oneshot\nTimeoutStartSec=30\n'
                    'ExecStart=/bin/sh -c "echo UCONSOLE_BOOT_DIAGNOSTICS; '
                    'systemctl --no-pager list-jobs; systemctl --no-pager --failed; '
                    'journalctl -b -n 80 --no-pager; echo UCONSOLE_BOOT_DIAGNOSTICS_END"\n'
                    'StandardOutput=journal+console\nStandardError=journal+console\n',
                diagnostic_timer: '[Unit]\nDescription=Disposable forge boot diagnostic trigger\n'
                    f'ConditionKernelCommandLine={marker}\n'
                    '[Timer]\nOnBootSec=120s\nAccuracySec=1s\n'
                    f'Unit={name}-diagnostic.service\n',
            }
            for index, (target, content) in enumerate(files.items()):
                source = Path(staging) / str(index)
                source.write_text(content)
                runtime.upload(source, target)
        execute(f'mkdir -p /etc/systemd/system/timers.target.wants && '
                f'ln -s {timer} {link} && ln -s {diagnostic_timer} {diagnostic_link}')
        runtime.stop()
        start('normal')
        wait_for_shutdown(runtime.process, workspace / 'serial.log', identity,
                          options.boot_shutdown_timeout)
        console = (workspace / 'serial.log').read_text(errors='replace')
        evidence['normal_console'] = console
        assert runtime.process.returncode == 0, runtime.process.returncode
        assert 'UCONSOLE_SYSTEMD_POWEROFF_' + identity in console, console[-4000:]
        assert 'reboot: Power down' in console, console[-4000:]
        shutdown = console.split('UCONSOLE_SYSTEMD_POWEROFF_' + identity, 1)[1]
        assert 'No atomic I2C transfer handler' not in shutdown, shutdown
        assert 'WARNING:' not in shutdown and 'rcu:' not in shutdown, shutdown
        runtime.release()
        evidence['root_after_normal_shutdown'] = check_overlay_root(workspace, runtime.args.qemu_img)
        evidence['normal_shutdown_passed'] = True
    except BaseException as exc:
        evidence['error'] = str(exc)
        raise
    finally:
        evidence['validation'] = 'failed'
        if runtime and runtime.process is not None and runtime.process.poll() is None:
            evidence['forced_cleanup'] = True
            evidence['last_console'] = (workspace / 'serial.log').read_text(errors='replace')
            runtime.stop(force=True)
        if runtime:
            runtime.release()
        try:
            if installed:
                start('maintenance')
                # Exact, per-run paths only; never remove unrelated guest data.
                execute(f'rm -f {" ".join(fixture_files)} {directory}/run.sh {directory}/driver.ko; '
                        f'rmdir {directory}')
                runtime.stop()
                evidence['fixtures_removed'] = True
            evidence['validation'] = ('passed' if evidence.get('normal_shutdown_passed') and
                                      evidence.get('fixtures_removed') else 'failed')
        except BaseException as exc:
            evidence['cleanup_error'] = str(exc)
            raise
        finally:
            if runtime and runtime.process is not None and runtime.process.poll() is None:
                evidence['forced_fixture_cleanup'] = True
                runtime.stop(force=True)
            with evidence_path.open('x') as output:
                json.dump(evidence, output, indent=2)
                output.write('\n')
            print(f'Systemd power-off acceptance: {evidence["validation"]}; {evidence_path}', flush=True)
    return evidence_path


if __name__ == '__main__':
    main()
