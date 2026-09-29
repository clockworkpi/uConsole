#!/usr/bin/env python3
"""Exercise the uConsole AXP221 through the emulated BCM2835 I2C controller."""
from pathlib import Path
import json
import socket
import subprocess
import tempfile
import time

from uconsole_emulator import executable, qmp


I2C = 0xFE205000
C, S, DLEN, A, FIFO = (0x00, 0x04, 0x08, 0x0C, 0x10)
I2CEN, ST, READ = (1 << 15), (1 << 7), 1
DONE, ERR = (1 << 1), (1 << 8)


def connect(path, process):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.connect(str(path))
            sock.settimeout(5)
            return sock
        except OSError:
            sock.close()
            if process.poll() is not None:
                raise RuntimeError('QEMU exited before opening qtest socket')
            time.sleep(0.05)
    raise TimeoutError(path)


def main(thermal_shutdown=False, fault_after_enable=False):
    with tempfile.TemporaryDirectory(prefix='uc-pmic-qtest-') as directory:
        directory = Path(directory)
        with (directory / 'qemu.log').open('wb') as log:
            process = subprocess.Popen([
                executable('qemu-system-aarch64'), '-M', 'raspi4b',
                # Register tests have no guest instructions to execute. Keep
                # dummy CPUs paused; reset still exercises all device state.
                '-accel', 'qtest', '-S', '-display', 'none', '-serial', 'none',
                '-monitor', 'none', '-qtest',
                f'unix:{directory}/test.sock,server=on,wait=off',
                '-qmp', f'unix:{directory}/qmp.sock,server=on,wait=off',
            ], stdout=log, stderr=log)
            try:
                with connect(directory / 'test.sock', process) as sock:
                    test = sock.makefile('rwb', buffering=0)
                    irq_events = []

                    def qtest(command):
                        test.write((command + '\n').encode())
                        while True:
                            line = test.readline().decode().strip()
                            if line.startswith('IRQ '):
                                irq_events.append(line)
                                continue
                            assert line.startswith('OK'), line
                            return line

                    def write(offset, value):
                        qtest(f'writel 0x{I2C + offset:x} 0x{value:x}')

                    def read(offset):
                        return int(qtest(f'readl 0x{I2C + offset:x}').split()[1], 16)

                    def reg_write(register, value):
                        write(S, DONE | ERR)
                        write(A, 0x34)
                        write(DLEN, 2)
                        write(C, I2CEN | ST)
                        write(FIFO, register)
                        write(FIFO, value)
                        assert read(S) & DONE and not read(S) & ERR

                    def reg_read(register):
                        write(S, DONE | ERR)
                        write(A, 0x34)
                        write(DLEN, 1)
                        write(C, I2CEN | ST)
                        write(FIFO, register)
                        write(S, DONE)
                        write(C, I2CEN | ST | READ)
                        return read(FIFO)

                    endpoint = directory / 'qmp.sock'
                    children = qmp(endpoint, 'qom-list', {'path': '/machine/unattached'})
                    pmics = [child for child in children if child['type'] == 'child<axp221_pmu>']
                    assert len(pmics) == 1, children
                    pmic = '/machine/unattached/' + pmics[0]['name']
                    qtest(f'irq_intercept_out {pmic}')

                    def state(prop):
                        return qmp(endpoint, 'qom-get', {'path': pmic, 'property': prop})

                    def ac(present):
                        qmp(endpoint, 'qom-set', {'path': pmic, 'property': 'ac-present', 'value': present})
                        assert state('ac-present') is present

                    assert state('ac-present') is True
                    assert reg_read(0) == 0xc2
                    def mmio(address):
                        return int(qtest(f'readl {address:#x}').split()[1], 16)

                    # I2C DONE must reach the CM4 GIC, not only legacy IRQ53.
                    write(C, I2CEN | (1 << 8))
                    assert mmio(0xff841210) & (1 << 21)  # ID149 / SPI117.
                    write(S, DONE)
                    qtest('writel 0xff841290 0x200000')  # Clear GIC's latched pending bit.
                    assert not mmio(0xff841210) & (1 << 21)
                    write(C, I2CEN)
                    assert mmio(0xfe200034) & 4  # Deasserted active-low pin.
                    qtest('writel 0xfe200070 4')  # GPIO2 low-level detector.
                    reg_write(0x40, 0)
                    ac(False)
                    assert reg_read(0) == 0
                    assert reg_read(0x48) == 0x20
                    assert state('irq-active') is False
                    assert 'IRQ raise 0' not in irq_events
                    reg_write(0x40, 0x60)
                    assert state('irq-active') is True
                    assert irq_events[-1] == 'IRQ raise 0', irq_events
                    assert not mmio(0xfe200034) & 4
                    assert mmio(0xfe200040) & 4
                    assert mmio(0xff841210) & (1 << 17)  # GIC ID145 / SPI113.
                    qtest('writel 0xfe200040 4')
                    assert mmio(0xfe200040) & 4  # Held low: cannot acknowledge away.
                    reg_write(0x48, 0x40)  # Acknowledge a different bit.
                    assert reg_read(0x48) == 0x20
                    reg_write(0x48, 0x20)
                    assert reg_read(0x48) == 0 and state('irq-active') is False
                    assert irq_events[-1] == 'IRQ lower 0', irq_events
                    assert mmio(0xfe200034) & 4
                    qtest('writel 0xfe200040 4')
                    assert not mmio(0xfe200040) & 4
                    ac(False)  # Repeating the same state is not another edge.
                    assert reg_read(0x48) == 0
                    ac(True)
                    assert reg_read(0x48) == 0x40 and state('irq-active') is True
                    reg_write(0x40, 0)
                    assert state('irq-active') is False and reg_read(0x48) == 0x40
                    for register, expected in ((0, 0xc2), (1, 0), (3, 6)):
                        reg_write(register, 0xff)
                        assert reg_read(register) == expected
                    ac(False)
                    qmp(endpoint, 'system_reset')
                    assert state('ac-present') is False
                    assert reg_read(0x48) == 0 and state('irq-active') is False
                    ac(True)
                    reg_write(0x48, 0xff)

                    def battery(present):
                        qmp(endpoint, 'qom-set', {'path': pmic, 'property': 'battery-present',
                                                 'value': present})
                        assert state('battery-present') is present

                    assert state('battery-present') is False
                    for voltage in (0, 3300550, 4400000, 4504500):
                        qmp(endpoint, 'qom-set', {'path': pmic, 'property': 'battery-voltage-uv',
                                                'value': voltage})
                        raw = voltage // 1100
                        assert state('battery-voltage-uv') == raw * 1100
                        assert (reg_read(0x78) << 4 | reg_read(0x79)) == raw
                        reg_write(0x78, 0)
                        reg_write(0x79, 0xff)
                        assert (reg_read(0x78) << 4 | reg_read(0x79)) == raw
                    for voltage in (-1, 4504501):
                        try:
                            qmp(endpoint, 'qom-set', {'path': pmic, 'property': 'battery-voltage-uv',
                                                    'value': voltage})
                        except ValueError:
                            pass
                        else:
                            raise AssertionError('Out-of-range voltage accepted')
                        assert state('battery-voltage-uv') == 4504500
                    reg_write(0x41, 0)
                    battery(True)
                    assert reg_read(1) & 0x20
                    assert reg_read(0x49) == 0x80 and state('irq-active') is False
                    reg_write(0x41, 0xc0)
                    assert state('irq-active') is True
                    reg_write(0x49, 0x40)
                    assert reg_read(0x49) == 0x80
                    reg_write(0x49, 0x80)
                    assert state('irq-active') is False
                    battery(True)
                    assert reg_read(0x49) == 0
                    qmp(endpoint, 'system_reset')
                    assert state('battery-present') is True and reg_read(0x49) == 0
                    assert state('battery-voltage-uv') == 4504500
                    def set_value(prop, value):
                        qmp(endpoint, 'qom-set', {'path': pmic, 'property': prop, 'value': value})

                    def rejected(prop, value):
                        previous = state(prop)
                        try:
                            set_value(prop, value)
                        except ValueError:
                            pass
                        else:
                            raise AssertionError((prop, value))
                        assert state(prop) == previous

                    rejected('battery-current-ma', 100)  # Full battery.
                    assert state('pmic-temperature-mc') == 25000
                    assert (reg_read(0x56) << 4 | reg_read(0x57)) == 2927
                    for temperature in (-267700, -101, 0, 25999, 141800):
                        set_value('pmic-temperature-mc', temperature)
                        expected = temperature // 100 * 100
                        assert state('pmic-temperature-mc') == expected
                        raw = (expected + 267700) // 100
                        assert (reg_read(0x56) << 4 | reg_read(0x57)) == raw
                        reg_write(0x56, 0xff)
                        reg_write(0x57, 0xff)
                        assert state('pmic-temperature-mc') == expected
                        qmp(endpoint, 'system_reset')
                        assert state('pmic-temperature-mc') == expected
                    rejected('pmic-temperature-mc', -267701)
                    rejected('pmic-temperature-mc', 141801)
                    set_value('pmic-temperature-mc', 25000)
                    assert state('pmic-over-temperature') is False
                    reg_write(0x42, 0)
                    set_value('pmic-over-temperature', True)
                    assert reg_read(1) & 0x80 and reg_read(0x4a) == 0x80
                    assert state('irq-active') is False
                    assert state('pmic-temperature-mc') == 25000  # Independent fault injection.
                    reg_write(1, 0)
                    assert state('pmic-over-temperature') is True  # Read-only status.
                    reg_write(0x42, 0x80)
                    assert state('irq-active') is True
                    assert not mmio(0xfe200034) & 4  # Carrier active-low GPIO2.
                    reg_write(0x4a, 0x01)
                    assert reg_read(0x4a) == 0x80  # Unrelated W1C bit.
                    set_value('pmic-over-temperature', False)
                    assert not reg_read(1) & 0x80 and reg_read(0x4a) == 0x80
                    reg_write(0x4a, 0x80)
                    assert state('irq-active') is False and mmio(0xfe200034) & 4
                    set_value('pmic-over-temperature', True)
                    reg_write(0x4a, 0x80)
                    set_value('pmic-over-temperature', True)
                    assert reg_read(0x4a) == 0  # No synthetic repeated edge.
                    qmp(endpoint, 'system_reset')
                    assert state('pmic-over-temperature') is True
                    assert reg_read(0x4a) == 0 and state('irq-active') is False
                    set_value('pmic-over-temperature', False)
                    set_value('pmic-over-temperature', True)
                    assert reg_read(0x4a) == 0x80
                    set_value('pmic-over-temperature', False)
                    reg_write(0x4a, 0x80)
                    set_value('battery-capacity', 50)
                    assert reg_read(0xb9) == 0x80 | 50
                    reg_write(0xb9, 0)
                    assert reg_read(0xb9) == 0x80 | 50
                    reg_write(0x41, 0x0c)
                    set_value('battery-current-ma', 500)
                    assert reg_read(0) & 4 and reg_read(1) & 0x40
                    assert reg_read(0x49) == 8 and state('irq-active') is True
                    assert (reg_read(0x7a) << 4 | reg_read(0x7b)) == 500
                    reg_write(0x49, 8)
                    set_value('battery-current-ma', 500)
                    assert reg_read(0x49) == 0
                    set_value('battery-capacity', 100)
                    assert state('battery-current-ma') == 0 and not reg_read(0) & 4
                    assert reg_read(0x49) == 4 and state('irq-active') is True
                    reg_write(0x49, 4)
                    set_value('battery-capacity', 50)
                    set_value('battery-current-ma', -4095)
                    assert (reg_read(0x7c) << 4 | reg_read(0x7d)) == 4095
                    for prop, value in (('battery-current-ma', -4096), ('battery-current-ma', 4096),
                                        ('battery-capacity', -1), ('battery-capacity', 101)):
                        rejected(prop, value)
                    qmp(endpoint, 'system_reset')
                    assert state('battery-current-ma') == -4095 and state('battery-capacity') == 50
                    assert reg_read(0x49) == 0
                    set_value('battery-current-ma', 100)
                    ac(False)
                    assert state('battery-current-ma') == 0
                    rejected('battery-current-ma', 100)
                    ac(True)
                    set_value('battery-current-ma', 100)
                    reg_write(0x33, 0x46)
                    assert state('battery-current-ma') == 0
                    rejected('battery-current-ma', 100)
                    reg_write(0x33, 0xc6)
                    set_value('battery-current-ma', -100)
                    reg_write(0x49, 0xff)
                    battery(False)
                    assert state('battery-current-ma') == 0 and reg_read(0xb9) == 50
                    rejected('battery-current-ma', -100)
                    assert not reg_read(1) & 0x20
                    assert reg_read(0x49) == 0x40
                    reg_write(0x49, 0x40)

                    # Warning thresholds: high nibble +5%, low nibble 0..15%.
                    # Strictly below, latched on entry, independently masked.
                    reg_write(0x41, 0)
                    reg_write(0x43, 0)
                    for register in range(0x48, 0x4d):
                        reg_write(register, 0xff)
                    battery(True)
                    reg_write(0xe6, 0xa5)  # 15%, 5%
                    set_value('battery-capacity', 15)
                    assert reg_read(0x4b) == 0
                    set_value('battery-capacity', 14)
                    assert reg_read(0x4b) == 2 and state('irq-active') is False
                    reg_write(0x43, 3)
                    assert state('irq-active') is True
                    reg_write(0x4b, 2)
                    set_value('battery-capacity', 14)
                    assert reg_read(0x4b) == 0 and state('irq-active') is False
                    set_value('battery-capacity', 5)
                    assert reg_read(0x4b) == 0
                    set_value('battery-capacity', 4)
                    assert reg_read(0x4b) == 1 and state('irq-active') is True
                    set_value('battery-capacity', 20)
                    assert reg_read(0x4b) == 1  # Recovery does not acknowledge.
                    reg_write(0x4b, 1)
                    set_value('battery-capacity', 0)
                    assert reg_read(0x4b) == 3
                    reg_write(0x4b, 2)
                    assert reg_read(0x4b) == 1
                    reg_write(0x4b, 1)
                    reg_write(0xb8, 0x40)  # Disable fuel gauge.
                    set_value('battery-capacity', 50)
                    set_value('battery-capacity', 0)
                    assert reg_read(0x4b) == 0
                    reg_write(0xb8, 0xc0)
                    assert reg_read(0x4b) == 3
                    reg_write(0x4b, 3)
                    battery(False)
                    set_value('battery-capacity', 50)
                    set_value('battery-capacity', 0)
                    assert reg_read(0x4b) == 0
                    battery(True)
                    assert reg_read(0x4b) == 3
                    reg_write(0x4b, 3)
                    set_value('battery-capacity', 10)
                    reg_write(0xe6, 0x0f)  # Warning1=5%, warning2=15%.
                    assert reg_read(0x4b) == 1
                    reg_write(0x4b, 1)
                    set_value('battery-capacity', 0)
                    qmp(endpoint, 'system_reset')
                    assert state('battery-capacity') == 0 and reg_read(0x4b) == 0
                    set_value('battery-capacity', 0)
                    assert reg_read(0x4b) == 0  # Reset does not invent an entry.
                    set_value('battery-capacity', 20)
                    set_value('battery-capacity', 14)
                    assert reg_read(0x4b) == 2
                    reg_write(0x4b, 2)
                    set_value('battery-capacity', 50)
                    battery(False)
                    reg_write(0xe6, 0xa0)
                    reg_write(0x49, 0xff)

                    reg_write(0x48, 0xff)
                    assert state('power-key-pressed') is False
                    reg_write(0x44, 0)
                    set_value('power-key-pressed', True)
                    assert reg_read(0x4c) == 0x20 and state('irq-active') is False
                    reg_write(0x44, 0x60)
                    assert state('irq-active') is True
                    reg_write(0x4c, 0x40)
                    assert reg_read(0x4c) == 0x20
                    reg_write(0x4c, 0x20)
                    set_value('power-key-pressed', True)
                    assert reg_read(0x4c) == 0 and state('irq-active') is False
                    qmp(endpoint, 'system_reset')
                    assert state('power-key-pressed') is True and reg_read(0x4c) == 0
                    reg_write(0x44, 0x60)
                    set_value('power-key-pressed', False)
                    assert reg_read(0x4c) == 0x40 and state('irq-active') is True
                    reg_write(0x4c, 0x40)
                    assert state('irq-active') is False

                    # The AXP2xx model uses a write to select a register, followed
                    # by a read transaction. AXP221 silicon identifies as 0x06.
                    write(A, 0x34)
                    write(DLEN, 1)
                    write(C, I2CEN | ST)
                    write(FIFO, 0x03)
                    assert read(S) & DONE
                    write(S, DONE)

                    write(DLEN, 1)
                    write(C, I2CEN | ST | READ)
                    assert read(FIFO) == 0x06
                    assert read(S) & DONE
                    assert not read(S) & ERR

                    # An unused address must report a NACK through the controller.
                    write(S, DONE)
                    write(A, 0x35)
                    write(DLEN, 1)
                    write(C, I2CEN | ST | READ)
                    assert read(S) & ERR
                    # An unimplemented register must not corrupt the register
                    # pointer (the upstream array has entries 0x00..0xfe).
                    write(S, DONE | ERR)
                    write(A, 0x34)
                    write(DLEN, 2)
                    write(C, I2CEN | ST)
                    write(FIFO, 0xff)
                    write(FIFO, 0x11)
                    write(S, DONE)
                    write(DLEN, 1)
                    write(C, I2CEN | ST | READ)
                    assert read(FIFO) == 0xff

                    # OFF_CTRL bit 7 is the Linux AXP20x power-off command.
                    # Other control bits must not remove power.
                    write(S, DONE)
                    write(DLEN, 2)
                    write(C, I2CEN | ST)
                    write(FIFO, 0x32)
                    write(FIFO, 0x43)
                    assert read(S) & DONE
                    assert process.poll() is None
                    if thermal_shutdown:
                        # Masking the IRQ must not disable hardware protection.
                        reg_write(0x42, 0)
                        if fault_after_enable:
                            reg_write(0x8f, 0x05)
                        else:
                            set_value('pmic-over-temperature', True)
                        assert process.poll() is None  # Only fault AND enable remove power.
                    with connect(endpoint, process) as monitor, monitor.makefile('rwb', buffering=0) as events:
                        assert 'QMP' in json.loads(events.readline())
                        events.write(b'{"execute":"qmp_capabilities","id":"watch"}\n')
                        assert json.loads(events.readline()).get('id') == 'watch'
                        if fault_after_enable:
                            events.write((json.dumps({'execute': 'qom-set', 'arguments': {
                                'path': pmic, 'property': 'pmic-over-temperature', 'value': True}})
                                + '\n').encode())
                        else:
                            write(S, DONE)
                            write(DLEN, 2)
                            write(C, I2CEN | ST)
                            write(FIFO, 0x8f if thermal_shutdown else 0x32)
                            # Shutdown may close qtest before its acknowledgement.
                            test.write(f'writel 0x{I2C + FIFO:x} {0x05 if thermal_shutdown else 0x80:#x}\n'.encode())
                        shutdown = None
                        for _ in range(64):
                            event = json.loads(events.readline())
                            assert 'error' not in event, event
                            if event.get('event') == 'SHUTDOWN':
                                shutdown = event['data']
                                break
                        assert shutdown == {'guest': not thermal_shutdown,
                            'reason': 'host-error' if thermal_shutdown else 'guest-shutdown'}, shutdown
                    assert process.wait(timeout=5) == 0
                    print('PASS: AXP221 AC/battery transitions, voltage/current/capacity, PMIC temperature, low-capacity warnings, charge/key events, IRQ mask/latch/W1C/reset, read-only status, '
                          'identity, NACK, register bounds, thermal fault latch and '
                          + ('abrupt thermal power removal' if thermal_shutdown else 'power-off command'))
            except Exception:
                print((directory / 'qemu.log').read_text())
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        # This exact owned diskless qtest process has no user
                        # image or guest state to preserve. Do not orphan it.
                        process.kill()
                        process.wait(timeout=10)


if __name__ == '__main__':
    main()
    main(thermal_shutdown=True)
    main(thermal_shutdown=True, fault_after_enable=True)
