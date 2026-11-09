"""Real stdio MCP attachment to an owner-controlled audio guest."""
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from forge_client import ClientSession
from forge_controller import Controller
from forge_local import LocalListener
from target_mcp_validation import MCPTransitions
from uconsole_mcp import PROTOCOL


class MCPAudio:
    def __init__(self, runtime, output):
        self.controller = Controller({'audio': output}, ('device-control',),
                                     history=output / 'audio-mcp-jobs.sqlite3')
        self.controller.runtimes['audio'] = runtime
        self.directory = tempfile.TemporaryDirectory(prefix='uc-audio-mcp-')
        self.listener = self.rpc = None
        try:
            endpoint = Path(self.directory.name) / 'mcp.sock'
            self.listener = LocalListener(endpoint, lambda: ClientSession(
                self.controller, ['audio'], ('device-control',)))
            # Reuse the established JSON-RPC framing/transcript transport only;
            # physical target policy preparation and transitions are not used.
            self.rpc = MCPTransitions(output, {})
            self.rpc.log = (output / 'mcp-stderr.log').open('x')
            self.rpc.transcript = (output / 'mcp-transcript.jsonl').open('x')
            self.rpc.process = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name('uconsole_mcp.py')),
                 '--connect', str(endpoint)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=self.rpc.log, text=True, bufsize=1)
            initialized = self.rpc.request('initialize', {
                'protocolVersion': PROTOCOL, 'capabilities': {},
                'clientInfo': {'name': 'audio-acceptance', 'version': '1'}})
            if initialized['protocolVersion'] != PROTOCOL:
                raise ValueError('Unexpected audio MCP protocol')
            self.rpc.request('notifications/initialized', notification=True)
            denied = self.rpc.request('tools/call', {'name': 'guest_exec',
                'arguments': {'workspace': 'audio', 'script': 'true'}})
            if (not denied.get('isError') or
                    'Client was not granted authority for guest_exec' not in str(denied)):
                raise ValueError('Audio client unexpectedly obtained guest execution authority')
            self.initial = self.wait_job(self.rpc.call('audio_query', {'workspace': 'audio'}))
            if self.initial['result']['connected'] is not True:
                raise ValueError('MCP did not observe initial connected audio')
        except BaseException:
            self.close()
            raise

    def wait_job(self, submitted):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            job = self.rpc.call('job_status', {'job_id': submitted['job_id']})
            if job['status'] in ('completed', 'failed', 'cancelled'):
                if job['status'] != 'completed':
                    raise ValueError('MCP audio job failed: ' + str(job))
                return job
            threading.Event().wait(0.05)
        raise TimeoutError('MCP audio job deadline; inspect retained history')

    def operation(self, runtime, evidence, connected):
        job = self.wait_job(self.rpc.call('audio_set', {
            'workspace': 'audio', 'connected': connected}))
        if (job['context']['connected'] is not connected or
                job['result']['observed']['connected'] is not connected):
            raise ValueError('MCP audio requested/observed state mismatch')
        return {'frontend': 'stdio-mcp-attached', 'job': job}

    def close(self):
        if self.rpc is not None:
            self.rpc.close()
            self.rpc = None
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        self.controller.executor.shutdown(wait=True)
        self.controller.runtimes.clear()
        self.controller.close()
        self.directory.cleanup()
