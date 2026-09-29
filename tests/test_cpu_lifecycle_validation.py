from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import test_emulator_cpu_lifecycle as validation


class CPULifecycleTests(unittest.TestCase):
    def test_monitor_negotiates_once_and_matches_acknowledgements(self):
        stream = mock.Mock()
        stream.readline.side_effect = [json.dumps(value).encode() + b'\n' for value in (
            {'QMP': {}}, {'id': 1, 'return': {}}, {'event': 'STOP'},
            {'id': 2, 'return': {}}, {'id': 3, 'return': {'status': 'paused'}})]
        sock = mock.Mock()
        sock.makefile.return_value = nullcontext(stream)
        with mock.patch.object(validation, 'connect', return_value=nullcontext(sock)):
            with validation.monitor(Path('/endpoint'), mock.Mock()) as command:
                self.assertEqual(command('stop'), {})
                self.assertEqual(command('query-status'), {'status': 'paused'})
        self.assertEqual([json.loads(call.args[0]) for call in stream.write.call_args_list],
                         [{'execute': 'qmp_capabilities', 'id': 1},
                          {'execute': 'stop', 'id': 2}, {'execute': 'query-status', 'id': 3}])

    def test_monitor_does_not_treat_disconnect_or_error_as_acknowledgement(self):
        for response in (b'', b'{"id": 2, "error": {"desc": "failed"}}\n'):
            with self.subTest(response=response):
                stream = mock.Mock()
                stream.readline.side_effect = [b'{"QMP": {}}\n', b'{"id": 1, "return": {}}\n', response]
                sock = mock.Mock()
                sock.makefile.return_value = nullcontext(stream)
                with mock.patch.object(validation, 'connect', return_value=nullcontext(sock)):
                    with validation.monitor(Path('/endpoint'), mock.Mock()) as command:
                        with self.assertRaises((ConnectionError, RuntimeError)):
                            command('stop')
                self.assertEqual(stream.write.call_count, 2)

    def test_running_cpu_operations_and_clean_exit_are_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'evidence'
            process = mock.Mock(pid=123)
            process.wait.return_value = 0
            process.poll.return_value = 0
            control = mock.Mock()
            with mock.patch.object(validation.subprocess, 'Popen', return_value=process) as spawn, \
                    mock.patch.object(validation, 'monitor', return_value=nullcontext(control)):
                validation.validate('/qemu', output, 1, 2)
            self.assertNotIn('-S', spawn.call_args.args[0])
            self.assertEqual([call.args[0] for call in control.call_args_list],
                             ['system_reset', 'stop', 'cont'] * 2 + ['quit'])
            events = [json.loads(line) for line in (output / 'events.jsonl').read_text().splitlines()]
            self.assertEqual(events[-1], {'passed': True, 'iterations': 1, 'cycles': 2})
            process.kill.assert_not_called()

    def test_timeout_is_failure_and_owned_child_is_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'evidence'
            process = mock.Mock(pid=123)
            process.poll.return_value = None
            process.wait.side_effect = [subprocess.TimeoutExpired('qemu', 5), -9]
            control = mock.Mock(side_effect=TimeoutError('reset timeout'))
            with mock.patch.object(validation.subprocess, 'Popen', return_value=process), \
                    mock.patch.object(validation, 'monitor', return_value=nullcontext(control)):
                with self.assertRaisesRegex(TimeoutError, 'reset timeout'):
                    validation.validate('/qemu', output, 2, 2)
            process.terminate.assert_called_once()
            process.kill.assert_called_once()
            events = [json.loads(line) for line in (output / 'events.jsonl').read_text().splitlines()]
            self.assertEqual(events[-1], {'iteration': 0, 'forced_kill': True})
            self.assertFalse(any(event.get('passed') for event in events))

    def test_existing_evidence_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(validation.subprocess, 'Popen') as spawn:
            with self.assertRaises(FileExistsError):
                validation.validate('/qemu', Path(directory), 1, 1)
            spawn.assert_not_called()
