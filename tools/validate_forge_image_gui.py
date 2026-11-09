#!/usr/bin/env python3
"""Run a real GUI image job and reconnect to its durable job history.

Run under a desktop or Xvfb with a stopped disposable prepared workspace.
This refreshes host boot artifacts or prepares conditional desktop adapters.
Preparation is a no-op on an already configured workspace. This does not
claim physical boot compatibility or full GUI/controller parity.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk
from unittest.mock import patch
import uuid

from uconsole_mcp import PROTOCOL
from uconsole_workbench import Workbench


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--workspace', type=Path, required=True)
    cli.add_argument('--operation', choices=('refresh-boot', 'configure-display'), default='refresh-boot')
    options = cli.parse_args()
    workspace = options.workspace.resolve()
    directory = workspace / ('gui-image-' + uuid.uuid4().hex)
    directory.mkdir(mode=0o700)
    history = directory / 'jobs.sqlite3'
    evidence = {'validation': 'failed', 'workspace': str(workspace), 'operation': options.operation}
    root = tk.Tk()
    app = Workbench(root, workspace)
    deadline = time.monotonic() + 180
    errors = []
    job_id = None

    def poll():
        try:
            if time.monotonic() > deadline:
                raise TimeoutError('GUI image operation did not complete within 180 seconds')
            if app.lifecycle is not None:
                root.after(100, poll)
                return
            result = app.controller.job(job_id)
            evidence['gui_result'] = result
            if result['status'] != 'completed' or result['result']['exit_code'] != 0:
                raise ValueError(f'GUI image operation failed: {result}')
            evidence['gui_status'] = app.status.get()
            messages = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                    'protocolVersion': PROTOCOL, 'capabilities': {},
                    'clientInfo': {'name': 'gui-history-proof', 'version': '1'}}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
                    'name': 'job_status', 'arguments': {'job_id': job_id}}},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
                    'name': options.operation.replace('-', '_'), 'arguments': {'workspace': 'agent'}}},
            ]
            # A distinct read-only MCP process, not another in-process facade.
            reply = subprocess.run([
                sys.executable, str(Path(__file__).with_name('uconsole_mcp.py')),
                '--workspace', f'agent={workspace}', '--history', str(history)],
                input=''.join(json.dumps(item) + '\n' for item in messages),
                text=True, capture_output=True, timeout=15, check=True)
            responses = [json.loads(line) for line in reply.stdout.splitlines()]
            evidence['mcp_transcript'] = {'requests': messages, 'responses': responses,
                                          'stderr': reply.stderr}
            if [item.get('id') for item in responses] != [1, 2, 3]:
                raise ValueError('Unexpected MCP response sequence')
            record = responses[1]['result']['structuredContent']
            if not record.get('historical') or record['status'] != 'completed':
                raise ValueError(f'Reconnect did not find completed GUI job: {record}')
            evidence['reconnected_result'] = record
            denial = responses[2]['result']
            if not denial.get('isError') or 'image-write' not in denial['content'][0]['text']:
                raise AssertionError('Read-only MCP observer did not deny image write')
            evidence['read_only_observer_enforced'] = True
            evidence['validation'] = 'passed'
            root.quit()
        except BaseException as exc:
            errors.append(exc)
            root.quit()

    try:
        with patch('uconsole_workbench.history_default_path', return_value=history):
            app.start_lifecycle([options.operation], options.operation)
        job_id = app.lifecycle
        evidence['job_id'] = job_id
        root.after(100, poll)
        root.mainloop()
    finally:
        if app.controller:
            if app.lifecycle is not None:
                app.controller.cancel(app.lifecycle)
            app.controller.close()
        if errors:
            evidence['error'] = str(errors[0])
        evidence['console'] = app.console.get('1.0', 'end')
        (directory / 'record.json').write_text(json.dumps(evidence, indent=2) + '\n')
        root.destroy()
        print(f'GUI image job validation: {evidence["validation"]}; {directory / "record.json"}')
    if errors:
        raise errors[0]


if __name__ == '__main__':
    main()
