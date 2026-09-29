import unittest
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from forge_recovery_wifi import compile_config, capture, default_pmf


class RecoveryWifiTests(unittest.TestCase):
    def test_capture_freezes_profile_and_rejects_changes_without_output(self):
        connection = '755db6f9-79d2-459a-af5a-264ad917fb97'
        for changed in ('none', 'secret', 'policy'):
            calls = []
            def nmcli(args, **kwargs):
                if args[0] == '/usr/sbin/NetworkManager':
                    calls.append('global-config')
                    config = '[main]\nplugins=keyfile\n'
                    if changed == 'policy' and calls.count('global-config') == 2:
                        config += '[connection]\nwifi-sec.pmf=3\n'
                    return SimpleNamespace(returncode=0, stdout=config)
                field = args[args.index('-g')+1]
                calls.append(field)
                value = {'GENERAL.CON-UUID': connection, '802-11-wireless.ssid': 'IEEE',
                         '802-11-wireless-security.key-mgmt': 'wpa-psk',
                         '802-11-wireless-security.pmf': '0',
                         '802-11-wireless-security.psk': 'password'}.get(field, '')
                if changed == 'secret' and field.endswith('.psk') and calls.count(field) == 2:
                    value = 'changed-secret'
                return SimpleNamespace(returncode=0, stdout=value+'\n')
            with tempfile.TemporaryDirectory() as directory, \
                    patch('forge_recovery_wifi.os.geteuid', return_value=0), \
                    patch.object(Path, 'read_text', return_value='a'*32), \
                    patch.object(Path, 'lstat', return_value=SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR|0o700)), \
                    patch('forge_recovery_wifi.subprocess.run', side_effect=nmcli):
                output = Path(directory)/'credentials'
                if changed != 'none':
                    with self.assertRaisesRegex(ValueError, 'changed during'):
                        capture(output, connection, 'a'*32)
                    self.assertFalse(output.exists())
                else:
                    result = capture(output, connection, 'a'*32)
                    self.assertFalse(result['network_qualified'])
                    self.assertEqual(stat.S_IMODE((output/'wpa.conf').stat().st_mode), 0o600)
                    self.assertEqual((output/'wpa.conf').read_bytes(), compile_config('IEEE','password','wpa-psk','0','',resolved_default='2'))

    def test_known_psk_vector_and_hex_ssid(self):
        result = compile_config('IEEE', 'password', 'wpa-psk', '0', '', resolved_default='2')
        self.assertIn(b'psk=f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e', result)
        self.assertIn(b'ssid=49454545', result)
        self.assertNotIn(b'password', result)

    def test_prederived_psk_and_management_policy(self):
        for pmf, expected in [('1',0), ('2',1), ('3',2)]:
            result = compile_config('a"b\\c', 'AB'*32, 'wpa-psk', pmf, 'rsn')
            self.assertIn(b'psk=' + b'ab'*32, result)
            self.assertIn(('ieee80211w='+str(expected)).encode(), result)

    def test_unresolved_default_is_rejected_not_upgraded_or_downgraded(self):
        with self.assertRaisesRegex(ValueError, 'Resolve the effective PMF'):
            compile_config('IEEE','password','wpa-psk','0','')
        self.assertEqual(default_pmf('[main]\nplugins=keyfile\n'), '2')
        for config in ('', 'broken', '[connection]\nwifi-sec.pmf=3\n',
                       '[connection-match]\nwifi-sec.pmf=1\n', '[DEFAULT]\npmf=3\n[main]\n'):
            with self.assertRaises(ValueError):
                default_pmf(config)

    def test_unsupported_inputs_fail_without_echoing_secrets(self):
        for args in [('', 'password', 'wpa-psk', '0', ''),
                     ('a'*33, 'password', 'wpa-psk', '0', ''),
                     ('ssid', 'short', 'wpa-psk', '0', ''),
                     ('ssid', 'password', 'sae', '0', ''),
                     ('ssid', 'password', 'wpa-psk', '3', 'wpa'),
                     ('ssid\nnew', 'password', 'wpa-psk', '0', '')]:
            with self.assertRaises(ValueError) as failure:
                compile_config(*args)
            self.assertNotIn(args[1], str(failure.exception))
