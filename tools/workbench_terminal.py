"""Tk rendering and keyboard transport for the bundled pyte VT terminal engine."""
import copy
import hashlib
from pathlib import Path
import sys
import tkinter as tk
from tkinter import font
from tkinter import messagebox

WHEELS = {
    'pyte-0.8.2-py3-none-any.whl': '85db42a35798a5aafa96ac4d8da78b090b2c933248819157fc0e6f78876a0135',
    'wcwidth-0.2.13-py2.py3-none-any.whl': '3da69048e4540d84af32131829ff948f1e022c1c6bdb8d6102117aac784f6859',
}
for name, digest in WHEELS.items():
    path = Path(__file__).parent / 'vendor' / name
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise RuntimeError('Bundled terminal dependency checksum mismatch: '+name)
    sys.path.insert(0, str(path))
import pyte


class Screen(pyte.Screen):
    """Add xterm alternate-screen switching without changing the dependency."""
    def __init__(self, *args):
        self.primary = None
        self.reply = lambda data: None
        super().__init__(*args)

    def write_process_input(self, data):
        self.reply(data.encode('utf-8'))

    def set_mode(self, *modes, **kwargs):
        if kwargs.get('private') and any(mode in (47, 1047, 1049) for mode in modes):
            if self.primary is None:
                self.primary = copy.deepcopy((self.buffer, self.cursor, self.margins))
                self.erase_in_display(2)
                self.cursor_position()
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes, **kwargs):
        if kwargs.get('private') and any(mode in (47, 1047, 1049) for mode in modes):
            if self.primary is not None:
                self.buffer, self.cursor, self.margins = self.primary
                self.primary = None
                self.dirty.update(range(self.lines))
        super().reset_mode(*modes, **kwargs)


PALETTE = {'default': '#d8e4ef', 'black': '#151a20', 'red': '#ef5350',
           'green': '#66bb6a', 'brown': '#d4b45c', 'blue': '#6495ed',
           'magenta': '#ce93d8', 'cyan': '#4dd0e1', 'white': '#eeeeee',
           'brightblack': '#777777', 'brightred': '#ff7777', 'brightgreen': '#99ff99',
           'brightbrown': '#ffff99', 'brightblue': '#9999ff', 'brightmagenta': '#ff99ff',
           'brightcyan': '#99ffff', 'brightwhite': '#ffffff'}


def color(value, background=False):
    if value == 'default' and background:
        return '#151a20'
    if value in PALETTE:
        return PALETTE[value]
    if len(value) == 6 and all(c in '0123456789abcdefABCDEF' for c in value):
        return '#'+value
    return PALETTE['default']


class Terminal(tk.Text):
    def __init__(self, parent, send):
        super().__init__(parent, wrap='none', state='disabled', font='TkFixedFont',
                         width=80, height=24, background='#151a20', foreground='#d8e4ef',
                         insertbackground='white', takefocus=True)
        self.send = send
        self.screen = Screen(80, 24)
        self.stream = pyte.Stream(self.screen)
        self.styles = {}
        self.fonts = {}
        self.bind('<KeyPress>', self.key)
        self.bind('<<Paste>>', self.paste)
        self.bind('<Control-Shift-V>', self.paste)
        self.bind('<Control-Shift-C>', self.copy)
        self.bind('<Button-1>', lambda event: self.focus_set(), add='+')
        self.render()

    def feed(self, text, reply=None):
        self.screen.reply = reply or (lambda data: None)
        try:
            self.stream.feed(text)
        finally:
            self.screen.reply = lambda data: None
        self.render()

    def reset_terminal(self):
        self.screen = Screen(80, 24)
        self.stream = pyte.Stream(self.screen)
        self.render()

    def render(self):
        self.configure(state='normal')
        if self.index('end-1c') == '1.0':
            self.insert('1.0', (' '*80+'\n')*23+' '*80)
        for row in sorted(self.screen.dirty):
            self.delete(f'{row+1}.0', f'{row+1}.end')
            cells = [self.screen.buffer[row][column] for column in range(80)]
            offset = 0
            for cell in cells:
                if not cell.data:  # Continuation cell of a wide character.
                    continue
                key = (cell.fg, cell.bg, cell.bold, cell.italics, cell.underscore, cell.reverse)
                tag = self.styles.get(key)
                if tag is None and len(self.styles) < 4096:
                    tag = 'ansi'+str(len(self.styles))
                    self.styles[key] = tag
                    fg, bg = color(cell.fg), color(cell.bg, True)
                    style = (cell.bold, cell.italics)
                    if style not in self.fonts:
                        face = font.nametofont('TkFixedFont').copy()
                        face.configure(weight='bold' if cell.bold else 'normal',
                                       slant='italic' if cell.italics else 'roman')
                        self.fonts[style] = face
                    self.tag_configure(tag, foreground=bg if cell.reverse else fg,
                                       background=fg if cell.reverse else bg, underline=cell.underscore,
                                       font=self.fonts[style])
                self.insert(f'{row+1}.{offset}', cell.data, (tag,) if tag else ())
                offset += len(cell.data)
        self.screen.dirty.clear()
        cursor = self.screen.cursor
        column = len(''.join(self.screen.buffer[cursor.y][x].data for x in range(min(cursor.x, 80))))
        self.mark_set('insert', f'{cursor.y+1}.{column}')
        self.tag_remove('terminal_cursor', '1.0', 'end')
        if not cursor.hidden:
            self.tag_configure('terminal_cursor', background='#d8e4ef', foreground='#151a20')
            self.tag_add('terminal_cursor', 'insert', 'insert + 1 chars')
        self.configure(state='disabled')

    def key(self, event):
        key = event.keysym
        if event.state & 4 and event.state & 1 and key.lower() in ('c', 'v'):
            return self.copy() if key.lower() == 'c' else self.paste()
        application = (1 << 5) in self.screen.mode
        prefix = '\x1bO' if application else '\x1b['
        mapping = {'Up': prefix+'A', 'Down': prefix+'B', 'Right': prefix+'C', 'Left': prefix+'D',
                   'Home': '\x1b[H', 'End': '\x1b[F', 'Insert': '\x1b[2~', 'Delete': '\x1b[3~',
                   'Prior': '\x1b[5~', 'Next': '\x1b[6~', 'Return': '\r', 'KP_Enter': '\r',
                   'BackSpace': '\x7f', 'Tab': '\t', 'Escape': '\x1b',
                   'F1': '\x1bOP', 'F2': '\x1bOQ', 'F3': '\x1bOR', 'F4': '\x1bOS',
                   **{f'F{i}': f'\x1b[{n}~' for i, n in zip(range(5, 13), (15, 17, 18, 19, 20, 21, 23, 24))}}
        value = mapping.get(key, event.char)
        if event.state & 4 and len(key) == 1 and '@' <= key.upper() <= '_':
            value = chr(ord(key.upper()) & 31)
        if value:
            self.send(value.encode('utf-8'))
        return 'break'

    def copy(self, event=None):
        try:
            value = self.get('sel.first', 'sel.last')
        except tk.TclError:
            return 'break'
        self.clipboard_clear()
        self.clipboard_append(value)
        return 'break'

    def paste(self, event=None):
        value = self.clipboard_get()
        if len(value.encode('utf-8')) > 65536:
            raise ValueError('Terminal paste is limited to 64 KiB')
        if any(ord(c) < 32 for c in value) and not messagebox.askyesno(
                'Paste into guest terminal?', 'This paste contains control characters or newlines and may execute commands. Continue?', parent=self):
            return 'break'
        self.send(value.replace('\r\n', '\n').replace('\n', '\r').encode('utf-8'))
        return 'break'
