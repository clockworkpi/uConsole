"""Bounded observation/effect adapters for the durable target phase runner.

Internal API: caller supplies reviewed scopes, holds the target transaction
lock, and invokes effects only through a durable phase ledger. No arbitrary
commands, enable/disable, unit-name patterns, or client-defined executables.
"""
from pathlib import Path
import os
import re
import subprocess

from forge_target_backup import paths_checked, verify
from forge_target_files import apply_file, parent_fd, snapshot as file_snapshot, recover_stage as recover_file
from forge_target_links import apply_link, snapshot as link_snapshot, validate as validate_link, recover_stage as recover_link
from forge_target_services import FIELDS, parse_state, service_names


def fingerprint(record):
    return {key: value for key, value in record.items() if key not in ('data', 'atime_ns')}


class TargetEffects:
    def __init__(self, *, services=(), files=(), links=()):
        self.services = frozenset(service_names(list(services)) if services else ())
        self.files = frozenset(paths_checked(list(files)) if files else ())
        self.links = frozenset(paths_checked(list(links)) if links else ())
        if self.files & self.links:
            raise ValueError('A target path cannot be both file and symlink scope')

    @staticmethod
    def identity():
        return {'machine_id': Path('/etc/machine-id').read_text().strip(),
                'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}

    def checked(self, phase):
        action = phase['action']
        operation = action.get('operation')
        required = {'operation', 'observe'}
        if operation in ('start', 'stop'):
            required.add('service')
            if action.get('service') not in self.services or phase['repeatable'] is not False:
                raise ValueError('Service start/stop requires scoped literal unit and non-repeatable phase')
        elif operation in ('file', 'link'):
            required |= {'expected', 'desired', 'stage_token'}
            if not re.fullmatch('[0-9a-f]{32}', action.get('stage_token', '')):
                raise ValueError('Invalid effect staging token')
            expected, desired = action['expected'], action['desired']
            if operation == 'file':
                for record in (expected, desired):
                    verify({'schema': 1, 'machine_id': '0' * 32, 'files': [record]}, [expected['path']])
                allowed = self.files
            else:
                validate_link(expected)
                validate_link(desired)
                allowed = self.links
            if expected['path'] != desired['path'] or expected['path'] not in allowed:
                raise ValueError('Effect path is not in owner-approved scope')
        elif operation not in ('daemon-reload', 'verify'):
            raise ValueError('Unsupported target effect')
        if set(action) != required:
            raise ValueError('Unexpected target effect arguments')
        scope = action['observe']
        if not isinstance(scope, dict) or set(scope) != {'services', 'files', 'links'}:
            raise ValueError('Effect requires explicit observation scopes')
        for key, allowed in (('services', self.services), ('files', self.files), ('links', self.links)):
            values = scope[key]
            if (not isinstance(values, list) or not all(isinstance(v, str) for v in values) or
                    len(set(values)) != len(values) or not set(values) <= allowed):
                raise ValueError('Observation escapes owner-approved scope')
        if operation in ('start', 'stop') and action['service'] not in scope['services']:
            raise ValueError('Service effect must observe its affected unit')
        if operation in ('file', 'link') and action['expected']['path'] not in scope['files' if operation == 'file' else 'links']:
            raise ValueError('File/link effect must observe its affected path')
        if operation == 'daemon-reload' and not scope['services']:
            raise ValueError('Daemon reload requires observable unit postconditions')
        return action

    def reconcile(self, phase):
        """Cleanup only verified scratch from a durable unacknowledged intent.

        Does not publish a new file or run any service command. Phase runner
        invokes this only for explicitly repeatable, already-intended effects.
        """
        action = self.checked(phase)
        if action['operation'] not in ('file', 'link'):
            return
        file = action['operation'] == 'file'
        recover, reader = (recover_file, file_snapshot) if file else (recover_link, link_snapshot)
        temporary = ('.uconsole-forge-' if file else '.uconsole-link-') + action['stage_token']
        desired = action['desired']
        with parent_fd(desired['path']) as (fd, name):
            staged = recover(fd, name, temporary, desired)
            if staged and fingerprint(reader(fd, name, desired['path'])) == fingerprint(desired):
                os.unlink(temporary, dir_fd=fd)
                os.fsync(fd)

    def observe(self, phase):
        action = self.checked(phase)
        result = {'services': {}, 'files': {}, 'links': {}}
        for name in action['observe']['services']:
            completed = self.command(['show', '--all', '--property=' + ','.join(FIELDS), '--', name])
            state = parse_state(name, completed.stdout, allow_reload=True)
            # Active state is the lifecycle contract; running vs exited depends
            # on service Type. Application health remains a separate gate.
            result['services'][name] = {key: value for key, value in state.items() if key != 'SubState'}
        for kind, reader in (('files', file_snapshot), ('links', link_snapshot)):
            for path in action['observe'][kind]:
                with parent_fd(path) as (fd, name):
                    result[kind][path] = fingerprint(reader(fd, name, path))
        return result

    @staticmethod
    def command(arguments):
        result = subprocess.run(['systemctl', *arguments], text=True, capture_output=True, timeout=60,
            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'SYSTEMD_PAGER': 'cat'})
        if result.returncode:
            raise RuntimeError('systemctl effect/query did not succeed: ' + result.stderr[-2000:])
        if len(result.stdout) > 65536:
            raise ValueError('systemctl output exceeds observation limit')
        return result

    def act(self, phase):
        action = self.checked(phase)
        operation = action['operation']
        if operation in ('start', 'stop'):
            return self.command([operation, '--', action['service']])
        if operation == 'daemon-reload':
            return self.command(['daemon-reload'])
        if operation in ('file', 'link'):
            function = apply_file if operation == 'file' else apply_link
            return function(action['expected'], action['desired'], stage_token=action['stage_token'])
        raise ValueError('Verification-only phase cannot invoke an effect')
