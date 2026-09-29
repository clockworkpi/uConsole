#!/usr/bin/env python3
"""Render the repository's schematic sheets for offline Tk use (build-time Poppler)."""
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from workbench_schematic import COMPONENTS


def build(root):
    output = root / 'assets' / 'schematics'
    output.mkdir(parents=True, exist_ok=True)
    manifest = {'schema': 1, 'renderer': 'Poppler pdftoppm, 216 dpi', 'sheets': {}}
    for name in sorted({c.reference for c in COMPONENTS}):
        source = root / name
        xml = subprocess.check_output(['pdftotext', '-bbox', str(source), '-'])
        pages = ET.fromstring(xml).findall('.//{http://www.w3.org/1999/xhtml}page')
        records = []
        for number, page in enumerate(pages, 1):
            prefix = output / (source.stem + '-' + str(number))
            subprocess.run(['pdftoppm', '-f', str(number), '-l', str(number), '-singlefile',
                            '-r', '216', '-png', str(source), str(prefix)], check=True)
            words = []
            for word in page.findall('{http://www.w3.org/1999/xhtml}word'):
                words.append({'text': word.text or '', 'box': [float(word.attrib[k]) * 3
                              for k in ('xMin', 'yMin', 'xMax', 'yMax')]})
            records.append(dict(page=number, image=prefix.name + '.png',
                                image_sha256=hashlib.sha256(Path(str(prefix) + '.png').read_bytes()).hexdigest(),
                                width=float(page.attrib['width']) * 3,
                                height=float(page.attrib['height']) * 3, words=words))
        manifest['sheets'][name] = dict(sha256=hashlib.sha256(source.read_bytes()).hexdigest(), pages=records)
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + '\n')


if __name__ == '__main__':
    build(Path(__file__).resolve().parents[1])
