from pathlib import Path
import unittest
from unittest.mock import patch

from validate_recovery_ssh import client, inside


class RecoverySshTests(unittest.TestCase):
    def test_client_disables_ambient_keys_and_requires_pinned_host(self):
        command = client(Path('/private/key'), Path('/private/known_hosts'))
        for setting in ('IdentityAgent=none', 'IdentitiesOnly=yes', 'BatchMode=yes',
                        'StrictHostKeyChecking=yes', 'GlobalKnownHostsFile=/dev/null',
                        'UserKnownHostsFile=/private/known_hosts'):
            self.assertIn(setting, command)
        self.assertEqual(command[:3], ['ssh', '-F', '/dev/null'])
        self.assertIn('root@127.0.0.1', command)

    def test_host_namespace_refused_before_any_mutation(self):
        with patch('validate_recovery_ssh.os.readlink', return_value='same'), \
                patch('validate_recovery_ssh.subprocess.run') as run:
            with self.assertRaises(RuntimeError):
                inside('/unused', '/unused')
        run.assert_not_called()
