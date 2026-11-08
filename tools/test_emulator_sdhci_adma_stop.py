#!/usr/bin/env python3
"""Real PCI SDHCI DMA: pending ADMA abort, stale SDMA address and live resume.

Use virt PCI rather than the Pi model, whose SDHCI capabilities disable DMA.
The controls inspect guest RAM and card data, not only register echoes.
"""
import argparse
from contextlib import contextmanager
from pathlib import Path
import struct
import subprocess
import tempfile

from test_emulator_pmic import connect
from uconsole_emulator import executable, qmp

BASE = 0x10000000
ECAM = 0x3f008000
TABLE = 0x40010000
BUFFER = 0x40100000
GUARD = 0x40300000
FLAGS = 0x00000f06


def pattern(block):
    return bytes(((block * 13 + n) & 255) for n in range(512))


@contextmanager
def machine(binary):
    with tempfile.TemporaryDirectory(prefix='uc-sdhci-dma-') as directory:
        root = Path(directory)
        image = root / 'sd.img'
        with image.open('wb') as stream:
            for block in range(256):
                stream.write(pattern(block))
            stream.truncate(8 * 1024 * 1024)
        with (root / 'qemu.log').open('wb') as log:
            process = subprocess.Popen([
                binary, '-nodefaults', '-nic', 'none', '-M', 'virt,highmem=off',
                '-accel', 'qtest', '-S', '-display', 'none', '-serial', 'none',
                '-monitor', 'none', '-device', 'sdhci-pci,id=host,addr=1.0',
                '-drive', f'if=none,id=card,format=raw,file={image}',
                '-device', 'sd-card,drive=card',
                '-qtest', f'unix:{root}/test,server=on,wait=off',
                '-qmp', f'unix:{root}/qmp,server=on,wait=off',
            ], stdout=log, stderr=log)
            try:
                with connect(root / 'test', process) as sock:
                    stream = sock.makefile('rwb', buffering=0)

                    def command(text):
                        sock.sendall((text + '\n').encode())
                        while True:
                            result = stream.readline().decode().strip()
                            if result.startswith('IRQ '):
                                continue
                            assert result.startswith('OK'), result
                            return result

                    class Host:
                        def read(self, off, width='l'):
                            return int(command(f'read{width} {BASE + off:#x}').split()[1], 16)

                        def write(self, off, value, width='l'):
                            command(f'write{width} {BASE + off:#x} {value:#x}')

                        def memory(self, addr, data):
                            command(f'write {addr:#x} {len(data):#x} 0x{data.hex()}')

                        def inspect(self, addr, size):
                            return bytes.fromhex(command(f'read {addr:#x} {size:#x}').split()[1][2:])

                        def step(self):
                            command('clock_step 1000000')

                        def card(self, index, arg=0, flags=0x1a):
                            self.write(0x30, 0xffffffff)
                            self.write(8, arg)
                            self.write(0x0e, (index << 8) | flags, 'w')
                            status = self.read(0x30)
                            assert not status & 0xffff0000, (index, hex(status))
                            assert status & 1, (index, hex(status))
                            return self.read(0x10)

                    host = Host()
                    vendor = int(command(f'readl {ECAM:#x}').split()[1], 16)
                    assert vendor == 0x00071b36, hex(vendor)
                    command(f'writel {ECAM + 0x10:#x} {BASE:#x}')
                    command(f'writew {ECAM + 4:#x} 6')
                    assert host.read(0x40) & (1 << 19), 'PCI fixture must advertise ADMA2'
                    assert host.read(0x40) & (1 << 22), 'PCI fixture must advertise SDMA'
                    host.write(0x2f, 1, 'b')
                    host.write(0x29, 0x0f, 'b')
                    host.write(0x2c, 5, 'w')
                    host.write(0x34, 0xffffffff)
                    host.write(0x38, 0xffffffff)
                    assert host.read(0x24) & 0x10000
                    host.card(0, flags=0)
                    host.card(8, 0x1aa)
                    for _ in range(4):
                        host.card(55)
                        ocr = host.card(41, 0x40ff8000, flags=2)
                        if ocr & (1 << 31):
                            break
                        command('clock_step 1000000000')
                    assert ocr & (1 << 31) and not ocr & (1 << 30)
                    host.card(2, flags=1)
                    rca = host.card(3, flags=2) & 0xffff0000
                    host.card(7, rca, flags=3)
                    host.card(16, 512)
                    host.write(4, 512, 'w')
                    yield host
                qmp(root / 'qmp', 'quit')
                assert process.wait(timeout=10) == 0
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)


def adma_abort(binary, reset=False):
    with machine(binary) as host:
        count = 128
        sentinel = bytes([0xa5]) * (count * 512)
        host.memory(BUFFER, sentinel)
        descriptors = b''.join(struct.pack('<HHI', 0x21 | (2 if n == count - 1 else 0),
                                            512, BUFFER + n * 512) for n in range(count))
        host.memory(TABLE, descriptors)
        host.write(0x28, 0x10, 'b')  # ADMA2, 32-bit descriptors.
        host.write(0x58, TABLE)
        host.write(6, count, 'w')
        host.write(8, 0)
        host.write(0x0c, 0x33, 'w')  # DMA, READ, MULTI, block-count enabled.
        host.write(0x0e, (18 << 8) | 0x3a, 'w')
        before = host.inspect(BUFFER, len(sentinel))
        assert before[:512] == pattern(0), 'No actual ADMA card data transferred'
        assert before[-512:] == sentinel[-512:], 'Fixture must yield with descriptors still pending'
        # Do not read controller registers here: MMIO accesses resume pending
        # ADMA synchronously. CMD12's own write resumes one batch, then aborts.
        if reset:
            host.write(0x2f, 4, 'b')  # DATA reset must cancel the same callback.
        else:
            host.write(0x0e, (12 << 8) | 0xdb, 'w')
        stopped = host.inspect(BUFFER, len(sentinel))
        assert stopped[-512:] == sentinel[-512:]
        host.step()
        # Register polling also resumes a pending ADMA callback in this model.
        # Neither timer expiry nor the next status read may resurrect it.
        state = host.read(0x24)
        after = host.inspect(BUFFER, len(sentinel))
        assert after == stopped, 'Pending ADMA callback wrote guest RAM after ' + ('DATA reset' if reset else 'CMD12')
        assert state & FLAGS == 0, 'Pending ADMA callback reasserted aborted transfer state'
        print('PASS: actual pending ADMA read aborted by ' + ('DATA reset' if reset else 'CMD12') + '; no post-abort guest-memory writes or active state')


def start_sdma(host, blocks=32):
    host.write(0x28, 0, 'b')
    host.write(4, 512, 'w')  # 4 KiB SDMA boundary.
    host.write(0, BUFFER)
    host.write(6, blocks, 'w')
    host.write(0x0c, 0x33, 'w')
    host.card(18, 0, flags=0x3a)
    assert host.inspect(BUFFER, 4096) == b''.join(pattern(n) for n in range(8))
    assert host.read(6, 'w') == blocks - 8
    assert host.read(0x24) & 2


def sdma_stale(binary):
    with machine(binary) as host:
        sentinel = bytes([0xa5]) * 4096
        host.memory(GUARD, sentinel)
        start_sdma(host)
        host.card(12, flags=0xdb)
        assert host.read(6, 'w') == 24, 'Remaining count should remain observable after abort'
        host.write(0, GUARD)
        host.step()
        assert host.inspect(GUARD, 4096) == sentinel, 'Stale SYSAD write resumed aborted SDMA into guest RAM'
        assert host.read(0x24) & FLAGS == 0, 'Stale SYSAD write reasserted aborted SDMA state'
        assert host.read(0) == GUARD
        print('PASS: post-CMD12 SDMA address write preserves remaining count and cannot restart DMA')


def sdma_resume(binary):
    with machine(binary) as host:
        sentinel = bytes([0xa5]) * 4096
        host.memory(GUARD, sentinel)
        start_sdma(host)
        host.write(0x30, 0xffffffff)
        host.write(0, GUARD)
        assert host.inspect(GUARD, 4096) == b''.join(pattern(n) for n in range(8, 16)), 'Active SDMA boundary address write did not resume actual data transfer'
        assert host.read(6, 'w') == 16
        assert host.read(0x24) & 2
        assert host.read(0x30) & 8, 'No SDMA boundary interrupt on resumed transfer'
        host.card(12, flags=0xdb)
        assert host.read(0x24) & FLAGS == 0
        print('PASS: active SDMA boundary address write resumes real card data and advances remaining count')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', default=executable('qemu-system-aarch64'))
    parser.add_argument('--case', choices=('all', 'adma', 'adma-reset', 'sdma-stale', 'sdma-resume'), default='all')
    args = parser.parse_args()
    for name, case in (('adma', adma_abort), ('adma-reset', lambda binary: adma_abort(binary, reset=True)),
                       ('sdma-stale', sdma_stale), ('sdma-resume', sdma_resume)):
        if args.case in ('all', name):
            case(args.qemu)
