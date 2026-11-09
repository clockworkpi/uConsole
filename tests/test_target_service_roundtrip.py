import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_target_phases import canonical
from forge_target_service_runtime import validate
from validate_target_service_roundtrip import envelope, run
if __package__:
    from .target_test_support import identity
else:
    from target_test_support import identity


class ServiceRoundtripTests(unittest.TestCase):
    def test_lifecycle_start_is_nonrepeatable_and_final_scope_is_complete(self):
        scope = {'services': ['proof.service'], 'files': [], 'links': []}
        before = {'services': {'proof.service': {'ActiveState': 'inactive'}}, 'files': {}, 'links': {}}
        after = {'services': {'proof.service': {'ActiveState': 'active'}}, 'files': {}, 'links': {}}
        plan = envelope(identity(), scope, before, after, 'start', service='proof.service')
        validate(plan)
        self.assertFalse(plan['plan']['phases'][0]['repeatable'])
        self.assertEqual(plan['plan']['phases'][-1]['action']['observe'], scope)

    def test_invalid_host_cannot_create_evidence_or_connect(self):
        with tempfile.TemporaryDirectory() as root, patch('validate_target_service_roundtrip.rpc') as rpc:
            output = Path(root) / 'attempt'
            with self.assertRaises(ValueError):
                run('-oProxyCommand=bad', output)
            rpc.assert_not_called()
            self.assertFalse(output.exists())

    def test_existing_evidence_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as root, patch('validate_target_service_roundtrip.rpc') as rpc:
            with self.assertRaises(FileExistsError):
                run('target', root)
            rpc.assert_not_called()

    def test_existing_unit_refused_without_any_provisioning_or_effect(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'attempt'
            def probe(host, source, request):
                unit = request['scope']['services'][0]
                return {'nonce': request['nonce'], 'identity': identity(), 'state': {
                    'services': {unit: {'LoadState': 'loaded', 'ActiveState': 'inactive'}},
                    'files': {}, 'links': {}}}
            with patch('validate_target_service_roundtrip.rpc', side_effect=probe) as rpc, \
                    patch('validate_target_service_roundtrip.dispatch') as dispatch:
                with self.assertRaisesRegex(ValueError, 'absent unit'):
                    run('target', output)
                self.assertEqual(rpc.call_count, 1)
                dispatch.assert_not_called()
            self.assertTrue((output / 'incomplete.json').exists())
            self.assertFalse((output / 'provision-intent.json').exists())

    def test_envelope_digest_binds_effect_and_identity(self):
        scope = {'services': [], 'files': [], 'links': []}
        state = {'services': {}, 'files': {}, 'links': {}}
        plan = envelope(identity(), scope, state, state, 'daemon-reload')
        initial = hashlib.sha256(canonical(plan).encode()).hexdigest()
        plan['plan']['machine_id'] = '0' * 32
        self.assertNotEqual(initial, hashlib.sha256(canonical(plan).encode()).hexdigest())
