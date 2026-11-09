"""Installed bundle immutability and recoverable user project creation."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_projects import keyboard_project


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'installed'
        self.source = self.root / 'Code/uconsole_keyboard'
        self.source.mkdir(parents=True)
        (self.source / 'uconsole_keyboard.ino').write_text('original sketch\n')
        (self.source / 'keymap.h').write_text('original keymap\n')
        self.data = Path(self.temp.name).resolve() / 'user data'

    def test_installed_bundle_gets_complete_editable_user_copy(self):
        sketch, updated = keyboard_project(self.root, self.data)
        self.assertFalse(updated)
        self.assertEqual(sketch.parent, self.data / 'projects/uconsole_keyboard')
        self.assertEqual((sketch.parent / 'keymap.h').read_text(), 'original keymap\n')
        sketch.write_text('user edits\n')
        self.assertEqual((self.source / sketch.name).read_text(), 'original sketch\n')

    def test_upgrade_and_reopening_never_overwrite_user_edits(self):
        sketch, _ = keyboard_project(self.root, self.data)
        sketch.write_text('user edits\n')
        self.assertFalse(keyboard_project(self.root, self.data)[1])
        (self.source / sketch.name).write_text('new release source\n')
        reopened, updated = keyboard_project(self.root, self.data)
        self.assertTrue(updated)
        self.assertEqual(reopened.read_text(), 'user edits\n')

    def test_checkout_keeps_explicit_source_editing(self):
        (self.root / '.git').mkdir()
        sketch, updated = keyboard_project(self.root, self.data)
        self.assertEqual(sketch, self.source / 'uconsole_keyboard.ino')
        self.assertFalse(updated)
        self.assertFalse(self.data.exists())

    def test_existing_unrecognized_project_is_not_overwritten(self):
        project = self.data / 'projects/uconsole_keyboard'
        project.mkdir(parents=True)
        (project / 'notes.txt').write_text('keep this')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            keyboard_project(self.root, self.data)
        self.assertEqual((project / 'notes.txt').read_text(), 'keep this')

    def test_bundled_symlinks_are_not_followed_into_user_project(self):
        (self.source / 'outside').symlink_to(self.temp.name, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            keyboard_project(self.root, self.data)
        self.assertFalse((self.data / 'projects/uconsole_keyboard').exists())


if __name__ == '__main__':
    unittest.main()
