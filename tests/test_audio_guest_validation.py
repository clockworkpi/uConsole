import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import validate_audio_guest as validation


class AudioGuestValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / 'guest'
        self.runtime = MagicMock()
        self.runtime.process.poll.return_value = None
        self.runtime.process.returncode = 0
        self.runtime.stop.side_effect = lambda **kwargs: setattr(self.runtime.process.poll, 'return_value', 0)
        self.runtime.execute.side_effect = [
            {'exit_code': 0, 'stdout': '/usr/bin/aplay'},
            {'exit_code': 0, 'stdout': json.dumps({'status': 'played'})},
            {'exit_code': 0, 'stdout': ''}]
        for name, options in (
                ('sha256', {'return_value': 'a' * 64}),
                ('fixture', {'side_effect': lambda image, output, digest: output.mkdir()}),
                ('Runtime', {'return_value': self.runtime}),
                ('audio_operation', {'return_value': {'observed': {}}}),
                ('wait_for_log', {}), ('check_overlay_root', {'return_value': {'clean': True}})):
            patcher = patch.object(validation, name, **options)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(validation.shutil, 'disk_usage', return_value=MagicMock(free=1024 ** 3))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_guest(self):
        return validation.run(self.root / 'base.img', 'a' * 64, self.output)

    def test_repetition_bounds_precede_fixture(self):
        for count in (0, 11, True, 1.5):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'Repetitions'):
                validation.run(self.root / 'base.img', 'a' * 64, self.output, repetitions=count)
        validation.fixture.assert_not_called()

    def test_duplex_requires_both_directions_and_exact_wav_check(self):
        for options in ({}, {'wav': True, 'direct_alsa': True}, {'wav': True, 'usbmon': True}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'Duplex qualification'):
                validation.run(self.root / 'base.img', 'a' * 64, self.output, duplex=True, **options)
        validation.fixture.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'acknowledge playback'):
            validation.run(self.root / 'base.img', 'a' * 64, self.output, duplex=True, wav=True)
        self.assertEqual(validation.Runtime.call_args.args[0].audio, 'usb-duplex')
        self.assertEqual(json.loads((self.output / 'audio-acceptance.json').read_text())['status'], 'failed')

    def test_later_playback_failure_cannot_pass(self):
        self.runtime.execute.side_effect = [
            {'exit_code': 0}, {'exit_code': 0, 'stdout': '{"status":"played"}'},
            {'exit_code': 1, 'stderr': 'second drain failed'}]
        with self.assertRaisesRegex(ValueError, 'Repeated ALSA'):
            validation.run(self.root / 'base.img', 'a' * 64, self.output, repetitions=3)
        evidence = json.loads((self.output / 'audio-acceptance.json').read_text())
        self.assertEqual(evidence['status'], 'failed')
        self.assertEqual(evidence['repeated_playback'][0]['exit_code'], 1)
        self.runtime.stop.assert_called_once_with()

    def test_success_requires_probe_clean_shutdown_and_unchanged_base(self):
        evidence = self.run_guest()
        self.assertEqual(evidence['status'], 'passed')
        self.assertTrue(evidence['base_unchanged'])
        self.assertFalse(evidence['forced_cleanup'])
        self.runtime.stop.assert_called_once_with()
        validation.check_overlay_root.assert_called_once()

    def test_probe_failure_retains_failed_evidence_and_stops_guest(self):
        self.runtime.execute.side_effect = [{'exit_code': 0}, {'exit_code': 1, 'stderr': 'ALSA timeout'}]
        with self.assertRaisesRegex(ValueError, 'ALSA probe failed'):
            self.run_guest()
        evidence = json.loads((self.output / 'audio-acceptance.json').read_text())
        self.assertEqual(evidence['status'], 'failed')
        self.assertEqual(evidence['probe']['exit_code'], 1)
        self.runtime.stop.assert_called_once_with()

    def test_hash_mismatch_precedes_fixture_or_runtime(self):
        validation.sha256.return_value = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.run_guest()
        validation.fixture.assert_not_called()
        self.runtime.start.assert_not_called()

    def test_changed_base_cannot_report_success(self):
        validation.sha256.side_effect = ['a' * 64, 'b' * 64]
        with self.assertRaisesRegex(ValueError, 'Backing image changed'):
            self.run_guest()
        self.assertEqual(json.loads((self.output / 'audio-acceptance.json').read_text())['status'], 'failed')

    def test_guest_probe_compiles(self):
        compile(validation.PROBE, '<guest-audio-probe>', 'exec')
        compile(validation.DIRECT_ALSA + '\n' + validation.PROBE, '<direct-guest-audio-probe>', 'exec')

    def test_module_requires_pin_and_rejects_changed_bytes_before_fixture(self):
        module = self.root / 'candidate.ko'
        module.write_bytes(b'candidate module')
        for options in ({'module': module}, {'module_sha256': 'a' * 64},
                        {'module': module, 'module_sha256': 'a' * 64}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                validation.run(self.root / 'base.img', 'a' * 64, self.output, **options)
        validation.fixture.assert_not_called()

    def test_module_load_failure_is_retained_and_never_force_loaded(self):
        import hashlib
        module = self.root / 'candidate.ko'
        module.write_bytes(b'candidate module')
        digest = hashlib.sha256(module.read_bytes()).hexdigest()
        self.runtime.execute.side_effect = [{'exit_code': 0}, {'exit_code': 1, 'stderr': 'symbol mismatch'}]
        with self.assertRaisesRegex(ValueError, 'did not load normally'):
            validation.run(self.root / 'base.img', 'a' * 64, self.output,
                           module=module, module_sha256=digest)
        record = json.loads((self.output / 'audio-acceptance.json').read_text())
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['candidate_module']['sha256'], digest)
        self.assertEqual((self.output / 'candidate-snd-usb-audio.ko').read_bytes(), module.read_bytes())
        script = self.runtime.execute.call_args_list[1].args[0]
        self.assertIn('insmod /tmp/forge-audio-module-', script)
        self.assertNotIn('insmod -f', script)
        self.runtime.stop.assert_called_once_with()

    def test_raw_drain_requires_direct_client_before_creating_fixture(self):
        with self.assertRaisesRegex(ValueError, 'requires the direct ALSA'):
            validation.run(self.root / 'base.img', 'a' * 64, self.output, raw_drain=True)
        validation.fixture.assert_not_called()

    def test_gui_requires_hotplug_before_creating_fixture(self):
        with self.assertRaisesRegex(ValueError, 'requires hotplug'):
            validation.run(self.root / 'base.img', 'a' * 64, self.output, gui=True)
        validation.fixture.assert_not_called()

    def test_mcp_requires_hotplug_and_excludes_gui(self):
        for options in ({'mcp': True}, {'hotplug': True, 'mcp': True, 'gui': True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                validation.run(self.root / 'base.img', 'a' * 64, self.output, **options)
        validation.fixture.assert_not_called()

    def test_mcp_hotplug_routes_both_actions_and_closes_transport(self):
        played = {'exit_code': 0, 'stdout': json.dumps({'status': 'played'})}
        self.runtime.execute.side_effect = [
            {'exit_code': 0}, played,
            {'exit_code': 0, 'stdout': 'presence-verified\n'},
            {'exit_code': 0, 'stdout': 'presence-verified\n'}, played,
            {'exit_code': 0}]
        with patch('audio_mcp_validation.MCPAudio') as frontend:
            frontend.return_value.operation.return_value = {'frontend': 'stdio-mcp-attached'}
            result = validation.run(self.root / 'base.img', 'a' * 64, self.output, hotplug=True, mcp=True)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual([call.args[2] for call in frontend.return_value.operation.call_args_list], [False, True])
        frontend.return_value.close.assert_called_once_with()
        validation.audio_operation.assert_not_called()

    def test_gui_failure_still_stops_guest_if_panel_cleanup_fails(self):
        played = {'exit_code': 0, 'stdout': json.dumps({'status': 'played'})}
        self.runtime.execute.side_effect = [{'exit_code': 0}, played]
        with patch('audio_gui_validation.GUIAudio') as frontend:
            frontend.return_value.operation.side_effect = ValueError('panel failed')
            frontend.return_value.close.side_effect = RuntimeError('cleanup failed')
            with self.assertRaisesRegex(ValueError, 'panel failed'):
                validation.run(self.root / 'base.img', 'a' * 64, self.output, hotplug=True, gui=True)
        self.runtime.stop.assert_called_once_with()
        evidence = json.loads((self.output / 'audio-acceptance.json').read_text())
        self.assertEqual(evidence['status'], 'failed')
        self.assertEqual(evidence['frontend_cleanup_error'], 'cleanup failed')

    def test_hotplug_requires_disappearance_reappearance_and_replayed_stream(self):
        played = {'exit_code': 0, 'stdout': json.dumps({'status': 'played'})}
        self.runtime.execute.side_effect = [
            {'exit_code': 0}, played,
            {'exit_code': 0, 'stdout': 'presence-verified\n'},
            {'exit_code': 0, 'stdout': 'presence-verified\n'}, played,
            {'exit_code': 0}]
        result = validation.run(self.root / 'base.img', 'a' * 64, self.output, hotplug=True)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual([call.args[2] for call in validation.audio_operation.call_args_list], [False, True])
        self.assertIn('hotplug_probe', result)

    def test_hotplug_disappearance_failure_cannot_report_success(self):
        self.runtime.execute.side_effect = [
            {'exit_code': 0}, {'exit_code': 0, 'stdout': json.dumps({'status': 'played'})},
            {'exit_code': 1, 'stdout': 'still present'}]
        with self.assertRaisesRegex(ValueError, 'hotplug state'):
            validation.run(self.root / 'base.img', 'a' * 64, self.output, hotplug=True)
        self.assertEqual(validation.audio_operation.call_count, 1)

    def test_wav_hotplug_requires_recorded_replay_not_only_guest_success(self):
        played = {'exit_code': 0, 'stdout': json.dumps({'status': 'played'})}
        self.runtime.execute.side_effect = [
            {'exit_code': 0}, played, played,
            {'exit_code': 0, 'stdout': 'presence-verified\n'},
            {'exit_code': 0, 'stdout': 'presence-verified\n'}, played,
            {'exit_code': 0}]
        with patch('verify_audio_recording.verify', side_effect=ValueError('missing replay')) as verify:
            with self.assertRaisesRegex(ValueError, 'missing replay'):
                validation.run(self.root / 'base.img', 'a' * 64, self.output,
                               hotplug=True, wav=True, repetitions=2)
        verify.assert_called_once_with(self.runtime.audio_recording, repetitions=3)
        record = json.loads((self.output / 'audio-acceptance.json').read_text())
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['hotplug_probe'], played)
        self.runtime.stop.assert_called_once_with()
