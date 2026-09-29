"""Private, durable job outcomes; records never establish runtime ownership."""
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time

APPLICATION_ID = 0x5543464A


def default_path():
    root = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    if not root.is_absolute():
        root = Path.home() / '.local/state'
    return root / 'uconsole-forge/jobs.sqlite3'


class History:
    def __init__(self, path):
        if os.name != 'posix':
            raise ValueError('Durable MCP history currently requires POSIX file permissions')
        path = Path(path).absolute()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.parent.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError('History directory must be private (0700) and owned by this user')
        fresh = False
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
            fresh = True
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_mode & 0o077 or info.st_nlink != 1):
            raise PermissionError('History must be a private, owned regular file, not a link')
        self.mutex = threading.Lock()
        self.db = sqlite3.connect(path, timeout=10, check_same_thread=False)
        try:
            if fresh:
                with self.db:
                    self.db.execute('BEGIN IMMEDIATE')
                    self.db.execute(f'PRAGMA application_id={APPLICATION_ID}')
                    self.db.execute('PRAGMA user_version=1')
                    self.db.execute('''CREATE TABLE jobs (
                        job_id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
                        operation TEXT NOT NULL, session TEXT NOT NULL,
                        created REAL NOT NULL, updated REAL NOT NULL,
                        status TEXT NOT NULL, outcome TEXT NOT NULL)''')
                    self.db.execute('CREATE INDEX jobs_workspace ON jobs(workspace, created DESC)')
            elif (self.db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID or
                  self.db.execute('PRAGMA user_version').fetchone()[0] != 1):
                raise ValueError('Not a supported uConsole forge history database')
            self.db.execute('PRAGMA synchronous=FULL')
        except BaseException:
            self.db.close()
            raise

    def create(self, job_id, workspace, operation, session, *, context=None):
        workspace = Path(workspace).resolve()
        now = time.time()
        outcome = {'status': 'queued'}
        if context is not None:
            outcome['context'] = context
        encoded = json.dumps(outcome)
        if len(encoded.encode()) > 256 * 1024:
            raise ValueError('Job context exceeds durable history limit (256 KiB)')
        with self.mutex, self.db:
            self.db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                            (job_id, str(workspace), operation, session, now, now,
                             'queued', encoded))

    def record(self, job_id, outcome):
        encoded = json.dumps(outcome)
        if len(encoded.encode()) > 256 * 1024:
            raise ValueError('Job outcome exceeds durable history limit (256 KiB)')
        with self.mutex, self.db:
            cursor = self.db.execute('UPDATE jobs SET updated=?, status=?, outcome=? WHERE job_id=?',
                                    (time.time(), outcome['status'], encoded, job_id))
            if cursor.rowcount != 1:
                raise ValueError('Cannot update an unknown history job')

    @staticmethod
    def public(row, include_result=False):
        job_id, workspace, operation, session, created, updated, status, outcome = row
        result = {'job_id': job_id, 'workspace_path': workspace, 'operation': operation,
                  'session': session, 'created': created, 'updated': updated,
                  'historical': True, 'recorded_status': status}
        if status in ('queued', 'running'):
            result.update(status='unresolved', detail='Recorded state is not evidence of live work. '
                          'Inspect the original owner and workspace before retrying; no runtime is adopted.')
            if include_result:
                context = json.loads(outcome).get('context')
                if context is not None:
                    result['context'] = context
        elif include_result:
            result.update(json.loads(outcome))
        else:
            result['status'] = status
        return result

    def get(self, job_id, workspaces):
        with self.mutex:
            row = self.db.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        if row is None or row[1] not in {str(Path(path).resolve()) for path in workspaces}:
            raise ValueError('Unknown job ID in registered workspaces')
        return self.public(row, include_result=True)

    def recent(self, workspace, limit=20, before=None):
        workspace = Path(workspace).resolve()
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('History limit must be between 1 and 100')
        with self.mutex:
            where = 'workspace=?'
            values = [str(workspace)]
            if before is not None:
                cursor = self.db.execute('SELECT created, job_id FROM jobs WHERE workspace=? AND job_id=?',
                                         (str(workspace), before)).fetchone()
                if cursor is None:
                    raise ValueError('Unknown history cursor in registered workspace')
                where += ' AND (created < ? OR (created = ? AND job_id < ?))'
                values += [cursor[0], cursor[0], cursor[1]]
            rows = self.db.execute('SELECT job_id, workspace, operation, session, created, updated, '
                                   'status, NULL FROM jobs WHERE ' + where +
                                   ' ORDER BY created DESC, job_id DESC LIMIT ?',
                                   [*values, limit]).fetchall()
        return [self.public(row) for row in rows]

    def close(self):
        with self.mutex:
            self.db.close()
