from pathlib import Path
import tempfile
import unittest

from validate_target_recovery_inspection import run, verify


class RecoveryValidationTests(unittest.TestCase):
    def test_hash_mismatch_precedes_output_or_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'plan.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'hash differs'):
                run(root, 'a'*64, root/'output')
            self.assertFalse((root/'output').exists())

    def test_oracle_requires_exact_job_and_no_qualification(self):
        report = dict(recovery_qualified=False, error_paths=[], watchdog_observations={
            'firmware_handoff_qualified': False, 'failed_boot_fallback_qualified': False})
        job = dict(status='completed', operation='target_recovery_inspect',
                   context={'policy_sha256': 'pin'}, result=report)
        self.assertIs(verify(job, 'pin'), report)
        for field, value in [('status', 'failed'), ('operation', 'target_apply'),
                             ('context', {'policy_sha256': 'wrong'})]:
            with self.assertRaises(ValueError):
                verify(dict(job, **{field: value}), 'pin')
        report['watchdog_observations']['failed_boot_fallback_qualified'] = True
        with self.assertRaises(ValueError):
            verify(job, 'pin')
