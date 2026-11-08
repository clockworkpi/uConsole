#!/usr/bin/env python3
"""RNG200 MMIO, host-backed words, reset and IRQ controls; not entropy certification."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import subprocess
import tempfile

from test_emulator_pmic import connect
from uconsole_emulator import executable, qmp

BASE = 0xfe104000
DEVICE = '/machine/soc/peripherals/rng200'


def migration(binary):
    from test_emulator_adc_migration import Machine, migrate
    for held_reset in (False, True):
        with tempfile.TemporaryDirectory(prefix='uc-rng200-migration-') as tmp, ExitStack() as stack:
            root = Path(tmp)
            source = Machine(root, 'source', stack, base=BASE, binary=binary)
            target = Machine(root, 'target', stack, incoming=True, base=BASE, binary=binary)
            source.write(0x10, 64)
            source.write(0x1c, 1)
            source.write(0, 1)
            source.read(0x20)
            source.read(0x20)
            source.write(0x24, 11 << 8)
            if held_reset:
                source.write(4, 1)
            offsets = (0, 4, 8, 0x0c, 0x10, 0x18, 0x1c, 0x24)
            expected = [source.read(off) for off in offsets]
            migrate(source, target, root)
            assert [target.read(off) for off in offsets] == expected
            # RNG200 SPI 125 is architectural interrupt 157, bit 29 here.
            pending = int(target.command('readl 0xff841210').split()[1], 16)
            assert bool(pending & (1 << 29)) == (not held_reset)
            if held_reset:
                assert target.read(0x20) == 0
                target.write(4, 0)
                assert target.read(0x24) & 0xff == 1
            else:
                target.write(0x18, 0xffffffff)
                pending = int(target.command('readl 0xff841210').split()[1], 16)
                assert not pending & (1 << 29)
                target.read(0x20)
                assert target.read(0x0c) == 96
            print('PASS: RNG200 real migration, reset-held=' + str(held_reset))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', default=executable('qemu-system-aarch64'))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='uc-rng200-') as directory:
        directory = Path(directory)
        with (directory / 'qemu.log').open('wb') as log:
            process = subprocess.Popen([
                args.qemu, '-M', 'raspi4b', '-accel', 'qtest', '-S',
                '-display', 'none', '-serial', 'none', '-monitor', 'none',
                '-qtest', f'unix:{directory}/test,server=on,wait=off',
                '-qmp', f'unix:{directory}/qmp,server=on,wait=off',
            ], stdout=log, stderr=log)
            try:
                with connect(directory / 'test', process) as sock:
                    stream = sock.makefile('rwb', buffering=0)
                    interrupts = {}

                    def command(text):
                        stream.write((text + '\n').encode())
                        while True:
                            line = stream.readline().decode().strip()
                            if line.startswith('IRQ '):
                                _, action, index = line.split()
                                interrupts[int(index)] = action == 'raise'
                                continue
                            assert line.startswith('OK'), line
                            return line

                    def read(offset):
                        return int(command(f'readl {BASE + offset:#x}').split()[1], 16)

                    def write(offset, value):
                        command(f'writel {BASE + offset:#x} {value:#x}')

                    command(f'irq_intercept_out {DEVICE} sysbus-irq')
                    assert read(0x24) & 0xff == 0
                    assert read(0x20) == 0 and read(0x0c) == 0
                    write(0x10, 64)  # Exact UEFI faulting register, now mapped.
                    write(0x1c, 1 << 17)
                    write(0, 1)
                    assert read(0x24) & 0xff == 1
                    assert read(0x18) & (1 << 17)
                    assert interrupts.get(0)
                    write(0x18, 1 << 17)
                    assert not read(0x18) and not interrupts.get(0)
                    write(0x1c, 1)
                    words = [read(0x20) for _ in range(8)]
                    assert len(set(words)) > 1, 'constant output is not a functional random source'
                    assert read(0x0c) == 256
                    assert read(0x18) & 1 and interrupts.get(0)
                    write(0x18, 1)
                    assert read(0x18) == 0 and not interrupts.get(0)
                    write(0x24, 7 << 8)
                    assert read(0x24) == (7 << 8) | 1
                    for reset in (4, 8):
                        write(reset, 1)
                        assert read(reset) == 1
                        assert read(0x24) & 0xff == 0
                        assert read(0x20) == 0 and read(0x0c) == 0
                        write(reset, 0)
                        assert read(0x24) & 0xff == 1
                    write(0, 0)
                    assert read(0x24) & 0xff == 0 and read(0x20) == 0
                    qmp(directory / 'qmp', 'system_reset')
                    for offset in (0, 4, 8, 0x0c, 0x10, 0x18, 0x1c, 0x24):
                        assert read(offset) == 0, hex(offset)
                    assert not interrupts.get(0)
                    print('PASS: RNG200 enable/disable, host random words, counters, W1C, IRQ, both resets and system reset')
                qmp(directory / 'qmp', 'quit')
                assert process.wait(timeout=10) == 0
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
    migration(args.qemu)


if __name__ == '__main__':
    main()
