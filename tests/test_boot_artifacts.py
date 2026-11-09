import hashlib
from pathlib import Path
import tempfile
import unittest

from forge_boot_artifacts import inventory


class BootArtifactTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root/'overlays').mkdir()
        (self.root/'overlays/example.dtbo').write_bytes(b'overlay')
        (self.root/'config.txt').write_text('dtoverlay=example\n')

    def test_stock_tree_inventory_has_hashes_without_contents(self):
        values = inventory(self.root, [])
        self.assertEqual([v['path'] for v in values], ['config.txt', 'overlays', 'overlays/example.dtbo'])
        self.assertEqual(values[-1]['sha256'], hashlib.sha256(b'overlay').hexdigest())
        self.assertNotIn('data', values[-1])

    def test_all_generated_artifact_names_are_rejected(self):
        for name in ('forge-recovery-abc.img', '.forge-image-abc', '.uconsole-forge-abc', 'RECOVERY.img'):
            path = self.root/name
            path.write_bytes(b'private')
            with self.assertRaisesRegex(ValueError, 'artifact requires separate review'): inventory(self.root, [])
            path.unlink()

    def test_renamed_known_credential_image_is_rejected(self):
        (self.root/'innocent.img').write_bytes(b'credential image')
        with self.assertRaisesRegex(ValueError, 'another name'):
            inventory(self.root, [hashlib.sha256(b'credential image').hexdigest()])

    def test_symlink_cannot_escape_inventory(self):
        (self.root/'alias').symlink_to('/etc')
        with self.assertRaisesRegex(ValueError, 'link, special'): inventory(self.root, [])

    def test_hardlink_is_refused(self):
        (self.root/'alias').hardlink_to(self.root/'config.txt')
        with self.assertRaisesRegex(ValueError, 'size/link'): inventory(self.root, [])
