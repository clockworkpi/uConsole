import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_controller import Controller
from forge_client import ClientSession
from uconsole_mcp import BY_NAME, validate


class TargetControllerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.policy = self.root / 'policy.json'
        self.definition = {'schema': 1, 'transactions': {'proof': {'workspace': 'main',
            'journal': str(self.root / 'transaction'), 'plan_sha256': 'a' * 64}}}
        self.policy.write_text(json.dumps(self.definition))
        self.digest = hashlib.sha256(self.policy.read_bytes()).hexdigest()

    def controller(self, grants=('target-write',)):
        owner = Controller({'main': self.root / 'workspace', 'other': self.root / 'other'}, grants,
                           target_policy=self.policy, target_policy_sha256=self.digest)
        self.addCleanup(owner.close)
        return owner

    def test_grant_requires_pinned_policy(self):
        with self.assertRaises(ValueError):
            Controller({'main': self.root}, ('target-write',))
        self.policy.write_text('{}')
        with self.assertRaises(ValueError):
            self.controller()

    def test_recovery_inspection_scoped_job_and_client_grant(self):
        owner = self.controller()
        args = {'workspace': 'main', 'transaction': 'proof'}
        validate(BY_NAME['target_recovery_inspect']['inputSchema'], args)
        with self.assertRaises(PermissionError):
            ClientSession(owner, ['main']).call('target_recovery_inspect', args)
        with patch('forge_target_recovery.binding', return_value=('fixture', 'a'*32)), \
                patch('forge_target_recovery.inspect', return_value={'recovery_qualified': False}) as inspect:
            client = ClientSession(owner, ['main'], ['target-write'])
            job = client.call('target_recovery_inspect', args)
            owner.jobs[job['job_id']][2].result(timeout=5)
            inspect.assert_called_once_with(owner.targets.get('proof', 'main'))
            self.assertFalse(owner.job(job['job_id'])['result']['recovery_qualified'])
        with self.assertRaises(ValueError):
            owner.submit_target_recovery('other', 'proof')

    def test_readonly_listing_does_not_allow_hardware_write(self):
        owner = self.controller(())
        listing = owner.call('target_transactions', {'workspace': 'main'})
        self.assertFalse(listing['execution_granted'])
        self.assertEqual(listing['transactions'][0]['name'], 'proof')
        with self.assertRaises(PermissionError):
            owner.submit_target('main', 'proof', 'apply')
        self.assertEqual(owner.jobs, {})

    def test_workspace_and_policy_snapshot(self):
        owner = self.controller()
        self.policy.write_text('{}')
        with self.assertRaises(ValueError):
            owner.submit_target('other', 'proof', 'apply')
        self.assertEqual(owner.targets.get('proof', 'main').plan_sha256, 'a' * 64)

    def test_dispatch_receives_owner_pin_and_job_context(self):
        owner = self.controller()
        with patch('forge_target_recovery.binding', return_value=('fixture', 'a'*32)), \
                patch('forge_target_ssh.dispatch', return_value={'files': []}) as dispatch:
            job = owner.call('target_transition', {'workspace': 'main', 'transaction': 'proof', 'direction': 'apply'})
            owner.jobs[job['job_id']][2].result(timeout=5)
        dispatch.assert_called_once_with(self.root / 'transaction', 'apply', approved_plan_sha256='a' * 64)
        result = owner.job(job['job_id'])
        self.assertEqual(result['context']['policy_sha256'], self.digest)
        self.assertEqual(result['context']['plan_sha256'], 'a' * 64)

    def test_attached_readonly_client_cannot_use_owner_hardware_grant(self):
        owner = self.controller()
        client = ClientSession(owner, ['main'])
        listing = client.call('target_transactions', {'workspace': 'main'})
        self.assertFalse(listing['execution_granted'])
        with self.assertRaises(PermissionError):
            client.call('target_transition', {'workspace': 'main', 'transaction': 'proof', 'direction': 'apply'})

    def test_mcp_rejects_host_and_journal_overrides(self):
        arguments = {'workspace': 'main', 'transaction': 'proof', 'direction': 'apply'}
        validate(BY_NAME['target_transition']['inputSchema'], arguments)
        for key in ('host', 'journal', 'plan_sha256', 'command'):
            with self.assertRaises(ValueError):
                validate(BY_NAME['target_transition']['inputSchema'], dict(arguments, **{key: 'override'}))

    def service_policy(self):
        self.definition['transactions']['service'] = {'kind': 'service', 'workspace': 'main',
            'journal': str(self.root / 'service-transaction'), 'authorization_sha256': 'b' * 64}
        self.policy.write_text(json.dumps(self.definition))
        self.digest = hashlib.sha256(self.policy.read_bytes()).hexdigest()

    def test_service_dispatch_uses_approved_binding_and_shared_job(self):
        self.service_policy()
        owner = self.controller()
        with patch('forge_target_recovery.binding', return_value=('fixture', 'a'*32)), \
                patch('forge_target_service_dispatch.dispatch', return_value={'status': 'verified'}) as dispatch:
            job = owner.call('target_transition', {'workspace': 'main', 'transaction': 'service', 'direction': 'apply'})
            owner.jobs[job['job_id']][2].result(timeout=5)
        dispatch.assert_called_once_with(self.root / 'service-transaction', 'apply', 'b' * 64)
        result = owner.job(job['job_id'])
        self.assertEqual(result['context']['authorization_sha256'], 'b' * 64)
        self.assertEqual(result['context']['kind'], 'service')
        listing = owner.call('target_transactions', {'workspace': 'main'})['transactions']
        self.assertEqual(listing[1], {'name': 'service', 'kind': 'service',
                                     'authorization_sha256': 'b' * 64, 'policy_sha256': self.digest})
        self.assertEqual(owner.targets.get('service', 'main').definition(), self.definition['transactions']['service'])

    def test_service_policy_cannot_mix_pins_or_escape_client_grant(self):
        self.service_policy()
        owner = self.controller()
        client = ClientSession(owner, ['main'])
        with self.assertRaises(PermissionError):
            client.call('target_transition', {'workspace': 'main', 'transaction': 'service', 'direction': 'restore'})
        self.definition['transactions']['service']['plan_sha256'] = 'a' * 64
        self.policy.write_text(json.dumps(self.definition))
        self.digest = hashlib.sha256(self.policy.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            self.controller()
