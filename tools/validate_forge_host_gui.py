#!/usr/bin/env python3
"""Real Tk/controller approved host-task execution and cancellation proof."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import tkinter as tk
from unittest.mock import patch
import uuid

from uconsole_workbench import Workbench


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--workspace', type=Path, required=True)
    options = cli.parse_args()
    workspace = options.workspace.resolve()
    directory = workspace / ('gui-host-' + uuid.uuid4().hex)
    directory.mkdir(mode=0o700)
    cwd = directory / 'work'
    cwd.mkdir()
    ready = cwd / 'ready'
    policy = directory / 'approved-policy.json'
    definitions = {
        'proof': {'workspace': 'gui', 'cwd': str(cwd), 'timeout': 20,
                  'argv': [str(Path(sys.executable).resolve()), '-c',
                           'import os; print("approved GUI host task"); print(os.getcwd())']},
        'cancel-proof': {'workspace': 'gui', 'cwd': str(cwd), 'timeout': 90,
                         'argv': [str(Path(sys.executable).resolve()), '-c',
                                  'from pathlib import Path; import os,time; '
                                  f'Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(60)']},
    }
    policy.write_text(json.dumps({'schema': 1, 'tasks': definitions}))
    digest = hashlib.sha256(policy.read_bytes()).hexdigest()
    root = tk.Tk()
    with patch('uconsole_workbench.history_default_path', return_value=directory / 'jobs.sqlite3'):
        app = Workbench(root, workspace, host_task_policy=policy, host_task_sha256=digest)
    evidence = {'validation': 'failed', 'approved_policy_sha256': digest}
    errors = []
    state = 'complete-proof'
    job_id = None
    deadline = time.monotonic() + 60

    def drive():
        nonlocal state, job_id
        try:
            if time.monotonic() > deadline:
                raise TimeoutError('Host task GUI validation timed out in ' + state)
            if state == 'complete-proof' and app.host_job is None:
                result = app.controller.job(job_id)
                evidence['execution'] = result
                if result['status'] != 'completed' or result['result']['exit_code']:
                    raise ValueError(f'Approved host task did not complete: {result}')
                if result['result']['stdout'] != f'approved GUI host task\n{cwd}\n':
                    raise ValueError('Host task output/cwd differed from the approved fixture')
                if result['context']['policy_sha256'] != digest:
                    raise ValueError('Job evidence did not preserve approved policy identity')
                try:
                    app.start_host_task('not-approved')
                except ValueError:
                    evidence['unapproved_name_denied'] = True
                else:
                    raise AssertionError('Unapproved host task accepted')
                app.start_host_task('cancel-proof')
                job_id = app.host_job
                state = 'observe-running'
            elif state == 'observe-running' and ready.exists():
                pid = int(ready.read_text())
                os.kill(pid, 0)  # Read-only liveness check of our fixture PID.
                evidence['observed_live_pid'] = pid
                app.cancel_host_task()
                state = 'cancel-cleanup'
            elif state == 'cancel-cleanup' and app.host_job is None:
                result = app.controller.job(job_id)
                evidence['cancellation'] = result
                if result['status'] != 'cancelled':
                    raise ValueError(f'Host cancellation did not reach terminal: {result}')
                try:
                    os.kill(evidence['observed_live_pid'], 0)
                except ProcessLookupError:
                    evidence['fixture_process_exited'] = True
                else:
                    raise ValueError('Fixture PID still exists after terminal cancellation')
                evidence['validation'] = 'passed'
                root.quit()
                return
            root.after(50, drive)
        except BaseException as exc:
            errors.append(exc)
            root.quit()

    try:
        app.start_host_task('proof')
        job_id = app.host_job
        root.after(50, drive)
        root.mainloop()
    finally:
        if app.host_job is not None:
            app.controller.cancel(app.host_job)
        app.controller.close()
        if errors:
            evidence['error'] = str(errors[0])
        evidence['console'] = app.console.get('1.0', 'end')
        (directory / 'record.json').write_text(json.dumps(evidence, indent=2) + '\n')
        root.destroy()
        print(f'GUI host-task validation: {evidence["validation"]}; {directory / "record.json"}')
    if errors:
        raise errors[0]


if __name__ == '__main__':
    main()
