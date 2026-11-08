#!/usr/bin/env python3
"""GENET TX frames split across producer doorbells, cleanup and migration.

The backend is an isolated socketpair. No host network or physical Pi is used.
"""
import argparse
from contextlib import ExitStack
from pathlib import Path
import select
import socket
import tempfile

from test_emulator_adc_migration import migrate
from test_emulator_genet_network import Machine, PACKET, setup
from uconsole_emulator import executable, qmp

TX_RAM = 0x110000000


def descriptor(m, index, data, flags, address=None):
    address = TX_RAM + index * 4096 if address is None else address
    if data is not None:
        m.memory(address, data)
    m.write(0x4004 + index * 12, address & 0xffffffff)
    m.write(0x4008 + index * 12, address >> 32)
    m.write(0x4000 + index * 12, ((len(data) if data is not None else 100) << 16) | flags)


def silence(m):
    if select.select([m.wire], [], [], 0.05)[0]:
        raise AssertionError('unexpected backend frame: ' +
                             m.wire.recv(4096, socket.MSG_PEEK).hex())


def first(m, tx, packet=PACKET, check_irq=True):
    descriptor(m, 0, bytes(64) + packet[:30], 0x2000)
    m.write(tx + 12, 1)
    assert m.read(tx + 8) & 0xffff == 1
    silence(m)
    if check_irq:
        assert not m.read(0x200) & (1 << 16), 'packet completion before EOP'



def migrate_staging(source, target, root):
    # QEMU net/announce.c emits an unrelated 60-byte RARP after migration.
    # Disable this management announcement so silence checks observe GENET TX,
    # not generic backend link-learning packets. Retained negative evidence:
    # stg7-rarp.log has EtherType 8035/opcode 3, not the test's EtherType88b5.
    for machine in (source, target):
        qmp(machine.monitor, 'migrate-set-parameters', {'announce-rounds': 0})
    migrate(source, target, root)


def run(binary, root, case):
    with ExitStack() as stack:
        m = Machine(root, 'src', stack, binary)
        _, tx = setup(m)
        first(m, tx, check_irq=case != 'payload')
        # Stopping DMA pauses a valid assembly; it does not flush its prefix.
        m.write(0x5044, 0)
        m.write(0x5044, 1 | (1 << 17))
        descriptor(m, 1, PACKET[30:], 0x4000)
        m.write(tx + 12, 2)
        actual = m.receive_wire()
        assert actual == PACKET, f'split doorbell payload: got {actual.hex()}, expected {PACKET.hex()}'
        assert m.read(tx + 8) & 0xffff == 2
        assert m.read(0x200) & (1 << 16)
        print('PASS: partial producer update stays buffered; EOP emits one exact frame')
        if case == 'payload':
            return
        # Reuse a one-entry descriptor ring across the 16-bit counter rollover.
        # The consumed prefix must live in hardware staging, not its old RAM.
        _, tx = setup(m, count=1)
        m.write(tx + 8, 0xffff)
        m.write(tx + 12, 0xffff)
        descriptor(m, 0, bytes(64) + PACKET[:30], 0x2000)
        m.write(tx + 12, 0)
        assert m.read(tx + 8) & 0xffff == 0
        silence(m)
        assert not m.read(0x200) & (1 << 16)
        descriptor(m, 0, PACKET[30:], 0x4000)
        m.write(tx + 12, 1)
        assert m.receive_wire() == PACKET
        assert m.read(tx + 8) & 0xffff == 1
        print('PASS: staged frame survives descriptor reuse and 16-bit producer rollover')
        # Two rings maintain separate assembly even across interleaved doorbells.
        _, tx16 = setup(m, ring=16)
        _, tx0 = setup(m, ring=0)
        m.write(0x5040, (1 << 16) | 1)
        m.write(0x5044, 1 | (1 << 17) | (1 << 1))
        first(m, tx16, PACKET + b'A')
        descriptor(m, 0, bytes(64) + (PACKET + b'B')[:30], 0x2000)
        m.write(tx0 + 12, 1)
        descriptor(m, 1, (PACKET + b'B')[30:], 0x4000)
        m.write(tx0 + 12, 2)
        assert m.receive_wire() == PACKET + b'B'
        descriptor(m, 1, (PACKET + b'A')[30:], 0x4000)
        m.write(tx16 + 12, 2)
        assert m.receive_wire() == PACKET + b'A'
        print('PASS: independent per-ring assembly across interleaved producer updates')
        # New SOP explicitly discards unfinished data rather than splicing frames.
        _, tx = setup(m)
        first(m, tx)
        replacement = PACKET + b'new-SOP'
        descriptor(m, 1, bytes(64) + replacement, 0x6000)
        m.write(tx + 12, 2)
        assert m.receive_wire() == replacement
        silence(m)
        # Failed DMA aborts staging; corrected orphan EOP cannot reuse the prefix.
        _, tx = setup(m)
        first(m, tx)
        descriptor(m, 1, None, 0x4000, address=0xfe200000)
        m.write(tx + 12, 2)
        assert m.read(tx + 8) & 0xffff == 1
        assert not m.read(0x200) & (1 << 16)
        descriptor(m, 1, PACKET[30:], 0x4000)
        m.write(tx + 12, 2)
        assert m.read(tx + 8) & 0xffff == 2
        silence(m)
        descriptor(m, 2, bytes(64) + replacement, 0x6000)
        m.write(tx + 12, 3)
        assert m.receive_wire() == replacement
        # Overflow also invalidates staging without advancing the failing entry.
        _, tx = setup(m)
        first(m, tx)
        descriptor(m, 1, bytes(4095), 0x4000)
        m.write(tx + 12, 2)
        assert m.read(tx + 8) & 0xffff == 1
        descriptor(m, 1, bytes(64) + replacement, 0x6000)
        m.write(tx + 12, 2)
        assert m.receive_wire() == replacement
        # Malformed ring geometry invalidates assembly; restoring it alone
        # must not revive a prefix belonging to the abandoned frame.
        _, tx = setup(m)
        first(m, tx)
        descriptor(m, 1, PACKET[30:], 0x4000)
        m.write(tx + 28, 0xffffffff)
        m.write(tx + 12, 2)
        assert m.read(tx + 8) & 0xffff == 1
        m.write(tx + 28, 11)
        m.write(tx + 12, 2)
        silence(m)
        assert not m.read(0x200) & (1 << 16)
        descriptor(m, 2, bytes(64) + replacement, 0x6000)
        m.write(tx + 12, 3)
        assert m.receive_wire() == replacement
        # A terminal descriptor too short to hold an Ethernet header cannot
        # generate a successful packet event merely because EOP was set.
        _, tx = setup(m)
        descriptor(m, 0, bytes(64) + PACKET[:13], 0x6000)
        m.write(tx + 12, 1)
        assert m.read(tx + 8) & 0xffff == 1
        silence(m)
        assert not m.read(0x200) & (1 << 16)
        descriptor(m, 1, bytes(64) + replacement, 0x6000)
        m.write(tx + 12, 2)
        assert m.receive_wire() == replacement
        print('PASS: new SOP, failed DMA and overflow/geometry/short-frame cannot splice stale frame bytes')
        for reset in ('flush', 'reset'):
            _, tx = setup(m)
            first(m, tx)
            if reset == 'reset':
                m.write(0x808, 1 << 13)
                _, tx = setup(m)
                index, producer = 0, 1
            else:
                m.write(0xb34, 1)
                m.write(0xb34, 0)
                index, producer = 1, 2
            descriptor(m, index, PACKET[30:], 0x4000)
            m.write(tx + 12, producer)
            silence(m)
            assert not m.read(0x200) & (1 << 16)
            descriptor(m, index + 1, bytes(64) + replacement, 0x6000)
            m.write(tx + 12, producer + 1)
            assert m.receive_wire() == replacement
        print('PASS: explicit TX flush and reset discard unfinished frame; recovery is clean')
        # A consumed prefix must migrate independently of reused guest RAM.
        _, tx = setup(m)
        first(m, tx)
        m.memory(TX_RAM, b'\xa5' * 256)
        m.write(0x600, 0)  # SOP captured 64-byte status mode must also migrate.
        target = Machine(root, 'dst', stack, binary, incoming=True)
        migrate_staging(m, target, root)
        if qmp(target.monitor, 'query-status')['status'] != 'running':
            qmp(target.monitor, 'cont')
        silence(target)
        assert target.read(tx + 8) & 0xffff == 1
        assert not target.read(0x200) & (1 << 16)
        descriptor(target, 1, PACKET[30:], 0x4000)
        target.write(tx + 12, 2)
        assert target.receive_wire() == PACKET
        assert target.read(0x200) & (1 << 16)
        silence(target)
        print('PASS: migrate unfinished assembly after guest buffer reuse; one exact EOP frame')



def legacy_compatibility(binary, legacy, root):
    with ExitStack() as stack:
        source = Machine(root, 'v1', stack, legacy)
        _, tx = setup(source)
        first(source, tx, check_irq=False)
        source.write(0x208, 0xffffffff)
        target = Machine(root, 'v2', stack, binary, incoming=True)
        migrate_staging(source, target, root)
        if qmp(target.monitor, 'query-status')['status'] != 'running':
            qmp(target.monitor, 'cont')
        # V1 never recorded this consumed prefix. Do not invent/replay it.
        descriptor(target, 1, PACKET[30:], 0x4000)
        target.write(tx + 12, 2)
        silence(target)
        assert target.read(tx + 8) & 0xffff == 2
        assert not target.read(0x200) & (1 << 16)
        descriptor(target, 2, bytes(64) + PACKET, 0x6000)
        target.write(tx + 12, 3)
        assert target.receive_wire() == PACKET
        print('PASS: legacy V1 migration loads empty staging, rejects orphan tail, recovers on SOP')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu-system')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--legacy-qemu', help='optional V1 baseline for compatibility control')
    parser.add_argument('--case', choices=('all', 'payload'), default='all')
    args = parser.parse_args()
    binary = args.qemu_system or str(executable('qemu-system-aarch64'))
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
        run(binary, args.output, args.case)
        if args.legacy_qemu:
            legacy_root = args.output / 'legacy'
            legacy_root.mkdir()
            legacy_compatibility(binary, args.legacy_qemu, legacy_root)
    else:
        with tempfile.TemporaryDirectory(prefix='uc-genet-staging-') as temporary:
            run(binary, Path(temporary), args.case)
            if args.legacy_qemu:
                legacy_root = Path(temporary) / 'legacy'
                legacy_root.mkdir()
                legacy_compatibility(binary, args.legacy_qemu, legacy_root)


if __name__ == '__main__':
    main()
