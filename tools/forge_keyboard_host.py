"""Focus-scoped host keys to logical firmware contacts, with held-key identity."""
MATRIX_ROWS = (
    ('Select', 'Start', 'Volume', '`', '[', ']', '-', '='),
    tuple('12345678'), ('9', '0', 'Esc', 'Tab', '', '', '', ''),
    tuple('qwertyui'), tuple('opasdfgh'), tuple('jklzxcvb'),
    ('n', 'm', ',', '.', '/', '\\', ';', "'"),
    ('Backspace', 'Enter', 'Fn L', 'Fn R', 'Space', '', '', ''),
)
CONTACTS = {label: ('matrix', row, column)
            for row, labels in enumerate(MATRIX_ROWS)
            for column, label in enumerate(labels) if label}
ALIASES = {'Escape': 'Esc', 'Return': 'Enter', 'BackSpace': 'Backspace', 'space': 'Space',
           'grave': '`', 'bracketleft': '[', 'bracketright': ']', 'minus': '-', 'equal': '=',
           'comma': ',', 'period': '.', 'slash': '/', 'backslash': '\\',
           'semicolon': ';', 'apostrophe': "'"}
SHIFTED = dict(zip('!@#$%^&*()_+{}:"<>?~|', '1234567890-=[];\',./`\\'))
SHIFT_NAMES = dict(zip(
    ('exclam', 'at', 'numbersign', 'dollar', 'percent', 'asciicircum', 'ampersand',
     'asterisk', 'parenleft', 'parenright', 'underscore', 'plus', 'braceleft',
     'braceright', 'colon', 'quotedbl', 'less', 'greater', 'question', 'asciitilde', 'bar'),
    SHIFTED))
DIRECT = {name: ('key', index) for name, index in (
    ('Up', 0), ('Down', 1), ('Left', 2), ('Right', 3), ('Shift_L', 8), ('Shift_R', 9),
    ('Control_L', 10), ('Control_R', 11), ('Alt_L', 12), ('Alt_R', 14))}
DIRECT.update({'Mouse1': ('key', 13), 'Mouse2': ('key', 16), 'Mouse3': ('key', 15)})
FN, SHIFT = CONTACTS['Fn L'], ('key', 8)


def wheel_commands(notches):
    """Faithful Select-scroll: also emits Space or game button 9 in current firmware."""
    if type(notches) is not int or not -8 <= notches <= 8:
        raise ValueError('Wheel action must contain at most 8 signed notches')
    if not notches:
        return []
    direction = 2 if notches > 0 else 3
    commands = [('matrix', 0, 0, 1), ('run', 10)]
    for _ in range(abs(notches)):
        commands.extend([('edge', direction), ('edge', direction), ('run', 1)])
    return commands + [('matrix', 0, 0, 0), ('run', 10)]


def contacts(keysym):
    if keysym in DIRECT:
        return frozenset((DIRECT[keysym],))
    if keysym == 'Menu':
        return frozenset((FN,))
    if keysym.startswith('F') and keysym[1:].isdigit() and 1 <= int(keysym[1:]) <= 12:
        key = '1234567890-='[int(keysym[1:]) - 1]
        return frozenset((FN, CONTACTS[key]))
    character = SHIFT_NAMES.get(keysym, keysym)
    if character in SHIFTED:
        return frozenset((SHIFT, CONTACTS[SHIFTED[character]]))
    if len(character) == 1 and 'A' <= character <= 'Z':
        return frozenset((SHIFT, CONTACTS[character.lower()]))
    name = ALIASES.get(keysym, keysym)
    return frozenset((CONTACTS[name],)) if name in CONTACTS else frozenset()


class HostKeys:
    def __init__(self):
        self.down = {}

    @property
    def held(self):
        return set().union(*self.down.values()) if self.down else set()

    def change(self, keycode=None, keysym=None, *, release=False, clear=False):
        before = self.held
        updated = dict(self.down)
        if clear:
            updated.clear()
        elif release:
            updated.pop(keycode, None)  # Release the original contacts, even if keysym changed.
        elif keycode not in updated:
            mapped = contacts(keysym)
            if mapped:
                updated[keycode] = mapped
        after = set().union(*updated.values()) if updated else set()
        if len(updated) > 32 or len(after) > 32:
            raise ValueError('Host input held-contact limit exceeded')
        modifiers = {FN} | {('key', n) for n in (8, 9, 10, 11, 12, 14)}
        releases = sorted(before - after, key=lambda key: (key in modifiers, key))
        presses = sorted(after - before, key=lambda key: (key not in modifiers, key))
        commands = []
        # Let production scanners settle each transition so Fn is selected before
        # its character, independent of matrix wiring scan order.
        for value, changes in ((0, releases), (1, presses)):
            for contact in changes:
                commands.extend([(*contact, value), ('run', 10)])
        self.down = updated
        return commands, after


class HostPointer:
    """Four host pixels per edge; virtual scans retain production acceleration."""
    def __init__(self):
        self.anchor = None
        self.remainder = (0, 0)

    def reset(self):
        self.anchor = None
        self.remainder = (0, 0)

    def move(self, x, y):
        if self.anchor is None:
            self.anchor = (x, y)
            return []
        dx = x - self.anchor[0] + self.remainder[0]
        dy = y - self.anchor[1] + self.remainder[1]
        nx, ny = abs(dx) // 4, abs(dy) // 4
        if nx + ny > 128:
            raise ValueError('Pointer jump exceeds 128 firmware edges; input stopped')
        directions = [0 if dx < 0 else 1] * nx + [2 if dy < 0 else 3] * ny
        batches = []
        for offset in range(0, len(directions), 31):
            commands = []
            for direction in directions[offset:offset + 31]:
                commands.extend([('edge', direction), ('run', 1)])
            commands.append(('run', 10))
            batches.append(commands)
        self.anchor = (x, y)
        self.remainder = (dx - (nx * 4 if dx >= 0 else -nx * 4),
                          dy - (ny * 4 if dy >= 0 else -ny * 4))
        return batches
