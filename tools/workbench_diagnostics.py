"""Private JSON-lines diagnostics, independent of guest transcripts and job journals."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys
import threading
import traceback


def default_path():
    root = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    if not root.is_absolute():
        root = Path.home() / '.local/state'
    return root / 'uconsole-workbench' / 'application.jsonl'


class Diagnostics:
    def __init__(self, workspace, path=None):
        self.workspace = str(workspace)
        self.path = Path(path) if path is not None else default_path()
        self.lock = threading.Lock()
        self.reported_jobs = set()

    def emit(self, event, action, error, *, job_id=None):
        record = {'timestamp': datetime.now(timezone.utc).isoformat(), 'level': 'ERROR',
                  'application': 'uconsole-workbench', 'event': event, 'action': action,
                  'workspace': self.workspace, 'error_type': type(error).__name__, 'message': str(error)}
        if job_id is not None:
            record['job_id'] = str(job_id)
        if isinstance(error, BaseException) and error.__traceback__:
            # Frame locations only; no locals, source lines or command payloads.
            record['frames'] = [{'file': frame.filename, 'line': frame.lineno, 'function': frame.name}
                                for frame in traceback.extract_tb(error.__traceback__)]
        line = json.dumps(record, ensure_ascii=True) + '\n'
        try:
            sys.stderr.write(line)
            sys.stderr.flush()
        except (OSError, AttributeError):
            pass
        try:
            with self.lock:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                info = self.path.parent.lstat()
                if not stat.S_ISDIR(info.st_mode) or (os.name == 'posix' and
                        (info.st_uid != os.getuid() or info.st_mode & 0o077)):
                    raise PermissionError('Diagnostic directory must be private and owned by this user')
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT |
                             getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), 0o600)
                with os.fdopen(fd, 'a', encoding='utf-8') as output:
                    info = os.fstat(output.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (os.name == 'posix' and
                            (info.st_uid != os.getuid() or info.st_mode & 0o077)):
                        raise PermissionError('Diagnostic log must be a private owned regular file')
                    output.write(line)
            return line + f'\nLog: {self.path}'
        except OSError as exc:
            fallback = json.dumps({'timestamp': record['timestamp'], 'level': 'ERROR',
                                   'event': 'diagnostic_write_failed', 'message': str(exc),
                                   'path': str(self.path)})
            try:
                sys.stderr.write(fallback + '\n')
            except (OSError, AttributeError):
                pass
            return line + f'\nCould not persist diagnostics: {exc}\nRequested log: {self.path}'

    def recent(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                         getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (os.name == 'posix' and
                        (info.st_uid != os.getuid() or info.st_mode & 0o077)):
                    raise PermissionError('Diagnostic log must be a private owned regular file')
                source.seek(0, 2)
                size = source.tell()
                source.seek(max(0, size - 128 * 1024))
                if size > 128 * 1024:
                    source.readline()
                return source.read().decode('utf-8', errors='replace')
        except FileNotFoundError:
            return 'No application errors recorded yet.'


def details_window(parent, title, text, help_command=None):
    import tkinter as tk
    from tkinter import ttk
    from tkinter.scrolledtext import ScrolledText
    window = tk.Toplevel(parent)
    window.title(title)
    window.geometry('800x420')
    window.minsize(600, 260)
    ttk.Label(window, text=title, padding=10, font=('TkDefaultFont', 14, 'bold')).pack(anchor='w')
    body = ScrolledText(window, wrap='word', font='TkFixedFont', padx=10, pady=10)
    body.insert('1.0', text)
    body.configure(state='disabled')
    bar = ttk.Frame(window, padding=10)
    # Reserve the actions before giving the transcript the remaining space.
    # Otherwise Text's requested height can push Copy/Close off the window.
    bar.pack(side='bottom', fill='x')
    body.pack(fill='both', expand=True, padx=10)
    copied = tk.StringVar(value='')
    def copy():
        window.clipboard_clear()
        window.clipboard_append(text)
        copied.set('Copied')
    ttk.Button(bar, text='Copy text', command=copy).pack(side='left')
    if help_command:
        ttk.Button(bar, text='Troubleshooting guide', command=help_command).pack(side='left', padx=6)
    ttk.Label(bar, textvariable=copied).pack(side='left', padx=6)
    ttk.Button(bar, text='Close', command=window.destroy).pack(side='right')
    window.bind('<Escape>', lambda event: window.destroy())
    return window


def ui_diagnostics(widget):
    while widget is not None:
        logger = getattr(widget, '_workbench_diagnostics', None)
        if logger is not None:
            return logger
        widget = getattr(widget, 'master', None)
    return None


def report_ui_error(widget, action, error):
    logger = ui_diagnostics(widget)
    if logger is not None:
        logger.emit('action_failed', action, error)


def report_job_error(widget, result):
    logger = ui_diagnostics(widget)
    if logger is None:
        return
    payload = result.get('result') or {}
    code = payload.get('exit_code', payload.get('returncode', 0)) if isinstance(payload, dict) else 0
    failed = result.get('status') == 'failed' or (result.get('status') == 'completed' and code != 0)
    job_id = result.get('job_id')
    if failed and job_id not in logger.reported_jobs:
        logger.reported_jobs.add(job_id)
        # Do not copy job results or command streams into the application log.
        logger.emit('job_failed', result.get('operation', 'background job'),
                    result.get('error') or f'Command exited with status {code}; inspect retained job evidence',
                    job_id=job_id)
