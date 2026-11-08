#!/usr/bin/env python3
"""Real BCM2711 GENET rings, packet backend, RAM-only DMA, IRQ/reset/migration.

Uses a private socketpair: no physical interface or external network access.
"""
import argparse
from contextlib import ExitStack
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
import zlib

from test_emulator_adc_migration import migrate
from test_emulator_pmic import connect
from test_emulator_pi4_memory import sparse_ram
from uconsole_emulator import executable, qmp

BASE = 0xfd580000
PACKET = bytes.fromhex('525400123456525400abcdef88b5') + bytes(range(100))


class Machine:
    def __init__(self, root, name, stack, binary, incoming=False, phy_address=1, backend=True):
        self.monitor = root / (name + '-qmp')
        test = root / (name + '-test')
        self.wire, peer = socket.socketpair()
        stack.callback(self.wire.close)
        self.wire.settimeout(5)
        log = stack.enter_context((root / (name + '.log')).open('wb'))
        network = (['-netdev', f'socket,id=wire,fd={peer.fileno()}',
                    '-global', 'bcm2838-genet.netdev=wire'] if backend else [])
        self.process = subprocess.Popen([
            binary, '-M', 'raspi4b-8g,memory-backend=test-memory', *sparse_ram(8),
            '-accel', 'qtest', '-S', '-display', 'none',
            '-serial', 'none', '-monitor', 'none', '-global',
            'bcm2838-peripherals.genet-network=on',
            *network, '-nic', 'none',
            '-global', f'bcm2838-genet.phy-addr={phy_address}',
            '-global', 'bcm2838-genet.mac=52:54:00:12:34:56',
            '-qtest', f'unix:{test},server=on,wait=off',
            '-qmp', f'unix:{self.monitor},server=on,wait=off',
            *(['-incoming', 'defer'] if incoming else [])],
            stdout=log, stderr=log, pass_fds=((peer.fileno(),) if backend else ()))
        peer.close()
        stack.callback(self.close)
        try:
            self.sock = stack.enter_context(connect(test, self.process))
        except Exception as exc:
            raise RuntimeError(Path(log.name).read_text()) from exc
        self.stream = stack.enter_context(self.sock.makefile('rwb', buffering=0))
        if not incoming:
            qmp(self.monitor, 'cont')

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

    def read(self, offset):
        return int(self.command(f'readl {BASE + offset:#x}').split()[1], 16)

    def write(self, offset, value):
        self.command(f'writel {BASE + offset:#x} {value:#x}')

    def memory(self, address, data):
        self.command(f'write {address:#x} {len(data):#x} 0x{data.hex()}')

    def inspect(self, address, length):
        return bytes.fromhex(self.command(f'read {address:#x} {length:#x}').split()[1][2:])

    def irq(self, bank=0):
        return bool(int(self.command('readl 0xff841214').split()[1], 16) & (1 << (29 + bank)))

    def receive_wire(self):
        def exact(size):
            result = b''
            while len(result) < size:
                part = self.wire.recv(size - len(result))
                assert part, 'backend disconnected'
                result += part
            return result
        length = struct.unpack('!I', exact(4))[0]
        assert length <= 65536
        return exact(length)

    def send_wire(self, packet):
        self.wire.sendall(struct.pack('!I', len(packet)) + packet)


def mdio(m, reg, value=None, address=1):
    m.write(0xe14, (1 << 29) | (address << 21) | (reg << 16) |
            ((2 if value is None else 1) << 26) | (value or 0))
    result = m.read(0xe14)
    assert not result & (1 << 29)
    return result


def setup(m, ring=16, count=4, tx_base=0x110000000, rx_base=0x120000000):
    m.write(0x808, 3)
    m.write(0x300, 3)  # 64-byte status + 2-byte alignment
    m.write(0x600, 1)  # transmit status block
    for block in (0x200, 0x240):
        m.write(block + 8, 0xffffffff)
        m.write(block + 0x10, 0xffffffff)
    for dma, base in ((0x2000, rx_base), (0x4000, tx_base)):
        registers = dma + 0xc00 + ring * 0x40
        for offset, value in ((0, 0), (4, 0), (8, 0), (12, 0),
                              (16, (count << 16) | 2048), (20, 0), (24, 0),
                              (28, count * 3 - 1), (32, 0)):
            m.write(registers + offset, value)
        m.write(dma + 0x1040, 1 << ring)
        m.write(dma + 0x1044, 1 | (1 << (ring + 1)))
        for i in range(count):
            address = base + i * 4096
            m.write(dma + i * 12 + 4, address & 0xffffffff)
            m.write(dma + i * 12 + 8, address >> 32)
    return 0x2c00 + ring * 0x40, 0x4c00 + ring * 0x40


def wait_index(m, register, value):
    deadline = time.monotonic() + 5
    while m.read(register) & 0xffff != value:
        if time.monotonic() >= deadline:
            raise TimeoutError(f'GENET register {register:#x}: {m.read(register):#x}, expected{value}')


def run(binary, root):
    with ExitStack() as stack:
        m = Machine(root, 'source', stack, binary)
        assert m.read(0) == 0x06000000
        assert mdio(m, 2) & 0xffff == 0x600d
        assert mdio(m, 3) & 0xffff == 0x84a2
        assert mdio(m, 2, address=0) & 0x1000ffff == 0x1000ffff
        assert mdio(m, 1) & 0x24 == 0x24
        assert mdio(m, 25) & 0x8704 == 0x8704
        mdio(m, 0, 0x8000)
        assert not mdio(m, 0) & 0x8000
        mdio(m, 2, 0xffff)
        assert mdio(m, 2) & 0xffff == 0x600d
        qmp(m.monitor, 'set_link', {'name': 'wire', 'up': False})
        assert not mdio(m, 1) & 4
        assert mdio(m, 25) & 0x8704 == 0
        mdio(m, 25, 0xffff)
        assert mdio(m, 25) & 0x8704 == 0
        qmp(m.monitor, 'set_link', {'name': 'wire', 'up': True})
        assert mdio(m, 1) & 4
        print('PASS: real backend MAC/PHY and link transitions')
        for ring in (16, 0):
            rx, tx = setup(m, ring)
            bank, block = (0, 0x200) if ring == 16 else (1, 0x240)
            txbit, rxbit = ((1 << 16), (1 << 13)) if ring == 16 else (1, (1 << 16))
            for index in range(300):
                slot = index % 4
                packet = PACKET + struct.pack('!I', index)
                m.memory(0x110000000 + slot * 4096, bytes(64) + packet)
                m.write(0x4000 + slot * 12, ((64 + len(packet)) << 16) | 0x6000)
                m.write(tx + 12, (index + 1) & 0xffff)
                assert m.receive_wire() == packet
                assert m.read(tx + 8) & 0xffff == index + 1
                assert m.read(block) & txbit
                assert not m.irq(bank), 'masked IRQ asserted'
                m.write(block + 0x14, txbit)
                assert m.irq(bank)
                m.write(block + 8, txbit)
                assert not m.irq(bank)
                m.write(block + 0x10, txbit)
                m.memory(0x120000000 + slot * 4096, b'\xa5' * 256)
                m.send_wire(packet)
                wait_index(m, rx + 8, index + 1)
                data = m.inspect(0x120000000 + slot * 4096, 256)
                assert data[66:66 + len(packet)] == packet
                assert data[66 + len(packet):] == b'\xa5' * (190 - len(packet))
                assert struct.unpack('<I', data[:4])[0] >> 16 == 66 + len(packet)
                assert m.read(block) & rxbit
                m.write(block + 8, rxbit)
                m.write(rx + 12, index + 1)
            print(f'PASS: ring{ring} 300 real TX/RX frames, high RAM DMA, wrap, IRQ masks/W1C')
        # Real data paths also honor disabled status blocks/alignment and FCS.
        for control, crc in ((0, False), (2, False), (3, True)):
            rx, tx = setup(m, tx_base=0x11000000, rx_base=0x12000000)
            m.write(0x300, control)
            m.write(0x600, 0)
            m.write(0x808, 3 | ((1 << 6) if crc else 0))
            m.memory(0x11000000, PACKET)
            m.write(0x4000, (len(PACKET) << 16) | 0x6000)
            m.write(tx + 12, 1)
            assert m.receive_wire() == PACKET
            m.memory(0x12000000, b'\xa5' * 256)
            m.send_wire(PACKET)
            wait_index(m, rx + 8, 1)
            offset = (64 if control & 1 else 0) + (2 if control & 2 else 0)
            expected = PACKET + (struct.pack('<I', zlib.crc32(PACKET)) if crc else b'')
            assert m.inspect(0x12000000 + offset, len(expected)) == expected
            assert m.inspect(0x12000000 + offset + len(expected), 1) == b'\xa5'
        print('PASS: low RAM, enabled/disabled status/alignment and real Ethernet FCS')
        # Native 16-bit producer/consumer rollover, and a two-descriptor frame.
        rx, tx = setup(m)
        m.write(tx + 8, 0xffff)
        m.write(tx + 12, 0xffff)
        m.memory(0x110000000, bytes(64) + PACKET[:30])
        m.memory(0x110001000, PACKET[30:])
        m.write(0x4000, ((64 + 30) << 16) | 0x2000)
        m.write(0x400c, ((len(PACKET) - 30) << 16) | 0x4000)
        m.write(tx + 12, 1)
        assert m.receive_wire() == PACKET
        assert m.read(tx + 8) & 0xffff == 1
        print('PASS: split-descriptor packet and 16-bit counter rollover')
        # Invalid ring geometry/zero-buffer/full ring must preserve RAM/indices.
        for badend in (0xffffffff, 1, 1023):
            m.write(tx + 28, badend)
            before = m.read(tx + 8)
            m.write(tx + 12, 2)
            assert m.read(tx + 8) == before
        rx, tx = setup(m, count=1)
        m.send_wire(PACKET)
        wait_index(m, rx + 8, 1)
        before = m.inspect(0x120000000, 256)
        m.send_wire(PACKET + b'new')
        deadline = time.monotonic() + 5
        while not m.read(rx + 8) >> 16:
            assert time.monotonic() < deadline
        assert m.inspect(0x120000000, 256) == before
        assert m.read(rx + 8) & 0xffff == 1
        rx, tx = setup(m)
        m.write(rx + 16, 4 << 16)
        m.memory(0x120000000, b'\xa5' * 256)
        m.send_wire(PACKET)
        deadline = time.monotonic() + 5
        while not m.read(rx + 8) >> 16:
            assert time.monotonic() < deadline
        assert m.read(rx + 8) & 0xffff == 0
        assert m.inspect(0x120000000, 256) == b'\xa5' * 256
        print('PASS: malformed geometry, full RX ring preserves guest ownership')
        # Reject the complete peripheral hole and addresses beyond board RAM.
        for invalid in (0xfc000000, 0xfe200000, 0xfffff000, 0x200000000):
            rx, tx = setup(m)
            gpio_before = m.command('readl 0xfe200000')
            m.write(0x4004, invalid & 0xffffffff)
            m.write(0x4008, invalid >> 32)
            m.write(0x4000, ((64 + len(PACKET)) << 16) | 0x6000)
            m.write(tx + 12, 1)
            assert m.read(tx + 8) == 0 and not m.read(0x200) & (1 << 16)
            m.write(0x2004, invalid & 0xffffffff)
            m.write(0x2008, invalid >> 32)
            m.send_wire(PACKET)
            deadline = time.monotonic() + 5
            while not m.read(0x2000) & 4:
                assert time.monotonic() < deadline
            assert m.read(rx + 8) == 0 and not m.read(0x200) & (1 << 13)
            assert m.command('readl 0xfe200000') == gpio_before
        print('PASS: full peripheral-hole/out-of-RAM DMA fails without MMIO side effects/completion')
        # Pending native IRQ migrates; next actual packet completes on target.
        rx, tx = setup(m)
        m.write(0x214, (1 << 13))
        m.send_wire(PACKET)
        wait_index(m, rx + 8, 1)
        assert m.irq()
        target = Machine(root, 'target', stack, binary, incoming=True)
        migrate(m, target, root)
        assert target.irq() and target.inspect(0x120000000 + 66, len(PACKET)) == PACKET
        # -S on the destination intentionally leaves it paused after incoming.
        # Socket backend delivery, unlike qtest MMIO, needs RUNNING state.
        if qmp(target.monitor, 'query-status')['status'] != 'running':
            qmp(target.monitor, 'cont')
        target.write(0x208, 1 << 13)
        target.write(rx + 12, 1)
        target.send_wire(PACKET + b'after-migration')
        wait_index(target, rx + 8, 2)
        assert target.inspect(0x120001000 + 66, len(PACKET) + 15) == PACKET + b'after-migration'
        assert target.irq()
        target.write(0x808, 1 << 13)
        assert not target.irq() and target.read(rx + 8) == 0
        setup(target)
        target.send_wire(PACKET)
        wait_index(target, rx + 8, 1)
        print('PASS: live descriptor/IRQ migration, next packet, reset and recovery')
        # CM4 has PHY0; Pi4B has PHY1. This is an explicit immutable strap.
        zero = Machine(root, 'phy-zero', stack, binary, phy_address=0)
        assert mdio(zero, 2, address=0) & 0xffff == 0x600d
        assert mdio(zero, 2, address=1) & 0x1000ffff == 0x1000ffff
        mdio(zero, 0, 0x8000, address=0)
        assert not mdio(zero, 0, address=0) & 0x8000
        zero_target = Machine(root, 'phy-zero-target', stack, binary, incoming=True, phy_address=0)
        (root / "strap-migration").mkdir()
        migrate(zero, zero_target, root / "strap-migration")
        assert mdio(zero_target, 3, address=0) & 0xffff == 0x84a2
        assert mdio(zero_target, 2, address=1) & 0x1000ffff == 0x1000ffff
        mismatch = Machine(root, 'phy-mismatch', stack, binary, incoming=True, phy_address=1)
        endpoint = 'unix:' + str(root / 'mismatch-migration')
        qmp(mismatch.monitor, 'migrate-incoming', {'uri': endpoint})
        qmp(zero_target.monitor, 'migrate', {'uri': endpoint})
        assert mismatch.process.wait(timeout=30) != 0
        error = (root / 'phy-mismatch.log').read_text()
        assert 'phy_addr' in error or 'GENET PHY strap differs' in error, error
        unconnected = Machine(root, 'no-backend', stack, binary, backend=False)
        assert not mdio(unconnected, 1) & 4
        assert not mdio(unconnected, 25) & 4
        invalid = subprocess.run([
            binary, '-M', 'raspi4b', '-accel', 'qtest', '-display', 'none',
            '-serial', 'none', '-monitor', 'none', '-nic', 'none',
            '-global', 'bcm2838-peripherals.genet-network=on',
            '-global', 'bcm2838-genet.phy-addr=32'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        assert invalid.returncode != 0 and b'phy-addr' in invalid.stdout
        print('PASS: PHY0/1 straps, absent PHY/no-backend, matching migration and mismatch rejection')



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu-system')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    binary = args.qemu_system or str(executable('qemu-system-aarch64'))
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
        run(binary, args.output)
    else:
        with tempfile.TemporaryDirectory(prefix='uc-genet-network-') as temporary:
            run(binary, Path(temporary))


if __name__ == '__main__':
    main()
