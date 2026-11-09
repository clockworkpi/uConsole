"""Real Unix-socket MCP clients share one owner without acquiring VM ownership."""
import json
import os
from pathlib import Path
import socket
import select
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_client import ClientSession
from forge_controller import Controller
from forge_local import LocalListener
from uconsole_mcp import PROTOCOL


@unittest.skipUnless(os.name == 'posix', 'Unix local transport')
class LocalTransportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='uc-local-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'machine.json').write_text('{"coverage":"partial-cm4"}')
        self.owner = Controller({'gui': self.root}, grants=('boot', 'force-stop'),
                                history=self.root / 'history.sqlite3')
        self.addCleanup(self.owner.close)
        self.path = self.root / 'mcp.sock'

    def listener(self, **options):
        listener = LocalListener(self.path, lambda: ClientSession(self.owner, ['gui']), **options)
        self.addCleanup(listener.close)
        return listener

    def connect(self):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(3)
        connection.connect(str(self.path))
        self.addCleanup(connection.close)
        stream = connection.makefile('rwb', buffering=0)
        self.addCleanup(stream.close)
        def request(method, params):
            stream.write((json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}) + '\n').encode())
            return json.loads(stream.readline())
        request('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {},
                               'clientInfo': {'name': 'test', 'version': '1'}})
        stream.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        return connection, stream, request

    def test_separate_process_client_inspects_but_cannot_inherit_owner_grants(self):
        listener = self.listener()
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        code = '''import socket,json,sys
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.settimeout(3); s.connect(sys.argv[1])
f=s.makefile('rwb',buffering=0)
def call(method,params):
 f.write((json.dumps(dict(jsonrpc='2.0',id=1,method=method,params=params))+'\\n').encode())
 print(f.readline().decode().strip())
call('initialize',dict(protocolVersion=sys.argv[2],capabilities={},clientInfo=dict(name='external',version='1')))
f.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\\n')
call('tools/call',dict(name='workspace_inspect',arguments=dict(workspace='gui')))
call('tools/call',dict(name='boot',arguments=dict(workspace='gui')))
f.close(); s.close()
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.path), PROTOCOL],
                                capture_output=True, text=True, timeout=10, check=True)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        state = replies[1]['result']['structuredContent']
        self.assertEqual(state['grants'], [])
        self.assertTrue(state['attached'])
        self.assertTrue(replies[2]['result']['isError'])
        self.assertFalse(self.owner.jobs)
        listener.close()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.owner.inspect('gui')['grants'], ['boot', 'force-stop'])

    def test_disconnect_does_not_cancel_job_and_peer_cannot_cancel_it(self):
        self.listener()
        entered, release = threading.Event(), threading.Event()
        def query(*args):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('local job fixture')
            return {'power': {'ac_present': True}}
        with patch.object(self.owner, 'power_operation', side_effect=query):
            connection, stream, request = self.connect()
            reply = request('tools/call', {'name': 'power_query', 'arguments': {'workspace': 'gui'}})
            job_id = reply['result']['structuredContent']['job_id']
            try:
                self.assertTrue(entered.wait(2))
                stream.close()
                connection.close()
                _, _, peer = self.connect()
                observed = peer('tools/call', {'name': 'job_status', 'arguments': {'job_id': job_id}})
                self.assertEqual(observed['result']['structuredContent']['status'], 'running')
                denied = peer('tools/call', {'name': 'job_cancel', 'arguments': {'job_id': job_id}})
                self.assertTrue(denied['result']['isError'])
            finally:
                release.set()
            self.owner.jobs[job_id][2].result(timeout=5)
        self.assertEqual(self.owner.job(job_id)['status'], 'completed')

    def test_existing_endpoint_and_replacement_are_not_removed(self):
        self.path.write_text('user-owned file')
        with self.assertRaises(OSError):
            self.listener()
        self.assertEqual(self.path.read_text(), 'user-owned file')
        self.path.rename(self.root / 'retained-file')
        listener = self.listener()
        self.path.rename(self.root / 'retained-socket')
        self.path.write_text('replacement')
        listener.close()
        self.assertEqual(self.path.read_text(), 'replacement')

    def test_nonprivate_or_symlink_directory_is_rejected(self):
        public = self.root / 'public'
        public.mkdir(mode=0o755)
        with self.assertRaises(PermissionError):
            LocalListener(public / 'mcp.sock', lambda: None)
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(PermissionError):
            LocalListener(alias / 'mcp.sock', lambda: None)

    def test_idle_connection_releases_its_slot(self):
        closed = threading.Event()
        def factory():
            session = ClientSession(self.owner, ['gui'])
            close = session.close
            def done():
                close()
                closed.set()
            session.close = done
            return session
        listener = LocalListener(self.path, factory, max_clients=1, idle_timeout=0.1)
        self.addCleanup(listener.close)
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.connect(str(self.path))
        self.addCleanup(connection.close)
        self.assertTrue(closed.wait(3))

    def test_connection_limit_rejects_extra_client_without_new_session(self):
        entered = threading.Event()
        sessions = []
        def factory():
            session = ClientSession(self.owner, ['gui'])
            sessions.append(session)
            entered.set()
            return session
        listener = LocalListener(self.path, factory, max_clients=1)
        self.addCleanup(listener.close)
        first = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(first.close)
        first.connect(str(self.path))
        self.assertTrue(entered.wait(2))
        second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(second.close)
        second.settimeout(2)
        second.connect(str(self.path))
        self.assertEqual(second.recv(1), b'')
        self.assertEqual(len(sessions), 1)

    def test_listener_rejects_owner_as_session_without_closing_owner(self):
        listener = LocalListener(self.path, lambda: self.owner)
        self.addCleanup(listener.close)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(client.close)
        client.settimeout(2)
        client.connect(str(self.path))
        self.assertEqual(client.recv(1), b'')
        self.assertIn('restricted ClientSession', listener.errors[0])
        self.assertEqual(self.owner.history.recent(self.root), [])

    def test_stdio_adapter_relays_large_replies_and_does_not_create_controller(self):
        self.listener()
        (self.root / 'serial.log').write_text('guest data λ\n' * 10000)
        messages = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': PROTOCOL, 'capabilities': {},
                'clientInfo': {'name': 'stdio-client', 'version': '1'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'resources/read',
             'params': {'uri': 'forge://workspace/gui/serial'}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
             'params': {'name': 'boot', 'arguments': {'workspace': 'gui'}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'ping', 'params': {'padding': 'x' * 150000}},
        ]
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'tools/uconsole_mcp.py'),
                   '--connect', str(self.path)]
        result = subprocess.run(command, input=''.join(json.dumps(item) + '\n' for item in messages),
                                text=True, capture_output=True, timeout=10, check=True)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([item['id'] for item in replies], [1, 2, 3, 4])
        self.assertIn('guest data λ', replies[1]['result']['contents'][0]['text'])
        self.assertGreater(len(result.stdout), 65536)
        self.assertTrue(replies[2]['result']['isError'])
        self.assertEqual(replies[3]['result'], {})
        self.assertFalse(self.owner.jobs)
        self.assertEqual(result.stderr, '')

    def test_stdio_adapter_exits_on_owner_disconnect_with_stdin_still_open(self):
        listener = self.listener()
        process = subprocess.Popen([
            sys.executable, str(Path(__file__).resolve().parents[1] / 'tools/uconsole_mcp.py'),
            '--connect', str(self.path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            process.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
            process.stdin.flush()
            self.assertTrue(select.select([process.stdout], [], [], 5)[0])
            self.assertEqual(json.loads(process.stdout.readline())['result'], {})
            listener.close()
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_stdio_adapter_cannot_set_owner_configuration(self):
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'tools/uconsole_mcp.py'),
                   '--connect', str(self.path)]
        for flags in (['--allow', 'boot'], ['--files-root', str(self.root)],
                      ['--history', str(self.root / 'unapproved.sqlite3')],
                      ['--workspace', 'other=/tmp']):
            with self.subTest(flags=flags):
                result = subprocess.run(command + flags, input='', text=True,
                                        capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
        self.assertFalse((self.root / 'unapproved.sqlite3').exists())

    def test_stdio_adapter_refuses_symlink_socket(self):
        self.listener()
        alias = self.root / 'alias.sock'
        alias.symlink_to(self.path)
        result = subprocess.run([
            sys.executable, str(Path(__file__).resolve().parents[1] / 'tools/uconsole_mcp.py'),
            '--connect', str(alias)], input='', text=True, capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not a link', result.stderr)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
