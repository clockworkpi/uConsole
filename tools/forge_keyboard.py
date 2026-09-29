"""Persistent firmware-to-USB bridge for one explicitly owned composite guest.

Logical pin commands, not global host input capture. Calls are serialized and
bounded. Any failure after dispatch poisons the session: partial effects are
retained, never automatically replayed or rolled back.
"""
import json
import os
from pathlib import Path
import re
import select
import subprocess
import threading
import time
import uuid

from keyboard_reports import Reports, integer, MAX_EVENTS, MAX_TRACE


LIMITS = {'matrix': (7, 7, 1), 'key': (16, 1), 'switch': (1,), 'run': (32,),
          'advance': (1000,), 'edge': (3,), 'state': ()}
STATE = re.compile(r'queued=(\d+) leds=(\d+) cdc-rx=(\d+) lines=(\d+) '
                   r'reset-requested=([01]) configuration=(\d+) epoch=(\d+)')


def default_oracle(root=None, build_root=None):
    """Owner-side discovery only; never consult the client's workspace or PATH."""
    if root is None or build_root is None:
        from uconsole_emulator import ROOT, BUILD_ROOT
        root = ROOT if root is None else root
        build_root = BUILD_ROOT if build_root is None else build_root
    for candidate in (Path(root) / 'bin/keyboard-oracle',
                      Path(build_root) / 'keyboard-oracle/keyboard-oracle'):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def commands_snapshot(commands):
    if not isinstance(commands, (list, tuple)) or not 1 <= len(commands) <= 64:
        raise ValueError('Expected 1..64 logical keyboard commands')
    result = []
    for command in commands:
        if not isinstance(command, (list, tuple)) or not command or not isinstance(command[0], str):
            raise ValueError('Invalid keyboard command')
        name, *values = command
        limits = LIMITS.get(name)
        if limits is None or len(values) != len(limits):
            raise ValueError('Unsupported keyboard command or argument count')
        for value, maximum in zip(values, limits):
            integer(value, 0, maximum)
        result.append((name, *values))
    return tuple(result)


class KeyboardBridge:
    def __init__(self, runtime, oracle):
        if os.name != 'posix':
            raise ValueError('Firmware bridge currently requires POSIX pipes')
        if runtime.args.keyboard != 'composite':
            raise ValueError('Firmware bridge requires an explicitly selected composite keyboard')
        self.runtime = runtime
        self.identity = runtime.identity
        self.oracle = Path(oracle).resolve()
        self.process = None
        self.encoder = Reports()
        self.buffer = bytearray()
        self.header = False
        self.timestamp = 0
        self.epoch = None
        self.error = None
        self.last_attempt = None
        self.closed = False
        self.mutex = threading.Lock()

    def control(self, command, arguments=None):
        if self.runtime.identity != self.identity:
            raise ValueError('Keyboard owner identity changed')
        # Runtime.control also verifies the actual QMP identity, never a shared port.
        return self.runtime.control(command, arguments)

    def transport(self):
        raw = self.control('qom-get', {'path': '/machine/peripheral/deck', 'property': 'transport-state'})
        match = STATE.fullmatch(raw) if isinstance(raw, str) else None
        if not match:
            raise ValueError('Unrecognized composite keyboard transport state')
        state = dict(zip(('queued', 'leds', 'cdc_rx', 'lines', 'reset_requested', 'configuration', 'epoch'),
                         map(int, match.groups())))
        if state['queued'] > 32 or state['leds'] > 255 or state['configuration'] != 1 or state['reset_requested']:
            raise ValueError('Composite keyboard is unconfigured, resetting or has invalid state')
        if self.epoch is not None and state['epoch'] != self.epoch:
            raise ValueError('Composite USB reset invalidated the bridge session')
        return state

    def line(self, deadline):
        while b'\n' not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                raise TimeoutError('Firmware oracle acknowledgement timed out')
            block = os.read(self.process.stdout.fileno(), 65536)
            if not block:
                raise ValueError('Firmware oracle exited before acknowledgement')
            self.buffer.extend(block)
            if len(self.buffer) > MAX_TRACE:
                raise ValueError('Firmware oracle output exceeds limit')
        line, _, tail = self.buffer.partition(b'\n')
        self.buffer = bytearray(tail)
        return json.loads(line)

    def transact(self, commands, deadline):
        if self.process is None:
            self.process = subprocess.Popen([str(self.oracle)], stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        token = uuid.uuid4().hex
        wire = ''.join(' '.join(map(str, command)) + '\n' for command in commands)
        wire += 'sync ' + token + '\n'
        payload = wire.encode('ascii')
        if self.process.stdin.write(payload) != len(payload):
            raise OSError('Incomplete oracle command write; input state is uncertain')
        self.process.stdin.flush()
        records = []
        for _ in range(MAX_EVENTS + 2):
            item = self.line(deadline)
            if not self.header:
                if (item != {'evidence': 'host-firmware-semantic-oracle', 'schema': 1}
                        or type(item.get('schema')) is not int):
                    raise ValueError('Invalid firmware oracle header')
                self.header = True
                continue
            if isinstance(item, dict) and set(item) == {'sync', 'us'}:
                if item['sync'] != token:
                    raise ValueError('Incorrect oracle acknowledgement token')
                self.timestamp = integer(item['us'], self.timestamp, (1 << 64) - 1)
                return records
            if not isinstance(item, dict) or set(item) != {'us', 'event', 'args'}:
                raise ValueError('Invalid firmware oracle event')
            self.timestamp = integer(item['us'], self.timestamp, (1 << 64) - 1)
            records.append(item)
        raise ValueError('Firmware oracle event limit exceeded')

    def apply(self, commands, *, timeout=5):
        commands = commands_snapshot(commands)  # Invalid callers never touch the VM/oracle.
        integer(timeout, 1, 30)
        if not self.mutex.acquire(blocking=False):
            raise ValueError('Another keyboard action owns this bridge')
        try:
            if self.closed or self.error:
                raise ValueError('Keyboard bridge is closed or uncertain; inspect prior effects before recreating')
            deadline = time.monotonic() + timeout
            attempt = {'runtime_identity': self.identity, 'commands': commands,
                       'status': 'running', 'events': [], 'reports': [], 'pending_report': None}
            self.last_attempt = attempt
            try:
                before = self.transport()
                if self.epoch is None:
                    self.epoch = before['epoch']
                records = self.transact(commands, deadline)
                attempt['events'] = records
                delivered = attempt['reports']
                for record in records:
                    # Sample host LEDs for each firmware API call, not merely at reset.
                    state = self.transport()
                    self.encoder.set_leds(state['leds'])
                    for report in self.encoder.feed(record['event'], record['args']):
                        while state['queued'] >= 32:
                            if time.monotonic() >= deadline:
                                raise TimeoutError('Composite keyboard queue did not drain')
                            # Bound polling without a busy loop; effects are already accepted.
                            threading.Event().wait(min(0.01, max(0, deadline - time.monotonic())))
                            state = self.transport()
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Keyboard delivery deadline expired')
                        attempt['pending_report'] = report.hex()
                        self.control('qom-set', {'path': '/machine/peripheral/deck',
                                                'property': 'inject-report', 'value': report.hex()})
                        delivered.append({'us': record['us'], 'report_hex': report.hex(), 'leds': state['leds']})
                        attempt['pending_report'] = None
                        state = self.transport()
                attempt.update(status='completed', transport=self.transport(),
                               clock='explicit firmware virtual time; host delivery is not USB timing fidelity')
                return attempt
            except BaseException as exc:
                self.error = str(exc)
                attempt.update(status='failed', error=self.error, rollback=False)
                raise
        finally:
            self.mutex.release()

    def close(self):
        with self.mutex:
            self.closed = True
            if self.process is not None:
                if self.process.stdin:
                    self.process.stdin.close()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=2)
                self.process.stdout.close()
        # Never stop the VM or claim that previously delivered keys were released.
