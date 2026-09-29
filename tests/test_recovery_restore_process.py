import io
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import forge_recovery_restore_process as transport


class RestoreProcessTests(unittest.TestCase):
    def setUp(self):
        self.errors = io.BytesIO()
        self.processes = []
        original = subprocess.Popen
        def launch(*args, **kwargs):
            process = original(*args, **kwargs)
            self.processes.append(process)
            return process
        patcher = patch.object(transport.subprocess, 'Popen', side_effect=launch)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_child(self, source, operation=None, **options):
        if operation is None:
            def operation(channel, half_close, wait_success):
                half_close()
                data = bytearray()
                while True:
                    part = channel.read(65536)
                    if not part: break
                    data.extend(part)
                wait_success()
                return bytes(data)
        return transport.run([sys.executable, '-I', '-c', source], operation, self.errors,
                             timeout=options.get('timeout', 5), idle_timeout=options.get('idle_timeout', 1))

    def test_half_close_keeps_output_and_diagnostics_available(self):
        result = self.run_child("import sys; assert sys.stdin.buffer.read()==b''; "
                                "sys.stderr.write('diagnostic');sys.stdout.buffer.write(b'complete')")
        self.assertEqual(result, b'complete')
        self.assertEqual(self.errors.getvalue(), b'diagnostic')
        self.assertEqual(self.processes[0].returncode, 0)

    def test_large_stderr_does_not_deadlock_before_stdout(self):
        result = self.run_child("import os;os.write(2,b'x'*60000);os.write(1,b'ready')")
        self.assertEqual(result, b'ready')
        self.assertEqual(len(self.errors.getvalue()), 60000)

    def test_diagnostic_overflow_is_bounded_and_child_is_reaped(self):
        with self.assertRaisesRegex(ValueError, 'diagnostics exceed'):
            self.run_child("import os;os.write(2,b'x'*100000);os.write(1,b'ready')")
        self.assertEqual(len(self.errors.getvalue()), transport.MAX_ERRORS)
        self.assertIsNotNone(self.processes[0].poll())

    def test_stdout_overflow_rejects_even_zero_exit(self):
        with patch.object(transport, 'MAX_OUTPUT', 20):
            with self.assertRaisesRegex(ValueError, 'protocol output exceeds'):
                self.run_child("import os;os.write(1,b'x'*21)")
        self.assertIsNotNone(self.processes[0].poll())

    def test_nonzero_exit_never_returns_successful_output(self):
        with self.assertRaisesRegex(RuntimeError, 'exited unsuccessfully'):
            self.run_child("import os;os.write(1,b'complete');raise SystemExit(9)")

    def test_stalled_protocol_times_out_and_reaps_child(self):
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.run_child('import time;time.sleep(10)', idle_timeout=0.05)
        self.assertLess(time.monotonic()-started, 3)
        self.assertIsNotNone(self.processes[0].poll())

    def test_closed_stdout_but_live_process_has_exit_deadline(self):
        with self.assertRaises((TimeoutError, subprocess.TimeoutExpired)):
            self.run_child('import os,time;os.close(1);os.close(2);time.sleep(10)', idle_timeout=0.1)
        self.assertIsNotNone(self.processes[0].poll())

    def test_operation_failure_reaps_child_without_replacing_original_error(self):
        def operation(*_): raise ValueError('journal failure')
        with self.assertRaisesRegex(ValueError, 'journal failure'):
            self.run_child('import time;time.sleep(10)', operation)
        self.assertIsNotNone(self.processes[0].poll())

    def test_callback_cannot_skip_stdout_eof_verification(self):
        with self.assertRaisesRegex(ValueError, 'output EOF'):
            self.run_child("print('ready')", lambda *_: 'claimed success')

    def test_invalid_options_rejected_before_launch(self):
        for timeout in (True, 0, float('nan'), 90000):
            with self.assertRaises(ValueError): self.run_child("print('ready')", timeout=timeout)
        self.assertEqual(self.processes, [])

    def test_diagnostic_flush_failure_does_not_hide_original_operation_error(self):
        class FailedJournal(io.BytesIO):
            def flush(self): raise OSError('diagnostic journal failed')
        self.errors = FailedJournal()
        def operation(*_): raise ValueError('original owner failure')
        with self.assertRaisesRegex(ValueError, 'original owner failure'):
            self.run_child('import time;time.sleep(10)', operation)
        self.assertIsNotNone(self.processes[0].poll())


if __name__ == '__main__': unittest.main()
