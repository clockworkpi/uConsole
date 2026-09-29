#!/usr/bin/env python3
"""Exercise real QEMU MMIO/timers through qtest, without booting Linux (POSIX)."""
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time

from uconsole_emulator import executable


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


def main():
    with tempfile.TemporaryDirectory(prefix='uc-qtest-') as directory:
        directory = Path(directory)
        with (directory / 'qemu.log').open('wb') as log:
            process = subprocess.Popen([executable('qemu-system-aarch64'), '-M', 'raspi4b',
                '-accel', 'qtest', '-display', 'none', '-serial', 'none', '-monitor', 'none', '-no-shutdown',
                '-qtest', f'unix:{directory}/test.sock,server=on,wait=off',
                '-qmp', f'unix:{directory}/qmp.sock,server=on,wait=off'], stdout=log, stderr=log)
            try:
                with connect(directory / 'test.sock', process) as test, connect(directory / 'qmp.sock', process) as monitor:
                    t = test.makefile('rwb', buffering=0)
                    m = monitor.makefile('rwb', buffering=0)
                    events = []
                    def qtest(command):
                        t.write((command + '\n').encode())
                        while True:
                            line = t.readline().decode().strip()
                            if line.startswith('IRQ '):
                                continue
                            assert line.startswith('OK'), line
                            return line
                    def qmp(command):
                        m.write((json.dumps({'execute': command}) + '\n').encode())
                        while True:
                            value = json.loads(m.readline())
                            if 'event' in value:
                                events.append(value['event'])
                            if 'return' in value:
                                return value['return']
                            assert 'error' not in value, value
                    m.readline()
                    qmp('qmp_capabilities')
                    qtest('writel 0xfe100024 0x5a050000')
                    qtest('writel 0xfe10001c 0x5a000020')
                    assert int(qtest('readl 0xfe100024').split()[1], 16) == 5 * 65536
                    qtest('clock_step 4000000000')
                    assert int(qtest('readl 0xfe100024').split()[1], 16) == 65536
                    qmp('query-status')
                    assert 'RESET' not in events, 'Arming watchdog reset the board'
                    # Reload, then stop: canceled watchdog must not reset later.
                    qtest('writel 0xfe100024 0x5a050000')
                    qtest('clock_step 2000000000')
                    assert int(qtest('readl 0xfe100024').split()[1], 16) == 3 * 65536
                    qtest('writel 0xfe10001c 0x5a000102')
                    qtest('clock_step 6000000000')
                    qmp('query-status')
                    assert 'RESET' not in events, 'Canceled watchdog reset the board'
                    # Bad-password writes must not arm the timer.
                    qtest('writel 0xfe10001c 0x00000020')
                    qtest('clock_step 6000000000')
                    qmp('query-status')
                    assert 'RESET' not in events
                    # Match the production driver's start/ping sequence with
                    # a 60-second userspace timeout: the 20-bit hardware value
                    # wraps to 12 seconds and the core refreshes it within 8.
                    # Exercise many refreshes, not just one successful reload.
                    for heartbeat in range(128):
                        qtest('writel 0xfe100024 0x5a0c0000')
                        qtest('writel 0xfe10001c 0x5a000122')
                        qtest('clock_step 8000000000')
                        left = int(qtest('readl 0xfe100024').split()[1], 16)
                        assert left == 4 * 65536, (heartbeat, left)
                        qmp('query-status')
                        assert 'RESET' not in events, (heartbeat, events)
                    qtest('writel 0xfe10001c 0x5a000102')
                    qtest('writel 0xfe100024 0x5a010000')
                    qtest('writel 0xfe10001c 0x5a000020')
                    qtest('clock_step 1000000000')
                    qmp('query-status')
                    assert 'RESET' in events, 'Expired watchdog failed to reset'
                    events.clear()
                    qtest('writel 0xfe100020 0x5a000555')
                    qtest('writel 0xfe100024 0x5a00000a')
                    qtest('writel 0xfe10001c 0x5a000020')
                    qtest('clock_step 1000000')
                    qmp('query-status')
                    assert 'SHUTDOWN' in events, 'Linux poweroff request failed'
                    print('PASS: watchdog arm, countdown, sustained heartbeat, reload, cancel, password, expiry, poweroff')
            except Exception:
                print((directory / 'qemu.log').read_text())
                raise
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    main()
