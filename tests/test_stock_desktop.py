from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from repair_stock_desktop import BACKUP_SUFFIX, REPAIRED, STOCK, repair


class StockDesktopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'etc/skel/.config/pcmanfm/LXDE-pi/desktop-items-0.conf'
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(STOCK)

    def test_explicit_repair_backup_and_idempotence(self):
        self.assertEqual(repair(self.root)[0]['status'], 'eligible')
        self.assertEqual(self.path.read_bytes(), STOCK)
        self.path.chmod(0o640)
        self.assertEqual(repair(self.root, True)[0]['status'], 'repaired')
        self.assertEqual(self.path.read_bytes(), REPAIRED)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.path.with_name(self.path.name + BACKUP_SUFFIX).read_bytes(), STOCK)
        self.assertEqual(repair(self.root, True), [])

    def test_custom_configuration_is_preserved(self):
        self.path.write_bytes(STOCK.replace(b'Sans 20', b'Sans 21'))
        self.assertEqual(repair(self.root, True), [])
        self.assertIn(b'Sans 21', self.path.read_bytes())

    def test_existing_backup_is_never_overwritten(self):
        backup = self.path.with_name(self.path.name + BACKUP_SUFFIX)
        backup.write_bytes(b'original user data')
        self.assertEqual(repair(self.root, True)[0]['status'], 'skipped')
        self.assertEqual(backup.read_bytes(), b'original user data')
        self.assertEqual(self.path.read_bytes(), STOCK)

    def test_symlink_configuration_is_not_followed(self):
        target = self.root / 'unrelated'
        self.path.rename(target)
        self.path.symlink_to(target)
        self.assertEqual(repair(self.root, True), [])
        self.assertEqual(target.read_bytes(), STOCK)

    def test_cpi_home_preserves_stock_settings(self):
        (self.root / 'home/cpi').mkdir(parents=True)
        self.assertEqual(repair(self.root, True)[0]['status'], 'skipped')
        self.assertEqual(self.path.read_bytes(), STOCK)

    def test_existing_user_and_symlink_home(self):
        home = self.root / 'home/newuser'
        path = home / self.path.relative_to(self.root / 'etc/skel')
        path.parent.mkdir(parents=True)
        path.write_bytes(STOCK)
        (self.root / 'home/alias').symlink_to(home, target_is_directory=True)
        self.assertEqual(len(repair(self.root, True)), 2)
        self.assertEqual(path.read_bytes(), REPAIRED)


if __name__ == '__main__':
    unittest.main()
