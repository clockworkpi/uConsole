"""Real source/journal checks with physical transport boundaries mocked."""
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from forge_recovery_deploy_plan import INPUT_NAMES, prepare
from forge_recovery_restore_source import digest
from forge_recovery_source_contract import completion, DERIVATIVE
from forge_recovery_restore_reconcile_host import original_attempt
import forge_recovery_restore_dispatch as dispatch
import test_deploy_executor


class DeployDispatchTests(unittest.TestCase):
    def setUp(self):
        fixture = test_deploy_executor.DeployExecutorTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        owner = fixture.owner
        self.root = owner.fixture.source.root
        self.directory = self.root/'deploy-journal'
        self.pin = prepare(self.directory, *(owner.inputs[name] for name in INPUT_NAMES))['plan_sha256']
        self.plan, self.inputs = owner.plan, owner.inputs
        binding = self.plan['binding']
        self.probe = SimpleNamespace(**{key:binding[key] for key in ('nonce','kernel','serial','mode')},
            inspect_storage=Mock(return_value=dict(extent=binding['extent'])), inspect=Mock())
        self.lease = SimpleNamespace(probe=self.probe, boot_id=binding['boot_id'], owner=binding['lease_owner'],
                                     renew=Mock(return_value={}))
        self.health = self.inputs['health']
        self.health_pin = self.inputs['health_pin']
        self.health_dir = self.root/'health'
        self.health_dir.mkdir(mode=0o700)
        save = owner.fixture.source.save
        save(self.health_dir/'acceptance.json', self.health)
        save(self.health_dir/'source-stream.json', completion(fixture.manifest, fixture.source_pin,
                                                             expected_kind=DERIVATIVE))
        command = ['/fixture/e2fsck', '-f', '-n']
        save(self.health_dir/'plan.json', dict(derivative_manifest_sha256=fixture.source_pin,
            source=str(fixture.directory), image=fixture.manifest['card'], root=fixture.manifest['root'], command=command))
        save(self.health_dir/'root-check.json', dict(argv=command, returncode=0, output='fixture clean evidence'))
        self.patches = {}
        for name, value in (('capture_selection', Mock()), ('argv', Mock(return_value=['fixture-worker'])),
                ('start', Mock()), ('exchange', Mock(side_effect=self.exchange)),
                ('run_process', Mock(side_effect=lambda argv,op,errors:op(None,lambda:None,lambda:None)))):
            patched = patch.object(dispatch, name, value)
            self.patches[name] = patched.start()
            self.addCleanup(patched.stop)

    def approve(self, plan, pin, phase, health):
        self.assertEqual(health, self.health)
        return dict(deploy=pin, boot_id=plan['binding']['boot_id'], phase=phase,
            source_health_sha256=self.health_pin, rollback_manifest_sha256=plan['rollback_manifest_sha256'])

    def exchange(self, channel, plan, pin, manifest, attempt, produce, *, authorize, renew, record, **_):
        for phase in ('acquire', 'inspect', 'write'):
            self.assertEqual(authorize(plan, pin, phase),
                dict(restore=pin, boot_id=plan['binding']['boot_id'], phase=phase))
            renew()
        chunks = []
        verified = produce(lambda chunk,data:chunks.append(data))
        self.assertEqual(b''.join(chunks)[:8], b'enhanced')
        self.assertEqual(verified['status'], 'verified-derivative-stream')
        record('source-verified', verified)
        return dict(status='root-restore-verified',plan_sha256=pin,
            source_manifest_sha256=plan['source_manifest_sha256'],boot_id=plan['binding']['boot_id'],
            root=plan['root_after'],prefix=plan['prefix_guard'],suffix=plan['suffix_guard'],
            bytes_written=4*1024*1024,protected_ranges_verified=True,boot_unmounted=True,
            normal_boot_release_authorized=False,physical_restore_qualified=False)

    def run_dispatch(self, **kwargs):
        return dispatch.deploy(self.probe, self.directory, self.pin, self.lease, self.fixture.directory,
            self.health_dir, kwargs.get('health_pin', self.health_pin), authorize=kwargs.get('authorize',self.approve))

    def test_deployment_retains_distinct_approval_and_reconciliation_lineage(self):
        result = self.run_dispatch()
        self.assertEqual(result['status'], 'acknowledged')
        self.assertFalse(result['normal_boot_release_authorized'])
        events = [json.loads(line) for line in (self.directory/'restore-attempt/events.jsonl').read_text().splitlines()]
        approvals = [item['value'] for item in events if item['kind']=='source-health-approval']
        self.assertEqual(len(approvals), 3)
        self.assertTrue(all(value['deploy']==self.pin and 'restore' not in value for value in approvals))
        original = original_attempt(self.directory, self.plan, self.pin, self.inputs)
        self.assertEqual(original['source_health_sha256'], self.health_pin)

    def test_existing_attempt_never_reverifies_or_contacts_target(self):
        self.run_dispatch()
        self.patches['capture_selection'].reset_mock()
        self.lease.renew.reset_mock()
        with self.assertRaises(FileExistsError):
            self.run_dispatch()
        self.patches['capture_selection'].assert_not_called()
        self.lease.renew.assert_not_called()

    def test_changed_source_refused_before_target_contact_or_intent(self):
        image = self.root/'enhanced.img'
        info = image.stat()
        os.utime(image, ns=(info.st_atime_ns, info.st_mtime_ns+1000000))
        with self.assertRaises(ValueError):
            self.run_dispatch()
        self.patches['capture_selection'].assert_not_called()
        self.assertFalse((self.directory/'restore-attempt').exists())

    def test_health_pin_must_equal_approved_plan_pin(self):
        with self.assertRaisesRegex(ValueError, 'approved deployment plan'):
            self.run_dispatch(health_pin='0'*64)
        self.patches['capture_selection'].assert_not_called()

    def test_backup_approval_cannot_authorize_deployment(self):
        def wrong(plan,pin,phase,health):
            value = self.approve(plan,pin,phase,health)
            value['restore'] = value.pop('deploy')
            return value
        with self.assertRaisesRegex(ValueError, 'source-health approval'):
            self.run_dispatch(authorize=wrong)
        result = json.loads((self.directory/'restore-attempt/acceptance.json').read_text())
        self.assertEqual(result['status'], 'uncertain')

    def test_transport_loss_retains_uncertainty_and_blocks_retry(self):
        self.patches['run_process'].side_effect = EOFError('connection lost')
        with self.assertRaises(EOFError):
            self.run_dispatch()
        result = json.loads((self.directory/'restore-attempt/acceptance.json').read_text())
        self.assertEqual(result['root_written'], 'unknown')
        with self.assertRaises(FileExistsError):
            self.run_dispatch()

    def test_backup_entrypoint_rejects_deployment_journal(self):
        with self.assertRaises(ValueError):
            dispatch.dispatch(self.probe,self.directory,self.pin,self.lease,self.fixture.directory,
                              self.health_dir,self.health_pin,authorize=self.approve)
        self.assertFalse((self.directory/'restore-attempt').exists())

    def test_preflight_lease_failure_never_creates_write_intent(self):
        self.lease.renew.side_effect = RuntimeError('lease failed')
        with self.assertRaisesRegex(RuntimeError, 'lease failed'):
            self.run_dispatch()
        self.patches['run_process'].assert_not_called()
        self.assertFalse((self.directory/'restore-attempt').exists())

    def test_changed_rollback_archive_prevents_deployment(self):
        archive = self.fixture.owner.fixture.source.source/'card.img.gz'
        info = archive.stat()
        os.utime(archive, ns=(info.st_atime_ns, info.st_mtime_ns+1000000))
        with self.assertRaises(ValueError):
            self.run_dispatch()
        self.patches['run_process'].assert_not_called()
        self.assertFalse((self.directory/'restore-attempt').exists())
