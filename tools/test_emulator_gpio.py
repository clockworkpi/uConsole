#!/usr/bin/env python3
"""BCM2711 digital GPIO inputs, event detection and interrupt group qtests."""
from pathlib import Path
import subprocess
import tempfile

from test_emulator_pmic import connect
from uconsole_emulator import executable, qmp


BASE = 0xfe200000
GPIO = '/machine/soc/peripherals/gpio'


def main():
    with tempfile.TemporaryDirectory(prefix='uc-gpio-qtest-') as directory:
        directory = Path(directory)
        with (directory / 'qemu.log').open('wb') as log:
            process = subprocess.Popen([
                executable('qemu-system-aarch64'), '-M', 'raspi4b', '-accel', 'qtest',
                # Register tests do not execute guest instructions. Keep dummy
                # CPUs paused, including during system_reset (Darwin race).
                '-S', '-display', 'none', '-serial', 'none', '-monitor', 'none',
                '-qtest', f'unix:{directory}/test,server=on,wait=off',
                '-qmp', f'unix:{directory}/qmp,server=on,wait=off'], stdout=log, stderr=log)
            try:
                with connect(directory / 'test', process) as sock:
                    stream = sock.makefile('rwb', buffering=0)
                    irqs = {}

                    def command(text):
                        stream.write((text + '\n').encode())
                        while True:
                            line = stream.readline().decode().strip()
                            if line.startswith('IRQ '):
                                _, action, number = line.split()
                                irqs[int(number)] = action == 'raise'
                                continue
                            assert line.startswith('OK'), line
                            return line

                    def write(offset, value):
                        command(f'writel {BASE + offset:#x} {value:#x}')

                    def read(offset):
                        return int(command(f'readl {BASE + offset:#x}').split()[1], 16)

                    def input_level(pin, level):
                        command(f'set_irq_in {GPIO} unnamed-gpio-in {pin} {level}')

                    def gic_pending(group):
                        # SPI 113..116 have architectural IDs 145..148.
                        return int(command('readl 0xff841210').split()[1], 16) & (1 << (17 + group))

                    command(f'irq_intercept_out {GPIO} sysbus-irq')
                    for pin in (0, 2, 27, 28, 31, 32, 45, 46, 53, 54, 57):
                        bank, bit = pin // 32, 1 << (pin % 32)
                        group = 0 if pin < 28 else (1 if pin < 46 else 2)
                        # Both edge modes, including asynchronous detectors.
                        for rising, falling in ((0x4c, 0x58), (0x7c, 0x88)):
                            input_level(pin, 0)
                            write(rising + 4 * bank, bit)
                            write(falling + 4 * bank, bit)
                            input_level(pin, 1)
                            assert read(0x34 + 4 * bank) & bit
                            assert read(0x40 + 4 * bank) == bit
                            assert irqs.get(group) and irqs.get(3), irqs
                            assert gic_pending(group) and gic_pending(3)
                            assert all(not irqs.get(other, False) for other in range(3) if other != group)
                            write(0x40 + 4 * bank, bit)
                            assert read(0x40 + 4 * bank) == 0
                            assert not irqs[group] and not irqs[3]
                            input_level(pin, 0)
                            assert read(0x40 + 4 * bank) == bit
                            write(rising + 4 * bank, 0)
                            write(falling + 4 * bank, 0)
                            write(0x40 + 4 * bank, bit)
                        # Level conditions cannot be cleared while active.
                        for offset, level in ((0x64, 1), (0x70, 0)):
                            input_level(pin, level)
                            write(offset + 4 * bank, bit)
                            write(0x40 + 4 * bank, bit)
                            assert read(0x40 + 4 * bank) == bit
                            input_level(pin, 1 - level)
                            write(0x40 + 4 * bank, bit)
                            assert read(0x40 + 4 * bank) == 0
                            write(offset + 4 * bank, 0)
                        input_level(pin, 0)

                    # Reserved upper-bank bits never become events/enables.
                    write(0x50, 0xffffffff)
                    assert read(0x50) == 0x03ffffff
                    write(0x50, 0)
                    # Output latches must not drive inputs; pins 54..57 exist.
                    pin, bit = 57, 1 << 25
                    write(0x20, bit)
                    assert read(0x38) & bit == 0
                    write(0x14, 1 << 21)
                    assert read(0x38) & bit
                    input_level(pin, 0)
                    assert read(0x38) & bit
                    write(0x2c, bit)
                    assert read(0x38) & bit == 0
                    write(0x14, 0)
                    input_level(2, 1)
                    write(0x64, 4)
                    assert read(0x40) == 4
                    qmp(directory / 'qmp', 'system_reset')
                    assert read(0x34) & 4  # External input survives reset.
                    assert read(0x40) == read(0x64) == 0
                    assert not any(irqs.values()), irqs
                    print('PASS: BCM2711 GPIO edges, levels, W1C, IRQ groups, upper pins, latches and reset')
            except Exception:
                print((directory / 'qemu.log').read_text())
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)


if __name__ == '__main__':
    main()
