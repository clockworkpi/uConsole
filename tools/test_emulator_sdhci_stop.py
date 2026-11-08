#!/usr/bin/env python3
"""Real SD-card PIO: manual CMD12 must release SDHCI data/FIFO state, IRQ and reset."""
import argparse
from pathlib import Path
import subprocess
import tempfile

from test_emulator_pmic import connect
from uconsole_emulator import executable, qmp

BASE = 0xfe340000
DATA_FLAGS = 0x00000f06


def pattern(block):
    return bytes(((block * 13 + n) & 255) for n in range(512))


def run(binary):
    with tempfile.TemporaryDirectory(prefix='uc-sdhci-stop-') as temporary:
        root = Path(temporary)
        image = root / 'sd.img'
        with image.open('wb') as stream:
            for block in range(64):
                stream.write(pattern(block))
            stream.truncate(8 * 1024 * 1024)
        with (root / 'qemu.log').open('wb') as log:
            process = subprocess.Popen([
                binary, '-M', 'raspi4b', '-accel', 'qtest', '-S',
                '-display', 'none', '-serial', 'none', '-monitor', 'none',
                '-drive', f'if=none,id=card,format=raw,file={image}',
                '-device', 'sd-card,bus=/generic-sdhci/sd-bus,drive=card',
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

                    def read(off, width='l'):
                        return int(command(f'read{width} {BASE + off:#x}').split()[1], 16)

                    def write(off, value, width='l'):
                        command(f'write{width} {BASE + off:#x} {value:#x}')

                    def irq():
                        # EMMC2 GIC SPI126 -> architectural interrupt158.
                        value = int(command('readl 0xff841210').split()[1], 16)
                        return bool(value & (1 << 30))

                    def card_cmd(index, arg=0, flags=0x1a, transfer=None):
                        write(0x30, 0xffffffff)
                        write(8, arg)
                        if transfer is not None:
                            write(0x0c, transfer, 'w')
                        write(0x0e, (index << 8) | flags, 'w')
                        assert not read(0x30) & 0xffff0000, (index, hex(read(0x30)))
                        assert read(0x30) & 1, (index, hex(read(0x30)))
                        return read(0x10)

                    def setup():
                        write(0x2f, 1, 'b')
                        write(0x29, 0x0f, 'b')
                        write(0x2c, 5, 'w')
                        write(0x34, 0xffffffff)
                        write(0x38, 0xffffffff)
                        assert read(0x24) & 0x10000, 'No real SD card attached'
                        card_cmd(0, flags=0)
                        card_cmd(8, 0x1aa)
                        for _ in range(4):
                            card_cmd(55)
                            ocr = card_cmd(41, 0x40ff8000, flags=2)
                            if ocr & (1 << 31):
                                break
                            command('clock_step 1000000000')
                        assert ocr & (1 << 31)
                        assert not ocr & (1 << 30), 'Fixture requires byte-addressed SDSC'
                        card_cmd(2, flags=1)
                        rca = card_cmd(3, flags=2) & 0xffff0000
                        card_cmd(7, rca, flags=3)
                        card_cmd(16, 512)
                        write(4, 512, 'w')
                        return rca

                    def fifo(size=512):
                        return b''.join(read(0x20).to_bytes(4, 'little') for _ in range(size // 4))

                    def single(block):
                        write(4, 512, 'w')
                        write(6, 1, 'w')
                        card_cmd(17, block * 512, flags=0x3a, transfer=0x10)
                        value = fifo()
                        assert read(0x24) & DATA_FLAGS == 0
                        return value

                    def stopped():
                        assert read(0x24) & DATA_FLAGS == 0, 'Manual CMD12 left data/FIFO state: ' + hex(read(0x24))
                        assert read(0x30) & 2, 'Missing transfer-complete status'
                        assert irq(), 'Missing transfer-complete GIC IRQ'
                        write(0x30, 0xffffffff)
                        assert not irq(), 'W1C failed to lower GIC IRQ'
                        # A second AutoCMD12 must not overwrite this register.
                        assert read(0x1c) == 0

                    rca = setup()
                    assert single(0) == pattern(0)
                    card_cmd(18, 2 * 512, flags=0x3a, transfer=0x30)
                    assert fifo() == pattern(2)
                    assert fifo() == pattern(3)
                    assert read(0x24) & 2
                    card_cmd(12, flags=0xdb)
                    stopped()
                    assert card_cmd(13, rca) & 0x1e00 == 0x800
                    assert single(7) == pattern(7)
                    # Abort a partially consumed FIFO; the next transfer must
                    # start at bytezero, not reuse stale buffered bytes.
                    card_cmd(18, 9 * 512, flags=0x3a, transfer=0x34)
                    assert fifo(16) == pattern(9)[:16]
                    card_cmd(12, flags=0xdb)
                    stopped()
                    assert single(11) == pattern(11)
                    # Automatic CMD12 with a bounded two-block transfer remains
                    # functional and releases the same data-state flags.
                    write(6, 2, 'w')
                    card_cmd(18, 12 * 512, flags=0x3a, transfer=0x36)
                    assert fifo() == pattern(12) and fifo() == pattern(13)
                    assert read(0x24) & DATA_FLAGS == 0
                    assert single(15) == pattern(15)
                    # Manual write stop commits complete blocks only, discards
                    # a partial FIFO and permits the next read command.
                    card_cmd(25, 20 * 512, flags=0x3a, transfer=0x20)
                    payload = pattern(99)
                    for n in range(0, 512, 4):
                        write(0x20, int.from_bytes(payload[n:n + 4], 'little'))
                    for n in range(4):
                        write(0x20, 0x11223344)
                    card_cmd(12, flags=0xdb)
                    stopped()
                    assert single(20) == payload
                    assert single(21) == pattern(21)
                    # ACMD22 has a 32-bit big-endian wire payload, independent
                    # of host byte order. The 128-block case is the firmware's
                    # 64-KiB NVRAM flush; byte-swapping it fabricates a 1-TiB
                    # buffer advance in its caller. Count only complete blocks.
                    for count in (1, 2, 128):
                        write(4, 512, 'w')
                        card_cmd(25, 32 * 512, flags=0x3a, transfer=0x20)
                        payload = pattern(123)
                        for _ in range(count):
                            for n in range(0, 512, 4):
                                write(0x20, int.from_bytes(payload[n:n + 4], 'little'))
                        write(0x20, 0x11223344)  # Partial next block must not count.
                        card_cmd(12, flags=0xdb)
                        stopped()
                        card_cmd(55, rca)
                        write(4, 4, 'w')
                        card_cmd(22, flags=0x3a, transfer=0x10)
                        wire = fifo(4)
                        assert wire == count.to_bytes(4, 'big'), (count, wire.hex(), int.from_bytes(wire, 'big'))
                        assert single(32 + count - 1) == payload
                    print('PASS: ACMD22 wire-endian complete-write counts 1, 2 and 128')
                    # Command-only/non-abort CMD13 cannot clear an active data
                    # phase. DATA software reset must clear the interrupted host.
                    card_cmd(18, 24 * 512, flags=0x3a, transfer=0x30)
                    assert fifo(16) == pattern(24)[:16]
                    card_cmd(13, rca)
                    assert read(0x24) & 2
                    write(0x2f, 4, 'b')
                    assert read(0x24) & DATA_FLAGS == 0
                    qmp(root / 'qmp', 'system_reset')
                    setup()
                    assert single(31) == pattern(31)
                    print('PASS: real SD PIO manual read/write CMD12, partial FIFO, AutoCMD12, GIC IRQ/W1C and reset')
                qmp(root / 'qmp', 'quit')
                assert process.wait(timeout=10) == 0
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', default=executable('qemu-system-aarch64'))
    args = parser.parse_args()
    run(args.qemu)
