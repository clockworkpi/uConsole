"""Owner-approved host commands, pinned to a reviewed policy-file digest.

This is an invocation policy, not a sandbox: approved build tools execute
mutable project code with the host user's filesystem permissions.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class HostTask:
    name: str
    workspace: str
    cwd: Path
    argv: tuple
    timeout: int


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate policy key: {key}')
        result[key] = value
    return result


class HostTasks:
    def __init__(self, path, expected_sha256, workspaces):
        if not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', expected_sha256):
            raise ValueError('Host-task policy requires an explicitly approved SHA-256')
        with Path(path).open('rb') as source:
            payload = source.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError('Host-task policy exceeds 1 MiB')
        self.sha256 = hashlib.sha256(payload).hexdigest()
        if self.sha256 != expected_sha256.lower():
            raise ValueError('Host-task policy does not match its approved SHA-256')
        data = json.loads(payload, object_pairs_hook=unique_object)
        if (not isinstance(data, dict) or set(data) != {'schema', 'tasks'} or
                type(data['schema']) is not int or data['schema'] != 1 or
                not isinstance(data['tasks'], dict) or not 1 <= len(data['tasks']) <= 64):
            raise ValueError('Host-task policy requires schema 1 and 1-64 task definitions')
        self.tasks = {}
        for name, item in data['tasks'].items():
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', name):
                raise ValueError('Invalid host-task name')
            if (not isinstance(item, dict) or set(item) - {'workspace', 'cwd', 'argv', 'timeout'} or
                    not {'workspace', 'cwd', 'argv'} <= set(item)):
                raise ValueError(f'Invalid definition for host task {name}')
            if len(json.dumps(item).encode()) > 16384:
                raise ValueError('Host-task definition exceeds 16 KiB')
            if not isinstance(item['workspace'], str) or item['workspace'] not in workspaces:
                raise ValueError(f'Host task {name} is not bound to a registered workspace')
            argv = item['argv']
            if (not isinstance(argv, list) or not 1 <= len(argv) <= 128 or
                    not all(isinstance(arg, str) and '\0' not in arg and len(arg) <= 4096 for arg in argv)):
                raise ValueError('Host task requires a bounded string argv array')
            if not isinstance(item['cwd'], str) or not Path(item['cwd']).is_absolute():
                raise ValueError('Host-task cwd must be absolute')
            cwd, executable = Path(item['cwd']).resolve(), Path(argv[0])
            if not cwd.is_dir() or not executable.is_absolute():
                raise ValueError('Host task requires an existing cwd and absolute executable')
            executable = executable.resolve()
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise ValueError('Host-task executable is not executable')
            timeout = item.get('timeout', 300)
            if type(timeout) is not int or not 1 <= timeout <= 1800:
                raise ValueError('Host-task timeout must be 1-1800 seconds')
            self.tasks[name] = HostTask(name, item['workspace'], cwd,
                                        (str(executable), *argv[1:]), timeout)
        # Do not automatically forward API credentials from a coding client's
        # environment. This is still not isolation from files or mutable code.
        self.environment = {key: os.environ[key] for key in
                            ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL',
                             'TMPDIR', 'XDG_CACHE_HOME') if key in os.environ}

    def get(self, name, workspace):
        task = self.tasks.get(name)
        if task is None or task.workspace != workspace:
            raise ValueError('Host task is not approved for this workspace')
        return task

    def describe(self, workspace):
        return [{'name': task.name, 'argv': list(task.argv), 'cwd': str(task.cwd),
                 'timeout': task.timeout, 'policy_sha256': self.sha256}
                for task in sorted(self.tasks.values(), key=lambda item: item.name)
                if task.workspace == workspace]
