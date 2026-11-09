#!/usr/bin/env python3
"""Linux/Xvfb acceptance: launch an actual archive, edit/save, preserve bundle."""
import argparse
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import uuid


def editor_position(workspace_name):
    """Read widget geometry over Tk's local X test connection; do not mutate it."""
    import tkinter as tk
    inspector = tk.Tk()
    inspector.withdraw()
    try:
        for app in inspector.tk.call('winfo', 'interps'):
            try:
                title = inspector.tk.call('send', app, 'wm title .')
            except tk.TclError:
                continue
            if workspace_name not in title:
                continue
            script = '''
set pending [list .]
set found [list]
while {[llength $pending]} {
    set widget [lindex $pending 0]
    set pending [lrange $pending 1 end]
    foreach child [winfo children $widget] {lappend pending $child}
    if {[winfo class $widget] eq "Text"} {
        set parent [winfo parent $widget]
        if {[winfo class $parent] eq "TLabelframe" && [$parent cget -text] eq "Host source editor"} {
            lappend found [list [expr {[winfo rootx $widget]+10}] [expr {[winfo rooty $widget]+10}]]
        }
    }
}
if {[llength $found] != 1} {error "Expected one source editor"}
lindex $found 0
'''
            position = inspector.tk.call('send', app, script)
            return tuple(map(int, inspector.tk.splitlist(position)))
        raise AssertionError('Installed Workbench Tk application was not found')
    finally:
        inspector.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='uc-installed-') as directory:
        directory = Path(directory)
        with tarfile.open(args.archive.resolve()) as archive:
            archive.extractall(directory, filter='data')
        payload = directory / 'uconsole-workbench'
        source = payload / 'libexec/uconsole-workbench/Code/uconsole_keyboard/uconsole_keyboard.ino'
        original = source.read_bytes()
        data = directory / 'user-data'
        env = dict(os.environ, XDG_DATA_HOME=str(data))
        workspace_name = 'guest-' + uuid.uuid4().hex
        with (directory / 'gui.log').open('w') as log:
            process = subprocess.Popen([str(payload / 'bin/uconsole-workbench'),
                                        '--workspace', str(directory / workspace_name)],
                                       cwd=directory, env=env, stdout=log, stderr=log)
            try:
                window = subprocess.check_output([
                    'xdotool', 'search', '--sync', '--onlyvisible', '--name', workspace_name],
                    text=True, timeout=30).splitlines()[0]
                title = subprocess.check_output(['xdotool', 'getwindowname', window], text=True)
                assert 'Workbench' in title, title
                # Toolbar count and theme metrics change: locate the real editor.
                subprocess.run(['xdotool', 'windowfocus', '--sync', window], check=True)
                x, y = editor_position(workspace_name)
                subprocess.run(['xdotool', 'mousemove', str(x), str(y), 'click', '1'], check=True)
                subprocess.run(['xdotool', 'key', '--clearmodifiers', 'ctrl+Home'], check=True)
                proof = '// installed archive edit/save proof\n'
                subprocess.run(['xdotool', 'type', '--clearmodifiers', '--', proof], check=True)
                subprocess.run(['xdotool', 'key', '--clearmodifiers', 'ctrl+s'], check=True)
                sketch = data / 'uconsole-workbench/projects/uconsole_keyboard/uconsole_keyboard.ino'
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if sketch.exists() and sketch.read_bytes().startswith(proof.encode()):
                        break
                    if process.poll() is not None:
                        raise RuntimeError((directory / 'gui.log').read_text())
                    time.sleep(0.05)
                else:
                    raise AssertionError('Installed GUI did not save the keyboard project')
                assert source.read_bytes() == original, 'Bundled firmware source was modified'
                assert (sketch.parent / '.forge-origin.json').is_file()
                print('PASS: actual archive launch outside checkout, user-owned edit/save, immutable bundle')
            finally:
                # No guest was booted, and the test edit has already been saved.
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    main()
