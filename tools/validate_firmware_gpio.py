#!/usr/bin/env python3
"""Verify the official kernel binds its firmware GPIO driver in a test guest."""
import argparse
import json
from pathlib import Path
import uuid

from forge_runtime import Runtime
from uconsole_emulator import parser, wait_for_log


PROBE = r'''set -eux
mountpoint -q /proc || mount -t proc proc /proc
mountpoint -q /sys || mount -t sysfs sysfs /sys
driver=/sys/bus/platform/devices/soc:firmware:gpio/driver
test -L "$driver"
test "$(basename "$(readlink "$driver")")" = raspberrypi-exp-gpio
found=0
for chip in /sys/class/gpio/gpiochip*; do
    test -f "$chip/label" || continue
    if test "$(cat "$chip/label")" = raspberrypi-exp-gpio; then
        test "$(cat "$chip/ngpio")" = 8
        printf 'firmware_gpio_driver=bound\nlabel='
        cat "$chip/label"
        printf 'ngpio='
        cat "$chip/ngpio"
        found=$((found + 1))
    fi
done
test "$found" = 1
'''


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--workspace', type=Path, required=True,
                     help='Disposable test workspace; booted in maintenance mode')
    options = cli.parse_args()
    workspace = options.workspace.resolve()
    evidence = {'validation': 'failed', 'physical_fidelity': 'unverified'}
    target = workspace / ('firmware-gpio-' + uuid.uuid4().hex + '.json')
    args = parser().parse_args(['--workspace', str(workspace), 'run', '--mode', 'maintenance'])
    runtime = Runtime(args)
    try:
        runtime.start()
        wait_for_log(workspace / 'serial.log', b'root@(none):/#', runtime.process, 120)
        result = runtime.execute(PROBE)
        evidence['probe'] = result
        if result['exit_code'] != 0:
            raise ValueError(f'Firmware GPIO driver did not bind: {result}')
        console = (workspace / 'serial.log').read_text(errors='replace')
        evidence['console'] = console
        if 'Failed to get GPIO' in console or 'cleanup_srcu_struct' in console:
            raise ValueError('Firmware GPIO boot failure remains in the kernel log')
        runtime.stop()
        evidence['validation'] = 'passed'
    except BaseException as exc:
        evidence['error'] = str(exc)
        raise
    finally:
        try:
            if runtime.process is not None and runtime.process.poll() is None:
                try:
                    runtime.stop()
                except BaseException as exc:
                    evidence['cleanup_error'] = str(exc)
                    evidence['forced_cleanup'] = True
                    runtime.stop(force=True)
            else:
                runtime.release()
        finally:
            if 'console' not in evidence:
                try:
                    evidence['console'] = (workspace / 'serial.log').read_text(errors='replace')
                except OSError as exc:
                    evidence['console_error'] = str(exc)
            with target.open('x') as output:
                json.dump(evidence, output, indent=2)
                output.write('\n')
            print(f'Firmware GPIO validation: {evidence["validation"]}; {target}', flush=True)


if __name__ == '__main__':
    main()
