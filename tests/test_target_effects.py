import base64
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_target_effects import TargetEffects, fingerprint
from forge_target_phases import run, PhaseUncertain
if __package__:
    from .target_test_support import linux_target
else:
    from target_test_support import linux_target


class TargetEffectTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.identity = {'machine_id': 'a' * 32, 'boot_id': '9542e0f8-0d83-4e63-9fb1-625830fe3519'}
        self.effects = TargetEffects(services=['proof.service'])
        self.scope = {'services': ['proof.service'], 'files': [], 'links': []}
        self.state = {'Id': 'proof.service', 'LoadState': 'loaded', 'ActiveState': 'inactive',
                      'SubState': 'dead', 'UnitFileState': 'disabled', 'FragmentPath': '/etc/systemd/system/proof.service',
                      'DropInPaths': '', 'NeedDaemonReload': 'no', 'Transient': 'no'}

    def service_phase(self, operation='start'):
        before = {'services': {'proof.service': {k: v for k, v in self.state.items() if k != 'SubState'}},
                  'files': {}, 'links': {}}
        after = {'services': {'proof.service': dict(before['services']['proof.service'], ActiveState='active')},
                 'files': {}, 'links': {}}
        return {'id': 'service', 'before': before, 'after': after, 'repeatable': False,
                'action': {'operation': operation, 'service': 'proof.service', 'observe': self.scope}}

    def run_phase(self, phase):
        return run(self.root / 'ledger', dict(self.identity, schema=1, phases=[phase]),
                   identity=lambda: self.identity, observe=self.effects.observe, act=self.effects.act)

    def command(self, argv, **kwargs):
        if argv[1] == 'start':
            self.state['ActiveState'] = 'active'
        output = '\n'.join(k + '=' + v for k, v in self.state.items()) if argv[1] == 'show' else ''
        return subprocess.CompletedProcess(argv, 0, output, '')

    def test_service_command_runs_only_between_verified_observations(self):
        phase = self.service_phase()
        with patch('forge_target_effects.subprocess.run', side_effect=self.command) as command:
            self.assertEqual(self.run_phase(phase)['status'], 'verified')
            self.run_phase(phase)
        starts = [call for call in command.call_args_list if call.args[0][1] == 'start']
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0].args[0], ['systemctl', 'start', '--', 'proof.service'])
        self.assertNotIn('shell', starts[0].kwargs)

    def test_failed_start_stays_uncertain_and_is_not_repeated(self):
        phase = self.service_phase()
        def failure(argv, **kwargs):
            if argv[1] == 'start':
                return subprocess.CompletedProcess(argv, 1, '', 'start failed')
            return self.command(argv, **kwargs)
        with patch('forge_target_effects.subprocess.run', side_effect=failure) as command:
            with self.assertRaises(RuntimeError):
                self.run_phase(phase)
            with self.assertRaises(PhaseUncertain):
                self.run_phase(phase)
        self.assertEqual(len([c for c in command.call_args_list if c.args[0][1] == 'start']), 1)

    def test_scope_overrides_enable_and_repeatable_start_refused(self):
        for change in ('scope', 'enable', 'repeatable', 'extra'):
            phase = self.service_phase()
            if change == 'scope':
                phase['action']['service'] = 'ssh.service'
            elif change == 'enable':
                phase['action']['operation'] = 'enable'
            elif change == 'repeatable':
                phase['repeatable'] = True
            else:
                phase['action']['argv'] = ['arbitrary']
            with self.subTest(change=change), patch('forge_target_effects.subprocess.run') as command:
                with self.assertRaises(ValueError):
                    self.effects.act(phase)
                command.assert_not_called()

    def test_pending_reload_observable_without_relaxing_backup_probe(self):
        from forge_target_services import parse_state
        self.state['NeedDaemonReload'] = 'yes'
        phase = self.service_phase()
        with patch('forge_target_effects.subprocess.run', side_effect=self.command):
            self.assertEqual(self.effects.observe(phase)['services']['proof.service']['NeedDaemonReload'], 'yes')
        with self.assertRaises(ValueError):
            parse_state('proof.service', '\n'.join(k + '=' + v for k, v in self.state.items()))

    def test_reload_has_no_client_arguments_and_requires_unit_observation(self):
        phase = self.service_phase()
        phase['action'] = {'operation': 'daemon-reload', 'observe': self.scope}
        phase['repeatable'] = True
        with patch('forge_target_effects.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as command:
            self.effects.act(phase)
            self.assertEqual(command.call_args.args[0], ['systemctl', 'daemon-reload'])
        phase['action']['observe'] = {'services': [], 'files': [], 'links': []}
        with self.assertRaises(ValueError):
            self.effects.act(phase)

    @linux_target
    def test_real_file_effect_through_ledger(self):
        path = str(self.root / 'application')
        self.effects = TargetEffects(files=[path])
        data = b'proof'
        absent = {'kind': 'absent', 'path': path}
        desired = {'kind': 'file', 'path': path, 'data': base64.b64encode(data).decode(),
                   'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'mode': 0o644,
                   'uid': os.getuid(), 'gid': os.getgid(), 'mtime_ns': 1000000000, 'atime_ns': 1000000000, 'xattrs': {}}
        phase = {'id': 'file', 'repeatable': True,
                 'before': {'services': {}, 'files': {path: fingerprint(absent)}, 'links': {}},
                 'after': {'services': {}, 'files': {path: fingerprint(desired)}, 'links': {}},
                 'action': {'operation': 'file', 'observe': {'services': [], 'files': [path], 'links': []},
                            'expected': absent, 'desired': desired, 'stage_token': 'b' * 32}}
        self.assertEqual(self.run_phase(phase)['status'], 'verified')
        self.assertEqual(Path(path).read_bytes(), data)

    def test_real_link_effect_through_ledger(self):
        path = str(self.root / 'proof.service')
        self.effects = TargetEffects(links=[path])
        absent = {'kind': 'absent', 'path': path}
        desired = {'kind': 'symlink', 'path': path, 'target': '../proof.service',
                   'uid': os.getuid(), 'gid': os.getgid(), 'mtime_ns': 1000000000}
        phase = {'id': 'link', 'repeatable': True,
                 'before': {'services': {}, 'files': {}, 'links': {path: absent}},
                 'after': {'services': {}, 'files': {}, 'links': {path: desired}},
                 'action': {'operation': 'link', 'observe': {'services': [], 'files': [], 'links': [path]},
                            'expected': absent, 'desired': desired, 'stage_token': 'b' * 32}}
        self.assertEqual(self.run_phase(phase)['status'], 'verified')
        self.assertEqual(os.readlink(path), '../proof.service')
