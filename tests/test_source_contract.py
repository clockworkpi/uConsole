import unittest

from forge_recovery_derivative import load, stream as derivative_stream
from forge_recovery_restore_source import stream as backup_stream
from forge_recovery_source_contract import BACKUP, DERIVATIVE, validate, completion, check_completion, rollback_manifest
import test_recovery_derivative


class SourceContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_recovery_derivative.RecoveryDerivativeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.change(512, b'enhanced')
        result = self.fixture.run_prepare()
        self.pin = result['manifest_sha256']
        self.manifest, _ = load(self.fixture.output, self.pin)
        self.original = self.manifest['original_manifest']
        self.original_pin = self.manifest['original_manifest_sha256']

    def test_actual_producer_receipts_preserve_distinct_source_kinds(self):
        backup = backup_stream(self.fixture.fixture.source, self.original, self.original_pin, lambda *_: None)
        derivative = derivative_stream(self.fixture.output, self.pin, lambda *_: None)
        for kind, manifest, pin, receipt in ((BACKUP, self.original, self.original_pin, backup),
                (DERIVATIVE, self.manifest, self.pin, derivative)):
            with self.subTest(kind=kind):
                self.assertEqual(check_completion(receipt, manifest, pin, expected_kind=kind), receipt)
                self.assertFalse(receipt['target_restore_verified'])
                self.assertFalse(receipt['normal_boot_release_authorized'])
        self.assertNotEqual(backup['status'], derivative['status'])

    def test_kind_confusion_and_unknown_kinds_are_rejected(self):
        for manifest, pin, kind in ((self.original, self.original_pin, DERIVATIVE),
                (self.manifest, self.pin, BACKUP), (self.manifest, self.pin, 'root-transfer'),
                (self.manifest, '0'*64, DERIVATIVE)):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate(manifest, pin, expected_kind=kind)

    def test_altered_status_authority_and_types_do_not_complete(self):
        receipt = completion(self.manifest, self.pin, expected_kind=DERIVATIVE)
        for field, value in (('status', 'verified-source-stream'), ('target_restore_verified', True),
                             ('normal_boot_release_authorized', 0), ('chunks', False)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                check_completion(dict(receipt, **{field: value}), self.manifest, self.pin,
                                 expected_kind=DERIVATIVE)

    def test_original_rollback_identity_and_returned_values_are_isolated(self):
        original, pin = rollback_manifest(self.manifest, self.pin, expected_kind=DERIVATIVE)
        self.assertEqual((original, pin), (self.original, self.original_pin))
        self.assertNotEqual(original['root'], self.manifest['root'])
        original['root']['sha256'] = '0'*64
        self.assertNotEqual(self.original['root']['sha256'], '0'*64)
        copied = validate(self.manifest, self.pin, expected_kind=DERIVATIVE)
        copied['root'].clear()
        self.assertTrue(self.manifest['root'])
