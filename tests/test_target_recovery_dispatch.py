import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from forge_target_recovery import inspect
from target_recovery_inventory import PATHS


class RecoveryDispatchTests(unittest.TestCase):
    def test_changed_service_authorization_never_connects(self):
        from forge_target_service_dispatch import digest
        authorization = {'schema': 1, 'host': 'clockworkpi.local',
                         'transaction_sha256': 'a'*64, 'ledger_parent': '/private/ledger'}
        approved = SimpleNamespace(kind='service', journal='/unused',
                                   authorization_sha256=digest(authorization))
        changed = dict(authorization, host='different-target')
        with patch('forge_target_service_dispatch.locked') as locked, \
                patch('forge_target_journal.read_record', return_value=changed), \
                patch('forge_target_service_dispatch.transaction') as transaction, \
                patch('forge_target_recovery.subprocess.run') as ssh:
            locked.return_value.__enter__.return_value = 42
            with self.assertRaisesRegex(PermissionError, 'differs from owner'):
                inspect(approved)
            transaction.assert_not_called()
            ssh.assert_not_called()

    def response(self, command, **kwargs):
        request = json.loads(kwargs['input'])
        self.assertEqual(command[0], 'ssh')
        self.assertEqual(command[-2], 'clockworkpi.local')
        self.assertEqual(kwargs['timeout'], 45)
        report = dict(nonce=request['nonce'], machine_id=request['machine_id'],
                      recovery_qualified=False, error_paths=[],
                      file_status={p: 'absent' for p in PATHS},
                      watchdog_observations=dict(runtime_state='active', runtime_timeout='60',
                          kernel_open_timeout='0', bootstatus='0',
                          firmware_handoff_qualified=False, failed_boot_fallback_qualified=False))
        report.update(self.overrides)
        return SimpleNamespace(returncode=0, stdout=json.dumps(report))

    def test_transport_identity_nonce_and_no_readiness_inference(self):
        self.overrides = {}
        with patch('forge_target_recovery.binding', return_value=('clockworkpi.local', 'a'*32)), \
                patch('forge_target_recovery.subprocess.run', side_effect=self.response):
            result = inspect(object())
            self.assertFalse(result['recovery_qualified'])
            self.assertNotIn('base64', json.dumps(result))
            for override in ({'nonce': 'wrong'}, {'machine_id': 'b'*32},
                             {'recovery_qualified': True}, {'file_status': {}}):
                self.overrides = override
                with self.assertRaises(ValueError):
                    inspect(object())

    def test_bad_binding_never_connects(self):
        with patch('forge_target_recovery.binding', return_value=('-oProxyCommand=bad', 'a'*32)), \
                patch('forge_target_recovery.subprocess.run') as ssh:
            with self.assertRaises(ValueError):
                inspect(object())
            ssh.assert_not_called()

    def test_ssh_failure_is_not_readiness(self):
        with patch('forge_target_recovery.binding', return_value=('clockworkpi.local', 'a'*32)), \
                patch('forge_target_recovery.subprocess.run', return_value=SimpleNamespace(returncode=255)):
            with self.assertRaisesRegex(RuntimeError, 'no recovery readiness'):
                inspect(object())
