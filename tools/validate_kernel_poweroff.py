#!/usr/bin/env python3
"""Qualify a candidate BCM2835 I2C module's late power-off on a disposable guest."""
import argparse
import hashlib
import json
from pathlib import Path
import uuid

from forge_runtime import Runtime
from uconsole_emulator import parser, wait_for_log


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--workspace', type=Path, required=True)
    cli.add_argument('--module', type=Path, required=True)
    cli.add_argument('--test-module', type=Path,
                     help='Optional disposable-guest atomic read/NACK/recovery test module')
    options = cli.parse_args()
    identity = uuid.uuid4().hex
    transcript = options.workspace / f'kernel-poweroff-{identity}.json'
    with options.module.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    evidence = {'validation': 'running', 'module_sha256': digest, 'commands': []}
    runtime = Runtime(parser().parse_args(['--workspace', str(options.workspace),
                                          'run', '--mode', 'maintenance']))
    try:
        runtime.start()
        wait_for_log(runtime.workspace / 'serial.log', b'root@(none):/#', runtime.process, 120)
        def execute(script):
            result = runtime.execute(script)
            evidence['commands'].append({'script': script, 'result': result})
            assert result['exit_code'] == 0, result
            return result['stdout']
        execute('set -e; mount -t proc proc /proc; mount -t sysfs sysfs /sys; uname -a')
        guest_module = '/tmp/i2c-bcm2835-' + identity + '.ko'
        runtime.upload(options.module, guest_module)
        # Ordinary module loading only: never bypass vermagic or symbol CRCs.
        execute('set -e; modprobe -r i2c_bcm2835; insmod ' + guest_module)
        execute('set -e; modprobe axp20x_i2c; modprobe axp20x_ac_power; '
                'cat /sys/class/power_supply/axp22x-ac/online; '
                'cat /sys/module/i2c_bcm2835/srcversion')
        if options.test_module:
            with options.test_module.open('rb') as source:
                evidence['test_module_sha256'] = hashlib.file_digest(source, 'sha256').hexdigest()
            guest_test = '/tmp/atomic-test-' + identity + '.ko'
            runtime.upload(options.test_module, guest_test)
            execute('insmod ' + guest_test + ' confirm_disposable=1 bus=22')
            output = execute('dmesg | tail -40')
            assert 'UCONSOLE_ATOMIC_TEST_PASS' in output, output
            assert 'WARNING:' not in output and 'BUG:' not in output, output
            execute('rmmod uconsole_i2c_atomic_test')
        execute('sync; mount -o remount,ro /')
        offset = (runtime.workspace / 'serial.log').stat().st_size
        with runtime.connect_serial() as serial:
            serial.sendall(b'/sbin/poweroff -f\n')
            # Keep the stream attached until QEMU has consumed the command;
            # disconnecting immediately can discard queued character input.
            runtime.process.wait(timeout=60)
        output = (runtime.workspace / 'serial.log').read_bytes()[offset:].decode(errors='replace')
        evidence['shutdown_console'] = output
        assert runtime.process.returncode == 0, runtime.process.returncode
        assert 'reboot: Power down' in output, output
        assert 'No atomic I2C transfer handler' not in output, output
        assert 'WARNING:' not in output and 'rcu:' not in output, output
        evidence['validation'] = 'passed'
    except BaseException as exc:
        evidence['validation'] = 'failed'
        evidence['error'] = str(exc)
        raise
    finally:
        if runtime.process is not None and runtime.process.poll() is None:
            evidence['forced_cleanup'] = True
            runtime.stop(force=True)
        runtime.release()
        with transcript.open('x') as output:
            json.dump(evidence, output, indent=2)
            output.write('\n')
        print(f'Kernel power-off acceptance: {evidence["validation"]}; {transcript}', flush=True)


if __name__ == '__main__':
    main()
