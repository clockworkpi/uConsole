import json
from pathlib import Path
import sys
import tempfile
import unittest
import tarfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import validate_native_archive as validation


class NativeArchiveGuards(unittest.TestCase):
    def test_all_packaged_builder_inputs_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'tools').mkdir()
            assets = root/'Code/patch/qemu'
            assets.mkdir(parents=True)
            (root/'tools/build_emulator_qemu.py').write_text(
                "PATCHES = ['model.patch']\nMODEL_SOURCES = [('forge-modem.c', 'hw/usb/forge-modem.c'), "
                "('usb-net-internal.h', 'hw/usb/usb-net-internal.h')]\n")
            (assets/'model.patch').write_text('patch')
            with self.assertRaises(FileNotFoundError):
                validation.check_qemu_sources(root)
            (assets/'forge-modem.c').write_text('model')
            with self.assertRaises(FileNotFoundError):
                validation.check_qemu_sources(root)
            (assets/'usb-net-internal.h').write_text('header')
            self.assertEqual(len(validation.check_qemu_sources(root)), 4)
            (assets/'usb-net-internal.h').unlink()
            outside = root.parent/('outside-'+root.name)
            try:
                outside.write_text('outside')
                (assets/'usb-net-internal.h').symlink_to(outside)
                with self.assertRaisesRegex(ValueError, 'escaping'):
                    validation.check_qemu_sources(root)
            finally:
                outside.unlink(missing_ok=True)

    def test_packaged_builder_input_paths_cannot_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'tools').mkdir()
            (root/'tools/build_emulator_qemu.py').write_text(
                "PATCHES = ['../outside.patch']\nMODEL_SOURCES = [('model.c', 'hw/model.c')]\n")
            with self.assertRaisesRegex(ValueError, 'patch name'):
                validation.check_qemu_sources(root)

    def test_packaged_skill_links_must_exist_and_stay_inside_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory)
            root = payload/'share/doc/uconsole-workbench/skills/uconsole-forge'
            (root/'references').mkdir(parents=True)
            (root/'SKILL.md').write_text('[Targets](references/targets.md)')
            with self.assertRaises(FileNotFoundError):
                validation.check_skill(payload)
            (root/'references/targets.md').write_text('fixture')
            self.assertEqual(set(validation.check_skill(payload)), {'SKILL.md', 'references/targets.md'})
            (root/'references/targets.md').unlink()
            outside = payload/'outside.md'
            outside.write_text('outside')
            (root/'references/targets.md').symlink_to(outside)
            with self.assertRaises(ValueError):
                validation.check_skill(payload)

    def test_installed_resources_require_scoped_complete_set_and_new_tools(self):
        from uconsole_mcp import TOOLS
        tools = {'result': {'tools': TOOLS}}
        resources = {'result': {'resources': [dict(uri='forge://workspace/test/'+kind,
                      mimeType='text/plain' if kind == 'serial' else 'application/json')
                      for kind in ('state', 'serial', 'jobs', 'tasks', 'targets')]}}
        self.assertEqual(len(validation.check_forge_surface(tools, resources)), 5)
        resources['result']['resources'][0]['uri'] = 'forge://workspace/other/state'
        with self.assertRaises(ValueError):
            validation.check_forge_surface(tools, resources)
        with self.assertRaises(ValueError):
            validation.check_forge_surface({'result': {'tools': []}}, resources)

    def test_installed_adc_schema_requires_fields_and_exact_bounds(self):
        fields = {'adc_input_uv': {'type': 'integer', 'minimum': 0, 'maximum': 3300000},
                  'adc_powered': {'type': 'boolean'}}
        reference = {'type': 'string', 'enum': ['fixed', 'missing']}
        reply = {'result': {'tools': [{'name': 'power_set',
                                      'inputSchema': {'properties': fields}},
                                     {'name': 'boot', 'inputSchema': {'properties': {
                                         'adc_reference': reference}}}]}}
        self.assertEqual(validation.check_adc_schema(reply),
                         dict(fields, boot_adc_reference=reference))
        reference['enum'] = ['fixed']
        with self.assertRaisesRegex(ValueError, 'boot reference'):
            validation.check_adc_schema(reply)
        reference['enum'] = ['fixed', 'missing']
        fields['adc_input_uv']['maximum'] = 5000000
        with self.assertRaisesRegex(ValueError, 'adc_input_uv'):
            validation.check_adc_schema(reply)
        fields['adc_input_uv']['maximum'] = 3300000
        del fields['adc_powered']
        with self.assertRaisesRegex(ValueError, 'adc_powered'):
            validation.check_adc_schema(reply)

    def test_missing_archive_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(FileNotFoundError):
                validation.run(root / 'missing.tar.gz', root / 'evidence')
            self.assertFalse((root / 'evidence').exists())

    def test_missing_model_source_fails_before_launching_installed_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'missing-assets.tar.gz'
            with tarfile.open(archive, 'w:gz'):
                pass
            with patch.object(validation.subprocess, 'run') as launch:
                with self.assertRaises(FileNotFoundError):
                    validation.run(archive, root / 'evidence')
                launch.assert_not_called()
            record = json.loads((root / 'evidence/native-archive-acceptance.json').read_text())
            self.assertEqual(record['status'], 'failed')
            self.assertIn('adc101c.c', record['error'])

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'bad.tar.gz'
            archive.write_bytes(b'fixture')
            output = root / 'evidence'
            output.mkdir()
            proof = output / 'native-archive-acceptance.json'
            proof.write_text('prior evidence')
            with self.assertRaises(FileExistsError):
                validation.run(archive, output)
            self.assertEqual(proof.read_text(), 'prior evidence')

    def test_bad_archive_retains_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'bad.tar.gz'
            archive.write_bytes(b'not a tar archive')
            with self.assertRaises(tarfile.ReadError):
                validation.run(archive, root / 'evidence')
            record = json.loads((root / 'evidence/native-archive-acceptance.json').read_text())
            self.assertEqual(record['status'], 'failed')
            self.assertIn('error', record)
