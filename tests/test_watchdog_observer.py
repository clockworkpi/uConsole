import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import observe_emulator_watchdog as observer


class ObserverTests(unittest.TestCase):
    def test_parse_and_reject_wrong_registers(self):
        value = observer.registers('00000000fe10001c: 0x122 0x1000 0x20000\r\n')
        self.assertTrue(value['armed'])
        self.assertEqual(value['remaining_seconds'], 2)
        for invalid in ('error', 'fe100024: 0x122 0x1000 0x20000',
                        'fe10001c: 0x122 0x1000 0x100000'):
            with self.assertRaises(ValueError):
                observer.registers(invalid)

    def test_scope_and_explicit_pause(self):
        for threshold in (None, 3):
            calls, output = [], []
            def qmp(endpoint, operation, arguments=None):
                calls.append(operation)
                if operation == 'query-name':
                    return {'name': 'owned'}
                if operation == 'query-status':
                    return {'running': calls.count('query-status') == 1}
                if operation == 'human-monitor-command':
                    return ('fe10001c: 0x122 0x1000 0x20000'
                            if arguments['command-line'].startswith('xp') else 'CPU registers')
                return {}
            with patch.object(observer, 'qmp', side_effect=qmp), patch.object(observer.time, 'sleep'):
                observer.observe(Path('/private/qmp'), 'owned', pause_below=threshold, emit=output.append)
            self.assertEqual('stop' in calls, threshold is not None)
            self.assertNotIn('cont', calls)
            self.assertNotIn('quit', calls)
            for index, operation in enumerate(calls):
                if operation != 'query-name':
                    self.assertEqual(calls[index - 1], 'query-name')
            self.assertEqual(sum(e['event'] == 'cpu-registers' for e in output), 4 if threshold else 0)

    def test_identity_mismatch_and_invalid_limits_fail_before_mutation(self):
        with patch.object(observer, 'qmp', return_value={'name': 'other'}) as qmp:
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                observer.observe(Path('/private/qmp'), 'owned', pause_below=3)
            self.assertEqual(qmp.call_count, 1)
        for arguments in ({'duration': float('nan')}, {'duration': 301},
                          {'interval': 0}, {'pause_below': float('inf')}):
            with patch.object(observer, 'qmp') as qmp:
                with self.assertRaises(ValueError):
                    observer.observe(Path('/private/qmp'), 'owned', **arguments)
                qmp.assert_not_called()


if __name__ == '__main__':
    unittest.main()
