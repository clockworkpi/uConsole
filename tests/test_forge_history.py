"""Durable outcomes, private storage, reconnect scope and failure semantics."""
from concurrent.futures import Future, ThreadPoolExecutor
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_controller import Controller, JobCancelled
from forge_history import History


@unittest.skipUnless(os.name == 'posix', 'History currently enforces POSIX permissions')
class HistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'history/jobs.sqlite3'
        self.workspace = self.root / 'workspace'

    def controller(self, name='test'):
        return Controller({name: self.workspace}, history=self.path)

    def test_terminal_outcomes_survive_reconnect_without_grants(self):
        original = self.controller()
        try:
            context = {'argv': ['approved']}
            completed = original.submit('test', 'fixture', lambda: {'answer': 42}, context=context)
            context['argv'].append('changed-after-submit')
            original.jobs[completed['job_id']][2].result(timeout=5)
            failed = original.submit('test', 'fixture', lambda: (_ for _ in ()).throw(ValueError('failed')))
            with self.assertRaises(ValueError):
                original.jobs[failed['job_id']][2].result(timeout=5)
            cancelled = original.submit('test', 'fixture', lambda: (_ for _ in ()).throw(JobCancelled('stopped')))
            with self.assertRaises(JobCancelled):
                original.jobs[cancelled['job_id']][2].result(timeout=5)
        finally:
            original.close()
        reader = self.controller(name='renamed')
        try:
            state = reader.job(completed['job_id'])
            self.assertTrue(state['historical'])
            self.assertEqual(state['result'], {'answer': 42})
            self.assertEqual(state['context'], {'argv': ['approved']})
            self.assertEqual(reader.job(failed['job_id'])['status'], 'failed')
            self.assertEqual(reader.job(cancelled['job_id'])['status'], 'cancelled')
            listing = reader.call('job_history', {'workspace': 'renamed', 'limit': 2})
            self.assertEqual(len(listing['jobs']), 2)
            self.assertTrue(all('result' not in item for item in listing['jobs']))
            with self.assertRaisesRegex(ValueError, 'history is read-only'):
                reader.cancel(completed['job_id'])
            self.assertEqual(reader.runtimes, {})
        finally:
            reader.close()

    def test_nonterminal_records_are_unresolved_not_live_or_adopted(self):
        history = History(self.path)
        history.create('unfinished', self.workspace, 'boot', 'old-session')
        history.record('unfinished', {'status': 'running'})
        history.close()
        reader = self.controller()
        try:
            state = reader.job('unfinished')
            self.assertEqual(state['status'], 'unresolved')
            self.assertEqual(state['recorded_status'], 'running')
            self.assertEqual(reader.runtimes, {})
        finally:
            reader.close()

    def test_long_session_retires_only_durable_jobs_and_keeps_results(self):
        controller = self.controller()
        self.addCleanup(controller.close)
        ids = []
        for index in range(140):
            job_id = controller.submit('test', 'fixture', lambda: index,
                                       context={'index': index})['job_id']
            controller.jobs[job_id][2].result(timeout=5)
            ids.append(job_id)
        self.assertEqual(len(controller.jobs), 128)
        for mapping in (controller.job_controls, controller.job_context,
                        controller.job_finalized, controller.job_persisted):
            self.assertEqual(set(mapping), set(controller.jobs))
        for index, job_id in enumerate(ids):
            state = controller.job(job_id)
            self.assertEqual(state['status'], 'completed')
            self.assertEqual(state['result'], index)
            self.assertEqual(state['context'], {'index': index})
        self.assertTrue(controller.job(ids[0])['historical'])
        with self.assertRaisesRegex(ValueError, 'history is read-only'):
            controller.cancel(ids[0])

    def test_no_history_does_not_discard_results_to_make_room(self):
        controller = Controller({'test': self.workspace})
        self.addCleanup(controller.close)
        for index in range(128):
            job_id = controller.submit('test', 'fixture', lambda: index)['job_id']
            controller.jobs[job_id][2].result(timeout=5)
        work = MagicMock()
        with self.assertRaisesRegex(ValueError, 'no safely persisted terminal'):
            controller.submit('test', 'fixture', work)
        work.assert_not_called()
        self.assertEqual(len(controller.jobs), 128)

    def test_retirement_preserves_running_and_failed_persistence_jobs(self):
        controller = Controller({'test': self.workspace, 'busy': self.root / 'busy'},
                                history=self.path)
        self.addCleanup(controller.close)
        release = threading.Event()
        self.addCleanup(release.set)
        active = controller.submit('busy', 'waiting', lambda: release.wait(10))['job_id']
        record = controller.history.record

        def failing(job_id, outcome):
            if outcome['status'] == 'completed':
                raise OSError('disk full')
            record(job_id, outcome)

        with patch.object(controller.history, 'record', side_effect=failing):
            failed = controller.submit('test', 'fixture', lambda: 42)['job_id']
            with self.assertRaisesRegex(RuntimeError, 'history update failed'):
                controller.jobs[failed][2].result(timeout=5)
        for _ in range(130):
            job_id = controller.submit('test', 'fixture', lambda: 0)['job_id']
            controller.jobs[job_id][2].result(timeout=5)
        self.assertIn(active, controller.jobs)
        self.assertIn(failed, controller.jobs)
        self.assertEqual(controller.job(active)['status'], 'running')
        self.assertIn('disk full', controller.job(failed)['error'])
        self.assertEqual(controller.history.get(failed, [self.workspace])['status'], 'unresolved')
        release.set()
        controller.jobs[active][2].result(timeout=5)

    def test_failed_and_cancelled_outcomes_are_retirable(self):
        controller = self.controller()
        self.addCleanup(controller.close)
        ids = []
        for error in (ValueError('fixture failure'), JobCancelled('fixture cancel')):
            job_id = controller.submit('test', 'fixture',
                                       lambda: (_ for _ in ()).throw(error))['job_id']
            with self.assertRaises(type(error)):
                controller.jobs[job_id][2].result(timeout=5)
            ids.append(job_id)
        with patch.object(controller.executor, 'submit', return_value=Future()):
            queued = controller.submit('test', 'fixture', lambda: None)['job_id']
        controller.cancel(queued)
        ids.append(queued)
        for _ in range(128):
            job_id = controller.submit('test', 'fixture', lambda: None)['job_id']
            controller.jobs[job_id][2].result(timeout=5)
        for job_id, status in zip(ids, ('failed', 'cancelled', 'cancelled')):
            self.assertNotIn(job_id, controller.jobs)
            self.assertEqual(controller.job(job_id)['status'], status)
            self.assertTrue(controller.job(job_id)['historical'])

    def test_history_is_scoped_to_registered_canonical_workspace_paths(self):
        history = History(self.path)
        history.create('other', self.root / 'other', 'guest_exec', 'session')
        history.record('other', {'status': 'completed', 'result': {'secret': 'other workspace'}})
        history.close()
        reader = self.controller()
        try:
            with self.assertRaisesRegex(ValueError, 'registered workspaces'):
                reader.job('other')
            self.assertEqual(reader.call('job_history', {'workspace': 'test'})['jobs'], [])
        finally:
            reader.close()

    def test_workspace_aliases_share_history_without_expanding_scope(self):
        self.workspace.mkdir()
        alias = self.root / 'alias'
        alias.symlink_to(self.workspace, target_is_directory=True)
        history = History(self.path)
        self.addCleanup(history.close)
        history.create('alias-job', alias, 'fixture', 'session')
        history.record('alias-job', {'status': 'completed', 'result': 42})
        history.create('canonical-job', self.workspace.resolve(), 'fixture', 'session')
        for path in (alias, self.workspace, self.workspace.resolve()):
            self.assertEqual(history.get('alias-job', [path])['result'], 42)
            listing = history.recent(path)
            self.assertEqual({item['job_id'] for item in listing}, {'alias-job', 'canonical-job'})
            self.assertTrue(all(item['workspace_path'] == str(self.workspace.resolve()) for item in listing))
            self.assertEqual(len(history.recent(path, before=listing[0]['job_id'])), 1)
        alias.unlink()
        alias.mkdir()
        with self.assertRaisesRegex(ValueError, 'registered workspaces'):
            history.get('alias-job', [alias])
        self.assertEqual(history.recent(alias), [])

    def test_private_files_and_rejection_of_links_and_unrelated_databases(self):
        history = History(self.path)
        history.close()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        link = self.path.with_name('link.sqlite3')
        link.symlink_to(self.path)
        before = self.path.read_bytes()
        with self.assertRaises(PermissionError):
            History(link)
        self.assertEqual(self.path.read_bytes(), before)
        other = self.path.with_name('unrelated.sqlite3')
        with sqlite3.connect(other) as db:
            db.execute('CREATE TABLE unrelated (value TEXT)')
        other.chmod(0o600)
        before = other.read_bytes()
        with self.assertRaisesRegex(ValueError, 'supported uConsole'):
            History(other)
        self.assertEqual(other.read_bytes(), before)

    def test_queued_record_failure_prevents_execution(self):
        controller = self.controller()
        work = MagicMock()
        try:
            with patch.object(controller.history, 'create', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    controller.submit('test', 'fixture', work)
            work.assert_not_called()
            self.assertEqual(controller.jobs, {})
            self.assertEqual(controller.busy, {})
        finally:
            controller.close()

    def test_failed_result_persistence_does_not_claim_durable_success(self):
        controller = self.controller()
        committed = []
        record = controller.history.record

        def failing(job_id, outcome):
            if outcome['status'] == 'completed':
                raise OSError('disk full after operation')
            record(job_id, outcome)

        try:
            with patch.object(controller.history, 'record', side_effect=failing):
                job = controller.submit('test', 'fixture', lambda: committed.append('effect'))
                with self.assertRaisesRegex(RuntimeError, 'inspect effects before retrying'):
                    controller.jobs[job['job_id']][2].result(timeout=5)
            self.assertEqual(committed, ['effect'])
            self.assertEqual(controller.job(job['job_id'])['status'], 'failed')
        finally:
            controller.close()
        reader = self.controller()
        try:
            self.assertEqual(reader.job(job['job_id'])['status'], 'unresolved')
        finally:
            reader.close()

    def test_queued_cancellation_is_persisted(self):
        controller = self.controller()
        try:
            with patch.object(controller.executor, 'submit', return_value=Future()):
                job = controller.submit('test', 'fixture', lambda: None)
            self.assertTrue(controller.cancel(job['job_id'])['cancelled'])
        finally:
            controller.close()
        reader = self.controller()
        try:
            self.assertEqual(reader.job(job['job_id'])['status'], 'cancelled')
        finally:
            reader.close()

    def test_terminal_result_waits_for_history_commit(self):
        controller = self.controller()
        entered, release = threading.Event(), threading.Event()
        record = controller.history.record

        def delayed(job_id, outcome):
            if outcome['status'] == 'completed':
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('fixture')
            record(job_id, outcome)

        try:
            with patch.object(controller.history, 'record', side_effect=delayed):
                job = controller.submit('test', 'fixture', lambda: 42)
                self.assertTrue(entered.wait(5))
                self.assertEqual(controller.job(job['job_id'])['status'], 'running')
                with self.assertRaisesRegex(ValueError, 'owns this workspace'):
                    controller.submit('test', 'next', lambda: None)
                release.set()
                controller.jobs[job['job_id']][2].result(timeout=5)
            self.assertEqual(controller.history.get(job['job_id'], [self.workspace])['result'], 42)
        finally:
            release.set()
            controller.close()

    def test_independent_connections_commit_without_losing_records(self):
        first, second = History(self.path), History(self.path)

        def write(history, prefix):
            for index in range(10):
                job_id = f'{prefix}-{index}'
                history.create(job_id, self.workspace, 'fixture', prefix)
                history.record(job_id, {'status': 'completed', 'result': index})

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(write, first, 'first'), pool.submit(write, second, 'second')]
                for future in futures:
                    future.result(timeout=20)
            self.assertEqual(len(first.recent(self.workspace, 100)), 20)
            self.assertEqual(second.get('first-9', [self.workspace])['result'], 9)
            self.assertEqual(first.get('second-9', [self.workspace])['result'], 9)
            seen, before = [], None
            while True:
                page = first.recent(self.workspace, 7, before)
                if not page:
                    break
                seen.extend(item['job_id'] for item in page)
                before = page[-1]['job_id']
            self.assertEqual(len(seen), 20)
            self.assertEqual(len(set(seen)), 20)
            with self.assertRaisesRegex(ValueError, 'Unknown history cursor'):
                first.recent(self.root / 'unregistered', 7, before)
        finally:
            first.close()
            second.close()
