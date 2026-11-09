"""Guest supervisor process-group semantics, using real Linux subprocesses."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import forge_guest_jobs as jobs
import forge_guest_worker as worker
from uconsole_agent import GuestChannelUncertain


@unittest.skipUnless(sys.platform == 'linux', 'The guest supervisor targets Linux /proc')
class GuestWorkerTests(unittest.TestCase):
    def run_job(self, script, timeout=3):
        with tempfile.TemporaryDirectory(prefix='guest-worker-test-') as directory:
            path = Path(directory)
            (path / 'script').write_text(script.replace('@JOB@', directory))
            result = subprocess.run([sys.executable, worker.__file__, directory, str(timeout)],
                                    capture_output=True, text=True, timeout=12)
            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads((path / 'result.json').read_text())
            group = int((path / 'started').read_text())
            self.assertEqual(worker.members(group), [])
            self.assertTrue(state['process_group_terminated'], state)
            return state

    def test_exit_code_and_bounded_separate_output(self):
        result = self.run_job("printf 'hello'; head -c 100000 /dev/zero | tr '\\0' x >&2; exit 7")
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['exit_code'], 7)
        self.assertEqual(result['stdout'], 'hello')
        self.assertEqual(result['stderr'], 'x' * worker.TAIL)

    def test_timeout_kills_term_ignoring_group(self):
        result = self.run_job("trap '' TERM; sleep 60 & wait", timeout=0.2)
        self.assertEqual(result['status'], 'timeout')
        self.assertTrue(result['timed_out'])
        self.assertEqual(result['exit_code'], 124)

    def test_cancel_kills_running_group(self):
        result = self.run_job("trap '' TERM; sleep 60 & touch '@JOB@/cancel'; wait")
        self.assertEqual(result['status'], 'cancelled')
        self.assertFalse(result['timed_out'])

    def test_shell_completion_reaps_background_group_members(self):
        result = self.run_job('sleep 60 & echo done')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['stdout'], 'done\n')


class GuestJobTransportTests(unittest.TestCase):
    def test_prelaunch_cancellation_does_not_contact_guest(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(jobs, 'serial_exec') as execute:
            with self.assertRaises(jobs.GuestJobCancelled):
                jobs.execute(1234, 'true', cancel=cancel)
            execute.assert_not_called()

    def test_invalid_directory_is_not_used_for_upload_or_cleanup(self):
        with patch.object(jobs, 'serial_exec', return_value={'exit_code': 0, 'stdout': '/tmp'}), \
             patch.object(jobs, 'guest_put') as upload:
            with self.assertRaises(GuestChannelUncertain):
                jobs.execute(1234, 'true')
            upload.assert_not_called()

    def test_ambiguous_or_wrong_directory_marker_is_rejected(self):
        directory = '/tmp/uconsole-forge-job.ABCDEFGH1234'
        for reply in (f'UC_JOB_DIRECTORY_other:{directory}\n',
                      f'UC_JOB_DIRECTORY_fixture:{directory}\nUC_JOB_DIRECTORY_fixture:{directory}\n'):
            with self.subTest(reply=reply), patch.object(jobs.uuid, 'uuid4') as identity, \
                    patch.object(jobs, 'serial_exec', return_value={'exit_code': 0, 'stdout': reply}) as execute, \
                    patch.object(jobs, 'guest_put') as upload:
                identity.return_value.hex = 'fixture'
                with self.assertRaises(GuestChannelUncertain):
                    jobs.execute(1234, 'true')
                execute.assert_called_once()
                upload.assert_not_called()

    def test_uncertain_launch_does_not_send_cleanup(self):
        with patch.object(jobs.uuid, 'uuid4') as identity, patch.object(jobs, 'serial_exec', side_effect=[
                {'exit_code': 0, 'stdout': 'UC_JOB_DIRECTORY_fixture:/tmp/uconsole-forge-job.ABCDEFGH1234'},
                GuestChannelUncertain('launch acknowledgement lost')]) as execute, \
             patch.object(jobs, 'guest_put'):
            identity.return_value.hex = 'fixture'
            with self.assertRaises(GuestChannelUncertain):
                jobs.execute(1234, 'true')
            self.assertEqual(execute.call_count, 2)

    def test_kernel_console_noise_is_not_interpreted_as_job_state(self):
        done = {'status': 'completed', 'exit_code': 0, 'process_group_terminated': True}
        encoded = base64.b64encode(json.dumps(done).encode()).decode()
        ok = {'exit_code': 0, 'stdout': ''}
        replies = [dict(ok, stdout='[3.4] USB input ready\nUC_JOB_DIRECTORY_fixture:/tmp/uconsole-forge-job.ABCDEFGH1234\n[3.5] more kernel output'), ok,
                   dict(ok, stdout='[9.3] random: crng init done\n'),
                   dict(ok, stdout='[9.4] kernel message\nUC_JOB_RUNNING:123\n'),
                   dict(ok, stdout='UC_JOB_RESULT:' + encoded + '\n'), ok]
        started = []
        with patch.object(jobs.uuid, 'uuid4') as identity, patch.object(jobs, 'serial_exec', side_effect=replies), \
             patch.object(jobs, 'guest_put'):
            identity.return_value.hex = 'fixture'
            self.assertEqual(jobs.execute(1234, 'true', on_started=started.append), done)
        self.assertEqual(started[0]['process_group'], 123)


if __name__ == '__main__':
    unittest.main()
