import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from forge_session_enrollment import inputs, prepare, summary
from forge_recovery_bootplan import digest
from forge_ram_transport import RecoveryProbe
from forge_controller import Controller
from forge_client import ClientSession
import test_recovery_stage_prepare
import test_ram_identity


class SessionEnrollmentTests(unittest.TestCase):
    def setUp(self):
        fixture = test_recovery_stage_prepare.RecoveryStagePreparationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        accepted = test_recovery_stage_prepare.prepare(fixture.output, fixture.frozen)
        self.root, self.stage = fixture.root, fixture.output
        self.pin = digest(accepted)
        self.output = self.root/'enrollment'
        for name in ('key', 'known_hosts'):
            (self.root/name).write_text('private fixture credential')
            (self.root/name).chmod(0o600)
        ram = test_ram_identity.RamIdentityTests()
        ram.setUp()
        self.boot = ram.record['boot_id']
        self.value = inputs(self.stage, self.pin, 'fixture', self.root/'key', self.root/'known_hosts',
                            ram.record['kernel'], ram.serial, self.boot, 1)
        self.observation = dict(ram.record, cmdline='root=/dev/ram0 rdinit=/init uconsole.recovery=1 '
            'uconsole.forge_trial='+self.value.probe.nonce+' uconsole.recovery_owner='+self.value.owner+
            ' uconsole.recovery_lease=1 uconsole.recovery_watchdog=1')
        self.records = [copy.deepcopy(self.observation),
                        dict(identity=copy.deepcopy(self.observation), bootloader=dict(tryboot=1, partition=1)),
                        copy.deepcopy(self.observation)]

    def observe(self):
        return patch.object(RecoveryProbe, '_observe', side_effect=copy.deepcopy(self.records))

    def test_verified_enrollment_is_private_unrenewed_and_non_authorizing(self):
        with self.observe() as remote, patch('forge_recovery_session.Session.renew') as renew:
            result = prepare(self.output, self.value)
        self.assertEqual(remote.call_count, 3)
        renew.assert_not_called()
        self.assertEqual(result['status'], 'enrolled-not-leased')
        for flag in ('lease_acquired', 'target_written', 'policy_approved', 'root_write_authorized',
                     'normal_boot_release_authorized', 'reboot_performed'):
            self.assertIs(result[flag], False)
        self.assertEqual(sorted(path.name for path in (self.output/'session').iterdir()), ['binding.json'])
        for path in self.output.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        binding = json.loads((self.output/'session/binding.json').read_text())
        self.assertEqual(binding['owner'], self.value.owner)
        self.assertEqual(result['binding_sha256'], digest(binding))
        self.assertNotIn(self.value.owner, json.dumps(result))
        self.assertNotIn('private fixture credential', json.dumps(result))

    def test_discovery_pins_verified_boot_without_manual_uuid(self):
        value = inputs(self.stage, self.pin, 'fixture', self.root/'key', self.root/'known_hosts',
                       self.value.probe.kernel, self.value.probe.serial, None, 1)
        with self.observe() as remote:
            result = prepare(self.output, value)
        self.assertEqual(remote.call_count, 3)
        self.assertEqual(result['boot_id'], self.boot)
        self.assertTrue((self.output/'discovery.json').exists())
        self.assertEqual(json.loads((self.output/'session/binding.json').read_text())['boot_id'], self.boot)
        self.assertFalse(result['lease_acquired'])

    def test_discovery_does_not_follow_a_second_boot(self):
        records = copy.deepcopy(self.records)
        records[1]['identity']['boot_id'] = '99999999-1234-1234-1234-123456789abc'
        with patch.object(RecoveryProbe, '_observe', side_effect=records) as remote:
            with self.assertRaisesRegex(ValueError, 'boot UUID differs'):
                prepare(self.output, replace(self.value, boot_id=None))
        self.assertEqual(remote.call_count, 2)
        self.assertFalse((self.output/'session').exists())

    def test_discovery_refuses_existing_claim_after_one_read_only_probe(self):
        with self.observe(): prepare(self.output, self.value)
        with self.observe() as remote:
            with self.assertRaises(FileExistsError):
                prepare(self.root/'duplicate', replace(self.value, boot_id=None))
        self.assertEqual(remote.call_count, 1)
        self.assertFalse((self.root/'duplicate/session').exists())

    def test_duplicate_boot_claim_and_output_never_repeat_ssh(self):
        with self.observe(): prepare(self.output, self.value)
        with patch.object(RecoveryProbe, '_observe') as remote:
            with self.assertRaises(FileExistsError): prepare(self.output, self.value)
            with self.assertRaises(FileExistsError): prepare(self.root/'duplicate', self.value)
        remote.assert_not_called()
        self.assertFalse((self.root/'duplicate/session').exists())

    def assert_failed_observation(self, records):
        with patch.object(RecoveryProbe, '_observe', side_effect=records):
            with self.assertRaises(ValueError): prepare(self.output, self.value)
        self.assertTrue((self.output/'failure.json').exists())
        self.assertFalse((self.output/'session').exists())

    def test_wrong_owner_preserves_failure_without_session(self):
        records = copy.deepcopy(self.records)
        records[0]['cmdline'] = records[0]['cmdline'].replace(self.value.owner, 'f'*64)
        self.assert_failed_observation(records)

    def test_boot_change_preserves_failure_without_session(self):
        records = copy.deepcopy(self.records)
        records[-1]['boot_id'] = '99999999-1234-1234-1234-123456789abc'
        self.assert_failed_observation(records)

    def test_invalid_selection_or_normal_boot_fails_locally(self):
        for boot, selected in ((self.boot, True), ('bad', 1),
                                ('11111111-2222-3333-4444-555555555555', 1)):
            with self.subTest(boot=boot, selected=selected), patch.object(RecoveryProbe, '_observe') as remote:
                with self.assertRaises(ValueError):
                    inputs(self.stage, self.pin, 'fixture', self.root/'key', self.root/'known_hosts',
                           self.value.probe.kernel, self.value.probe.serial, boot, selected)
                remote.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_changed_credentials_fail_before_outputs_or_ssh(self):
        (self.root/'key').write_text('changed')
        with patch.object(RecoveryProbe, '_observe') as remote:
            with self.assertRaisesRegex(ValueError, 'changed'):
                prepare(self.output, self.value)
        remote.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_changed_staging_pin_fails_before_outputs_or_ssh(self):
        (self.stage/'acceptance.json').write_text('{}')
        with patch.object(RecoveryProbe, '_observe') as remote:
            with self.assertRaises(ValueError): prepare(self.output, self.value)
        remote.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_controller_job_does_not_expand_grants_or_expose_owner(self):
        controller = Controller({'fixture': self.root/'workspace'})
        try:
            client = ClientSession(controller, ['fixture'])
            with self.observe():
                submitted = controller.enroll_recovery_session('fixture', self.value, self.output)
                controller.jobs[submitted['job_id']][2].result(timeout=5)
            self.assertEqual(controller.grants, frozenset())
            self.assertIsNone(controller.recovery_jobs)
            self.assertNotIn(self.value.owner, json.dumps(controller.job(submitted['job_id'])))
            with self.assertRaises((ValueError, PermissionError)):
                client.call('enroll_recovery_session', {'workspace': 'fixture'})
            self.assertNotIn(self.value.owner, json.dumps(summary(self.value)))
        finally:
            controller.close()
