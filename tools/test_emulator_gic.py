#!/usr/bin/env python3
"""Diskless TCG regression for debugger reads of the multi-CPU GIC."""
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

from test_emulator_pmic import connect
from uconsole_emulator import executable, qmp


def main():
    with tempfile.TemporaryDirectory(prefix='uc-gic-debug-') as directory:
        directory = Path(directory)
        endpoint = directory / 'qmp'
        identity = 'uc-gic-test-' + uuid.uuid4().hex
        with (directory / 'qemu.log').open('wb') as log:
            # qtest bypasses the faulty current_cpu path: use real TCG with
            # CPUs paused, no disk and no guest kernel for this regression.
            process = subprocess.Popen([
                executable('qemu-system-aarch64'), '-M', 'raspi4b',
                '-accel', 'tcg', '-S', '-display', 'none', '-serial', 'none',
                '-monitor', 'none', '-name', identity,
                '-qmp', f'unix:{endpoint},server=on,wait=off'],
                stdout=log, stderr=log)
            try:
                with connect(endpoint, process):
                    pass
                def control(operation, arguments=None):
                    assert qmp(endpoint, 'query-name')['name'] == identity
                    return qmp(endpoint, operation, arguments)
                def read(address):
                    result = control('human-monitor-command',
                                     {'command-line': f'xp /1wx 0x{address:x}'})
                    match = re.search(r':\s+0x([0-9a-f]+)', result)
                    assert match, result
                    return int(match[1], 16)
                assert read(0xff841000) == 0  # Distributor disabled at reset.
                typer = read(0xff841004)
                assert typer & 31 == 6, hex(typer)  # 192 external + 32 internal.
                assert (typer >> 5) & 7 == 3, hex(typer)  # Four CPUs.
                assert read(0xff841100) & 0xffff == 0xffff  # Banked SGI enables.
                assert read(0xff842000) == 0  # CPU interface bank zero.
                assert read(0xff844000) == 0  # Hypervisor interface bank zero.
                assert read(0xff846000) == 0  # Virtual CPU interface bank zero.
                assert not control('query-status')['running']
                control('quit')
                assert process.wait(timeout=5) == 0
                print('PASS: diskless TCG GIC debugger reads, banked defaults, no CPU resume')
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == '__main__':
    main()
