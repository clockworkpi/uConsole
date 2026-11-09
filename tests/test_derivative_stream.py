import copy
import json
import os
import unittest
from unittest.mock import Mock

from forge_recovery_derivative import load, stream, validate
from forge_recovery_restore_source import digest
import test_recovery_derivative


class DerivativeStreamTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_recovery_derivative.RecoveryDerivativeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.change(512, b'enhanced')
        self.result = self.fixture.run_prepare()
        self.directory = self.fixture.output
        self.pin = self.result['manifest_sha256']
        self.manifest, self.inputs = load(self.directory, self.pin)

    def save(self, name, value):
        path = self.directory/name
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def repin(self, manifest):
        pin = digest(manifest)
        self.save('manifest.json', manifest)
        accepted = self.fixture.read('acceptance.json')
        accepted.update(manifest_sha256=pin, card=manifest['card'], root=manifest['root'])
        self.save('acceptance.json', accepted)
        return pin

    def test_stream_verifies_all_chunks_and_full_source_without_target_authority(self):
        received, pulses = [], []
        result = stream(self.directory, self.pin, lambda chunk,data: received.append((chunk,data)),
                        heartbeat=lambda: pulses.append(True))
        self.assertEqual(b''.join(data for _,data in received), self.fixture.image.read_bytes()[512:-512])
        self.assertEqual([chunk for chunk,_ in received], self.manifest['chunks'])
        self.assertEqual(result['status'], 'verified-derivative-stream')
        self.assertGreaterEqual(len(pulses), 5)
        self.assertFalse(result['target_restore_verified'])
        self.assertFalse(result['normal_boot_release_authorized'])

    def test_manifest_shape_lineage_and_strict_types_are_validated(self):
        edits = [('schema', True), ('kind', 'backup-root-chunk-source'),
                 ('target_write_authorized', True), ('native_boot_qualified', True),
                 ('backup_acceptance_sha256', '0'*64), ('original_manifest_sha256', '0'*64),
                 ('image_fingerprint', [0]*8), ('changed_chunks', [False]),
                 ('chunks', self.manifest['chunks'][:-1]), ('chunk_bytes', True),
                 ('prefix', dict(self.manifest['prefix'], offset=False)),
                 ('root', dict(self.manifest['root'], bytes=512)),
                 ('card', dict(self.manifest['card'], bytes=512))]
        for key, value in edits:
            with self.subTest(key=key):
                changed = copy.deepcopy(self.manifest)
                changed[key] = value
                with self.assertRaises(ValueError):
                    validate(changed, digest(changed))
        with self.assertRaisesRegex(ValueError, 'pinned digest'):
            validate(self.manifest, '0'*64)

    def test_incomplete_or_different_inputs_never_deliver(self):
        original = self.fixture.read('acceptance.json')
        self.save('acceptance.json', dict(original, status='incomplete'))
        consume = Mock()
        with self.assertRaisesRegex(ValueError, 'qualification'):
            stream(self.directory, self.pin, consume)
        self.save('acceptance.json', original)
        changed = dict(self.inputs, original_manifest_sha256='0'*64)
        self.save('inputs.json', changed)
        with self.assertRaisesRegex(ValueError, 'retained inputs'):
            stream(self.directory, self.pin, consume)
        consume.assert_not_called()

    def test_source_or_rollback_identity_change_precedes_delivery(self):
        consume = Mock()
        initial = self.fixture.image.stat()
        os.utime(self.fixture.image, ns=(initial.st_atime_ns, initial.st_mtime_ns+1000000))
        with self.assertRaisesRegex(ValueError, 'source identity'):
            stream(self.directory, self.pin, consume)
        backup = self.fixture.fixture.source/'card.img.gz'
        initial = backup.stat()
        os.utime(backup, ns=(initial.st_atime_ns, initial.st_mtime_ns+1000000))
        with self.assertRaisesRegex(ValueError, 'Rollback backup'):
            stream(self.directory, self.pin, consume)
        consume.assert_not_called()

    def test_wrong_chunk_or_prefix_is_rejected_before_any_delivery(self):
        for field in ('prefix', 'chunk'):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.manifest)
                value = changed['prefix'] if field == 'prefix' else changed['chunks'][0]
                value['sha256'] = '0'*64
                pin = self.repin(changed)
                consume = Mock()
                with self.assertRaisesRegex(ValueError, 'checksum|before delivery'):
                    stream(self.directory, pin, consume)
                consume.assert_not_called()

    def test_full_hash_and_suffix_failure_are_not_stream_completion(self):
        for field in ('root', 'card', 'suffix'):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.manifest)
                changed[field]['sha256'] = '0'*64
                pin = self.repin(changed)
                consume = Mock()
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    stream(self.directory, pin, consume)
                self.assertEqual(consume.call_count, 2)

    def test_consumer_mutation_never_completes(self):
        def consume(chunk, data):
            info = self.fixture.image.stat()
            os.utime(self.fixture.image, ns=(info.st_atime_ns, info.st_mtime_ns+1000000))
            chunk.clear()
        with self.assertRaisesRegex(ValueError, 'changed during delivery'):
            stream(self.directory, self.pin, consume)

    def test_consumer_failure_is_not_hidden(self):
        consume = Mock(side_effect=ConnectionError('consumer disconnected'))
        with self.assertRaisesRegex(ConnectionError, 'consumer disconnected'):
            stream(self.directory, self.pin, consume)
        self.assertEqual(consume.call_count, 1)

    def test_changed_second_chunk_is_not_delivered(self):
        received = []
        def consume(chunk, data):
            received.append(chunk)
            self.fixture.change(512+self.manifest['chunks'][1]['offset'], b'changed')
        with self.assertRaisesRegex(ValueError, 'before delivery'):
            stream(self.directory, self.pin, consume)
        self.assertEqual(len(received), 1)

    def test_rollback_receipt_mutation_prevents_completion(self):
        def consume(chunk, data):
            self.fixture.fixture.save(self.fixture.fixture.source/'acceptance.json',
                                      dict(self.fixture.fixture.accepted, unexpected=True))
        with self.assertRaisesRegex(ValueError, 'changed during streaming'):
            stream(self.directory, self.pin, consume)

    def test_appended_bytes_fail_eof_verification(self):
        def consume(chunk, data):
            with self.fixture.image.open('ab') as output:
                output.write(b'not part of the qualified image')
        with self.assertRaisesRegex(ValueError, 'EOF'):
            stream(self.directory, self.pin, consume)

    def test_heartbeat_failure_is_terminal(self):
        consume = Mock()
        with self.assertRaisesRegex(RuntimeError, 'lease failed'):
            stream(self.directory, self.pin, consume, heartbeat=Mock(side_effect=RuntimeError('lease failed')))
        consume.assert_not_called()
