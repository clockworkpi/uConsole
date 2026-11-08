#!/usr/bin/env python3
"""GENET disconnected control profile: actual CPU abort, MDIO, no DMA, IRQ and migration."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import subprocess
import tempfile
import time

from test_emulator_adc_migration import Machine, migrate
from test_emulator_pmic import connect
from uconsole_emulator import executable, qmp

BASE = 0xfd580000
MDIO = 0xe14
BUSY = 1 << 29
DONE = 1 << 23
ERROR = 1 << 24


def irq(machine, bank):
    # GIC SPIs157/158 correspond to architectural interrupt189/190.
    value = int(machine.command('readl 0xff841214').split()[1], 16)
    return bool(value & (1 << (29 + bank)))


def mdio(machine, reg, value=None, address=1):
    command = BUSY | (address << 21) | (reg << 16)
    command |= (2 if value is None else 1) << 26
    machine.write(MDIO, command | (0 if value is None else value))
    result = machine.read(MDIO)
    assert not result & BUSY
    return result


def firmware_reset(machine):
    # EDK2 GenetReset and station MAC setup.
    machine.write(8, 2)
    assert machine.read(8) == 2
    machine.write(8, 0)
    machine.write(0x808, 0)
    machine.write(0x808, 0xa000)
    assert machine.read(0x808) == 0x0040e000
    machine.write(0x808, 0)
    machine.write(0xd80, 7)
    machine.write(0xd80, 0)
    machine.write(0x814, 1536)
    machine.write(0x300, 2)
    machine.write(0x3b4, 1)
    machine.write(0x80c, 0x02000000)
    machine.write(0x810, 0x1234)
    assert machine.read(0x80c) == 0x02000000
    assert machine.read(0x810) == 0x1234


def cpu_mmio_contract(binary):
    # Original AArch64 EL3 payload, assembled once and embedded to avoid a
    # cross-assembler dependency in packaged macOS/Linux tests:
    # mov x1,#0x800; msr vbar_el3,x1; isb; mov x0,#offset;
    # movk x0,#0xfd58,lsl#16; ldr w2,[x0]; mov x3,#0xdead;
    # mov x5,#0x4000; str x3,[x5]; wfi; b .-4
    # Vectors at0x800/current-SP0 and0xa00/current-SPx branch to0x1000:
    # mrs x3,esr_el3; mrs x4,far_el3; mov x5,#0x4000;
    # str x4,[x5,#8]; str x3,[x5]; wfi; b .-4
    for offset, fault in ((8, False), (0x314, False), (0x304, True), (0x1800, True)):
        with tempfile.TemporaryDirectory(prefix='uc-genet-cpu-') as temporary:
            root = Path(temporary)
            with (root / 'qemu.log').open('wb') as log:
                process = subprocess.Popen([
                    binary, '-M', 'raspi4b', '-accel', 'tcg', '-S',
                    '-display', 'none', '-serial', 'none', '-monitor', 'none',
                    '-qtest', f'unix:{root}/test,server=on,wait=off',
                    '-qmp', f'unix:{root}/qmp,server=on,wait=off',
                ], stdout=log, stderr=log)
                try:
                    with connect(root / 'test', process) as sock:
                        stream = sock.makefile('rwb', buffering=0)

                        def command(text):
                            stream.write((text + '\n').encode())
                            while True:
                                result = stream.readline().decode().strip()
                                if result.startswith('IRQ '):
                                    continue
                                assert result.startswith('OK'), result
                                return result

                        program = bytearray(0x1020)
                        main = bytes.fromhex('010081d201c01ed5df3f03d5000083d200abbff2020040b9a3d59bd2050088d2a30000f97f2003d5ffffff17')
                        program[:len(main)] = main
                        program[12:16] = (0xd2800000 | (offset << 5)).to_bytes(4, 'little')
                        program[0x800:0x804] = bytes.fromhex('00020014')
                        program[0xa00:0xa04] = bytes.fromhex('80010014')
                        handler = bytes.fromhex('03523ed504603ed5050088d2a40400f9a30000f97f2003d5ffffff17')
                        program[0x1000:0x1000 + len(handler)] = handler
                        command(f'write 0 {len(program):#x} 0x{program.hex()}')
                        qmp(root / 'qmp', 'cont')
                        deadline = time.monotonic() + 10
                        while True:
                            esr = int(command('readq 0x4000').split()[1], 16)
                            if esr:
                                break
                            if process.poll() is not None or time.monotonic() >= deadline:
                                raise TimeoutError('AArch64 MMIO probe did not report: ' + (root / 'qemu.log').read_text())
                            time.sleep(0.01)
                        far = int(command('readq 0x4008').split()[1], 16)
                        if fault:
                            # EA bit9 encodes the external-abort type and can differ
                            # between EL3 and the firmware EL2 exception route.
                            assert esr & ~0x200 == 0x96000010 and far == BASE + offset, (hex(esr), hex(far))
                        else:
                            assert esr == 0xdead, (hex(esr), hex(far))
                    qmp(root / 'qmp', 'quit')
                    assert process.wait(timeout=10) == 0
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=10)
        print('PASS: actual AArch64 CPU GENET ' + ('unsupported MMIO external abort' if fault else 'mapped MMIO control'))


def run(binary):
    with tempfile.TemporaryDirectory(prefix='uc-genet-') as temporary, ExitStack() as stack:
        root = Path(temporary)
        source = Machine(root, 'source', stack, base=BASE, binary=binary)
        target = Machine(root, 'target', stack, incoming=True, base=BASE, binary=binary)
        assert source.read(0) == 0x06000000
        firmware_reset(source)
        # PHY1 is present; all other addresses are absent. Link and remote
        # autonegotiation cannot be fabricated by writing status/partner state.
        assert mdio(source, 2) & 0xffff == 0x600d
        assert mdio(source, 3) & 0xffff == 0x84a0
        assert mdio(source, 2, address=0) & 0x1000ffff == 0x1000ffff
        source.write(0x208, 0xffffffff)
        source.write(0x214, DONE | ERROR)
        assert not irq(source, 0)
        mdio(source, 0, 0x8000)
        assert not mdio(source, 0) & 0x8000
        assert irq(source, 0) and source.read(0x200) & DONE
        source.write(0x208, DONE)
        assert not irq(source, 0)
        mdio(source, 1, 0xffff)
        mdio(source, 5, 0xffff)
        assert not mdio(source, 1) & 0x24
        assert mdio(source, 5) & 0xffff == 0
        mdio(source, 0, 0x1340)
        assert not mdio(source, 1) & 0x24
        # Broadcom AUX/clock-shadow selectors used by the firmware.
        mdio(source, 0x18, 0x8207)
        mdio(source, 0x18, 0x7007)
        assert mdio(source, 0x18) & 0x7fff == 0x0207
        mdio(source, 0x1c, 0x8e00)
        mdio(source, 0x1c, 0x0c00)
        assert mdio(source, 0x1c) & 0xffff == 0x0e00
        source.write(0x08c, 0x50)
        assert not source.read(0x08c) & 0x10
        # Both software interrupt banks, including mask and W1C through GIC.
        for bank, base in enumerate((0x200, 0x240)):
            source.write(base + 8, 0xffffffff)
            source.write(base + 0x14, 1)
            source.write(base + 4, 1)
            assert irq(source, bank)
            source.write(base, 0)
            assert source.read(base) & 1
            source.write(base + 0x10, 1)
            assert not irq(source, bank)
            source.write(base + 0x14, 1)
            assert irq(source, bank)
        # Program descriptor SRAM and default queue16 as firmware does, with
        # deliberately invalid packet addresses. There must be no guest-memory
        # DMA access, descriptor consumption or invented RX/TX completion.
        source.command('writel 0x10000 0xdecafbad')
        for base in (0x2000, 0x4000):
            source.write(base, 0x0040e000)  # 64-byte owned SOP/EOP descriptor.
            source.write(base + 4, 0x10000)
            source.write(base + 8, 0xffffffff)
            source.write(base + 0x1008, 0)
            source.write(base + 0x100c, 0)
            source.write(base + 0x1040, 1 << 16)
            source.write(base + 0x1044, 0x20001)
            assert source.read(base + 0x1048) == 0
        source.write(0x808, 3)
        source.command('clock_step 1000000000')
        assert int(source.command('readl 0x10000').split()[1], 16) == 0xdecafbad
        for base in (0x2000, 0x4000):
            assert source.read(base) == 0x0040e000
            assert source.read(base + 0x1008) == source.read(base + 0x100c) == 0
        assert source.read(0x200) & ((1 << 16) | (1 << 13)) == 0
        # Preserve selector, MAC, ring/descriptor and asserted IRQ state.
        offsets = (8, 0x80c, 0x810, 0x814, 0x200, 0x20c, 0x240, 0x24c,
                   0x2000, 0x2004, 0x4000, 0x4008, 0x3044, 0x5044, 0xe14)
        expected = [source.read(off) for off in offsets]
        target.command('clock_set 1000000000')
        migrate(source, target, root)
        assert [target.read(off) for off in offsets] == expected
        assert irq(target, 0) and irq(target, 1)
        assert mdio(target, 0x1c) & 0xffff == 0x0e00
        target.write(0x248, 1)
        assert not irq(target, 1)
        target.write(0x808, 0x2000)
        assert target.read(0x80c) == target.read(0x810) == 0
        assert target.read(0x2000) == 0x0040e000  # MAC reset does not reset DMA SRAM.
        mdio(target, 4, 0x1234)
        assert mdio(target, 4) & 0xffff == 0x1234
        target.write(0x080, 1 << 8)  # EXT_PHY_RESET affects PHY, not DMA SRAM.
        assert mdio(target, 4) & 0xffff == 0x01e1
        assert target.read(0x2000) == 0x0040e000
        target.write(0x080, 0)
        qmp(target.monitor, 'system_reset')
        assert target.read(0x2000) == target.read(0x3044) == 0
        assert target.read(0x20c) == target.read(0x24c) == 0xffffffff
        assert not irq(target, 0) and not irq(target, 1)
        assert not mdio(target, 1) & 0x24
        print('PASS: GENET reset/MAC, MDIO+PHY no-link, GIC IRQ, descriptor SRAM without DMA and real migration')
    cpu_mmio_contract(binary)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', default=executable('qemu-system-aarch64'))
    args = parser.parse_args()
    run(args.qemu)
