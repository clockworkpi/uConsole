"""Cooperative transfer cancellation at acknowledged transaction boundaries."""
import base64
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import uconsole_agent as agent
from forge_controller import Controller


class TransferCancellationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.source = self.path / 'source'
        self.source.write_bytes(b'x' * 2000)
        self.cancel = threading.Event()
        self.ok = {'exit_code': 0, 'stdout': ''}

    def test_upload_cancel_between_chunks_cleans_staging_without_installing(self):
        commands = []

        def execute(port, script):
            commands.append(script)
            if 'base64 -d >>' in script:
                self.cancel.set()
            return self.ok

        with patch.object(agent, 'serial_exec', side_effect=execute):
            with self.assertRaises(agent.GuestTransferCancelled):
                agent.guest_put(1234, self.source, '/tmp/target', cancel=self.cancel)
        self.assertEqual(len(commands), 4)
        self.assertTrue(commands[-1].startswith('rm -f /tmp/uconsole-agent-'))
        self.assertFalse(any('install -m' in script for script in commands))

    def test_upload_commit_wins_late_cancellation(self):
        def execute(port, script):
            if 'install -m' in script:
                self.cancel.set()
            return self.ok

        with patch.object(agent, 'serial_exec', side_effect=execute):
            result = agent.guest_put(1234, self.source, '/tmp/target', cancel=self.cancel)
        self.assertTrue(self.cancel.is_set())
        self.assertEqual(result['bytes'], 2000)

    def test_download_cancels_after_ack_before_host_publication(self):
        target = self.path / 'download'

        def execute(port, script):
            self.cancel.set()
            return dict(self.ok, stdout='UC_DATA_fixture:' + base64.b64encode(b'content').decode())

        with patch.object(agent.uuid, 'uuid4', return_value=MagicMock(hex='fixture')), \
             patch.object(agent, 'serial_exec', side_effect=execute):
            with self.assertRaises(agent.GuestTransferCancelled):
                agent.guest_get(1234, '/tmp/source', target, cancel=self.cancel)
        self.assertFalse(target.exists())

    def test_pre_cancelled_transfer_does_not_contact_guest(self):
        self.cancel.set()
        with patch.object(agent, 'serial_exec') as execute:
            for transfer in (lambda: agent.guest_put(1234, self.source, '/tmp/target', cancel=self.cancel),
                             lambda: agent.guest_get(1234, '/tmp/source', self.path / 'target',
                                                      cancel=self.cancel)):
                with self.assertRaises(agent.GuestTransferCancelled):
                    transfer()
            execute.assert_not_called()

    def test_uncertain_cleanup_overrides_cancellation(self):
        def execute(port, script):
            if 'base64 -d >>' in script:
                self.cancel.set()
            if script.startswith('rm -f'):
                raise agent.GuestChannelUncertain('cleanup not acknowledged')
            return self.ok

        with patch.object(agent, 'serial_exec', side_effect=execute):
            with self.assertRaises(agent.GuestChannelUncertain):
                agent.guest_put(1234, self.source, '/tmp/target', cancel=self.cancel)

    def test_controller_requests_running_transfer_cancel_without_stopping_vm(self):
        for operation in ('upload', 'download'):
            with self.subTest(operation=operation):
                controller = Controller({'test': self.path}, grants=('transfer',), files_root=self.path)
                entered = threading.Event()
                runtime = MagicMock()

                def transfer(*args, cancel):
                    entered.set()
                    if not cancel.wait(5):
                        raise TimeoutError('fixture cancellation missing')
                    raise agent.GuestTransferCancelled('Transfer cancelled')

                getattr(runtime, operation).side_effect = transfer
                try:
                    with patch.object(controller, 'runtime', return_value=runtime):
                        job = controller.call(operation, {'workspace': 'test', 'host_path': 'source',
                                                          'guest_path': '/tmp/target'})
                        self.assertTrue(entered.wait(5))
                        self.assertTrue(controller.cancel(job['job_id'])['requested'])
                        with self.assertRaises(agent.GuestTransferCancelled):
                            controller.jobs[job['job_id']][2].result(timeout=5)
                        self.assertEqual(controller.job(job['job_id'])['status'], 'cancelled')
                        runtime.stop.assert_not_called()
                finally:
                    controller.close()


if __name__ == '__main__':
    unittest.main()
