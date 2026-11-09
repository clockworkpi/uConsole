from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from validate_recovery_build import check_modules


class ModulePreflightTests(unittest.TestCase):
    def test_resolution_is_dry_run_and_bound_to_selected_kernel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / 'lib/modules/fixture/driver.ko'
            module.parent.mkdir(parents=True)
            module.touch()
            with patch('validate_recovery_build.subprocess.run') as run:
                run.return_value.stdout = 'insmod /lib/modules/fixture/driver.ko option=1\n'
                result = check_modules(root, 'fixture')
                self.assertEqual(set(result), {'bcm2835_wdt', 'brcmfmac', 'cdc_ether',
                                              'vfat', 'nls_cp437', 'nls_ascii'})
                for call in run.call_args_list:
                    self.assertEqual(call.args[0][3:6],
                                     ['--show-depends', '--set-version', 'fixture'])
                    self.assertTrue(call.kwargs['check'])

    def test_builtin_is_accepted(self):
        with patch('validate_recovery_build.subprocess.run') as run:
            run.return_value.stdout = 'builtin fixture\n'
            self.assertEqual(len(check_modules(Path('/unused'), 'fixture')), 6)

    def test_dynamic_vendor_is_resolved_explicitly(self):
        with patch('validate_recovery_build.subprocess.run') as run:
            run.return_value.stdout = 'builtin fixture\n'
            result = check_modules(Path('/unused'), 'fixture', ['brcmfmac_wcc'])
            self.assertIn('brcmfmac_wcc', result)
            self.assertEqual(run.call_args.args[0][-1], 'brcmfmac_wcc')

    def test_bad_resolution_is_rejected(self):
        for output in ('', 'error missing module\n', 'insmod /lib/modules/other/x.ko\n'):
            with self.subTest(output=output), patch('validate_recovery_build.subprocess.run') as run:
                run.return_value.stdout = output
                with self.assertRaises(RuntimeError):
                    check_modules(Path('/unused'), 'fixture')

    def test_failed_modprobe_is_not_accepted(self):
        with patch('validate_recovery_build.subprocess.run',
                   side_effect=subprocess.CalledProcessError(1, 'modprobe')):
            with self.assertRaises(subprocess.CalledProcessError):
                check_modules(Path('/unused'), 'fixture')
