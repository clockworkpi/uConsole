#!/usr/bin/env python3
"""Bounded BCM2711 watchdog observation through an owned private QMP socket.

Print JSON lines to stdout. Reading registers is the default; --pause-below
explicitly authorizes pausing (never resetting or resuming) the identified VM.
This is a diagnostic for the forge's raspi4b model, not a hardware verifier.
"""
import argparse
import json
import math
from pathlib import Path
import re
import time

from uconsole_emulator import qmp


def registers(text):
    match = re.fullmatch(
        r'\s*0*fe10001c:\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)\s*',
        text, re.IGNORECASE)
    if not match:
        raise ValueError('Unexpected BCM2711 watchdog register response')
    rstc, rsts, counter = (int(value, 16) for value in match.groups())
    if max(rstc, rsts) > 0xffffffff or counter > 0xfffff:
        raise ValueError('Watchdog register value outside model range')
    return {'rstc': rstc, 'rsts': rsts, 'counter': counter,
            'armed': bool(rstc & 0x20), 'remaining_seconds': counter / 65536}


def observe(endpoint, identity, duration=30, interval=0.5, pause_below=None,
            emit=None):
    if not identity or not isinstance(endpoint, Path):
        raise ValueError('An explicit runtime identity and Unix socket Path are required')
    if not math.isfinite(duration) or not 0 < duration <= 300:
        raise ValueError('Duration must be within (0, 300] seconds')
    if not math.isfinite(interval) or not 0.1 <= interval <= 10:
        raise ValueError('Interval must be within [0.1, 10] seconds')
    if pause_below is not None and (
            not math.isfinite(pause_below) or not 0 < pause_below < 16):
        raise ValueError('Pause threshold must be within (0, 16) seconds')
    emit = emit or (lambda event: print(json.dumps(event), flush=True))

    def control(operation, arguments=None):
        if qmp(endpoint, 'query-name').get('name') != identity:
            raise ValueError('QMP runtime identity mismatch')
        return qmp(endpoint, operation, arguments)

    start = time.monotonic()
    while time.monotonic() - start < duration:
        status = control('query-status')
        if not status.get('running'):
            emit({'event': 'not-running', 'status': status})
            return
        sample = registers(control('human-monitor-command',
                                   {'command-line': 'xp /3wx 0xfe10001c'}))
        emit(dict(sample, event='sample', elapsed=time.monotonic() - start))
        if pause_below is not None and sample['armed'] and sample['remaining_seconds'] < pause_below:
            control('stop')
            emit({'event': 'paused', 'runtime_id': identity,
                  'detail': 'Explicit diagnostic pause; VM remains paused after tool exit.'})
            for cpu in range(4):
                value = control('human-monitor-command',
                                {'command-line': 'info registers', 'cpu-index': cpu})
                emit({'event': 'cpu-registers', 'cpu': cpu, 'registers': value})
            return
        time.sleep(min(interval, max(0, duration - (time.monotonic() - start))))
    emit({'event': 'observation-complete', 'runtime_id': identity})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qmp-socket', type=Path, required=True)
    parser.add_argument('--runtime-id', required=True)
    parser.add_argument('--duration', type=float, default=30)
    parser.add_argument('--interval', type=float, default=0.5)
    parser.add_argument('--pause-below', type=float, metavar='SECONDS')
    args = parser.parse_args()
    observe(args.qmp_socket, args.runtime_id, args.duration, args.interval, args.pause_below)


if __name__ == '__main__':
    main()
