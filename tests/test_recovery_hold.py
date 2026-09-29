import copy
import unittest

from forge_recovery_hold import compile_hold
from forge_trial_firmware import compile_recovery_recipe
from forge_tryboot_recipe import CONFIG, TRYBOOT
from forge_target_journal import validate_plan
import test_trial_firmware
import test_tryboot_recipe


class RecoveryHoldTests(unittest.TestCase):
    def setUp(self):
        fixture = test_trial_firmware.TrialFirmwareTests()
        fixture.setUp()
        self.original = fixture.backup
        self.bundle = fixture.bundle
        self.nonce = fixture.nonce
        self.image = test_tryboot_recipe.TrybootRecipeTests().image_plan()
        for record in self.original['files']:
            if record['kind'] == 'file':
                record['mode'] = 0o700
        recipe = compile_recovery_recipe(self.original, self.nonce, self.bundle, self.image)
        template = self.original['files'][0]
        outputs = {record['path']: dict(template, **record) for record in recipe['files']}
        self.staged = copy.deepcopy(self.original)
        self.staged['files'] = [outputs.get(record['path'], record) for record in self.staged['files']]

    def compile(self):
        return compile_hold(self.original, self.staged, self.nonce, self.bundle, self.image)

    def test_changes_only_normal_selector_and_preserves_all_dependencies(self):
        original, staged = copy.deepcopy(self.original), copy.deepcopy(self.staged)
        result = self.compile()
        before = {r['path']: r for r in result['before']['files']}
        after = {r['path']: r for r in result['after']['files']}
        self.assertEqual([path for path in before if before[path] != after[path]], [CONFIG])
        for key in ('data', 'size', 'sha256'):
            self.assertEqual(after[CONFIG][key], before[TRYBOOT][key])
        self.assertEqual(after[CONFIG]['mode'], 0o700)
        self.assertIn(self.image['destination'], result['protected_paths'])
        self.assertEqual(self.original, original)
        self.assertEqual(self.staged, staged)

    def test_no_generic_dispatch_release_or_whole_card_authority(self):
        result = self.compile()
        for key in ('deployment_authorized', 'normal_boot_release_authorized', 'whole_card_write_authorized'):
            self.assertFalse(result[key])
        with self.assertRaises(ValueError):
            validate_plan(result)
        self.assertIn('verified-persistent-recovery-reboot', result['required_gates'])
        self.assertIn('verified-complete-root-restoration', result['release_requires'])

    def test_missing_or_changed_dependency_rejected(self):
        for index in range(len(self.staged['files'])):
            before = copy.deepcopy(self.staged)
            record = self.staged['files'][index]
            if record['kind'] == 'file':
                record['mtime_ns'] += 1
            else:
                record.update(copy.deepcopy(self.staged['files'][0]), path=record['path'])
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.compile()
            self.staged = before

    def test_wrong_target_and_public_policy_rejected(self):
        self.staged['machine_id'] = 'f'*32
        with self.assertRaises(ValueError):
            self.compile()
        self.staged['machine_id'] = self.original['machine_id']
        self.original['files'][0]['mode'] = 0o755
        with self.assertRaises(ValueError):
            self.compile()

    def test_leased_hold_preserves_exact_owner_and_inherits_all_recipe_gates(self):
        owner = 'c'*64
        recipe = compile_recovery_recipe(self.original, self.nonce, self.bundle, self.image,
                                         lease_owner=owner)
        template = self.original['files'][0]
        outputs = {record['path']: dict(template, **record) for record in recipe['files']}
        staged = copy.deepcopy(self.original)
        staged['files'] = [outputs.get(record['path'], record) for record in staged['files']]
        result = compile_hold(self.original, staged, self.nonce, self.bundle, self.image,
                              lease_owner=owner)
        self.assertEqual(result['lease'], recipe['lease'])
        self.assertTrue(set(recipe['required_gates']).issubset(result['required_gates']))
        self.assertFalse(result['root_write_authorized'])
        self.assertFalse(result['lease']['persistent_hold_authorized'])
        self.assertFalse(result['deployment_authorized'])
        for wrong_owner in (None, 'd'*64, 'bad'):
            with self.subTest(owner=wrong_owner), self.assertRaises(ValueError):
                compile_hold(self.original, staged, self.nonce, self.bundle, self.image,
                             lease_owner=wrong_owner)
