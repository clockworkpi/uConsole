"""Deploy/run/restore the forge's inert proof application over existing SSH."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
import time

from forge_target_backup import capture
from forge_target_journal import prepare
from forge_target_ssh import dispatch


def ssh_run(host, command, timeout=30):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                           host, command], text=True, capture_output=True, timeout=timeout)


def boot_state(host):
    result = ssh_run(host, 'cat /proc/sys/kernel/random/boot_id /etc/machine-id; '
                    'sha256sum /boot/firmware/config.txt /boot/firmware/cmdline.txt; '
                    'systemctl is-system-running', timeout=10)
    lines = result.stdout.splitlines()
    if (len(lines) != 5 or not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', lines[0]) or
            not re.fullmatch('[0-9a-f]{32}', lines[1]) or
            any(not re.fullmatch('[0-9a-f]{64}  /boot/firmware/' + re.escape(name), line)
                for name, line in zip(('config.txt', 'cmdline.txt'), lines[2:4]))):
        raise ValueError('Incomplete target boot-state capture')
    return {'boot_id': lines[0], 'machine_id': lines[1], 'native_boot_hashes': lines[2:4],
            'system_state': lines[4]}


def wait_reboot(host, before, timeout=300):
    deadline = time.monotonic() + timeout
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            current = boot_state(host)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            current = None
        if current is not None:
            if current['machine_id'] != before['machine_id']:
                raise ValueError('Different target answered after reboot')
            if current['native_boot_hashes'] != before['native_boot_hashes']:
                raise ValueError('Native boot configuration changed')
            if current['boot_id'] != before['boot_id'] and current['system_state'] == 'running':
                return dict(current, reconnect_attempts=attempts)
        threading.Event().wait(min(2, max(0, deadline - time.monotonic())))
    raise TimeoutError('Target did not return with a new boot ID and running system; retain restore journal')


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--host', required=True)
    cli.add_argument('--source', type=Path, required=True)
    cli.add_argument('--sha256', required=True)
    cli.add_argument('--proof', required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--reboot', action='store_true', help='Explicitly reboot the physical target during validation')
    transport = cli.add_mutually_exclusive_group()
    transport.add_argument('--mcp', action='store_true', help='Apply and restore through a real stdio MCP owner')
    transport.add_argument('--gui', action='store_true', help='Invoke actual Workbench physical-target buttons')
    transport.add_argument('--gui-author', action='store_true', help='Also prepare and approve through Workbench dialogs')
    args = cli.parse_args()
    if not re.fullmatch('attached-forge-[0-9a-f]{32}', args.proof):
        raise ValueError('Expected an emulator proof application name')
    data = args.source.read_bytes()
    if (hashlib.sha256(data).hexdigest() != args.sha256 or
            data != ('#!/bin/sh\nprintf "%s\\n" "' + args.proof + '"\n').encode()):
        raise ValueError('Only the exact inert emulator proof application is supported')
    output = args.output.resolve()
    output.mkdir(mode=0o700)
    path = '/usr/local/bin/' + args.proof
    evidence = {'status': 'failed', 'scope': 'live application deploy/run/restore, not full-image boot',
                'application_sha256': args.sha256, 'application_path': path}
    applied = False
    client = None
    transaction = output / 'transaction'
    try:
        evidence['backup'] = capture(args.host, [path], output / 'before.json')
        before = json.loads((output / 'before.json').read_text())
        if before['files'] != [{'path': path, 'kind': 'absent'}]:
            raise ValueError('Proof path already exists; preserve it and choose another fixture')
        timestamp = time.time_ns()
        after = dict(before, files=[{'path': path, 'kind': 'file', 'size': len(data),
            'data': base64.b64encode(data).decode(), 'sha256': args.sha256,
            'mode': 0o755, 'uid': 0, 'gid': 0, 'atime_ns': timestamp, 'mtime_ns': timestamp, 'xattrs': {}}])
        if args.gui_author:
            from target_gui_validation import GUIAuthorTransitions
            client = GUIAuthorTransitions(output, args.host, args.source, args.proof, args.sha256).start()
            evidence['journal'] = client.prepared
        else:
            evidence['journal'] = prepare(transaction, args.host, before, after)
        if args.mcp:
            from target_mcp_validation import MCPTransitions
            client = MCPTransitions(output, evidence['journal']).start()
        elif args.gui:
            from target_gui_validation import GUITransitions
            client = GUITransitions(output, evidence['journal']).start()
        transition = client.transition if client else lambda direction: dispatch(transaction, direction)
        evidence['transport'] = ('tk-workbench-author' if args.gui_author else
                                 ('tk-workbench' if args.gui else ('stdio-mcp' if client else 'ssh-direct')))
        if args.reboot:
            evidence['boot_before'] = boot_state(args.host)
            if (evidence['boot_before']['machine_id'] != before['machine_id'] or
                    evidence['boot_before']['system_state'] != 'running'):
                raise ValueError('Target must be the backed-up machine with a running system before reboot test')
        evidence['apply'] = transition('apply')
        applied = True
        result = ssh_run(args.host, path)
        evidence['application_test'] = {'exit_code': result.returncode, 'stdout': result.stdout,
                                        'stderr': result.stderr}
        if result.returncode or result.stdout != args.proof + '\n':
            raise ValueError('Physical application test failed')
        if args.reboot:
            print('Requesting physical target reboot; waiting for new boot identity and SSH.', flush=True)
            # Persist recovery location before the connection is deliberately interrupted.
            (output / 'acceptance.json').write_text(json.dumps(evidence, indent=2) + '\n')
            try:
                reboot = ssh_run(args.host, 'sudo -n systemctl reboot')
                evidence['reboot_request_exit'] = reboot.returncode
            except subprocess.TimeoutExpired:
                evidence['reboot_request_exit'] = 'unacknowledged-timeout'
            # Neither SSH success nor disconnect proves that reboot happened.
            evidence['boot_after'] = wait_reboot(args.host, evidence['boot_before'])
            result = ssh_run(args.host, path)
            evidence['post_reboot_application'] = {'exit_code': result.returncode, 'stdout': result.stdout}
            if result.returncode or result.stdout != args.proof + '\n':
                raise ValueError('Application did not survive physical reboot')
        evidence['status'] = 'application-tested'
    except BaseException as exc:
        evidence['error'] = str(exc)
        raise
    finally:
        try:
            if applied:
                evidence['restore'] = transition('restore')
                evidence['restored_capture'] = capture(args.host, [path], output / 'restored.json')
                restored = json.loads((output / 'restored.json').read_text())
                if restored['machine_id'] != before['machine_id'] or restored['files'] != before['files']:
                    raise ValueError('Restored target does not match preimage')
                if evidence['status'] == 'application-tested':
                    evidence['status'] = 'passed'
        except BaseException as exc:
            evidence['status'] = 'restore-unverified'
            evidence['restore_error'] = str(exc)
            raise
        finally:
            try:
                if client is not None:
                    client.close()
            except BaseException as exc:
                evidence['status'] = 'owner-shutdown-unverified'
                evidence['owner_shutdown_error'] = str(exc)
                raise
            finally:
                (output / 'acceptance.json').write_text(json.dumps(evidence, indent=2) + '\n')
                print(f'Target application round trip: {evidence["status"]}; {output / "acceptance.json"}')


if __name__ == '__main__':
    main()
