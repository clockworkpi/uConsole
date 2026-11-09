"""Real stdio MCP transport used by physical round-trip qualification."""
import hashlib
import json
from pathlib import Path
import select
import subprocess
import sys
import threading
import time

from uconsole_mcp import PROTOCOL


class MCPTransitions:
    def __init__(self, output, prepared):
        self.output = Path(output)
        self.prepared = prepared
        self.process = None
        self.log = None
        self.transcript = None
        self.next_id = 0
        self.pin = 'authorization_sha256' if prepared.get('kind') == 'service' else 'plan_sha256'

    def start(self):
        policy = self.output / 'target-policy.json'
        with policy.open('x') as stream:
            entry = {'workspace': 'target', 'journal': self.prepared['journal'], self.pin: self.prepared[self.pin]}
            if self.pin == 'authorization_sha256':
                entry['kind'] = 'service'
            json.dump({'schema': 1, 'transactions': {'proof': entry}}, stream)
        self.policy_sha256 = hashlib.sha256(policy.read_bytes()).hexdigest()
        self.log = (self.output / 'mcp-stderr.log').open('x')
        self.transcript = (self.output / 'mcp-transcript.jsonl').open('x')
        try:
            self.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('uconsole_mcp.py')),
                '--workspace', 'target=' + str(self.output), '--history', str(self.output / '.history/jobs.sqlite3'),
                '--allow', 'target-write', '--target-policy', str(policy),
                '--target-policy-sha256', self.policy_sha256],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1)
            result = self.request('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {},
                                  'clientInfo': {'name': 'physical-target-acceptance', 'version': '1'}})
            if result['protocolVersion'] != PROTOCOL:
                raise ValueError('Unexpected MCP protocol')
            self.request('notifications/initialized', notification=True)
            listing = self.call('target_transactions', {'workspace': 'target'})
            expected = {'name': 'proof', self.pin: self.prepared[self.pin], 'policy_sha256': self.policy_sha256}
            if self.pin == 'authorization_sha256':
                expected['kind'] = 'service'
            if listing != {'execution_granted': True, 'transactions': [expected]}:
                raise ValueError('Owner transaction listing differs from approved policy')
            return self
        except BaseException:
            self.close()
            raise

    def request(self, method, params=None, *, notification=False):
        self.next_id += 1
        message = {'jsonrpc': '2.0', 'method': method, 'params': params or {}}
        if not notification:
            message['id'] = self.next_id
        self.transcript.write(json.dumps({'request': message}) + '\n')
        self.transcript.flush()
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()
        if notification:
            return None
        if not select.select([self.process.stdout], [], [], 15)[0]:
            raise TimeoutError('MCP reply deadline; retain transaction journal')
        reply = json.loads(self.process.stdout.readline())
        self.transcript.write(json.dumps({'response': reply}) + '\n')
        self.transcript.flush()
        if reply.get('id') != self.next_id or 'error' in reply:
            raise ValueError('MCP request failed: ' + str(reply))
        return reply['result']

    def call(self, name, arguments):
        result = self.request('tools/call', {'name': name, 'arguments': arguments})
        if result.get('isError'):
            raise RuntimeError('MCP tool failed: ' + str(result))
        return result['structuredContent']

    def transition(self, direction):
        submitted = self.call('target_transition', {'workspace': 'target', 'transaction': 'proof',
                                                  'direction': direction})
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = self.call('job_status', {'job_id': submitted['job_id']})
            if job['status'] in ('completed', 'failed', 'cancelled'):
                if job['status'] != 'completed':
                    raise RuntimeError('Target job did not complete successfully: ' + str(job))
                if (job['context'][self.pin] != self.prepared[self.pin] or
                        job['context']['policy_sha256'] != self.policy_sha256):
                    raise ValueError('Target job provenance mismatch')
                return job
            threading.Event().wait(0.1)
        raise TimeoutError('Target MCP job deadline; retain journal and reconcile, do not blindly restore')

    def close(self):
        try:
            if self.process is not None:
                if not self.process.stdin.closed:
                    self.process.stdin.close()
                # Let the owner finish accepted jobs and durable history. Never
                # terminate it just because the client lost an acknowledgement.
                self.process.wait(timeout=120)
                self.process.stdout.close()
                if self.process.returncode != 0:
                    raise RuntimeError('MCP owner exited unsuccessfully; inspect stderr and durable history')
        finally:
            if self.log is not None:
                self.log.close()
            if self.transcript is not None:
                self.transcript.close()
