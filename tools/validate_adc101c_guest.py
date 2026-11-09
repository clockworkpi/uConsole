#!/usr/bin/env python3
"""Validate the stock ADC driver in a disposable emulator overlay, never SSH."""
import argparse
import json
from pathlib import Path
import shlex

from forge_filesystem import check_overlay_root
from forge_runtime import Runtime
from forge_workspace import sha256
from uconsole_emulator import parser, wait_for_log
from validate_desktop_preparation_gui import fixture


READER = '''import json
from pathlib import Path
devices = [p for p in Path('/sys/bus/iio/devices').glob('iio:device*')
           if (p/'of_node/compatible').is_file()
           and b'ti,adc101c' in (p/'of_node/compatible').read_bytes().split(bytes([0]))]
assert len(devices) == 1, [str(p) for p in devices]
p = devices[0]
result = {'device': str(p), 'readings': [],
          'reference_supply_present': (p/'of_node/vref-supply').exists()}
for unused in range(3):
    try:
        result['readings'].append(int((p/'in_voltage_raw').read_text()))
    except OSError as error:
        result['readings'].append({'errno': error.errno})
try:
    result['scale'] = (p/'in_voltage_scale').read_text().strip()
except OSError as error:
    result['scale'] = {'errno': error.errno}
print('ADC_SAMPLE:' + json.dumps(result))
'''


def run(image, digest, output, *, reference='fixed'):
    if reference not in ('fixed', 'missing'):
        raise ValueError('Unknown reference profile')
    image, output = Path(image).resolve(), Path(output).resolve()
    if sha256(image) != digest:
        raise ValueError('Backing image hash mismatch')
    fixture(image, output, digest)
    runtime = Runtime(parser().parse_args(['--workspace', str(output), 'run', '--mode', 'maintenance',
                                          '--adc-reference', reference]))
    evidence = {'status': 'failed', 'base_sha256': digest, 'samples': [], 'guest': [],
                'reference_profile': reference,
                'scope': 'ADC reference and power-loss scenarios, not physical equivalence'}
    try:
        runtime.start()
        wait_for_log(output / 'serial.log', b'root@(none):/#', runtime.process, 120)

        def execute(script):
            result = runtime.execute(script)
            evidence['guest'].append(result)
            if result['exit_code']:
                raise ValueError('Guest command failed; see evidence')
            return result['stdout']

        execute('set -e; mountpoint -q /proc || mount -t proc proc /proc; '
                'mountpoint -q /sys || mount -t sysfs sysfs /sys; '
                'modprobe i2c_bcm2835; modprobe ti_adc081c')

        def change(prop, value):
            runtime.control('qom-set', {'path': '/machine/battery-adc', 'property': prop, 'value': value})
            actual = runtime.control('qom-get', {'path': '/machine/battery-adc', 'property': prop})
            if actual != value:
                raise ValueError('ADC control readback mismatch')

        def sample():
            output = execute('python3 -c ' + shlex.quote(READER))
            value = json.loads(next(line.split(':', 1)[1] for line in output.splitlines()
                                    if line.startswith('ADC_SAMPLE:')))
            evidence['samples'].append(value)
            if value['reference_supply_present'] != (reference == 'fixed'):
                raise ValueError('Guest reference-supply device tree mismatch')
            return value

        for voltage, expected in ((0, 0), (1650000, 512), (3300000, 1023)):
            change('input-uv', voltage)
            observed = sample()
            scale_ok = (observed['scale'] == {'errno': 22} if reference == 'missing' else
                        abs(float(observed['scale']) - 3300 / 1024) < 1e-8)
            if observed['readings'][-1] != expected or not scale_ok:
                raise ValueError('Guest ADC conversion or scale mismatch')
        change('powered', False)
        if sample()['readings'] != [{'errno': 5}] * 3:
            raise ValueError('Unpowered ADC did not report I/O failure')
        change('powered', True)
        if sample()['readings'][-1] != 1023:
            raise ValueError('ADC did not recover after power restoration')
        runtime.stop()
        if runtime.process.returncode != 0:
            raise ValueError('Guest did not exit cleanly')
        evidence['root_after_stop'] = check_overlay_root(output, runtime.args.qemu_img)
        evidence['base_unchanged'] = sha256(image) == digest
        if not evidence['base_unchanged']:
            raise ValueError('Backing image changed')
        evidence['status'] = 'passed'
    finally:
        if runtime.process is not None and runtime.process.poll() is None:
            try:
                runtime.stop()
            except Exception as error:
                evidence['cleanup_error'] = str(error)
                runtime.stop(force=True)
        (output / 'adc-acceptance.json').write_text(json.dumps(evidence, indent=2) + '\n')
        print('ADC guest: ' + evidence['status'])


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--image', type=Path, required=True)
    cli.add_argument('--sha256', required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--reference', choices=['fixed', 'missing'], default='fixed')
    args = cli.parse_args()
    run(args.image, args.sha256, args.output, reference=args.reference)


if __name__ == '__main__':
    main()
