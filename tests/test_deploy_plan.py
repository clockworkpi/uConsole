import copy
import json
import os
import unittest

from forge_recovery_deploy_plan import compile_plan, prepare as prepare_plan, load as load_plan
from forge_recovery_derivative import prepare, load
from forge_recovery_restore_ledger import request
from forge_recovery_restore_source import digest
import test_recovery_restore_plan


class DeployPlanTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_recovery_restore_plan.RestorePlanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        source = self.fixture.source
        image = source.root/'derived.img'
        image.write_bytes(source.data)
        image.chmod(0o600)
        with image.open('r+b') as output:
            output.seek(source.expected['offset'])
            output.write(b'enhanced')
        result = prepare(source.source, self.fixture.manifest, self.fixture.source_pin,
                         image, source.root/'derivative')
        self.pin = result['manifest_sha256']
        self.derivative, _ = load(source.root/'derivative', self.pin)
        self.fixture.hashes['digests']['root'] = copy.deepcopy(source.expected)
        self.health = dict(status='checked-derivative-root', derivative_manifest_sha256=self.pin,
            image=self.derivative['card'], root=self.derivative['root'],
            root_filesystem_consistency_qualified=True, boot_filesystem_checked=False,
            repair_performed=False, target_written=False, target_write_authorized=False,
            normal_boot_release_authorized=False, root_check_returncode=0)

    def arguments(self):
        values = list(self.fixture.arguments())
        values[2:4] = [self.derivative, self.pin]
        return (*values, self.health, digest(self.health))

    def compile(self):
        return compile_plan(*self.arguments())

    def test_separate_deployment_keeps_original_rollback_and_no_authority(self):
        plan = self.compile()
        self.assertEqual(plan['root_after'], self.derivative['root'])
        self.assertEqual(plan['rollback_root'], self.fixture.manifest['root'])
        self.assertEqual(plan['rollback_manifest_sha256'], self.fixture.source_pin)
        self.assertEqual(plan['source_manifest_sha256'], self.pin)
        self.assertEqual(plan['kind'], 'guarded-derived-root-deploy-plan')
        for name in ('root_write_authorized', 'boot_write_authorized', 'whole_card_write_authorized',
                     'normal_boot_release_authorized', 'native_boot_qualified', 'filesystem_consistency_qualified'):
            self.assertIs(plan[name], False)
        # The same boot-wide fence names this distinct plan without granting
        # authority. The executor separately recompiles all retained evidence.
        self.assertEqual(request(plan, digest(plan), 'a'*32)['plan_sha256'], digest(plan))
        self.derivative['root']['sha256'] = '0'*64
        self.assertNotEqual(plan['root_after']['sha256'], '0'*64)

    def test_nonclean_derivative_is_not_deployable(self):
        self.health.update(root_check_returncode=4, root_filesystem_consistency_qualified=False)
        with self.assertRaisesRegex(ValueError, 'clean derivative'):
            self.compile()

    def test_changed_current_root_requires_reconciliation(self):
        self.fixture.hashes['digests']['root']['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'reconcile'):
            self.compile()

    def test_changed_protected_suffix_is_rejected(self):
        self.fixture.hashes['digests']['suffix']['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'protected suffix'):
            self.compile()

    def test_existing_physical_boot_and_hold_invariants_still_apply(self):
        self.fixture.boot['bootloader']['tryboot'] = 1
        with self.assertRaises(ValueError):
            self.compile()

    def test_durable_private_plan_roundtrip_and_exclusive_destination(self):
        directory = self.fixture.source.root/'deploy'
        receipt = prepare_plan(directory, *self.arguments())
        self.assertEqual(load_plan(directory, receipt['plan_sha256']), self.compile())
        self.assertFalse(receipt['root_write_authorized'])
        self.assertFalse(receipt['normal_boot_release_authorized'])
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for name in ('inputs.json', 'plan.json'):
            self.assertEqual((directory/name).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            prepare_plan(directory, *self.arguments())

    def test_changed_plan_or_retained_evidence_is_rejected(self):
        directory = self.fixture.source.root/'deploy'
        receipt = prepare_plan(directory, *self.arguments())
        with self.assertRaisesRegex(ValueError, 'owner plan pin'):
            load_plan(directory, '0'*64)
        path = directory/'inputs.json'
        original = json.loads(path.read_text())
        for kind in ('extra', 'health', 'boot'):
            changed = copy.deepcopy(original)
            if kind == 'extra':
                changed['unexpected'] = True
            elif kind == 'health':
                changed['health']['root_check_returncode'] = 4
            else:
                changed['ram_boot_observation']['bootloader']['tryboot'] = 1
            path.write_text(json.dumps(changed))
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                load_plan(directory, receipt['plan_sha256'])
        path.write_text(json.dumps(original))
        changed = self.compile()
        changed['root_write_authorized'] = True
        (directory/'plan.json').write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, 'retained evidence'):
            load_plan(directory, digest(changed))

    def test_linked_journal_input_is_rejected(self):
        directory = self.fixture.source.root/'deploy'
        receipt = prepare_plan(directory, *self.arguments())
        os.link(directory/'inputs.json', directory/'linked.json')
        with self.assertRaises(PermissionError):
            load_plan(directory, receipt['plan_sha256'])
