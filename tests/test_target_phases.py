import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import forge_target_phases as phases


class TargetPhaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name) / 'ledger'
        self.identity = {'machine_id': 'a' * 32, 'boot_id': '9542e0f8-0d83-4e63-9fb1-625830fe3519'}
        self.plan = dict(self.identity, schema=1, phases=[{
            'id': 'stop', 'before': {'stage': 0}, 'after': {'stage': 1},
            'action': {'operation': 'stop'}, 'repeatable': False}, {
            'id': 'final', 'before': {'stage': 1}, 'after': {'stage': 2},
            'action': {'operation': 'files-and-verify'}, 'repeatable': True}])
        self.stage = 0
        self.actions = []

    def act(self, phase):
        index = len(self.actions)
        self.assertTrue((self.directory / f'{index:04d}-intent.json').exists())
        self.actions.append(phase['id'])
        self.stage = phase['after']['stage']

    def run_plan(self, act=None):
        return phases.run(self.directory, self.plan, identity=lambda: self.identity,
                          observe=lambda phase: {'stage': self.stage}, act=act or self.act)

    def test_order_intent_before_effect_and_idempotent_completed_reentry(self):
        self.assertEqual(self.run_plan()['status'], 'verified')
        self.assertEqual(self.actions, ['stop', 'final'])
        self.run_plan()
        self.assertEqual(self.actions, ['stop', 'final'])

    def test_required_invocation_is_not_inferred_from_equal_properties(self):
        phase = self.plan['phases'][0]
        phase.update(before={'stage': 0}, after={'stage': 0}, repeatable=True)
        self.plan['phases'] = [phase]
        def execute():
            return phases.run(self.directory, self.plan, identity=lambda: self.identity,
                              observe=lambda p: {'stage': 0}, act=self.act, must_act=lambda p: True)
        execute()
        self.assertEqual(self.actions, ['stop'])
        execute()
        self.assertEqual(self.actions, ['stop'])

    def test_required_invocation_retries_unacknowledged_repeatable_intent(self):
        phase = self.plan['phases'][0]
        phase.update(before={'stage': 0}, after={'stage': 0}, repeatable=True)
        self.plan['phases'] = [phase]
        calls = []
        def interrupted(p):
            calls.append('interrupted')
            raise ConnectionError('effect completion unknown')
        kwargs = dict(identity=lambda: self.identity, observe=lambda p: {'stage': 0}, must_act=lambda p: True)
        with self.assertRaises(ConnectionError):
            phases.run(self.directory, self.plan, act=interrupted, **kwargs)
        phases.run(self.directory, self.plan, act=lambda p: calls.append('retried'), **kwargs)
        self.assertEqual(calls, ['interrupted', 'retried'])

    def test_effect_happened_but_ack_lost_is_observed_not_repeated(self):
        def lost_ack(phase):
            self.act(phase)
            raise ConnectionError('lost connection after effect')
        with self.assertRaises(ConnectionError):
            self.run_plan(lost_ack)
        self.assertFalse((self.directory / '0000-done.json').exists())
        self.run_plan()
        self.assertEqual(self.actions, ['stop', 'final'])

    def test_nonrepeatable_intent_with_original_state_blocks_retry(self):
        def uncertain(phase):
            raise ConnectionError('unknown outcome')
        with self.assertRaises(ConnectionError):
            self.run_plan(uncertain)
        with self.assertRaisesRegex(phases.PhaseUncertain, 'non-repeatable'):
            self.run_plan()
        self.assertEqual(self.actions, [])

    def test_repeatable_intent_can_retry_from_verified_before_state(self):
        self.plan['phases'][0]['repeatable'] = True
        with self.assertRaises(ConnectionError):
            self.run_plan(lambda phase: (_ for _ in ()).throw(ConnectionError('no ack')))
        self.run_plan()
        self.assertEqual(self.actions, ['stop', 'final'])

    def test_wrong_machine_or_boot_refused_before_ledger_creation(self):
        self.identity = dict(self.identity, machine_id='b' * 32)
        with self.assertRaises(phases.PhaseUncertain):
            self.run_plan()
        self.assertFalse(self.directory.exists())

    def test_reboot_during_effect_leaves_unacknowledged_intent(self):
        def reboot(phase):
            self.act(phase)
            self.identity = dict(self.identity, boot_id='12345678-1234-1234-1234-123456789abc')
        with self.assertRaises(phases.PhaseUncertain):
            self.run_plan(reboot)
        self.assertFalse((self.directory / '0000-done.json').exists())

    def test_changed_plan_refused_without_more_effects(self):
        self.run_plan()
        self.plan['phases'][0]['action']['operation'] = 'different'
        with self.assertRaises(phases.PhaseUncertain):
            self.run_plan()
        self.assertEqual(self.actions, ['stop', 'final'])

    def test_partial_acknowledgement_is_not_discarded(self):
        self.run_plan()
        (self.directory / '0001-done.json').write_text('{')
        with self.assertRaises(json.JSONDecodeError):
            self.run_plan()

    def test_final_state_drift_not_hidden_by_completed_journal(self):
        self.run_plan()
        self.stage = 99
        with self.assertRaisesRegex(phases.PhaseUncertain, 'postcondition'):
            self.run_plan()

    def test_boolean_is_not_equivalent_to_integer_observation(self):
        self.stage = False
        with self.assertRaises(phases.PhaseUncertain):
            self.run_plan()
        self.assertEqual(self.actions, [])

    def test_out_of_order_ledger_refused(self):
        self.run_plan()
        (self.directory / '0000-done.json').unlink()
        with self.assertRaisesRegex(phases.PhaseUncertain, 'out-of-order'):
            self.run_plan()

    def test_failed_postcondition_is_never_acknowledged(self):
        with self.assertRaisesRegex(phases.PhaseUncertain, 'not verified'):
            self.run_plan(lambda phase: None)
        self.assertTrue((self.directory / '0000-intent.json').exists())
        self.assertFalse((self.directory / '0000-done.json').exists())

    def test_real_process_exit_after_effect_recovers_without_repetition(self):
        script = '''
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from forge_target_phases import run
root = Path(sys.argv[2])
plan = json.loads(sys.argv[3])
state = root / 'observed-state'
effects = root / 'effects'
def observe(phase):
    return {'stage': int(state.read_text()) if state.exists() else 0}
def act(phase):
    with effects.open('a') as stream:
        stream.write(phase['id'] + '\\n')
    state.write_text(str(phase['after']['stage']))
    if sys.argv[4] == 'crash':
        os._exit(23)
run(root / 'ledger', plan, identity=lambda: {k:plan[k] for k in ('machine_id','boot_id')},
    observe=observe, act=act)
'''
        args = [sys.executable, '-c', script, str(Path(__file__).resolve().parents[1] / 'tools'),
                str(self.directory.parent), json.dumps(self.plan)]
        crashed = subprocess.run([*args, 'crash'], capture_output=True, text=True, timeout=5)
        self.assertEqual(crashed.returncode, 23, crashed.stderr)
        self.assertTrue((self.directory / '0000-intent.json').exists())
        self.assertFalse((self.directory / '0000-done.json').exists())
        resumed = subprocess.run([*args, 'resume'], capture_output=True, text=True, timeout=5)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual((self.directory.parent / 'effects').read_text().splitlines(), ['stop', 'final'])
