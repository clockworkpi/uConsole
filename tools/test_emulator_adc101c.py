#!/usr/bin/env python3
"""Exercise ADC101C through BCM2835 I2C1 and QEMU virtual time."""
from pathlib import Path
import subprocess
import tempfile

from test_emulator_pmic import connect
from uconsole_emulator import executable, qmp


def main():
    with tempfile.TemporaryDirectory(prefix='uc-adc-') as temporary:
        root = Path(temporary)
        with (root / 'qemu.log').open('wb') as log:
            process = subprocess.Popen([
                executable('qemu-system-aarch64'), '-M', 'raspi4b', '-accel', 'qtest',
                '-S', '-display', 'none', '-serial', 'none', '-monitor', 'none',
                '-qtest', f'unix:{root}/test,server=on,wait=off',
                '-qmp', f'unix:{root}/qmp,server=on,wait=off'], stdout=log, stderr=log)
            try:
                with connect(root / 'test', process) as sock, sock.makefile('rwb', buffering=0) as stream:
                    def command(value):
                        stream.write((value + '\n').encode())
                        while True:
                            line = stream.readline().decode().strip()
                            if line.startswith('IRQ '):
                                continue
                            assert line.startswith('OK'), line
                            return line

                    def write(offset, value):
                        command(f'writel {0xfe804000 + offset:#x} {value:#x}')

                    def read(offset):
                        return int(command(f'readl {0xfe804000 + offset:#x}').split()[1], 16)

                    def send(values):
                        write(4, 0x102)
                        write(12, 0x54)
                        write(8, len(values))
                        write(0, 0x8080)
                        for value in values:
                            write(16, value)
                        return read(4)

                    def reg_write(reg, value, width=2):
                        values = [reg] + ([value >> 8, value & 255] if width == 2 else [value])
                        status = send(values)
                        assert status & 2 and not status & 0x100, (reg, status)

                    def reg_read(reg, width=2):
                        assert not send([reg]) & 0x100
                        write(4, 2)
                        write(8, width)
                        write(0, 0x8081)
                        value = 0
                        for _ in range(width):
                            value = (value << 8) | read(16)
                        assert not read(4) & 0x100
                        return value

                    def get(name):
                        return qmp(root / 'qmp', 'qom-get', {'path': '/machine/battery-adc', 'property': name})

                    def set_value(name, value):
                        qmp(root / 'qmp', 'qom-set', {'path': '/machine/battery-adc',
                                                     'property': name, 'value': value})
                        assert get(name) == value

                    assert get('reference-uv') == 3300000
                    assert get('reference-supply-present') is True
                    try:
                        set_value('reference-supply-present', False)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError('Boot reference profile changed after realization')
                    assert get('reference-supply-present') is True
                    assert reg_read(4) == reg_read(6) == 0xffc
                    assert reg_read(0) == 0
                    set_value('input-uv', 1650000)
                    command('clock_step 399')
                    assert get('conversion-word') == 0
                    command('clock_step 1')
                    set_value('input-uv', 0)
                    command('clock_step 999')
                    assert get('conversion-word') == 0
                    command('clock_step 1')
                    assert get('conversion-word') == 0x800
                    assert reg_read(6) == 0xffc and reg_read(7) == 0
                    for invalid in (-1, 3300001):
                        try:
                            set_value('input-uv', invalid)
                        except ValueError:
                            pass
                        else:
                            raise AssertionError('Invalid voltage accepted')
                    assert get('input-uv') == 0
                    reg_write(3, 400 * 4)
                    reg_write(4, 600 * 4)
                    reg_write(5, 10 * 4)
                    reg_write(2, 0x3c, 1)  # automatic, hold, flag and pin
                    command('clock_step 1400')
                    assert reg_read(1, 1) == 1
                    assert get('alert-enabled') and not get('alert-level')
                    assert get('conversion-word') == 0x8000
                    set_value('input-uv', 3300000)
                    command('clock_step 32000')
                    assert reg_read(1, 1) == 3
                    assert reg_read(6) == 0 and reg_read(7) == 0xffc
                    reg_write(1, 1, 1)
                    assert reg_read(1, 1) == 2
                    reg_write(1, 2, 1)
                    assert reg_read(1, 1) == 0
                    command('clock_step 32000')
                    assert reg_read(1, 1) == 2
                    reg_write(2, 0x2d, 1)  # self-clear, active high
                    set_value('input-uv', 1650000)
                    command('clock_step 32000')
                    assert reg_read(1, 1) == 0 and not get('alert-level')
                    assert send([8]) & 0x100
                    assert send([0, 255]) & 0x100
                    set_value('powered', False)
                    status = send([0])
                    assert status & 0x103 == 0x102, status  # ERR + DONE, never TA
                    command('clock_step 100000')
                    assert get('conversion-word') == 0
                    set_value('powered', True)
                    assert reg_read(4) == reg_read(6) == 0xffc
                    assert reg_read(2, 1) == 0
                    reg_write(4, 0)
                    qmp(root / 'qmp', 'system_reset')
                    assert reg_read(4) == 0xffc
                    assert get('input-uv') == 1650000
                    print('PASS: ADC101C I2C1 registers, virtual sample/hold timing, '
                          'limits/hold/W1C/extrema, alert state, NACKs, power loss and reset')
            except BaseException:
                print((root / 'qemu.log').read_text())
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == '__main__':
    main()
