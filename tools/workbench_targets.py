"""Saved SSH destinations are preferences, never deployment authorizations."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def destination(host, username=''):
    host, username = host.strip(), username.strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Enter a hostname, IPv4 address or SSH alias (without user@ or options).')
    if username and not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', username):
        raise ValueError('Enter a username without spaces or SSH options.')
    return f'{username}@{host}' if username else host


def default_path():
    root = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    if not root.is_absolute():
        root = Path.home() / '.config'
    return root / 'uconsole-workbench/ssh-targets.json'


def load(path):
    if not path.exists():
        return []
    rows = json.loads(path.read_text())
    if not isinstance(rows, list):
        raise ValueError('Invalid saved SSH targets')
    for row in rows:
        destination(row['host'], row['username'])
    return rows


def save(path, rows):
    for row in rows:
        destination(row['host'], row['username'])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.ssh-targets-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(rows, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def test_connection(host):
    # Same key/agent authentication as deployment, without sudo or target writes.
    username, separator, target = host.rpartition('@')
    host = destination(target, username if separator else '')
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                             '-o', 'ConnectTimeout=10', '-o', 'ConnectionAttempts=1',
                             host, 'true'], stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=15)
    if result.returncode:
        raise RuntimeError('SSH connection failed: ' + result.stderr[-2000:] +
                           '\nUse your terminal to establish trusted SSH key access first; '
                           'Workbench does not store passwords or accept unknown host keys.')
    return {'host': host, 'connected': True}
