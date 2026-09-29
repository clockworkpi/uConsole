"""Native startup options without opening a display or changing preferences."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import uconsole_workbench as workbench


class NativeStartup(unittest.TestCase):
    def test_recovery_policy_pair_required_before_tk_and_forwarded(self):
        argv = ['uconsole-workbench', '--recovery-policy', '/owner/policy.json']
        with patch.object(sys, 'argv', argv), patch.object(workbench.tk, 'Tk') as tk, \
                patch('sys.stderr'), self.assertRaises(SystemExit):
            workbench.main()
        tk.assert_not_called()
        with patch.object(sys, 'argv', argv + ['--recovery-policy-sha256', 'a'*64]), \
                patch.object(workbench.tk, 'Tk'), patch.object(workbench, 'Workbench') as app:
            workbench.main()
        self.assertEqual(app.call_args.kwargs['recovery_policy'], Path('/owner/policy.json'))
        self.assertEqual(app.call_args.kwargs['recovery_policy_sha256'], 'a'*64)

    def test_appkit_option_preserved_for_tk(self):
        for value in ('YES', 'NO'):
            argv = ['uconsole-workbench', '-ApplePersistenceIgnoreState', value]
            with self.subTest(value=value), patch.object(sys, 'platform', 'darwin'), \
                    patch.object(sys, 'argv', argv), patch.object(workbench.tk, 'Tk') as tk, \
                    patch.object(workbench, 'Workbench'):
                tk.side_effect = lambda: self.assertEqual(sys.argv, argv) or unittest.mock.MagicMock()
                workbench.main()
                tk.assert_called_once()

    def test_invalid_or_non_native_option_rejected_before_tk(self):
        for platform, value in (('darwin', 'maybe'), ('linux', 'YES')):
            with self.subTest(platform=platform), patch.object(sys, 'platform', platform), \
                    patch.object(sys, 'argv', ['uconsole-workbench', '-ApplePersistenceIgnoreState', value]), \
                    patch.object(workbench.tk, 'Tk') as tk, patch('sys.stderr'), \
                    self.assertRaises(SystemExit) as failure:
                workbench.main()
            self.assertEqual(failure.exception.code, 2)
            tk.assert_not_called()
