#!/usr/bin/env python3
"""Exercise the CM4 firmware GPIO mailbox contract, not physical rail effects."""
from pathlib import Path
import subprocess
import tempfile

from test_emulator_pmic import connect
from uconsole_emulator import executable, qmp


GET_STATE, SET_STATE = 0x30041, 0x38041
GET_CONFIG, SET_CONFIG = 0x30043, 0x38043
BUFFER = 0x10000
MAILBOX = 0xfe00b880


def main():
    with tempfile.TemporaryDirectory(prefix='uc-fw-gpio-') as directory:
        directory = Path(directory)
        with (directory / 'qemu.log').open('wb') as log:
            process = subprocess.Popen([
                executable('qemu-system-aarch64'), '-M', 'raspi4b', '-accel', 'qtest',
                '-display', 'none', '-serial', 'none', '-monitor', 'none',
                '-qtest', f'unix:{directory}/test,server=on,wait=off',
                '-qmp', f'unix:{directory}/qmp,server=on,wait=off'], stdout=log, stderr=log)
            try:
                with connect(directory / 'test', process) as sock:
                    stream = sock.makefile('rwb', buffering=0)

                    def command(text):
                        stream.write((text + '\n').encode())
                        line = stream.readline().decode().strip()
                        assert line.startswith('OK'), line
                        return line

                    def write(address, value):
                        command(f'writel {address:#x} {value:#x}')

                    def read(address):
                        return int(command(f'readl {address:#x}').split()[1], 16)

                    def request(tag, values, total=None):
                        words = [24 + 4 * len(values), 0, tag, 4 * len(values), 0, *values, 0]
                        if total is not None:
                            words[0] = total
                        for index, word in enumerate(words):
                            write(BUFFER + 4 * index, word)
                        write(BUFFER + len(words) * 4, 0xdeadbeef)
                        write(MAILBOX + 0x20, BUFFER | 8)
                        assert read(MAILBOX) == BUFFER | 8
                        assert read(BUFFER + 4) == 0x80000000
                        assert read(BUFFER + (len(words) - 1) * 4) == 0
                        assert read(BUFFER + len(words) * 4) == 0xdeadbeef
                        return read(BUFFER + 16), [read(BUFFER + 20 + 4 * i)
                                                  for i in range(len(values))]

                    for pin in range(128, 136):
                        length, config = request(GET_CONFIG, [pin, 9, 9, 9, 9])
                        assert length == 0x80000014 and config == [0, 0, 0, 0, 0], (pin, length, config)
                        values = [pin, 1, pin & 1, (pin >> 1) & 1, (pin >> 2) & 1, 1]
                        assert request(SET_CONFIG, values) == (0x80000018, [0, *values[1:]])
                        assert request(GET_CONFIG, [pin, 9, 9, 9, 9])[1] == [0, *values[1:5]]
                        assert request(GET_STATE, [pin, 9]) == (0x80000008, [0, 1])
                        assert request(SET_STATE, [pin, 0]) == (0x80000008, [0, 0])
                        assert request(GET_STATE, [pin, 9])[1] == [0, 0]

                    for pin in range(128, 136):
                        assert request(GET_CONFIG, [pin, 9, 9, 9, 9])[1] == [
                            0, 1, pin & 1, (pin >> 1) & 1, (pin >> 2) & 1]

                    # Reject unsupported IDs and malformed values without
                    # altering any pin or overwriting the following tag/guard.
                    for pin in (0, 127, 136, 0xffffffff):
                        assert request(GET_CONFIG, [pin, 9, 9, 9, 9])[1][0] != 0
                    for index in range(1, 6):
                        values = [128, 1, 0, 1, 1, 1]
                        values[index] = 2
                        assert request(SET_CONFIG, values)[1][0] != 0
                        assert request(GET_CONFIG, [128, 9, 9, 9, 9])[1] == [0, 1, 0, 0, 0]
                    assert request(SET_STATE, [128, 2])[1][0] != 0
                    assert request(GET_STATE, [128, 9])[1] == [0, 0]
                    for tag, count in ((GET_STATE, 2), (SET_STATE, 2),
                                       (GET_CONFIG, 5), (SET_CONFIG, 6)):
                        for size in range(count):
                            length, _ = request(tag, ([128] + [0] * count)[:size])
                            assert length == 0x80000000, (tag, size, length)
                    assert request(SET_CONFIG, [128, 0, 1, 1, 1, 1], total=24)[0] == 0x80000000
                    assert request(GET_CONFIG, [128, 9, 9, 9, 9])[1] == [0, 1, 0, 0, 0]
                    assert request(GET_STATE, [128, 9])[1] == [0, 0]
                    assert request(SET_STATE, [134, 1])[1] == [0, 1]
                    qmp(directory / 'qmp', 'system_reset')
                    for pin in range(128, 136):
                        assert request(GET_CONFIG, [pin, 9, 9, 9, 9])[1] == [0, 0, 0, 0, 0]
                        assert request(GET_STATE, [pin, 9])[1] == [0, 0]
                    print('PASS: firmware GPIO configuration/state, pin isolation, invalid requests, bounds and reset')
            except Exception:
                print((directory / 'qemu.log').read_text())
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == '__main__':
    main()
