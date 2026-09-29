import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from forge_recovery_journal import acknowledgement, dispatch, prepare as prepare_publication
from forge_recovery_stage_prepare import inputs, normal_boot, prepare
from forge_recovery_stage_review import load
from forge_recovery_bootplan import digest
from forge_target_journal import append_event, private_directory, write_record
import test_trial_firmware
import test_tryboot_recipe


class RecoveryStagePreparationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.output = self.root/'output'
        firmware = test_trial_firmware.TrialFirmwareTests()
        firmware.setUp()
        self.backup = firmware.backup
        for record in self.backup['files']:
            if record['kind'] == 'file': record['mode'] = 0o700
        self.plan = test_tryboot_recipe.TrybootRecipeTests().image_plan()
        self.publication = self.root/'publication'
        pin = prepare_publication(self.publication, self.plan)['plan_sha256']
        dispatch(self.publication, 'apply', pin, lambda plan, direction, nonce, digest:
                 acknowledgement(plan, direction, nonce, digest))
        fd = private_directory(self.root)
        try: bundle_pin = write_record(fd, 'firmware.json', firmware.bundle)
        finally: os.close(fd)
        self.frozen = inputs(self.publication, pin, self.root/'firmware.json', bundle_pin)
        self.boot = dict(machine_id=self.plan['machine_id'], boot_id='11111111-2222-3333-4444-555555555555',
                         cmdline='root=PARTUUID=1234-02 rootwait rw', tryboot=0, partition=1)
        self.captures = 0
        self.change_preimage = self.wrong_machine = False
        self.bad_image = False
        for name, effect in (('capture_boot', lambda host: copy.deepcopy(self.boot)),
                             ('capture_files', self.capture), ('transport', self.inspect)):
            patched = patch('forge_recovery_stage_prepare.'+name, side_effect=effect)
            patched.start()
            self.addCleanup(patched.stop)

    def capture(self, host, paths, output):
        self.captures += 1
        self.assertEqual(host, self.plan['host'])
        before = copy.deepcopy(self.backup)
        for record, path in zip(before['files'], paths): record['path'] = path
        before['ssh_host'] = host
        if self.wrong_machine: before['machine_id'] = 'f'*32
        if self.change_preimage and self.captures == 2: before['files'][0]['mtime_ns'] += 1
        fd = private_directory(Path(output).parent)
        try: write_record(fd, Path(output).name, before)
        finally: os.close(fd)

    def inspect(self, plan, direction, nonce, digest):
        self.assertEqual(direction, 'inspect')
        return dict(kind='inspection', nonce=nonce, plan_sha256=digest, machine_id=plan['machine_id'],
                    mutation_performed=False, retry_authorized=False, boot_id=self.boot['boot_id'],
                    policy={'valid': not self.bad_image},
                    files=dict(destination={'state': 'matching-bytes'}, scratch={'state': 'absent'}))

    def acceptance(self):
        return json.loads((self.output/'acceptance.json').read_text())

    def test_preparation_retains_backup_and_four_ordered_drafts_without_authority(self):
        result = prepare(self.output, self.frozen)
        self.assertEqual(result, self.acceptance())
        self.assertEqual(result['status'], 'prepared-not-approved')
        self.assertEqual(result['apply_order'], ['firmware-start', 'firmware-fixup', 'command', 'selector'])
        self.assertEqual(result['restore_order'], list(reversed(result['apply_order'])))
        self.assertEqual(len(result['staging_plan_pins']), 4)
        self.assertEqual(self.captures, 2)
        for key in ('target_written', 'staging_performed', 'reboot_performed', 'deployment_authorized', 'recovery_qualified'):
            self.assertIs(result[key], False)
        self.assertTrue(result['whole_card_backup_required'])
        self.assertNotIn('lease_owner', result)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.output/'before-capture.json').stat().st_mode & 0o777, 0o600)
        self.assertTrue((self.output/'hold-review.json').exists())
        with self.assertRaises(FileExistsError): prepare(self.output, self.frozen)
        self.assertEqual(self.captures, 2)

    def test_local_pins_fail_before_remote_access(self):
        with patch('forge_recovery_stage_prepare.capture_boot') as capture:
            with self.assertRaises(ValueError):
                inputs(self.publication, '0'*64, self.root/'firmware.json', self.frozen['firmware_sha256'])
            changed = copy.deepcopy(self.frozen)
            changed['firmware_bundle']['revision'] = 'f'*40
            with self.assertRaises(ValueError): prepare(self.output, changed)
            capture.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_sealed_review_recompiles_offline_without_granting_authority(self):
        result = prepare(self.output, self.frozen)
        with patch('forge_recovery_stage_prepare.capture_boot') as boot, \
                patch('forge_recovery_stage_prepare.transport') as remote:
            reviewed = load(self.output, digest(result))
            boot.assert_not_called()
            remote.assert_not_called()
        self.assertEqual(reviewed['acceptance'], result)
        self.assertEqual(len(reviewed['plans']), 4)
        self.assertFalse(reviewed['acceptance']['deployment_authorized'])

    def rewrite(self, name, value):
        # Deliberate local mutation of a test fixture, retaining private mode.
        (self.output/name).write_text(json.dumps(value))

    def test_seal_rejects_every_changed_record(self):
        result = prepare(self.output, self.frozen)
        for name in result['record_pins']:
            with self.subTest(name=name):
                original = json.loads((self.output/name).read_text())
                self.rewrite(name, dict(original, unexpected=True))
                with self.assertRaisesRegex(ValueError, 'digest'):
                    load(self.output, digest(result))
                self.rewrite(name, original)

    def test_old_unsealed_draft_cannot_be_approved(self):
        result = prepare(self.output, self.frozen)
        del result['record_pins']
        self.rewrite('acceptance.json', result)
        with self.assertRaisesRegex(ValueError, 'sealed'):
            load(self.output, digest(result))

    def test_resealed_phase_with_unexpected_write_fails_recompilation(self):
        result = prepare(self.output, self.frozen)
        name = 'staging/firmware-start/plan.json'
        plan = json.loads((self.output/name).read_text())
        plan['after']['files'][0]['mtime_ns'] += 1
        self.rewrite(name, plan)
        result['staging_plan_pins'][0] = digest(plan)
        self.rewrite('acceptance.json', result)
        with self.assertRaisesRegex(ValueError, 'compiled transition'):
            load(self.output, digest(result))

    def test_resealed_review_cannot_redirect_phase_journal(self):
        result = prepare(self.output, self.frozen)
        name = 'staging/review.json'
        review = json.loads((self.output/name).read_text())
        review['phases'][0]['journal'] = str(self.root/'unrelated')
        self.rewrite(name, review)
        result['record_pins'][name] = digest(review)
        self.rewrite('acceptance.json', result)
        with self.assertRaisesRegex(ValueError, 'compiled transitions'):
            load(self.output, digest(result))

    def test_resealed_request_cannot_drop_lease_owner(self):
        result = prepare(self.output, self.frozen)
        request = json.loads((self.output/'request.json').read_text())
        request['lease_owner'] = None
        self.rewrite('request.json', request)
        result['record_pins']['request.json'] = digest(request)
        self.rewrite('acceptance.json', result)
        with self.assertRaisesRegex(ValueError, 'lease owner'):
            load(self.output, digest(result))

    def test_frozen_firmware_is_not_replaced_by_later_file_edits(self):
        (self.root/'firmware.json').write_text('{}')
        result = prepare(self.output, self.frozen)
        self.assertEqual(result['firmware_sha256'], self.frozen['firmware_sha256'])

    def test_uncertain_publication_is_not_replaced_by_successful_inspection(self):
        fd = private_directory(self.publication)
        try:
            append_event(fd, dict(state='dispatch', direction='restore', nonce='f'*32))
        finally: os.close(fd)
        with patch('forge_recovery_stage_prepare.transport') as contact:
            with self.assertRaisesRegex(RuntimeError, 'Uncertain'): prepare(self.output, self.frozen)
            contact.assert_not_called()
        self.assertEqual(self.acceptance()['status'], 'incomplete')
        self.assertEqual(self.captures, 0)

    def test_damaged_private_image_or_policy_stops_before_capture(self):
        self.bad_image = True
        with self.assertRaisesRegex(ValueError, 'not intact'): prepare(self.output, self.frozen)
        self.assertEqual(self.captures, 0)
        self.assertFalse(self.acceptance()['target_written'])

    def test_wrong_target_preimages_rejected_and_retained(self):
        self.wrong_machine = True
        with self.assertRaisesRegex(ValueError, 'another target'): prepare(self.output, self.frozen)
        self.assertTrue((self.output/'before-capture.json').exists())
        self.assertFalse((self.output/'staging').exists())

    def test_changed_preimages_leave_incomplete_draft_not_ready(self):
        self.change_preimage = True
        with self.assertRaisesRegex(ValueError, 'preimages changed'): prepare(self.output, self.frozen)
        self.assertEqual(self.acceptance()['status'], 'incomplete')
        self.assertFalse(self.acceptance()['deployment_authorized'])

    def test_reboot_during_preparation_never_marks_draft_ready(self):
        changed = dict(self.boot, boot_id='22222222-2222-3333-4444-555555555555')
        with patch('forge_recovery_stage_prepare.capture_boot', side_effect=[self.boot, changed]):
            with self.assertRaisesRegex(ValueError, 'boot changed'): prepare(self.output, self.frozen)
        self.assertEqual(self.acceptance()['status'], 'incomplete')

    def test_normal_boot_rejects_wrong_selection_and_recovery_namespace(self):
        self.assertEqual(normal_boot(self.boot, self.plan['machine_id']), self.boot)
        for changes in (dict(tryboot=True), dict(tryboot=1), dict(partition=2), dict(machine_id='f'*32),
                        dict(cmdline='root=/dev/ram0'), dict(cmdline='root=x root=y'),
                        dict(cmdline='root=x uconsole.recovery_owner='+'a'*64),
                        dict(cmdline='root=x uconsole.emulator=1')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                normal_boot(dict(self.boot, **changes), self.plan['machine_id'])

    def test_owner_controller_preparation_serializes_target_and_has_no_mcp_route(self):
        from forge_controller import Controller
        from forge_client import ClientSession
        from uconsole_mcp import BY_NAME
        owner = Controller({'main': self.root/'workspace', 'other': self.root/'other'})
        self.addCleanup(owner.close)
        self.assertNotIn('prepare_recovery_staging', BY_NAME)
        with self.assertRaises(ValueError):
            ClientSession(owner, ['main']).call('prepare_recovery_staging', {})
        entered, release = threading.Event(), threading.Event()
        def worker(output, frozen):
            entered.set()
            if not release.wait(5): raise TimeoutError('fixture not released')
            return {'status': 'prepared-not-approved'}
        with patch('forge_recovery_stage_prepare.prepare', side_effect=worker):
            submitted = owner.prepare_recovery_staging('main', self.publication, self.frozen['publication_sha256'],
                self.root/'firmware.json', self.frozen['firmware_sha256'], self.output)
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaisesRegex(ValueError, 'physical target'):
                    owner.submit('other', 'competing', lambda: None, target_identity=self.plan['machine_id'])
                self.assertFalse(owner.cancel(submitted['job_id'])['requested'])
            finally:
                release.set()
                owner.jobs[submitted['job_id']][2].result(timeout=5)
        self.assertEqual(owner.grants, frozenset())
        self.assertNotIn(str(self.root), json.dumps(owner.job(submitted['job_id'])))


if __name__ == '__main__':
    unittest.main()
