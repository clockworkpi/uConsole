#!/usr/bin/env python3
"""BCM2711 disconnected PCIe controller: firmware reset, no-link, IRQ, migration.

This does not qualify a PCI bus, enumeration, endpoint, DMA or PCIe PHY.
"""
import argparse
from contextlib import ExitStack
from pathlib import Path
import tempfile

from test_emulator_adc_migration import Machine, migrate
from uconsole_emulator import executable, qmp

BASE = 0xfd500000
PENDING = 0xff841214


def irq(machine, n):
    value = int(machine.command(f'readl {PENDING:#x}').split()[1], 16)
    return bool(value & (1 << (19 + n)))


def absent(machine):
    # Absent vendor/device ID and unimplemented config function. No fake root
    # port or endpoint may appear when firmware deasserts resets/programs BARs.
    assert machine.read(0) == 0xffffffff
    machine.write(0x9000, 0x01001000)
    assert machine.read(0x9000) == 0x01001000
    assert machine.read(0x8000) == 0xffffffff
    machine.write(0x8000, 0x12345678)
    assert machine.read(0x8000) == 0xffffffff
    assert machine.read(0x4068) == 0x80  # Root-complex strap, PHY and DL down.
    machine.write(0x4068, 0xb0)
    assert machine.read(0x4068) == 0x80  # Guest cannot invent a connected link.


def firmware_sequence(machine):
    # pftf UEFI v1.53 constructor sequence: reset, inbound/outbound setup, PERST.
    machine.write(0x9210, 3)
    machine.write(0x9210, 1)
    machine.write(0x4204, 0)
    assert machine.read(0x406c) == 0x0320
    machine.write(0x4008, 0x88003000)
    machine.write(0x4034, 0x11)
    machine.write(0x4038, 0)
    machine.write(0x400c, 0xf8000000)
    machine.write(0x4010, 0)
    machine.write(0x4070, 0x63f06000)
    machine.write(0x4080, 6)
    machine.write(0x4084, 6)
    machine.write(0x4314, 0xffffffff)
    machine.write(0x4310, 0xffffffff)
    machine.write(0x9210, 0)
    assert machine.read(0x4034) == 0x11
    assert machine.read(0x400c) == 0xf8000000
    assert machine.read(0x4070) == 0x63f06000
    absent(machine)


def run(binary):
    with tempfile.TemporaryDirectory(prefix='uc-pcie-') as temporary, ExitStack() as stack:
        root = Path(temporary)
        source = Machine(root, 'source', stack, base=BASE, binary=binary)
        target = Machine(root, 'target', stack, incoming=True, base=BASE, binary=binary)
        assert source.read(0x9210) == 3
        assert source.read(0x430c) == source.read(0x450c) == 0xffffffff
        source.write(0x400c, 0x12345678)  # Bridge remains in reset.
        assert source.read(0x400c) == 0
        firmware_sequence(source)
        # Both interrupt banks: software-set, mask/unmask, read-only status and
        # write-one-clear, observed at the real GIC input (SPI 147 and 148).
        for n, base in enumerate((0x4300, 0x4500)):
            source.write(base + 4, 8)
            assert source.read(base) == 8
            assert not irq(source, n)
            source.write(base + 0x14, 8)
            assert irq(source, n)
            source.write(base, 0)  # Status is read-only.
            assert source.read(base) == 8
            source.write(base + 8, 8)
            assert source.read(base) == 0 and not irq(source, n)
            source.write(base + 4, 8)
            source.write(base + 0x10, 8)
            assert not irq(source, n)
            source.write(base + 0x14, 8)
            assert irq(source, n)
        offsets = (0x9210, 0x9000, 0x4034, 0x400c, 0x4070,
                   0x4300, 0x430c, 0x4500, 0x450c)
        expected = [source.read(n) for n in offsets]
        migrate(source, target, root)
        assert [target.read(n) for n in offsets] == expected
        assert irq(target, 0) and irq(target, 1)
        absent(target)
        target.write(0x4308, 8)
        assert not irq(target, 0) and irq(target, 1)
        target.write(0x9210, 2)  # Bridge reset clears registers and IRQ banks.
        assert target.read(0x400c) == target.read(0x9000) == 0
        assert target.read(0x4500) == 0
        assert target.read(0x450c) == 0xffffffff
        assert not irq(target, 0) and not irq(target, 1)
        firmware_sequence(target)
        qmp(target.monitor, 'system_reset')
        assert target.read(0x9210) == 3 and target.read(0x400c) == 0
        print('PASS: PCIe firmware reset/window readback, absent config/link, GIC IRQ, real migration and system reset')
    # Migration while bridge/PERST remain held must not resurrect prior state.
    with tempfile.TemporaryDirectory(prefix='uc-pcie-reset-') as temporary, ExitStack() as stack:
        root = Path(temporary)
        source = Machine(root, 'source', stack, base=BASE, binary=binary)
        target = Machine(root, 'target', stack, incoming=True, base=BASE, binary=binary)
        migrate(source, target, root)
        assert target.read(0x9210) == 3
        assert target.read(0x400c) == 0
        absent(target)
        assert not irq(target, 0) and not irq(target, 1)
        print('PASS: PCIe migration with bridge and PERST held')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', default=executable('qemu-system-aarch64'))
    args = parser.parse_args()
    run(args.qemu)
