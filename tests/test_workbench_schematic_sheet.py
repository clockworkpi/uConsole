import hashlib
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from workbench_schematic import COMPONENTS, BY_ID, REFERENCE_TOKENS
from workbench_schematic_sheet import load_manifest, Sheet


class AssetTests(unittest.TestCase):
    def test_provenance_and_all_reference_tokens(self):
        directory, manifest = load_manifest(ROOT)
        for component in COMPONENTS:
            sheet = manifest['sheets'][component.reference]
            self.assertEqual(sheet['sha256'], hashlib.sha256((ROOT / component.reference).read_bytes()).hexdigest())
            tokens = {word['text'] for page in sheet['pages'] for word in page['words']}
            for token in REFERENCE_TOKENS[component.id]:
                self.assertIn(token, tokens, (component.id, token))
            for page in sheet['pages']:
                self.assertEqual(page['image_sha256'], hashlib.sha256((directory / page['image']).read_bytes()).hexdigest())


class SheetGuiTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
        except ImportError as exc:
            self.skipTest(str(exc))
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))

    def tearDown(self):
        self.root.destroy()

    def test_sheet_search_zoom_and_component_link(self):
        opened = []
        sheet = Sheet(self.root, ROOT, BY_ID['keyboard'], opened.append)
        self.root.update()
        self.assertEqual(len(sheet.sheet['pages']), 2)
        # Keyboard MCU is on the second PDF page.
        sheet.page.current(1)
        sheet.change_page()
        self.root.update()
        sheet.query.set('GD32F103Rx')
        sheet.find()
        self.assertTrue(sheet.canvas.find_withtag('search'))
        sheet.resize(True)
        self.root.update()
        self.assertEqual(sheet.zoom, 1.)
        box, component = next((box, c) for box, c in sheet.link_boxes if c.id == 'keyboard')
        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        event = SimpleNamespace(x=int(x - sheet.canvas.canvasx(0)), y=int(y - sheet.canvas.canvasy(0)))
        sheet.press(event)
        sheet.release(event)
        self.assertEqual(opened[-1], component.sources[0])
        sheet.window.destroy()


if __name__ == '__main__':
    unittest.main()
