import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_guest_files import listing_script


class GuestFilesTests(unittest.TestCase):
    def test_json_names_and_bounded_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = {f'entry-{i}' for i in range(75)} | {'quote\'tab\tline\n', '雪' * 70}
            for name in names:
                (root / name).touch()
            (root / 'folder').mkdir()
            (root / 'symlink').symlink_to(root / 'folder', target_is_directory=True)
            observed = {}
            offset = 0
            while offset is not None:
                result = subprocess.run(listing_script(str(root), offset), shell=True,
                                        check=True, capture_output=True, text=True)
                self.assertLess(len(result.stdout.encode()), 16384)
                page = json.loads(result.stdout)
                self.assertLessEqual(len(page['entries']), 32)
                for entry in page['entries']:
                    self.assertNotIn(entry['name'], observed)
                    observed[entry['name']] = entry
                if page['next_offset'] is not None:
                    self.assertGreater(page['next_offset'], offset)
                offset = page['next_offset']
            self.assertEqual(set(observed), names | {'folder', 'symlink'})
            self.assertEqual(observed['folder']['type'], 'd')
            self.assertEqual(observed['symlink']['type'], 'l')

    def test_path_quoting_and_limits(self):
        with tempfile.TemporaryDirectory(prefix="uc-files-' ") as directory:
            script = listing_script(directory)
            result = subprocess.run(script, shell=True, check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(result.stdout)['entries'], [])
        for path, offset in [('relative', 0), ('/\x00', 0), ('/', True), ('/', -1),
                             ('/', 1000001), ('/' + 'x' * 2200, 0)]:
            with self.assertRaises(ValueError):
                listing_script(path, offset)


if __name__ == '__main__':
    unittest.main()
