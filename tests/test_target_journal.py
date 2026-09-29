import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import forge_target_journal as journal


class TargetJournalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / 'transaction'
        self.before = {'schema': 1, 'machine_id': 'a' * 32,
                       'files': [{'path': '/usr/local/bin/proof', 'kind': 'absent'}]}

    def prepare(self):
        return journal.prepare(self.path, 'jkh@clockworkpi.local', self.before, self.before)

    def test_private_durable_plan_with_distinct_restore_tokens(self):
        result = self.prepare()
        self.assertEqual(result['status'], 'prepared')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.path / 'plan.json').stat().st_mode & 0o777, 0o600)
        with journal.locked(self.path) as (fd, plan):
            self.assertNotEqual(plan['stage_tokens']['apply'], plan['stage_tokens']['restore'])
            self.assertEqual(journal.events(fd), [])
            journal.append_event(fd, {'state': 'dispatch', 'direction': 'apply'})
            journal.append_event(fd, {'state': 'uncertain', 'direction': 'apply'})
        with journal.locked(self.path) as (fd, plan):
            self.assertEqual([event['state'] for event in journal.events(fd)], ['dispatch', 'uncertain'])

    def test_existing_transaction_never_overwritten(self):
        self.prepare()
        original = (self.path / 'plan.json').read_bytes()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual((self.path / 'plan.json').read_bytes(), original)

    def test_invalid_plan_creates_no_directory(self):
        other = copy.deepcopy(self.before)
        other['machine_id'] = 'b' * 32
        with self.assertRaises(ValueError):
            journal.prepare(self.path, 'clockworkpi.local', self.before, other)
        self.assertFalse(self.path.exists())

    def test_parallel_dispatch_refused(self):
        self.prepare()
        with journal.locked(self.path):
            with self.assertRaises(BlockingIOError):
                with journal.locked(self.path):
                    self.fail('concurrent writer acquired journal')

    def test_partial_event_blocks_further_dispatch(self):
        self.prepare()
        pending = self.path / 'event-000000.json'
        pending.touch(mode=0o600)
        with journal.locked(self.path) as (fd, _):
            with self.assertRaises(json.JSONDecodeError):
                journal.append_event(fd, {'state': 'dispatch', 'direction': 'restore'})
        self.assertFalse((self.path / 'event-000001.json').exists())

    def test_permissions_links_and_event_gaps_refused(self):
        self.prepare()
        plan = self.path / 'plan.json'
        plan.chmod(0o644)
        with self.assertRaises(PermissionError):
            with journal.locked(self.path):
                pass
        plan.chmod(0o600)
        os.link(plan, self.root / 'alias')
        with self.assertRaises(PermissionError):
            with journal.locked(self.path):
                pass
        (self.root / 'alias').unlink()
        (self.path / 'event-000001.json').touch(mode=0o600)
        with journal.locked(self.path) as (fd, _):
            with self.assertRaises(ValueError):
                journal.events(fd)


if __name__ == '__main__':
    unittest.main()
