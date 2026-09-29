#!/usr/bin/env python3
"""ADC-only migration contract through two stopped raspi4b qtest instances.

Explicitly align qtest virtual clocks; this is not running-guest or whole-board
migration qualification. Exercise the actual VMState stream and I2C controller.
"""
from contextlib import ExitStack
import argparse
from pathlib import Path
import subprocess
import tempfile
import time

from test_emulator_pmic import connect
from uconsole_emulator import executable, qmp

CASES = ('acquire', 'convert', 'write', 'read', 'interval', 'unpowered',
         'reference-missing', 'reference-mismatch')


class Machine:
    def __init__(self, root, name, stack, incoming=False, *, base=0xfe804000, address=0x54,
                 binary=None, reference='fixed'):
        if reference not in ('fixed', 'missing'):
            raise ValueError('Unknown ADC reference profile')
        self.base, self.address = base, address
        self.monitor = root / (name + '-qmp')
        test = root / (name + '-test')
        log = stack.enter_context((root / (name + '.log')).open('wb'))
        self.process = subprocess.Popen([
            str(binary or executable('qemu-system-aarch64')), '-M', 'raspi4b', '-accel', 'qtest',
            '-S', '-display', 'none', '-serial', 'none', '-monitor', 'none',
            '-qtest', f'unix:{test},server=on,wait=off',
            '-qmp', f'unix:{self.monitor},server=on,wait=off',
            *(['-global', 'adc101c.reference-supply-present=false'] if reference == 'missing' else []),
            *(['-incoming', 'defer'] if incoming else [])], stdout=log, stderr=log)
        stack.callback(self.close)
        sock = stack.enter_context(connect(test, self.process))
        self.stream = stack.enter_context(sock.makefile('rwb', buffering=0))

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def command(self, value):
        self.stream.write((value + '\n').encode())
        while True:
            reply = self.stream.readline().decode().strip()
            if reply.startswith('IRQ '):
                continue
            assert reply.startswith('OK'), reply
            return reply

    def write(self, register, value):
        self.command(f'writel {self.base + register:#x} {value:#x}')

    def read(self, register):
        return int(self.command(f'readl {self.base + register:#x}').split()[1], 16)

    def begin(self, size, read=False):
        self.write(4, 0x102)
        self.write(12, self.address)
        self.write(8, size)
        self.write(0, 0x8081 if read else 0x8080)

    def send(self, values):
        self.begin(len(values))
        for value in values:
            self.write(16, value)
        assert self.read(4) & 0x102 == 2

    def read_reg(self, register):
        self.send([register])
        self.begin(2, read=True)
        value = self.read(16) << 8 | self.read(16)
        assert self.read(4) & 0x102 == 2
        return value

    def get(self, prop):
        return qmp(self.monitor, 'qom-get', {'path': '/machine/battery-adc', 'property': prop})

    def set(self, prop, value):
        qmp(self.monitor, 'qom-set', {'path': '/machine/battery-adc', 'property': prop, 'value': value})


def migrate(source, target, root):
    endpoint = 'unix:' + str(root / 'migration')
    qmp(target.monitor, 'migrate-incoming', {'uri': endpoint})
    qmp(source.monitor, 'migrate', {'uri': endpoint})
    deadline = time.monotonic() + 60
    while True:
        state = qmp(source.monitor, 'query-migrate')
        if state.get('status') == 'failed':
            raise RuntimeError(state)
        if (state.get('status') == 'completed' and
                qmp(target.monitor, 'query-status')['status'] != 'inmigrate'):
            return
        if time.monotonic() > deadline:
            raise TimeoutError('Device migration: ' + repr(state))
        time.sleep(0.02)


def exercise(case, source_binary=None):
    if case not in CASES:
        raise ValueError('Unknown ADC migration case: ' + case)
    with tempfile.TemporaryDirectory(prefix='uc-adc-mig-') as temporary, ExitStack() as stack:
        root = Path(temporary)
        source = Machine(root, 'source', stack,
                         binary=source_binary,
                         reference='missing' if case.startswith('reference-') else 'fixed')
        target = Machine(root, 'target', stack, incoming=True,
                         reference='missing' if case == 'reference-missing' else 'fixed')
        clock = 0
        try:
            if source_binary is not None:
                properties = qmp(source.monitor, 'qom-list', {'path': '/machine/battery-adc'})
                assert not any(item['name'] == 'reference-supply-present' for item in properties), \
                    'Legacy acceptance requires the original ADC version-1 model'
            source.set('input-uv', 1650000)
            if case == 'reference-mismatch':
                endpoint = 'unix:' + str(root / 'migration')
                qmp(target.monitor, 'migrate-incoming', {'uri': endpoint})
                qmp(source.monitor, 'migrate', {'uri': endpoint})
                assert target.process.wait(timeout=30) != 0, 'Different profile was accepted'
                error = (root / 'target.log').read_text()
                assert 'adc101c' in error and 'load' in error, error
                print('PASS: ADC migration rejects reference-profile mismatch', flush=True)
                return
            if case in ('acquire', 'convert', 'reference-missing'):
                assert source.read_reg(0) == 0
                clock = 399 if case == 'acquire' else 500
                source.command(f'clock_set {clock}')
            elif case == 'write':
                source.begin(3)
                source.write(16, 3)
                source.write(16, 0x0a)
            elif case == 'read':
                source.send([3, 0x0a, 0xbc])
                source.send([3])
                source.begin(2, read=True)
                assert source.read(16) == 0x0a
            elif case == 'interval':
                source.send([4, 0x04, 0])
                source.send([2, 0x2c])
                clock = 1400
                source.command(f'clock_set {clock}')
                assert source.get('conversion-word') == 0x8800
            elif case == 'unpowered':
                source.set('powered', False)
            if clock:
                target.command(f'clock_set {clock}')
            migrate(source, target, root)
            assert target.get('input-uv') == 1650000
            if case in ('acquire', 'convert', 'reference-missing'):
                assert target.get('reference-supply-present') == (case != 'reference-missing')
                assert target.get('conversion-word') == 0
                if case == 'acquire':
                    target.command('clock_step 1')
                target.set('input-uv', 3300000)
                target.command('clock_set 1399')
                assert target.get('conversion-word') == 0
                target.command('clock_step 1')
                assert target.get('conversion-word') == 0x800
            elif case == 'write':
                target.write(16, 0xbc)
                assert target.read(4) & 0x102 == 2
                assert target.read_reg(3) == 0xabc
            elif case == 'read':
                assert target.read(16) == 0xbc
                assert target.read(4) & 0x102 == 2
            elif case == 'interval':
                assert target.get('conversion-word') == 0x8800
                assert target.get('alert-enabled') and not target.get('alert-level')
                assert target.read_reg(6) == target.read_reg(7) == 0x800
                target.set('input-uv', 0)
                target.command('clock_set 33400')
                assert target.read_reg(6) == 0 and target.read_reg(7) == 0x800
            elif case == 'unpowered':
                assert not target.get('powered')
                target.begin(1)
                assert target.read(4) & 0x103 == 0x102
            print('PASS: ADC migration ' + case, flush=True)
            if source_binary is not None:
                print('PASS: legacy ADC version-1 fixed-reference conversion restored', flush=True)
        except BaseException:
            for name in ('source', 'target'):
                print((root / (name + '.log')).read_text())
            raise


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--v1-source', type=Path,
                     help='Also test a retained ADC v1 / PMIC v3 binary as migration source')
    args = cli.parse_args()
    for case in CASES:
        exercise(case)
    if args.v1_source:
        exercise('convert', args.v1_source.resolve())
