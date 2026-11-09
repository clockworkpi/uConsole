import io
import hashlib
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from build_emulator_qemu import extract_source, VERSION
from build_emulator_qemu import generation_key, publish_build
import build_emulator_qemu as builder


class BuildGenerationTests(unittest.TestCase):
    def test_overlapping_stack_and_patched_model_can_be_rebuilt_without_reapplying(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root/'source.tar'
            with tarfile.open(archive, 'w') as stream:
                for name, data in [('configure', b'original\n'), ('meson.build', b'fixture\n')]:
                    member = tarfile.TarInfo(f'qemu-{VERSION}/{name}')
                    member.size = len(data)
                    stream.addfile(member, io.BytesIO(data))
            def change(path, old, new):
                return f'--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{old}\n+{new}\n'.encode()
            patches = [('one', change('configure', 'original', 'middle')),
                       ('two', change('configure', 'middle', 'final')),
                       ('three', change('model.h', 'model input', 'model output'))]
            models = [('model.h', b'model input\n')]
            source = builder.prepare_source(archive, root, patches, models)
            self.assertEqual((source/'configure').read_text(), 'final\n')
            self.assertEqual((source/'model.h').read_text(), 'model output\n')
            with mock.patch.object(builder.subprocess, 'run') as patcher:
                self.assertEqual(builder.prepare_source(archive, root, patches, models), source)
            patcher.assert_not_called()
            (source/'model.h').write_text('changed outside builder')
            with self.assertRaisesRegex(ValueError, 'Cached patched source differs'):
                builder.prepare_source(archive, root, patches, models)
            self.assertEqual((source/'model.h').read_text(), 'changed outside builder')

    def test_failed_stack_is_not_published_and_can_start_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root/'source.tar'
            with tarfile.open(archive, 'w') as stream:
                for name in ('configure', 'meson.build'):
                    member = tarfile.TarInfo(f'qemu-{VERSION}/{name}')
                    member.size = len(b'original\n')
                    stream.addfile(member, io.BytesIO(b'original\n'))
            good = ('one', b'--- a/configure\n+++ b/configure\n@@ -1 +1 @@\n-original\n+changed\n')
            bad = ('two', b'--- a/configure\n+++ b/configure\n@@ -1 +1 @@\n-missing\n+broken\n')
            with self.assertRaises(subprocess.CalledProcessError):
                builder.prepare_source(archive, root, [good, bad], [])
            self.assertFalse((root/f'qemu-{VERSION}').exists())
            source = builder.prepare_source(archive, root, [good], [])
            self.assertEqual((source/'configure').read_text(), 'changed\n')

    def test_changed_patch_uses_pristine_source_and_bad_patch_keeps_selection(self):
        # Use real extraction and patch application; only replace compilation.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / f'qemu-{VERSION}.tar.xz'
            with tarfile.open(archive, 'w:xz') as stream:
                for name, data in [('configure', b'original\n'), ('meson.build', b'fixture\n')]:
                    member = tarfile.TarInfo(f'qemu-{VERSION}/{name}')
                    member.size = len(data)
                    stream.addfile(member, io.BytesIO(data))
            patches = root / 'Code/patch/qemu'
            patches.mkdir(parents=True)
            (root / 'tools').mkdir()
            (root / 'tools/build_emulator_qemu.py').write_text('fixture builder')
            patch = patches / 'fixture.patch'
            model = patches / 'fixture.h'
            model.write_text('first model')
            real_run = subprocess.run
            def run(command, **kwargs):
                if command[0] == 'patch':
                    return real_run(command, **kwargs)
                return subprocess.CompletedProcess(command, 0)
            def revision(value, before='original'):
                patch.write_text('--- a/configure\n+++ b/configure\n@@ -1 +1 @@\n'
                                 f'-{before}\n+{value}\n')
            with mock.patch.multiple(builder, ROOT=root, RESOURCE_ROOT=root,
                                     MODEL_SOURCES=[('fixture.h', 'fixture.h')],
                                     PATCHES=['fixture.patch'],
                                     SHA256=hashlib.sha256(archive.read_bytes()).hexdigest()), \
                    mock.patch.object(builder.subprocess, 'run', side_effect=run):
                revision('first')
                builder.build_qemu(1)
                first = (root / 'qemu-build').resolve()
                revision('second')
                builder.build_qemu(1)
                second = (root / 'qemu-build').resolve()
                self.assertNotEqual(first, second)
                self.assertEqual((first / 'qemu-source/configure').read_text(), 'first\n')
                self.assertEqual((second / 'qemu-source/configure').read_text(), 'second\n')
                model.write_text('second model')
                builder.build_qemu(1)
                third = (root / 'qemu-build').resolve()
                self.assertNotEqual(second, third)
                self.assertEqual((second / 'qemu-source/fixture.h').read_text(), 'first model')
                self.assertEqual((third / 'qemu-source/fixture.h').read_text(), 'second model')
                revision('invalid', before='missing')
                with self.assertRaises(subprocess.CalledProcessError):
                    builder.build_qemu(1)
                self.assertEqual((root / 'qemu-build').resolve(), third)

    def test_failed_build_never_replaces_working_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public = root / 'qemu-build'
            public.mkdir()
            (public / 'qemu-system-aarch64').write_text('working')
            archive = root / f'qemu-{VERSION}.tar.xz'
            archive.write_bytes(b'archive fixture')
            def extract(archive, generation):
                source = generation / f'qemu-{VERSION}'
                source.mkdir()
                return source
            def run(command, **kwargs):
                if command[0] == 'ninja':
                    raise subprocess.CalledProcessError(1, command)
                return subprocess.CompletedProcess(command, 0)
            with mock.patch.multiple(builder, ROOT=root, PATCHES=[], MODEL_SOURCES=[],
                                     SHA256=hashlib.sha256(archive.read_bytes()).hexdigest()), \
                    mock.patch.object(builder, 'extract_source', side_effect=extract), \
                    mock.patch.object(builder.subprocess, 'run', side_effect=run):
                with self.assertRaises(subprocess.CalledProcessError):
                    builder.build_qemu(2)
            self.assertFalse(public.is_symlink())
            self.assertEqual((public / 'qemu-system-aarch64').read_text(), 'working')

    def test_patch_revision_and_order_change_identity(self):
        original = [('one', b'a'), ('two', b'b')]
        self.assertEqual(generation_key(original), generation_key(original))
        self.assertNotEqual(generation_key(original), generation_key(original[::-1]))
        self.assertNotEqual(generation_key(original), generation_key([('one', b'changed'), original[1]]))

    def test_legacy_build_is_retained_and_next_switch_preserves_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public = root / 'qemu-build'
            public.mkdir()
            (public / 'old').write_text('original')
            first, second = root / 'first', root / 'second'
            first.mkdir()
            second.mkdir()
            publish_build(root, first)
            self.assertEqual(public.resolve(), first.resolve())
            legacy, = root.glob('qemu-build.legacy-*')
            self.assertEqual((legacy / 'old').read_text(), 'original')
            publish_build(root, second)
            self.assertEqual(public.resolve(), second.resolve())
            self.assertTrue(first.is_dir())

    def test_failed_publication_restores_legacy_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public = root / 'qemu-build'
            public.mkdir()
            (public / 'old').write_text('original')
            with mock.patch('build_emulator_qemu.os.replace', side_effect=OSError('fault')):
                with self.assertRaises(OSError):
                    publish_build(root, root / 'new')
            self.assertEqual((public / 'old').read_text(), 'original')
            self.assertFalse(list(root.glob('.qemu-publish-*')))

    def test_unexpected_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'qemu-build').write_text('preserve')
            with self.assertRaises(ValueError):
                publish_build(root, root / 'new')
            self.assertEqual((root / 'qemu-build').read_text(), 'preserve')


class SourceExtractionTests(unittest.TestCase):
    def archive(self, root, link_name, target):
        archive = root / 'source.tar'
        prefix = 'qemu-' + VERSION
        with tarfile.open(archive, 'w') as stream:
            for name in ('configure', 'meson.build'):
                member = tarfile.TarInfo(prefix + '/' + name)
                member.size = 4
                stream.addfile(member, io.BytesIO(b'test'))
            link = tarfile.TarInfo(prefix + '/' + link_name)
            link.type = tarfile.SYMTYPE
            link.linkname = target
            stream.addfile(link)
        return archive

    def test_only_known_unused_link_is_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = 'roms/edk2/EmulatorPkg/Unix/Host/X11IncludeHack'
            archive = self.archive(root, name, '/opt/X11/include')
            source = extract_source(archive, root)
            self.assertEqual((source / 'configure').read_bytes(), b'test')
            self.assertFalse((source / name).is_symlink())
            self.assertFalse(list(root.glob('.qemu-extract-*')))

    def test_other_absolute_or_escaping_links_leave_no_partial_source(self):
        for name, target in (('other', '/opt/X11/include'),
                             ('roms/edk2/EmulatorPkg/Unix/Host/X11IncludeHack', '/tmp/elsewhere'),
                             ('escape', '../../outside')):
            with self.subTest(name=name, target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = self.archive(root, name, target)
                with self.assertRaises(tarfile.FilterError):
                    extract_source(archive, root)
                self.assertFalse((root / ('qemu-' + VERSION)).exists())
                self.assertFalse(list(root.glob('.qemu-extract-*')))

    def test_internal_symlink_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = extract_source(self.archive(root, 'internal', 'configure'), root)
            self.assertTrue((source / 'internal').is_symlink())
            self.assertEqual((source / 'internal').read_bytes(), b'test')
