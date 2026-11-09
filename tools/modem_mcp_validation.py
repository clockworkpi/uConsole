"""Separate stdio MCP process used by the live Workbench modem acceptance."""
import json
from pathlib import Path
import select
import subprocess
import sys
import time

from uconsole_mcp import PROTOCOL


def change(endpoint, registration):
    transcript = []
    process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('uconsole_mcp.py')),
                                '--connect', str(endpoint)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        sequence = 0
        def request(method, params):
            nonlocal sequence
            sequence += 1
            message = dict(jsonrpc='2.0', id=sequence, method=method, params=params)
            process.stdin.write((json.dumps(message)+'\n').encode())
            process.stdin.flush()
            if not select.select([process.stdout], [], [], 20)[0]:
                raise TimeoutError('Attached modem client response deadline')
            reply = json.loads(process.stdout.readline(262145))
            transcript.append(dict(request=message, response=reply))
            if reply.get('id') != sequence or 'error' in reply:
                raise RuntimeError('Unexpected MCP response: ' + str(reply))
            return reply['result']
        def call(name, arguments):
            result = request('tools/call', dict(name=name, arguments=arguments))
            if result.get('isError'):
                raise RuntimeError(str(result))
            return result['structuredContent']
        request('initialize', dict(protocolVersion=PROTOCOL, capabilities={},
                                  clientInfo=dict(name='external-modem-proof', version='1')))
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()
        state = call('workspace_inspect', {'workspace':'gui'})
        if state['grants'] != ['device-control'] or not state['runtime']['owned']:
            raise RuntimeError('Attached client scope or runtime ownership differs')
        denial = request('tools/call', {'name':'boot', 'arguments':{'workspace':'gui'}})
        if not denial.get('isError'):
            raise RuntimeError('Device-control client unexpectedly acquired boot authority')
        job = call('modem_set', {'workspace':'gui', 'registration':registration})
        deadline = time.monotonic()+30
        while True:
            result = call('job_status', {'job_id':job['job_id']})
            if result['status'] in ('completed','failed','cancelled'):
                break
            if time.monotonic() > deadline:
                raise TimeoutError('Attached modem job deadline')
            time.sleep(0.05)
        if result['status'] != 'completed':
            raise RuntimeError(str(result))
        query_job = call('modem_query', {'workspace':'gui'})
        deadline = time.monotonic()+30
        while True:
            observed = call('job_status', {'job_id':query_job['job_id']})
            if observed['status'] in ('completed','failed','cancelled'):
                break
            if time.monotonic() > deadline:
                raise TimeoutError('Attached modem query deadline')
            time.sleep(0.05)
        if (observed['status'] != 'completed' or observed['result']['status'] != 'observed' or
                observed['result']['observed']['registration'] != registration):
            raise RuntimeError('Read-only modem query differs')
        process.stdin.close()
        process.wait(timeout=10)
        if process.returncode:
            raise RuntimeError('Attached MCP process did not exit cleanly')
        return dict(job=result, query=observed, transcript=transcript, process_exit=process.returncode,
                    boot_without_grant_denied=True)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
