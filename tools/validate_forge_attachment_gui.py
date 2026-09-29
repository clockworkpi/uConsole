#!/usr/bin/env python3
"""Real GUI-owned maintenance VM accessed by a separate stdio MCP client."""
import argparse
import hashlib
import json
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import uuid

from forge_filesystem import check_overlay_root
from forge_workspace import sha256
from emulator_image import BootPartition
from uconsole_mcp import PROTOCOL


def verify_peer(replies):
    if ([reply.get('id') for reply in replies] != [1, 2, 3, 4] or
            replies[1]['result']['structuredContent']['status'] != 'running'):
        raise ValueError('Peer did not observe the running owner job')
    for reply, reason in zip(replies[2:], ('Clients may cancel only their own submitted jobs',
                                         'Wait for the attached agent job to finish')):
        result = reply['result']
        if not result.get('isError') or reason not in result['content'][0]['text']:
            raise ValueError('Peer denial did not establish cancellation/exclusion policy')


def native_boot_hashes(image, destination):
    destination.mkdir(mode=0o700)
    result = {}
    with BootPartition(image) as boot:
        for name in ('config.txt', 'cmdline.txt', 'kernel8.img', 'bcm2711-rpi-cm4.dtb'):
            boot.extract(name, destination / name)
            result[name] = sha256(destination / name)
    return result


def main():
    import tkinter as tk
    from uconsole_workbench import Workbench
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--agent-boot', action='store_true', help='Have the attached client boot the GUI workspace')
    parser.add_argument('--agent-image-export', action='store_true',
                        help='With --agent-boot, retain a tested application in the guest and export after clean stop')
    args = parser.parse_args()
    if args.agent_image_export and not args.agent_boot:
        parser.error('--agent-image-export requires --agent-boot')
    workspace = args.workspace.resolve()
    if args.agent_image_export and shutil.disk_usage(workspace).free < (workspace / 'base.img').stat().st_size + 2 * 1024**3:
        raise ValueError('Image acceptance needs room for the full export plus a 2 GiB reserve')
    directory = workspace / ('gui-attachment-' + uuid.uuid4().hex)
    directory.mkdir(mode=0o700)
    files = directory / 'files'
    files.mkdir(mode=0o700)
    payload = bytes(range(256)) * 4
    (files / 'source.bin').write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    guest_fixture = '/var/tmp/uconsole-attachment-' + uuid.uuid4().hex
    socket_directory = tempfile.TemporaryDirectory(prefix='uc-attach-')
    endpoint = Path(socket_directory.name) / 'mcp.sock'
    root = tk.Tk()
    grants = ['guest-exec', 'transfer'] + (['boot', 'force-stop'] if args.agent_boot else [])
    if args.agent_image_export:
        grants.append('image-write')
    with patch('uconsole_workbench.history_default_path', return_value=directory / 'jobs.sqlite3'):
        app = Workbench(root, workspace, agent_socket=endpoint,
                        agent_grants=grants, agent_files_root=files)
    evidence = {'validation': 'failed', 'transcript': [], 'agent_initiated_boot': args.agent_boot}
    ready, done, abort = threading.Event(), threading.Event(), threading.Event()
    gui_connected = threading.Event()
    errors = []
    runtime = None
    deadline = time.monotonic() + (600 if args.agent_image_export else 180)
    state = 'boot'

    def agent():
        process = None
        try:
            if not ready.wait(130):
                raise TimeoutError('GUI guest did not become ready')
            if abort.is_set():
                return
            process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('uconsole_mcp.py')),
                                        '--connect', str(endpoint)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            sequence = 0
            def request(method, params):
                nonlocal sequence
                sequence += 1
                message = {'jsonrpc': '2.0', 'id': sequence, 'method': method, 'params': params}
                process.stdin.write((json.dumps(message) + '\n').encode())
                process.stdin.flush()
                if not select.select([process.stdout], [], [], 20)[0]:
                    raise TimeoutError('Attached MCP response timed out')
                reply = json.loads(process.stdout.readline())
                evidence['transcript'].append({'request': message, 'response': reply})
                if 'error' in reply:
                    raise ValueError(reply)
                return reply['result']
            def call(name, arguments):
                result = request('tools/call', {'name': name, 'arguments': arguments})
                if result.get('isError'):
                    raise ValueError(result)
                return result['structuredContent']
            def wait_job(job_id):
                until = time.monotonic() + 300
                while True:
                    result = call('job_status', {'job_id': job_id})
                    if result['status'] in ('completed', 'failed', 'cancelled'):
                        return result
                    if abort.is_set() or time.monotonic() > until:
                        raise TimeoutError('Attached job did not finish')
                    abort.wait(0.1)
            def job(name, arguments):
                result = wait_job(call(name, {'workspace': 'gui', **arguments})['job_id'])
                if result['status'] != 'completed':
                    raise ValueError(result)
                return result
            request('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {},
                                   'clientInfo': {'name': 'attached-gui-proof', 'version': '1'}})
            process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            process.stdin.flush()
            denied_tool = 'host_task' if args.agent_image_export else ('configure_display' if args.agent_boot else 'boot')
            denied_args = {'workspace': 'gui'}
            if denied_tool == 'host_task':
                denied_args['task'] = 'not-approved'
            denial = request('tools/call', {'name': denied_tool, 'arguments': denied_args})
            if not denial.get('isError'):
                raise ValueError('Attached client inherited unapproved mutation authority')
            evidence['ungranted_tool_denied'] = denied_tool
            if args.agent_boot:
                evidence['agent_boot'] = job('boot', {'mode': 'maintenance'})
                if not gui_connected.wait(15):
                    raise TimeoutError('GUI did not expose the agent-booted runtime and serial channel')
                if evidence['agent_boot']['result']['identity'] != evidence['gui_owned_identity']:
                    raise ValueError('GUI exposed a different runtime than the attached boot job')
            status = call('workspace_inspect', {'workspace': 'gui'})
            if not status['runtime']['owned'] or status['grants'] != sorted(grants):
                raise ValueError('Attachment did not inspect the restricted live GUI owner')
            result = job('guest_exec', {'script': "printf 'attached-agent-proof\\n'"})
            if result['status'] != 'completed' or result['result']['stdout'] != 'attached-agent-proof\n':
                raise ValueError(result)
            evidence['guest_job'] = result
            denied_path = request('tools/call', {'name': 'upload', 'arguments': {
                'workspace': 'gui', 'host_path': '../jobs.sqlite3', 'guest_path': guest_fixture}})
            if not denied_path.get('isError'):
                raise ValueError('Attached file request escaped its narrower files root')
            evidence['outside_files_root_denied'] = True
            evidence['upload'] = job('upload', {'host_path': 'source.bin', 'guest_path': guest_fixture})
            evidence['download'] = job('download', {'host_path': 'received.bin', 'guest_path': guest_fixture})
            if (files / 'received.bin').read_bytes() != payload:
                raise ValueError('Attached binary transfer changed bytes')
            evidence['transfer_sha256'] = digest
            submitted = call('guest_exec', {'workspace': 'gui', 'script': 'sleep 60', 'timeout': 120})
            until = time.monotonic() + 15
            while True:
                status = call('workspace_inspect', {'workspace': 'gui'})
                live = status['guest_job']
                if live:
                    group = live['process_group']
                    if type(group) is not int or group <= 1:
                        raise ValueError('Invalid supervised guest process group')
                    break
                if abort.is_set() or time.monotonic() > until:
                    raise TimeoutError('Guest cancellation fixture was not observed live')
                abort.wait(0.1)
            evidence['observed_guest_group'] = group
            peer_messages = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                    'protocolVersion': PROTOCOL, 'capabilities': {},
                    'clientInfo': {'name': 'attached-peer-proof', 'version': '1'}}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
                    'name': 'job_status', 'arguments': {'job_id': submitted['job_id']}}},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
                    'name': 'job_cancel', 'arguments': {'job_id': submitted['job_id']}}},
                {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {
                    'name': 'guest_exec', 'arguments': {'workspace': 'gui', 'script': 'true'}}},
            ]
            peer = subprocess.run([sys.executable, str(Path(__file__).with_name('uconsole_mcp.py')),
                                   '--connect', str(endpoint)],
                                  input=''.join(json.dumps(item) + '\n' for item in peer_messages),
                                  capture_output=True, text=True, timeout=15, check=True)
            replies = [json.loads(line) for line in peer.stdout.splitlines()]
            verify_peer(replies)
            evidence['peer_observation_and_denials'] = {'requests': peer_messages, 'responses': replies}
            cancelled = call('job_cancel', {'job_id': submitted['job_id']})
            if not cancelled['requested']:
                raise ValueError('Live attached cancellation was not accepted')
            evidence['cancelled_guest_job'] = wait_job(submitted['job_id'])
            if evidence['cancelled_guest_job']['status'] != 'cancelled':
                raise ValueError('Attached guest job did not reach terminal cancellation')
            reuse = job('guest_exec', {'script':
                f'! kill -0 -- -{group} 2>/dev/null && '
                f'test "$(sha256sum {guest_fixture} | cut -d" " -f1)" = {digest} && '
                f'rm -- {guest_fixture} && printf "attached-channel-reusable\\n"'})
            if reuse['result']['exit_code'] != 0 or reuse['result']['stdout'] != 'attached-channel-reusable\n':
                raise ValueError('Cancelled process group survived, or serial/fixture cleanup failed')
            evidence['channel_reuse_and_fixture_cleanup'] = reuse
            if args.agent_image_export:
                proof = 'attached-forge-' + uuid.uuid4().hex
                application = '/usr/local/bin/' + proof
                source = files / 'application.sh'
                source.write_text(f'#!/bin/sh\nprintf "%s\\n" "{proof}"\n')
                evidence['application_path'] = application
                evidence['application_sha256'] = sha256(source)
                evidence['application_upload'] = job('upload', {'host_path': source.name, 'guest_path': application})
                tested = job('guest_exec', {'script': f'chmod 755 {application} && {application}'})
                if tested['result']['exit_code'] != 0 or tested['result']['stdout'] != proof + '\n':
                    raise ValueError('Installed application did not pass its guest test')
                evidence['application_test'] = tested
                evidence['agent_stop'] = job('stop', {'force': False})
                evidence['refresh_boot'] = job('refresh_boot', {})
                evidence['native_boot_before'] = native_boot_hashes(workspace / 'base.img', directory / 'native-before')
                evidence['export'] = job('export', {'host_path': 'enhanced.img'})
                exported = files / 'enhanced.img'
                evidence['export_sha256'] = sha256(exported)
                evidence['native_boot_after'] = native_boot_hashes(exported, directory / 'native-after')
                if evidence['native_boot_before'] != evidence['native_boot_after']:
                    raise ValueError('Native boot files differ from the immutable imported base')
            process.stdin.close()
            if process.wait(timeout=5) != 0:
                raise ValueError(process.stderr.read().decode())
            evidence['client_disconnected'] = True
        except BaseException as exc:
            errors.append(exc)
        finally:
            if process:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
            done.set()

    worker = threading.Thread(target=agent, daemon=True)
    def poll():
        nonlocal state, runtime
        try:
            if errors:
                raise errors[0]
            if time.monotonic() > deadline:
                raise TimeoutError('Attachment acceptance deadline expired')
            if state == 'boot' and app.serial is not None:
                runtime = app.runtime
                evidence['gui_owned_pid'] = runtime.process.pid
                evidence['gui_owned_identity'] = runtime.identity
                ready.set()
                gui_connected.set()
                state = 'agent'
            elif (state == 'agent' and args.agent_image_export and done.is_set()
                  and app.agent_job is None):
                if app.process is not None or runtime.process.returncode != 0:
                    raise ValueError('Export workflow did not leave the GUI guest cleanly stopped')
                evidence['root_after_stop'] = check_overlay_root(workspace, runtime.args.qemu_img)
                evidence['validation'] = 'passed'
                root.quit()
                return
            elif state == 'agent' and done.is_set() and app.agent_job is None and app.serial is not None:
                if app.runtime is not runtime or runtime.process.poll() is not None:
                    raise ValueError('Client disconnect lost or replaced GUI runtime ownership')
                evidence['same_vm_after_disconnect'] = True
                app.poweroff()
                evidence['shutdown_job_id'] = app.guest_job
                state = 'shutdown'
            elif state == 'shutdown' and app.process is None:
                evidence['shutdown'] = app.controller.job(evidence['shutdown_job_id'])
                if evidence['shutdown']['status'] != 'completed' or runtime.process.returncode != 0:
                    raise ValueError('GUI clean shutdown failed after attachment')
                evidence['root_after_stop'] = check_overlay_root(workspace, runtime.args.qemu_img)
                evidence['validation'] = 'passed'
                root.quit()
                return
            root.after(50, poll)
        except BaseException as exc:
            if not errors:
                errors.append(exc)
            root.quit()
    try:
        if args.agent_boot:
            ready.set()
        else:
            app.start()
        worker.start()
        root.after(50, poll)
        root.mainloop()
    finally:
        abort.set()
        ready.set()
        gui_connected.set()
        app.close_attachment()
        if worker.ident is not None:
            worker.join(timeout=5)
            if worker.is_alive():
                errors.append(TimeoutError('Attachment client thread did not stop'))
        if app.controller:
            app.controller.close()
        if runtime and runtime.process.poll() is None:
            evidence['forced_cleanup'] = True
            runtime.stop(force=True)
        if errors:
            evidence['error'] = str(errors[0])
        evidence['console'] = app.console.get('1.0', 'end')
        (directory / 'record.json').write_text(json.dumps(evidence, indent=2) + '\n')
        root.destroy()
        socket_directory.cleanup()
        print(f'GUI attachment acceptance: {evidence["validation"]}; {directory / "record.json"}')
    if errors:
        raise errors[0]


if __name__ == '__main__':
    main()
