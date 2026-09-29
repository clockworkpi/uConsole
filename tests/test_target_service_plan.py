import base64
import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_target_service_plan import compile_recipe
from forge_target_journal import validate_plan
from forge_target_services import DEPENDENCIES


class ServiceOrderingTests(unittest.TestCase):
    def setUp(self):
        self.path = '/etc/systemd/system/proof.service'
        self.state = {'Id': 'proof.service', 'LoadState': 'not-found', 'ActiveState': 'inactive',
                      'SubState': 'dead', 'UnitFileState': '', 'FragmentPath': '', 'DropInPaths': '',
                      'Transient': 'no', 'NeedDaemonReload': 'no'}
        self.snapshot = {'schema': 1, 'machine_id': 'a' * 32,
                         'boot_id': '9542e0f8-0d83-4e63-9fb1-625830fe3519',
                         'services': {'proof.service': self.state}}
        self.before = {'schema': 1, 'machine_id': 'a' * 32,
                       'files': [{'path': self.path, 'kind': 'absent'}]}
        data = b'[Service]\nExecStart=/usr/bin/true\n'
        self.file = {'path': self.path, 'kind': 'file', 'data': base64.b64encode(data).decode(),
                     'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                     'uid': 0, 'gid': 0, 'mode': 0o644, 'atime_ns': 0, 'mtime_ns': 0, 'xattrs': {}}
        self.after = dict(self.before, files=[self.file])
        self.desired = {'proof.service': {'present': True, 'active': True, 'enabled': True}}

    def compile(self):
        return compile_recipe(self.snapshot, self.before, self.after, self.desired)

    def test_order_stops_before_files_and_reloads_before_enable_start(self):
        plan = self.compile()
        self.assertEqual([step['action'] for step in plan['apply']], [
            'verify-file-and-service-preimages', 'stop-if-loaded', 'disable-if-loaded',
            'apply-journaled-files', 'daemon-reload', 'enable', 'start', 'verify-file-and-service-results'])
        self.assertEqual(plan['apply'][5]['services'], ['proof.service'])
        self.assertEqual(plan['apply'][6]['services'], ['proof.service'])
        self.assertFalse(plan['dispatchable'])

    def test_restoring_absence_disables_before_removing_unit_file(self):
        plan = self.compile()
        self.assertEqual(plan['restore'][2]['services'], ['proof.service'])
        self.assertEqual(plan['restore'][3]['direction'], 'restore')
        self.assertEqual(plan['restore'][5]['services'], [])
        self.assertEqual(plan['restore'][6]['services'], [])

    def test_dependency_review_preserves_edges_and_does_not_grant_outside_units(self):
        edges = {key: [] for key in DEPENDENCIES}
        edges.update(Requires=['sysinit.target'], WantedBy=['multi-user.target'])
        self.snapshot['dependencies'] = {'proof.service': edges}
        plan = self.compile()
        review = plan['dependency_review']
        self.assertTrue(review['captured'])
        self.assertFalse(review['execution_approved'])
        self.assertEqual(review['related_units_outside_selection'], ['multi-user.target', 'sysinit.target'])
        self.assertFalse(plan['dispatchable'])
        edges['Requires'].clear()
        self.assertEqual(review['edges']['proof.service']['Requires'], ['sysinit.target'])

    def test_missing_dependency_capture_is_explicit_not_empty_graph(self):
        review = self.compile()['dependency_review']
        self.assertFalse(review['captured'])
        self.assertIsNone(review['edges'])

    def test_transitive_graph_must_match_target_boot_and_selected_edges(self):
        edges = {key: [] for key in DEPENDENCIES}
        self.snapshot['dependencies'] = {'proof.service': edges}
        graph = {'schema': 1, 'roots': ['proof.service'], 'properties': list(DEPENDENCIES),
                 'complete': True, 'execution_approved': False, 'nodes': {'proof.service': copy.deepcopy(edges)},
                 'machine_id': self.snapshot['machine_id'], 'boot_id': self.snapshot['boot_id']}
        def compile_graph():
            return compile_recipe(self.snapshot, self.before, self.after, self.desired, dependency_graph=graph)
        plan = compile_graph()
        self.assertEqual(plan['dependency_review']['transitive_graph'], graph)
        self.assertFalse(plan['dispatchable'])
        graph['boot_id'] = '00000000-0000-0000-0000-000000000000'
        with self.assertRaisesRegex(ValueError, 'different target or boot'):
            compile_graph()
        graph['boot_id'] = self.snapshot['boot_id']
        edges['Before'] = ['proof.service']
        with self.assertRaisesRegex(ValueError, 'differs from selected'):
            compile_graph()

    def test_existing_enabled_active_service_restores_running_state(self):
        self.state.update(LoadState='loaded', ActiveState='active', SubState='running',
                          UnitFileState='enabled', FragmentPath=self.path)
        self.before['files'] = [copy.deepcopy(self.file)]
        self.desired['proof.service'].update(active=False, enabled=False)
        plan = self.compile()
        self.assertEqual(plan['apply'][2]['services'], ['proof.service'])
        self.assertEqual(plan['apply'][6]['services'], [])
        self.assertEqual(plan['restore'][5]['services'], ['proof.service'])
        self.assertEqual(plan['restore'][6]['services'], ['proof.service'])

    def test_missing_dropin_backup_refused(self):
        self.state.update(LoadState='loaded', UnitFileState='disabled', FragmentPath=self.path,
                          DropInPaths='/etc/systemd/system/proof.service.d/options.conf')
        self.before['files'] = [copy.deepcopy(self.file)]
        with self.assertRaisesRegex(ValueError, 'missing from file backup'):
            self.compile()

    def test_cross_target_and_inconsistent_presence_refused(self):
        self.snapshot['machine_id'] = 'b' * 32
        with self.assertRaises(ValueError):
            self.compile()
        self.snapshot['machine_id'] = 'a' * 32
        self.desired['proof.service'] = {'present': False, 'active': False, 'enabled': False}
        with self.assertRaises(ValueError):
            self.compile()

    def test_failed_and_masked_units_not_silently_normalized(self):
        for field, value in [('ActiveState', 'failed'), ('LoadState', 'masked')]:
            original = self.state[field]
            self.state[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.compile()
            self.state[field] = original

    def test_file_executor_rejects_recipe_and_unknown_actions(self):
        with self.assertRaises(ValueError):
            validate_plan(self.compile())
        plan = {'schema': 1, 'host': 'host', 'before': self.before, 'after': self.after,
                'stage_tokens': {'apply': ['b' * 32], 'restore': ['c' * 32]},
                'services': self.desired}
        with self.assertRaisesRegex(ValueError, 'cannot silently ignore'):
            validate_plan(plan)
