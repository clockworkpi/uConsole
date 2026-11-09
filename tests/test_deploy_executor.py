"""Real derivative writes to disposable files; physical boundaries are mocked."""
import copy
import io
import json
import unittest

from forge_recovery_deploy_plan import INPUT_NAMES, compile_plan
from forge_recovery_derivative import prepare, load, stream
from forge_recovery_operation_contract import check_evidence
from forge_recovery_restore_bootstrap import payload
from forge_recovery_restore_protocol import Sender
from forge_recovery_restore_source import digest
from forge_recovery_source_contract import DERIVATIVE
import test_recovery_restore_executor
import test_recovery_restore_exchange
from forge_recovery_restore_observe_worker import checked as check_observation


class DeployExecutorTests(unittest.TestCase):
    def setUp(self):
        self.owner = test_recovery_restore_executor.RestoreExecutorTests()
        self.owner.setUp()
        self.addCleanup(self.owner.doCleanups)
        fixture = self.owner.fixture
        source = fixture.source
        self.owner.before = source.root_bytes
        self.owner.path.write_bytes(source.root_bytes)
        fixture.hashes['digests']['root'] = copy.deepcopy(source.expected)
        image = source.root/'enhanced.img'
        image.write_bytes(source.data)
        image.chmod(0o600)
        with image.open('r+b') as output:
            output.seek(512)
            output.write(b'enhanced')
        self.directory = source.root/'derivative'
        accepted = prepare(source.source, fixture.manifest, fixture.source_pin, image, self.directory)
        self.source_pin = accepted['manifest_sha256']
        self.manifest, _ = load(self.directory, self.source_pin)
        # Compiler evidence fixture, not a claim that synthetic bytes are ext4.
        health = dict(status='checked-derivative-root', derivative_manifest_sha256=self.source_pin,
            image=self.manifest['card'], root=self.manifest['root'], root_filesystem_consistency_qualified=True,
            root_check_returncode=0, boot_filesystem_checked=False, repair_performed=False,
            target_written=False, target_write_authorized=False, normal_boot_release_authorized=False)
        args = list(fixture.arguments())
        args[2:4] = [self.manifest, self.source_pin]
        args += [health, digest(health)]
        self.owner.plan = compile_plan(*args)
        self.owner.pin = digest(self.owner.plan)
        self.owner.inputs = dict(zip(INPUT_NAMES, args))

    def wire(self):
        owner = self.owner
        output = io.BytesIO()
        sender = Sender(output, self.manifest, self.source_pin,
            dict(plan_sha256=owner.pin, manifest_sha256=self.source_pin, attempt=owner.attempt,
                 boot_id=owner.boot), expected_kind=DERIVATIVE)
        sender.finish(stream(self.directory, self.source_pin, sender.chunk))
        return output.getvalue()

    def test_owner_approved_derivative_uses_full_guarded_executor(self):
        result = self.owner.run_restore(wire=self.wire())
        self.assertEqual(result['root'], self.manifest['root'])
        self.assertEqual(result['bytes_written'], 4*1024*1024)
        self.assertTrue(result['protected_ranges_verified'])
        self.assertFalse(result['normal_boot_release_authorized'])
        self.assertFalse(result['physical_restore_qualified'])
        self.assertIn('protected-verified', self.owner.events)
        self.assertEqual(self.owner.events[-1], 'unlocked')
        self.assertEqual(self.owner.path.read_bytes()[:8], b'enhanced')

    def test_complete_derivative_bootstrap_retains_health_and_rollback(self):
        owner = self.owner
        raw, _ = payload(owner.plan, owner.pin, owner.inputs, owner.attempt)
        packet = json.loads(raw)
        self.assertEqual(packet['request']['inputs'], owner.inputs)
        self.assertIn('forge_recovery_operation_contract', packet['modules'])
        self.assertIn('forge_recovery_source_contract', packet['modules'])
        self.assertEqual(check_evidence(owner.plan, owner.pin, owner.inputs), (DERIVATIVE, self.manifest))

    def test_changed_health_and_operation_fail_before_any_claim(self):
        owner = self.owner
        original = copy.deepcopy(owner.inputs)
        owner.inputs['health']['root_filesystem_consistency_qualified'] = False
        with self.assertRaises(ValueError):
            owner.run_restore(wire=self.wire())
        self.assertFalse(owner.events)
        owner.inputs = original
        owner.plan['operation'] = 'restore-backup-root'
        owner.pin = digest(owner.plan)
        with self.assertRaises(ValueError):
            owner.run_restore(wire=b'')
        self.assertFalse(owner.events)
        self.assertEqual(owner.path.read_bytes(), owner.before)

    def test_interrupted_derivative_shares_boot_wide_fence(self):
        wire = self.wire()
        boundary = wire.index(b'\n')+1+self.manifest['chunks'][0]['bytes']
        with self.assertRaises(ValueError):
            self.owner.run_restore(wire=wire[:boundary])
        self.assertEqual(self.owner.path.read_bytes()[:8], b'enhanced')
        self.owner.attempt = '8'*32
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            self.owner.run_restore(wire=self.wire())

    def test_derivative_real_duplex_exchange_with_renewals_and_half_close(self):
        peer = test_recovery_restore_exchange.RestoreExchangeTests()
        peer.setUp()
        self.addCleanup(peer.doCleanups)
        peer.plan, peer.pin = self.owner.plan, self.owner.pin
        peer.fixture.manifest, peer.fixture.source_pin = self.manifest, self.source_pin
        peer.root.write_bytes(self.owner.before)
        peer.produce = lambda consume: stream(self.directory, self.source_pin, consume)
        result = peer.run_exchange()
        self.assertEqual(result['bytes_written'], 4*1024*1024)
        self.assertEqual(peer.root.read_bytes()[:8], b'enhanced')
        self.assertGreater(peer.sequence, 5)
        self.assertEqual(peer.events[-1][0], 'exchange-complete')
        receipt = next(value for kind, value in peer.events if kind == 'source-verified')
        self.assertEqual(receipt['status'], 'verified-derivative-stream')

    def test_derivative_observation_recompiles_the_same_pinned_evidence(self):
        owner = self.owner
        request = dict(protocol=1, plan=owner.plan, pin=owner.pin, inputs=owner.inputs,
            attempt=owner.attempt, query='f'*32, action='fence', observed_boot_id=owner.boot,
            owner=owner.plan['binding']['lease_owner'], accepted=None)
        self.assertEqual(check_observation(request), request)
        request['inputs'] = dict(owner.inputs, source_manifest=self.manifest)
        with self.assertRaises(ValueError):
            check_observation(request)
