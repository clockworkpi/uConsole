import base64
import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_target_effects import fingerprint
from forge_target_service_compile import new_service, update_service
if __package__:
    from .target_test_support import identity
else:
    from target_test_support import identity


class ServiceCompileTests(unittest.TestCase):
    def setUp(self):
        self.unit = 'proof.service'
        path = '/etc/systemd/system/' + self.unit
        link = '/etc/systemd/system/multi-user.target.wants/' + self.unit
        absent = {'path': path, 'kind': 'absent'}
        absent_link = {'path': link, 'kind': 'absent'}
        service = {'Id': self.unit, 'LoadState': 'not-found', 'ActiveState': 'inactive',
                   'UnitFileState': '', 'FragmentPath': '', 'DropInPaths': '',
                   'NeedDaemonReload': 'no', 'Transient': 'no'}
        self.backup = {'identity': identity(), 'files': [absent], 'links': [absent_link],
                       'state': {'services': {self.unit: service}, 'files': {path: absent}, 'links': {link: absent_link}}}
        body = b'[Service]\nExecStart=/usr/bin/true\n'
        self.file = {'path': path, 'kind': 'file', 'data': base64.b64encode(body).decode(),
                     'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body),
                     'uid': 0, 'gid': 0, 'mode': 0o644, 'xattrs': {}, 'atime_ns': 1, 'mtime_ns': 1}
        self.link = {'path': link, 'kind': 'symlink', 'target': path, 'uid': 0, 'gid': 0, 'mtime_ns': 1}

    def compile(self):
        return new_service(self.backup, self.unit, self.file, self.link)

    def test_complete_pair_is_fixed_before_execution_and_restores_backup(self):
        pair = self.compile()
        self.assertEqual([p['action']['operation'] for p in pair['apply']['plan']['phases']],
                         ['verify', 'file', 'daemon-reload', 'link', 'daemon-reload', 'start', 'verify'])
        self.assertEqual([p['action']['operation'] for p in pair['restore']['plan']['phases']],
                         ['verify', 'stop', 'link', 'file', 'daemon-reload', 'verify'])
        self.assertEqual(pair['restore']['plan']['phases'][-1]['after'], self.backup['state'])
        tokens = [p['action']['stage_token'] for direction in ('apply', 'restore')
                  for p in pair[direction]['plan']['phases'] if 'stage_token' in p['action']]
        self.assertEqual(len(set(tokens)), 4)
        for direction in ('apply', 'restore'):
            for phase in pair[direction]['plan']['phases']:
                if phase['action']['operation'] in ('start', 'stop'):
                    self.assertFalse(phase['repeatable'])

    def test_existing_service_is_not_silently_treated_as_new(self):
        self.backup['state']['services'][self.unit]['LoadState'] = 'loaded'
        with self.assertRaisesRegex(ValueError, 'exact captured absence'):
            self.compile()

    def test_restore_reload_accepts_cached_loaded_unit_before_refresh(self):
        phase = self.compile()['restore']['plan']['phases'][-2]
        before = phase['before']['services'][self.unit]
        after = phase['after']['services'][self.unit]
        self.assertEqual(before['LoadState'], 'loaded')
        self.assertEqual(before['NeedDaemonReload'], 'yes')
        self.assertEqual(before['ActiveState'], 'inactive')
        self.assertEqual(after['LoadState'], 'not-found')
        self.assertEqual(after['NeedDaemonReload'], 'no')

    def test_wrong_unit_or_enablement_target_refused(self):
        self.link['target'] = '/etc/systemd/system/unrelated.service'
        with self.assertRaisesRegex(ValueError, 'selected standalone'):
            self.compile()

    def test_corrupt_payload_refused_before_preparation(self):
        self.file['sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            self.compile()

    def test_compiled_pair_is_isolated_from_mutable_input(self):
        pair = self.compile()
        expected = copy.deepcopy(pair)
        self.file['mode'] = 0o777
        self.link['target'] = '/other'
        self.backup['state'].clear()
        self.assertEqual(pair, expected)

    def existing(self, *, active=True, enabled=True):
        backup = copy.deepcopy(self.backup)
        backup['files'] = [copy.deepcopy(self.file)]
        backup['state']['files'][self.file['path']] = fingerprint(self.file)
        if enabled:
            backup['links'] = [copy.deepcopy(self.link)]
            backup['state']['links'][self.link['path']] = copy.deepcopy(self.link)
        backup['state']['services'][self.unit].update(
            LoadState='loaded', FragmentPath=self.file['path'], ActiveState='active' if active else 'inactive',
            UnitFileState='enabled' if enabled else 'disabled')
        return backup

    def test_update_pair_quiesces_before_replacement_and_restores_original_metadata(self):
        backup = self.existing()
        desired = dict(self.file, mode=0o640, mtime_ns=2)
        pair = update_service(backup, self.unit, desired, active=True)
        for direction in ('apply', 'restore'):
            self.assertEqual([p['action']['operation'] for p in pair[direction]['plan']['phases']],
                             ['verify', 'stop', 'file', 'daemon-reload', 'start', 'verify'])
        restore_file = pair['restore']['plan']['phases'][2]['action']['desired']
        self.assertEqual(restore_file, backup['files'][0])
        self.assertEqual(pair['restore']['plan']['phases'][-1]['after'], backup['state'])

    def test_inactive_disabled_service_returns_to_inactive_disabled(self):
        backup = self.existing(active=False, enabled=False)
        pair = update_service(backup, self.unit, dict(self.file, mode=0o640), active=True)
        self.assertEqual([p['action']['operation'] for p in pair['apply']['plan']['phases']],
                         ['verify', 'file', 'daemon-reload', 'start', 'verify'])
        self.assertEqual([p['action']['operation'] for p in pair['restore']['plan']['phases']],
                         ['verify', 'stop', 'file', 'daemon-reload', 'verify'])
        self.assertEqual(pair['restore']['plan']['phases'][-1]['after'], backup['state'])

    def test_update_refuses_dropins_unstable_state_and_mismatched_enablement(self):
        for key, value in (('DropInPaths', '/etc/systemd/system/proof.service.d/override.conf'),
                           ('NeedDaemonReload', 'yes'), ('LoadState', 'masked'), ('ActiveState', 'failed')):
            backup = self.existing()
            backup['state']['services'][self.unit][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                update_service(backup, self.unit, self.file, active=True)
        backup = self.existing()
        backup['links'][0]['target'] = '/unrelated.service'
        with self.assertRaises(ValueError):
            update_service(backup, self.unit, self.file, active=True)

    def test_update_leaving_service_stopped_still_restores_original_running_state(self):
        pair = update_service(self.existing(), self.unit, dict(self.file, mode=0o640), active=False)
        self.assertNotIn('start', [p['action']['operation'] for p in pair['apply']['plan']['phases']])
        self.assertNotIn('stop', [p['action']['operation'] for p in pair['restore']['plan']['phases']])
        self.assertIn('start', [p['action']['operation'] for p in pair['restore']['plan']['phases']])
