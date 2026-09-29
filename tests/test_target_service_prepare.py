import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import forge_target_service_prepare as prepare
from forge_target_services import DEPENDENCIES
if __package__:
    from .target_test_support import identity
else:
    from target_test_support import identity


class ServicePreparationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'proof.service'
        self.source.write_text('[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/bin/true\n[Install]\nWantedBy=multi-user.target\n')
        self.output = self.root / 'review'
        self.unit = 'proof.service'
        self.identity = identity()
        self.services = dict(self.identity, schema=1, services={self.unit: {
            'Id': self.unit, 'LoadState': 'not-found', 'ActiveState': 'inactive', 'SubState': 'dead',
            'FragmentPath': '', 'DropInPaths': '', 'UnitFileState': '', 'Transient': 'no', 'NeedDaemonReload': 'no'}},
            dependencies={self.unit: {key: [] for key in DEPENDENCIES}},
            mutable_links={'roots': {'/etc/systemd/system': True, '/run/systemd/system': True}, 'links': []})
        self.files = {'schema': 1, 'machine_id': self.identity['machine_id'],
                      'files': [{'path': '/etc/systemd/system/proof.service', 'kind': 'absent'}]}

    def captures(self):
        return (patch.object(prepare, 'capture_services', side_effect=lambda host, names, output, **kw:
                             output.write_text(json.dumps(self.services))),
                patch.object(prepare, 'capture_files', side_effect=lambda host, paths, output:
                             output.write_text(json.dumps(self.files))))

    def test_readonly_preparation_retains_backup_pair_and_no_authorization(self):
        services, files = self.captures()
        with services as s, files as f:
            review = prepare.author(self.output, 'target', self.unit, self.source)
        self.assertEqual(s.call_count, 2)
        self.assertEqual(f.call_count, 2)
        self.assertFalse(review['deployment_performed'])
        self.assertFalse(review['authorization_performed'])
        self.assertTrue((self.output / 'transaction/transaction.json').exists())
        self.assertFalse((self.output / 'transaction/authorization.json').exists())
        self.assertEqual((self.output / 'review.json').stat().st_mode & 0o777, 0o600)

    def test_changed_service_capture_refuses_transaction(self):
        services, files = self.captures()
        def changed(host, names, output, **kwargs):
            if output.name == 'services-confirmed.json':
                self.services['dependencies'][self.unit]['Wants'] = ['other.service']
            output.write_text(json.dumps(self.services))
        with patch.object(prepare, 'capture_services', side_effect=changed), files:
            with self.assertRaisesRegex(ValueError, 'changed during'):
                prepare.author(self.output, 'target', self.unit, self.source)
        self.assertFalse((self.output / 'transaction').exists())
        self.assertTrue((self.output / 'incomplete.json').exists())

    def test_uncovered_alias_link_refused(self):
        self.services['mutable_links']['links'] = [{'path': '/etc/systemd/system/alias.service',
            'target': 'proof.service', 'uid': 0, 'gid': 0, 'mtime_ns': 1}]
        services, files = self.captures()
        with services, files, self.assertRaisesRegex(ValueError, 'alias links'):
            prepare.author(self.output, 'target', self.unit, self.source)
        self.assertFalse((self.output / 'transaction').exists())

    def test_invalid_source_fails_before_ssh_or_artifact_creation(self):
        with patch.object(prepare, 'capture_services') as capture, self.assertRaises(FileNotFoundError):
            prepare.author(self.output, 'target', self.unit, self.root / 'missing')
        capture.assert_not_called()
        self.assertFalse(self.output.exists())
