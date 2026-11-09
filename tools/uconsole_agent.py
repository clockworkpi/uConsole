#!/usr/bin/env python3
"""Machine-readable automation interface for the uConsole CM4 workspace."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

from uconsole_emulator import DEFAULT, ROOT, control_connection, qmp, read_config

ANSI = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]')
TASKS = ROOT / 'uconsole-tasks.json'
MAX_SERIAL_OUTPUT = 16 * 1024 * 1024


class GuestChannelUncertain(ConnectionError):
    """A dispatched serial command has no verified completion acknowledgement."""


class GuestTransferCancelled(Exception):
    """Transfer stopped at a known serial boundary before destination publication."""


def check_transfer_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise GuestTransferCancelled('Transfer cancelled before destination publication; no rollback is implied.')


def clean(text):
    return ANSI.sub('', text).replace('\r', '')


def clean_serial_lines(text):
    lines = clean(text).splitlines()
    return [line for line in lines
            if not line.startswith('UC_AGENT_')
            and not re.search(r'(^|[/#] )printf .*(UC_AGENT_|base64 -d)', line)]


def serial_exec(port, script, timeout=60):
    """Execute a bounded script in the maintenance shell and return clean output."""
    token = 'UC_AGENT_' + uuid.uuid4().hex
    encoded = base64.b64encode(script.encode()).decode()
    line = (f"printf '\\n{token}_BEGIN\\n'; printf '%s' '{encoded}' | base64 -d | "
            "SYSTEMD_PAGER=cat PAGER=cat SYSTEMD_COLORS=0 /bin/bash; "
            f"uc_rc=$?; printf '\\n{token}_END:%s:{token}_DONE\\n' \"$uc_rc\"\n")
    if len(line) > 3500:
        raise ValueError('Command exceeds the serial console line limit')
    deadline = time.monotonic() + timeout
    with control_connection(port) as sock:
        sock.settimeout(timeout)
        try:
            # sendall may deliver a prefix before failing; that is uncertain
            # too, even if no complete command was acknowledged by the guest.
            sock.sendall(line.encode())
            return _serial_response(sock, token, deadline)
        except (OSError, ValueError, KeyboardInterrupt) as exc:
            raise GuestChannelUncertain(
                f'Guest command completion is unknown ({exc}); it may still be running. '
                'Do not submit another command on this serial channel.') from exc


def _serial_response(sock, token, deadline):
    received = b''
    # Console printk may arrive between the status and its trailing newline.
    # Use an explicit nonce-bound terminator, never a partial decimal match.
    # Interleaving inside this frame still fails closed as uncertain completion.
    end_pattern = re.compile(rb'\n' + token.encode() + rb'_END:([0-9]+):' +
                             token.encode() + rb'_DONE')
    while time.monotonic() < deadline:
        sock.settimeout(max(0.1, deadline - time.monotonic()))
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError('Guest serial console closed')
        received += chunk
        if len(received) > MAX_SERIAL_OUTPUT:
            raise ValueError('Guest output exceeds the serial response limit')
        matches = list(end_pattern.finditer(received))
        if matches:
            end = matches[-1]
            begin = received.rfind(('\n' + token + '_BEGIN\r\n').encode(), 0, end.start())
            if begin < 0:
                begin = received.rfind(('\n' + token + '_BEGIN\n').encode(), 0, end.start())
            if begin < 0:
                continue
            begin = received.find(b'\n', begin + 1) + 1
            output = clean(received[begin:end.start()].decode(errors='replace'))
            return {'exit_code': int(end.group(1)), 'stdout': output}
    raise TimeoutError('Guest did not complete the command')


def require_success(result):
    if result['exit_code']:
        raise RuntimeError(f"guest command exited {result['exit_code']}: {result['stdout']}")
    return result


def guest_put(port, source, destination, *, cancel=None):
    check_transfer_cancel(cancel)
    payload = source.read_bytes()
    if len(payload) > 8 * 1024 * 1024:
        raise ValueError('Serial transfers are limited to 8 MiB; use SSH/SCP for larger files')
    if not destination.startswith('/') or '\0' in destination:
        raise ValueError('Guest destination must be an absolute path')
    temporary = '/tmp/uconsole-agent-' + uuid.uuid4().hex
    check_transfer_cancel(cancel)
    require_success(serial_exec(port, 'test "$(id -u)" = 0'))
    uncertain = False
    try:
        check_transfer_cancel(cancel)
        require_success(serial_exec(port, f'umask 077; : > {temporary}'))
        for position in range(0, len(payload), 1536):
            check_transfer_cancel(cancel)
            encoded = base64.b64encode(payload[position:position + 1536]).decode()
            require_success(serial_exec(port, f"printf '%s' '{encoded}' | base64 -d >> {temporary}"))
        expected = hashlib.sha256(payload).hexdigest()
        target = shlex.quote(destination)
        check_transfer_cancel(cancel)
        result = require_success(serial_exec(port,
            f"test \"$(sha256sum {temporary} | cut -d' ' -f1)\" = {expected} && "
            f"install -m 0644 {temporary} {target} && sync && sha256sum {target}"))
        return {'bytes': len(payload), 'sha256': expected, 'guest': destination,
                'guest_output': result['stdout'].strip()}
    except GuestChannelUncertain:
        uncertain = True
        raise
    finally:
        if not uncertain:
            try:
                serial_exec(port, f'rm -f {temporary}')
            except GuestChannelUncertain:
                # Installation may already have committed. Report uncertain
                # cleanup so the owner cannot reuse a possibly busy channel.
                raise
            except (OSError, RuntimeError, TimeoutError):
                pass


def guest_get(port, source, destination, *, cancel=None):
    check_transfer_cancel(cancel)
    if not source.startswith('/') or '\0' in source:
        raise ValueError('Guest source must be an absolute path')
    if destination.exists():
        raise FileExistsError(destination)
    marker = 'UC_DATA_' + uuid.uuid4().hex
    result = require_success(serial_exec(port,
        f"test -f {shlex.quote(source)} && printf '{marker}:' && base64 {shlex.quote(source)} | tr -d '\\n' && echo"))
    line = next((line for line in result['stdout'].splitlines() if line.startswith(marker + ':')), None)
    if line is None:
        raise RuntimeError('Guest transfer did not return a payload')
    payload = base64.b64decode(line.split(':', 1)[1], validate=True)
    check_transfer_cancel(cancel)
    destination.parent.mkdir(parents=True, exist_ok=True)
    check_transfer_cancel(cancel)
    with destination.open('xb') as stream:
        stream.write(payload)
    return {'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
            'guest': source, 'host': str(destination.resolve())}


def inspect(workspace, qmp_port, tail=80):
    config = read_config(workspace)
    state = {'workspace': str(workspace.resolve()), 'machine': config,
             'files': {}, 'runtime': {'running': False}}
    for name in ('base.img', 'disk.qcow2', 'kernel8.img', 'cm4-qemu.dtb', 'serial.log'):
        path = workspace / name
        if path.exists():
            state['files'][name] = {'bytes': path.stat().st_size, 'path': str(path.resolve())}
    try:
        if qmp_port is not None:
            status = qmp(qmp_port, 'query-status')
            state['runtime'] = {'running': bool(status.get('running')), 'status': status.get('status'),
                                'qmp': f'127.0.0.1:{qmp_port}'}
    except (OSError, ValueError):
        pass
    log = workspace / 'serial.log'
    if log.exists():
        lines = clean_serial_lines(log.read_text(errors='replace'))
        state['serial_tail'] = lines[-tail:]
    return state


def context_markdown(state):
    runtime = state['runtime']
    machine = state['machine']
    lines = [
        '# uConsole CM4 agent context', '',
        f"- Workspace: `{state['workspace']}`",
        f"- Machine: `{machine.get('machine', 'unknown')}` ({machine.get('coverage', 'unknown')})",
        f"- Runtime: `{runtime.get('status', 'stopped')}`",
        f"- Root: `{machine.get('root', 'unknown')}`",
        f"- Image SHA-256: `{machine.get('source_sha256', 'unknown')}`", '',
        '## Recent serial output', '', '```text',
        *state.get('serial_tail', ['(no serial output)']), '```', '',
        '## Known fidelity limits', '',
        'DSI/VC4 graphics, AXP221 battery/charging, STM32 keyboard firmware, modem, audio, Wi-Fi and Bluetooth are not fully modeled.',
    ]
    return '\n'.join(lines) + '\n'


def load_tasks(path):
    data = json.loads(path.read_text())
    if data.get('schema') != 1 or not isinstance(data.get('tasks'), dict):
        raise ValueError('Task file must have schema 1 and a tasks object')
    return data['tasks']


def run_task(name, task, serial_port):
    started = time.time()
    event = {'task': name, 'started': started, 'kind': task.get('kind')}
    if task.get('kind') == 'host':
        argv = task.get('argv')
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise ValueError(f'Host task {name} requires a string argv array')
        process = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True)
        event.update(exit_code=process.returncode, stdout=process.stdout, stderr=process.stderr)
    elif task.get('kind') == 'guest':
        script = task.get('script')
        if not isinstance(script, str):
            raise ValueError(f'Guest task {name} requires a script string')
        result = serial_exec(serial_port, script, int(task.get('timeout', 60)))
        event.update(exit_code=result['exit_code'], stdout=result['stdout'], stderr='')
    else:
        raise ValueError(f'Task {name} has an unsupported kind')
    event['duration_seconds'] = round(time.time() - started, 3)
    return event


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', type=Path, default=DEFAULT)
    p.add_argument('--serial-port', type=int, default=4445)
    p.add_argument('--serial-socket', type=Path, help='Private runtime serial socket instead of TCP')
    p.add_argument('--qmp-port', type=int, default=4444)
    sub = p.add_subparsers(dest='action', required=True)
    execute = sub.add_parser('exec', help='Run a command in a maintenance shell')
    execute.add_argument('--timeout', type=int, default=60)
    execute.add_argument('--json', action='store_true')
    execute.add_argument('command', nargs=argparse.REMAINDER)
    upload = sub.add_parser('put', help='Copy a host file into the guest')
    upload.add_argument('source', type=Path)
    upload.add_argument('destination')
    download = sub.add_parser('get', help='Copy a guest file to a new host path')
    download.add_argument('source')
    download.add_argument('destination', type=Path)
    listing = sub.add_parser('ls', help='List guest files as JSON')
    listing.add_argument('path', nargs='?', default='/')
    status = sub.add_parser('inspect', help='Return workspace/runtime state')
    status.add_argument('--tail', type=int, default=80)
    context = sub.add_parser('context', help='Create a pasteable agent context bundle')
    context.add_argument('--tail', type=int, default=120)
    context.add_argument('--format', choices=['markdown', 'json'], default='markdown')
    context.add_argument('--output', type=Path)
    tasks = sub.add_parser('tasks', help='List declarative tasks')
    tasks.add_argument('--file', type=Path, default=TASKS)
    task = sub.add_parser('task', help='Run one declarative task and emit JSON')
    task.add_argument('name')
    task.add_argument('--file', type=Path, default=TASKS)
    return p


def main():
    args = parser().parse_args()
    if args.serial_socket is not None:
        args.serial_port = args.serial_socket
    try:
        if args.action == 'exec':
            if args.command[:1] == ['--']:
                args.command = args.command[1:]
            if not args.command:
                raise ValueError('exec requires a command after --')
            result = serial_exec(args.serial_port, ' '.join(args.command), args.timeout)
            print(json.dumps(result, indent=2) if args.json else result['stdout'], end='' if not args.json else '\n')
            return result['exit_code']
        if args.action == 'put':
            print(json.dumps(guest_put(args.serial_port, args.source, args.destination), indent=2))
        elif args.action == 'get':
            print(json.dumps(guest_get(args.serial_port, args.source, args.destination), indent=2))
        elif args.action == 'ls':
            script = (f"find {shlex.quote(args.path)} -mindepth 1 -maxdepth 1 -printf "
                      "'%y\\t%s\\t%f\\n' | sort -k3")
            result = require_success(serial_exec(args.serial_port, script))
            entries = [{'type': row.split('\t', 2)[0], 'bytes': int(row.split('\t', 2)[1]),
                        'name': row.split('\t', 2)[2]} for row in result['stdout'].splitlines() if row.count('\t') >= 2]
            print(json.dumps({'path': args.path, 'entries': entries}, indent=2))
        elif args.action == 'inspect':
            print(json.dumps(inspect(args.workspace, args.qmp_port, args.tail), indent=2))
        elif args.action == 'context':
            state = inspect(args.workspace, args.qmp_port, args.tail)
            content = json.dumps(state, indent=2) + '\n' if args.format == 'json' else context_markdown(state)
            if args.output:
                if args.output.exists():
                    raise FileExistsError(args.output)
                args.output.write_text(content)
                print(args.output.resolve())
            else:
                print(content, end='')
        elif args.action == 'tasks':
            print(json.dumps(load_tasks(args.file), indent=2))
        elif args.action == 'task':
            tasks = load_tasks(args.file)
            if args.name not in tasks:
                raise ValueError(f'Unknown task: {args.name}')
            result = run_task(args.name, tasks[args.name], args.serial_port)
            print(json.dumps(result, indent=2))
            return result['exit_code']
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc), 'action': args.action}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
