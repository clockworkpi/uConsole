import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_target_phases import canonical
from forge_target_service_ssh import BOOTSTRAP, dispatch, MODULES
from forge_target_ssh import TargetUncertain


class ServiceSSHTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / 'attempt'
        scope = {'services': [], 'files': [], 'links': []}
        state = {'services': {}, 'files': {}, 'links': {}}
        self.envelope = {'schema': 1, 'scope': scope, 'plan': {
            'schema': 1, 'machine_id': 'a' * 32,
            'boot_id': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
            'phases': [{'id': 'verify', 'before': state, 'after': state,
                        'repeatable': True, 'action': {'operation': 'verify', 'observe': scope}}]}}
        self.digest = hashlib.sha256(canonical(self.envelope).encode()).hexdigest()

    def dispatch(self, **kwargs):
        return dispatch('jkh@clockworkpi.local', self.envelope, self.digest,
                        self.directory, ledger_parent='/var/tmp/private-ledger', **kwargs)

    def acknowledge(self, argv, **kwargs):
        request = json.loads(kwargs['input'])
        saved = json.loads((self.directory / 'dispatch.json').read_text())
        self.assertEqual(saved['envelope'], request['envelope'])
        self.assertEqual(saved['nonce'], request['nonce'])
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.directory / 'dispatch.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual([name for name, _ in request['modules']], list(MODULES))
        self.assertNotIn(self.digest, ' '.join(argv))
        return subprocess.CompletedProcess(argv, 0, json.dumps({'nonce': request['nonce'], 'result': {
            'status': 'verified', 'phases': 1, 'ledger': '/var/tmp/private-ledger/' + self.digest,
            'envelope_sha256': self.digest}}), '')

    def test_durable_request_precedes_ssh_and_ack_is_retained(self):
        with patch('forge_target_service_ssh.subprocess.run', side_effect=self.acknowledge):
            self.assertEqual(self.dispatch()['status'], 'verified')
        self.assertTrue((self.directory / 'acknowledged.json').exists())

    def test_wrong_pin_refused_before_any_dispatch_record(self):
        self.digest = '0' * 64
        with patch('forge_target_service_ssh.subprocess.run') as run:
            with self.assertRaises(PermissionError):
                self.dispatch()
            run.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_timeout_and_forged_ack_are_uncertain_never_retried(self):
        for effect in (subprocess.TimeoutExpired('ssh', 1),
                       subprocess.CompletedProcess('ssh', 0, '{}', '')):
            self.directory = self.directory.parent / ('attempt-' + str(len(list(self.directory.parent.iterdir()))))
            with patch('forge_target_service_ssh.subprocess.run') as run:
                if isinstance(effect, Exception):
                    run.side_effect = effect
                else:
                    run.return_value = effect
                with self.assertRaises(TargetUncertain):
                    self.dispatch()
                self.assertEqual(run.call_count, 1)
            self.assertTrue((self.directory / 'uncertain.json').exists())
            self.assertFalse((self.directory / 'acknowledged.json').exists())

    def test_existing_attempt_not_overwritten(self):
        self.directory.mkdir()
        with patch('forge_target_service_ssh.subprocess.run') as run:
            with self.assertRaises(FileExistsError):
                self.dispatch()
            run.assert_not_called()

    def test_invalid_timeout_precedes_dispatch(self):
        with self.assertRaises(ValueError):
            self.dispatch(timeout=True)
        self.assertFalse(self.directory.exists())

    def test_remote_module_bundle_loads_without_installed_repository(self):
        root = Path(__file__).resolve().parents[1] / 'tools'
        request = {'modules': [(name, (root / (name + '.py')).read_text()) for name in MODULES]}
        loader = BOOTSTRAP.split('from forge_target_service_runtime import execute')[0]
        result = subprocess.run([sys.executable, '-I', '-c', loader], input=json.dumps(request),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
