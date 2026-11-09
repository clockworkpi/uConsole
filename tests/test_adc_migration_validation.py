"""Acceptance-harness bounds and owned-process cleanup, not migration proof."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import test_emulator_adc_migration as validator
import test_emulator_pmic_migration as pmic


class ADCMigrationValidationTests(unittest.TestCase):
    def test_pmic_case_and_legacy_source_are_required_before_launch(self):
        with patch.object(pmic, 'Machine') as machine:
            with self.assertRaisesRegex(ValueError, 'Unknown PMIC'):
                pmic.exercise('typo')
            with self.assertRaisesRegex(ValueError, 'version-2 source'):
                pmic.exercise('legacy')
            machine.assert_not_called()

    def test_migration_failure_is_not_reported_as_completion(self):
        source, target = MagicMock(monitor='source'), MagicMock(monitor='target')
        with patch.object(validator, 'qmp', side_effect=[{}, {},
                {'status': 'failed', 'error-desc': 'fixture failure'}]):
            with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
                validator.migrate(source, target, Path('/fixture'))

    def test_unknown_case_cannot_silently_pass(self):
        with patch.object(validator, 'Machine') as machine:
            with self.assertRaisesRegex(ValueError, 'Unknown ADC migration case'):
                validator.exercise('typo')
            machine.assert_not_called()

    def test_cleanup_does_not_signal_a_terminal_process(self):
        machine = object.__new__(validator.Machine)
        machine.process = MagicMock()
        machine.process.poll.return_value = 0
        machine.close()
        machine.process.terminate.assert_not_called()
        machine.process.kill.assert_not_called()

    def test_cleanup_escalates_only_the_owned_process_after_deadline(self):
        machine = object.__new__(validator.Machine)
        machine.process = MagicMock()
        machine.process.poll.return_value = None
        machine.process.wait.side_effect = [subprocess.TimeoutExpired('owned-qemu', 10), 0]
        machine.close()
        machine.process.terminate.assert_called_once_with()
        machine.process.kill.assert_called_once_with()
        self.assertEqual([call.kwargs['timeout'] for call in machine.process.wait.call_args_list], [10, 5])
