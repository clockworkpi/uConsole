#!/usr/bin/env python3
"""Pi4 RAM profiles: actual CPU banks/aborts, mailbox/FDT and high PCIe USB DMA.

Host-backed simulation does not qualify physical DRAM, caches or a native Pi.
"""
import argparse
from contextlib import ExitStack
import json
import hashlib
from pathlib import Path
import struct
import subprocess
import tempfile
import time

from test_emulator_adc_migration import migrate
from test_emulator_pcie_xhci import Machine, configure, rings, noop, acknowledge
from test_emulator_xhci_storage import Storage, DATA, BULK_OUT, CBW, trb
from uconsole_emulator import executable, qmp

GIB = 1 << 30
PROFILES = [('raspi4b', 2, 0xb03115), ('raspi4b-4g', 4, 0xc03115),
            ('raspi4b-8g', 8, 0xd03115)]



def sparse_ram(capacity):
    # Capacity is real, but untouched pages need no host swap reservation.
    # Two eight-GiB migration peers must also run on small CI workers.
    return ('-object', f'memory-backend-ram,id=test-memory,size={capacity}G,reserve=off')


def request(m, tag, values):
    buffer = 0x30000
    words = [24 + 4 * len(values), 0, tag, 4 * len(values), 0, *values, 0]
    m.memory(buffer, struct.pack('<' + 'I' * len(words), *words))
    m.write(0xfe00b8a0, buffer | 8)
    assert m.read(0xfe00b880) == buffer | 8
    response = struct.unpack('<' + 'I' * len(words), m.inspect(buffer, len(words) * 4))
    assert response[1] == 0x80000000 and response[4] == 0x80000000 | len(values) * 4, response
    return response[5:5 + len(values)]


def mov64(register, value):
    return [0xd2800000 | ((value & 65535) << 5) | register] + [
        0xf2800000 | (part << 21) | (((value >> (16 * part)) & 65535) << 5) | register
        for part in range(1, 4)]


def cpu_probe(m, address, pattern, fault=False):
    # EL3 code executes real loads/stores, captures ESR/FAR on synchronous
    # exceptions, and leaves a result in low RAM. No qtest MMIO-fault inference.
    result = 0x4000
    main = [0xd2810001, 0xd51ec001, 0xd5033fdf]  # VBAR_EL3=0x800; ISB
    main += mov64(0, address) + mov64(2, pattern)
    main += [0xf9000002, 0xf9400003]  # str x2,[x0]; ldr x3,[x0]
    main += mov64(5, result) + [0xf90008a3]  # result[2]=readback
    main += mov64(3, 0xdead) + [0xf90000a3, 0xd503207f, 0x17ffffff]
    handler = [0xd53e5203, 0xd53e6004] + mov64(5, result)
    handler += [0xf90004a4, 0xf90000a3, 0xd503207f, 0x17ffffff]
    payload = bytearray(0x1100)
    payload[:len(main) * 4] = struct.pack('<' + 'I' * len(main), *main)
    for vector in (0x800, 0xa00):
        struct.pack_into('<I', payload, vector, 0x14000000 | ((0x1000 - vector) // 4))
    payload[0x1000:0x1000 + len(handler) * 4] = struct.pack('<' + 'I' * len(handler), *handler)
    qmp(m.monitor, 'system_reset')
    m.memory(result, bytes(24))
    m.memory(0, payload)
    qmp(m.monitor, 'cont')
    deadline = time.monotonic() + 10
    while not m.read(result, 'q'):
        assert time.monotonic() < deadline, ('CPU probe timed out', hex(address))
        time.sleep(.01)
    qmp(m.monitor, 'stop')
    esr, far, value = struct.unpack('<QQQ', m.inspect(result, 24))
    if fault:
        assert esr & ~0x240 == 0x96000010 and far == address, (hex(address), hex(esr), hex(far))
    else:
        assert esr == 0xdead and value == pattern, (hex(address), hex(esr), hex(value))
        assert m.read(address, 'q') == pattern


def cpu_mailbox(binary, root, board, capacity, revision):
    with ExitStack() as stack:
        m = Machine(root, board + '-cpu', stack, binary, board_name=board + ',memory-backend=test-memory',
                    device_args=sparse_ram(capacity), accel='tcg')
        assert request(m, 0x10002, [0]) == (revision,)
        # Legacy 32-bit mailbox reports only the low ARM bank, not all RAM.
        assert request(m, 0x10005, [0, 0]) == (0, 0x3c000000)
        assert request(m, 0x10006, [0, 0]) == (0x3c000000, 64 << 20)
        addresses = [0x20000000, 0x3c000000, 0x3ffffff8, 0x40000000,
                     min(capacity * GIB, 0xfc000000) - 8]
        if capacity >= 4:
            addresses += [0xbffffff8, 0xc0000000]
        if capacity == 8:
            addresses += [0x100000000, 0x100000008, 0x17ffffff8, 0x180000000, 0x1fffffff8]
        for index, address in enumerate(addresses):
            cpu_probe(m, address, 0x1234000000000000 + index)
        for index, address in enumerate(addresses):
            assert m.read(address, 'q') == 0x1234000000000000 + index, 'RAM banks alias one another'
        # Unsupported peripheral holes and exact upper capacity boundary fault.
        for address in (0xfc000000, 0xfd000000, 0xfd581800, 0xfffffff8, capacity * GIB):
            cpu_probe(m, address, 0xcafe, fault=True)
    print(f'PASS: {board} actual CPU bank access/isolation, peripheral-hole/capacity aborts and mailbox revision/low-bank semantics', flush=True)


def fixture_fdt(path, size_cells, address_cells=2):
    strings = bytearray()
    offsets = {}
    def name(s):
        if s not in offsets:
            offsets[s] = len(strings)
            strings.extend(s.encode() + b'\0')
        return offsets[s]
    data = bytearray()
    def node(s):
        data.extend(struct.pack('>I', 1) + s.encode() + b'\0')
        data.extend(bytes(-len(data) % 4))
    def end():
        data.extend(struct.pack('>I', 2))
    def prop(s, value):
        data.extend(struct.pack('>III', 3, len(value), name(s)) + value)
        data.extend(bytes(-len(data) % 4))
    def cell(s, value):
        prop(s, struct.pack('>I', value))
    node(''); cell('#address-cells', address_cells); cell('#size-cells', size_cells)
    node('chosen'); end()
    node('memory@0'); prop('device_type', b'memory\0'); prop('reg', bytes(4 * (address_cells + size_cells))); end()
    node('soc'); cell('#address-cells', 1); cell('#size-cells', 1)
    node('gpio@7e200000'); cell('phandle', 1); end()
    for child in ('i2c@7e205000', 'i2c@7e804000'):
        node(child); cell('#address-cells', 1); cell('#size-cells', 0); end()
    end(); end(); data.extend(struct.pack('>I', 9))
    header = struct.pack('>10I', 0xd00dfeed, 56 + len(data) + len(strings), 56, 56 + len(data), 40, 17, 16, 0, len(strings), len(data))
    path.write_bytes(header + bytes(16) + data + strings)


def parse_fdt(path):
    blob = path.read_bytes()
    _, _, off, strings, _, _, _, _, _, size = struct.unpack_from('>10I', blob)
    position, stack, nodes = off, [], {}
    while position < off + size:
        token, = struct.unpack_from('>I', blob, position); position += 4
        if token == 1:
            finish = blob.index(0, position)
            stack.append(blob[position:finish].decode())
            position = (finish + 4) & ~3
            nodes['/' + '/'.join(stack[1:])] = {}
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, index = struct.unpack_from('>II', blob, position); position += 8
            finish = blob.index(0, strings + index)
            key = blob[strings + index:finish].decode()
            nodes['/' + '/'.join(stack[1:])][key] = blob[position:position + length]
            position = (position + length + 3) & ~3
        elif token == 9:
            break
        else:
            assert token == 4, token
    return nodes


def fdt_contract(binary, root):
    kernel = root / 'kernel.bin'; kernel.write_bytes(bytes(4096))
    for board, capacity, revision in PROFILES:
        for size_cells in (1, 2):
            source = root / f'{board}-{size_cells}.dtb'
            fixture_fdt(source, size_cells)
            with ExitStack() as stack:
                m = Machine(root, f'{board}-fdt{size_cells}', stack, binary,
                    board_name=board + ',memory-backend=test-memory',
                    device_args=(*sparse_ram(capacity), '-kernel', str(kernel), '-dtb', str(source)))
                output = root / f'{board}-{size_cells}-result.dtb'
                qmp(m.monitor, 'dumpdtb', {'filename': str(output)})
                nodes = parse_fdt(output)
                assert struct.unpack('>I', nodes['/']['#size-cells'])[0] == size_cells
                regions = []
                for props in nodes.values():
                    if props.get('device_type') == b'memory\0':
                        reg = props['reg']; width = (2 + size_cells) * 4
                        assert len(reg) % width == 0
                        for offset in range(0, len(reg), width):
                            address = int.from_bytes(reg[offset:offset + 8], 'big')
                            length = int.from_bytes(reg[offset + 8:offset + width], 'big')
                            regions.append((address, length))
                expected = [(0, 0x3c000000), (0x40000000, min(capacity * GIB, 0xfc000000) - GIB)]
                if capacity == 8:
                    expected += [(4 * GIB, 2 * GIB), (6 * GIB, 2 * GIB)] if size_cells == 1 else [(4 * GIB, 4 * GIB)]
                assert sorted(regions) == expected, (board, size_cells, regions, expected)
    invalid = root / 'unrepresentable-address.dtb'
    fixture_fdt(invalid, 1, address_cells=1)
    failed = subprocess.run([binary, '-M', 'raspi4b-8g', '-accel', 'qtest', '-S',
        '-display', 'none', '-serial', 'none', '-monitor', 'none', '-kernel', str(kernel),
        '-dtb', str(invalid)], capture_output=True, timeout=15)
    assert failed.returncode != 0 and b'not representable in FDT cells' in failed.stderr, failed
    (root / 'fdt-rejected.log').write_bytes(failed.stderr)
    print('PASS: actual arm_load_dtb memory banks, one/two size cells, upper-bank splitting and unchanged VC64MiB reservation', flush=True)


def high_dma(binary, root):
    pattern = bytes((i * 17 + i // 4096) & 255 for i in range(256 * 1024))
    disk = root / 'disk.raw'; disk.write_bytes(pattern)
    with ExitStack() as stack:
        def machine(name, incoming=False):
            selected_disk = disk
            if incoming:
                selected_disk = root / 'target.raw'
                selected_disk.write_bytes(replacement + pattern[len(replacement):])
            return Machine(root, name, stack, binary, board_name='raspi4b-8g,memory-backend=test-memory', incoming=incoming,
                device_args=(*sparse_ram(8), '-d', 'guest_errors', '-drive', f'if=none,id=disk,format=raw,file={selected_disk},snapshot=on',
                             '-device', 'usb-storage,id=stick,bus=bcm-xhci.0,drive=disk'))
        m = machine('high-dma')
        configure(m)
        # SCB0 memory geometry: ilog2(8GiB)-15=0x12, alongside access/UR bits.
        m.reg(0x4008, 0x90003000)
        # Same captured Linux translation: PCI 16GiB -> ARM0, eight-GiB window.
        m.reg(0x4034, 0x12); m.reg(0x4038, 4)
        m.ram_offset = 4 * GIB
        m.dma_base = 16 * GIB + m.ram_offset
        rings(m); noop(m, 0); acknowledge(m, 1)
        s = Storage(m); s.command = s.event = 1; s.setup()
        s.read(512); assert s.csw()[12] == 1
        start, events = s.read(131072)
        assert len(events) == 2 and m.inspect(DATA, 131072) == pattern[:131072]
        assert s.csw()[12] == 0
        # WRITE(10) uses real bulk endpoints and writes high guest RAM to disk.
        replacement = bytes(value ^ 0xff for value in pattern[:131072])
        cdb = bytes([0x2a, 0]) + struct.pack('>I', 0) + bytes([0]) + struct.pack('>H', 256) + bytes([0])
        tag = s.out + 1
        cbw = struct.pack('<IIIBBB16s', 0x43425355, tag, len(replacement), 0, 0, 10, cdb)
        s.put(CBW, cbw)
        offset = BULK_OUT + s.out * 16; s.out += 1
        s.put(offset, trb(s.address(CBW), len(cbw), 1, 32))
        m.xwrite(m.doorbell + 4, 4); assert s.events()[0][1] == 1
        s.put(DATA, replacement)
        offset = BULK_OUT + s.out * 16; s.out += 2
        s.put(offset, trb(s.address(DATA), 65536, 1, 16 | 32) + trb(s.address(DATA + 65536), 65536, 1, 32))
        m.xwrite(m.doorbell + 4, 4); events = s.events(minimum=2)
        assert len(events) == 2 and all(event[1] == 1 for event in events), events
        next_out = s.out
        s.out = tag  # CSW checks the current CBW tag, not the extra OUT data TRBs.
        assert s.csw()[12] == 0
        s.out = next_out
        s.read(131072); assert m.inspect(DATA, 131072) == replacement and s.csw()[12] == 0
        # Migrate genuinely high rings and pending real USB data completion IRQ.
        s.ack = False; s.read(65536); assert m.irq()
        target = machine('high-target', incoming=True)
        migrate(m, target, root)
        target.command('clock_step 1')
        target.ram_offset, target.dma_base = m.ram_offset, m.dma_base
        target.oper, target.runtime, target.doorbell = m.oper, m.runtime, m.doorbell
        (root / 'migration-registers.json').write_text(json.dumps({
            'source': {'xhci_status': m.xread(m.oper + 4), 'bar2': [m.reg(0x4034), m.reg(0x4038)], 'cmd': m.config(1,0,0,4)},
            'target': {'xhci_status': target.xread(target.oper + 4), 'bar2': [target.reg(0x4034), target.reg(0x4038)], 'cmd': target.config(1,0,0,4)}}, indent=2))
        assert not target.xread(target.oper + 4) & 0x1000, 'Migration restored xHCI before its DMA translation'
        assert target.irq() and target.inspect(DATA, 65536) == replacement[:65536]
        s.m = target; acknowledge(target, s.event); s.ack = True
        assert s.csw()[12] == 0
        s.read(65536); assert target.inspect(DATA, 65536) == replacement[:65536] and s.csw()[12] == 0
    print('PASS: real xHCI NoOp/read/write DMA above4GiB with nonidentity64-bit PCI translation, native INTx and migration continuation', flush=True)



def dma_bounds(binary, root):
    backing = root / 'ram.raw'
    poison = trb(0, 0, 23)
    with backing.open('wb') as memory:
        memory.truncate(8 * GIB)
        # A valid command in otherwise hidden backing proves that merely
        # declining to process zero-filled RAM is not the negative control.
        memory.seek(0xfc001000); memory.write(poison)
    with ExitStack() as stack:
        m = Machine(root, 'dma-bounds', stack, binary,
            board_name='raspi4b-8g,memory-backend=backing',
            device_args=('-object', f'memory-backend-file,id=backing,size=8G,mem-path={backing},share=on,reserve=off',
                         '-d', 'guest_errors'))
        configure(m); m.reg(0x4008, 0x90003000)
        m.reg(0x4034, 0x12); m.reg(0x4038, 4)
        m.ram_offset = 4 * GIB; m.dma_base = 20 * GIB
        for cpu_address in (0xfbffffc0, 0x100000000, 0x17fffffc0, 0x180000000, 0x1ffffffc0):
            rings(m)
            m.command(f'write {cpu_address:#x} 0x10 0x{poison.hex()}')
            pointer = 16 * GIB + cpu_address
            m.xwrite(m.oper + 0x18, (pointer | 1) & 0xffffffff)
            m.xwrite(m.oper + 0x1c, pointer >> 32)
            m.xwrite(m.doorbell, 0)
            event = struct.unpack('<QII', m.inspect(0x1200000, 16))
            assert event[0] == pointer and event[1] >> 24 == 1 and m.irq(), (hex(cpu_address), event)
        for cpu_address in (0xfc001000, 8 * GIB):
            rings(m)
            pointer = 16 * GIB + cpu_address
            m.xwrite(m.oper + 0x18, (pointer | 1) & 0xffffffff)
            m.xwrite(m.oper + 0x1c, pointer >> 32)
            m.xwrite(m.doorbell, 0)
            assert m.inspect(0x1200000, 16) == bytes(16) and not m.irq(), hex(cpu_address)
        assert 'DMA memory access failed!' in (root / 'dma-bounds.log').read_text()
    with backing.open('rb') as memory:
        memory.seek(0xfc001000); assert memory.read(16) == poison
    # Keep output evidence small; the sparse backing is an eight-GiB map.
    backing.unlink()
    print('PASS: real xHCI DMA across low/high bank boundaries; seeded MMIO-hole backing and exact8GiB boundary remain inaccessible', flush=True)


def run(args, root):
    if args.only in (None, 'cpu'):
        for board, capacity, revision in PROFILES:
            cpu_mailbox(args.qemu, root, board, capacity, revision)
    if args.only in (None, 'fdt'):
        fdt_contract(args.qemu, root)
    if args.only in (None, 'dma'):
        dma_bounds(args.qemu, root)
        high_dma(args.qemu, root)
    (root / 'result.json').write_text(json.dumps({'schema': 'uconsole/pi4-memory-model@1', 'passed': True,
        'qemu': str(Path(args.qemu).resolve()),
        'qemu_sha256': hashlib.sha256(Path(args.qemu).read_bytes()).hexdigest(),
        'fixture_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope': args.only or 'all',
        'limitations': ['host-backed RAM and modeled device DMA', 'no physical DRAM/cache/interconnect qualification', 'bank-boundary probes, not an exhaustive RAM walk']}, indent=2) + '\n')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu', default=executable('qemu-system-aarch64'))
    p.add_argument('--output', type=Path)
    p.add_argument('--only', choices=('cpu', 'fdt', 'dma'))
    args = p.parse_args()
    if args.output:
        args.output.mkdir(mode=0o700)
        run(args, args.output.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix='uc-pi4-mem-') as temporary:
            run(args, Path(temporary))
