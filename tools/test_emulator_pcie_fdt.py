#!/usr/bin/env python3
"""Native FreeBSD BCM2711 PCIe init contract with real xHCI DMA/INTx."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import tempfile
import struct
from test_emulator_pcie_xhci import Machine, geometry, rings, noop, acknowledge
from test_emulator_adc_migration import migrate
from uconsole_emulator import executable, qmp


def native_init(m):
    # sys/arm/broadcom/bcm2835/bcm2838_pci.c reset/attach values.
    m.reg(0x9210, 3)
    m.reg(0x9210, 1)
    m.reg(0x4204, 0)
    assert m.reg(0x406c) & 0xffff == 0x320
    m.reg(0x4034, 0x12)  # identity8GiB BAR2 aperture
    m.reg(0x4038, 0)
    m.reg(0x4008, 0x90003000)
    m.reg(0x402c, 0)
    m.reg(0x403c, 0)
    m.reg(0x9210, 0)
    assert m.reg(0x4068) & 0x30 == 0x30
    # BCM has PCIe capability at0xac; link control/status is at0xbc.
    cap = m.config(0, 0, 0, 0x34) & 0xff
    visited = set()
    while cap:
        assert 0x40 <= cap <= 0xfc and cap % 4 == 0 and cap not in visited, (
            'invalid/cyclic conventional PCI capability chain', cap, visited)
        visited.add(cap)
        value = m.config(0, 0, 0, cap)
        if value & 0xff == 0x10:
            break
        cap = (value >> 8) & 0xff
    assert cap == 0xac, f'PCIe capability at{cap:#x}, native link status at0xbc={m.reg(0xbc):#x}'
    assert m.reg(0xbc) >> 16 & 0xf
    assert m.reg(0xbc) == m.config(0, 0, 0, cap + 0x10)
    m.reg(0x400c, 0xc0000000)
    m.reg(0x4010, 0)
    m.reg(0x4070, 0x3ff00000)
    m.reg(0x4080, 6)
    m.reg(0x4084, 6)
    m.reg(0x43c, 0x060400)
    m.reg(0x4204, 2)
    # Reproduce the native cold bridge-window relocation arithmetic.
    base = ((m.config(0, 0, 0, 0x20) & 0xfff0) << 16)
    limit = (m.config(0, 0, 0, 0x20) & 0xfff00000) | 0xfffff
    size = (limit - base) & ((1 << 64) - 1)
    new_limit = (0xc0000000 + size) & ((1 << 64) - 1)
    m.config(0, 0, 0, 0x20, ((new_limit >> 16) & 0xfff0) << 16 | 0xc000)
    m.config(0, 0, 0, 0x18, 0x00010100)
    m.config(0, 0, 0, 4, 6)
    # MSI setup is register/control only; selected xHCI exposes no MSI/MSI-X.
    m.reg(0x4508, 0xffffffff)
    m.reg(0x4514, 0xffffffff)
    m.reg(0x4044, 0xfffffffd)
    m.reg(0x4048, 0xf)
    m.reg(0x404c, 0xffe06540)
    assert m.reg(0x4500) == 0
    assert m.config(1, 0, 0, 8) >> 8 == 0x0c0330
    m.config(1, 0, 0, 0x10, 0xc0010000)
    m.config(1, 0, 0, 0x14, 0)
    m.config(1, 0, 0, 4, 6)
    m.xbase = 0x600010000
    m.dma_base = 0
    assert m.xread(0) == 0x01000040
    geometry(m, compact=True)


def mdio_read(m, register, port=0):
    address = (1 << 20) | (port << 16) | register
    m.reg(0x1100, address)
    assert m.reg(0x1100) == address
    value = m.reg(0x1108)
    assert value & (1 << 31), f'MDIO read did not complete: {value:#x}'
    return value & 0xffff


def mdio_write(m, register, value, port=0):
    address = (port << 16) | register
    m.reg(0x1100, address)
    assert m.reg(0x1100) == address
    m.reg(0x1104, (1 << 31) | value)
    assert not m.reg(0x1104) & (1 << 31)


def ssc_contract(m):
    assert mdio_read(m, 0x1f) == 0
    mdio_write(m, 0x1f, 0x1100)
    assert mdio_read(m, 0x1f) == 0x1100
    assert mdio_read(m, 2) == 0
    assert mdio_read(m, 1) == 0x800
    mdio_write(m, 2, 0x8000)
    assert mdio_read(m, 1) == 0x800
    mdio_write(m, 2, 0xc000)
    assert mdio_read(m, 1) == 0xc00
    mdio_write(m, 1, 0)  # PLL/SSC status is read-only.
    assert mdio_read(m, 1) == 0xc00
    m.reg(0x1108, 0)
    assert m.reg(0x1108) == 0x80000c00
    assert mdio_read(m, 1, port=1) == 0xffff
    assert mdio_read(m, 0x1234) == 0xffff
    # No interrupt or PHY state is fabricated by an absent port/register write.
    mdio_write(m, 2, 0, port=1)
    assert mdio_read(m, 2) == 0xc000
    assert not m.irq()
    m.reg(0x4204, 1 << 27)
    m.reg(0x1100, (1 << 20) | 1)
    assert not m.reg(0x1108) & (1 << 31)
    m.reg(0x4204, 2)
    assert mdio_read(m, 1) == 0xc00
    print('PASS: native SSC bank/control/read-only status, DONE handshakes, absent PHY tuples and clock gating')


def run(binary):
    with tempfile.TemporaryDirectory(prefix='uc-pcie-fdt-') as temporary, ExitStack() as stack:
        root = Path(temporary)
        source = Machine(root, 'src', stack, binary)
        native_init(source)
        ssc_contract(source)
        rings(source)
        noop(source, 0)
        target = Machine(root, 'dst', stack, binary, incoming=True)
        migrate(source, target, root)
        target.xbase, target.dma_base = source.xbase, source.dma_base
        target.oper, target.runtime, target.doorbell = source.oper, source.runtime, source.doorbell
        assert target.irq()
        assert target.reg(0xbc) == source.reg(0xbc)
        assert mdio_read(target, 0x1f) == 0x1100
        assert mdio_read(target, 2) == 0xc000
        assert mdio_read(target, 1) == 0xc00
        acknowledge(target, 1)
        noop(target, 1)
        acknowledge(target, 2)
        qmp(target.monitor, 'system_reset')
        assert all(target.reg(offset) == 0 for offset in (0x1100, 0x1104, 0x1108))
        native_init(target)
        ssc_contract(target)
        rings(target)
        noop(target, 0)
        acknowledge(target, 1)
        print('PASS: native BCM PCIe capability/reset/windows/MSI-control init, real xHCI DMA/INTx, migration/reset')

def incompatible_connected(binary, legacy):
    from test_emulator_pcie_xhci import configure
    with tempfile.TemporaryDirectory(prefix='uc-pcie-layout-') as temporary, ExitStack() as stack:
        root = Path(temporary)
        source = Machine(root, 'old', stack, legacy)
        target = Machine(root, 'new', stack, binary, incoming=True)
        configure(source)
        endpoint = 'unix:' + str(root / 'migration')
        qmp(target.monitor, 'migrate-incoming', {'uri': endpoint})
        qmp(source.monitor, 'migrate', {'uri': endpoint})
        assert target.process.wait(timeout=30) != 0
        error = (root / 'new.log').read_text(errors='replace')
        assert 'pcie-root-port' in error and 'Unknown section or instance' in error, error[-2000:]
        print('PASS: older incompatible connected capability-layout migration is rejected')


def fdt_gate(binary):
    from test_emulator_pi4_memory import fixture_fdt, parse_fdt
    with tempfile.TemporaryDirectory(prefix='uc-pcie-dtb-') as temporary:
        root = Path(temporary)
        source = root / 'input.dtb'
        fixture_fdt(source, 2)
        blob = source.read_bytes()
        header = list(struct.unpack_from('>10I', blob))
        data = blob[header[2]:header[2] + header[9]]
        strings = bytearray(blob[header[3]:header[3] + header[8]])
        name = len(strings)
        strings.extend(b'compatible\0')
        extra = bytearray()
        for node, compatible in (('pcie', b'brcm,bcm2711-pcie\0'),
                                  ('genet', b'brcm,bcm2711-genet-v5\0')):
            extra.extend(struct.pack('>I', 1) + node.encode() + b'\0')
            extra.extend(bytes(-len(extra) % 4))
            extra.extend(struct.pack('>III', 3, len(compatible), name) + compatible)
            extra.extend(bytes(-len(extra) % 4))
            extra.extend(struct.pack('>I', 2))
        data = data[:-8] + extra + data[-8:]
        header[3] = header[2] + len(data)
        header[8], header[9] = len(strings), len(data)
        header[1] = header[3] + len(strings)
        source.write_bytes(struct.pack('>10I', *header) + blob[40:header[2]] + data + strings)
        kernel = root / 'kernel.bin'
        kernel.write_bytes(bytes(4096))
        for connected in (False, True):
            with ExitStack() as stack:
                m = Machine(root, 'on' if connected else 'off', stack, binary,
                    device_args=('-kernel', str(kernel), '-dtb', str(source),
                        '-global', 'bcm2838-pcie-disconnected.connected-xhci=' +
                        ('on' if connected else 'off'),
                        '-global', 'bcm2838-peripherals.genet-network=on'))
                assert qmp(m.monitor, 'qom-get', {'path': '/machine/soc/peripherals/pcie',
                                                'property': 'connected-xhci'}) is connected
                output = root / ('on.dtb' if connected else 'off.dtb')
                qmp(m.monitor, 'dumpdtb', {'filename': str(output)})
                nodes = parse_fdt(output)
                assert ('/pcie' in nodes) is connected
                assert nodes['/genet']['compatible'] == b'brcm,bcm2711-genet-v5\0'
        print('PASS: actual arm_load_dtb retains PCIe only when connected and preserves native GENET')


def legacy_disconnected(binary, legacy):
    from test_emulator_adc_migration import Machine as ControlMachine
    with tempfile.TemporaryDirectory(prefix='uc-pcie-old-') as temporary, ExitStack() as stack:
        root = Path(temporary)
        source = ControlMachine(root, 'old', stack, base=0xfd500000, binary=legacy)
        target = ControlMachine(root, 'new', stack, incoming=True,
                                base=0xfd500000, binary=binary)
        source.write(0x9210, 1)
        source.write(0x9210, 0)
        source.write(0x4008, 0x90003000)
        source.write(0x4304, 1)
        source.write(0x4314, 1)
        migrate(source, target, root)
        assert target.read(0x4008) == 0x90003000
        assert target.read(0x4300) == 1
        assert target.read(0x4068) == 0x80
        print('PASS: older disconnected profile migrates unchanged into new controller')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', '--qemu-system', dest='binary')
    parser.add_argument('--legacy-qemu', help='optional older disconnected profile compatibility control')
    args = parser.parse_args()
    binary = args.binary or str(executable('qemu-system-aarch64'))
    run(binary)
    fdt_gate(binary)
    if args.legacy_qemu:
        legacy_disconnected(binary, args.legacy_qemu)
        incompatible_connected(binary, args.legacy_qemu)
