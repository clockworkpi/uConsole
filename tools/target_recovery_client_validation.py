"""Separate attached MCP process; never creates another physical-target owner."""
from pathlib import Path
import json
import subprocess
import sys
import time

from target_mcp_validation import MCPTransitions
from uconsole_mcp import PROTOCOL


def inspect(endpoint, output, prepared, policy, gui_job):
    client = MCPTransitions(output, prepared)
    client.log = (Path(output)/'attached-mcp-stderr.log').open('x')
    client.transcript = (Path(output)/'attached-mcp-transcript.jsonl').open('x')
    try:
        client.process = subprocess.Popen([sys.executable,
            str(Path(__file__).with_name('uconsole_mcp.py')), '--connect', str(endpoint)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=client.log, text=True, bufsize=1)
        initialized = client.request('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {},
            'clientInfo': {'name': 'attached-target-inspection', 'version': '1'}})
        if initialized['protocolVersion'] != PROTOCOL:
            raise ValueError('Unexpected MCP protocol')
        client.request('notifications/initialized', notification=True)
        state = client.call('workspace_inspect', {'workspace': 'gui'})
        if state['grants'] != ['target-write'] or not state.get('attached'):
            raise ValueError('Attached client authority differs')
        previous = client.call('job_status', {'job_id': gui_job['job_id']})
        if previous != gui_job:
            raise ValueError('Attached client cannot observe the same GUI job')
        resources = {}
        for kind in ('jobs', 'tasks', 'targets'):
            response = client.request('resources/read', {'uri': 'forge://workspace/gui/' + kind})
            resources[kind] = json.loads(response['contents'][0]['text'])
        if (not any(j['job_id'] == gui_job['job_id'] for j in resources['jobs']['jobs'])
                or resources['tasks']['execution_granted']
                or not resources['targets']['execution_granted']
                or [t['name'] for t in resources['targets']['transactions']] != ['proof']):
            raise ValueError('Attached resources differ from scoped owner evidence')
        denial = client.request('tools/call', {'name': 'boot', 'arguments': {'workspace': 'gui'}})
        if not denial.get('isError'):
            raise ValueError('Target-only client unexpectedly acquired boot authority')
        submitted = client.call('target_recovery_inspect', {'workspace': 'gui', 'transaction': 'proof'})
        deadline = time.monotonic() + 60
        while True:
            job = client.call('job_status', {'job_id': submitted['job_id']})
            if job['status'] in ('completed', 'failed', 'cancelled'):
                break
            if time.monotonic() > deadline:
                raise TimeoutError('Attached inspection deadline')
            time.sleep(0.05)
        if job['context']['policy_sha256'] != policy:
            raise ValueError('Attached client policy differs')
        client.close()
        return dict(job=job, gui_job_observed=True, boot_without_grant_denied=True,
                    process_exit=client.process.returncode, resources=resources)
    finally:
        if client.process is not None and client.process.poll() is None:
            client.close()
        elif client.process is None:
            client.log.close()
            client.transcript.close()
