#!/usr/bin/env python3
"""Check AXP221 register and in-flight I2C restoration across real migration."""
from contextlib import ExitStack
import argparse
from pathlib import Path
import tempfile

from test_emulator_adc_migration import Machine, migrate
from uconsole_emulator import qmp
from forge_scenario import change_power, power_device, query_power


def exercise(case, legacy_source=None):
    if case not in ('write', 'read', 'legacy'):
        raise ValueError('Unknown PMIC migration case: ' + case)
    if case == 'legacy' and legacy_source is None:
        raise ValueError('Legacy rejection requires a version-2 source binary')
    with tempfile.TemporaryDirectory(prefix='uc-pmic-mig-') as temporary, ExitStack() as stack:
        root = Path(temporary)
        source = Machine(root, 'source', stack, base=0xfe205000, address=0x34,
                         binary=legacy_source)
        target = Machine(root, 'target', stack, incoming=True, base=0xfe205000, address=0x34)
        try:
            source_control = lambda command, arguments: qmp(source.monitor, command, arguments)
            target_control = lambda command, arguments: qmp(target.monitor, command, arguments)
            source.send([0x44, 0x20])  # Enable the power-key press IRQ.
            change_power(source_control, 'battery_capacity', 25)
            change_power(source_control, 'power_key_pressed', True)
            expected = query_power(source_control)
            # Ordinary writable voltage-control registers; no shutdown command.
            source.send([0x28, 0x12, 0x34])
            if case == 'legacy':
                endpoint = 'unix:' + str(root / 'migration')
                qmp(target.monitor, 'migrate-incoming', {'uri': endpoint})
                qmp(source.monitor, 'migrate', {'uri': endpoint})
                assert target.process.wait(timeout=30) != 0, 'Legacy stream was accepted'
                error = (root / 'target.log').read_text()
                assert 'axp2xx_pmu' in error and 'load' in error, error
                print('PASS: rejected legacy PMIC stream without I2C parent state', flush=True)
                return
            if case == 'write':
                source.begin(3)
                source.write(16, 0x28)
                source.write(16, 0x56)
            else:
                source.send([0x28])
                source.begin(2, read=True)
                assert source.read(16) == 0x12
            migrate(source, target, root)
            assert query_power(target_control) == expected
            assert target_control('qom-get', {'path': power_device(target_control),
                                             'property': 'irq-active'}) is True
            if case == 'write':
                target.write(16, 0x78)
                assert target.read(4) & 0x102 == 2, 'Destination rejected remaining byte'
                observed = target.read_reg(0x28)
                assert observed == 0x5678, f'Restored write: expected 0x5678, got {observed:#06x}'
            else:
                assert target.read(16) == 0x34, 'Destination lost active I2C slave'
                assert target.read(4) & 0x102 == 2
                assert target.read_reg(0x28) == 0x1234
            print('PASS: PMIC migration ' + case, flush=True)
        except BaseException:
            for name in ('source', 'target'):
                print((root / (name + '.log')).read_text())
            raise


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--legacy-source', type=Path,
                     help='Also verify rejection of an actual retained version-2 QEMU stream')
    args = cli.parse_args()
    for case in ('write', 'read'):
        exercise(case)
    if args.legacy_source:
        exercise('legacy', args.legacy_source.resolve())
