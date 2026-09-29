"""One-click Start qualification with a selected image and disposable workspace.

Exercises real import and maintenance boot; staged desktop preparation is covered
separately. Never targets a physical device or an existing writable workspace.
"""
import argparse
import json
from pathlib import Path
import time
import tkinter as tk
from unittest.mock import patch

from forge_workspace import sha256
from uconsole_workbench import Workbench


def run(image, expected, output, recommended=False, wizard=False):
    image, output = image.resolve(), output.resolve()
    if sha256(image) != expected:
        raise ValueError('Source image checksum mismatch')
    output.mkdir(mode=0o700)
    record = dict(status='failed', scope='Real public Start: image import and maintenance boot',
                  recommended_image=recommended, wizard=wizard)
    root = tk.Tk()
    app = Workbench(root, output / 'guest')
    app.mode.set('maintenance')
    app.display.set('gtk')
    app.show_setup()
    app.setup_panel.custom_image.set(not recommended)
    errors = []
    app.report_error = lambda action, exc: errors.append(f'{action}: {exc}')
    deadline = time.monotonic() + 300
    shutdown = False
    terminal_sent = False

    def poll():
        nonlocal shutdown, terminal_sent
        try:
            if errors:
                raise RuntimeError('; '.join(errors))
            if time.monotonic() > deadline:
                raise TimeoutError('Public Start validation timed out')
            panel = app.setup_panel
            if not panel.flow_active and not shutdown:
                if not panel.status.get().startswith('Emulator started'):
                    raise RuntimeError(panel.status.get())
                serial = (app.workspace / 'serial.log').read_text(errors='replace')
                if 'root@(none):/#' in serial:
                    if wizard and not terminal_sent:
                        if app.serial is None:
                            root.after(100, poll)
                            return
                        app.send_terminal(b"printf '\\033[2J\\033[H\\033[32mVT_PROOF\\033[0m\\n'\r")
                        terminal_sent = True
                        root.after(100, poll)
                        return
                    if wizard and not app.console.get('1.0', '1.8') == 'VT_PROOF':
                        root.after(100, poll)
                        return
                    if wizard:
                        if app.console.screen.buffer[0][0].fg != 'green':
                            raise RuntimeError('Guest terminal color rendering failed')
                        record['guest_terminal_input_cursor_color'] = True
                    record['startup_log'] = str(panel.flow_path)
                    record['emulator'] = json.loads((app.workspace / 'last-command.json').read_text())[0]
                    panel.close()
                    if app.process.poll() is not None:
                        raise RuntimeError('Closing startup progress stopped the guest')
                    record['close_keeps_guest_running'] = True
                    app.poweroff()
                    shutdown = True
            if shutdown and app.process is None:
                record.update(status='passed', clean_shutdown=True)
                root.quit()
            else:
                root.after(100, poll)
        except Exception as exc:
            errors.append(str(exc))
            root.quit()

    try:
        def consent(title, *args, **kwargs):
            if title == 'Set up the recommended uConsole desktop?':
                return True
            raise RuntimeError('Qualification must not authorize a host package install: ' + title)
        with patch('workbench_setup.messagebox.askyesno', side_effect=consent), \
                patch('uconsole_workbench.filedialog.askopenfilename', return_value=str(image)) as picker, \
                patch('uconsole_workbench.simpledialog.askstring', return_value=expected), \
                patch('uconsole_workbench.history_default_path', return_value=output / 'jobs.sqlite3'):
            if wizard:
                app.show_wizard()
                config = app.setup_wizard
                config.source.set('recommended' if recommended else 'custom')
                config.image.set(str(image))
                config.sha.set(expected)
                config.download.set(recommended)
                config.show(3)
                config.next()  # The sole Finish call; no dialogs or per-stage continuation.
            else:
                app.start()  # The sole Start call: no manual per-stage continuation.
            root.after(100, poll)
            root.mainloop()
            if recommended or wizard:
                picker.assert_not_called()
        if errors:
            raise RuntimeError('; '.join(errors))
        if sha256(image) != expected:
            raise ValueError('Source image changed')
        record['source_unchanged'] = True
    finally:
        if app.runtime and app.runtime.process and app.runtime.process.poll() is None:
            app.runtime.stop(force=True)
        if app.controller:
            app.controller.close()
        root.destroy()
        record['errors'] = errors
        (output / 'acceptance.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps(record), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--recommended', action='store_true', help='Use the default download/cache path without an image picker')
    parser.add_argument('--wizard', action='store_true', help='Exercise Finish and real guest VT rendering without further dialogs')
    args = parser.parse_args()
    run(args.image, args.sha256, args.output, args.recommended, args.wizard)
