#!/usr/bin/env python3
"""Actual xHCI USB mass-storage bulk transfer completion contract."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import struct
import tempfile
import time

from test_emulator_adc_migration import migrate
from test_emulator_pcie_xhci import Machine, rings, CMD, EVENT, DCBAA, acknowledge
from uconsole_emulator import executable, qmp

INPUT = 0x01400000
OUTPUT = 0x01401000
EP0 = 0x01500000
BULK_OUT = 0x01510000
BULK_IN = 0x01520000
CBW = 0x01600000
DATA = 0x01700000


def trb(pointer, length, kind, flags=0):
    return struct.pack('<QII', pointer, length, (kind << 10) | flags | 1)


class Storage:
    def __init__(self, machine):
        self.m = machine
        self.event = self.command = 0
        self.ep0 = self.out = self.ins = 0
        self.controls = []
        self.ack = True

    def put(self, address, data):
        self.m.memory(address, data)

    def address(self, offset):
        return self.m.dma_base + offset

    def events(self, timeout=2, minimum=1):
        found = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.m.inspect(EVENT + self.event * 16, 16)
            pointer, status, control = struct.unpack('<QII', data)
            if control & 1:
                found.append((pointer, status >> 24, (control >> 10) & 63, status & 0xffffff))
                self.controls.append(control)
                self.event += 1
                if self.ack:
                    acknowledge(self.m, self.event)
            else:
                if len(found) >= minimum:
                    break
                time.sleep(.005)
        return found

    def cmd(self, kind, pointer=0, slot=0):
        offset = CMD + self.command * 16
        self.command += 1
        self.put(offset, trb(pointer, 0, kind, slot << 24))
        self.m.xwrite(self.m.doorbell, 0)
        events = self.events()
        match = [event for event in events if event[2] == 33 and event[0] == self.address(offset)]
        assert len(match) == 1 and match[0][1] == 1, ('command', kind, events)

    def setup(self):
        self.cmd(9)  # Enable Slot; first slot is 1.
        self.put(DCBAA + 8, struct.pack('<Q', self.address(OUTPUT)))
        self.put(INPUT, bytes(4096))
        self.put(OUTPUT, bytes(4096))
        self.put(INPUT, struct.pack('<II', 0, 3))
        self.put(INPUT + 32, struct.pack('<IIII', (1 << 27) | (4 << 20), 1 << 16, 0, 0))
        self.put(INPUT + 64, struct.pack('<IIQII', 0, (512 << 16) | (4 << 3) | 6,
                                       self.address(EP0) | 1, 8, 0))
        self.cmd(11, self.address(INPUT), 1)
        setup = bytes([0, 9, 1, 0, 0, 0, 0, 0])
        self.put(EP0, trb(int.from_bytes(setup, 'little'), 8, 2, 64) +
                 trb(0, 0, 4, (1 << 16) | 32))
        self.ep0 = 2
        self.m.xwrite(self.m.doorbell + 4, 1)
        event = self.events()
        assert any(e[2] == 32 and e[1] == 1 for e in event), ('set configuration', event)
        self.put(INPUT, bytes(4096))
        self.put(INPUT, struct.pack('<II', 0, 1 | (1 << 3) | (1 << 4)))
        self.put(INPUT + 32, struct.pack('<IIII', (4 << 27) | (4 << 20), 1 << 16, 0, 0))
        for dci, kind, ring in ((3, 6, BULK_IN), (4, 2, BULK_OUT)):
            self.put(INPUT + 32 + dci * 32,
                     struct.pack('<IIQII', 0, (1024 << 16) | (15 << 8) | (kind << 3) | 6,
                                 self.address(ring) | 1, 1024, 0))
        self.cmd(12, self.address(INPUT), 1)

    def read(self, size, intermediate=True, requested=None, event_data=False):
        tag = self.out + 1
        cdb = bytes([0x28, 0]) + struct.pack('>I', 0) + bytes([0]) + struct.pack('>H', size // 512) + bytes([0])
        cbw = struct.pack('<IIIBBB16s', 0x43425355, tag, size, 0x80, 0, 10, cdb)
        self.put(CBW, cbw)
        offset = BULK_OUT + self.out * 16
        self.out += 1
        self.put(offset, trb(self.address(CBW), len(cbw), 1, 32))
        self.m.xwrite(self.m.doorbell + 4, 4)
        events = self.events()
        assert any(e[0] == self.address(offset) and e[1:3] == (1, 32) for e in events), ('CBW', events)
        requested = requested or size
        self.put(DATA, bytes([0xa5]) * requested)
        start = BULK_IN + self.ins * 16
        descriptors = []
        for pos in range(0, requested, 65536):
            last = pos + 65536 >= requested
            flags = (0 if last else 16) | (32 if last or intermediate else 0) | 4
            descriptors.append(trb(self.address(DATA + pos), min(65536, requested - pos), 1, 16 if event_data else flags))
            if event_data:
                descriptors.append(trb(0x1234000000000000 + pos, 0, 7, 32 | (0 if last else 16)))
        self.ins += len(descriptors)
        self.put(start, b''.join(descriptors))
        self.m.xwrite(self.m.doorbell + 4, 3)
        return start, self.events(minimum=(requested + 65535) // 65536 if intermediate else 1)

    def csw(self):
        address = BULK_IN + self.ins * 16
        self.ins += 1
        self.put(address, trb(self.address(CBW + 64), 13, 1, 32))
        self.m.xwrite(self.m.doorbell + 4, 3)
        events = self.events()
        data = self.m.inspect(CBW + 64, 13)
        assert len(data) == 13 and data[:4] == b'USBS', ('CSW signature', data.hex(), events)
        _, tag, residue, status = struct.unpack('<IIIB', data)
        assert tag == self.out, ('CSW tag', tag, self.out)
        assert status != 0 or residue == 0, ('successful CSW residue', residue)
        return data


def run(binary, output=None, expect_missing=False):
    with ExitStack() as stack:
        root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='uc-usb-storage-'))) if output is None else output
        if output is not None:
            root.mkdir(mode=0o700)
        pattern = bytes((i * 17 + i // 4096) & 255 for i in range(256 * 1024))
        disk = root / 'disk.raw'
        disk.write_bytes(pattern)
        m = Machine(root, 'storage', stack, binary, generic=True)
        cfg = 0x3f008000
        m.write(cfg + 0x10, m.xbase)
        m.write(cfg + 0x14, 0)
        m.write(cfg + 4, 6, 'w')
        qmp(m.monitor, 'blockdev-add', {'node-name':'disk', 'driver':'raw',
            'file':{'driver':'file', 'filename':str(disk.resolve())}})
        qmp(m.monitor, 'device_add', {'driver':'usb-storage', 'id':'stick', 'bus':'host.0', 'drive':'disk'})
        rings(m)
        s = Storage(m)
        s.setup()
        # Drain the SCSI power-on Unit Attention before requiring real data.
        s.read(512)
        first = s.csw()
        assert first[12] == 1, ('expected initial Unit Attention', first.hex())
        for size in (65536, 131072):
            start, events = s.read(size, intermediate=False)
            assert m.inspect(DATA, size) == pattern[:size], ('single IOC disk payload', size)
            assert [(e[0], e[1], e[2], e[3]) for e in events] == [
                (s.address(start + (size // 65536 - 1) * 16), 1, 32, 0)], events
            assert s.csw()[12] == 0
        start, events = s.read(131072)
        assert m.inspect(DATA, 131072) == pattern[:131072], 'Actual disk payload was not DMA-written completely'
        expected = [s.address(start), s.address(start + 16)]
        actual = [e[0] for e in events if e[2] == 32 and e[1] == 1]
        print('actual128KiB disk payload verified; completion events:', events, flush=True)
        assert actual == (expected[:1] if expect_missing else expected), ('per-IOC bulk completion', expected, actual)
        assert s.csw()[12] == 0
        if expect_missing:
            print('PASS: baseline reproduces missing final IOC despite exact128KiB disk DMA; single IOC controls pass')
            return
        # Preserve the existing one-event terminal-short behavior. This is a
        # regression guard, not full qualification of later IOC/short semantics.
        start, events = s.read(512, requested=131072)
        assert m.inspect(DATA, 512) == pattern[:512]
        assert events == [(s.address(start), 13, 32, 65536 - 512)], events
        assert s.csw()[12] == 0
        start, events = s.read(131072, event_data=True)
        assert m.inspect(DATA, 131072) == pattern[:131072]
        assert events == [(0x1234000000000000, 1, 32, 65536),
                          (0x1234000000010000, 1, 32, 65536)], events
        assert all(control & 4 for control in s.controls[-2:]), 'Event Data flag missing'
        assert s.csw()[12] == 0
        # Preserve completed bulk data, unacknowledged interrupt/events and
        # mass-storage CSW phase across real QEMU migration, then read again.
        s.ack = False
        start, events = s.read(131072)
        assert len(events) == 2 and m.irq(), ('pending storage migration', events)
        target = Machine(root, 'target', stack, binary, generic=True, incoming=True)
        target_disk = root / 'target.raw'
        target_disk.write_bytes(pattern)
        qmp(target.monitor, 'blockdev-add', {'node-name':'disk', 'driver':'raw',
            'file':{'driver':'file', 'filename':str(target_disk.resolve())}})
        qmp(target.monitor, 'device_add', {'driver':'usb-storage', 'id':'stick', 'bus':'host.0', 'drive':'disk'})
        migrate(m, target, root)
        target.oper, target.runtime, target.doorbell = m.oper, m.runtime, m.doorbell
        assert target.irq() and target.inspect(DATA, 131072) == pattern[:131072]
        m = target
        s.m = target
        acknowledge(m, s.event)
        s.ack = True
        assert s.csw()[12] == 0
        start, events = s.read(65536)
        assert len(events) == 1 and events[0][1] == 1
        assert m.inspect(DATA, 65536) == pattern[:65536] and s.csw()[12] == 0
        # A transport STALL remains terminal even with multiple IOC flags.
        # Invalid CBW signature provokes the real USB storage endpoint error.
        s.put(CBW, bytes(31))
        start = BULK_OUT + s.out * 16
        s.put(start, trb(s.address(CBW), 16, 1, 16 | 32) +
                     trb(s.address(CBW + 16), 15, 1, 32))
        m.xwrite(m.doorbell + 4, 4)
        events = s.events(minimum=2)
        assert len(events) == 1 and events[0][1:3] == (6, 32), events
        print('PASS: actual USB storage64/128KiB, per-IOC DMA events, single-IOC, short, EventData/EDTLA, migration continuation and terminal-STALL controls')



if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu', default=executable('qemu-system-aarch64'))
    p.add_argument('--output', type=Path)
    p.add_argument('--expect-missing-ioc', action='store_true', help='require the unfixed baseline failure')
    args = p.parse_args()
    run(args.qemu, args.output, args.expect_missing_ioc)
