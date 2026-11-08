#!/usr/bin/env python3
"""Connected BCM2711 PCIe: real bridge/xHCI config, ring DMA, INTx and migration.

This exercises the optional virtual endpoint profile, not VL805 hardware,
physical PCIe signaling, USB peripherals or Pi 5 RP1.
"""
import argparse
from contextlib import ExitStack
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

from test_emulator_adc_migration import migrate
from test_emulator_pmic import connect
from uconsole_emulator import executable, qmp

PCIE = 0xfd500000
XHCI = 0x600000000
CMD = 0x01000000
ERST = 0x01100000
EVENT = 0x01200000
DCBAA = 0x01300000


class Machine:
    def __init__(self, root, name, stack, binary, incoming=False, generic=False, compact=False,
                 board_name='raspi4b', device_args=(), accel='qtest'):
        self.monitor = root / (name + '-qmp')
        test = root / (name + '-test')
        log = stack.enter_context((root / (name + '.log')).open('wb'))
        self.generic = generic
        self.xbase = 0x10000000 if generic else XHCI
        self.ram_offset = 0x40000000 if generic else 0
        board = (['-nodefaults', '-M', 'virt,highmem=off', '-device',
                  'qemu-xhci,id=host,addr=1.0,msi=off,msix=off' +
                  (',compact-mmio=on' if compact else '')] if generic else
                 ['-M', board_name, '-global', 'bcm2838-pcie-disconnected.connected-xhci=on'])
        self.process = subprocess.Popen([
            binary, *board, *device_args, '-accel', accel, '-S',
            '-display', 'none', '-serial', 'none', '-monitor', 'none', '-nic', 'none',
            '-qtest', f'unix:{test},server=on,wait=off',
            '-qmp', f'unix:{self.monitor},server=on,wait=off',
            *(['-incoming', 'defer'] if incoming else [])], stdout=log, stderr=log)
        stack.callback(self.close)
        sock = stack.enter_context(connect(test, self.process))
        self.sock = sock
        self.stream = stack.enter_context(sock.makefile('rwb', buffering=0))
        self.dma_base = self.ram_offset

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def command(self, text):
        self.sock.sendall((text + '\n').encode())
        while True:
            reply = self.stream.readline().decode().strip()
            if reply.startswith('IRQ '):
                continue
            assert reply.startswith('OK'), reply
            return reply

    def read(self, address, width='l'):
        return int(self.command(f'read{width} {address:#x}').split()[1], 16)

    def write(self, address, value, width='l'):
        self.command(f'write{width} {address:#x} {value:#x}')

    def reg(self, offset, value=None):
        if value is None:
            return self.read(PCIE + offset)
        self.write(PCIE + offset, value)

    def config(self, bus, dev, fn, offset, value=None):
        assert 0 <= offset <= 0xffc and offset % 4 == 0
        if (bus, dev, fn) == (0, 0, 0):
            address = PCIE + offset
        else:
            self.reg(0x9000, (bus << 20) | (dev << 15) | (fn << 12))
            address = PCIE + 0x8000 + offset
        if value is None:
            return self.read(address)
        self.write(address, value)

    def memory(self, address, data):
        self.command(f'write {address + self.ram_offset:#x} {len(data):#x} 0x{data.hex()}')

    def inspect(self, address, length):
        return bytes.fromhex(self.command(f'read {address + self.ram_offset:#x} {length:#x}').split()[1][2:])

    def xread(self, offset):
        return self.read(self.xbase + offset)

    def xwrite(self, offset, value):
        self.write(self.xbase + offset, value)

    def irq(self):
        if self.generic:
            return bool(self.xread(self.runtime + 0x20) & 1)
        # PCI INTx A is native SPI143, architectural interrupt175.
        return bool(self.read(0xff841214) & (1 << 15))


def configure(machine, dma_base=0):
    machine.reg(0x9210, 3)
    machine.reg(0x9210, 1)
    machine.reg(0x4204, 0)
    machine.reg(0x4008, 0x88003000)
    machine.reg(0x4034, dma_base | (0x11 if dma_base == 0 else 0x0f))
    machine.reg(0x4038, 0)
    machine.reg(0x400c, 0xf8000000)
    machine.reg(0x4010, 0)
    machine.reg(0x4070, 0x03f00000)
    machine.reg(0x4080, 6)
    machine.reg(0x4084, 6)
    machine.reg(0x9210, 0)
    assert machine.reg(0x4068) == 0xb0, 'Connected PHY/data link did not become active'
    assert machine.config(0, 0, 0, 0) not in (0, 0xffffffff)
    assert machine.config(0, 0, 0, 8) >> 16 == 0x0604, 'Real PCI bridge class missing'
    machine.config(0, 0, 0, 0x18, 0x00010100)
    machine.config(0, 0, 0, 0x20, 0xf800f800)
    machine.config(0, 0, 0, 4, 6)
    assert machine.config(1, 0, 0, 0) not in (0, 0xffffffff), 'Real xHCI PCI function missing'
    assert machine.config(1, 0, 0, 8) >> 8 == 0x0c0330, 'Endpoint is not an xHCI class function'
    # BAR sizing is performed by the actual PCI device, not stored echoes.
    machine.config(1, 0, 0, 0x10, 0xffffffff)
    bar_mask = machine.config(1, 0, 0, 0x10)
    assert bar_mask & ~0xf == 0xfffff000 and bar_mask & 6 == 4, hex(bar_mask)
    machine.config(1, 0, 0, 0x10, 0xf8000000)
    machine.config(1, 0, 0, 0x14, 0)
    machine.config(1, 0, 0, 4, 6)  # MEM + bus master; MSI/MSI-X remain disabled.
    for bdf in ((1, 1, 0), (1, 0, 1), (2, 0, 0)):
        assert machine.config(*bdf, 0) == 0xffffffff, ('Absent BDF responded', bdf)
    assert machine.xread(0) == 0x01000040, 'Outbound bridge/window/BAR did not reach real xHCI'
    geometry(machine, compact=True)
    machine.config(0, 0, 0, 4, 4)  # Bridge memory forwarding disabled.
    assert machine.xread(0) != 0x01000040
    machine.config(0, 0, 0, 4, 6)
    assert machine.xread(0) == 0x01000040
    assert not machine.irq()
    machine.dma_base = dma_base


def geometry(machine, compact):
    """All advertised banks must fit the actual aperture, including last entries."""
    size = 0x1000 if compact else 0x4000
    params = machine.xread(4)
    slots, interrupts, ports = params & 255, (params >> 8) & 0x7ff, params >> 24
    oper = machine.xread(0) & 255
    runtime = machine.xread(0x18) & ~0x1f
    doorbell = machine.xread(0x14) & ~3
    assert (runtime, doorbell) == ((0x800, 0xc00) if compact else (0x1000, 0x2000))
    assert slots > 0 and interrupts > 0 and ports > 0
    assert oper + 0x400 + ports * 16 <= runtime
    assert runtime + 0x20 + interrupts * 0x20 <= doorbell
    assert doorbell + (slots + 1) * 4 <= size
    # Last interrupter's real moderation register is writable, not just a cap.
    last = runtime + 0x20 + (interrupts - 1) * 0x20 + 4
    machine.xwrite(last, 0x1234)
    assert machine.xread(last) & 0xffff == 0x1234
    machine.xwrite(last, 0)


def rings(machine):
    machine.memory(CMD, bytes(4096))
    machine.memory(ERST, struct.pack('<QII', machine.dma_base + EVENT, 64, 0))
    machine.memory(EVENT, bytes(1024))
    machine.memory(DCBAA, bytes(2048))
    machine.oper = machine.xread(0) & 0xff
    machine.runtime = machine.xread(0x18) & ~0x1f
    machine.doorbell = machine.xread(0x14) & ~3
    # Host-controller reset also verifies writes reach the endpoint's behavior.
    machine.xwrite(machine.oper, 2)
    assert machine.xread(machine.oper + 4) & 1
    command = machine.dma_base + CMD
    machine.xwrite(machine.oper + 0x18, (command | 1) & 0xffffffff)
    machine.xwrite(machine.oper + 0x1c, command >> 32)
    machine.xwrite(machine.oper + 0x30, (machine.dma_base + DCBAA) & 0xffffffff)
    machine.xwrite(machine.oper + 0x34, (machine.dma_base + DCBAA) >> 32)
    machine.xwrite(machine.oper + 0x38, 1)
    intr = machine.runtime + 0x20
    machine.xwrite(intr + 0x08, 1)
    machine.xwrite(intr + 0x10, (machine.dma_base + ERST) & 0xffffffff)
    machine.xwrite(intr + 0x14, (machine.dma_base + ERST) >> 32)
    machine.xwrite(intr + 0x18, (machine.dma_base + EVENT) & 0xffffffff)
    machine.xwrite(intr + 0x1c, (machine.dma_base + EVENT) >> 32)
    machine.xwrite(intr, 2)
    machine.xwrite(machine.oper, 5)  # Run/stop + interrupt enable.
    assert not machine.xread(machine.oper + 4) & 1


def noop(machine, number):
    command = CMD + number * 16
    machine.memory(command, struct.pack('<QII', 0, 0, (23 << 10) | 1))
    machine.xwrite(machine.doorbell, 0)
    pointer, status, control = struct.unpack('<QII', machine.inspect(EVENT + number * 16, 16))
    assert pointer == machine.dma_base + command, (hex(pointer), hex(command))
    assert status >> 24 == 1, ('NoOp completion was not successful', hex(status))
    assert (control >> 10) & 63 == 33 and control & 1, hex(control)
    assert machine.irq(), 'Real xHCI command completion did not reach native GIC INTx A'


def acknowledge(machine, next_event):
    intr = machine.runtime + 0x20
    pointer = machine.dma_base + EVENT + next_event * 16
    machine.xwrite(intr + 0x18, (pointer | 8) & 0xffffffff)
    machine.xwrite(intr + 0x1c, pointer >> 32)
    machine.xwrite(intr, 3)  # IE plus write-one-clear pending.
    machine.xwrite(machine.oper + 4, 8)
    assert not machine.irq(), 'Acknowledging actual xHCI pending state did not lower INTx'


def dma_gates(machine):
    # An initialized command ring must remain unreadable while the inbound
    # window is disabled, even though CPU access to the endpoint still works.
    rings(machine)
    machine.memory(CMD, struct.pack('<QII', 0, 0, (23 << 10) | 1))
    machine.reg(0x4034, 0)
    machine.xwrite(machine.doorbell, 0)
    assert machine.inspect(EVENT, 16) == bytes(16), 'Disabled inbound BAR2 still allowed endpoint DMA'
    assert not machine.irq()
    machine.reg(0x4034, machine.dma_base | (0x11 if machine.dma_base == 0 else 0x0f))
    # The endpoint's actual PCI bus-master enable must gate DMA independently.
    rings(machine)
    machine.memory(CMD, struct.pack('<QII', 0, 0, (23 << 10) | 1))
    machine.config(1, 0, 0, 4, 2)
    machine.xwrite(machine.doorbell, 0)
    assert machine.inspect(EVENT, 16) == bytes(16), 'PCI bus-master disable did not gate ring DMA'
    assert not machine.irq()
    machine.config(1, 0, 0, 4, 6)


def exercise(binary, root, stack):
    source = Machine(root, 'source', stack, binary)
    target = Machine(root, 'target', stack, binary, incoming=True)
    # Deliberately nonidentity DMA: PCI c0000000 maps ARM RAM0. Successful
    # ring fetches/event writes therefore prove BAR2 translation as well.
    configure(source, dma_base=0xc0000000)
    dma_gates(source)
    rings(source)
    noop(source, 0)
    migrate(source, target, root)
    target.dma_base = source.dma_base
    target.oper, target.runtime, target.doorbell = source.oper, source.runtime, source.doorbell
    assert target.xread(0) == 0x01000040
    assert target.irq(), 'Pending endpoint INTx was lost across migration'
    assert target.inspect(EVENT, 16) == source.inspect(EVENT, 16)
    acknowledge(target, 1)
    noop(target, 1)
    acknowledge(target, 2)
    # PERST gates CPU/device windows and resets endpoint configuration/state.
    target.reg(0x9210, 1)
    assert target.reg(0x4068) == 0x80
    assert target.xread(0) != 0x01000040
    assert not target.irq()
    configure(target)
    rings(target)
    noop(target, 0)
    acknowledge(target, 1)
    qmp(target.monitor, 'system_reset')
    assert target.reg(0x9210) == 3 and target.reg(0x4068) == 0x80
    assert target.xread(0) != 0x01000040
    assert not target.irq()
    configure(target)
    rings(target)
    noop(target, 0)
    acknowledge(target, 1)
    print('PASS: real PCI bridge/xHCI BAR enumeration, command/event DMA, native INTx/W1C, absent BDF, reset gating and live migration')


def run(binary, output=None):
    with ExitStack() as stack:
        if output is None:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='uc-pcie-xhci-')))
        else:
            output.mkdir(mode=0o700)
            root = output.resolve()
        try:
            exercise(binary, root, stack)
            for compact in (False, True):
                generic = Machine(root, 'generic-' + str(compact), stack, binary,
                                  generic=True, compact=compact)
                config = 0x3f008000  # virt highmem=off ECAM, slot 1 function 0.
                generic.write(config + 0x10, 0xffffffff)
                expected = 0xfffff000 if compact else 0xffffc000
                assert generic.read(config + 0x10) & ~15 == expected
                generic.write(config + 0x10, generic.xbase)
                generic.write(config + 0x14, 0)
                generic.write(config + 4, 6, 'w')
                geometry(generic, compact)
                rings(generic)
                noop(generic, 0)
                acknowledge(generic, 1)
                noop(generic, 1)
                acknowledge(generic, 2)
                qmp(generic.monitor, 'quit')
                assert generic.process.wait(timeout=10) == 0
            invalid = subprocess.run([binary, '-nodefaults', '-M', 'virt', '-display', 'none',
                '-serial', 'none', '-monitor', 'none', '-nic', 'none', '-S', '-device',
                'qemu-xhci,compact-mmio=on,msix=on'], capture_output=True, timeout=10)
            assert invalid.returncode != 0
            assert b'compact-mmio requires msix=off' in invalid.stderr
            print('PASS: compact 4KiB bank bounds, unchanged generic 16KiB/default and opt-in '
                  'ring operation, compact/MSI-X refusal')
        except BaseException:
            for path in sorted(root.glob('*.log')):
                print(f'--- retained diagnostic: {path} ---', file=sys.stderr)
                print(path.read_text(errors='replace')[-16000:], file=sys.stderr)
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', default=executable('qemu-system-aarch64'))
    parser.add_argument('--output', type=Path, help='new private directory retaining all QEMU/migration evidence')
    args = parser.parse_args()
    run(args.qemu, args.output)
