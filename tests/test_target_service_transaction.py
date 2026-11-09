import base64
import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_target_effects import TargetEffects, fingerprint
from forge_target_service_transaction import prepare, validate
import forge_target_service_dispatch as dispatcher
from forge_target_ssh import TargetUncertain
if __package__:
    from .target_test_support import identity, linux_target
else:
    from target_test_support import identity, linux_target


class ServiceTransactionTests(unittest.TestCase):
    def setUp(self):
        self.identity = identity()
        self.path = '/etc/systemd/system/forge-proof.service'
        self.absent = {'path': self.path, 'kind': 'absent'}
        data = b'unit fixture'
        self.file = {'path': self.path, 'kind': 'file', 'data': base64.b64encode(data).decode(),
                     'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                     'uid': 0, 'gid': 0, 'mode': 0o644, 'mtime_ns': 1, 'atime_ns': 1, 'xattrs': {}}
        self.scope = {'services': [], 'files': [self.path], 'links': []}
        self.backup = {'identity': self.identity, 'state': self.state(self.absent),
                       'files': [self.absent], 'links': []}
        self.apply = self.envelope(self.absent, self.file, 'a')
        self.restore = self.envelope(self.file, self.absent, 'b')

    def state(self, record):
        return {'services': {}, 'files': {self.path: fingerprint(record)}, 'links': {}}

    def envelope(self, old, new, token):
        before, after = self.state(old), self.state(new)
        def verify(identifier, state):
            return {'id': identifier, 'before': state, 'after': state, 'repeatable': True,
                    'action': {'operation': 'verify', 'observe': self.scope}}
        return {'schema': 1, 'scope': self.scope, 'plan': dict(self.identity, schema=1, phases=[
            verify('preimage', before),
            {'id': 'file', 'before': before, 'after': after, 'repeatable': True,
             'action': {'operation': 'file', 'observe': self.scope, 'expected': old,
                        'desired': new, 'stage_token': token * 32}}, verify('result', after)])}

    def test_prepare_retains_both_directions_and_backup_privately_without_effects(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'transaction'
            result = prepare(directory, self.backup, self.apply, self.restore)
            self.assertEqual(result['status'], 'prepared')
            path = directory / 'transaction.json'
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual(json.loads(path.read_text())['backup'], self.backup)
            with self.assertRaises(FileExistsError):
                prepare(directory, self.backup, self.apply, self.restore)

    def test_mismatched_boot_refused(self):
        self.restore['plan']['boot_id'] = '00000000-0000-0000-0000-000000000000'
        with self.assertRaisesRegex(ValueError, 'same target and boot'):
            validate(self.backup, self.apply, self.restore)

    def test_restore_must_end_at_backup(self):
        with self.assertRaisesRegex(ValueError, 'return to backup'):
            validate(self.backup, self.apply, self.apply)

    def test_aggregate_preimage_required_before_any_effect(self):
        self.apply['plan']['phases'].pop(0)
        with self.assertRaisesRegex(ValueError, 'aggregate preimage'):
            validate(self.backup, self.apply, self.restore)

    def test_observation_cannot_substitute_for_backup_payload(self):
        self.backup['files'] = [self.file]
        with self.assertRaisesRegex(ValueError, 'retained payloads'):
            validate(self.backup, self.apply, self.restore)

    def test_input_mutation_does_not_change_validated_transaction(self):
        record = validate(self.backup, self.apply, self.restore)
        saved = copy.deepcopy(record)
        self.backup['files'].clear()
        self.apply['plan']['phases'].clear()
        self.assertEqual(record, saved)

    def authorized(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name) / 'transaction'
        prepared = prepare(directory, self.backup, self.apply, self.restore)
        authorization = dispatcher.authorize(directory, prepared['transaction_sha256'],
                                             host='target', ledger_parent='/var/tmp/private-ledger')
        return directory, authorization['authorization_sha256']

    def test_paired_dispatch_retains_direction_history_and_pinned_target(self):
        directory, approval = self.authorized()
        with patch.object(dispatcher, 'dispatch_envelope', return_value={'status': 'verified'}) as remote:
            dispatcher.dispatch(directory, 'apply', approval)
            dispatcher.dispatch(directory, 'restore', approval)
        self.assertEqual([call.args[1] for call in remote.call_args_list], [self.apply, self.restore])
        self.assertEqual(remote.call_args.args[0], 'target')
        self.assertEqual(remote.call_args.kwargs['ledger_parent'], '/var/tmp/private-ledger')
        events = [json.loads(path.read_text()) for path in sorted(directory.glob('event-*.json'))]
        self.assertEqual([event['state'] for event in events], ['dispatch', 'acknowledged'] * 2)
        self.assertNotEqual(events[0]['attempt'], events[2]['attempt'])

    def test_uncertain_apply_cannot_reverse_but_same_direction_can_reconcile(self):
        directory, approval = self.authorized()
        with patch.object(dispatcher, 'dispatch_envelope', side_effect=TimeoutError):
            with self.assertRaises(TargetUncertain):
                dispatcher.dispatch(directory, 'apply', approval)
        with patch.object(dispatcher, 'dispatch_envelope', return_value={'status': 'verified'}) as remote:
            with self.assertRaisesRegex(TargetUncertain, 'uncertain direction'):
                dispatcher.dispatch(directory, 'restore', approval)
            remote.assert_not_called()
            dispatcher.dispatch(directory, 'apply', approval)
            dispatcher.dispatch(directory, 'restore', approval)
        self.assertEqual(remote.call_count, 2)

    def test_initial_restore_and_reapply_after_restore_require_new_plan(self):
        directory, approval = self.authorized()
        with patch.object(dispatcher, 'dispatch_envelope', return_value={'status': 'verified'}) as remote:
            with self.assertRaises(ValueError):
                dispatcher.dispatch(directory, 'restore', approval)
            remote.assert_not_called()
            dispatcher.dispatch(directory, 'apply', approval)
            dispatcher.dispatch(directory, 'restore', approval)
            with self.assertRaisesRegex(ValueError, 'new transaction'):
                dispatcher.dispatch(directory, 'apply', approval)
            self.assertEqual(remote.call_count, 2)

    def test_wrong_approval_and_mutated_transaction_refused_before_remote(self):
        directory, approval = self.authorized()
        with patch.object(dispatcher, 'dispatch_envelope') as remote:
            with self.assertRaises(PermissionError):
                dispatcher.dispatch(directory, 'apply', '0' * 64)
            record = json.loads((directory / 'transaction.json').read_text())
            record['apply']['plan']['phases'][1]['action']['stage_token'] = 'c' * 32
            (directory / 'transaction.json').write_text(json.dumps(record))
            with self.assertRaises(PermissionError):
                dispatcher.dispatch(directory, 'apply', approval)
            remote.assert_not_called()
        self.assertEqual(list(directory.glob('event-*')), [])

    def test_transaction_lock_excludes_concurrent_dispatch(self):
        directory, approval = self.authorized()
        with dispatcher.locked(directory), patch.object(dispatcher, 'dispatch_envelope') as remote:
            with self.assertRaises(BlockingIOError):
                dispatcher.dispatch(directory, 'apply', approval)
            remote.assert_not_called()

    def test_authorization_cannot_be_rebound(self):
        directory, approval = self.authorized()
        binding = json.loads((directory / 'authorization.json').read_text())
        with self.assertRaises(FileExistsError):
            dispatcher.authorize(directory, binding['transaction_sha256'],
                                 host='other-target', ledger_parent='/var/tmp/other-ledger')
        self.assertEqual(dispatcher.digest(json.loads((directory / 'authorization.json').read_text())), approval)

    def test_orphan_acknowledgement_cannot_authorize_restore(self):
        directory, approval = self.authorized()
        with dispatcher.locked(directory) as fd:
            dispatcher.append_event(fd, {'state': 'acknowledged', 'direction': 'apply',
                'nonce': 'a' * 32, 'attempt': 'attempt-' + 'a' * 32, 'authorization_sha256': approval})
        with patch.object(dispatcher, 'dispatch_envelope') as remote:
            with self.assertRaisesRegex(TargetUncertain, 'no matching dispatch'):
                dispatcher.dispatch(directory, 'restore', approval)
            remote.assert_not_called()

    @linux_target
    def test_pair_runs_real_local_file_effects_and_restores_absence(self):
        from forge_target_service_runtime import execute
        self.identity = TargetEffects.identity()
        self.backup['identity'] = self.identity
        with tempfile.TemporaryDirectory() as root:
            self.path = str(Path(root) / 'fixture')
            self.absent['path'] = self.file['path'] = self.path
            self.file.update(uid=os.getuid(), gid=os.getgid())
            self.scope['files'] = [self.path]
            self.backup['state'] = self.state(self.absent)
            self.apply = self.envelope(self.absent, self.file, 'a')
            self.restore = self.envelope(self.file, self.absent, 'b')
            directory, approval = self.authorized()
            ledger = Path(root) / 'ledger'
            ledger.mkdir(mode=0o700)
            def local(host, plan, digest, attempt, **kwargs):
                return execute(plan, digest, ledger_parent=ledger, lock_path=Path(root) / 'lock')
            with patch.object(dispatcher, 'dispatch_envelope', side_effect=local):
                dispatcher.dispatch(directory, 'apply', approval)
                self.assertEqual(Path(self.path).read_bytes(), b'unit fixture')
                dispatcher.dispatch(directory, 'restore', approval)
                self.assertFalse(Path(self.path).exists())

    def test_provisioning_checks_pin_and_persists_identity_bound_authorization(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'transaction'
            prepared = prepare(directory, self.backup, self.apply, self.restore)
            def acknowledge(argv, **kwargs):
                self.assertTrue((directory / 'provision-intent.json').exists())
                request = json.loads(kwargs['input'])
                return subprocess.CompletedProcess(argv, 0, json.dumps(dict(request,
                    ledger_parent='/var/tmp/uconsole-forge-service-proof')), '')
            with patch.object(dispatcher.subprocess, 'run', side_effect=acknowledge) as remote:
                result = dispatcher.provision_and_authorize(directory, prepared['transaction_sha256'], host='target')
                self.assertFalse(result['deployment_performed'])
                binding = json.loads((directory / 'authorization.json').read_text())
                self.assertEqual(dispatcher.digest(binding), result['authorization_sha256'])
                with self.assertRaisesRegex(ValueError, 'already authorized'):
                    dispatcher.provision_and_authorize(directory, prepared['transaction_sha256'], host='target')
                self.assertEqual(remote.call_count, 1)

    def test_uncertain_provision_cannot_be_blindly_repeated(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'transaction'
            prepared = prepare(directory, self.backup, self.apply, self.restore)
            with patch.object(dispatcher.subprocess, 'run', side_effect=TimeoutError) as remote:
                with self.assertRaises(TargetUncertain):
                    dispatcher.provision_and_authorize(directory, prepared['transaction_sha256'], host='target')
                with self.assertRaises(FileExistsError):
                    dispatcher.provision_and_authorize(directory, prepared['transaction_sha256'], host='target')
                self.assertEqual(remote.call_count, 1)
            self.assertTrue((directory / 'provision-uncertain.json').exists())
            self.assertFalse((directory / 'authorization.json').exists())

    def test_provisioning_wrong_pin_never_connects(self):
        directory, approval = self.authorized()
        with patch.object(dispatcher.subprocess, 'run') as remote:
            with self.assertRaises(PermissionError):
                dispatcher.provision_and_authorize(directory, '0' * 64, host='target')
            remote.assert_not_called()
