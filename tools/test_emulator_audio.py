"""Verify pinned QEMU can instantiate the null-backed USB playback surrogate.

No guest disk, host audio device, or microphone is opened. This is not ALSA
playback/capture or native carrier-audio qualification.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from uconsole_emulator import qmp


def reconnect_probe(qemu, cycles=200):
    """Stress reconnect across asynchronous device deletion, without a disk."""
    if type(cycles) is not int or not 1 <= cycles <= 10000:
        raise ValueError('Reconnect cycles must be between 1 and 10000')
    with tempfile.TemporaryDirectory(prefix='uc-audio-qmp-') as directory:
        endpoint = Path(directory) / 'qmp'
        command = [str(qemu), '-machine', 'raspi4b', '-display', 'none',
                   '-serial', 'none', '-monitor', 'none', '-S',
                   '-audiodev', 'none,id=forge-audio',
                   '-qmp', f'unix:{endpoint},server=on,wait=off']
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=errors)
            try:
                deadline = time.monotonic() + 10
                while not endpoint.exists():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError('QEMU reconnect probe failed to start')
                    time.sleep(0.01)
                for _ in range(cycles):
                    qmp(endpoint, 'device_add', {'driver': 'usb-audio', 'id': 'audio-surrogate',
                                                'audiodev': 'forge-audio'})
                    qmp(endpoint, 'device_del', {'id': 'audio-surrogate'})
                    if qmp(endpoint, 'query-status')['running']:
                        raise ValueError('Diskless probe unexpectedly running')
                qmp(endpoint, 'quit')
                if process.wait(timeout=10) != 0:
                    raise RuntimeError('Diskless QEMU did not exit cleanly')
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
    return {'status': 'passed', 'cycles': cycles, 'connections': cycles * 3 + 1,
            'scope': 'diskless QMP reconnect across USB device deletion; not guest ALSA'}


def probe(qemu, capture=False):
    requests = [{'execute': 'qmp_capabilities', 'id': 'caps'},
                {'execute': 'human-monitor-command', 'arguments': {'command-line': 'info usb'}, 'id': 'usb'},
                {'execute': 'quit', 'id': 'quit'}]
    command = [str(qemu), '-machine', 'raspi4b', '-accel', 'tcg', '-display', 'none',
               '-serial', 'none', '-monitor', 'none', '-S', '-qmp', 'stdio']
    if capture:
        command += ['-device', 'usb-forge-capture,id=capture-fixture']
        identity = 'Forge synthetic USB capture, ID: capture-fixture'
    else:
        command += ['-audiodev', 'none,id=forge-audio',
                    '-device', 'usb-audio,id=audio-surrogate,audiodev=forge-audio']
        identity = 'QEMU USB Audio Interface, ID: audio-surrogate'
    result = subprocess.run(command, input=''.join(json.dumps(item) + '\n' for item in requests),
                            text=True, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError('QEMU audio model probe failed: ' + result.stderr[-2000:])
    messages = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    replies = [item for item in messages if 'id' in item]
    if ([item['id'] for item in replies] != ['caps', 'usb', 'quit'] or
            any('error' in item or 'return' not in item for item in replies)):
        raise ValueError('Incomplete audio model QMP evidence')
    topology = replies[1]['return']
    if not isinstance(topology, str) or identity not in topology:
        raise ValueError('Requested audio surrogate is not present')
    return {'status': 'instantiated', 'audio': 'synthetic-capture' if capture else 'usb-null', 'command': command, 'qmp': messages,
            'coverage': 'QEMU model construction only; guest ALSA, capture, jack and native audio unqualified'}


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--qemu', required=True, type=Path)
    cli.add_argument('--output', type=Path, help='Retain exclusive JSON evidence; otherwise print it')
    cli.add_argument('--reconnect-cycles', type=int, default=0)
    cli.add_argument('--capture', action='store_true', help='Also instantiate synthetic input without a host audio backend')
    args = cli.parse_args()
    if args.output is not None:
        # Reserve evidence before starting QEMU; never overwrite prior results.
        stream = args.output.open('x')
    else:
        stream = None
    try:
        record = probe(args.qemu)
        if args.capture:
            record['capture'] = probe(args.qemu, capture=True)
        if args.reconnect_cycles:
            record['reconnect'] = reconnect_probe(args.qemu, args.reconnect_cycles)
        if stream is not None:
            json.dump(record, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        else:
            print(json.dumps(record, indent=2))
    finally:
        if stream is not None:
            stream.close()
    if args.output is not None:
        print(record['status'])
