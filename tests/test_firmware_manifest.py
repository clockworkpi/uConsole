"""Firmware freshness checks reject stale sources, settings and binary bytes."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from firmware_manifest import FQBN, build_firmware, builder_identity, record, verify


class FirmwareManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source'
        (self.root / 'Code/uconsole_keyboard').mkdir(parents=True)
        (self.root / 'tools').mkdir()
        (self.root / 'Code/uconsole_keyboard/example.ino').write_text('source')
        (self.root / 'Makefile').write_text('recipe')
        (self.root / 'tools/firmware_manifest.py').write_text('recorder')
        self.build = Path(self.temp.name) / 'build'
        (self.build / 'firmware').mkdir(parents=True)
        self.binary = self.build / 'firmware/uconsole_keyboard.ino.bin'
        self.binary.write_bytes(b'compiled fixture')
        record(self.build, FQBN, {'fixture': 'builder'}, self.root)

    def test_current_inputs_and_bytes_are_accepted(self):
        self.assertEqual(verify(self.build, FQBN, self.root)['builder'], {'fixture': 'builder'})

    def test_changed_binary_is_rejected(self):
        self.binary.write_bytes(b'different binary')
        with self.assertRaisesRegex(ValueError, 'bytes'):
            verify(self.build, FQBN, self.root)

    def test_changed_source_and_build_settings_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'FQBN'):
            verify(self.build, 'another:board', self.root)
        (self.root / 'Code/uconsole_keyboard/example.ino').write_text('changed source')
        with self.assertRaisesRegex(ValueError, 'source/build recipe'):
            verify(self.build, FQBN, self.root)

    def test_added_source_is_rejected(self):
        (self.root / 'Code/uconsole_keyboard/new.h').write_text('new header')
        with self.assertRaisesRegex(ValueError, 'source/build recipe'):
            verify(self.build, FQBN, self.root)

    def test_build_records_unchanged_inputs_and_toolchain(self):
        with patch('firmware_manifest.builder_identity', return_value={'cli': 'fixture'}), \
             patch('firmware_manifest.subprocess.run') as compile_:
            build_firmware(self.build, FQBN, 'fixture-cli', self.root)
        self.assertEqual(compile_.call_args.args[0][:4], ['fixture-cli', 'compile', '--fqbn', FQBN])
        self.assertEqual(verify(self.build, FQBN, self.root)['builder'], {'cli': 'fixture'})

    def test_mid_build_source_change_invalidates_provenance(self):
        def changed(*args, **kwargs):
            (self.root / 'Code/uconsole_keyboard/example.ino').write_text('changed while compiling')

        with patch('firmware_manifest.builder_identity', return_value={'cli': 'fixture'}), \
             patch('firmware_manifest.subprocess.run', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'inputs changed during compilation'):
                build_firmware(self.build, FQBN, 'fixture-cli', self.root)
        self.assertFalse((self.build / 'firmware/provenance.json').exists())

    def test_mid_build_toolchain_change_invalidates_provenance(self):
        with patch('firmware_manifest.builder_identity', side_effect=[{'cli': 'old'}, {'cli': 'new'}]), \
             patch('firmware_manifest.subprocess.run'):
            with self.assertRaisesRegex(ValueError, 'toolchain identity changed'):
                build_firmware(self.build, FQBN, 'fixture-cli', self.root)
        self.assertFalse((self.build / 'firmware/provenance.json').exists())

    def test_builder_identity_ignores_available_catalog_and_json_order(self):
        import json
        first = {'id': 'vendor:core', 'installed_version': '1', 'releases': {'old': {}, 'new': {}}}
        second = dict(first, releases={'new': {}, 'old': {}, 'future': {}})
        with patch('firmware_manifest.subprocess.check_output', side_effect=[
                '{"version":"1","commit":"a"}', json.dumps({'platforms': [first]}),
                '{"commit":"a","version":"1"}', json.dumps({'platforms': [second]})]):
            self.assertEqual(builder_identity('cli'), builder_identity('cli'))


if __name__ == '__main__':
    unittest.main()
