"""Negative checks for the live composite keyboard acceptance procedure."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import validate_keyboard_guest as validator


class KeyboardGuestValidationTests(unittest.TestCase):
    def test_pointer_requires_motion_on_both_axes_and_ordered_button_releases(self):
        buttons = [{'type': 1, 'code': code, 'value': value} for code, value in
                   ((272, 1), (272, 0), (274, 1), (274, 0), (273, 1), (273, 0))]
        motion = [{'type': 2, 'code': axis, 'value': 1} for axis in (0, 1)]
        validator.verify_pointer_events({'profile': 'pointer', 'events': motion + buttons})
        for events in (buttons, motion[:1] + buttons, motion + buttons[::-1], motion + buttons[:-1]):
            with self.assertRaises(ValueError):
                validator.verify_pointer_events({'profile': 'pointer', 'events': events})

    def test_scroll_profile_requires_both_wheel_directions_and_space_side_effects(self):
        events = [{'type': 1, 'code': code, 'value': value} for code, value in
                  [(57, 1), (57, 0)] * 2 + [(272, 1), (272, 0), (274, 1), (274, 0), (273, 1), (273, 0)]]
        events += [{'type': 2, 'code': code, 'value': value}
                   for code, value in ((0, 1), (1, 1), (8, 1), (8, -1))]
        validator.verify_pointer_events({'profile': 'pointer-scroll', 'events': events})
        for missing in (57, 8):
            with self.assertRaises(ValueError):
                validator.verify_pointer_events({'profile': 'pointer-scroll',
                                                 'events': [e for e in events if e['code'] != missing]})

    def test_host_typing_requires_gui_before_touching_image(self):
        with patch.object(sys, 'argv', ['verify', '--image', 'missing.img',
                                      '--sha256', '0' * 64, '--output', 'unused', '--host-typing']), \
                patch.object(validator, 'fixture') as fixture, \
                patch.object(validator, 'sha256') as digest, \
                patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                validator.main()
            fixture.assert_not_called()
            digest.assert_not_called()

    def test_caps_feedback_requires_both_sampled_led_and_adjusted_bytes(self):
        validator.verify_caps_report([{'report_hex': '020200390400000000', 'leds': 2}])
        for reports in ([], [{'report_hex': '020200390400000000', 'leds': 0}],
                        [{'report_hex': '020000390400000000', 'leds': 2}]):
            with self.assertRaisesRegex(ValueError, 'Caps LED'):
                validator.verify_caps_report(reports)

    def test_firmware_profile_requires_ordered_key_transitions(self):
        events = [{'type': 1, 'code': code, 'value': value}
                  for code, value in [(30, 1), (30, 0), (59, 1), (59, 0)]]
        validator.verify_firmware_events({'profile': 'firmware', 'events': events})
        for candidate in (events[::-1], events[:-1], events + events[:1]):
            with self.assertRaises(ValueError):
                validator.verify_firmware_events({'profile': 'firmware', 'events': candidate})
        with self.assertRaises(ValueError):
            validator.verify_firmware_events({'profile': 'raw', 'events': events})

    def test_wrong_base_hash_prevents_fixture_and_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'base.img'
            image.write_bytes(b'fixture')
            with patch.object(sys, 'argv', ['verify', '--image', str(image), '--sha256', '0' * 64,
                                          '--output', str(Path(directory) / 'output')]), \
                    patch.object(validator, 'fixture') as fixture, \
                    patch.object(validator, 'Runtime') as runtime:
                with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    validator.main()
                fixture.assert_not_called()
                runtime.assert_not_called()

    def exercise(self, observation, *, ready_failure=False):
        with tempfile.TemporaryDirectory() as directory:
            image, output = Path(directory) / 'base.img', Path(directory) / 'output'
            image.write_bytes(b'fixture')
            runtime = MagicMock(identity='fixture-runtime')
            runtime.process.poll.return_value = None
            runtime.process.returncode = 0
            runtime.execute.side_effect = [
                {'exit_code': 0, 'stdout': ''},
                {'exit_code': 0, 'stdout': json.dumps(observation)},
                {'exit_code': 0, 'stdout': ''},
            ]
            runtime.control.return_value = 'queued=0'
            runtime.stop.side_effect = lambda: setattr(runtime.process.poll, 'return_value', 0)
            with patch.object(sys, 'argv', ['verify', '--image', str(image),
                                          '--sha256', hashlib.sha256(b'fixture').hexdigest(),
                                          '--output', str(output)]), \
                    patch.object(validator, 'fixture', side_effect=lambda *args: output.mkdir()), \
                    patch.object(validator, 'Runtime', return_value=runtime) as factory, \
                    patch.object(validator, 'wait_for_log', side_effect=[None, TimeoutError('not ready')] if ready_failure else None), \
                    patch.object(validator, 'check_overlay_root', return_value={'clean_state': True}):
                if ready_failure:
                    with self.assertRaisesRegex(TimeoutError, 'not ready'):
                        validator.main()
                elif not observation['events'] or observation['drivers'] != ['usbhid', 'cdc_acm', 'cdc_acm']:
                    with self.assertRaises(ValueError):
                        validator.main()
                else:
                    validator.main()
            record = json.loads((output / 'acceptance.json').read_text())
            self.assertEqual(factory.call_args.args[0].keyboard, 'composite')
            self.assertEqual(factory.call_args.args[0].mode, 'maintenance')
            runtime.stop.assert_called_once_with()
            self.assertFalse(record['forced_cleanup'])
            return record, runtime

    @staticmethod
    def observation():
        return {'events': [{'type': kind, 'code': code, 'value': value}
                           for kind, code, value in validator.EXPECTED],
                'drivers': ['usbhid', 'cdc_acm', 'cdc_acm'], 'missing': []}

    def test_zero_exit_and_empty_missing_list_do_not_replace_event_evidence(self):
        observed = dict(self.observation(), events=[])
        record, _ = self.exercise(observed)
        self.assertEqual(record['validation'], 'failed')
        self.assertIn('evdev', record['error'])

    def test_wrong_drivers_do_not_pass_with_correct_events(self):
        record, _ = self.exercise(dict(self.observation(), drivers=['generic']))
        self.assertEqual(record['validation'], 'failed')
        self.assertIn('driver bindings', record['error'])

    def test_readiness_failure_retains_probe_and_injects_nothing(self):
        record, runtime = self.exercise(self.observation(), ready_failure=True)
        self.assertEqual(record['validation'], 'failed')
        self.assertIn('probe', record)
        runtime.control.assert_not_called()

    def test_success_requires_events_drivers_clean_stop_and_unchanged_base(self):
        record, runtime = self.exercise(self.observation())
        self.assertEqual(record['validation'], 'passed')
        self.assertTrue(record['base_unchanged'])
        self.assertEqual(len(record['reports']), 7)
