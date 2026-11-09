#!/usr/bin/env python3
"""Exercise DWC2 keyboard remote wakeup through real USB DMA and QMP input."""
from pathlib import Path
import subprocess
import tempfile
import uuid

from test_emulator_watchdog import connect
from uconsole_emulator import executable, qmp


def main():
    with tempfile.TemporaryDirectory(prefix='uc-usb-wake-') as directory:
        root = Path(directory)
        identity = 'usb-wake-' + uuid.uuid4().hex
        with (root / 'qemu.log').open('wb') as log:
            process = subprocess.Popen([
                executable('qemu-system-aarch64'), '-M', 'raspi4b',
                '-accel', 'qtest', '-display', 'none', '-serial', 'none',
                '-monitor', 'none', '-name', identity, '-device', 'usb-kbd,port=1',
                '-qtest', f'unix:{root}/test,server=on,wait=off',
                '-qmp', f'unix:{root}/qmp,server=on,wait=off'],
                stdout=log, stderr=log)
            try:
                with connect(root / 'test', process) as sock:
                    stream = sock.makefile('rwb', buffering=0)

                    def test(command):
                        stream.write((command + '\n').encode())
                        while True:
                            line = stream.readline().decode().strip()
                            if line.startswith('IRQ '):
                                continue
                            assert line.startswith('OK'), line
                            return line.split()[1:]

                    def read(offset):
                        return int(test(f'readl {0xfe980000 + offset:#x}')[0], 16)

                    def write(offset, value):
                        test(f'writel {0xfe980000 + offset:#x} {value:#x}')

                    def control(command, arguments=None):
                        assert qmp(root / 'qmp', 'query-name')['name'] == identity
                        return qmp(root / 'qmp', command, arguments)

                    # Reset the attached keyboard, enable DMA, acknowledge
                    # connection/enable changes, then enable remote wakeup
                    # using SET_FEATURE on endpoint zero (not a model hook).
                    write(0x440, 0x1100)
                    write(0x440, 0x1000)
                    write(0x440, 0x100a)
                    write(0x008, 0x21)
                    write(0x018, 0x81000000)
                    test('write 0x1000 8 0x0003010000000000')
                    write(0x514, 0x1000)
                    write(0x510, 0x60080008)
                    write(0x500, 0x80000040)
                    assert read(0x508) & 1, 'SET_FEATURE setup failed'
                    write(0x508, 0xffffffff)
                    write(0x510, 0x40080000)
                    write(0x500, 0x80008040)
                    assert read(0x508) & 1, 'SET_FEATURE status failed'
                    write(0x508, 0xffffffff)
                    assert not read(0x014) & 0x81000000
                    write(0x440, 0x1080)
                    assert read(0x440) & 0x80
                    control('cont')
                    control('input-send-event', {'events': [{'type': 'key', 'data': {
                        'down': True, 'key': {'type': 'qcode', 'data': 'a'}}}]})
                    test('clock_step 1000000')
                    status, port = read(0x014), read(0x440)
                    assert status & 0x80000000, f'No wakeup interrupt: {status:#x}, {port:#x}'
                    assert not status & 0x01000000, f'Spurious port interrupt: {status:#x}'
                    assert port & 0x40, f'No resume signaling: {port:#x}'
                    # The Linux wakeup handler acknowledges WKUPINT, then its
                    # timer clears RES after the USB resume signaling period.
                    write(0x014, 0x80000000)
                    test('clock_step 71000000')
                    write(0x440, 0x1000)
                    assert not read(0x440) & 0xc0, 'Resume/suspend bits stuck'
                    assert not read(0x014) & 0x81000000
                    control('input-send-event', {'events': [{'type': 'key', 'data': {
                        'down': False, 'key': {'type': 'qcode', 'data': 'a'}}}]})
                    assert not read(0x014) & 0x81000000, 'Awake key release raised wake IRQ'
                    control('query-status')
                    print('PASS: DWC2 USB keyboard remote wakeup and resume acknowledgement')
            except Exception:
                print((root / 'qemu.log').read_text())
                raise
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    main()
