#!/usr/bin/env python3
"""Real Tk/controller guest-job and transfer acceptance on a disposable image."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import tkinter as tk
from unittest.mock import patch
import uuid

from forge_filesystem import check_overlay_root
from forge_workspace import WorkspaceLock
from uconsole_workbench import Workbench


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--workspace', type=Path, required=True)
    cli.add_argument('--cancel-boot', action='store_true', help='Only test live boot cancellation on this disposable image')
    options = cli.parse_args()
    workspace = options.workspace.resolve()
    identity = uuid.uuid4().hex
    directory = workspace / ('gui-transfer-' + identity)
    directory.mkdir(mode=0o700)
    source, received = directory / 'source.txt', directory / 'received.txt'
    payload = 'GUI controller transfer proof ' + identity + '\n'
    digest = hashlib.sha256(payload.encode()).hexdigest()
    guest = '/var/tmp/uconsole-gui-transfer-' + identity
    root = tk.Tk()
    app = Workbench(root, workspace)
    evidence = {'validation': 'failed', 'guest_fixture': guest}
    errors = []
    state = 'boot'
    job_id = None
    runtime = None
    deadline = time.monotonic() + 180

    def completed():
        result = app.controller.job(job_id)
        if result['status'] != 'completed':
            raise ValueError(f'GUI transfer failed: {result}')
        return result

    def drive():
        nonlocal state, job_id, deadline, runtime
        try:
            if time.monotonic() > deadline:
                raise TimeoutError('GUI transfer acceptance timed out in ' + state)
            if options.cancel_boot:
                owned = app.controller.runtimes.get('gui')
                if state == 'boot' and owned and owned.process.poll() is None:
                    if app.boot_job is None:
                        raise ValueError('Boot completed before live cancellation could be requested')
                    runtime = owned
                    evidence['observed_live_pid'] = runtime.process.pid
                    app.cancel_boot()
                    state = 'cancel-boot'
                elif state == 'cancel-boot' and app.boot_job is None and app.process is None:
                    evidence['boot'] = app.controller.job(evidence['boot_job_id'])
                    if evidence['boot']['status'] != 'cancelled' or runtime.process.poll() is None:
                        raise ValueError('Boot cancellation did not terminate the owned VM')
                    with WorkspaceLock(workspace):
                        pass
                    evidence['ownership_released'] = True
                    evidence['validation'] = 'passed'
                    root.quit()
                    return
                root.after(50, drive)
                return
            output = app.console.get('1.0', 'end')
            if state == 'boot' and app.serial is not None and 'root@(none):/#' in output:
                runtime = app.runtime
                evidence['boot'] = app.controller.job(evidence['boot_job_id'])
                if evidence['boot']['status'] != 'completed':
                    raise ValueError('Guest channel became available without completed boot job')
                with patch('uconsole_workbench.history_default_path', return_value=directory / 'jobs.sqlite3'):
                    app.guest_files()
                job_id = app.guest_job
                state = 'listing'
                deadline = time.monotonic() + 90
            elif state == 'listing' and app.guest_job is None:
                evidence['listing'] = completed()
                listing = json.loads(evidence['listing']['result']['stdout'])
                if not any(entry['name'] == 'etc' and entry['type'] == 'd' for entry in listing['entries']):
                    raise ValueError('Guest root listing did not contain /etc')
                app.filename = source
                app.editor.delete('1.0', 'end')
                app.editor.insert('1.0', payload)
                with patch('uconsole_workbench.simpledialog.askstring', return_value=guest), \
                        patch('uconsole_workbench.history_default_path', return_value=directory / 'jobs.sqlite3'):
                    app.copy_to_guest()
                job_id = app.transfer
                evidence['upload_job_id'] = job_id
                state = 'upload'
                deadline = time.monotonic() + 90
            elif state == 'upload' and app.transfer is None:
                evidence['upload'] = completed()
                app.start_transfer('download', received, guest, 'Download')
                job_id = app.transfer
                evidence['download_job_id'] = job_id
                state = 'download'
                deadline = time.monotonic() + 90
            elif state == 'download' and app.transfer is None:
                evidence['download'] = completed()
                if received.read_bytes() != payload.encode() or source.read_bytes() != payload.encode():
                    raise ValueError('GUI file round trip changed bytes')
                evidence['sha256'] = digest
                app.release_serial()
                # Only remove this validator's UUID fixture after checking its
                # expected digest. This is not a general guest deletion command.
                result = runtime.execute(
                    f'test "$(sha256sum {guest} | cut -d " " -f1)" = {digest} && rm -- {guest}')
                evidence['fixture_cleanup'] = result
                if result['exit_code']:
                    raise ValueError('Guest fixture cleanup failed')
                app.start_guest_command("exec /usr/bin/python3 -c 'import time; time.sleep(60)'",
                                        'Deadline fixture', timeout=1)
                job_id = app.guest_job
                state = 'deadline'
                deadline = time.monotonic() + 90
            elif state == 'deadline' and app.guest_job is None:
                evidence['deadline'] = completed()
                result = evidence['deadline']['result']
                if result['exit_code'] != 124 or not result.get('timed_out') or not result.get('process_group_terminated'):
                    raise ValueError(f'Guest timeout was not acknowledged: {result}')
                app.start_guest_command("exec /usr/bin/python3 -c 'import time; time.sleep(60)'",
                                        'Cancellation fixture', timeout=90)
                job_id = app.guest_job
                state = 'cancel-running'
                deadline = time.monotonic() + 90
            elif state == 'cancel-running' and runtime.active_guest_job:
                evidence['cancelled_process_group'] = runtime.active_guest_job['process_group']
                app.cancel_guest()
                state = 'cancel-cleanup'
            elif state == 'cancel-cleanup' and app.guest_job is None:
                result = app.controller.job(job_id)
                evidence['cancellation'] = result
                if result['status'] != 'cancelled' or runtime.guest_channel_error:
                    raise ValueError(f'Guest cancellation was not acknowledged: {result}')
                group = evidence['cancelled_process_group']
                app.start_guest_command(f'! kill -0 -- -{group} 2>/dev/null && printf "channel reusable\\n"',
                                        'Post-cancellation probe')
                job_id = app.guest_job
                state = 'reuse'
                deadline = time.monotonic() + 90
            elif state == 'reuse' and app.guest_job is None:
                evidence['channel_reuse'] = completed()
                result = evidence['channel_reuse']['result']
                if result['exit_code'] != 0 or result['stdout'] != 'channel reusable\n':
                    raise ValueError(f'Process group survived or channel reuse failed: {result}')
                state = 'stop'
            elif state == 'stop' and app.serial is not None:
                app.poweroff()
                job_id = app.guest_job
                state = 'stopping'
                deadline = time.monotonic() + 90
            elif state == 'stopping' and app.process is None:
                evidence['clean_shutdown'] = completed()
                if evidence['clean_shutdown']['result'] != {'stopped': True}:
                    raise ValueError('Clean shutdown controller result was not confirmed')
                if runtime.process.returncode != 0:
                    raise ValueError('QEMU did not exit successfully')
                evidence['root_after_stop'] = check_overlay_root(workspace, runtime.args.qemu_img)
                evidence['validation'] = 'passed'
                root.quit()
                return
            root.after(100, drive)
        except BaseException as exc:
            errors.append(exc)
            root.quit()

    try:
        with patch('uconsole_workbench.history_default_path', return_value=directory / 'jobs.sqlite3'):
            app.start()
        evidence['boot_job_id'] = app.boot_job
        root.after(100, drive)
        root.mainloop()
    finally:
        if app.controller:
            if app.boot_job is not None:
                app.controller.cancel(app.boot_job)
            if app.transfer is not None:
                app.controller.cancel(app.transfer)
            if app.guest_job is not None:
                app.controller.cancel(app.guest_job)
            runtime = runtime or app.controller.runtimes.get('gui')
            app.controller.close()
        if runtime and runtime.process.poll() is None:
            evidence['forced_cleanup'] = True
            runtime.stop(force=True)
        if errors:
            evidence['error'] = str(errors[0])
        evidence['console'] = app.console.get('1.0', 'end')
        (directory / 'record.json').write_text(json.dumps(evidence, indent=2) + '\n')
        root.destroy()
        print(f'GUI transfer validation: {evidence["validation"]}; {directory / "record.json"}')
    if errors:
        raise errors[0]


if __name__ == '__main__':
    main()
