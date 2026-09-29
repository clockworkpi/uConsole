import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from validate_watchdog_trial import step


class WatchdogTrialDriverTests(unittest.TestCase):
    def test_alternate_apply_and_reboot_require_physical_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            review = root / 'review.json'
            review.write_text(json.dumps(dict(host='clockworkpi.local', kind='alternate-firmware-watchdog-trial')))
            review.chmod(0o600)
            with patch('validate_watchdog_trial.dispatch') as dispatch, patch('validate_watchdog_trial.subprocess.run') as run:
                for action in ('apply','reboot-trial'):
                    with self.assertRaises(PermissionError):
                        step(root, action, 'clockworkpi.local')
                dispatch.assert_not_called()
                run.assert_not_called()
            self.assertEqual([p.name for p in root.iterdir()], ['review.json'])

    def test_revision_cannot_be_changed_after_prepare(self):
        with self.assertRaises(ValueError):
            step('/nonexistent', 'apply', 'clockworkpi.local', firmware_revision='a'*40)

    def test_restore_available_without_boot_observation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            review = root / 'review.json'
            review.write_text(json.dumps(dict(host='clockworkpi.local', kind='alternate-firmware-watchdog-trial',
                phases=[dict(journal='first',plan_sha256='a'*64),dict(journal='second',plan_sha256='b'*64)])))
            review.chmod(0o600)
            with patch('validate_watchdog_trial.dispatch') as dispatch:
                step(root, 'restore', 'clockworkpi.local')
                self.assertEqual([c.args for c in dispatch.call_args_list], [('second','restore'),('first','restore')])
            self.assertTrue((root/'restore-complete.json').exists())
