#!/usr/bin/env python3
"""Exercise the experimental keyboard transport through actual DWC2 USB DMA.

No Linux boot or physical-device comparison is implied by this qtest.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from test_emulator_watchdog import connect
from uconsole_emulator import executable, qmp
from keyboard_reports import encode_trace


FIRMWARE_TRACE = '''matrix 4 2 1
run 10
matrix 4 2 0
run 10
matrix 7 2 1
run 10
matrix 1 0 1
run 10
matrix 1 0 0
run 10
matrix 0 2 1
run 10
matrix 0 2 0
run 10
matrix 7 2 0
run 10
switch 0
key 4 1
run 10
key 4 0
run 10
matrix 0 0 1
run 10
edge 3
edge 3
run 1
matrix 0 0 0
run 10
key 13 1
run 10
key 13 0
run 10
edge 1
run 20
'''


def firmware_reports(oracle):
    result = subprocess.run([str(oracle.resolve())], input=FIRMWARE_TRACE, text=True,
                            capture_output=True, check=True, timeout=20)
    reports = encode_trace(json.loads(line) for line in result.stdout.splitlines())
    if {bytes.fromhex(item['report_hex'])[0] for item in reports} != {1, 2, 3, 20}:
        raise ValueError('Firmware trace did not exercise all four HID report collections')
    return reports


class USBTest:
    def __init__(self, stream):
        self.stream = stream

    def command(self, command):
        self.stream.write((command + '\n').encode())
        while True:
            line = self.stream.readline().decode().strip()
            if line.startswith('IRQ '):
                continue
            assert line.startswith('OK'), line
            return line.split()[1:]

    def read(self, offset):
        return int(self.command(f'readl {0xfe980000 + offset:#x}')[0], 16)

    def write(self, offset, value):
        self.command(f'writel {0xfe980000 + offset:#x} {value:#x}')

    def reset(self):
        self.write(0x440, 0x1100)
        self.write(0x440, 0x1000)
        self.write(0x440, 0x100a)
        self.write(0x008, 0x21)

    def packet(self, *, endpoint=0, kind=0, incoming=False, pid=2, payload=b'', size=None):
        size = len(payload) if size is None else size
        if payload:
            self.command(f'write 0x1000 {len(payload)} 0x{payload.hex()}')
        self.write(0x508, 0xffffffff)
        self.write(0x514, 0x1000)
        self.write(0x510, (pid << 29) | (max(1, (size + 63) // 64) << 19) | size)
        self.write(0x500, 0x80000040 | (endpoint << 11) | (kind << 18) | (0x8000 if incoming else 0))
        status = self.read(0x508)
        for _ in range(20):
            if status:
                break
            self.command('clock_step 1000000')
            status = self.read(0x508)
        remaining = self.read(0x510) & 0x7ffff
        data = b''
        if incoming and status & 1 and size > remaining:
            data = bytes.fromhex(self.command(f'read 0x1000 {size - remaining}')[0][2:])
        return status, data

    def control(self, request, value=0, index=0, length=0, payload=b'', *, stall=False):
        setup = struct.pack('<BBHHH', request >> 8, request & 255, value, index, length)
        status, _ = self.packet(pid=3, payload=setup)
        # IN control handlers run on SETUP; OUT handlers run after data/status.
        if stall and status & 8:
            return
        assert status & 1, f'SETUP failed: {status:#x}'
        result = b''
        if length:
            status, result = self.packet(incoming=bool(request & 0x8000),
                                         payload=payload, size=length)
            if stall and status & 8:
                return
            assert status & 1, f'DATA failed: {status:#x}'
        status, _ = self.packet(incoming=not bool(request & 0x8000))
        if stall:
            assert status & 8, f'Expected STALL, got {status:#x}'
        else:
            assert status & 1, f'STATUS failed: {status:#x}'
        return result


def exercise(reset_sequence, trace_reports=()):
    with tempfile.TemporaryDirectory(prefix='uc-keyboard-usb-') as directory:
        root = Path(directory)
        with (root / 'qemu.log').open('wb') as log:
            process = subprocess.Popen([
                executable('qemu-system-aarch64'), '-M', 'raspi4b', '-accel', 'qtest',
                '-display', 'none', '-serial', 'none', '-monitor', 'none',
                '-device', 'usb-uconsole-keyboard,id=deck,port=1',
                '-qtest', f'unix:{root}/test,server=on,wait=off',
                '-qmp', f'unix:{root}/qmp,server=on,wait=off'], stdout=log, stderr=log)
            try:
                with connect(root / 'test', process) as sock:
                    usb = USBTest(sock.makefile('rwb', buffering=0))
                    usb.reset()
                    device = usb.control(0x8006, 0x0100, length=18)
                    assert device == bytes.fromhex('1201000200000140af1e2400000201020301'), device.hex()
                    config = usb.control(0x8006, 0x0200, length=255)
                    assert len(config) == 100, config.hex()
                    assert config[:9] == bytes.fromhex('09026400030100c032')
                    assert config[34:42] == bytes.fromhex('080b010202020102'), config.hex()
                    assert config.hex() == (
                        '09026400030100c03209040000010301000009211001000122db00'
                        '0705810340000a080b0102020201020904010001020201000524000110'
                        '0524010302042402060524060102070583031000ff09040200020a000000'
                        '0705020240000007058202400000')
                    report = usb.control(0x8106, 0x2200, length=1024)
                    assert hashlib.sha256(report).hexdigest() == (
                        '351629b59fd36f6dc0b2aea094a207e232e668a0eff8a5bb38caa2d9a7c297b7')
                    for index, expected in ((1, 'ClockworkPI'), (2, 'uConsole'), (3, '20230713')):
                        data = usb.control(0x8006, 0x0300 | index, index=0x0409, length=255)
                        assert data[2:].decode('utf-16le') == expected
                    usb.control(0x0009, 1)
                    assert usb.control(0x8008, length=1) == b'\x01'
                    assert usb.control(0xa121, index=1, length=7) == bytes.fromhex('00c20100000008')
                    usb.control(0x2120, index=1, length=7, payload=bytes.fromhex('80250000000008'))
                    assert usb.control(0xa121, index=1, length=7) == bytes.fromhex('80250000000008')
                    usb.control(0xa121, index=2, length=7, stall=True)

                    def monitor(command, **arguments):
                        return qmp(root / 'qmp', command, arguments)

                    def state():
                        return monitor('qom-get', path='/machine/peripheral/deck', property='transport-state')

                    def inject(report):
                        return monitor('qom-set', path='/machine/peripheral/deck', property='inject-report', value=report)

                    def refused(report):
                        before = state()
                        try:
                            inject(report)
                        except ValueError:
                            pass
                        else:
                            raise AssertionError(f'Invalid report accepted: {report}')
                        assert state() == before, 'Rejected report changed transport state'

                    for item in trace_reports:
                        inject(item['report_hex'])
                        status, data = usb.packet(endpoint=1, kind=3, incoming=True, size=64)
                        assert status & 1 and data.hex() == item['report_hex'], (status, data.hex(), item)
                    for malformed in ('', '02', '0200', '02' + '00' * 9, 'aa0000', '03gg00', '03000'):
                        refused(malformed)
                    for report in ('010001ff00', '020000040000000000', '03e900', '14' + '00' * 12):
                        inject(report)
                        status, data = usb.packet(endpoint=1, kind=3, incoming=True, size=64)
                        assert status & 1 and data.hex() == report, (status, data.hex(), report)
                    status, _ = usb.packet(endpoint=1, kind=3, incoming=True, size=64)
                    assert status & 0x10, f'Empty queue did not NAK: {status:#x}'
                    usb.control(0x2109, 0x0202, length=2, payload=b'\x02\x05')
                    assert 'leds=5' in state(), state()
                    usb.control(0x2109, 0x0203, length=2, payload=b'\x03\x00', stall=True)
                    assert 'leds=5' in state(), state()
                    for value in range(32):
                        inject(f'03{value:02x}00')
                    refused('030000')
                    for value in range(32):
                        status, data = usb.packet(endpoint=1, kind=3, incoming=True, size=64)
                        assert status & 1 and data == bytes((3, value, 0)), (status, data)
                    inject('030000')
                    usb.reset()
                    assert 'queued=0' in state(), state()
                    refused('030000')  # Bus reset removes configuration/authority to inject.
                    usb.control(0x0009, 1)
                    assert usb.control(0xa121, index=1, length=7) == bytes.fromhex('80250000000008')
                    if reset_sequence == 'magic':
                        status, _ = usb.packet(endpoint=2, kind=2, pid=0, payload=b'1EAF')
                        assert status & 1 and 'reset-requested=0' in state(), state()
                        usb.reset()
                        usb.control(0x0009, 1)
                        usb.control(0x2122, 1, index=1)
                        usb.control(0x2122, 0, index=1)
                        for data in (b'1', b'EAF'):
                            status, _ = usb.packet(endpoint=2, kind=2, pid=0, payload=data)
                            assert status & 1 and 'reset-requested=0' in state(), state()
                        # The first short RX consumed the edge, so arm it anew.
                        usb.reset()
                        usb.control(0x0009, 1)
                        usb.control(0x2122, 1, index=1)
                        usb.control(0x2122, 0, index=1)
                        status, _ = usb.packet(endpoint=2, kind=2, pid=0, payload=b'1EAF')
                        assert status & 1, status
                        expected_used = 196
                    else:
                        usb.control(0x2120, index=1, length=7, payload=bytes.fromhex('b0040000000008'))
                        usb.control(0x2122, 1, index=1)
                        assert 'reset-requested=0' in state(), state()
                        usb.control(0x2122, 0, index=1)
                        expected_used = 192
                    assert 'reset-requested=1' in state(), state()
                    for _ in range(3):
                        status, _ = usb.packet(endpoint=2, kind=2, pid=0, payload=b'x' * 64)
                        assert status & 1, status
                    status, _ = usb.packet(endpoint=2, kind=2, pid=0, payload=b'x')
                    assert status & 0x10, f'Full CDC buffer did not NAK: {status:#x}'
                    assert f'cdc-rx={expected_used}' in state(), state()
                    print(f'PASS ({reset_sequence}): composite HID/CDC descriptors, report DMA, LEDs, line coding and reset request')
                    if trace_reports:
                        print(f'PASS: {len(trace_reports)} firmware-derived reports traversed DWC2 DMA (ordering/bytes, not real-time pacing)')
                    print('LIMIT: reset request is diagnostic only; DFU transition and live host input mapping are not implemented')
            except BaseException:
                print((root / 'qemu.log').read_text()[-8000:])
                raise
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--oracle', type=Path, help='also replay encoded production-firmware input trace')
    options = cli.parse_args()
    reports = firmware_reports(options.oracle) if options.oracle else ()
    for sequence in ('magic', '1200'):
        exercise(sequence, reports)
