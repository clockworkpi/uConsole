"""macOS zombie-only groups must not hide genuine signal permission failures."""
from pathlib import Path
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_controller import signal_owned_group, zombie_only_group


class HostSignalTests(unittest.TestCase):
    def test_success_and_absent_group_need_no_census(self):
        for error in (None, ProcessLookupError()):
            with patch('forge_controller.os.killpg', side_effect=error) as kill, \
                    patch('forge_controller.zombie_only_group') as census:
                signal_owned_group(123, signal.SIGKILL)
                kill.assert_called_once_with(123, signal.SIGKILL)
                census.assert_not_called()

    def test_permission_exception_only_for_verified_darwin_zombies(self):
        for platform, zombies in (('darwin', True), ('darwin', False), ('linux', True)):
            with self.subTest(platform=platform, zombies=zombies), \
                    patch('forge_controller.sys.platform', platform), \
                    patch('forge_controller.os.killpg', side_effect=PermissionError('denied')), \
                    patch('forge_controller.zombie_only_group', return_value=zombies) as census:
                if platform == 'darwin' and zombies:
                    signal_owned_group(123, signal.SIGKILL)
                else:
                    with self.assertRaises(PermissionError):
                        signal_owned_group(123, signal.SIGKILL)
                if platform != 'darwin':
                    census.assert_not_called()

    def test_census_requires_leader_and_no_live_group_members(self):
        cases = [('123 123 Z\n124 123 Z+\n9 9 S\n', True),
                 ('123 123 Z\n124 123 S\n', False), ('123 123 S\n', False),
                 ('124 123 Z\n', False), ('123 124 Z\n', False), ('', False),
                 ('bad output\n', False), ('123 123 Z\n123 123 Z\n', False)]
        for output, expected in cases:
            with self.subTest(output=output), patch('forge_controller.subprocess.run',
                    return_value=subprocess.CompletedProcess([], 0, output, '')) as run:
                self.assertEqual(zombie_only_group(123), expected)
                run.assert_called_once_with(['/bin/ps', '-axo', 'pid=,pgid=,stat='],
                    capture_output=True, text=True, timeout=5, check=True)

    def test_failed_census_does_not_confirm_exit(self):
        for error in (OSError('missing'), subprocess.TimeoutExpired('ps', 5),
                      subprocess.CalledProcessError(1, 'ps')):
            with patch('forge_controller.subprocess.run', side_effect=error):
                self.assertFalse(zombie_only_group(123))

    def test_invalid_group_never_signalled(self):
        with patch('forge_controller.os.killpg') as kill:
            for pid in (0, 1, -1, True, '123'):
                with self.assertRaises(ValueError):
                    signal_owned_group(pid, signal.SIGKILL)
            kill.assert_not_called()
