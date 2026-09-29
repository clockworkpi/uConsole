#!/usr/bin/env python3
"""Stress running qtest CPU reset/pause/exit; retain failures and reap children."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import tempfile

from test_emulator_pmic import connect
from uconsole_emulator import executable


@contextmanager
def monitor(endpoint, process):
    """Keep one QMP connection: this tests CPUs, not reconnect scheduling."""
    with connect(endpoint, process) as sock, sock.makefile('rwb', buffering=0) as stream:
        greeting = json.loads(stream.readline())
        if not isinstance(greeting.get('QMP'), dict):
            raise ValueError('Missing QMP greeting')
        sequence = 0

        def command(operation):
            nonlocal sequence
            sequence += 1
            stream.write((json.dumps({'execute': operation, 'id': sequence}) + '\n').encode())
            for _ in range(64):
                line = stream.readline()
                if not line:
                    raise ConnectionError('QMP closed before acknowledgement')
                reply = json.loads(line)
                if reply.get('id') == sequence:
                    if 'error' in reply:
                        raise RuntimeError(reply['error'])
                    return reply['return']
            raise RuntimeError('QMP event limit exceeded')

        command('qmp_capabilities')
        yield command


def validate(binary, output, iterations, cycles):
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'events.jsonl').open('x') as events:
        def record(**value):
            events.write(json.dumps(value) + '\n')
            events.flush()

        for iteration in range(iterations):
            with tempfile.TemporaryDirectory(prefix='uc-cpu-') as temporary, \
                    (output / f'qemu-{iteration}.log').open('xb') as log:
                endpoint = Path(temporary) / 'qmp'
                command = [binary, '-M', 'raspi4b', '-accel', 'qtest',
                           '-display', 'none', '-serial', 'none', '-monitor', 'none',
                           '-qmp', f'unix:{endpoint},server=on,wait=off']
                process = subprocess.Popen(command, stdout=log, stderr=log)
                record(iteration=iteration, pid=process.pid, command=command)
                try:
                    with monitor(endpoint, process) as command:
                        for cycle in range(cycles):
                            for operation in ('system_reset', 'stop', 'cont'):
                                record(iteration=iteration, cycle=cycle, dispatch=operation)
                                command(operation)
                                record(iteration=iteration, cycle=cycle, acknowledged=operation)
                        record(iteration=iteration, dispatch='quit')
                        command('quit')
                    code = process.wait(timeout=10)
                    if code != 0:
                        raise RuntimeError(f'QEMU exit status {code}')
                    record(iteration=iteration, exited=code)
                except BaseException as error:
                    record(iteration=iteration, error=str(error))
                    raise
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            record(iteration=iteration, forced_kill=True)
                            process.kill()
                            process.wait(timeout=5)
        record(passed=True, iterations=iterations, cycles=cycles)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', default=executable('qemu-system-aarch64'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--iterations', type=int, default=50)
    parser.add_argument('--cycles', type=int, default=10)
    args = parser.parse_args()
    if args.iterations < 1 or args.cycles < 1:
        parser.error('iterations and cycles must be positive')
    validate(args.qemu, args.output, args.iterations, args.cycles)
    print(f'PASS: {args.iterations} running qtest processes, {args.cycles} reset/stop/cont cycles each')


if __name__ == '__main__':
    main()
