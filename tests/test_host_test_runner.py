import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from run_host_tests import main


class HostTestRunnerTests(unittest.TestCase):
    def fixture(self, body, interval=10):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'test_fixture.py').write_text('import time, unittest\n'
                'class Fixture(unittest.TestCase):\n    def test_fixture(self):\n        '+body+'\n')
            source = ('import sys; sys.path.insert(0, '+repr(str(Path(__file__).resolve().parents[1]/'tools'))+'); '
                'from run_host_tests import main; main('+repr(['discover', '-s', str(root), '-v'])+
                ', dump_after='+repr(interval)+')')
            try:
                return subprocess.run([sys.executable, '-c', source], capture_output=True,
                                      text=True, timeout=15, env=dict(os.environ))
            except subprocess.TimeoutExpired as exc:
                # TimeoutExpired's normal traceback omits captured diagnostics.
                # Keep the child's periodic stacks when investigating a CI stall.
                def text(value):
                    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''
                self.fail('Runner fixture timed out after 15 seconds\nstdout:\n'+text(exc.stdout)+
                          '\nstderr:\n'+text(exc.stderr))

    def test_timeout_retains_child_diagnostics(self):
        error = subprocess.TimeoutExpired('fixture', 15, output=b'child output',
                                         stderr=b'child traceback\xff')
        with patch('subprocess.run', side_effect=error):
            with self.assertRaises(AssertionError) as caught:
                self.fixture('self.assertTrue(True)')
        self.assertIn('child output', str(caught.exception))
        self.assertIn('child traceback', str(caught.exception))
        self.assertIn('timed out after 15 seconds', str(caught.exception))

    def test_success_preserves_unittest_exit_and_count(self):
        result = self.fixture('self.assertTrue(True)')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Ran 1 test', result.stderr)
        self.assertIn('OK', result.stderr)

    def test_failure_preserves_nonzero_exit(self):
        result = self.fixture('self.fail("fixture failure")')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('FAILED (failures=1)', result.stderr)

    def test_slow_test_emits_stack_without_changing_result(self):
        result = self.fixture('time.sleep(0.4)', interval=0.05)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Timeout (', result.stderr)
        self.assertIn('test_fixture.py', result.stderr)
        self.assertIn('OK', result.stderr)

    def test_invalid_interval_cannot_start_suite(self):
        with patch('run_host_tests.unittest.main') as suite:
            for value in (0, -1, float('inf'), float('nan'), True, '300'):
                with self.assertRaises(ValueError): main([], dump_after=value)
            suite.assert_not_called()
