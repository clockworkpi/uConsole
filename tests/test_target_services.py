import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import forge_target_services as services
if __package__:
    from .target_test_support import target_identity_text
else:
    from target_test_support import target_identity_text


class TargetServiceTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(Path, 'read_text', target_identity_text))
        self.properties = {'Id': 'proof.service', 'LoadState': 'loaded', 'ActiveState': 'active',
                           'SubState': 'running', 'UnitFileState': 'enabled',
                           'FragmentPath': '/etc/systemd/system/proof.service', 'DropInPaths': '',
                           'NeedDaemonReload': 'no', 'Transient': 'no'}

    def result(self, **changes):
        return subprocess.CompletedProcess([], 0, '\n'.join(key + '=' + value for key, value in
                                                            dict(self.properties, **changes).items()), '')

    def test_capture_twice_uses_only_readonly_show(self):
        with patch.object(services.subprocess, 'run', return_value=self.result()) as run:
            record = services.capture_local(['proof.service'])
        self.assertEqual(services.verify(record, ['proof.service'])['services']['proof.service'], self.properties)
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertEqual(call.args[0][:3], ['systemctl', 'show', '--all'])
            self.assertEqual(call.args[0][-2:], ['--', 'proof.service'])

    def dependencies(self, **changes):
        values = {key: '' for key in services.DEPENDENCIES}
        values.update(changes)
        return '\n'.join(key + '=' + value for key, value in values.items())

    def test_dependency_capture_is_stable_and_includes_incoming_edges(self):
        output = self.result().stdout + '\n' + self.dependencies(
            Requires='sysinit.target system.slice', WantedBy='multi-user.target',
            TriggeredBy='proof.timer', After='dev-disk-by\\x2duuid.device')
        with patch.object(services.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output, '')) as run:
            record = services.capture_local(['proof.service'], with_dependencies=True)
        services.verify(record, ['proof.service'])
        self.assertEqual(record['dependencies']['proof.service']['WantedBy'], ['multi-user.target'])
        self.assertEqual(run.call_count, 2)
        self.assertIn('StopPropagatedFrom', run.call_args.args[0][3])

    def test_missing_dependency_property_is_not_treated_as_no_dependencies(self):
        output = self.dependencies().replace('Upholds=\n', '')
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            services.parse_dependencies(output)

    def test_dependency_changes_during_capture_fail(self):
        outputs = [self.result().stdout + '\n' + self.dependencies(Wants=unit)
                   for unit in ('first.service', 'second.service')]
        with patch.object(services.subprocess, 'run', side_effect=[
                subprocess.CompletedProcess([], 0, output, '') for output in outputs]):
            with self.assertRaisesRegex(ValueError, 'changed'):
                services.capture_local(['proof.service'], with_dependencies=True)

    def test_invalid_dependency_units_and_duplicate_properties_fail(self):
        for units in ('*.service', '--help', 'a.service a.service', 'bad/unit.service'):
            with self.subTest(units=units), self.assertRaises(ValueError):
                services.parse_dependencies(self.dependencies(Wants=units))
        with self.assertRaises(ValueError):
            services.parse_dependencies(self.dependencies() + '\nWants=')

    def test_dependency_order_is_canonical_but_instances_remain_literal(self):
        parsed = services.parse_dependencies(self.dependencies(Wants='z@one.service a.service'))
        self.assertEqual(parsed['Wants'], ['a.service', 'z@one.service'])

    def test_systemctl_quoted_device_words_preserve_literal_unit_escapes(self):
        unit = r'dev-disk-by\x2duuid-example.swap'
        parsed = services.parse_dependencies(self.dependencies(Requires=json.dumps(unit) + ' -.slice'))
        self.assertEqual(parsed['Requires'], ['-.slice', unit])
        for malformed in ('"unterminated', '"one.service""two.service"', '"one.service"suffix'):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                services.parse_dependencies(self.dependencies(Wants=malformed))

    def test_dependency_capture_cannot_silently_downgrade_old_worker_output(self):
        with patch.object(services.subprocess, 'run', return_value=self.result()):
            record = services.capture_local(['proof.service'])
        with tempfile.TemporaryDirectory() as directory, patch.object(services.subprocess, 'run',
                return_value=subprocess.CompletedProcess([], 0, json.dumps(record), '')):
            with self.assertRaisesRegex(ValueError, 'omitted'):
                services.capture('target', ['proof.service'], Path(directory) / 'capture.json', with_dependencies=True)

    def test_absent_is_not_confused_with_inactive_loaded(self):
        absent = self.result(LoadState='not-found', ActiveState='inactive', SubState='dead',
                             UnitFileState='', FragmentPath='')
        with patch.object(services.subprocess, 'run', return_value=absent):
            record = services.capture_local(['proof.service'])
        self.assertEqual(record['services']['proof.service']['LoadState'], 'not-found')

    def test_change_between_reads_refused(self):
        with patch.object(services.subprocess, 'run', side_effect=[self.result(), self.result(ActiveState='inactive')]):
            with self.assertRaises(ValueError):
                services.capture_local(['proof.service'])

    def test_transition_alias_reload_and_transient_refused(self):
        for changes in ({'ActiveState': 'activating'}, {'Id': 'other.service'},
                        {'NeedDaemonReload': 'yes'}, {'Transient': 'yes'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                services.parse_state('proof.service', self.result(**changes).stdout)

    def test_missing_duplicate_and_failed_properties_refused(self):
        for text in ('Id=proof.service', self.result().stdout + '\nId=proof.service'):
            with self.assertRaises(ValueError):
                services.parse_state('proof.service', text)
        with patch.object(services.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'denied')):
            with self.assertRaises(ValueError):
                services.capture_local(['proof.service'])

    def test_patterns_templates_and_options_refused_before_commands(self):
        for names in ([], ['*.service'], ['foo@bar.service'], ['--help'], ['proof.service'] * 2):
            with self.subTest(names=names), patch.object(services.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    services.capture_local(names)
                run.assert_not_called()

    def test_host_file_private_and_exclusive(self):
        with patch.object(services.subprocess, 'run', return_value=self.result()):
            record = services.capture_local(['proof.service'])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'services.json'
            result = subprocess.CompletedProcess([], 0, json.dumps(record), '')
            with patch.object(services.subprocess, 'run', return_value=result) as run:
                services.capture('clockworkpi.local', ['proof.service'], output)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(FileExistsError):
                    services.capture('clockworkpi.local', ['proof.service'], output)
                self.assertEqual(run.call_count, 1)

    def test_link_inventory_preserves_literal_targets_and_alias_chains(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wants = root / 'multi-user.target.wants'
            wants.mkdir()
            (root / 'alias.service').symlink_to('proof.service')
            (root / 'second.service').symlink_to('alias.service')
            (wants / 'second.service').symlink_to('../second.service')
            (wants / 'proof.service').symlink_to('/usr/lib/systemd/system/proof.service')
            (wants / 'unrelated.service').symlink_to('/usr/lib/systemd/system/unrelated.service')
            result = services.link_inventory(['proof.service'], [root])
            links = {item['path']: item['target'] for item in result['links']}
            self.assertEqual(links, {str(root / 'alias.service'): 'proof.service',
                str(root / 'second.service'): 'alias.service', str(wants / 'second.service'): '../second.service',
                str(wants / 'proof.service'): '/usr/lib/systemd/system/proof.service'})

    def test_missing_root_differs_from_empty_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = services.link_inventory(['proof.service'], [root, root / 'missing'])
            self.assertEqual(result['roots'], {str(root): True, str(root / 'missing'): False})
            self.assertEqual(result['links'], [])

    def test_linked_dependency_directory_is_not_followed_or_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / 'outside'
            outside.mkdir()
            (root / 'multi-user.target.wants').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'Linked dependency directory'):
                services.link_inventory(['proof.service'], [root])

    def test_link_changes_between_passes_fail_capture(self):
        with patch.object(services.subprocess, 'run', return_value=self.result()), \
                patch.object(services, 'link_inventory', side_effect=[{'links': []}, {'links': ['changed']}]):
            with self.assertRaises(ValueError):
                services.capture_local(['proof.service'])

    def test_link_inventory_validation_rejects_escape_and_duplicate_records(self):
        with patch.object(services.subprocess, 'run', return_value=self.result()):
            record = services.capture_local(['proof.service'])
        item = {'path': '/etc/systemd/system/proof.service', 'target': '/usr/lib/systemd/system/proof.service',
                'uid': 0, 'gid': 0, 'mtime_ns': 0}
        record['mutable_links']['links'] = [item, dict(item)]
        with self.assertRaises(ValueError):
            services.verify(record, ['proof.service'])
        item['path'] = '/etc/systemd/system/../outside'
        record['mutable_links']['links'] = [item]
        with self.assertRaises(ValueError):
            services.verify(record, ['proof.service'])
