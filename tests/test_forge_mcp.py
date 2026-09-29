"""MCP wire protocol, permission boundaries and asynchronous job semantics."""
import io
from concurrent.futures import Future
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_controller import Controller, JobCancelled
from forge_workspace import WorkspaceLock
from uconsole_emulator import wait_for_log
from uconsole_mcp import Server, PROTOCOL
from validate_forge_mcp import cancel_boot, ac_irq_counts


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve()
        (self.path / 'machine.json').write_text('{"coverage":"partial-cm4"}')
        self.controller = Controller({'test': self.path}, files_root=self.path / 'files')
        self.addCleanup(self.controller.close)
        self.server = Server(self.controller)

    def request(self, method, params=None):
        return self.server.handle({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})

    def initialize(self):
        result = self.request('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {},
                                             'clientInfo': {'name': 'test', 'version': '1'}})
        self.assertEqual(result['result']['protocolVersion'], PROTOCOL)
        self.assertIsNone(self.server.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))

    def test_lifecycle_tools_resources_and_no_notification_mutations(self):
        self.assertIn('error', self.request('tools/list'))
        self.initialize()
        tools = self.request('tools/list')['result']['tools']
        self.assertIn('guest_exec', [tool['name'] for tool in tools])
        resources = self.request('resources/list')['result']['resources']
        state = self.request('resources/read', {'uri': resources[0]['uri']})
        self.assertIn('partial-cm4', state['result']['contents'][0]['text'])
        with patch.object(self.controller, 'call') as call:
            self.assertIsNone(self.server.handle({'jsonrpc': '2.0', 'method': 'tools/call',
                                                  'params': {'name': 'boot', 'arguments': {'workspace': 'test'}}}))
            call.assert_not_called()

    def test_mutations_default_denied_and_cannot_self_grant(self):
        self.initialize()
        for name, extra in (('boot', {}), ('pause', {}), ('resume', {}), ('checkpoint', {'name': 'safe'}), ('configure_display', {}),
                            ('guest_exec', {'script': 'true'})):
            result = self.request('tools/call', {'name': name, 'arguments': {'workspace': 'test', **extra}})
            self.assertTrue(result['result']['isError'])
        result = self.request('tools/call', {'name': 'boot', 'arguments': {'workspace': 'test', 'allow': ['boot']}})
        self.assertEqual(result['error']['code'], -32602)
        self.assertEqual(self.controller.jobs, {})

    def test_serial_resource_rejects_links_and_special_files(self):
        self.initialize()
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        private = Path(outside.name)/'private.txt'
        private.write_text('private-canary')
        log = self.path/'serial.log'
        for create in (lambda: log.symlink_to(private),
                       lambda: os.link(private, log), lambda: os.mkfifo(log),
                       lambda: log.mkdir()):
            create()
            with patch('sys.stderr', new=io.StringIO()):
                response = self.request('resources/read', {'uri': 'forge://workspace/test/serial'})
            self.assertIn('error', response)
            self.assertNotIn('private-canary', json.dumps(response))
            log.rmdir() if log.is_dir() and not log.is_symlink() else log.unlink()

    def test_serial_resource_is_bounded_and_missing_is_empty(self):
        self.initialize()
        def read():
            return self.request('resources/read', {'uri': 'forge://workspace/test/serial'})['result']['contents'][0]['text']
        self.assertEqual(read(), '')
        (self.path/'serial.log').write_bytes(b'x'*70000 + b'end')
        value = read()
        self.assertEqual(len(value), 65536)
        self.assertTrue(value.endswith('end'))

    def test_metadata_resources_use_scoped_readonly_controller_calls(self):
        self.initialize()
        for resource, tool in [('jobs', 'job_history'), ('tasks', 'host_tasks'),
                               ('targets', 'target_transactions')]:
            expected = {'workspace': 'test', **({'limit': 20} if resource == 'jobs' else {})}
            with patch.object(self.controller, 'call', return_value={'evidence': 'read-only'}) as call:
                result = self.request('resources/read', {'uri': 'forge://workspace/test/' + resource})
                content = result['result']['contents'][0]
                self.assertEqual(content['mimeType'], 'application/json')
                self.assertEqual(json.loads(content['text']), {'evidence': 'read-only'})
                call.assert_called_once_with(tool, expected)
        self.assertEqual(self.controller.jobs, {})

    def test_attached_resource_listing_cannot_escape_workspace_or_grants(self):
        from forge_client import ClientSession
        owner = Controller({'one': self.path/'one', 'two': self.path/'two'})
        self.addCleanup(owner.close)
        client = ClientSession(owner, ['one'])
        server = Server(client)
        server.ready = True
        uris = [r['uri'] for r in server.resources()]
        self.assertTrue(all('/one/' in uri for uri in uris))
        for kind in ('jobs', 'tasks', 'targets'):
            response = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'resources/read',
                                      'params': {'uri': 'forge://workspace/two/' + kind}})
            self.assertEqual(response['error']['code'], -32602)
        with patch.object(owner, 'call', return_value={'tasks': [], 'execution_granted': True}):
            content = server.dispatch('resources/read', {'uri': 'forge://workspace/one/tasks'})['contents'][0]
        self.assertFalse(json.loads(content['text'])['execution_granted'])

    def test_keyboard_wire_validation_and_default_denial(self):
        self.initialize()
        for commands in ([['run', True]], [['sync', 'token']], [['run', 33]], []):
            result = self.request('tools/call', {'name': 'keyboard_input',
                                  'arguments': {'workspace': 'test', 'commands': commands}})
            self.assertEqual(result['error']['code'], -32602)
        result = self.request('tools/call', {'name': 'keyboard_input',
                              'arguments': {'workspace': 'test', 'commands': [['state']]}})
        self.assertTrue(result['result']['isError'])
        self.assertEqual(self.controller.jobs, {})
        with patch.object(self.controller, 'submit_keyboard', return_value={'job_id': 'fixture'}) as submit:
            result = self.request('tools/call', {'name': 'keyboard_input',
                                  'arguments': {'workspace': 'test',
                                                'commands': [['matrix', 4, 2, 1], ['run', 10]]}})
            self.assertFalse(result['result'].get('isError', False))
            submit.assert_called_once_with('test', [['matrix', 4, 2, 1], ['run', 10]], timeout=5)

    def test_display_preparation_uses_shared_cancellable_image_job(self):
        self.controller.grants = frozenset({'image-write'})
        with patch.object(self.controller, 'lifecycle', return_value={'exit_code': 0}) as action:
            job = self.controller.call('configure_display', {'workspace': 'test'})
            self.controller.jobs[job['job_id']][2].result(timeout=5)
        action.assert_called_once_with('test', 'configure-display', ())
        self.assertTrue(self.controller.job_controls[job['job_id']][1])
        self.assertEqual(self.controller.job(job['job_id'])['operation'], 'configure_display')

    def test_pause_resume_owned_state_readback_and_no_running_cancellation(self):
        self.controller.grants = frozenset({'boot'})
        runtime = MagicMock()
        self.controller.runtimes['test'] = runtime
        for tool, command, expected in (('pause', 'stop', False), ('resume', 'cont', True)):
            with self.subTest(tool=tool):
                before = {'status': 'paused' if expected else 'running', 'running': not expected}
                after = {'status': 'running' if expected else 'paused', 'running': expected}
                runtime.control.reset_mock()
                runtime.control.side_effect = [before, {}, after]
                job = self.controller.call(tool, {'workspace': 'test'})
                result = self.controller.jobs[job['job_id']][2].result(timeout=5)
                self.assertEqual(result, {'before': before, 'observed': after})
                self.assertEqual([call.args[0] for call in runtime.control.call_args_list],
                                 ['query-status', command, 'query-status'])
                self.assertFalse(self.controller.job_controls[job['job_id']][1])

    def test_pause_readback_mismatch_fails_without_rollback(self):
        self.controller.grants = frozenset({'boot'})
        runtime = MagicMock()
        self.controller.runtimes['test'] = runtime
        running = {'status': 'running', 'running': True}
        runtime.control.side_effect = [running, {}, running]
        job = self.controller.call('pause', {'workspace': 'test'})
        with self.assertRaisesRegex(ValueError, 'not rolled back'):
            self.controller.jobs[job['job_id']][2].result(timeout=5)
        self.assertEqual(self.controller.job(job['job_id'])['status'], 'failed')
        self.assertEqual(len(runtime.control.call_args_list), 3)

    def test_workspace_and_host_path_boundaries(self):
        self.initialize()
        self.assertTrue(self.request('tools/call', {'name': 'workspace_inspect',
                       'arguments': {'workspace': 'missing'}})['result']['isError'])
        with self.assertRaises(PermissionError):
            self.controller.host_file('../outside')
        root = self.path / 'files'
        root.mkdir()
        (root / 'escape').symlink_to(self.path, target_is_directory=True)
        with self.assertRaises(PermissionError):
            self.controller.host_file('escape/machine.json')
        with self.assertRaises(PermissionError):
            self.controller.host_file(str(self.path / 'machine.json'))

    def test_types_and_limits_enforced_before_execution(self):
        self.initialize()
        for timeout in (True, 0, 301, '10'):
            result = self.request('tools/call', {'name': 'guest_exec', 'arguments': {
                'workspace': 'test', 'script': 'true', 'timeout': timeout}})
            self.assertEqual(result['error']['code'], -32602)

    def test_async_job_exclusion_and_terminal_result(self):
        entered, release = threading.Event(), threading.Event()

        def work():
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError('fixture')
            return {'answer': 42}

        job = self.controller.submit('test', 'fixture', work)
        try:
            self.assertTrue(entered.wait(timeout=5))
            self.assertEqual(self.controller.job(job['job_id'])['status'], 'running')
            with self.assertRaisesRegex(ValueError, 'owns this workspace'):
                self.controller.submit('test', 'second', lambda: None)
            self.assertFalse(self.controller.cancel(job['job_id'])['cancelled'])
        finally:
            release.set()
        self.controller.jobs[job['job_id']][2].result(timeout=5)
        result = self.controller.job(job['job_id'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result'], {'answer': 42})

    def test_native_cocoa_display_reaches_gui_owned_boot_job(self):
        self.controller.grants = frozenset({'boot', 'force-stop'})
        with patch.object(self.controller, 'submit', return_value={'job_id': 'fixture'}) as submit, \
                patch.object(self.controller, 'boot') as boot:
            self.controller.submit_boot('test', mode='desktop', display='cocoa')
            self.assertEqual(submit.call_args.kwargs['context']['display'], 'cocoa')
            submit.call_args.args[2]()
            self.assertEqual(boot.call_args.kwargs['display'], 'cocoa')
        with patch.object(self.controller, 'submit') as submit, self.assertRaises(ValueError):
            self.controller.submit_boot('test', display='unexpected')
        submit.assert_not_called()

    def test_adc_reference_wire_validation_dispatch_and_history_context(self):
        self.initialize()
        self.controller.grants = frozenset({'boot', 'force-stop'})
        with patch.object(self.controller, 'submit', return_value={'job_id': 'fixture'}) as submit, \
                patch.object(self.controller, 'boot') as boot:
            result = self.request('tools/call', {'name': 'boot', 'arguments': {
                'workspace': 'test', 'adc_reference': 'missing'}})
            self.assertFalse(result['result']['isError'])
            self.assertEqual(submit.call_args.kwargs['context']['adc_reference'], 'missing')
            submit.call_args.args[2]()
            self.assertEqual(boot.call_args.kwargs['adc_reference'], 'missing')
        for invalid in ('typo', False, 1):
            with self.subTest(invalid=invalid), patch.object(self.controller, 'submit') as submit:
                result = self.request('tools/call', {'name': 'boot', 'arguments': {
                    'workspace': 'test', 'adc_reference': invalid}})
                self.assertEqual(result['error']['code'], -32602)
                with self.assertRaises(ValueError):
                    self.controller.submit_boot('test', adc_reference=invalid)
                submit.assert_not_called()

    def test_boot_scenario_is_bounded_and_snapshotted_before_submission(self):
        self.controller.grants = frozenset({'boot', 'force-stop'})
        root = self.path / 'files'
        root.mkdir()
        profile = root / 'power.json'
        profile.write_text('{"schema":1,"power":{"ac_present":false}}')
        with patch.object(self.controller, 'submit') as submit, \
             patch.object(self.controller, 'boot') as boot:
            self.controller.call('boot', {'workspace': 'test', 'scenario_path': 'power.json'})
            context = submit.call_args.kwargs['context']
            self.assertFalse(context['power']['ac_present'])
            profile.write_text('not json anymore')
            submit.call_args.args[2]()
            snapshot = boot.call_args.args[2]
            self.assertFalse(snapshot.power.ac_present)
            self.assertEqual(context['scenario_sha256'], snapshot.sha256)
        with patch.object(self.controller, 'submit') as submit:
            with self.assertRaises(PermissionError):
                self.controller.call('boot', {'workspace': 'test', 'scenario_path': '../machine.json'})
            with self.assertRaises(ValueError):
                self.controller.call('boot', {'workspace': 'test', 'scenario_path': 'power.json'})
            submit.assert_not_called()

    def test_boot_scenario_wire_schema_and_permission(self):
        self.initialize()
        tools = self.request('tools/list')['result']['tools']
        boot = next(item for item in tools if item['name'] == 'boot')
        self.assertIn('scenario_path', boot['inputSchema']['properties'])
        result = self.request('tools/call', {'name': 'boot', 'arguments': {
            'workspace': 'test', 'scenario_path': 42}})
        self.assertEqual(result['error']['code'], -32602)
        with patch('forge_scenario.Scenario.load') as load:
            result = self.request('tools/call', {'name': 'boot', 'arguments': {
                'workspace': 'test', 'scenario_path': 'power.json'}})
            self.assertTrue(result['result']['isError'])
            load.assert_not_called()

    def test_runtime_power_permission_and_exactly_one_field(self):
        with patch.object(self.controller, 'submit') as submit:
            with self.assertRaises(PermissionError):
                self.controller.call('power_set', {'workspace': 'test', 'ac_present': False})
            self.controller.grants = frozenset({'device-control'})
            for extra in ({}, {'ac_present': False, 'battery_present': True}, {'battery_capacity': True}):
                with self.assertRaises(ValueError):
                    self.controller.call('power_set', {'workspace': 'test', **extra})
            submit.assert_not_called()
            self.controller.call('power_set', {'workspace': 'test', 'ac_present': False})
            self.assertEqual(submit.call_args.kwargs['context']['requested'], {'ac_present': False})
            self.assertEqual(Path(submit.call_args.kwargs['context']['evidence_path']).parent, self.path)
            self.assertFalse(submit.call_args.kwargs['cancellable'])
        self.initialize()
        result = self.request('tools/call', {'name': 'power_set', 'arguments': {
            'workspace': 'test', 'battery_current_ma': 4096}})
        self.assertEqual(result['error']['code'], -32602)

    def test_modem_changes_require_grant_and_validate_before_submission(self):
        with patch.object(self.controller, 'submit') as submit:
            with self.assertRaises(PermissionError):
                self.controller.call('modem_set', {'workspace':'test', 'registration':3})
            self.controller.grants = frozenset({'device-control'})
            for changes in ({}, {'radio':True}, {'registration':6}, {'pin':'1234'}, {'rssi':32}):
                with self.assertRaises(ValueError):
                    self.controller.call('modem_set', {'workspace':'test', **changes})
            submit.assert_not_called()
        self.initialize()
        result = self.request('tools/call', {'name':'modem_set',
                              'arguments':{'workspace':'test', 'radio':True}})
        self.assertEqual(result['error']['code'], -32602)

    def test_modem_query_without_mutation_grant_never_calls_change(self):
        runtime = MagicMock(identity='read-only-fixture')
        runtime.modem.query.return_value = {'sequence':1, 'status':'observed',
                                            'observed':{'registration':1}}
        with patch.object(self.controller, 'runtime', return_value=runtime):
            pending = self.controller.call('modem_query', {'workspace':'test'})
            result = self.controller.jobs[pending['job_id']][2].result(timeout=5)
        self.assertEqual(result['status'], 'observed')
        runtime.modem.query.assert_called_once_with()
        runtime.modem.change.assert_not_called()

    def test_modem_dispatch_is_recorded_before_owned_runtime_mutation(self):
        self.controller.grants = frozenset({'device-control'})
        runtime = MagicMock(identity='owned-fixture')
        def change(changes):
            files = list(self.path.glob('modem-event-*.jsonl'))
            self.assertEqual(len(files), 1)
            records = [json.loads(line) for line in files[0].read_text().splitlines()]
            self.assertEqual(records, [{'state':'dispatch', 'runtime_identity':'owned-fixture',
                                        'requested':{'registration':3}}])
            return {'sequence':1, 'status':'applied', 'observed':{'registration':3, 'link_up':False}}
        runtime.modem.change.side_effect = change
        with patch.object(self.controller, 'runtime', return_value=runtime):
            pending = self.controller.call('modem_set', {'workspace':'test', 'registration':3})
            self.controller.jobs[pending['job_id']][2].result(timeout=5)
        records = [json.loads(line) for line in next(self.path.glob('modem-event-*.jsonl')).read_text().splitlines()]
        self.assertEqual(records[-1]['state'], 'acknowledged')
        runtime.modem.change.assert_called_once_with({'registration':3})

    def test_modem_uncertainty_records_failure_without_retry(self):
        self.controller.grants = frozenset({'device-control'})
        runtime = MagicMock(identity='owned-fixture')
        runtime.modem.change.side_effect = TimeoutError('lost acknowledgement')
        with patch.object(self.controller, 'runtime', return_value=runtime):
            pending = self.controller.call('modem_set', {'workspace':'test', 'radio':4})
            with self.assertRaises(TimeoutError):
                self.controller.jobs[pending['job_id']][2].result(timeout=5)
        runtime.modem.change.assert_called_once()
        records = [json.loads(line) for line in next(self.path.glob('modem-event-*.jsonl')).read_text().splitlines()]
        self.assertEqual(records[-1]['state'], 'failed')
        self.assertFalse(records[-1]['rollback'])

    def test_replay_irq_acceptance_counts_only_both_ac_driver_irqs(self):
        rows = ('36: 100 200 0 0 pinctrl-bcm2835 2 Level axp22x_irq_chip\n'
                '39: 1 2 0 0 axp22x_irq_chip 2 Edge axp20x-ac-power-supply\n'
                '40: 3 4 0 0 axp22x_irq_chip 3 Edge axp20x-ac-power-supply\n'
                '59: 99 0 0 0 axp22x_irq_chip 22 Edge axp20x-pek-dbf\n')
        self.assertEqual(ac_irq_counts(rows), {'39': 3, '40': 7})
        with self.assertRaises(ValueError):
            ac_irq_counts(rows.splitlines()[0])

    def test_replay_cancel_retains_partial_log_and_releases_workspace(self):
        from forge_replay import ReplayCancelled
        root = self.path / 'files'
        root.mkdir()
        schedule = root / 'events.json'
        schedule.write_text(json.dumps({'schema': 1, 'events': [
            {'at_ms': 0, 'power': {'ac_present': False}},
            {'at_ms': 300000, 'power': {'ac_present': True}}]}))
        with self.assertRaises(PermissionError):
            self.controller.call('power_replay', {'workspace': 'test', 'schedule_path': 'events.json'})
        self.controller.grants = frozenset({'device-control'})
        first = threading.Event()
        runtime = MagicMock(identity='fixture')
        runtime.control.return_value = {'running': True}
        def change(*args, **kwargs):
            first.set()
            return {'observed': {'ac_present': False}}
        with patch.object(self.controller, 'runtime', return_value=runtime), \
             patch('forge_replay.change_power', side_effect=change) as mutate:
            pending = self.controller.call('power_replay', {'workspace': 'test', 'schedule_path': 'events.json'})
            self.assertTrue(first.wait(5))
            self.assertTrue(self.controller.cancel(pending['job_id'])['requested'])
            with self.assertRaises(ReplayCancelled):
                self.controller.jobs[pending['job_id']][2].result(timeout=5)
            mutate.assert_called_once()
        result = self.controller.job(pending['job_id'])
        self.assertEqual(result['status'], 'cancelled')
        events = [json.loads(line) for line in Path(result['context']['evidence_path']).read_text().splitlines()]
        self.assertEqual(events[-1]['completed_events'], 1)
        self.assertEqual(events[-1]['status'], 'cancelled')
        self.assertNotIn(self.path.resolve(), self.controller.busy)

    def test_workspace_aliases_share_job_exclusion(self):
        alias = self.path / 'alias'
        alias.symlink_to(self.path, target_is_directory=True)
        controller = Controller({'first': self.path, 'second': alias})
        release = threading.Event()
        try:
            first = controller.submit('first', 'fixture', lambda: release.wait(5))
            with self.assertRaisesRegex(ValueError, 'owns this workspace'):
                controller.submit('second', 'fixture', lambda: None)
            release.set()
            controller.jobs[first['job_id']][2].result(timeout=5)
            second = controller.submit('second', 'fixture', lambda: 42)
            self.assertEqual(controller.jobs[second['job_id']][2].result(timeout=5), 42)
        finally:
            release.set()
            controller.close()

    @unittest.skipUnless(os.name == 'posix', 'POSIX independent-process locking')
    def test_competing_process_blocks_boot_without_touching_workspace(self):
        tools = str(Path(__file__).resolve().parents[1] / 'tools')
        code = ('import sys; sys.path.insert(0, sys.argv[1]); '
                'from forge_workspace import WorkspaceLock; '
                'lock = WorkspaceLock(sys.argv[2]); '
                'print("locked", flush=True); sys.stdin.readline(); lock.close()')
        process = subprocess.Popen([sys.executable, '-c', code, tools, str(self.path)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 5)[0])
            self.assertEqual(process.stdout.readline().strip(), 'locked')
            self.assertIsNone(process.poll())
            serial = self.path / 'serial.log'
            serial.write_text('existing owner evidence\n')
            self.controller.grants = frozenset({'boot', 'force-stop'})
            with patch('forge_runtime.refresh_boot') as refresh, \
                 patch('forge_runtime.subprocess.Popen') as spawn:
                pending = self.controller.call('boot', {'workspace': 'test'})
                with self.assertRaisesRegex(ValueError, 'busy'):
                    self.controller.jobs[pending['job_id']][2].result(timeout=5)
                refresh.assert_not_called()
                spawn.assert_not_called()
            self.assertEqual(serial.read_text(), 'existing owner evidence\n')
            self.assertEqual(self.controller.runtimes, {})
            self.assertIsNone(process.poll())
        finally:
            process.communicate('\n', timeout=5)
        self.assertEqual(process.returncode, 0)
        with WorkspaceLock(self.path):
            pass

    def test_terminal_job_releases_workspace_before_done_callbacks(self):
        callbacks = []
        # Deterministically delay done callbacks beyond Future.result(), as
        # another client can observe a terminal future before callbacks run.
        with patch.object(Future, 'add_done_callback',
                          lambda future, callback: callbacks.append((future, callback))):
            first = self.controller.submit('test', 'first', lambda: 1)
            self.assertEqual(self.controller.jobs[first['job_id']][2].result(timeout=5), 1)
            self.assertEqual(self.controller.job(first['job_id'])['status'], 'completed')
            second = self.controller.submit('test', 'second', lambda: 2)
            self.assertEqual(self.controller.jobs[second['job_id']][2].result(timeout=5), 2)
        for future, callback in callbacks:
            callback(future)

    def test_stale_completion_cannot_release_a_new_workspace_owner(self):
        first = self.controller.submit('test', 'first', lambda: None)
        self.controller.jobs[first['job_id']][2].result(timeout=5)
        entered, release = threading.Event(), threading.Event()

        def work():
            entered.set()
            if not release.wait(5):
                raise TimeoutError('fixture')

        second = self.controller.submit('test', 'second', work)
        try:
            self.assertTrue(entered.wait(5))
            self.controller.finished('test', first['job_id'])
            with self.assertRaisesRegex(ValueError, 'owns this workspace'):
                self.controller.submit('test', 'third', lambda: None)
        finally:
            release.set()
        self.controller.jobs[second['job_id']][2].result(timeout=5)

    def test_queued_cancellation_releases_ownership_without_running_work(self):
        future = Future()
        work = MagicMock()
        with patch.object(self.controller.executor, 'submit', return_value=future):
            job = self.controller.submit('test', 'queued', work)
        self.assertEqual(self.controller.job(job['job_id'])['status'], 'queued')
        self.assertTrue(self.controller.cancel(job['job_id'])['cancelled'])
        work.assert_not_called()
        replacement = self.controller.submit('test', 'replacement', lambda: 42)
        self.assertEqual(self.controller.jobs[replacement['job_id']][2].result(timeout=5), 42)

    def test_wire_stream_recovers_after_invalid_json(self):
        source = io.BytesIO(b'broken\n' + json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'ping'}).encode() + b'\n')
        output = io.StringIO()
        self.server.serve(source, output)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(replies[0]['error']['code'], -32700)
        self.assertEqual(replies[1]['result'], {})

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group lifecycle')
    def test_running_image_job_cancellation_stops_owned_process(self):
        self.controller.grants = frozenset({'image-write'})
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        self.addCleanup(os.close, writer)
        real_popen = subprocess.Popen
        children = []

        def launch(command, **kwargs):
            if not any(str(item).endswith('/uconsole_emulator.py') for item in command):
                # Keep the real Darwin zombie census intact; only replace the
                # image worker, not every child launched during cancellation.
                return real_popen(command, **kwargs)
            kwargs['pass_fds'] = (*kwargs.get('pass_fds', ()), writer)
            child = real_popen([sys.executable, '-c',
                f'import os, signal; os.write({writer}, b"ready"); signal.pause()'],
                **kwargs)
            children.append(child)
            return child

        with patch('forge_controller.subprocess.Popen', side_effect=launch):
            job = self.controller.call('checkpoint', {'workspace': 'test', 'name': 'cancelled'})
            try:
                self.assertTrue(select.select([reader], [], [], 5)[0])
                self.assertEqual(os.read(reader, 5), b'ready')
                result = self.controller.cancel(job['job_id'])
                self.assertTrue(result['requested'])
                self.assertFalse(result['cancelled'])
                with self.assertRaises(JobCancelled):
                    self.controller.jobs[job['job_id']][2].result(timeout=15)
                terminal = self.controller.job(job['job_id'])
                self.assertEqual(terminal['status'], 'cancelled')
                self.assertIn('No rollback', terminal['detail'])
                self.assertIsNotNone(children[0].poll())
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                        child.wait()

    def test_image_output_is_returned_as_bounded_tail(self):
        self.controller.grants = frozenset({'image-write'})
        real_popen = subprocess.Popen

        def launch(command, **kwargs):
            return real_popen([sys.executable, '-c', 'print("x" * 1000000)'], **kwargs)

        with patch('forge_controller.subprocess.Popen', side_effect=launch):
            result = self.controller.lifecycle('test', 'checkpoint', ['bounded'])
        self.assertEqual(len(result['stdout']), 16384)
        self.assertTrue(result['stdout'].endswith('x\n'))

    def test_shared_image_submission_preserves_permissions_and_argument_snapshot(self):
        with self.assertRaises(PermissionError):
            self.controller.submit_lifecycle('test', 'checkpoint', ['fixture'])
        self.controller.grants = frozenset({'image-write'})
        with self.assertRaises(ValueError):
            self.controller.submit_lifecycle('test', 'run', [])
        with self.assertRaises(ValueError):
            self.controller.submit_lifecycle('test', 'checkpoint', [None])
        arguments = ['original']
        with patch.object(self.controller, 'submit') as submit:
            self.controller.submit_lifecycle('test', 'checkpoint', arguments)
            arguments[0] = 'later change'
            action = submit.call_args.args[2]
            with patch.object(self.controller, 'lifecycle') as execute:
                action()
                execute.assert_called_once_with('test', 'checkpoint', ('original',))
            self.assertEqual(submit.call_args.kwargs['context'], {'arguments': ['original']})
            self.assertTrue(submit.call_args.kwargs['cancellable'])
        with patch.object(self.controller, 'submit_lifecycle') as submit:
            self.controller.call('checkpoint', {'workspace': 'test', 'name': 'mcp-fixture'})
            submit.assert_called_once_with('test', 'checkpoint', ['mcp-fixture'])

    def test_shared_guest_submission_enforces_limits_and_omits_script_from_context(self):
        with self.assertRaises(PermissionError):
            self.controller.submit_guest('test', 'true')
        self.controller.grants = frozenset({'guest-exec'})
        for script, timeout in [('', 60), ('x' * 2201, 60), ('true', True), ('true', 0), ('true', 301)]:
            with self.assertRaises(ValueError):
                self.controller.submit_guest('test', script, timeout)
        with patch.object(self.controller, 'submit') as submit:
            self.controller.submit_guest('test', 'printf secret', 9)
            self.assertEqual(submit.call_args.args[:2], ('test', 'guest_exec'))
            context = submit.call_args.kwargs['context']
            self.assertEqual(context['timeout'], 9)
            self.assertEqual(len(context['script_sha256']), 64)
            self.assertNotIn('secret', json.dumps(context))
            self.assertTrue(submit.call_args.kwargs['cancellable'])
        with patch.object(self.controller, 'submit_guest') as submit:
            self.controller.call('guest_exec', {'workspace': 'test', 'script': 'true', 'timeout': 7})
            submit.assert_called_once_with('test', 'true', 7)

    def test_cancelled_image_job_does_not_launch_command(self):
        self.controller.grants = frozenset({'image-write'})
        self.controller.worker_context.cancel = threading.Event()
        self.controller.worker_context.cancel.set()
        with patch('forge_controller.subprocess.Popen') as launch:
            with self.assertRaises(JobCancelled):
                self.controller.lifecycle('test', 'checkpoint', ['not-started'])
            launch.assert_not_called()

    def test_cancelled_boot_does_not_touch_previous_runtime(self):
        self.controller.grants = frozenset({'boot', 'force-stop'})
        self.controller.worker_context.cancel = threading.Event()
        self.controller.worker_context.cancel.set()
        previous = MagicMock()
        self.controller.runtimes['test'] = previous
        with patch('forge_controller.Runtime') as runtime:
            with self.assertRaises(JobCancelled):
                self.controller.boot('test', 'maintenance')
            runtime.assert_not_called()
        self.assertIs(self.controller.runtimes.pop('test'), previous)
        self.assertEqual(previous.mock_calls, [])

    @unittest.skipUnless(os.name == 'posix', 'POSIX owned-process fixture')
    def test_running_boot_cancellation_reaps_only_owned_child(self):
        self.controller.grants = frozenset({'boot', 'force-stop'})
        entered = threading.Event()

        def waiting(*args, **kwargs):
            entered.set()
            return wait_for_log(*args, **kwargs)

        with patch('forge_runtime.refresh_boot'), \
             patch('uconsole_emulator.command', return_value=[
                 sys.executable, '-c', 'import signal; signal.pause()']), \
             patch('forge_controller.wait_for_log', side_effect=waiting):
            job = self.controller.call('boot', {'workspace': 'test'})
            try:
                self.assertTrue(entered.wait(timeout=5))
                runtime = self.controller.runtimes['test']
                self.assertIsNone(runtime.process.poll())
                with self.assertRaisesRegex(ValueError, 'busy'):
                    WorkspaceLock(self.path)
                self.assertTrue(self.controller.cancel(job['job_id'])['requested'])
                with self.assertRaises(JobCancelled):
                    self.controller.jobs[job['job_id']][2].result(timeout=5)
                state = self.controller.job(job['job_id'])
                self.assertEqual(state['status'], 'cancelled')
                self.assertIn('filesystem may be unclean', state['detail'])
                self.assertIsNotNone(runtime.process.poll())
                self.assertIsNone(runtime.directory)
                with WorkspaceLock(self.path):
                    pass
            finally:
                runtime = self.controller.runtimes.get('test')
                if runtime and runtime.process.poll() is None:
                    runtime.stop(force=True)

    def test_boot_cancellation_cleanup_failure_retains_owner_and_reports_failure(self):
        self.controller.grants = frozenset({'boot', 'force-stop'})
        entered = threading.Event()

        def waiting(*args, **kwargs):
            entered.set()
            return wait_for_log(*args, **kwargs)

        with patch('forge_controller.Runtime') as factory, \
             patch('forge_controller.wait_for_log', side_effect=waiting):
            runtime = factory.return_value.start.return_value
            runtime.process.poll.return_value = None
            runtime.stop.side_effect = TimeoutError('owned child did not exit')
            job = self.controller.call('boot', {'workspace': 'test'})
            try:
                self.assertTrue(entered.wait(timeout=5))
                self.controller.cancel(job['job_id'])
                with self.assertRaisesRegex(TimeoutError, 'did not exit'):
                    self.controller.jobs[job['job_id']][2].result(timeout=5)
                self.assertEqual(self.controller.job(job['job_id'])['status'], 'failed')
                self.assertIs(self.controller.runtimes['test'], runtime)
                runtime.stop.assert_called_once_with(force=True)
                runtime.release.assert_not_called()
            finally:
                runtime.process.poll.return_value = 0

    def test_start_cleanup_failure_retains_exact_runtime(self):
        self.controller.grants = frozenset({'boot', 'force-stop'})
        with patch('forge_controller.Runtime') as factory:
            runtime = factory.return_value
            runtime.process.poll.return_value = None
            runtime.start.side_effect = TimeoutError('initial scenario cleanup failed')
            job = self.controller.call('boot', {'workspace': 'test'})
            try:
                with self.assertRaisesRegex(TimeoutError, 'cleanup failed'):
                    self.controller.jobs[job['job_id']][2].result(timeout=5)
                self.assertIs(self.controller.runtimes['test'], runtime)
                self.assertEqual(self.controller.job(job['job_id'])['status'], 'failed')
            finally:
                runtime.process.poll.return_value = 0

    def test_cancel_acceptance_rejects_already_completed_boot(self):
        call = MagicMock(side_effect=[{'job_id': 'test-job'},
                                     {'runtime': {'owned': True, 'running': True}},
                                     {'status': 'completed'}])
        with self.assertRaisesRegex(AssertionError, 'terminal before running cancellation'):
            cancel_boot(call, self.path)
        self.assertNotIn('job_cancel', [entry.args[0] for entry in call.call_args_list])

    def test_cancel_acceptance_requires_observed_owned_runtime(self):
        call = MagicMock(side_effect=[{'job_id': 'test-job'},
                                     {'runtime': {'owned': False, 'running': None}},
                                     {'status': 'running'}])
        with patch('validate_forge_mcp.time.monotonic', side_effect=[0, 0, 181]), \
             patch('validate_forge_mcp.time.sleep'):
            with self.assertRaisesRegex(TimeoutError, 'owned running QEMU'):
                cancel_boot(call, self.path)
        self.assertNotIn('job_cancel', [entry.args[0] for entry in call.call_args_list])


if __name__ == '__main__':
    unittest.main()
