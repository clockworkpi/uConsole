"""Scoped client sessions share one owner without inheriting its authority."""
from pathlib import Path
import tempfile
import threading
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_client import ClientSession
from forge_controller import Controller
from uconsole_mcp import Server, PROTOCOL


class ClientSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.paths = {name: self.root / name for name in ('one', 'two')}
        for path in self.paths.values():
            path.mkdir()
            (path / 'machine.json').write_text('{"coverage":"partial-cm4"}')
        self.owner = Controller(self.paths, grants=('boot', 'force-stop', 'transfer', 'guest-exec',
                                                    'image-write', 'device-control'),
                                files_root=self.root / 'files', history=self.root / 'jobs.sqlite3')
        self.addCleanup(self.owner.close)

    def test_owner_authority_is_not_inherited_and_cannot_be_expanded(self):
        client = ClientSession(self.owner, ['one'])
        self.assertEqual(client.inspect('one')['grants'], [])
        self.assertTrue(client.inspect('one')['attached'])
        with self.assertRaises(PermissionError):
            client.host_file('not-inherited')
        with self.assertRaises(ValueError):
            client.inspect('two')
        with self.assertRaises(PermissionError):
            ClientSession(self.owner, ['one'], ['host-task'])
        with self.assertRaises(PermissionError):
            ClientSession(self.owner, ['one'], files_root=self.root)
        for tool, extra in (('boot', {}), ('stop', {'force': True}), ('pause', {}),
                            ('guest_exec', {'script': 'true'}), ('configure_display', {}),
                            ('power_set', {'ac_present': False})):
            with self.subTest(tool=tool), self.assertRaises(PermissionError):
                client.call(tool, {'workspace': 'one', **extra})
        self.assertEqual(self.owner.jobs, {})

    def test_force_stop_requires_separate_client_grant(self):
        client = ClientSession(self.owner, ['one'], ['boot'])
        with patch.object(self.owner, 'call') as call:
            with self.assertRaisesRegex(PermissionError, 'force-stop'):
                client.call('stop', {'workspace': 'one', 'force': True})
            call.assert_not_called()

    def test_modem_permission_is_scoped_to_explicit_grant_and_workspace(self):
        denied = ClientSession(self.owner, ['one'])
        allowed = ClientSession(self.owner, ['one'], ['device-control'])
        with patch.object(self.owner, 'call', return_value={'accepted':True}) as call:
            with self.assertRaises(PermissionError):
                denied.call('modem_set', {'workspace':'one', 'registration':3})
            with self.assertRaises(ValueError):
                allowed.call('modem_set', {'workspace':'two', 'registration':3})
            call.assert_not_called()
            allowed.call('modem_set', {'workspace':'one', 'registration':3})
            call.assert_called_once_with('modem_set', {'workspace':'one', 'registration':3})
        with patch.object(self.owner, 'call', return_value={'accepted':True}) as call:
            denied.call('modem_query', {'workspace':'one'})
            call.assert_called_once_with('modem_query', {'workspace':'one'})

    def test_usb_modem_connection_requires_mutation_grant(self):
        client = ClientSession(self.owner, ['one'])
        allowed = ClientSession(self.owner, ['one'], ['device-control'])
        with patch.object(self.owner, 'call', return_value={'accepted':True}) as call:
            with self.assertRaises(PermissionError):
                client.call('modem_connection', {'workspace':'one', 'connected':False})
            with self.assertRaises(ValueError):
                allowed.call('modem_connection', {'workspace':'one', 'connected':0})
            call.assert_not_called()
            allowed.call('modem_connection', {'workspace':'one', 'connected':False})
            call.assert_called_once_with('modem_connection', {'workspace':'one', 'connected':False})

    def test_long_attachment_keeps_bounded_authority_and_historical_reads(self):
        client = ClientSession(self.owner, ['one'])
        peer = ClientSession(self.owner, ['one'])
        with patch.object(self.owner, 'power_operation', return_value={'ac_present': True}):
            ids = []
            for _ in range(140):
                job_id = client.call('power_query', {'workspace': 'one'})['job_id']
                self.owner.jobs[job_id][2].result(timeout=5)
                ids.append(job_id)
        self.assertEqual(len(client.submitted), 128)
        self.assertNotIn(ids[0], client.submitted)
        self.assertTrue(client.job(ids[0])['historical'])
        self.assertEqual(peer.job(ids[0])['result'], {'ac_present': True})
        with self.assertRaisesRegex(PermissionError, 'only their own'):
            client.call('job_cancel', {'job_id': ids[0]})
        with self.assertRaisesRegex(PermissionError, 'only their own'):
            peer.call('job_cancel', {'job_id': ids[-1]})

    def test_client_file_root_is_narrower_than_owner_and_frozen_before_dispatch(self):
        files = self.root / 'files' / 'client'
        files.mkdir(parents=True)
        (files / 'escape').symlink_to(self.root, target_is_directory=True)
        client = ClientSession(self.owner, ['one'], ['transfer'], files_root=files)
        with patch.object(self.owner, 'call', return_value={'job_id': 'fixture'}) as call:
            client.call('upload', {'workspace': 'one', 'host_path': 'source', 'guest_path': '/tmp/test'})
            self.assertEqual(call.call_args.args[1]['host_path'], str(files / 'source'))
            for bad in ('../peer', 'escape/secret', str(self.root / 'outside')):
                with self.subTest(path=bad), self.assertRaises(PermissionError):
                    client.call('upload', {'workspace': 'one', 'host_path': bad, 'guest_path': '/tmp/test'})
            self.assertEqual(call.call_count, 1)

    def test_live_and_historical_results_are_workspace_scoped(self):
        first = self.owner.submit('one', 'fixture', lambda: {'private': 'one'})
        second = self.owner.submit('two', 'fixture', lambda: {'private': 'two'})
        for job in (first, second):
            self.owner.jobs[job['job_id']][2].result(timeout=5)
        client = ClientSession(self.owner, ['one'])
        self.assertEqual(client.job(first['job_id'])['result'], {'private': 'one'})
        with self.assertRaises(ValueError):
            client.job(second['job_id'])
        with self.assertRaises(PermissionError):
            client.call('job_cancel', {'job_id': first['job_id']})
        self.owner.jobs.pop(first['job_id'])
        self.owner.jobs.pop(second['job_id'])
        self.assertTrue(client.job(first['job_id'])['historical'])
        with self.assertRaises(ValueError):
            client.job(second['job_id'])

    def test_shared_workspace_exclusion_and_disconnect_preserve_accepted_job(self):
        entered, release = threading.Event(), threading.Event()
        first, second = ClientSession(self.owner, ['one']), ClientSession(self.owner, ['one'])

        def query(*args):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('client fixture')
            return {'power': {'ac_present': True}}

        with patch.object(self.owner, 'power_operation', side_effect=query):
            submitted = first.call('power_query', {'workspace': 'one'})
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaisesRegex(ValueError, 'Another controller job'):
                    second.call('power_query', {'workspace': 'one'})
                self.assertEqual(second.job(submitted['job_id'])['status'], 'running')
                own_cancel = first.call('job_cancel', {'job_id': submitted['job_id']})
                self.assertFalse(own_cancel['requested'])  # Running power reads are not cancellable.
                with self.assertRaises(PermissionError):
                    second.call('job_cancel', {'job_id': submitted['job_id']})
                first.close()
                with self.assertRaisesRegex(ValueError, 'disconnected'):
                    first.job(submitted['job_id'])
                self.assertEqual(second.job(submitted['job_id'])['status'], 'running')
            finally:
                release.set()
            self.owner.jobs[submitted['job_id']][2].result(timeout=5)
        self.assertEqual(second.job(submitted['job_id'])['status'], 'completed')
        self.assertEqual(self.owner.busy, {})

    def test_mcp_resources_and_tools_cannot_bypass_session_scope(self):
        server = Server(ClientSession(self.owner, ['one']))
        def request(method, params):
            return server.handle({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
        request('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {},
                               'clientInfo': {'name': 'fixture', 'version': '1'}})
        server.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        resources = request('resources/list', {})['result']['resources']
        self.assertEqual({item['uri'] for item in resources}, {
            'forge://workspace/one/' + kind
            for kind in ('state', 'serial', 'jobs', 'tasks', 'targets')})
        self.assertEqual(len(resources), 5)
        self.assertTrue(all('/one/' in item['uri'] for item in resources))
        for kind in ('state', 'serial', 'jobs', 'tasks', 'targets'):
            denied = request('resources/read', {'uri': 'forge://workspace/two/' + kind})
            self.assertIn('error', denied)
        denied = request('tools/call', {'name': 'boot', 'arguments': {'workspace': 'one'}})
        self.assertTrue(denied['result']['isError'])
        self.assertEqual(self.owner.jobs, {})


if __name__ == '__main__':
    unittest.main()
