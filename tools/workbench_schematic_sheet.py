"""Offline schematic-sheet drill-down using checked renderings of repository PDFs."""
import hashlib
import json
from pathlib import Path

from workbench_schematic import COMPONENTS, REFERENCE_TOKENS


def load_manifest(root):
    directory = Path(root) / 'assets' / 'schematics'
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest.get('schema') != 1:
        raise ValueError('Unsupported schematic asset manifest')
    return directory, manifest


class Sheet:
    def __init__(self, parent, root, component, navigate):
        import tkinter as tk
        from tkinter import ttk
        from workbench_layout import WrappingToolbar
        self.directory, manifest = load_manifest(root)
        self.sheet = manifest['sheets'][component.reference]
        self.component, self.navigate = component, navigate
        self.window = tk.Toplevel(parent)
        self.window.title('Schematic sheet — ' + component.label)
        self.window.geometry('1100x760')
        self.window.minsize(720, 480)
        self.zoom = .5
        self.page_index = 0
        self.note = tk.StringVar()
        ttk.Label(self.window, textvariable=self.note, wraplength=1050, padding=8).pack(fill='x')
        toolbar = WrappingToolbar(self.window, padding=6)
        toolbar.pack(fill='x')
        ttk.Label(toolbar, text='Page').pack(side='left')
        self.page = ttk.Combobox(toolbar, state='readonly', width=4,
                                 values=[str(p['page']) for p in self.sheet['pages']])
        self.page.current(0)
        self.page.pack(side='left')
        self.page.bind('<<ComboboxSelected>>', self.change_page)
        ttk.Button(toolbar, text='Zoom +', command=lambda: self.resize(True)).pack(side='left')
        ttk.Button(toolbar, text='Zoom −', command=lambda: self.resize(False)).pack(side='left')
        self.query = tk.StringVar(value=REFERENCE_TOKENS[component.id][0])
        search = ttk.Entry(toolbar, textvariable=self.query, width=22)
        search.pack(side='left', padx=6)
        search.bind('<Return>', lambda event: self.find())
        ttk.Button(toolbar, text='Find component / net', command=self.find).pack(side='left')
        ttk.Button(toolbar, text='Open component code',
                   command=lambda: self.navigate(component.sources[0])).pack(side='left')
        self.status = tk.StringVar(value='Drag to pan. Blue boxes link to implementation; other text is inspection only.')
        ttk.Label(self.window, textvariable=self.status, wraplength=1000, padding=6).pack(side='bottom', fill='x')
        area = ttk.Frame(self.window)
        area.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(area, background='#dbe2eb', highlightthickness=0)
        horizontal = ttk.Scrollbar(area, orient='horizontal', command=self.canvas.xview)
        vertical = ttk.Scrollbar(area, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        horizontal.pack(side='bottom', fill='x')
        vertical.pack(side='right', fill='y')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', lambda event: self.canvas.scan_dragto(event.x, event.y, gain=1))
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.load_page()
        self.find_timer = self.window.after_idle(self.initial_find)
        self.window.bind('<Destroy>', self.destroyed)

    def initial_find(self):
        self.find_timer = None
        self.find()

    def destroyed(self, event):
        if event.widget is self.window and self.find_timer is not None:
            self.window.after_cancel(self.find_timer)
            self.find_timer = None

    def change_page(self, event=None):
        self.page_index = self.page.current()
        self.load_page()

    def load_page(self):
        import tkinter as tk
        page = self.sheet['pages'][self.page_index]
        path = (self.directory / page['image']).resolve()
        if not path.is_relative_to(self.directory.resolve()) or path.suffix != '.png':
            raise ValueError('Unsafe schematic image path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != page['image_sha256']:
            raise ValueError('Schematic rendering checksum mismatch')
        self.original = tk.PhotoImage(master=self.window, file=str(path))
        self.draw()

    def resize(self, larger):
        scales = (.25, .5, 1., 2.)
        position = scales.index(self.zoom)
        self.zoom = scales[min(3, max(0, position + (1 if larger else -1)))]
        self.draw()
        self.find()

    def draw(self):
        self.image = self.original.zoom(2) if self.zoom == 2 else (
            self.original if self.zoom == 1 else self.original.subsample(round(1 / self.zoom)))
        self.canvas.delete('all')
        self.canvas.create_image(0, 0, image=self.image, anchor='nw')
        self.canvas.configure(scrollregion=(0, 0, self.image.width(), self.image.height()))
        self.links = {}
        self.link_boxes = []
        for index, word in enumerate(self.sheet['pages'][self.page_index]['words']):
            matching = [c for c in COMPONENTS if c.reference == self.component.reference
                        and word['text'] in REFERENCE_TOKENS[c.id]]
            if matching:
                tag = 'link:' + str(index)
                self.links[tag] = matching[0]
                self.link_boxes.append(([v * self.zoom for v in word['box']], matching[0]))
                self.canvas.create_rectangle(*[v * self.zoom for v in word['box']],
                                             outline='#2563eb', width=2, tags=(tag,))
        self.note.set(f'{self.component.reference} • sheet {self.page_index + 1}/{len(self.sheet["pages"])}\n'
                      f'Source PDF SHA-256: {self.sheet["sha256"]}\n'
                      'Printed revision is retained in the title block. Shared-board circuits do not prove CM4 population.')

    def find(self):
        self.canvas.delete('search')
        needle = self.query.get().strip().casefold()
        matches = [word for word in self.sheet['pages'][self.page_index]['words']
                   if needle and needle in word['text'].casefold()]
        for word in matches:
            self.canvas.create_rectangle(*[v * self.zoom for v in word['box']],
                                         outline='#ea580c', width=2, tags=('search',))
        # Keep clickable implementation outlines above the search highlights.
        for tag in self.links:
            self.canvas.tag_raise(tag)
        if matches:
            x1, y1, x2, y2 = matches[0]['box']
            self.canvas.xview_moveto(max(0, ((x1 + x2) * self.zoom / 2 - self.canvas.winfo_width() / 2)
                                         / self.image.width()))
            self.canvas.yview_moveto(max(0, ((y1 + y2) * self.zoom / 2 - self.canvas.winfo_height() / 2)
                                         / self.image.height()))
        self.status.set(f'{len(matches)} text matches on this sheet. Blue boxes open functional implementation copies.')

    def press(self, event):
        self.anchor = event.x, event.y
        self.canvas.scan_mark(event.x, event.y)

    def release(self, event):
        if abs(event.x - self.anchor[0]) + abs(event.y - self.anchor[1]) > 4:
            return
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for (x1, y1, x2, y2), component in self.link_boxes:
            if x1 - 2 <= x <= x2 + 2 and y1 - 2 <= y <= y2 + 2:
                self.status.set(component.label + ' → ' + component.sources[0].label + ' (functional mapping)')
                self.navigate(component.sources[0])
                return
