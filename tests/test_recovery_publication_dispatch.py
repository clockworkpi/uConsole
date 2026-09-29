import json
import unittest
from unittest.mock import patch

import forge_recovery_publication_dispatch as publication
from forge_recovery_bootplan import digest
from forge_recovery_journal import acknowledgement
import test_recovery_publication_prepare


class PublicationDispatchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_recovery_publication_prepare.PublicationPreparationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        accepted = self.fixture.prepare()
        self.directory = self.fixture.output
        self.pin = digest(accepted)
        self.frozen = publication.inputs(self.directory, self.pin)
        self.boot = self.fixture.boot
        self.effects = []
        self.state = 'absent'

    def transport(self, plan, direction, nonce, pin):
        if direction == 'inspect':
            return dict(kind='inspection', nonce=nonce, plan_sha256=pin,
                machine_id=self.boot['machine_id'], boot_id=self.boot['boot_id'],
                policy=dict(valid=True), files=dict(parent_private=True,
                    destination=dict(state=self.state), scratch=dict(state='absent')),
                mutation_performed=False, retry_authorized=False)
        self.assertEqual(direction, 'apply')
        self.effects.append(direction)
        self.state = 'matching-bytes'
        if getattr(self, 'uncertain', False): raise TimeoutError('lost acknowledgement')
        return acknowledgement(plan, direction, nonce, pin)

    def publish(self, boots=None):
        with patch.object(publication, 'capture', side_effect=boots or [self.boot, self.boot]), \
                patch.object(publication, 'transport', side_effect=self.transport):
            return publication.publish(self.frozen)

    def test_publication_has_durable_ack_and_no_boot_authority(self):
        result = self.publish()
        self.assertEqual(result['status'], 'published-not-boot-qualified')
        self.assertEqual(self.effects, ['apply'])
        for key in ('reboot_performed', 'boot_qualified', 'root_write_authorized', 'automatic_retry_performed'):
            self.assertIs(result[key], False)
        self.assertTrue((self.directory/'publish-attempt/acknowledgement.json').exists())
        self.assertNotIn('source', result)

    def test_completed_attempt_is_not_replayed(self):
        self.publish()
        with self.assertRaises(ValueError): self.publish()
        self.assertEqual(self.effects, ['apply'])

    def test_uncertain_transport_retains_attempt_and_never_retries(self):
        self.uncertain = True
        with self.assertRaises(TimeoutError): self.publish()
        with self.assertRaises(ValueError): self.publish()
        self.assertEqual(self.effects, ['apply'])
        failure = json.loads((self.directory/'publish-attempt/failure.json').read_text())
        self.assertTrue(failure['publication_may_have_started'])
        self.assertFalse((self.directory/'publish-attempt/acceptance.json').exists())

    def test_changed_boot_blocks_before_dispatch(self):
        changed = dict(self.boot, boot_id='99999999-2222-3333-4444-555555555555')
        with self.assertRaises(ValueError): self.publish([changed])
        self.assertEqual(self.effects, [])
        failure = json.loads((self.directory/'publish-attempt/failure.json').read_text())
        self.assertFalse(failure['publication_may_have_started'])

    def test_existing_matching_image_is_not_adopted(self):
        self.state = 'matching-bytes'
        with self.assertRaises(ValueError): self.publish()
        self.assertEqual(self.effects, [])

    def test_reboot_after_ack_retains_ack_but_not_acceptance(self):
        changed = dict(self.boot, boot_id='99999999-2222-3333-4444-555555555555')
        with self.assertRaises(ValueError): self.publish([self.boot, changed])
        self.assertTrue((self.directory/'publish-attempt/acknowledgement.json').exists())
        self.assertFalse((self.directory/'publish-attempt/acceptance.json').exists())
        with self.assertRaises(ValueError): self.publish()
        self.assertEqual(self.effects, ['apply'])

    def test_changed_plan_or_acceptance_pin_rejects_without_target_contact(self):
        with self.assertRaises(ValueError): publication.inputs(self.directory, '0'*64)
        plan = json.loads((self.directory/'publication/plan.json').read_text())
        plan['source'] = '/unreviewed/source'
        (self.directory/'publication/plan.json').write_text(json.dumps(plan))
        with patch.object(publication, 'capture') as remote:
            with self.assertRaises(ValueError): publication.publish(self.frozen)
            remote.assert_not_called()
