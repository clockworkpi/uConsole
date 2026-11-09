#!/usr/bin/env python3
"""Exercise owned controls and guest transfers on a disposable CM4 workspace."""
import argparse
from pathlib import Path
import tempfile
import uuid

from forge_runtime import Runtime
from uconsole_emulator import parser, wait_for_log


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument('--workspace', type=Path, required=True,
                           help='Disposable prepared workspace; this test writes guest /tmp')
    options = arguments.parse_args()
    args = parser().parse_args(['--workspace', str(options.workspace), 'run', '--mode', 'maintenance'])
    runtime = Runtime(args).start()
    try:
        wait_for_log(runtime.workspace / 'serial.log', b'root@(none):/#', runtime.process, 120)
        status = runtime.control('query-status')
        assert status['running'], status
        result = runtime.execute('id -u; uname -m')
        assert result['exit_code'] == 0 and 'aarch64' in result['stdout'], result
        print(result['stdout'], flush=True)
        with tempfile.TemporaryDirectory(prefix='uc-transfer-') as directory:
            source = Path(directory) / 'source.txt'
            destination = Path(directory) / 'received.txt'
            payload = b'owned runtime transfer\n' + uuid.uuid4().hex.encode() + b'\n'
            source.write_bytes(payload)
            guest = '/tmp/uconsole-forge-proof-' + uuid.uuid4().hex
            runtime.upload(source, guest)
            runtime.download(guest, destination)
            assert destination.read_bytes() == payload
            result = runtime.execute(f'rm -f {guest}')
            assert result['exit_code'] == 0, result
        runtime.stop()
        print('PASS: private runtime identity, guest execution, file round trip and read-only shutdown')
    finally:
        if runtime.process.poll() is None:
            print('Test failed; forcing off this disposable test guest')
            runtime.stop(force=True)
        else:
            runtime.release()


if __name__ == '__main__':
    main()
